"""Presidio recognizer over the bundled GLiNER-PII ONNX model.

Replaces Presidio's stock ``SpacyRecognizer`` as the source of PERSON /
ORGANIZATION / LOCATION / DATE_TIME and of ID numbers no regex recognizer
covers. Every pattern and context recognizer stays.

The model spots sensitive numbers well but often picks the wrong kind (a phone
number as an SSN, an amount as a card number), so all of its number labels
map to one ``ID_NUMBER``. The specific types (CREDIT_CARD, IBAN_CODE, US_SSN,
...) come only from Presidio's pattern recognizers, which check structure
(checksums, formats). Model numbers with no digit, or that are currency
amounts, are dropped; capitalised names it calls numbers (often banks: the
"bank account" label) become ORGANIZATION (#56).

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
    "ssn": "ID_NUMBER",
    "driver license": "ID_NUMBER",
    "bank account": "ID_NUMBER",
    "credit card": "ID_NUMBER",
    "passport number": "ID_NUMBER",
    "tax id": "ID_NUMBER",
}

# A model "number" that is really an amount: "$9,433.00", "£4.2 million", "120 EUR".
_CURRENCY = re.compile(
    r"^(?:[$£€¥]\s?[\d.,]+(?:\s?(?:k|m|bn|million|billion))?"
    r"|[\d.,]+\s?(?:GBP|USD|EUR|CHF|JPY|dollars|pounds|euros))$",
    re.IGNORECASE,
)

DEFAULT_THRESHOLD = 0.2
MAX_CHUNK_CHARS = 1000

_EDGE_LEAD = " \t\n,;:()[]\"'"
_EDGE_TRAIL = _EDGE_LEAD + "."


class GlinerOnnxRecognizer(EntityRecognizer):
    """GLiNER span model as a Presidio ``EntityRecognizer``.

    Scores are rescaled from ``[threshold, 1]`` onto ``[score_floor, 1]``.
    Presidio applies one ``score_threshold`` to every recognizer, and the
    pattern recognizers are tuned around 0.35; without the rescale every model
    hit between 0.2 and 0.35 would be dropped. Keep ``score_floor`` fixed at
    that tuning point rather than tying it to the user's threshold: a stricter
    threshold (per request or as the default) then tightens this recognizer
    proportionally. Raising the floor along with it would lift every hit above
    the new threshold and filter nothing.
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
        if not wanted & set(self._prompts.values()):
            return []
        # Always prompt the full label set and filter afterwards: GLiNER's
        # spans and scores depend on the labels it is given, so prompting a
        # subset would change what is found for the types still wanted (#57).
        labels = list(self._prompts)

        results: list[RecognizerResult] = []
        for offset, chunk in chunk_text(text):
            for ent in self._model.predict(chunk, labels, threshold=self._threshold):
                start, end = _trim(text, offset + ent.start, offset + ent.end)
                if end <= start:
                    continue
                etype: str | None = self._prompts[ent.label]
                if etype == "ID_NUMBER":
                    etype = _number_type(text[start:end])
                if etype is None or etype not in wanted:
                    continue
                results.append(
                    RecognizerResult(
                        entity_type=etype,
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


def _number_type(span: str) -> str | None:
    """Re-check a span the model called a number.

    An amount ("$9,433.00") or a lowercase phrase ("client trust account") is
    dropped. A capitalised name with no digit ("Barclays Bank", "ABN AMRO") is
    what the model's "bank account" label often lands on: keep it, as the
    organisation it is, rather than leak it.
    """
    span = span.strip()
    if any(c.isdigit() for c in span):
        return None if _CURRENCY.match(span) else "ID_NUMBER"
    return "ORGANIZATION" if any(c.isupper() for c in span) else None


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    """Drop surrounding whitespace, quotes and brackets (and a trailing full stop)."""
    while start < end and text[start] in _EDGE_LEAD:
        start += 1
    while end > start and text[end - 1] in _EDGE_TRAIL:
        end -= 1
    return start, end
