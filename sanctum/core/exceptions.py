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


class MappingStoreError(SanctumError):
    """Raised for mapping-store read/write/state failures."""


class IncorrectPassphraseError(MappingStoreError):
    """Raised when an `unlock()` attempt fails AEAD authentication.

    Surfaced explicitly so callers don't have to interpret the underlying
    `cryptography.exceptions.InvalidTag`. A failed tag check in this
    context almost always means wrong passphrase (and occasionally
    tampering — same remediation from the user's perspective).
    """


class MappingStoreLockedError(MappingStoreError):
    """Raised when a locked store is asked to read, write, or mutate."""
