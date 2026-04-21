"""`/analyze` + `/anonymize` routes — engine-facing pipeline endpoints.

Both routes are authenticated (bearer token) and run the same engine the
CLI uses; they are effectively a JSON façade over `SanctumEngine.analyze`
and `SanctumEngine.process`. The mapping-store-backed `pseudonymize`
operator requires the store to be unlocked first via `/mapping/unlock`.
Two distinct error responses tell the client whether they forgot to
unlock (409) or whether the server has never had a store unlocked (400).
"""

from __future__ import annotations

from flask import Blueprint, current_app

from sanctum.api import MAX_INPUT_BYTES
from sanctum.api._internal import parse_body, validate_local_path
from sanctum.api.auth import require_bearer_token
from sanctum.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnonymizeRequest,
    AnonymizeResponse,
    CommitReviewRequest,
    CommitReviewResponse,
    ProcessFileRequest,
    ProcessFileResponse,
)
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import (
    AnalysisError,
    AnonymizationError,
    DocumentError,
    InvalidOperatorParamsError,
    UnsupportedDocumentFormatError,
)
from sanctum.core.models import OperatorPolicy
from sanctum.core.protocols import MappingStore
from sanctum.documents import adapter_for

# Re-exported so legacy callers (including existing unit tests) that
# imported `MAX_INPUT_BYTES` from the route module keep working. The
# authoritative definition now lives in `sanctum.api.__init__`.
__all__ = ["MAX_INPUT_BYTES", "pipeline_bp"]

pipeline_bp = Blueprint("pipeline", __name__)


def _get_engine() -> SanctumEngine | None:
    engine = current_app.config.get("SANCTUM_ENGINE")
    return engine if isinstance(engine, SanctumEngine) else None


def _unlocked_store() -> MappingStore | None:
    """Return the active mapping store iff one is currently unlocked."""
    store = current_app.config.get("SANCTUM_MAPPING_STORE")
    if store is None:
        return None
    is_unlocked = getattr(store, "is_unlocked", False)
    return store if is_unlocked else None


def _build_pseudonymize_policies(store: MappingStore, language: str) -> dict[str, OperatorPolicy]:
    """Mirror of `cli.commands._pseudonymize_policies`, kept in sync intentionally."""
    return {
        "DEFAULT": OperatorPolicy(
            operator_name="pseudonymize",
            params={"store": store, "language": language},
        )
    }


def _pseudonymize_unavailable() -> tuple[dict, int]:
    """Distinguish 'store is locked' (409) from 'no store ever unlocked' (400).

    409 Conflict signals "you probably forgot /mapping/unlock; retry after
    unlocking"; 400 signals "server has no store configured for this session
    — unlocking one is the caller's responsibility." Conflating the two was
    the original reviewer complaint — a client couldn't decide whether to
    prompt the user for a passphrase or to surface a permanent error.
    """
    raw = current_app.config.get("SANCTUM_MAPPING_STORE")
    if raw is None:
        current_app.logger.info(
            "pseudonymize requested but no mapping store has been unlocked this session"
        )
        return {
            "error": (
                "pseudonymize requires an encrypted mapping store; none has been unlocked "
                "this session. Call /mapping/unlock first."
            )
        }, 400
    current_app.logger.info("pseudonymize requested but the mapping store is locked")
    return {
        "error": "the mapping store is currently locked; unlock it via /mapping/unlock first",
    }, 409


@pipeline_bp.post("/analyze")
@require_bearer_token
def analyze() -> tuple[dict, int]:
    engine = _get_engine()
    if engine is None:
        current_app.logger.error("/analyze called but SANCTUM_ENGINE is unconfigured")
        return {"error": "engine not configured"}, 503

    req, err = parse_body(AnalyzeRequest)
    if err is not None:
        return err

    try:
        detections = engine.analyze(
            req.text,
            language=req.language,
            entities=req.entities,
            score_threshold=req.score_threshold,
        )
    except AnalysisError as exc:
        current_app.logger.exception("/analyze: analyzer raised AnalysisError")
        return {"error": f"analysis failed: {exc}"}, 500

    payload = AnalyzeResponse(detections=detections, count=len(detections))
    return payload.model_dump(), 200


@pipeline_bp.post("/anonymize")
@require_bearer_token
def anonymize() -> tuple[dict, int]:
    engine = _get_engine()
    if engine is None:
        current_app.logger.error("/anonymize called but SANCTUM_ENGINE is unconfigured")
        return {"error": "engine not configured"}, 503

    req, err = parse_body(AnonymizeRequest)
    if err is not None:
        return err

    operator_policies: dict[str, OperatorPolicy] | None = None
    if req.operator == "pseudonymize":
        store = _unlocked_store()
        if store is None:
            return _pseudonymize_unavailable()
        operator_policies = _build_pseudonymize_policies(store, req.language)
    elif req.operator is not None:
        operator_policies = {
            "DEFAULT": OperatorPolicy(
                operator_name=req.operator,
                params=req.operator_params or {},
            )
        }

    try:
        result = engine.process(
            req.text,
            language=req.language,
            entities=req.entities,
            score_threshold=req.score_threshold,
            operator_policies=operator_policies,
        )
    except InvalidOperatorParamsError as exc:
        # Bad caller input to mask/encrypt/etc. — a 400, not a 500.
        current_app.logger.info("/anonymize: invalid operator params: %s", exc)
        return {"error": f"invalid operator_params: {exc}"}, 400
    except (AnalysisError, AnonymizationError) as exc:
        current_app.logger.exception("/anonymize: pipeline raised %s", type(exc).__name__)
        return {"error": f"pipeline failed: {exc}"}, 500

    payload = AnonymizeResponse(
        original_text=result.original_text,
        anonymized_text=result.anonymized_text,
        detections=list(result.detections),
        operators_applied=dict(result.operators_applied),
    )
    return payload.model_dump(), 200


@pipeline_bp.post("/process-file")
@require_bearer_token
def process_file() -> tuple[dict, int]:
    engine = _get_engine()
    if engine is None:
        current_app.logger.error("/process-file called but SANCTUM_ENGINE is unconfigured")
        return {"error": "engine not configured"}, 503

    req, err = parse_body(ProcessFileRequest)
    if err is not None:
        return err

    operator_policies: dict[str, OperatorPolicy] | None = None
    if req.operator == "pseudonymize":
        store = _unlocked_store()
        if store is None:
            return _pseudonymize_unavailable()
        operator_policies = _build_pseudonymize_policies(store, req.language)
    elif req.operator is not None:
        operator_policies = {
            "DEFAULT": OperatorPolicy(
                operator_name=req.operator,
                params=req.operator_params or {},
            )
        }

    in_path, in_err = validate_local_path(req.input_path, must_exist=True)
    if in_err is not None:
        return {"error": f"input_path: {in_err}"}, 400
    out_path, out_err = validate_local_path(req.output_path, must_exist=False)
    if out_err is not None:
        return {"error": f"output_path: {out_err}"}, 400
    assert in_path is not None and out_path is not None  # narrowed by err checks

    size = in_path.stat().st_size
    if size > MAX_INPUT_BYTES:
        return {
            "error": f"input file exceeds {MAX_INPUT_BYTES} bytes (got {size})",
        }, 413

    try:
        reader, writer = adapter_for(in_path)
    except UnsupportedDocumentFormatError as exc:
        return {"error": str(exc)}, 415

    try:
        results = engine.process_document(
            reader,
            writer,
            in_path,
            out_path,
            language=req.language,
            entities=req.entities,
            score_threshold=req.score_threshold,
            operator_policies=operator_policies,
            review=req.review,
        )
    except DocumentError as exc:
        current_app.logger.exception("/process-file: DocumentError for %s", in_path)
        return {"error": f"document failure: {exc}"}, 500
    except InvalidOperatorParamsError as exc:
        current_app.logger.info("/process-file: invalid operator params: %s", exc)
        return {"error": f"invalid operator_params: {exc}"}, 400
    except NotImplementedError as exc:
        # review=True against an adapter that hasn't wired emit_review yet.
        # 501 maps to "server understands the request but hasn't
        # implemented it," which is the right signal for a Phase 1.5
        # rollout window where callers can retry once WS2-5 lands.
        current_app.logger.info("/process-file: review not yet supported: %s", exc)
        return {"error": f"review not yet supported: {exc}"}, 501
    except (AnalysisError, AnonymizationError) as exc:
        current_app.logger.exception("/process-file: pipeline raised %s", type(exc).__name__)
        return {"error": f"pipeline failed: {exc}"}, 500

    payload = ProcessFileResponse(
        output_path=str(out_path),
        segments_changed=len(results),
        entities_replaced=sum(len(r.detections) for r in results),
    )
    return payload.model_dump(), 200


@pipeline_bp.post("/commit-review")
@require_bearer_token
def commit_review() -> tuple[dict, int]:
    engine = _get_engine()
    if engine is None:
        current_app.logger.error("/commit-review called but SANCTUM_ENGINE is unconfigured")
        return {"error": "engine not configured"}, 503

    req, err = parse_body(CommitReviewRequest)
    if err is not None:
        return err

    if not req.attested:
        # API cannot prompt; the CLI can. If the caller hasn't explicitly
        # asserted a human has reviewed the file, refuse rather than
        # silently writing accepted pseudonyms to persistent state.
        return {
            "error": (
                "commit-review requires attested=true; the API cannot prompt, "
                "so the caller must assert human review before accepted "
                "pseudonyms are persisted."
            )
        }, 400

    store = _unlocked_store()
    if store is None:
        return _pseudonymize_unavailable()

    in_path, in_err = validate_local_path(req.input_path, must_exist=True)
    if in_err is not None:
        return {"error": f"input_path: {in_err}"}, 400
    out_path, out_err = validate_local_path(req.output_path, must_exist=False)
    if out_err is not None:
        return {"error": f"output_path: {out_err}"}, 400
    assert in_path is not None and out_path is not None

    try:
        reader, writer = adapter_for(in_path)
    except UnsupportedDocumentFormatError as exc:
        return {"error": str(exc)}, 415

    try:
        engine.commit_review(reader, writer, in_path, out_path, store)
    except NotImplementedError as exc:
        # WS1 always takes this path; WS6 turns it into a real success.
        current_app.logger.info("/commit-review: not yet implemented: %s", exc)
        return {"error": f"commit-review not yet implemented: {exc}"}, 501
    except DocumentError as exc:
        current_app.logger.exception("/commit-review: DocumentError for %s", in_path)
        return {"error": f"document failure: {exc}"}, 500

    # WS1 never reaches this — engine raises NotImplementedError. WS6
    # wires real counters. Response shape pinned here so clients can
    # already bind to it.
    payload = CommitReviewResponse(
        output_path=str(out_path),
        accepted=0,
        rejected=0,
        user_added=0,
    )
    return payload.model_dump(), 200
