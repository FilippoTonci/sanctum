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

from waitress import serve as waitress_serve

if TYPE_CHECKING:
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
