"""Waitress runner for the Sanctum API.

Enforces the airgap invariant at the boundary: ``run()`` refuses any bind
host that isn't a loopback address, raising before the socket opens.
Presidio engines are preloaded (constructed before ``waitress.serve``
starts accepting connections) so the first real request doesn't pay the
multi-second NLP warm-up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from waitress import serve as waitress_serve

if TYPE_CHECKING:
    from flask import Flask

LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def assert_loopback(host: str) -> None:
    """Raise ``ValueError`` unless ``host`` is a loopback alias.

    Part of the airgap invariant — the API must never be reachable from
    anything but the local machine. Enforced before the listener opens
    so a misconfigured deployment fails loudly at startup, not silently
    at traffic time.
    """
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            f"Sanctum API refuses to bind to non-loopback host {host!r}; "
            f"allowed: {sorted(LOOPBACK_HOSTS)}"
        )


def run(
    app: Flask,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    threads: int = 4,
) -> None:
    """Start waitress on ``host:port``. Blocks until the server is stopped."""
    assert_loopback(host)
    waitress_serve(app, host=host, port=port, threads=threads)
