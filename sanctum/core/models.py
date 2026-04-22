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
    """User-configurable anonymization operator for an entity type.

    Valid values for `operator_name` are listed with descriptions in
    `sanctum.anonymizer.operators.BUILTIN_OPERATOR_NAMES`.
    """

    operator_name: str
    params: dict[str, Any] = Field(default_factory=dict)


class AnonymizationResult(BaseModel):
    """The outcome of an anonymization pass over a document."""

    model_config = {"frozen": True}

    original_text: str
    anonymized_text: str
    detections: list[DetectionResult]
    operators_applied: dict[str, str]
    # Per-detection replacement strings, aligned 1:1 with ``detections``.
    # The adapter populates this when it has visibility into the operator
    # output (Presidio's ``EngineResult.items`` carries per-item text);
    # review-emitting writers (Phase 1.5 WS2+) use it to build the
    # ``<!-- sanctum: -->`` trailer for each detection. ``None`` means
    # the adapter couldn't recover per-detection replacements — review
    # mode must refuse to emit against such a result.
    per_detection_replacements: list[str] | None = None


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


# Phase 1.5 — review workflow. The trailer version is embedded in every
# `sanctum:` comment so future schema changes can be migrated or rejected
# cleanly rather than silently misparsed.
REVIEW_TRAILER_VERSION = 1


class ReviewComment(BaseModel):
    """Metadata for one detection emitted into a review file's comment.

    Carries everything a reviewer (and, for pseudonymize, ``commit-review``)
    needs to understand what Sanctum did and reverse it: entity type,
    confidence, the original text, and the replacement that now sits in
    the document. Serialized into the ``<!-- sanctum:... -->`` trailer at
    the end of each review comment.

    ``detection_id`` is a stable content-addressed hash so copy-paste in
    Word (which renumbers comment ids) does not break reconciliation.
    """

    model_config = {"frozen": True}

    detection_id: str
    entity_type: str
    score: float
    original: str
    replacement: str
    operator: str


class StagedMapping(BaseModel):
    """A pseudonymize mapping staged pending human approval.

    Pseudonymize's pass 1 emits these into the review file's comment
    trailers *without* writing to the encrypted mapping store. The
    ``commit-review`` step reads them back, reconciles against the
    reviewed document's current state (accepted / rejected / user-added),
    and only then persists to the store.

    This is the one piece of Sanctum state that deliberately lives in the
    user-facing review file before being committed — which is why trailer
    stripping on commit is a first-order correctness requirement.
    """

    model_config = {"frozen": True}

    detection_id: str
    entity_type: str
    original: str
    pseudonym: str


ReviewDecisionKind = Literal["accepted", "rejected", "user_added"]


class ReviewDecision(BaseModel):
    """One outcome extracted from a reviewed document.

    The three kinds correspond to the reconciliation states described in
    the Phase 1.5 plan:

    - ``accepted``: Sanctum's trailer is present *and* the replacement
      text still occupies the span — the reviewer left the change alone.
      ``staged`` carries the parsed trailer.
    - ``rejected``: Sanctum's trailer is present but the replacement text
      has been restored to the original — the reviewer undid the change.
      ``staged`` still carries the trailer so the engine knows what *not*
      to commit.
    - ``user_added``: a reviewer-authored comment with no Sanctum trailer.
      ``user_comment_body`` and ``user_anchor_text`` describe the span
      the reviewer flagged so ``commit-review`` can prompt for / generate
      a pseudonym.

    Kept as a tagged union with optional fields rather than separate
    classes so adapters can emit one flat list.
    """

    model_config = {"frozen": True}

    kind: ReviewDecisionKind
    staged: ReviewComment | None = None
    user_comment_body: str | None = None
    user_anchor_text: str | None = None
