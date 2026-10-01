"""Shared helpers for adapter-side construction of StructuredDocument."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sanctum.core.models import DocumentFormat, StructuredDocument, TextSegment

_RUN_SUFFIX = re.compile(r"/r\d+$")


def run_block(segment_id: str) -> str | None:
    """Paragraph key for a ``.../p{p}/r{r}`` run id; None for non-run segments.

    The runs of one paragraph share a block, so detection sees the whole
    paragraph (see ``sanctum.core.blocks``). Runs are joined with no
    separator: Word and PowerPoint store a paragraph's text as the plain
    concatenation of its runs.
    """
    return _RUN_SUFFIX.sub("", segment_id) if _RUN_SUFFIX.search(segment_id) else None


def build_segment(
    segment_id: str,
    text: str,
    *,
    block: str | None = None,
    join_before: str = "",
    **metadata: Any,
) -> TextSegment:
    """Shortcut: ``TextSegment(id=..., text=..., metadata={...}, block=...)``.

    Adapter code does this on every run/cell/shape, so having a one-liner
    keeps the hot path readable. ``block`` / ``join_before`` are described
    on ``TextSegment``.
    """
    return TextSegment(
        id=segment_id,
        text=text,
        metadata=dict(metadata),
        block=block,
        join_before=join_before,
    )


def build_document(
    source_path: Path,
    fmt: DocumentFormat,
    segments: list[TextSegment],
    raw_handle: Any,
) -> StructuredDocument:
    """Build a StructuredDocument with the raw handle attached.

    The handle is excluded from Pydantic dumps but kept in memory so the
    matching writer can project mutations back without re-parsing.
    """
    doc = StructuredDocument(
        source_path=source_path,
        format=fmt,
        segments=segments,
    )
    doc.raw_handle = raw_handle
    return doc
