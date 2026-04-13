from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DetectionResult(BaseModel):
    """A single PII detection found in text."""

    model_config = {"frozen": True}

    entity_type: str
    start: int
    end: int
    score: float
    text_span: str
    context: str = ""
    recognizer_name: str = ""


class OperatorPolicy(BaseModel):
    """User-configurable anonymization operator for an entity type."""

    operator_name: str
    params: dict[str, Any] = Field(default_factory=dict)


class AnonymizationResult(BaseModel):
    """The outcome of an anonymization pass over a document."""

    model_config = {"frozen": True}

    original_text: str
    anonymized_text: str
    detections: list[DetectionResult]
    operators_applied: dict[str, str]


DocumentFormat = Literal["docx", "xlsx", "pdf", "pptx"]


class TextSegment(BaseModel):
    """One atomic unit of text extracted from a structured document.

    Segments are the grain at which anonymization happens. A segment id is
    adapter-specific and stable across read/write round trips: the same
    run, cell, or text frame produces the same id every time.
    """

    id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredDocument(BaseModel):
    """Intermediate representation of a parsed office document.

    The core engine treats every adapter's output identically: a flat list
    of TextSegments plus an opaque ``raw_handle`` that only the originating
    writer understands. The core never inspects ``raw_handle``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_path: Path
    format: DocumentFormat
    segments: list[TextSegment]
    raw_handle: Any = Field(default=None, exclude=True)
