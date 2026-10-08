"""Document-level name propagation.

NER models judge each mention on its own. Once "Dwayne Kowalczyk" is found,
a later bare "Dwayne", or "Mr Price" after "Mark Price", is often missed: in
the sanctum-research benchmark that was the most common miss for every model.
This pass collects the text of every PERSON / ORGANIZATION finding, plus the
name tokens of each person and the distinctive first word of each
organisation, and marks every other occurrence with the same type.

It is a regex pass, deterministic and model-independent (101 -> 79 misses
with spaCy, 36 -> 22 with GLiNER-PII). Stdlib only, so it lives in core.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sanctum.core.models import DetectionResult

PROPAGATION_RECOGNIZER = "NamePropagation"
SEED_TYPES = frozenset({"PERSON", "ORGANIZATION"})

# Tokens never propagated on their own: titles, particles, suffixes.
_STOP = frozenset(
    {
        "mr", "mrs", "ms", "miss", "dr", "prof", "sir", "dame", "mme", "mlle", "herr",
        "frau", "d", "dña", "jr", "sr", "esq", "the", "and", "of", "de", "del", "la",
        "le", "van", "von", "der", "den", "da", "di", "du", "bin", "al",
    }
)  # fmt: skip
# Organisation words that identify nobody on their own.
_ORG_GENERIC = frozenset(
    {
        "bank", "group", "capital", "partners", "holdings", "limited", "ltd", "llp",
        "llc", "inc", "corp", "company", "co", "services", "consulting", "national",
        "first", "international", "health", "the", "and", "of", "ag", "sa", "srl",
        "gmbh", "bv", "plc", "pllc", "kg",
    }
)  # fmt: skip
# \u2019 is the typographic apostrophe ("O\u2019Brien").
_PERSON_TOKEN = re.compile(r"[^\W\d_][\w'\u2019-]*")
_ORG_TOKEN = re.compile(r"[^\W\d_][\w&'\u2019-]*")
_MIN_PERSON_TOKEN = 3
_MIN_ORG_TOKEN = 4
_CASE_WINDOW = 40


@dataclass(frozen=True)
class Seed:
    entity_type: str
    score: float
    forms: frozenset[str] = frozenset()  # exact spellings it was seen in


@dataclass(frozen=True)
class Mention:
    start: int
    end: int
    entity_type: str
    score: float


def collect_seeds(findings: Iterable[tuple[str, str, float]]) -> dict[str, Seed]:
    """Lower-cased strings to look for, from ``(entity_type, text, score)`` findings.

    When two findings yield the same string, the higher-scoring one decides
    its type.
    """
    seeds: dict[str, Seed] = {}
    for entity_type, text, score in findings:
        if entity_type not in SEED_TYPES:
            continue
        for candidate in _candidates(entity_type, text.strip()):
            key = candidate.lower()
            if not key:
                continue
            prev = seeds.get(key)
            forms = (prev.forms if prev else frozenset()) | {candidate}
            if prev is None or prev.score < score:
                seeds[key] = Seed(entity_type, score, forms)
            else:
                seeds[key] = Seed(prev.entity_type, prev.score, forms)
    return seeds


def find_mentions(
    text: str,
    seeds: dict[str, Seed],
    covered: Iterable[tuple[int, int]],
) -> list[Mention]:
    """Occurrences of ``seeds`` in ``text`` that touch none of the ``covered`` spans.

    Longer seeds are matched first, so "Mark Price" wins over "Price". A
    single-word seed must look like a name where it occurs (Capitalised or
    ALL CAPS), unless the text around it is all lower case (chat exports) or
    it is spelled exactly as detected. The last rule keeps propagation in step
    with the case-sensitive leak check: a word replaced once is replaced at
    every verbatim repeat, so a low-confidence hit on, say, "parties" cannot
    leave its other occurrences behind and fail the whole document.
    """
    taken = bytearray(len(text))
    for start, end in covered:
        taken[start:end] = b"\x01" * (end - start)

    mentions: list[Mention] = []
    for key in sorted(seeds, key=len, reverse=True):
        seed = seeds[key]
        pattern = r"(?<!\w)" + re.escape(key) + r"(?!\w)"
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = m.span()
            if any(taken[start:end]):
                continue
            word = text[start:end]
            if " " not in key and word not in seed.forms and not _looks_like_name(text, start, end):
                continue
            taken[start:end] = b"\x01" * (end - start)
            mentions.append(Mention(start, end, seed.entity_type, seed.score))
    return sorted(mentions, key=lambda m: m.start)


def propagate(text: str, detections: Sequence[DetectionResult]) -> list[DetectionResult]:
    """``detections`` plus every further mention of their names in ``text``."""
    seeds = collect_seeds((d.entity_type, d.text_span, d.score) for d in detections)
    if not seeds:
        return list(detections)
    added = [
        DetectionResult(
            entity_type=m.entity_type,
            start=m.start,
            end=m.end,
            score=m.score,
            text_span=text[m.start : m.end],
            context=text[max(0, m.start - 40) : m.end + 40],
            recognizer_name=PROPAGATION_RECOGNIZER,
        )
        for m in find_mentions(text, seeds, ((d.start, d.end) for d in detections))
    ]
    return sorted([*detections, *added], key=lambda d: (d.start, d.end))


def _candidates(entity_type: str, text: str) -> list[str]:
    out = [text]
    if entity_type == "PERSON":
        for tok in _PERSON_TOKEN.findall(text):
            if len(tok) >= _MIN_PERSON_TOKEN and tok.lower().strip(".") not in _STOP:
                out.append(tok)
    else:
        toks = _ORG_TOKEN.findall(text)
        if len(toks) > 1 and len(toks[0]) >= _MIN_ORG_TOKEN and toks[0].lower() not in _ORG_GENERIC:
            out.append(toks[0])  # "Verdana Pharmaceuticals" -> "Verdana"
    return out


def _looks_like_name(text: str, start: int, end: int) -> bool:
    word = text[start:end]
    if word[:1].isupper() or word.isupper():
        return True
    window = text[max(0, start - _CASE_WINDOW) : end + _CASE_WINDOW]
    return window.islower()
