"""Core domain layer — models, protocols, engine, and exceptions."""

from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import (
    AnalysisError,
    AnonymizationError,
    ConfigurationError,
    DocumentError,
    SanctumError,
)
from sanctum.core.models import AnonymizationResult, DetectionResult, OperatorPolicy
from sanctum.core.protocols import Analyzer, Anonymizer, DocumentReader, DocumentWriter

__all__ = [
    "AnalysisError",
    "AnonymizationError",
    "AnonymizationResult",
    "Analyzer",
    "Anonymizer",
    "ConfigurationError",
    "DetectionResult",
    "DocumentError",
    "DocumentReader",
    "DocumentWriter",
    "OperatorPolicy",
    "SanctumEngine",
    "SanctumError",
]
