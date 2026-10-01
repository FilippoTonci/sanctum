"""Block-level detection: analyse whole paragraphs, project findings onto segments.

Segments are the grain at which documents are written back (a Word run, a PDF
line). Detection needs more context than that: Word splits a name across runs
whenever formatting, spell-check or tracked edits touch it, and PDF breaks
names across lines. Adapters tag segments with a ``block`` key; this module
joins each block's segments (with ``join_before`` between them), analyses the
joined text once, and maps every finding back to the segment pieces it covers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from sanctum.core.models import DetectionResult, TextSegment


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
    """Findings in document order, each mapped onto the segments it covers."""
    findings: list[BlockFinding] = []
    for group in _group(segments):
        block = _join(group)
        if not block.text.strip():
            continue
        for det in analyze(block.text):
            pieces = _project(block, det.start, det.end)
            if not pieces:
                continue  # the span fell entirely inside join_before text
            findings.append(
                BlockFinding(det.entity_type, det.score, block.text[det.start : det.end], pieces)
            )
    return findings


def splice(text: str, edits: Iterable[tuple[int, int, str]]) -> str:
    """Apply non-overlapping ``(start, end, replacement)`` edits to ``text``."""
    out = text
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + repl + out[end:]
    return out
