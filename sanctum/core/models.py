from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ReviewProposal(BaseModel):
    """Metadata for one Sanctum-originated anonymization proposal.

    Represents a single detection Sanctum wants to anonymize, with enough
    context for a reviewer to verify or override it: entity type, score,
    the original text, and the replacement that would land in the
    committed document.

    ``detection_id`` is a stable content-addressed hash (see
    ``sanctum.documents.review.make_detection_id``) that doubles as the
    session's proposal id — a reviewer tool can't renumber it, which
    keeps ``ProposalDecision.proposal_id`` references stable across
    session reads.

    ``segment_anchor`` is an opaque, adapter-specific pointer into the
    parsed ``StructuredDocument`` segments (e.g. docx paragraph+run
    indices, xlsx sheet+cell). Optional for backwards-compat with the
    DOCX comment-export path (WS5) whose trailer format does not encode
    it — session-driven code paths always populate it.
    """

    model_config = {"frozen": True}

    detection_id: str
    entity_type: str
    score: float
    original: str
    replacement: str
    operator: str
    segment_anchor: str | None = None


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
    staged: ReviewProposal | None = None
    user_comment_body: str | None = None
    user_anchor_text: str | None = None


# --- Phase 1.5 WS2 — server-owned review sessions ------------------------
#
# A ``ReviewSession`` is the single source of truth for one human review,
# created when ``process-file --review`` runs and persisted under
# ``~/.sanctum/sessions/<id>/`` until commit or abandon. The API layer
# mutates it as the reviewer works; the UI projects it.
#
# Decisions come in two shapes, carried as a discriminated union so a
# single ``decisions`` list on the session is enough: ``ProposalDecision``
# references an existing ``ReviewProposal``; ``UserAddedDecision`` is a
# fully-specified span the reviewer added (a miss Sanctum didn't catch).


SessionStatus = Literal["open", "committed", "abandoned"]

ProposalDecisionStatus = Literal["accept", "reject", "edit"]


class ProposalDecision(BaseModel):
    """A reviewer's verdict on a Sanctum-originated proposal.

    ``proposal_id`` references the matching ``ReviewProposal.detection_id``
    inside the same session. Status semantics:

    - ``accept`` — apply the proposal's ``replacement`` to the committed file.
    - ``reject`` — leave the original text in place.
    - ``edit`` — apply ``edited_replacement`` instead of the proposal's
      default. ``edited_replacement`` is required when ``status == "edit"``.
    """

    model_config = {"frozen": True}

    kind: Literal["proposal"] = "proposal"
    proposal_id: str
    status: ProposalDecisionStatus
    edited_replacement: str | None = None

    @model_validator(mode="after")
    def _check_edit_replacement(self) -> ProposalDecision:
        if self.status == "edit" and self.edited_replacement is None:
            raise ValueError("edited_replacement is required when status='edit'")
        return self


class UserAddedDecision(BaseModel):
    """A reviewer-contributed span — a miss Sanctum did not catch.

    The reviewer identifies a span in the rendered segment that Sanctum
    missed, names its entity type, and supplies the replacement that
    should land in the committed document. For pseudonymize sessions the
    replacement is also staged into ``ReviewSession.staged_mappings`` by
    the WS4 commit path; non-persistent operators just apply the
    replacement directly.
    """

    model_config = {"frozen": True}

    kind: Literal["user_added"] = "user_added"
    segment_anchor: str
    entity_type: str
    original: str
    replacement: str


SessionDecision = Annotated[
    ProposalDecision | UserAddedDecision,
    Field(discriminator="kind"),
]


class ReviewSession(BaseModel):
    """Server-owned state for one human review session.

    Created by ``SanctumEngine.create_review_session`` (WS2 substep 3) when
    ``process-file --review`` runs. Persists under
    ``~/.sanctum/sessions/<id>/`` until committed or abandoned.

    Sessions are mutable by design: decisions accumulate as the reviewer
    works, and ``status`` transitions from ``open`` to ``committed`` /
    ``abandoned`` at the terminal step. The state machine lives in
    ``sanctum.core.review.session`` (WS2 substep 2).

    Pseudonymize is the one operator whose commit has a persistent side
    effect — ``staged_mappings`` holds what would land in the encrypted
    ``MappingStore`` *if* the reviewer commits. Non-persistent operators
    leave ``staged_mappings`` empty.

    Timestamps are UTC; the session store writes them in ISO-8601 form.
    """

    id: str
    source_path: Path
    format: DocumentFormat
    operator: str
    segments: list[TextSegment]
    proposals: list[ReviewProposal]
    decisions: list[SessionDecision] = Field(default_factory=list)
    staged_mappings: list[StagedMapping] = Field(default_factory=list)
    status: SessionStatus = "open"
    created_at: datetime
    committed_at: datetime | None = None
