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

import os
from typing import Final

DEV_SENTINEL: Final[str] = "dev"
"""Returned when `SANCTUM_COMMIT` is unset. Desktop treats this as "skip
the pin check" during local development — never ship a release build
with this value."""


def commit() -> str:
    """Return the bundled commit SHA or the dev sentinel."""
    raw = os.environ.get("SANCTUM_COMMIT", "").strip()
    return raw or DEV_SENTINEL
