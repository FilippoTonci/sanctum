from __future__ import annotations


class SanctumError(Exception):
    """Base exception for all Sanctum domain errors."""


class AnalysisError(SanctumError):
    """Raised when PII analysis fails."""


class AnonymizationError(SanctumError):
    """Raised when anonymization fails."""


class ConfigurationError(SanctumError):
    """Raised for invalid configuration."""


class DocumentError(SanctumError):
    """Raised for document read/write failures."""


class UnsupportedDocumentFormatError(DocumentError):
    """Raised when no registered adapter handles a file's extension."""


class UnsupportedPdfError(DocumentError):
    """Raised when a PDF cannot be text-extracted (e.g. scanned image-only PDFs).

    PDF burn-in redaction is deferred to Phase 3; Phase 1 can only handle
    PDFs that carry an extractable text layer.
    """


class PdfWriteRefusedError(DocumentError):
    """Raised if a caller tries to overwrite a source PDF.

    Phase 1 only produces *derivative* PDFs (a fresh, text-only reportlab
    document). Overwriting the original is disallowed because it would
    silently drop images, forms, and layout — deferred to Phase 3.
    """
