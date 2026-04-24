"""Pydantic request/response models for the Sanctum API.

One module for every route's wire types so the contract is reviewable in
one place and the GUI team can generate TypeScript clients off a single
import. Models accumulate here as routes land in subsequent substeps.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sanctum.core.models import (
    DetectionResult,
    DocumentFormat,
    ReviewProposal,
    SessionDecision,
    TextSegment,
)

# Hard cap on direct-text endpoints — `/analyze` and `/anonymize` accept
# the body inline, so an unchecked string can dominate server memory long
# before Presidio sees it. Anything larger belongs in `/process-file`,
# which streams from disk and is capped at MAX_INPUT_BYTES instead.
MAX_TEXT_CHARS: Final[int] = 1_000_000

# `custom` takes a Python callable in `params` — no JSON encoding can
# carry that, so it's permanently unreachable over HTTP regardless of
# any future `operator_params` work. `mask` and `encrypt` used to be in
# this set, but now accept their params via the `operator_params` field
# on AnonymizeRequest / ProcessFileRequest.
API_REJECTED_OPERATORS: Final[frozenset[str]] = frozenset({"custom"})


def _reject_api_unsupported_operator(value: str | None) -> str | None:
    """Fail pydantic validation if `value` is an HTTP-unsupported operator."""
    if value is not None and value in API_REJECTED_OPERATORS:
        raise ValueError(
            f"operator {value!r} cannot be used over HTTP — it requires a callable "
            "parameter that cannot be JSON-encoded. Use the CLI for this operator."
        )
    return value


class _Frozen(BaseModel):
    """Base for response models — immutable, no extra fields tolerated."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _StrictRequest(BaseModel):
    """Base for request models — extra fields rejected (client contract)."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(_Frozen):
    """Liveness payload for `/health`.

    `mapping_store_unlocked` is always present so the GUI can render the
    lock indicator from a single field; WS4.7 wires it to the real store
    state when the mapping routes land.
    """

    status: Literal["ok"]
    version: str
    mapping_store_unlocked: bool


class AnalyzeRequest(_StrictRequest):
    """Body for `POST /analyze`.

    Mirrors the knobs the CLI `analyze` command exposes. `entities=None`
    means "detect every entity type the analyzer recognizes"; passing an
    empty list narrows the scan to nothing (Presidio semantics — we do
    not second-guess it).
    """

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    language: str = "en"
    entities: list[str] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class AnalyzeResponse(_Frozen):
    """Body for `POST /analyze` — detections plus a convenience count."""

    detections: list[DetectionResult]
    count: int


class AnonymizeRequest(_StrictRequest):
    """Body for `POST /anonymize`.

    `operator` maps onto the ``DEFAULT`` entry in `OperatorPolicy`-land:
    ``None`` means the engine's configured default. ``pseudonymize`` is
    *not* accepted over HTTP until WS4.7 wires the mapping store — the
    store is per-session state that has to be unlocked explicitly first,
    and letting `/anonymize` silently skip it would produce an
    unrecoverable leak of originals.
    """

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    language: str = "en"
    entities: list[str] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    operator: str | None = None
    operator_params: dict[str, Any] | None = None

    _reject_api_unsupported_operator = field_validator("operator")(_reject_api_unsupported_operator)

    @model_validator(mode="after")
    def _operator_params_requires_operator(self) -> AnonymizeRequest:
        if self.operator_params is not None and self.operator is None:
            raise ValueError("operator_params requires operator to be set")
        return self


class AnonymizeResponse(_Frozen):
    """Body for `POST /anonymize` — full engine result, wire-friendly."""

    original_text: str
    anonymized_text: str
    detections: list[DetectionResult]
    operators_applied: dict[str, str]


class ProcessFileRequest(_StrictRequest):
    """Body for `POST /process-file`.

    Both paths are *server-side* paths (the API is local; the GUI runs on
    the same machine). The route refuses non-absolute paths and UNC paths
    so a malicious client can't coerce the server into reading from or
    writing to a network share — that would defeat the airgap by smuggling
    data across the network mount.

    ``review`` opts into the Flow B review session surface: when True the
    route does not write the anonymized file inline — it creates a review
    session and returns ``ProcessFileReviewResponse`` (session id + URL
    for the WS3 UI). When False, the route anonymizes and writes in one
    shot, returning ``ProcessFileResponse``. ``output_path`` is required
    when ``review=false`` and forbidden when ``review=true`` — the final
    file is produced later at ``POST /review-sessions/{id}/commit`` with
    its own output_path.
    """

    input_path: str
    output_path: str | None = None
    language: str = "en"
    entities: list[str] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    operator: str | None = None
    operator_params: dict[str, Any] | None = None
    review: bool = False

    _reject_api_unsupported_operator = field_validator("operator")(_reject_api_unsupported_operator)

    @model_validator(mode="after")
    def _operator_params_requires_operator(self) -> ProcessFileRequest:
        if self.operator_params is not None and self.operator is None:
            raise ValueError("operator_params requires operator to be set")
        return self

    @model_validator(mode="after")
    def _output_path_matches_mode(self) -> ProcessFileRequest:
        if self.review and self.output_path is not None:
            raise ValueError("output_path must be omitted when review=true")
        if not self.review and self.output_path is None:
            raise ValueError("output_path is required when review=false")
        return self


class ProcessFileResponse(_Frozen):
    """Body for `POST /process-file` — summary of what the engine did."""

    output_path: str
    segments_changed: int
    entities_replaced: int


class ProcessFileReviewResponse(_Frozen):
    """Body for `POST /process-file` when ``review=true`` — Flow B handoff.

    The route created a review session instead of writing the file inline.
    Clients use ``session_id`` to drive the ``/review-sessions`` endpoints
    (PATCH decisions, commit, etc.) and ``review_url`` to open the WS3
    reference UI. ``review_url`` is a template pointing at the server's
    bound host/port — if WS3 hasn't shipped yet the path 404s, but the
    ``session_id`` is fully usable against the API today.
    """

    session_id: str
    review_url: str


class UnlockMappingRequest(_StrictRequest):
    """Body for `POST /mapping/unlock`.

    `store_path` is server-side; same UNC/absolute rules as /process-file.
    A non-existent file is treated as "create on first lock" — matches the
    CLI behavior where `EncryptedFileMappingStore.unlock` on a missing
    path generates a fresh salt and starts with empty entries.
    """

    store_path: str
    passphrase: str = Field(min_length=1)


class UnlockMappingResponse(_Frozen):
    """Body for `POST /mapping/unlock` — the store is now unlocked."""

    unlocked: Literal[True]
    store_path: str


class LockMappingResponse(_Frozen):
    """Body for `POST /mapping/lock` — the store is now locked.

    `store_path` is ``None`` only when `/lock` was a no-op (nothing was
    unlocked to begin with); the idempotent-success payload.
    """

    unlocked: Literal[False]
    store_path: str | None


class ReverseMappingRequest(_StrictRequest):
    """Body for `POST /mapping/reverse` — look up the original behind a pseudonym."""

    pseudonym: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)


class ReverseMappingResponse(_Frozen):
    """Body for `POST /mapping/reverse` — the original that maps to the pseudonym."""

    pseudonym: str
    entity_type: str
    original: str


class RotateMappingKeyRequest(_StrictRequest):
    """Body for `POST /mapping/rotate-key` — re-encrypt under a new passphrase."""

    store_path: str
    old_passphrase: str = Field(min_length=1)
    new_passphrase: str = Field(min_length=1)


class RotateMappingKeyResponse(_Frozen):
    """Body for `POST /mapping/rotate-key` — confirmation of rotation."""

    rotated: bool
    store_path: str


# -------- Review sessions (Phase 1.5 WS2 substep 6) ------------------------
#
# All bodies ride the same `_StrictRequest` / `_Frozen` base so extra fields
# are rejected on input and responses advertise a closed shape. Wire names
# match the underlying ``sanctum.core.models`` fields verbatim — the UI
# treats the session dump as the source of truth and writes decisions back
# with the same field names.


class CreateReviewSessionRequest(_StrictRequest):
    """Body for `POST /review-sessions`.

    ``input_path`` is a server-side absolute path. ``default_operator``
    seeds the fallback operator that decisions inherit when they don't
    override. API clients can't use the ``custom`` operator because it
    takes a Python callable in ``params`` which is not JSON-encodable.
    """

    input_path: str
    default_operator: str
    default_operator_params: dict[str, Any] | None = None
    language: str = "en"
    entities: list[str] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)

    _reject_api_unsupported_operator = field_validator("default_operator")(
        _reject_api_unsupported_operator
    )


class ReviewSessionResponse(_Frozen):
    """Body for create / get — full session dump with computed previews.

    ``previews`` keys are either a ``ReviewProposal.detection_id`` (for
    proposal-scoped previews) or a ``UserAddedDecision.id`` (for user-
    added spans). The UI keys its ghost-text overlay off of those ids,
    so the dict shape intentionally mirrors a client-side cache.

    The preview set is recomputed server-side on every GET and every
    decision-touching PATCH — the session itself never persists a
    ``preview_cache`` field. This keeps session storage reviewable as
    "decisions in, replacements out" instead of a stale mirror.
    """

    id: str
    source_path: str
    format: DocumentFormat
    default_operator: str
    default_operator_params: dict[str, Any]
    segments: list[TextSegment]
    proposals: list[ReviewProposal]
    decisions: list[SessionDecision]
    status: Literal["open", "committed", "abandoned"]
    created_at: datetime
    committed_at: datetime | None
    previews: dict[str, str]


class PatchProposalDecisionRequest(_StrictRequest):
    """Body for `PATCH /review-sessions/{id}/decisions/{proposal_id}`.

    ``status`` is required. Any of ``operator`` / ``operator_params`` /
    ``custom_replacement`` may be present; omitted fields fall back to
    the session defaults (operator / params) or are treated as unset
    (custom_replacement). PATCH is a full replace on the decision — the
    server does not diff the previous decision forward.
    """

    status: Literal["accept", "reject"]
    operator: str | None = None
    operator_params: dict[str, Any] | None = None
    custom_replacement: str | None = None

    _reject_api_unsupported_operator = field_validator("operator")(_reject_api_unsupported_operator)

    @model_validator(mode="after")
    def _operator_params_requires_operator(self) -> PatchProposalDecisionRequest:
        if self.operator_params is not None and self.operator is None:
            raise ValueError("operator_params requires operator to be set")
        return self


class DecisionWithPreviewResponse(_Frozen):
    """Body for PATCH / POST user-added — echoes the decision + its preview.

    Returning the preview on the mutation response spares the UI a
    follow-up GET just to refresh one cell. The full session is still
    fetched via GET when the reviewer navigates across proposals.
    """

    decision: SessionDecision
    preview: str


class AddUserAddedDecisionRequest(_StrictRequest):
    """Body for `POST /review-sessions/{id}/decisions/user-added`.

    ``segment_anchor`` must match the ``id`` of one of the session's
    segments; the API rejects unknown anchors with 400 so the UI catches
    drift early (e.g., a cached session id whose segments have changed
    underneath it — this can't happen in Flow B but the validator
    documents the contract).
    """

    segment_anchor: str
    entity_type: str
    original: str
    operator: str | None = None
    operator_params: dict[str, Any] | None = None
    custom_replacement: str | None = None

    _reject_api_unsupported_operator = field_validator("operator")(_reject_api_unsupported_operator)

    @model_validator(mode="after")
    def _operator_params_requires_operator(self) -> AddUserAddedDecisionRequest:
        if self.operator_params is not None and self.operator is None:
            raise ValueError("operator_params requires operator to be set")
        return self


class CommitReviewSessionRequest(_StrictRequest):
    """Body for `POST /review-sessions/{id}/commit`.

    ``output_path`` is server-side. ``attested`` is a hard gate: the API
    cannot prompt, so the caller must assert on the record that a human
    has reviewed the session before persistent side effects (pseudonymize
    store writes, final file emission) land.
    """

    output_path: str
    attested: bool = False


class CommitReviewSessionResponse(_Frozen):
    """Body for `POST /review-sessions/{id}/commit` — the finalized copy."""

    session_id: str
    output_path: str
    committed_at: datetime
