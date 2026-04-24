"""Waitress runner for the Sanctum API.

Enforces the airgap invariant at the boundary: ``run()`` refuses any bind
host that isn't a loopback address, raising before the socket opens.
Presidio engines are preloaded (constructed before ``waitress.serve``
starts accepting connections) so the first real request doesn't pay the
multi-second NLP warm-up.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import TYPE_CHECKING

from waitress.server import create_server

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask

# Names we accept at all. The literal IPs are trivially loopback; the
# name ``localhost`` is re-resolved at check time and verified to point
# at loopback in DNS/hosts — it is **not** trusted by string.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def _resolve_to_loopback(host: str) -> bool:
    """Return True iff every address ``host`` resolves to is loopback.

    ``localhost`` is a name, not a guarantee. A compromised or misconfigured
    ``/etc/hosts`` can map it to a routable address; a DNS entry that has
    the same name (``localhost.corp.example``) will resolve wherever the
    resolver says. We check every address the resolver returns and demand
    they are all loopback — one non-loopback entry is enough to refuse.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for family, _, _, _, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if not ip.is_loopback:
            return False
        if family not in (socket.AF_INET, socket.AF_INET6):
            return False
    return True


def assert_loopback(host: str) -> None:
    """Raise ``ValueError`` unless ``host`` is a loopback alias.

    Part of the airgap invariant — the API must never be reachable from
    anything but the local machine. Enforced before the listener opens
    so a misconfigured deployment fails loudly at startup, not silently
    at traffic time. For the ``localhost`` alias we additionally resolve
    via ``getaddrinfo`` and verify every returned address is loopback;
    trusting the literal name would let a rogue ``/etc/hosts`` entry
    point the listener at a routable address.
    """
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            f"Sanctum API refuses to bind to non-loopback host {host!r}; "
            f"allowed: {sorted(LOOPBACK_HOSTS)}"
        )
    if not _resolve_to_loopback(host):
        raise ValueError(
            f"Sanctum API refuses to bind to {host!r}: its resolved address is "
            "not loopback. Check /etc/hosts and your resolver config."
        )


def pick_free_port(host: str = "127.0.0.1") -> int:
    """Bind a throwaway socket to ``host:0`` and return the allocated port.

    Used by `sanctum serve --port 0` to resolve a concrete port *before*
    the Flask app is built — the app's Host/Origin allowlist is keyed on
    host+port, so it has to be created with the real number. There is a
    theoretical race where another process binds the same port between
    this close and waitress's bind; acceptable for a single-user desktop.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def run(
    app: Flask,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    threads: int = 4,
    on_ready: Callable[[str, int], None] | None = None,
) -> None:
    """Start waitress on ``host:port``. Blocks until the server is stopped.

    Uses waitress's lower-level ``create_server`` so the socket binds
    *before* we return from setup; ``on_ready`` fires after bind but
    before the accept loop, which is the right moment to emit the
    machine-readable ``SANCTUM_READY`` line the Electron sidecar
    lifecycle polls against. The callback runs synchronously, so a
    misbehaving callback can stall startup — keep it small.
    """
    assert_loopback(host)
    server = create_server(app, host=host, port=port, threads=threads)
    if on_ready is not None:
        on_ready(host, port)
    server.run()
