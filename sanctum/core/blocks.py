"""Block-level detection: analyse whole paragraphs, project findings onto segments.

Segments are the grain at which documents are written back (a Word run, a PDF
line). Detection needs more context than that: Word splits a name across runs
whenever formatting, spell-check or tracked edits touch it, and PDF breaks
names across lines. Adapters tag segments with a ``block`` key; this module
joins each block's segments (with ``join_before`` between them), analyses the
joined text once, and maps every finding back to the segment pieces it covers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from sanctum.core.models import DetectionResult, TextSegment
from sanctum.core.propagation import collect_seeds, find_mentions


@dataclass(frozen=True)
class Piece:
    segment_id: str
    start: int  # offsets into the segment's own text
    end: int
    text: str


@dataclass(frozen=True)
class BlockFinding:
    entity_type: str
    score: float
    original: str  # the joined text of the finding (may include join_before)
    pieces: tuple[Piece, ...]


_WORD = re.compile(r"\w+")
_MIN_FRAGMENT_WORD = 3


def fragment_words(piece_text: str, original: str) -> list[str]:
    """Whole words of a piece that are also whole words of the finding's text.

    Used by the leak check for linked findings: if one piece of a split
    name survives in the output ("Dear <PERSON>Martin"), the whole-name
    check cannot see it, but the piece's whole words can. Words shorter
    than three characters, and fragments cut mid-word by a segment
    boundary ("Jen" of "Jennifer"), are left out so unrelated text does
    not trip the check.
    """
    whole = set(_WORD.findall(original))
    out: list[str] = []
    for word in _WORD.findall(piece_text):
        if len(word) >= _MIN_FRAGMENT_WORD and word in whole and word not in out:
            out.append(word)
    return out


@dataclass(frozen=True)
class _Block:
    segments: tuple[TextSegment, ...]
    offsets: tuple[int, ...]  # where each segment's text starts in ``text``
    text: str


def _group(segments: Sequence[TextSegment]) -> list[tuple[TextSegment, ...]]:
    groups: dict[object, list[TextSegment]] = {}
    for i, seg in enumerate(segments):
        key: object = ("block", seg.block) if seg.block is not None else ("solo", i)
        groups.setdefault(key, []).append(seg)
    return [tuple(g) for g in groups.values()]


def _join(segs: tuple[TextSegment, ...]) -> _Block:
    parts: list[str] = []
    offsets: list[int] = []
    pos = 0
    for i, seg in enumerate(segs):
        if i > 0 and seg.join_before:
            parts.append(seg.join_before)
            pos += len(seg.join_before)
        offsets.append(pos)
        parts.append(seg.text)
        pos += len(seg.text)
    return _Block(segs, tuple(offsets), "".join(parts))


def block_texts(segments: Sequence[TextSegment]) -> list[str]:
    """The joined text of every block, in document order.

    The same grouping and ``join_before`` joining that detection uses, so a
    name split across Word runs reads back as one word ("Jen" + "nifer" ->
    "Jennifer"). Used by the writers' ``extract_text`` for the leak check.
    """
    return [_join(group).text for group in _group(segments)]


def _project(block: _Block, start: int, end: int) -> tuple[Piece, ...]:
    pieces: list[Piece] = []
    for seg, off in zip(block.segments, block.offsets, strict=True):
        s = max(start, off)
        e = min(end, off + len(seg.text))
        if s < e:
            pieces.append(Piece(seg.id, s - off, e - off, seg.text[s - off : e - off]))
    return tuple(pieces)


def detect_blocks(
    segments: Sequence[TextSegment],
    analyze: Callable[[str], list[DetectionResult]],
) -> list[BlockFinding]:
    """Findings in document order, each mapped onto the segments it covers.

    After every block is analysed, names found anywhere in the document are
    looked for in every block (``sanctum.core.propagation``), so "Dwayne
    Kowalczyk" in the opening paragraph also catches a bare "Dwayne" on page 3.
    """
    analysed: list[tuple[_Block, list[tuple[int, int, str, float]]]] = []
    for group in _group(segments):
        block = _join(group)
        if not block.text.strip():
            continue
        spans = [(d.start, d.end, d.entity_type, d.score) for d in analyze(block.text)]
        analysed.append((block, spans))

    seeds = collect_seeds(
        (etype, block.text[s:e], score) for block, spans in analysed for s, e, etype, score in spans
    )

    findings: list[BlockFinding] = []
    for block, spans in analysed:
        if seeds:
            covered = [(s, e) for s, e, _, _ in spans]
            for m in find_mentions(block.text, seeds, covered):
                spans.append((m.start, m.end, m.entity_type, m.score))
            spans.sort(key=lambda sp: (sp[0], sp[1]))
        for start, end, etype, score in spans:
            pieces = _project(block, start, end)
            if not pieces:
                continue  # the span fell entirely inside join_before text
            findings.append(BlockFinding(etype, score, block.text[start:end], pieces))
    return findings


def splice(text: str, edits: Iterable[tuple[int, int, str]]) -> str:
    """Apply non-overlapping ``(start, end, replacement)`` edits to ``text``."""
    out = text
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + repl + out[end:]
    return out
