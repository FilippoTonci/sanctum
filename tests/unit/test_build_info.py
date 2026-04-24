"""Unit tests for `sanctum._build_info`."""

from __future__ import annotations

from pathlib import Path

import pytest
from sanctum._build_info import (
    _SPEC_CANDIDATES,
    DEV_SENTINEL,
    UNKNOWN_DIGEST,
    commit,
    openapi_digest,
)


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


def test_openapi_digest_returns_12_hex_chars_when_spec_present() -> None:
    """In a source checkout, schema/openapi.json is committed and digest
    returns 12 lowercase hex chars."""
    d = openapi_digest()
    assert d != UNKNOWN_DIGEST, "schema/openapi.json missing — run scripts/generate_openapi.py"
    assert len(d) == 12
    assert all(c in "0123456789abcdef" for c in d)


def test_openapi_digest_returns_unknown_sentinel_when_spec_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Point the candidate list at nonexistent paths — digest must return
    the sentinel rather than raise."""
    fake = (tmp_path / "missing1.json", tmp_path / "missing2.json")
    monkeypatch.setattr("sanctum._build_info._SPEC_CANDIDATES", fake)
    assert openapi_digest() == UNKNOWN_DIGEST


def test_openapi_digest_is_deterministic_across_calls() -> None:
    """Same file content → same digest; no time- or nonce-dependent output."""
    assert openapi_digest() == openapi_digest()


def test_spec_candidates_are_absolute_paths() -> None:
    """Catch regressions where a relative path leaks in — digest must not
    depend on CWD."""
    for p in _SPEC_CANDIDATES:
        assert p.is_absolute(), f"{p!r} must be absolute"
