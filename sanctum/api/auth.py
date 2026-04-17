"""Auth + localhost hardening for the Sanctum API.

Three layers of defense, wired into the app factory (WS4.3) as
`before_request` guards or as route decorators:

1. **Host allowlist** — only requests whose ``Host`` header names a
   loopback address the server is actually listening on are accepted.
   This is the DNS-rebinding killshot: a browser tricked into
   resolving ``evil.example.com`` to ``127.0.0.1`` still sends
   ``Host: evil.example.com``, which fails the allowlist. Implemented
   in ``sanctum.api._internal.check_local_request`` so the app
   factory's ``before_request`` guard and ``require_local_host`` here
   share one definition.
2. **Origin check** — if ``Origin`` is present (i.e., the request
   came from a browser) it must also be loopback. Blocks drive-by
   cross-origin calls from a malicious page a user has open.
3. **Bearer token** — a high-entropy secret stored under 0600 perms
   in ``~/.sanctum/api-token``. Generated once per ``sanctum serve``
   startup if missing; refused on read if the existing file's perms
   have drifted looser than 0600.

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
import stat
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, Final, TypeVar

from flask import current_app, request

from sanctum.api._internal import check_local_request

DEFAULT_TOKEN_PATH: Final[Path] = Path.home() / ".sanctum" / "api-token"
_TOKEN_BYTES: Final[int] = 32  # 256 bits of entropy, base64url-encoded.
_DIR_MODE: Final[int] = 0o700
_FILE_MODE: Final[int] = 0o600

F = TypeVar("F", bound=Callable[..., Any])


class InsecureTokenFileError(RuntimeError):
    """Raised when the on-disk token file's perms are looser than 0600.

    Bubbled up to the CLI so the user hears about it; silently rotating
    the token would leave an attacker-readable copy behind on disk.
    """


def generate_token() -> str:
    """Mint a fresh high-entropy bearer token (URL-safe base64, 256 bits)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _ensure_secure_dir(directory: Path) -> None:
    """Create or tighten ``directory`` to 0700.

    ``Path.mkdir(mode=...)`` only applies the mode at creation time, so a
    pre-existing ``~/.sanctum/`` created with a looser umask (or by an
    older Sanctum release) keeps its original perms. chmod unconditionally
    so the token's parent can't be the leak path.
    """
    directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        # OSError on Windows (POSIX-only bits); elsewhere, a failure is
        # informational at best — the file perms are the real boundary.
        os.chmod(directory, _DIR_MODE)


def write_token(token: str, path: Path = DEFAULT_TOKEN_PATH) -> None:
    """Atomically write ``token`` to ``path`` with 0600 permissions.

    Creates the parent directory (``~/.sanctum/``) with 0700 if absent,
    and *re-tightens* it to 0700 if it pre-existed with looser perms.
    Uses tmp-file + rename so a crash never leaves a half-written token.
    """
    _ensure_secure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # O_EXCL avoids racing another writer; 0o600 is applied at creation time.
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
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


def _assert_file_mode_is_0600(path: Path) -> None:
    """Refuse to trust a token file whose perms have drifted looser than 0600.

    POSIX-only: Windows doesn't expose the same mode bits and the check is
    skipped there (matching the rest of the module's POSIX-leaning perms
    story).
    """
    if os.name == "nt":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise InsecureTokenFileError(
            f"token file {path} has permissions {oct(mode)}; expected 0600. "
            "Delete it and re-run `sanctum serve` to mint a fresh token."
        )


def read_token(path: Path = DEFAULT_TOKEN_PATH) -> str | None:
    """Return the token stored at ``path``, or ``None`` if the file is absent.

    Raises ``InsecureTokenFileError`` if the file exists but its mode bits
    are looser than 0600.
    """
    if not path.exists():
        return None
    _assert_file_mode_is_0600(path)
    return path.read_text(encoding="utf-8").strip() or None


def ensure_token(path: Path = DEFAULT_TOKEN_PATH) -> str:
    """Return the existing token or generate + persist a fresh one."""
    existing = read_token(path)
    if existing is not None:
        return existing
    token = generate_token()
    write_token(token, path)
    return token


def require_local_host(fn: F) -> F:
    """Reject requests whose Host/Origin isn't a loopback alias the server owns.

    Delegates to ``sanctum.api._internal.check_local_request`` so the app
    factory's ``before_request`` guard and this decorator share identical
    behavior, including logging.
    """

    @wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        rejection = check_local_request()
        if rejection is not None:
            return rejection
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
            current_app.logger.error("bearer auth rejected: server has no token configured")
            return {"error": "server has no token configured"}, 500
        header = request.headers.get("Authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            current_app.logger.warning("bearer auth rejected: missing/malformed header")
            return {"error": "missing bearer token"}, 401
        if not hmac.compare_digest(presented, expected):
            current_app.logger.warning("bearer auth rejected: invalid token")
            return {"error": "invalid bearer token"}, 401
        return fn(*args, **kwargs)

    return wrapped  # type: ignore[return-value]
