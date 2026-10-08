"""Presidio recognizer over the bundled GLiNER-PII ONNX model.

Replaces Presidio's stock ``SpacyRecognizer`` as the source of PERSON /
ORGANIZATION / LOCATION / DATE_TIME and of ID numbers no regex recognizer
covers (passport, tax ID). Every pattern and context recognizer stays.

Choices below come from the sanctum-research benchmark (REPORT.md):

* Prompts are the label names Knowledgator trained on ("name", "location
  address", "ssn", ...), not generic ones: 18 vs 26 misses on the hard corpus.
* The model is calibrated low. 0.2 is its sweet spot (18 misses, precision
  0.83). At Presidio's usual 0.4 it misses 69, so the threshold ships here.
* Text is fed in chunks of at most 1000 characters on line boundaries, which
  keeps every chunk well inside the model's context window.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from presidio_analyzer import EntityRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts
from sanctum.analyzer.gliner_onnx import GlinerOnnx

# Knowledgator gliner-pii label -> Sanctum entity type. Order is the prompt
# order the benchmark used; GLiNER scores are mildly order-sensitive.
KNOWLEDGATOR_PROMPTS: dict[str, str] = {
    "name": "PERSON",
    "organization": "ORGANIZATION",
    "location address": "LOCATION",
    "location city": "LOCATION",
    "location country": "LOCATION",
    "date": "DATE_TIME",
    "dob": "DATE_TIME",
    "email address": "EMAIL_ADDRESS",
    "phone number": "PHONE_NUMBER",
    "url": "URL",
    "ip address": "IP_ADDRESS",
    "iban": "IBAN_CODE",
    "ssn": "US_SSN",
    "driver license": "US_DRIVER_LICENSE",
    "bank account": "US_BANK_NUMBER",
    "credit card": "CREDIT_CARD",
    "passport number": "ID_NUMBER",
    "tax id": "ID_NUMBER",
}

DEFAULT_THRESHOLD = 0.2
MAX_CHUNK_CHARS = 1000

_EDGE_LEAD = " \t\n,;:()[]\"'"
_EDGE_TRAIL = _EDGE_LEAD + "."


class GlinerOnnxRecognizer(EntityRecognizer):
    """GLiNER span model as a Presidio ``EntityRecognizer``.

    Scores are rescaled from ``[threshold, 1]`` onto ``[score_floor, 1]``.
    Presidio applies one ``score_threshold`` to every recognizer, and the
    pattern recognizers are tuned around 0.35; without the rescale every model
    hit between 0.2 and 0.35 would be dropped. Pass the analyzer's default
    threshold as ``score_floor`` so the defaults line up exactly; a stricter
    per-request threshold then tightens this recognizer proportionally.
    """

    def __init__(
        self,
        model_dir: str | Path,
        threshold: float = DEFAULT_THRESHOLD,
        score_floor: float = 0.35,
        prompts: Mapping[str, str] = KNOWLEDGATOR_PROMPTS,
        model: Any = None,
    ) -> None:
        if not 0.0 < threshold < 1.0:
            raise ValueError(f"threshold must be in (0, 1), got {threshold}")
        self._model_dir = Path(model_dir)
        self._threshold = threshold
        self._score_floor = score_floor
        self._prompts = dict(prompts)
        self._model = model
        super().__init__(
            supported_entities=sorted(set(self._prompts.values())),
            name="GlinerOnnxRecognizer",
            supported_language="en",
        )

    def load(self) -> None:
        if self._model is None:
            self._model = GlinerOnnx(self._model_dir)

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
    ) -> list[RecognizerResult]:
        wanted = set(entities) if entities else set(self.supported_entities)
        labels = [p for p, etype in self._prompts.items() if etype in wanted]
        if not labels:
            return []

        results: list[RecognizerResult] = []
        for offset, chunk in chunk_text(text):
            for ent in self._model.predict(chunk, labels, threshold=self._threshold):
                start, end = _trim(text, offset + ent.start, offset + ent.end)
                if end <= start:
                    continue
                results.append(
                    RecognizerResult(
                        entity_type=self._prompts[ent.label],
                        start=start,
                        end=end,
                        score=self._rescale(ent.score),
                        recognition_metadata={
                            RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                            RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                        },
                    )
                )
        return results

    def _rescale(self, score: float) -> float:
        span = (score - self._threshold) / (1.0 - self._threshold)
        return min(1.0, self._score_floor + (1.0 - self._score_floor) * max(0.0, span))


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[tuple[int, str]]:
    """``(offset, chunk)`` pairs covering ``text``'s lines, each <= ``max_chars``.

    Whole lines are packed together; a single over-long line is split at a
    sentence end (". ") or, failing that, hard at ``max_chars``.
    """
    pieces: list[tuple[int, int]] = []
    for m in re.finditer(r"[^\n]+", text):
        s, e = m.span()
        while e - s > max_chars:
            cut = text.rfind(". ", s, s + max_chars)
            cut = cut + 2 if cut > s else s + max_chars
            pieces.append((s, cut))
            s = cut
        pieces.append((s, e))

    out: list[tuple[int, str]] = []
    cur: tuple[int, int] | None = None
    for s, e in pieces:
        if cur is None:
            cur = (s, e)
        elif e - cur[0] <= max_chars:
            cur = (cur[0], e)
        else:
            out.append((cur[0], text[cur[0] : cur[1]]))
            cur = (s, e)
    if cur is not None:
        out.append((cur[0], text[cur[0] : cur[1]]))
    return out


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    """Drop surrounding whitespace, quotes and brackets (and a trailing full stop)."""
    while start < end and text[start] in _EDGE_LEAD:
        start += 1
    while end > start and text[end - 1] in _EDGE_TRAIL:
        end -= 1
    return start, end
