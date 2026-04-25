"""Review-session endpoint family — Sanctum-owned review surface.

Server-side state for the HITL review flow (Phase 1.5 WS2). Clients:

- ``POST /review-sessions`` — analyze, build proposals, persist session.
- ``GET /review-sessions/{id}`` — full session dump + per-proposal + per-
  user-added previews.
- ``PATCH /review-sessions/{id}/decisions/{proposal_id}`` — accept /
  reject a proposal; set operator / params / custom_replacement.
- ``POST /review-sessions/{id}/decisions/user-added`` — add a span the
  reviewer spotted that Sanctum missed.
- ``DELETE /review-sessions/{id}/decisions/user-added/{ua_id}`` — undo.
- ``POST /review-sessions/{id}/commit`` — write the final document,
  tear down the session directory.
- ``DELETE /review-sessions/{id}`` — abandon.

All routes 404 on unknown ids and 409 on mutations against a session
that is no longer ``open``. Previews are recomputed server-side so the
UI never needs to know how an operator renders — the response is the
authoritative cache.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, current_app

from sanctum.api._internal import parse_body, validate_local_path
from sanctum.api.auth import require_bearer_token
from sanctum.api.schemas import (
    AddUserAddedDecisionRequest,
    CommitReviewSessionRequest,
    CommitReviewSessionResponse,
    CreateReviewSessionRequest,
    DecisionWithPreviewResponse,
    PatchProposalDecisionRequest,
    ReviewSessionResponse,
)
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import (
    AnalysisError,
    AnonymizationError,
    DocumentError,
    InvalidOperatorParamsError,
    ReviewSessionAlreadyCommittedError,
    ReviewSessionInvalidDecisionError,
    ReviewSessionNotFoundError,
    UnsupportedDocumentFormatError,
)
from sanctum.core.models import (
    ProposalDecision,
    ReviewProposal,
    ReviewSession,
    UserAddedDecision,
)
from sanctum.core.protocols import MappingStore
from sanctum.core.review.preview_store import PreviewMappingStore
from sanctum.core.review.previews import compute_preview
from sanctum.core.review.session import abandon as abandon_session
from sanctum.core.review.session import add_decision
from sanctum.core.review.store import SessionStore
from sanctum.documents import adapter_for

review_sessions_bp = Blueprint("review_sessions", __name__, url_prefix="/review-sessions")


# ----- shared plumbing -----------------------------------------------------


def _get_engine() -> SanctumEngine | None:
    engine = current_app.config.get("SANCTUM_ENGINE")
    return engine if isinstance(engine, SanctumEngine) else None


def _get_store() -> SessionStore | None:
    store = current_app.config.get("SANCTUM_SESSION_STORE")
    return store if isinstance(store, SessionStore) else None


def _engine_and_store() -> tuple[SanctumEngine, SessionStore] | tuple[None, tuple[dict, int]]:
    """Either return both dependencies or an error tuple — never half of each."""
    engine = _get_engine()
    if engine is None:
        current_app.logger.error("/review-sessions called but SANCTUM_ENGINE is unconfigured")
        return None, ({"error": "engine not configured"}, 503)
    store = _get_store()
    if store is None:
        current_app.logger.error(
            "/review-sessions called but SANCTUM_SESSION_STORE is unconfigured"
        )
        return None, ({"error": "session store not configured"}, 503)
    return engine, store


def _load_session(
    store: SessionStore, session_id: str
) -> tuple[ReviewSession, None] | tuple[None, tuple[dict, int]]:
    try:
        return store.load(session_id), None
    except ReviewSessionNotFoundError:
        return None, ({"error": f"session {session_id!r} not found"}, 404)


def _require_open(session: ReviewSession) -> tuple[dict, int] | None:
    """Return a 409 tuple if the session can no longer accept mutations."""
    if session.status != "open":
        return (
            {"error": f"session is {session.status}; mutations are no longer permitted"},
            409,
        )
    return None


def _session_uses_pseudonymize(session: ReviewSession) -> bool:
    """True iff any decision that will actually be anonymized resolves to pseudonymize.

    Only accepted proposal decisions and user-added decisions run
    through the anonymizer at commit; rejected proposals and
    undecided ones are left alone, so they can't drag a store
    requirement in.
    """
    for d in session.decisions:
        if isinstance(d, ProposalDecision):
            if d.status != "accept":
                continue
            effective = d.operator or session.default_operator
        else:
            effective = d.operator or session.default_operator
        if effective == "pseudonymize":
            return True
    return False


def _unlocked_store_for_commit(
    session: ReviewSession,
) -> MappingStore | None | tuple[dict, int]:
    """Return the unlocked store, ``None`` if not needed, or an error tuple.

    Commit only needs a store when at least one accepted / user-added
    decision resolves to ``pseudonymize``. For other operator mixes
    the absence of a store is fine — the returned ``None`` never
    reaches the anonymizer.
    """
    needs_store = _session_uses_pseudonymize(session)
    raw = current_app.config.get("SANCTUM_MAPPING_STORE")
    if not needs_store:
        return None
    if raw is None:
        return (
            {
                "error": (
                    "commit requires a mapping store for pseudonymize decisions; "
                    "none has been unlocked this session. Call /mapping/unlock first."
                )
            },
            400,
        )
    if not getattr(raw, "is_unlocked", False):
        return (
            {
                "error": (
                    "the mapping store is currently locked; unlock it via "
                    "/mapping/unlock before committing a pseudonymize session"
                )
            },
            409,
        )
    # ``raw`` came out of an untyped Flask config slot; the isinstance
    # guard above confirms the MappingStore surface, so cast-narrow to
    # satisfy mypy without loosening the upstream type.
    assert isinstance(raw, MappingStore)
    return raw


def _preview_store() -> PreviewMappingStore | None:
    """Wrap the app's unlocked ``MappingStore`` in a non-persisting façade.

    Returns ``None`` when no store is unlocked — pseudonymize previews
    raise in that case, and the caller renders the preview slot with an
    error sentinel rather than crashing the whole GET.
    """
    raw = current_app.config.get("SANCTUM_MAPPING_STORE")
    if raw is None:
        return None
    if not getattr(raw, "is_unlocked", False):
        return None
    return PreviewMappingStore(raw)


def _compute_previews(session: ReviewSession, engine: SanctumEngine) -> dict[str, str]:
    """Render the full preview set for a session.

    - Proposals with no decision → preview under the session default.
    - Proposals with an accept decision → preview under the decision's
      operator / params (fallback default) / custom_replacement.
    - Proposals with a reject decision → ``proposal.original`` verbatim,
      so the UI's ghost-text reads "this stays unchanged".
    - User-added decisions → preview under their own operator / params
      / custom (same fallback chain).

    Pseudonymize previews consult the unlocked mapping store through a
    ``PreviewMappingStore`` wrapper: existing mappings are reused, new
    ones are minted but *not* persisted, so the reviewer sees the same
    pseudonym commit would produce without leaking mappings into the
    encrypted file. If no store is unlocked, pseudonymize previews
    render as a sentinel so the route stays a 200.
    """
    previews: dict[str, str] = {}
    # Build a proposal-id → decision map for quick lookup.
    proposal_decisions: dict[str, ProposalDecision] = {}
    user_added: list[UserAddedDecision] = []
    for d in session.decisions:
        if isinstance(d, ProposalDecision):
            proposal_decisions[d.proposal_id] = d
        else:
            user_added.append(d)

    anonymizer = engine._anonymizer
    preview_store = _preview_store()

    def _safe_preview(
        *,
        proposal: ReviewProposal,
        operator: str,
        operator_params: dict[str, Any] | None,
        custom_replacement: str | None,
    ) -> str:
        if operator == "pseudonymize" and preview_store is None:
            return "<pseudonymize preview unavailable — unlock the mapping store>"
        return compute_preview(
            proposal=proposal,
            operator=operator,
            operator_params=operator_params,
            custom_replacement=custom_replacement,
            anonymizer=anonymizer,
            mapping_store=preview_store,
        )

    for proposal in session.proposals:
        decision = proposal_decisions.get(proposal.detection_id)
        if decision is None:
            previews[proposal.detection_id] = _safe_preview(
                proposal=proposal,
                operator=session.default_operator,
                operator_params=session.default_operator_params,
                custom_replacement=None,
            )
            continue
        if decision.status == "reject":
            previews[proposal.detection_id] = proposal.original
            continue
        previews[proposal.detection_id] = _safe_preview(
            proposal=proposal,
            operator=decision.operator or session.default_operator,
            operator_params=(
                decision.operator_params
                if decision.operator_params is not None
                else session.default_operator_params
            ),
            custom_replacement=decision.custom_replacement,
        )

    for ua in user_added:
        shim = ReviewProposal(
            detection_id=ua.id,
            entity_type=ua.entity_type,
            score=1.0,
            original=ua.original,
            # See `_run_single_detection_for_preview` in engine.py for
            # the rationale: shim proposals exist only to call
            # `compute_preview`, which never reads start/end.
            start=ua.start,
            end=ua.end,
        )
        previews[ua.id] = _safe_preview(
            proposal=shim,
            operator=ua.operator or session.default_operator,
            operator_params=(
                ua.operator_params
                if ua.operator_params is not None
                else session.default_operator_params
            ),
            custom_replacement=ua.custom_replacement,
        )

    return previews


def _session_response(session: ReviewSession, engine: SanctumEngine) -> dict[str, Any]:
    previews = _compute_previews(session, engine)
    payload = ReviewSessionResponse(
        id=session.id,
        source_path=str(session.source_path),
        format=session.format,
        default_operator=session.default_operator,
        default_operator_params=dict(session.default_operator_params),
        segments=list(session.segments),
        proposals=list(session.proposals),
        decisions=list(session.decisions),
        status=session.status,
        created_at=session.created_at,
        committed_at=session.committed_at,
        previews=previews,
    )
    return payload.model_dump(mode="json")


# ----- routes --------------------------------------------------------------


@review_sessions_bp.post("")
@require_bearer_token
def create_session() -> tuple[dict, int]:
    engine_or_err = _engine_and_store()
    if engine_or_err[0] is None:
        return engine_or_err[1]
    engine, store = engine_or_err

    req, err = parse_body(CreateReviewSessionRequest)
    if err is not None:
        return err

    in_path, in_err = validate_local_path(req.input_path, must_exist=True)
    if in_err is not None:
        return {"error": f"input_path: {in_err}"}, 400
    assert in_path is not None

    try:
        reader, _ = adapter_for(in_path)
    except UnsupportedDocumentFormatError as exc:
        return {"error": str(exc)}, 415

    try:
        session = engine.create_review_session(
            reader=reader,
            input_path=in_path,
            default_operator=req.default_operator,
            session_store=store,
            default_operator_params=req.default_operator_params,
            language=req.language,
            entities=req.entities,
            score_threshold=req.score_threshold,
        )
    except DocumentError as exc:
        current_app.logger.exception("POST /review-sessions: DocumentError for %s", in_path)
        return {"error": f"document failure: {exc}"}, 500
    except AnalysisError as exc:
        current_app.logger.exception("POST /review-sessions: analysis failed")
        return {"error": f"analysis failed: {exc}"}, 500

    return _session_response(session, engine), 201


@review_sessions_bp.get("/<session_id>")
@require_bearer_token
def get_session(session_id: str) -> tuple[dict, int]:
    engine_or_err = _engine_and_store()
    if engine_or_err[0] is None:
        return engine_or_err[1]
    engine, store = engine_or_err

    session, err = _load_session(store, session_id)
    if err is not None:
        return err
    assert session is not None

    return _session_response(session, engine), 200


@review_sessions_bp.patch("/<session_id>/decisions/<proposal_id>")
@require_bearer_token
def patch_proposal_decision(session_id: str, proposal_id: str) -> tuple[dict, int]:
    engine_or_err = _engine_and_store()
    if engine_or_err[0] is None:
        return engine_or_err[1]
    engine, store = engine_or_err

    req, err = parse_body(PatchProposalDecisionRequest)
    if err is not None:
        return err

    session, load_err = _load_session(store, session_id)
    if load_err is not None:
        return load_err
    assert session is not None

    open_err = _require_open(session)
    if open_err is not None:
        return open_err

    decision = ProposalDecision(
        proposal_id=proposal_id,
        status=req.status,
        operator=req.operator,
        operator_params=req.operator_params,
        custom_replacement=req.custom_replacement,
    )
    try:
        add_decision(session, decision)
    except ReviewSessionInvalidDecisionError:
        return (
            {"error": f"proposal {proposal_id!r} not found in session {session_id!r}"},
            404,
        )
    except ReviewSessionAlreadyCommittedError:
        # Race: status flipped between _require_open and add_decision.
        return (
            {"error": f"session is {session.status}; mutations are no longer permitted"},
            409,
        )

    store.save(session)
    preview = _compute_previews(session, engine)[proposal_id]
    payload = DecisionWithPreviewResponse(decision=decision, preview=preview)
    return payload.model_dump(mode="json"), 200


@review_sessions_bp.post("/<session_id>/decisions/user-added")
@require_bearer_token
def add_user_added_decision(session_id: str) -> tuple[dict, int]:
    engine_or_err = _engine_and_store()
    if engine_or_err[0] is None:
        return engine_or_err[1]
    engine, store = engine_or_err

    req, err = parse_body(AddUserAddedDecisionRequest)
    if err is not None:
        return err

    session, load_err = _load_session(store, session_id)
    if load_err is not None:
        return load_err
    assert session is not None

    open_err = _require_open(session)
    if open_err is not None:
        return open_err

    segments_by_id = {s.id: s for s in session.segments}
    target_segment = segments_by_id.get(req.segment_anchor)
    if target_segment is None:
        return (
            {"error": f"segment_anchor {req.segment_anchor!r} not found in session"},
            400,
        )

    if req.end > len(target_segment.text):
        return (
            {
                "error": (
                    f"end ({req.end}) exceeds segment text length " f"({len(target_segment.text)})"
                )
            },
            400,
        )
    actual = target_segment.text[req.start : req.end]
    if actual != req.original:
        return (
            {
                "error": (
                    f"original {req.original!r} does not match segment "
                    f"slice [{req.start}:{req.end}] = {actual!r}"
                )
            },
            400,
        )

    decision = UserAddedDecision(
        segment_anchor=req.segment_anchor,
        entity_type=req.entity_type,
        original=req.original,
        start=req.start,
        end=req.end,
        operator=req.operator,
        operator_params=req.operator_params,
        custom_replacement=req.custom_replacement,
    )
    try:
        add_decision(session, decision)
    except ReviewSessionAlreadyCommittedError:
        return (
            {"error": f"session is {session.status}; mutations are no longer permitted"},
            409,
        )
    store.save(session)

    preview = _compute_previews(session, engine)[decision.id]
    payload = DecisionWithPreviewResponse(decision=decision, preview=preview)
    return payload.model_dump(mode="json"), 201


@review_sessions_bp.delete("/<session_id>/decisions/user-added/<ua_id>")
@require_bearer_token
def delete_user_added_decision(session_id: str, ua_id: str) -> tuple[dict, int] | tuple[str, int]:
    _, store_err = _engine_and_store()
    if store_err is not None and isinstance(store_err, tuple):
        return store_err
    store = _get_store()
    assert store is not None

    session, load_err = _load_session(store, session_id)
    if load_err is not None:
        return load_err
    assert session is not None

    open_err = _require_open(session)
    if open_err is not None:
        return open_err

    before = len(session.decisions)
    session.decisions = [
        d for d in session.decisions if not (isinstance(d, UserAddedDecision) and d.id == ua_id)
    ]
    if len(session.decisions) == before:
        return (
            {"error": f"user-added decision {ua_id!r} not found in session {session_id!r}"},
            404,
        )
    store.save(session)
    return "", 204


@review_sessions_bp.post("/<session_id>/commit")
@require_bearer_token
def commit_session(session_id: str) -> tuple[dict, int]:
    engine_or_err = _engine_and_store()
    if engine_or_err[0] is None:
        return engine_or_err[1]
    engine, store = engine_or_err

    req, err = parse_body(CommitReviewSessionRequest)
    if err is not None:
        return err

    if not req.attested:
        return (
            {
                "error": (
                    "commit requires attested=true; the API cannot prompt, so the "
                    "caller must assert human review before the final file is written."
                )
            },
            400,
        )

    session, load_err = _load_session(store, session_id)
    if load_err is not None:
        return load_err
    assert session is not None

    if session.status != "open":
        return (
            {"error": f"session is {session.status}; commit is no longer permitted"},
            409,
        )

    out_path, out_err = validate_local_path(req.output_path, must_exist=False)
    if out_err is not None:
        return {"error": f"output_path: {out_err}"}, 400
    assert out_path is not None

    try:
        reader, writer = adapter_for(session.source_path)
    except UnsupportedDocumentFormatError as exc:
        return {"error": str(exc)}, 415

    # Resolve the unlocked mapping store once so both the session-default
    # path *and* any per-decision override that asked for pseudonymize
    # see the same persisted minter. ``None`` is fine for non-
    # pseudonymize sessions; the anonymizer only reaches for it when a
    # pseudonymize decision is actually being applied.
    mapping_store = _unlocked_store_for_commit(session)
    if isinstance(mapping_store, tuple):
        return mapping_store

    committed_at = datetime.now(timezone.utc)
    try:
        engine.commit_review_session(
            reader=reader,
            writer=writer,
            session_id=session_id,
            output_path=out_path,
            session_store=store,
            mapping_store=mapping_store,
            committed_at=committed_at,
        )
    except ReviewSessionAlreadyCommittedError:
        # Race: session committed between our status check and the engine
        # call. Map to 409 the same as the eager check above.
        return (
            {"error": f"session is {session.status}; commit is no longer permitted"},
            409,
        )
    except InvalidOperatorParamsError as exc:
        current_app.logger.info("/commit: invalid operator params: %s", exc)
        return {"error": f"invalid operator_params: {exc}"}, 400
    except DocumentError as exc:
        current_app.logger.exception("POST /review-sessions/%s/commit: DocumentError", session_id)
        return {"error": f"document failure: {exc}"}, 500
    except AnonymizationError as exc:
        current_app.logger.exception(
            "POST /review-sessions/%s/commit: anonymization failed", session_id
        )
        return {"error": f"anonymization failed: {exc}"}, 500

    # Engine mutated the stored session with the same ``committed_at`` we
    # pinned above and deleted the on-disk dir. Echoing our local copy
    # avoids a reload for a timestamp we already own.
    payload = CommitReviewSessionResponse(
        session_id=session_id,
        output_path=str(out_path),
        committed_at=committed_at,
    )
    return payload.model_dump(mode="json"), 200


@review_sessions_bp.delete("/<session_id>")
@require_bearer_token
def abandon(session_id: str) -> tuple[dict, int] | tuple[str, int]:
    store = _get_store()
    if store is None:
        current_app.logger.error(
            "/review-sessions DELETE called but SANCTUM_SESSION_STORE is unconfigured"
        )
        return {"error": "session store not configured"}, 503

    session, load_err = _load_session(store, session_id)
    if load_err is not None:
        return load_err
    assert session is not None

    if session.status != "open":
        # Abandoning something already terminal is a client bug — refuse
        # rather than return a silent "ok" that papers over the mismatch.
        return (
            {"error": f"session is {session.status}; cannot abandon"},
            409,
        )

    abandon_session(session)
    store.delete(session_id)
    return "", 204
