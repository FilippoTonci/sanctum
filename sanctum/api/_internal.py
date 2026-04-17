"""Shared helpers used by more than one sanctum.api module.

Before this module existed, ``routes/mapping.py`` reached across into
``routes/pipeline.py`` for leading-underscore helpers, and ``app.py``
imported leading-underscore helpers out of ``auth.py``. Both were leaky:
the underscore lied about scope. Promoting the genuinely-shared helpers
here keeps blueprint code honest about what is and isn't private to it.

The helpers that stay behind are the single-callsite ones (e.g. the
token-IO functions in ``auth.py``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import current_app, request
from pydantic import ValidationError


def is_local_host_header(host_header: str, allowed: set[str]) -> bool:
    """Host header must match one of the loopback aliases the server owns."""
    return host_header in allowed


def is_local_origin(origin_header: str, allowed: set[str]) -> bool:
    """Extract host:port from an Origin URL and check it against the allowlist."""
    parts = urlsplit(origin_header)
    if parts.scheme not in ("http", "https"):
        return False
    return parts.netloc in allowed


def check_local_request() -> tuple[dict, int] | None:
    """Enforce the Host/Origin allowlist for the current Flask request.

    Returns a ``(body, status)`` tuple to short-circuit the response, or
    ``None`` if the request passes. Used both by the app factory's global
    ``before_request`` guard and by the ``require_local_host`` decorator —
    having one implementation means the two callsites cannot drift.
    """
    allowed: set[str] = current_app.config.get("SANCTUM_ALLOWED_HOSTS", set())
    host_header = request.headers.get("Host", "")
    if not is_local_host_header(host_header, allowed):
        current_app.logger.warning(
            "host header %r not in allowlist %s — rejecting", host_header, sorted(allowed)
        )
        return {"error": "host not allowed"}, 403
    origin = request.headers.get("Origin")
    if origin is not None and not is_local_origin(origin, allowed):
        current_app.logger.warning("origin %r not in allowlist — rejecting", origin)
        return {"error": "origin not allowed"}, 403
    return None


def parse_body(model_cls: type[Any]) -> tuple[Any, tuple[dict, int] | None]:
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


def validate_local_path(value: str, *, must_exist: bool) -> tuple[Path | None, str | None]:
    """Reject UNC, relative, non-existent, and symlinked paths.

    Returns ``(path, None)`` on success or ``(None, error_msg)`` on failure.
    The airgap invariant demands three checks here:

    1. UNC (``\\\\server\\share`` / ``//server/share``) names a network
       share. Honoring one would read or write across the network.
    2. A non-absolute path gives the client too much leeway to coerce the
       server into using its own CWD.
    3. A symlink anywhere in the path can redirect to a network mount
       (``~/data -> //fileserver/data``) and smuggle the airgap bypass
       past the UNC check. Compare ``realpath`` (follows symlinks) against
       ``abspath`` (doesn't) — any divergence means at least one component
       is a symlink, and we refuse the path rather than try to prove the
       resolved target is still local.
    """
    if not value:
        return None, "path is empty"
    # UNC must be caught before Path() normalizes some forms away.
    if value.startswith("\\\\") or value.startswith("//"):
        return None, "UNC paths are not allowed"
    path = Path(value)
    if not path.is_absolute():
        return None, "path must be absolute"
    real = os.path.realpath(str(path))
    abs_ = os.path.abspath(str(path))
    if real != abs_:
        return None, "symlinked paths are not allowed"
    if must_exist and not path.is_file():
        return None, "path does not exist or is not a regular file"
    return path, None


__all__ = [
    "check_local_request",
    "is_local_host_header",
    "is_local_origin",
    "parse_body",
    "validate_local_path",
]
