"""`/analyze` + `/anonymize` routes — engine-facing pipeline endpoints.

Both routes are authenticated (bearer token) and run the same engine the
CLI uses; they are effectively a JSON façade over `SanctumEngine.analyze`
and `SanctumEngine.process`. The mapping-store-backed `pseudonymize`
operator is rejected here until WS4.7 lands the unlock/lock routes —
until then, the store has no way to be made available server-side.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, request
from pydantic import ValidationError

from sanctum.api.auth import require_bearer_token
from sanctum.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnonymizeRequest,
    AnonymizeResponse,
)
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import AnalysisError, AnonymizationError
from sanctum.core.models import OperatorPolicy

pipeline_bp = Blueprint("pipeline", __name__)


def _get_engine() -> SanctumEngine | None:
    engine = current_app.config.get("SANCTUM_ENGINE")
    return engine if isinstance(engine, SanctumEngine) else None


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

    if req.operator == "pseudonymize":
        return {
            "error": (
                "pseudonymize requires the encrypted mapping store; "
                "unlock it via /mapping/unlock (available in WS4.7) first"
            )
        }, 400

    operator_policies: dict[str, OperatorPolicy] | None = None
    if req.operator is not None:
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
