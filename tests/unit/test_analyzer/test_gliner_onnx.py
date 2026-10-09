"""GlinerOnnx pre/post-processing, with a tiny tokenizer and a stand-in session.

Parity with the real gliner library on the real model is checked in the
sanctum-research repo (``make parity``); here we pin the mechanics: prompt
layout, word mask, span enumeration, thresholding and greedy decoding.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sanctum.analyzer.gliner_onnx import GlinerEntity, GlinerOnnx
from tokenizers import Tokenizer, models, processors

MAX_WIDTH = 3
INPUTS = ("input_ids", "attention_mask", "words_mask", "text_lengths", "span_idx", "span_mask")


def _write_model_dir(tmp_path: Path, max_len: int = 384, span_mode: str = "markerV0") -> Path:
    vocab = {
        "[PAD]": 0,
        "[CLS]": 1,
        "[SEP]": 2,
        "[UNK]": 3,
        "<<ENT>>": 4,
        "<<SEP>>": 5,
        "name": 6,
        "city": 7,
        "Mr": 8,
        "Smith": 9,
        "##son": 10,
        "met": 11,
        "in": 12,
        "Rome": 13,
        ".": 14,
    }
    tok = Tokenizer(models.WordPiece(vocab, unk_token="[UNK]"))
    tok.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)]
    )
    tok.save(str(tmp_path / "tokenizer.json"))
    config = {
        "max_width": MAX_WIDTH,
        "max_len": max_len,
        "ent_token": "<<ENT>>",
        "sep_token": "<<SEP>>",
        "span_mode": span_mode,
    }
    (tmp_path / "gliner_config.json").write_text(json.dumps(config))
    return tmp_path


class FakeSession:
    """Returns logits that put ``score`` on chosen (word_start, width, class) cells."""

    def __init__(self, hits: dict[tuple[int, int, int], float], n_classes: int = 2) -> None:
        self.hits = hits
        self.n_classes = n_classes
        self.feeds: dict[str, np.ndarray] = {}

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=n) for n in INPUTS]

    def run(self, outputs: list[str], feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        assert outputs == ["logits"]
        self.feeds = feeds
        n = int(feeds["text_lengths"][0, 0])
        logits = np.full((1, n, MAX_WIDTH, self.n_classes), -20.0, dtype=np.float32)
        for (s, k, c), p in self.hits.items():
            logits[0, s, k, c] = np.log(p / (1 - p))  # inverse sigmoid
        return [logits]


TEXT = "Mr Smithson met Smith in Rome."
# words: Mr(0) Smithson(1) met(2) Smith(3) in(4) Rome(5) .(6)


def _model(tmp_path: Path, hits: dict, **kw: object) -> tuple[GlinerOnnx, FakeSession]:
    session = FakeSession(hits)
    return GlinerOnnx(_write_model_dir(tmp_path, **kw), session=session), session


def test_split_words_matches_gliner_splitter() -> None:
    words = GlinerOnnx.split_words("Jean-Luc's e-mail: j_doe@x.io!")
    assert [w for w, _, _ in words] == [
        "Jean-Luc", "'", "s", "e-mail", ":", "j_doe", "@", "x", ".", "io", "!",
    ]  # fmt: skip
    assert words[0][1:] == (0, 8)


def test_encode_prompt_and_word_mask(tmp_path: Path) -> None:
    model, _ = _model(tmp_path, {})
    words = [w for w, _, _ in model.split_words(TEXT)]
    feeds = model.encode(words, ["name", "city"])

    ids = feeds["input_ids"][0].tolist()
    # [CLS] <<ENT>> name <<ENT>> city <<SEP>> Mr Smith ##son met Smith in Rome . [SEP]
    assert ids == [1, 4, 6, 4, 7, 5, 8, 9, 10, 11, 9, 12, 13, 14, 2]
    # prompt and special tokens are 0; "##son" continues word 2 so it is 0 too
    assert feeds["words_mask"][0].tolist() == [0, 0, 0, 0, 0, 0, 1, 2, 0, 3, 4, 5, 6, 7, 0]
    assert feeds["attention_mask"][0].tolist() == [1] * len(ids)
    assert feeds["text_lengths"].tolist() == [[7]]


def test_encode_enumerates_spans_and_masks_overflow(tmp_path: Path) -> None:
    model, _ = _model(tmp_path, {})
    feeds = model.encode(["a", "b"], ["name"])
    assert feeds["span_idx"][0].tolist() == [[0, 0], [0, 1], [0, 2], [1, 1], [1, 2], [1, 3]]
    assert feeds["span_mask"][0].tolist() == [True, True, False, True, False, False]


def test_predict_maps_word_spans_to_characters(tmp_path: Path) -> None:
    # "Mr Smithson" = words 0..1 (width 1), class 0; "Rome" = word 5, class 1
    model, _ = _model(tmp_path, {(0, 1, 0): 0.9, (5, 0, 1): 0.6})
    out = model.predict(TEXT, ["name", "city"], threshold=0.5)
    assert [(TEXT[e.start : e.end], e.label) for e in out] == [
        ("Mr Smithson", "name"),
        ("Rome", "city"),
    ]
    assert out[0].score == pytest.approx(0.9)
    assert isinstance(out[0], GlinerEntity)


def test_predict_threshold_is_strict(tmp_path: Path) -> None:
    model, _ = _model(tmp_path, {(3, 0, 0): 0.4})
    assert model.predict(TEXT, ["name", "city"], threshold=0.4 + 1e-6) == []
    assert len(model.predict(TEXT, ["name", "city"], threshold=0.39)) == 1


def test_flat_decoding_keeps_best_of_overlaps(tmp_path: Path) -> None:
    hits = {(0, 1, 0): 0.7, (1, 0, 0): 0.9, (1, 0, 1): 0.8, (3, 0, 0): 0.6}
    model, _ = _model(tmp_path, hits)
    out = model.predict(TEXT, ["name", "city"], threshold=0.5)
    # "Smithson"/name (0.9) wins over "Mr Smithson" (overlaps) and over
    # "Smithson"/city (same span, one label per span)
    assert [(TEXT[e.start : e.end], e.label) for e in out] == [
        ("Smithson", "name"),
        ("Smith", "name"),
    ]


def test_nested_decoding_allows_containment(tmp_path: Path) -> None:
    hits = {(0, 1, 0): 0.7, (1, 0, 0): 0.9, (0, 2, 1): 0.6}
    model, _ = _model(tmp_path, hits)
    out = model.predict(TEXT, ["name", "city"], threshold=0.5, flat_ner=False)
    assert sorted(TEXT[e.start : e.end] for e in out) == [
        "Mr Smithson",
        "Mr Smithson met",
        "Smithson",
    ]


def test_spans_past_the_text_are_ignored(tmp_path: Path) -> None:
    # word 6 is the last word; width 2 would run past it
    model, _ = _model(tmp_path, {(6, 2, 0): 0.99})
    assert model.predict(TEXT, ["name", "city"], threshold=0.5) == []


def test_duplicate_labels_and_empty_inputs(tmp_path: Path) -> None:
    model, session = _model(tmp_path, {})
    assert model.predict("   ", ["name"]) == []
    assert model.predict(TEXT, []) == []
    model.predict(TEXT, ["name", "name", "city"])
    assert session.feeds["input_ids"][0].tolist()[:6] == [1, 4, 6, 4, 7, 5]


def test_long_text_truncated_to_max_len(tmp_path: Path) -> None:
    model, session = _model(tmp_path, {}, max_len=3)
    model.predict(TEXT, ["name"])
    assert session.feeds["text_lengths"].tolist() == [[3]]


def test_token_level_checkpoints_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="token-level"):
        _model(tmp_path, {}, span_mode="token_level")


def test_feeds_only_inputs_the_graph_declares(tmp_path: Path) -> None:
    model, session = _model(tmp_path, {})
    session.get_inputs = lambda: [SimpleNamespace(name="input_ids")]  # type: ignore[method-assign]
    model = GlinerOnnx(tmp_path, session=session)
    assert set(model.encode(["Rome"], ["city"])) == {"input_ids"}
