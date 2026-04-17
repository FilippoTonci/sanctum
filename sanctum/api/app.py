"""Flask app factory for the Sanctum API.

Responsibilities:
- Populate config (bearer token, loopback Host allowlist, preloaded engine).
- Install the Host/Origin guard as a `before_request` hook so every route
  — including `/health` — is covered without route authors having to
  remember a decorator.
- Disable CORS entirely (the API is local-only; a browser has no legitimate
  reason to call it cross-origin).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from flask import Flask

from sanctum.api import MAX_INPUT_BYTES
from sanctum.api._internal import check_local_request
from sanctum.api.routes.health import health_bp
from sanctum.api.routes.mapping import mapping_bp
from sanctum.api.routes.pipeline import pipeline_bp
from sanctum.security import EncryptedFileMappingStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sanctum.core.engine import SanctumEngine


def _default_mapping_store_factory(path: Path) -> EncryptedFileMappingStore:
    """Production factory — uses keyring's RFC-9106 default KDF params."""
    return EncryptedFileMappingStore(path)


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
    mapping_store_factory: Callable[[Path], EncryptedFileMappingStore] | None = None,
) -> Flask:
    """Build a configured Flask app, ready to hand to the waitress runner.

    `engine` is optional so tests can construct the app without spinning up
    real Presidio engines. Route handlers that need it read from
    `current_app.config["SANCTUM_ENGINE"]` and 503 if it's unset.

    `mapping_store_factory` lets tests substitute an `EncryptedFileMappingStore`
    built with cheap KDF params — production callers should leave it as the
    default so RFC-9106 settings stick.
    """
    app = Flask("sanctum.api")
    app.config["SANCTUM_API_TOKEN"] = token
    app.config["SANCTUM_ALLOWED_HOSTS"] = _build_allowed_hosts(host, port)
    app.config["SANCTUM_ENGINE"] = engine
    app.config["SANCTUM_MAPPING_STORE"] = None
    app.config["SANCTUM_MAPPING_STORE_FACTORY"] = (
        mapping_store_factory or _default_mapping_store_factory
    )
    # Serializes /mapping/{unlock,lock,rotate-key} so two waitress worker
    # threads can't race the unlocked-store config slot. Reads through
    # /mapping/reverse rely on the store's own internal RLock instead.
    app.config["SANCTUM_MAPPING_LOCK"] = threading.Lock()
    # Defense in depth — the file-processing routes pass paths, not bodies,
    # but a JSON body big enough to OOM the server is still cheap to refuse
    # at the WSGI layer. Aligns with the per-file MAX_INPUT_BYTES check.
    app.config["MAX_CONTENT_LENGTH"] = MAX_INPUT_BYTES

    @app.before_request
    def _enforce_local_host() -> tuple[dict, int] | None:
        return check_local_request()

    app.register_blueprint(health_bp)
    app.register_blueprint(pipeline_bp)
    app.register_blueprint(mapping_bp)

    return app
