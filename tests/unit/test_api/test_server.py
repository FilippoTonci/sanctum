"""Unit tests for `sanctum.api.server` — loopback enforcement at bind time."""

from __future__ import annotations

import pytest
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
