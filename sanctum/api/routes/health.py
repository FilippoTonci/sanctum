"""`/health` route — liveness probe for the Sanctum API.

Intentionally unauthenticated so the GUI (or a packaging smoke test) can
confirm the server is alive without first needing the bearer token. The
host/origin guard in the app factory still applies, so this endpoint is
only reachable from loopback callers.
"""

from __future__ import annotations

from flask import Blueprint, current_app

from sanctum import __version__
from sanctum.api.schemas import HealthResponse

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health() -> tuple[dict, int]:
    """Return liveness + the mapping store's locked/unlocked state."""
    payload = HealthResponse(
        status="ok",
        version=__version__,
        mapping_store_unlocked=current_app.config.get("SANCTUM_MAPPING_STORE") is not None,
    )
    return payload.model_dump(), 200
