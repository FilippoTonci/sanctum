from __future__ import annotations


class SanctumError(Exception):
    """Base exception for all Sanctum domain errors."""


class AnalysisError(SanctumError):
    """Raised when PII analysis fails."""


class AnonymizationError(SanctumError):
    """Raised when anonymization fails."""


class InvalidOperatorParamsError(AnonymizationError):
    """Raised when a Presidio operator is given malformed parameters.

    Subclass of ``AnonymizationError`` so existing catch-all handlers
    still see it, but exposed as a distinct type so the HTTP layer can
    map a caller-supplied bad param (e.g. a too-short ``encrypt`` key,
    or ``mask`` with a missing ``masking_char``) to a 400 Bad Request
    instead of a 500 Internal Server Error.
    """


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


class ReviewError(SanctumError):
    """Base for review-workflow failures (Phase 1.5)."""


class StagedMappingParseError(ReviewError):
    """Raised when a Sanctum trailer in a review file is malformed.

    The trailer is the single source of truth for staged mappings; a parse
    failure means the reviewed file cannot be safely committed and the
    caller needs to re-emit it rather than ship with a partially-parsed
    mapping set.
    """


class CommitReviewOperatorMismatchError(ReviewError):
    """Raised when `commit-review` runs on a file not staged by pseudonymize.

    `commit-review` only makes sense for the pseudonymize operator — it is
    the one operator whose output includes local persistent state that
    must be committed post-review. Running it against a file staged by
    `replace`/`redact`/etc. is a user mistake; we fail loudly rather than
    silently writing nothing.
    """


class AlreadyCommittedError(ReviewError):
    """Raised when `commit-review` runs on a file that was already committed.

    The commit step strips all `sanctum:` trailers from the final file as
    it writes. A second run against the same file therefore finds no
    trailers and would silently do nothing — which looks like success but
    is almost always user error (wrong file, wrong store). Raise instead.
    """


class ReviewSessionNotFoundError(ReviewError):
    """Raised when a session id does not resolve to an on-disk session.

    Covers both "session was never created" and "session was deleted or
    abandoned" — the session store does not distinguish, because the API
    layer maps both to 404.
    """


class ReviewSessionAlreadyCommittedError(ReviewError):
    """Raised when a mutation is attempted on a non-open session.

    Applies to both ``committed`` and ``abandoned`` states: the state
    machine treats them as equivalent terminal conditions — any further
    mutation (add decision, commit, abandon) is a programming error.
    """


class ReviewSessionInvalidDecisionError(ReviewError):
    """Raised when a decision references an unknown proposal id.

    ``ProposalDecision.proposal_id`` must match the ``detection_id`` of a
    proposal on the same session. A mismatch usually means the UI is
    stale (the session was rebuilt underneath it) — fail loudly so the
    caller refetches.
    """
