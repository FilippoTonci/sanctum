"""Local filesystem persistence for ``ReviewSession``.

Sessions live under ``~/.sanctum/sessions/<session_id>/`` with 0700 dir
/ 0600 file permissions. The manifest is a JSON dump of the
``ReviewSession`` model; the input bytes — copied verbatim from the
user's source file — are stored alongside so the commit pass can apply
decisions without re-reading a path that may have moved or been deleted.

The session dir carries sensitive plaintext (proposals include the
original PII text) until the session commits or is abandoned. Treat it
like the unlocked mapping store: tighten perms, clean up on terminal
transitions.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sanctum.core.exceptions import ReviewSessionNotFoundError
from sanctum.core.models import ReviewSession


def _default_root() -> Path:
    return Path.home() / ".sanctum" / "sessions"


class SessionStore:
    """Persist and retrieve ``ReviewSession`` state on the local disk."""

    MANIFEST = "manifest.json"

    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else _default_root()

    @property
    def root(self) -> Path:
        return self._root

    def _session_dir(self, session_id: str) -> Path:
        return self._root / session_id

    def _manifest_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / self.MANIFEST

    def exists(self, session_id: str) -> bool:
        return self._manifest_path(session_id).exists()

    def save(
        self,
        session: ReviewSession,
        input_bytes: bytes | None = None,
    ) -> None:
        """Write the session manifest to disk (idempotent).

        Every save overwrites the manifest with the session's current
        state — cheap, and lets the API layer treat each decision PATCH
        as a straightforward save. ``input_bytes`` is only written the
        first time: rewriting on every PATCH would be wasteful and the
        bytes don't change over the session's life.
        """
        session_dir = self._session_dir(session.id)
        session_dir.mkdir(parents=True, exist_ok=True)
        session_dir.chmod(0o700)

        manifest = self._manifest_path(session.id)
        manifest.write_text(session.model_dump_json())
        manifest.chmod(0o600)

        if input_bytes is not None:
            input_file = session_dir / f"input.{session.format}"
            if not input_file.exists():
                input_file.write_bytes(input_bytes)
                input_file.chmod(0o600)

    def load(self, session_id: str) -> ReviewSession:
        manifest = self._manifest_path(session_id)
        if not manifest.exists():
            raise ReviewSessionNotFoundError(
                f"Session {session_id!r} not found under {self._root}."
            )
        return ReviewSession.model_validate_json(manifest.read_text())

    def load_input_bytes(self, session_id: str) -> bytes:
        """Read the stored input bytes for the session.

        Raises ``ReviewSessionNotFoundError`` if the session dir doesn't
        exist or the session was saved without input bytes.
        """
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            raise ReviewSessionNotFoundError(
                f"Session {session_id!r} not found under {self._root}."
            )
        matches = list(session_dir.glob("input.*"))
        if not matches:
            raise ReviewSessionNotFoundError(f"Session {session_id!r} has no stored input bytes.")
        return matches[0].read_bytes()

    def delete(self, session_id: str) -> None:
        """Remove the session directory. Idempotent.

        Used for both commit cleanup and explicit abandon. A missing
        directory is not an error — simplifies the API layer's error
        handling for the abandon/commit endpoints.
        """
        session_dir = self._session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir)

    def list_ids(self) -> list[str]:
        """Enumerate existing session ids. Empty list if root is missing."""
        if not self._root.exists():
            return []
        return sorted(p.name for p in self._root.iterdir() if p.is_dir())
