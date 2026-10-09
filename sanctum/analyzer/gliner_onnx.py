"""Torch-free GLiNER span-model inference on ONNX Runtime.

The ``gliner`` library imports PyTorch even when it runs an ONNX export, which
would put ~520 MB of PyTorch into the desktop sidecar. A *span-level,
uni-encoder* GLiNER checkpoint (e.g. ``knowledgator/gliner-pii-base-v1.0``)
needs only a little pre/post-processing around the graph, re-implemented here
on ``onnxruntime``, ``tokenizers`` and ``numpy``:

1. Split text into words with GLiNER's whitespace splitter.
2. Prepend the label prompt: ``<<ENT>> label1 <<ENT>> label2 ... <<SEP>>``.
3. Sub-word tokenize the words; mark the first sub-token of each text word.
4. Enumerate every span of up to ``max_width`` words.
5. Run the graph, sigmoid the logits, keep spans above the threshold.
6. Greedy flat decoding: highest score first, drop anything overlapping.

This mirrors gliner 0.2.29 (``UniEncoderSpanProcessor`` + ``SpanDecoder``);
the sanctum-research repo checks it span-for-span against the library
(``make parity``). The only known difference: ``tokenizers`` NFKC-normalises a
few compatibility characters ("º" -> "o") that gliner's slow SentencePiece
tokenizer maps to ``[UNK]``.

Everything is read from local files. Nothing here can reach the network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

_WORD = re.compile(r"\w+(?:[-_]\w+)*|\S")

# The files a model directory must contain (relative paths).
CONFIG_FILE = "gliner_config.json"
TOKENIZER_FILE = "tokenizer.json"
ONNX_FILE = "onnx/model_quint8.onnx"


@dataclass(frozen=True)
class GlinerEntity:
    start: int  # character offsets into the input text, end exclusive
    end: int
    label: str
    score: float


class GlinerOnnx:
    """A span-level GLiNER model loaded from a local directory.

    ``session`` lets tests inject a stand-in for ``onnxruntime.InferenceSession``.
    """

    def __init__(
        self,
        model_dir: str | Path,
        onnx_file: str = ONNX_FILE,
        intra_op_threads: int | None = None,
        session: Any = None,
    ) -> None:
        from tokenizers import Tokenizer

        model_dir = Path(model_dir)
        config = json.loads((model_dir / CONFIG_FILE).read_text(encoding="utf-8"))
        if config.get("span_mode") == "token_level":
            raise ValueError(f"{model_dir}: token-level GLiNER checkpoints are not supported")
        self.max_width = int(config["max_width"])
        self.max_len = int(config["max_len"])
        self.ent_token = str(config.get("ent_token", "<<ENT>>"))
        self.sep_token = str(config.get("sep_token", "<<SEP>>"))

        self._tokenizer = Tokenizer.from_file(str(model_dir / TOKENIZER_FILE))
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

        if session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            if intra_op_threads:
                options.intra_op_num_threads = intra_op_threads
            session = ort.InferenceSession(
                str(model_dir / onnx_file),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        self._session = session
        self._input_names = {i.name for i in session.get_inputs()}

    @staticmethod
    def split_words(text: str) -> list[tuple[str, int, int]]:
        """GLiNER's whitespace splitter: words (with inner - or _) and single symbols."""
        return [(m.group(), m.start(), m.end()) for m in _WORD.finditer(text)]

    def predict(
        self,
        text: str,
        labels: list[str],
        threshold: float = 0.5,
        flat_ner: bool = True,
    ) -> list[GlinerEntity]:
        """Entities in ``text`` for the given label prompts, sorted by start."""
        labels = list(dict.fromkeys(labels))
        words = self.split_words(text)[: self.max_len]
        if not words or not labels:
            return []
        feeds = self.encode([w for w, _, _ in words], labels)
        (logits,) = self._session.run(["logits"], feeds)
        # float32, as torch.sigmoid in gliner: keeps threshold edge cases identical
        probs = 1.0 / (1.0 + np.exp(-np.asarray(logits[0], dtype=np.float32)))  # (L, K, C)
        return self._decode(probs, words, labels, threshold, flat_ner)

    def encode(self, words: list[str], labels: list[str]) -> dict[str, np.ndarray]:
        """The graph's input tensors (batch of one) for pre-split ``words``."""
        prompt: list[str] = []
        for label in labels:
            prompt.append(self.ent_token)
            if label:
                prompt.append(label)
        prompt.append(self.sep_token)
        n_prompt = len(prompt)

        enc = self._tokenizer.encode(prompt + words, is_pretokenized=True)
        input_ids = np.asarray(enc.ids, dtype=np.int64)[None, :]
        attention_mask = np.asarray(enc.attention_mask, dtype=np.int64)[None, :]

        # words_mask: 1-based index of the text word on its *first* sub-token;
        # 0 on special tokens, prompt words and continuation sub-tokens.
        words_mask = np.zeros_like(input_ids)
        prev: int | None = None
        for i, wid in enumerate(enc.word_ids):
            if wid is not None and wid != prev and wid >= n_prompt:
                words_mask[0, i] = wid - n_prompt + 1
            prev = wid

        n = len(words)
        starts = np.repeat(np.arange(n, dtype=np.int64), self.max_width)
        ends = starts + np.tile(np.arange(self.max_width, dtype=np.int64), n)
        feeds = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "words_mask": words_mask,
            "text_lengths": np.asarray([[n]], dtype=np.int64),
            "span_idx": np.stack([starts, ends], axis=1)[None, :, :],
            "span_mask": (ends <= n - 1)[None, :],
        }
        return {name: value for name, value in feeds.items() if name in self._input_names}

    def _decode(
        self,
        probs: np.ndarray,
        words: list[tuple[str, int, int]],
        labels: list[str],
        threshold: float,
        flat_ner: bool,
    ) -> list[GlinerEntity]:
        n = len(words)
        candidates: list[tuple[int, int, int, float]] = []
        for s, k, c in zip(*np.nonzero(probs > threshold), strict=True):
            s, k, c = int(s), int(k), int(c)
            if s + k < n and c < len(labels):
                candidates.append((s, s + k, c, float(probs[s, k, c])))

        # Stable sort keeps gliner's tie order (start, width, class ascending).
        candidates.sort(key=lambda x: -x[3])
        kept: list[tuple[int, int, int, float]] = []
        for cand in candidates:
            if not any(_conflicts(cand, other, flat_ner) for other in kept):
                kept.append(cand)
        kept.sort(key=lambda x: x[0])

        return [GlinerEntity(words[s][1], words[e][2], labels[c], score) for s, e, c, score in kept]


def _conflicts(
    a: tuple[int, int, int, float], b: tuple[int, int, int, float], flat_ner: bool
) -> bool:
    """gliner's ``has_overlapping`` / ``has_overlapping_nested``, multi_label=False."""
    if (a[0], a[1]) == (b[0], b[1]):
        return True
    disjoint = a[0] > b[1] or b[0] > a[1]
    if flat_ner:
        return not disjoint
    nested = (a[0] <= b[0] and a[1] >= b[1]) or (b[0] <= a[0] and b[1] >= a[1])
    return not disjoint and not nested
