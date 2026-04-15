from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    OperatorPolicy,
    StructuredDocument,
)


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


@runtime_checkable
class StructuredDocumentReader(Protocol):
    """Reads a real-world office document into a StructuredDocument."""

    def read(self, path: Path) -> StructuredDocument: ...


@runtime_checkable
class StructuredDocumentWriter(Protocol):
    """Projects a (possibly mutated) StructuredDocument back to disk."""

    def write(self, doc: StructuredDocument, path: Path) -> None: ...


@runtime_checkable
class MappingStore(Protocol):
    """Persistent original -> pseudonym store for reversible pseudonymization.

    Lifecycle concerns (passphrase, unlock, lock) are intentionally *not* in
    this Protocol — they're specific to the encrypted-file impl. Consumers
    that only read/write mappings depend on this minimal surface; the CLI
    composition root knows the concrete type and drives lifecycle itself.
    """

    def get_or_create(self, original: str, entity_type: str, factory: Callable[[], str]) -> str: ...

    def reverse(self, pseudonym: str, entity_type: str) -> str | None: ...
