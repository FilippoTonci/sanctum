"""Auth + localhost hardening for the Sanctum API.

Three layers of defense, wired into the app factory (WS4.3) as
`before_request` guards or as route decorators:

1. **Host allowlist** — only requests whose ``Host`` header names a
   loopback address the server is actually listening on are accepted.
   This is the DNS-rebinding killshot: a browser tricked into
   resolving ``evil.example.com`` to ``127.0.0.1`` still sends
   ``Host: evil.example.com``, which fails the allowlist.
2. **Origin check** — if ``Origin`` is present (i.e., the request
   came from a browser) it must also be loopback. Blocks drive-by
   cross-origin calls from a malicious page a user has open.
3. **Bearer token** — a high-entropy secret stored under 0600 perms
   in ``~/.sanctum/api-token``. Generated once per ``sanctum serve``
   startup if missing.

The token file's perms are the authz boundary: anyone with the user's
OS account can read it. This matches the single-user desktop threat
model — sharing a machine implies sharing Sanctum, same as any other
local tool (Docker, SSH agent, the user's shell history).
"""

from __future__ import annotations

import contextlib
import hmac
import os
import secrets
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, Final, TypeVar
from urllib.parse import urlsplit

from flask import current_app, request

DEFAULT_TOKEN_PATH: Final[Path] = Path.home() / ".sanctum" / "api-token"
_TOKEN_BYTES: Final[int] = 32  # 256 bits of entropy, base64url-encoded.

F = TypeVar("F", bound=Callable[..., Any])


def generate_token() -> str:
    """Mint a fresh high-entropy bearer token (URL-safe base64, 256 bits)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def write_token(token: str, path: Path = DEFAULT_TOKEN_PATH) -> None:
    """Atomically write ``token`` to ``path`` with 0600 permissions.

    Creates the parent directory (``~/.sanctum/``) with 0700 if absent.
    Uses tmp-file + rename so a crash never leaves a half-written token.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # O_EXCL avoids racing another writer; 0o600 is applied at creation time.
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(token + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()


def read_token(path: Path = DEFAULT_TOKEN_PATH) -> str | None:
    """Return the token stored at ``path``, or ``None`` if the file is absent."""
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def ensure_token(path: Path = DEFAULT_TOKEN_PATH) -> str:
    """Return the existing token or generate + persist a fresh one."""
    existing = read_token(path)
    if existing is not None:
        return existing
    token = generate_token()
    write_token(token, path)
    return token


def _is_local_host_header(host_header: str, allowed: set[str]) -> bool:
    """Host header must match one of the loopback aliases the server owns."""
    return host_header in allowed


def _is_local_origin(origin_header: str, allowed: set[str]) -> bool:
    """Extract host:port from an Origin URL and check it against the allowlist."""
    parts = urlsplit(origin_header)
    if parts.scheme not in ("http", "https"):
        return False
    netloc = parts.netloc
    return netloc in allowed


def require_local_host(fn: F) -> F:
    """Reject requests whose Host/Origin isn't a loopback alias the server owns.

    Reads the allowlist from ``current_app.config["SANCTUM_ALLOWED_HOSTS"]``
    — a ``set[str]`` of ``host:port`` strings populated by the app factory.
    """

    @wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        allowed: set[str] = current_app.config.get("SANCTUM_ALLOWED_HOSTS", set())
        host = request.headers.get("Host", "")
        if not _is_local_host_header(host, allowed):
            return {"error": "host not allowed"}, 403
        origin = request.headers.get("Origin")
        if origin is not None and not _is_local_origin(origin, allowed):
            return {"error": "origin not allowed"}, 403
        return fn(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def require_bearer_token(fn: F) -> F:
    """Reject requests lacking a valid ``Authorization: Bearer <token>`` header.

    Reads the expected token from ``current_app.config["SANCTUM_API_TOKEN"]``.
    Uses ``hmac.compare_digest`` for constant-time comparison.
    """

    @wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        expected: str | None = current_app.config.get("SANCTUM_API_TOKEN")
        if not expected:
            return {"error": "server has no token configured"}, 500
        header = request.headers.get("Authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            return {"error": "missing bearer token"}, 401
        if not hmac.compare_digest(presented, expected):
            return {"error": "invalid bearer token"}, 401
        return fn(*args, **kwargs)

    return wrapped  # type: ignore[return-value]
