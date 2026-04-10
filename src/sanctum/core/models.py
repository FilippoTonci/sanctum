from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
