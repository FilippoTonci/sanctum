"""Build-time provenance for the bundled Sanctum Python backend.

The Phase 3 desktop app and the Python sidecar ship in one atomic
installer; the sidecar is built from a specific `sanctum` commit
pinned by the desktop release workflow. The desktop reads
``/health.sanctum_commit`` at startup and fails fast if it differs
from the SHA the installer was built against — that's the sentinel
for a corrupt install or a manually-swapped sidecar.

Source of truth: the ``SANCTUM_COMMIT`` environment variable, set by
the build script (PyInstaller step, CI workflow, or developer shell
via `export SANCTUM_COMMIT=$(git rev-parse --short HEAD)`). When the
variable is unset we return ``"dev"`` — a well-known sentinel the
desktop recognizes and tolerates during local development.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Final

DEV_SENTINEL: Final[str] = "dev"
"""Returned when `SANCTUM_COMMIT` is unset. Desktop treats this as "skip
the pin check" during local development — never ship a release build
with this value."""

UNKNOWN_DIGEST: Final[str] = "unknown"
"""Returned when the OpenAPI spec file cannot be located (e.g. an
unusual install layout). Desktop treats this the same as `DEV_SENTINEL`
for the pin check — warn, don't abort."""

# The generator script writes here; PyInstaller bundles it as a package
# data file alongside `sanctum/`. We resolve relative to the repo root
# when running from source, and relative to the bundled package dir when
# running inside a PyInstaller onedir build.
_SPEC_CANDIDATES: Final[tuple[Path, ...]] = (
    # Source checkout: repo_root/schema/openapi.json
    Path(__file__).resolve().parent.parent / "schema" / "openapi.json",
    # Bundled: <bundle>/schema/openapi.json (PyInstaller `datas` entry)
    Path(__file__).resolve().parent / "schema" / "openapi.json",
)


def commit() -> str:
    """Return the bundled commit SHA or the dev sentinel."""
    raw = os.environ.get("SANCTUM_COMMIT", "").strip()
    return raw or DEV_SENTINEL


def openapi_digest() -> str:
    """Return a short SHA-256 hex digest of the committed OpenAPI spec.

    The Phase 3 desktop app compares this to the digest of the spec it
    was built from. A mismatch means the bundled sidecar does not match
    the TypeScript client — corrupt install or a manually-swapped
    sidecar. Returns `UNKNOWN_DIGEST` if no spec file is reachable,
    which the desktop treats as "can't verify, warn the user".
    """
    for candidate in _SPEC_CANDIDATES:
        if candidate.is_file():
            h = hashlib.sha256(candidate.read_bytes()).hexdigest()
            # 12 hex chars is plenty of collision resistance for this
            # purpose and keeps the `/health` response compact.
            return h[:12]
    return UNKNOWN_DIGEST
