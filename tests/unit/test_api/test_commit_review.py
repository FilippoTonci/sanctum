"""Unit tests for `POST /commit-review`.

WS1 ships only the endpoint surface — the engine raises
NotImplementedError. These tests nail down the auth / attestation /
path / store-lock gates that remain load-bearing once WS6 wires the
reconciliation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from sanctum.api.app import create_app
from sanctum.core.engine import SanctumEngine

LOOPBACK = {"Host": "127.0.0.1:8765"}
AUTH = {"Authorization": "Bearer t"}


class _FakeAnalyzer:
    def analyze(self, text: str, **_: Any) -> list:
        return []


class _FakeAnonymizer:
    def anonymize(self, text: str, **_: Any) -> Any:
        raise AssertionError("commit-review should not call anonymize")


def _engine() -> SanctumEngine:
    return SanctumEngine(
        analyzer=_FakeAnalyzer(),  # type: ignore[arg-type]
        anonymizer=_FakeAnonymizer(),  # type: ignore[arg-type]
    )


class _UnlockedStore:
    """Minimal stand-in for the unlocked mapping store the route checks."""

    is_unlocked = True

    def get_or_create(self, *_a: object, **_k: object) -> str:  # pragma: no cover
        return ""

    def reverse(self, *_a: object, **_k: object) -> str | None:  # pragma: no cover
        return None


def _client(engine: SanctumEngine | None, store: object | None = None):
    app = create_app(token="t", host="127.0.0.1", port=8765, engine=engine)
    if store is not None:
        app.config["SANCTUM_MAPPING_STORE"] = store
    return app.test_client()


class _StubReaderWriter:
    def read(self, _p: Path) -> object:
        return object()

    def read_review_decisions(self, _p: Path) -> list:
        return []

    def write(self, _d: object, _p: Path) -> None: ...

    def emit_review(self, _d: object, _p: Path, _r: dict) -> None: ...


def test_commit_review_requires_bearer(tmp_path: Path):
    src = tmp_path / "review.docx"
    src.write_bytes(b"x")
    client = _client(_engine(), _UnlockedStore())
    r = client.post(
        "/commit-review",
        headers=LOOPBACK,
        json={
            "input_path": str(src),
            "output_path": str(tmp_path / "final.docx"),
            "attested": True,
        },
    )
    assert r.status_code == 401


def test_commit_review_400_without_attested(tmp_path: Path):
    """API can't prompt — no attestation, no commit."""
    src = tmp_path / "review.docx"
    src.write_bytes(b"x")
    client = _client(_engine(), _UnlockedStore())
    r = client.post(
        "/commit-review",
        headers={**LOOPBACK, **AUTH},
        json={
            "input_path": str(src),
            "output_path": str(tmp_path / "final.docx"),
        },
    )
    assert r.status_code == 400
    assert "attested" in r.get_json()["error"]


def test_commit_review_400_when_no_store_unlocked(tmp_path: Path):
    src = tmp_path / "review.docx"
    src.write_bytes(b"x")
    client = _client(_engine(), store=None)
    r = client.post(
        "/commit-review",
        headers={**LOOPBACK, **AUTH},
        json={
            "input_path": str(src),
            "output_path": str(tmp_path / "final.docx"),
            "attested": True,
        },
    )
    assert r.status_code == 400


def test_commit_review_400_when_store_locked(tmp_path: Path):
    """Distinct from the no-store case — returns 409 per pipeline convention."""

    class _Locked:
        is_unlocked = False

    src = tmp_path / "review.docx"
    src.write_bytes(b"x")
    client = _client(_engine(), store=_Locked())
    r = client.post(
        "/commit-review",
        headers={**LOOPBACK, **AUTH},
        json={
            "input_path": str(src),
            "output_path": str(tmp_path / "final.docx"),
            "attested": True,
        },
    )
    assert r.status_code == 409


def test_commit_review_501_when_engine_not_wired(tmp_path: Path):
    """WS1 always hits the NotImplementedError branch → 501."""
    src = tmp_path / "review.docx"
    src.write_bytes(b"x")

    with patch(
        "sanctum.api.routes.pipeline.adapter_for",
        return_value=(_StubReaderWriter(), _StubReaderWriter()),
    ):
        client = _client(_engine(), _UnlockedStore())
        r = client.post(
            "/commit-review",
            headers={**LOOPBACK, **AUTH},
            json={
                "input_path": str(src),
                "output_path": str(tmp_path / "final.docx"),
                "attested": True,
            },
        )
    assert r.status_code == 501, r.get_json()
    assert "not yet implemented" in r.get_json()["error"]


def test_commit_review_400_on_bad_input_path(tmp_path: Path):
    client = _client(_engine(), _UnlockedStore())
    r = client.post(
        "/commit-review",
        headers={**LOOPBACK, **AUTH},
        json={
            "input_path": "relative/path.docx",
            "output_path": str(tmp_path / "final.docx"),
            "attested": True,
        },
    )
    assert r.status_code == 400
    assert "input_path" in r.get_json()["error"]


def test_commit_review_503_when_engine_unconfigured(tmp_path: Path):
    src = tmp_path / "review.docx"
    src.write_bytes(b"x")
    client = _client(None, _UnlockedStore())
    r = client.post(
        "/commit-review",
        headers={**LOOPBACK, **AUTH},
        json={
            "input_path": str(src),
            "output_path": str(tmp_path / "final.docx"),
            "attested": True,
        },
    )
    assert r.status_code == 503
