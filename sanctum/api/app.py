"""Flask app factory for the Sanctum API.

Responsibilities:
- Populate config (bearer token, loopback Host allowlist, preloaded engine).
- Install the Host/Origin guard as a `before_request` hook so every route
  — including `/health` — is covered without route authors having to
  remember a decorator.
- Disable CORS entirely (the API is local-only; a browser has no legitimate
  reason to call it cross-origin).

Routes land in subsequent substeps (WS4.4+). The factory returns an empty
app with the guard wired up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, current_app, request

from sanctum.api.auth import _is_local_host_header, _is_local_origin
from sanctum.api.routes.health import health_bp

if TYPE_CHECKING:
    from sanctum.core.engine import SanctumEngine


def _build_allowed_hosts(host: str, port: int) -> set[str]:
    """Build the loopback allowlist for the `Host`/`Origin` check.

    Both `host:port` (the exact bind address) and the `localhost:port` alias
    are permitted — browsers resolve `localhost` to 127.0.0.1 and send that
    as the Host header, so both forms are legitimate loopback traffic.
    """
    return {f"{host}:{port}", f"localhost:{port}"}


def create_app(
    *,
    token: str,
    host: str,
    port: int,
    engine: SanctumEngine | None = None,
) -> Flask:
    """Build a configured Flask app, ready to hand to the waitress runner.

    `engine` is optional so tests can construct the app without spinning up
    real Presidio engines. Route handlers that need it read from
    `current_app.config["SANCTUM_ENGINE"]` and 503 if it's unset.
    """
    app = Flask("sanctum.api")
    app.config["SANCTUM_API_TOKEN"] = token
    app.config["SANCTUM_ALLOWED_HOSTS"] = _build_allowed_hosts(host, port)
    app.config["SANCTUM_ENGINE"] = engine

    @app.before_request
    def _enforce_local_host() -> tuple[dict, int] | None:
        allowed: set[str] = current_app.config.get("SANCTUM_ALLOWED_HOSTS", set())
        host_header = request.headers.get("Host", "")
        if not _is_local_host_header(host_header, allowed):
            return {"error": "host not allowed"}, 403
        origin = request.headers.get("Origin")
        if origin is not None and not _is_local_origin(origin, allowed):
            return {"error": "origin not allowed"}, 403
        return None

    app.register_blueprint(health_bp)

    return app
