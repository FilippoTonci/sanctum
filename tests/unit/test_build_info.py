"""Unit tests for `sanctum._build_info`."""

from __future__ import annotations

import pytest
from sanctum._build_info import DEV_SENTINEL, commit


def test_commit_returns_dev_sentinel_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANCTUM_COMMIT", raising=False)
    assert commit() == DEV_SENTINEL


def test_commit_returns_env_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANCTUM_COMMIT", "1234abc")
    assert commit() == "1234abc"


def test_commit_trims_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build scripts that `echo $(git rev-parse --short HEAD)` can append a
    trailing newline; don't surface that verbatim to the desktop's compare."""
    monkeypatch.setenv("SANCTUM_COMMIT", "  1234abc\n")
    assert commit() == "1234abc"


def test_commit_returns_dev_sentinel_when_env_is_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty-but-set SANCTUM_COMMIT (common when a CI var is unpopulated)
    must behave like "unset" rather than leaking "" as a valid pin."""
    monkeypatch.setenv("SANCTUM_COMMIT", "   ")
    assert commit() == DEV_SENTINEL
