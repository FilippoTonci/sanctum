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
