"""Core domain layer — models, protocols, engine, and exceptions."""

from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import (
    AnalysisError,
    AnonymizationError,
    ConfigurationError,
    DocumentError,
    PdfWriteRefusedError,
    SanctumError,
    UnsupportedDocumentFormatError,
    UnsupportedPdfError,
)
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    DocumentFormat,
    OperatorPolicy,
    StructuredDocument,
    TextSegment,
)
from sanctum.core.protocols import (
    Analyzer,
    Anonymizer,
    DocumentReader,
    DocumentWriter,
    StructuredDocumentReader,
    StructuredDocumentWriter,
)

__all__ = [
    "AnalysisError",
    "Analyzer",
    "AnonymizationError",
    "AnonymizationResult",
    "Anonymizer",
    "ConfigurationError",
    "DetectionResult",
    "DocumentError",
    "DocumentFormat",
    "DocumentReader",
    "DocumentWriter",
    "OperatorPolicy",
    "PdfWriteRefusedError",
    "SanctumEngine",
    "SanctumError",
    "StructuredDocument",
    "StructuredDocumentReader",
    "StructuredDocumentWriter",
    "TextSegment",
    "UnsupportedDocumentFormatError",
    "UnsupportedPdfError",
]
