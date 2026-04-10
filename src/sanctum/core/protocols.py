from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from sanctum.core.models import AnonymizationResult, DetectionResult, OperatorPolicy


@runtime_checkable
class Analyzer(Protocol):
    """Detects PII entities in text."""

    def analyze(
        self,
        text: str,
        language: str = "en",
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[DetectionResult]: ...


@runtime_checkable
class Anonymizer(Protocol):
    """Replaces detected PII entities according to operator policies."""

    def anonymize(
        self,
        text: str,
        detections: list[DetectionResult],
        operator_policies: dict[str, OperatorPolicy] | None = None,
    ) -> AnonymizationResult: ...


@runtime_checkable
class DocumentReader(Protocol):
    """Reads document content from a file path."""

    def read(self, path: Path) -> str: ...


@runtime_checkable
class DocumentWriter(Protocol):
    """Writes content to a file path."""

    def write(self, path: Path, content: str) -> None: ...
