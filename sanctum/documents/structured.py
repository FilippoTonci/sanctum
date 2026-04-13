"""Shared helpers for adapter-side construction of StructuredDocument."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sanctum.core.models import DocumentFormat, StructuredDocument, TextSegment


def build_segment(
    segment_id: str,
    text: str,
    **metadata: Any,
) -> TextSegment:
    """Shortcut: ``TextSegment(id=..., text=..., metadata={...})``.

    Adapter code does this on every run/cell/shape, so having a one-liner
    keeps the hot path readable.
    """
    return TextSegment(id=segment_id, text=text, metadata=dict(metadata))


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
