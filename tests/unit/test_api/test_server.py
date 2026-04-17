"""Unit tests for `sanctum.api.server` — loopback enforcement at bind time."""

from __future__ import annotations

import socket
from typing import Any

import pytest
from sanctum.api import server as server_mod
from sanctum.api.server import LOOPBACK_HOSTS, assert_loopback


def test_loopback_aliases_accepted():
    for host in ("127.0.0.1", "::1", "localhost"):
        assert_loopback(host)


def test_public_ip_rejected():
    with pytest.raises(ValueError, match="non-loopback"):
        assert_loopback("0.0.0.0")
    with pytest.raises(ValueError, match="non-loopback"):
        assert_loopback("10.0.0.5")


def test_empty_host_rejected():
    with pytest.raises(ValueError):
        assert_loopback("")


def test_loopback_set_is_frozen():
    # Guard against accidental mutation in another module.
    assert isinstance(LOOPBACK_HOSTS, frozenset)


def test_localhost_refused_if_resolver_returns_non_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostile /etc/hosts that maps `localhost -> 8.8.8.8` must not
    sneak past `assert_loopback` — the resolver check is the second line
    of defense behind the string allowlist."""
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))]

    def _fake_getaddrinfo(host: str, *_args: Any, **_kwargs: Any) -> Any:
        if host == "localhost":
            return fake
        return socket.getaddrinfo(host, *_args, **_kwargs)

    monkeypatch.setattr(server_mod.socket, "getaddrinfo", _fake_getaddrinfo)

    with pytest.raises(ValueError, match="not loopback"):
        assert_loopback("localhost")


def test_localhost_refused_if_resolution_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the resolver itself errors, treat `localhost` as untrustworthy."""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise socket.gaierror("resolver is offline")

    monkeypatch.setattr(server_mod.socket, "getaddrinfo", _boom)

    with pytest.raises(ValueError, match="not loopback"):
        assert_loopback("localhost")
