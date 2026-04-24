from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

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


class ReviewProposal(BaseModel):
    """A single detection presented to the reviewer (Flow B).

    Pure detection record — no replacement, no operator. Under Flow B
    the reviewer chooses the operator at decision time; replacements
    come from the anonymizer at commit time (or from a server-computed
    preview for the UI). The proposal just tells the reviewer *what*
    Sanctum found and *where*.

    ``detection_id`` is a stable content-addressed hash (see
    ``sanctum.core.review.identifiers.make_detection_id``) that doubles
    as the session's proposal id so ``ProposalDecision.proposal_id``
    references stay stable across session reads.

    ``segment_anchor`` is an opaque, adapter-specific pointer into the
    parsed ``StructuredDocument`` segments (e.g. docx paragraph+run
    indices, xlsx sheet+cell).
    """

    model_config = {"frozen": True}

    detection_id: str
    entity_type: str
    score: float
    original: str
    segment_anchor: str | None = None


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

ProposalDecisionStatus = Literal["accept", "reject"]


class ProposalDecision(BaseModel):
    """A reviewer's verdict on a Sanctum-originated proposal (Flow B).

    ``proposal_id`` references the matching ``ReviewProposal.detection_id``
    inside the same session.

    Status semantics:

    - ``accept`` — anonymize this span at commit. The operator is
      ``operator`` if set, otherwise the session's ``default_operator``;
      same for ``operator_params``. ``custom_replacement``, if set,
      short-circuits the operator and lands in the file verbatim.
    - ``reject`` — leave the original text in place. Operator / params /
      custom_replacement are ignored.
    """

    model_config = {"frozen": True}

    kind: Literal["proposal"] = "proposal"
    proposal_id: str
    status: ProposalDecisionStatus
    operator: str | None = None
    operator_params: dict[str, Any] | None = None
    custom_replacement: str | None = None


class UserAddedDecision(BaseModel):
    """A reviewer-contributed span — a miss Sanctum did not catch (Flow B).

    Same operator-selection semantics as ``ProposalDecision``: ``operator``
    and ``operator_params`` fall back to the session defaults when unset;
    ``custom_replacement`` wins over the operator when set.

    ``id`` is a UUID4 minted when the decision is added; it is the handle
    the ``DELETE /review-sessions/{id}/decisions/user-added/{ua_id}``
    route uses. Unlike ``ProposalDecision.proposal_id`` (which is a stable
    content-addressed hash shared with the native-comment export path),
    user-added ids have no meaning outside the session — they just need
    to be unique within it.
    """

    model_config = {"frozen": True}

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["user_added"] = "user_added"
    segment_anchor: str
    entity_type: str
    original: str
    operator: str | None = None
    operator_params: dict[str, Any] | None = None
    custom_replacement: str | None = None


SessionDecision = Annotated[
    ProposalDecision | UserAddedDecision,
    Field(discriminator="kind"),
]


class ReviewSession(BaseModel):
    """Server-owned state for one human review session (Flow B).

    Created by ``SanctumEngine.create_review_session`` when
    ``process-file --review`` runs. Persists under
    ``~/.sanctum/sessions/<id>/`` until committed or abandoned.

    Sessions are mutable by design: decisions accumulate as the reviewer
    works, and ``status`` transitions from ``open`` to ``committed`` /
    ``abandoned`` at the terminal step. The state machine lives in
    ``sanctum.core.review.session``.

    Flow B shape:

    - Proposals are pure detections; no pre-computed replacement lives
      on the session. The anonymizer runs per accepted/user-added
      decision at commit time.
    - ``default_operator`` + ``default_operator_params`` are the
      fallback the UI displays and uses for decisions with no per-
      decision operator override. The CLI's ``--operator`` flag at
      ``process-file --review`` time seeds these.
    - Preview strings are computed on demand by the API layer via
      ``sanctum.core.review.previews.compute_preview`` — they are not
      persisted on the session.

    Timestamps are UTC; the session store writes them in ISO-8601 form.
    """

    id: str
    source_path: Path
    format: DocumentFormat
    default_operator: str
    default_operator_params: dict[str, Any] = Field(default_factory=dict)
    segments: list[TextSegment]
    proposals: list[ReviewProposal]
    decisions: list[SessionDecision] = Field(default_factory=list)
    status: SessionStatus = "open"
    created_at: datetime
    committed_at: datetime | None = None
