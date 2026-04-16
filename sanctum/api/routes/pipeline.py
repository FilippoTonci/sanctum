"""`/analyze` + `/anonymize` routes — engine-facing pipeline endpoints.

Both routes are authenticated (bearer token) and run the same engine the
CLI uses; they are effectively a JSON façade over `SanctumEngine.analyze`
and `SanctumEngine.process`. The mapping-store-backed `pseudonymize`
operator is rejected here until WS4.7 lands the unlock/lock routes —
until then, the store has no way to be made available server-side.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from flask import Blueprint, current_app, request
from pydantic import ValidationError

from sanctum.api.auth import require_bearer_token
from sanctum.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnonymizeRequest,
    AnonymizeResponse,
    ProcessFileRequest,
    ProcessFileResponse,
)
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import (
    AnalysisError,
    AnonymizationError,
    DocumentError,
    UnsupportedDocumentFormatError,
)
from sanctum.core.models import OperatorPolicy
from sanctum.core.protocols import MappingStore
from sanctum.documents import adapter_for

pipeline_bp = Blueprint("pipeline", __name__)

MAX_INPUT_BYTES: Final[int] = 50 * 1024 * 1024  # 50 MiB hard cap on document inputs.


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


_PSEUDONYMIZE_LOCKED_ERROR: dict[str, str] = {
    "error": (
        "pseudonymize requires the encrypted mapping store; unlock it via /mapping/unlock first"
    ),
}


def _parse_body(model_cls: type[Any]) -> tuple[Any, tuple[dict, int] | None]:
    """Decode the JSON body into ``model_cls`` or return a 400 tuple.

    ``force=False`` so a missing/wrong Content-Type still refuses politely
    instead of trying to parse HTML forms.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, ({"error": "request body must be a JSON object"}, 400)
    try:
        return model_cls.model_validate(body), None
    except ValidationError as exc:
        # Trim pydantic's error objects to the bits a human (or GUI form)
        # actually needs — no internal URLs, no raw input echoes.
        details = [{"loc": err["loc"], "msg": err["msg"]} for err in exc.errors()]
        return None, ({"error": "invalid request", "details": details}, 400)


@pipeline_bp.post("/analyze")
@require_bearer_token
def analyze() -> tuple[dict, int]:
    engine = _get_engine()
    if engine is None:
        return {"error": "engine not configured"}, 503

    req, err = _parse_body(AnalyzeRequest)
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
        return {"error": f"analysis failed: {exc}"}, 500

    payload = AnalyzeResponse(detections=detections, count=len(detections))
    return payload.model_dump(), 200


@pipeline_bp.post("/anonymize")
@require_bearer_token
def anonymize() -> tuple[dict, int]:
    engine = _get_engine()
    if engine is None:
        return {"error": "engine not configured"}, 503

    req, err = _parse_body(AnonymizeRequest)
    if err is not None:
        return err

    operator_policies: dict[str, OperatorPolicy] | None = None
    if req.operator == "pseudonymize":
        store = _unlocked_store()
        if store is None:
            return _PSEUDONYMIZE_LOCKED_ERROR, 400
        operator_policies = _build_pseudonymize_policies(store, req.language)
    elif req.operator is not None:
        operator_policies = {"DEFAULT": OperatorPolicy(operator_name=req.operator)}

    try:
        result = engine.process(
            req.text,
            language=req.language,
            entities=req.entities,
            score_threshold=req.score_threshold,
            operator_policies=operator_policies,
        )
    except (AnalysisError, AnonymizationError) as exc:
        return {"error": f"pipeline failed: {exc}"}, 500

    payload = AnonymizeResponse(
        original_text=result.original_text,
        anonymized_text=result.anonymized_text,
        detections=list(result.detections),
        operators_applied=dict(result.operators_applied),
    )
    return payload.model_dump(), 200


def _validate_local_path(value: str, *, must_exist: bool) -> tuple[Path | None, str | None]:
    """Reject UNC paths and require absolute paths.

    Returns ``(path, None)`` on success or ``(None, error_msg)`` on failure.
    UNC paths — ``\\\\server\\share`` on Windows or ``//server/share`` as the
    POSIX analog — point at network shares; honoring one would defeat the
    airgap by reading or writing across the network. Refuse them outright.
    Used by /process-file and the /mapping routes — both take server-side
    paths from the client, both must defend the airgap boundary identically.
    """
    if not value:
        return None, "path is empty"
    # Catch UNC before constructing Path: pathlib normalizes some forms away.
    if value.startswith("\\\\") or value.startswith("//"):
        return None, "UNC paths are not allowed"
    path = Path(value)
    if not path.is_absolute():
        return None, "path must be absolute"
    if must_exist and not path.is_file():
        return None, "path does not exist or is not a regular file"
    return path, None


@pipeline_bp.post("/process-file")
@require_bearer_token
def process_file() -> tuple[dict, int]:
    engine = _get_engine()
    if engine is None:
        return {"error": "engine not configured"}, 503

    req, err = _parse_body(ProcessFileRequest)
    if err is not None:
        return err

    operator_policies: dict[str, OperatorPolicy] | None = None
    if req.operator == "pseudonymize":
        store = _unlocked_store()
        if store is None:
            return _PSEUDONYMIZE_LOCKED_ERROR, 400
        operator_policies = _build_pseudonymize_policies(store, req.language)
    elif req.operator is not None:
        operator_policies = {"DEFAULT": OperatorPolicy(operator_name=req.operator)}

    in_path, in_err = _validate_local_path(req.input_path, must_exist=True)
    if in_err is not None:
        return {"error": f"input_path: {in_err}"}, 400
    out_path, out_err = _validate_local_path(req.output_path, must_exist=False)
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
        )
    except DocumentError as exc:
        return {"error": f"document failure: {exc}"}, 500
    except (AnalysisError, AnonymizationError) as exc:
        return {"error": f"pipeline failed: {exc}"}, 500

    payload = ProcessFileResponse(
        output_path=str(out_path),
        segments_changed=len(results),
        entities_replaced=sum(len(r.detections) for r in results),
    )
    return payload.model_dump(), 200
