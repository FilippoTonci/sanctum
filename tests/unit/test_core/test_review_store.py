"""Tests for the local review-session store."""

from __future__ import annotations

import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sanctum.core.exceptions import ReviewSessionNotFoundError
from sanctum.core.models import (
    ProposalDecision,
    ReviewProposal,
    ReviewSession,
    TextSegment,
)
from sanctum.core.review.store import SessionStore


def _session(session_id: str = "sess-t0", **overrides: object) -> ReviewSession:
    defaults: dict[str, object] = {
        "id": session_id,
        "source_path": Path("/tmp/input.docx"),
        "format": "docx",
        "default_operator": "replace",
        "segments": [TextSegment(id="s0", text="Alice")],
        "proposals": [
            ReviewProposal(
                detection_id="abcdef012345",
                entity_type="PERSON",
                score=0.9,
                original="Alice",
                start=0,
                end=5,
            )
        ],
        "created_at": datetime(2026, 4, 24, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return ReviewSession(**defaults)  # type: ignore[arg-type]


def test_save_creates_dir_and_manifest(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    session = _session()
    store.save(session)
    assert (tmp_path / session.id / "manifest.json").exists()


def test_save_applies_restrictive_permissions(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    session = _session()
    store.save(session)
    session_dir = tmp_path / session.id
    manifest = session_dir / "manifest.json"
    assert stat.S_IMODE(session_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600


def test_save_persists_input_bytes_on_first_call(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    session = _session()
    store.save(session, input_bytes=b"PK\x03\x04fake-docx")
    input_file = tmp_path / session.id / "input.docx"
    assert input_file.exists()
    assert stat.S_IMODE(input_file.stat().st_mode) == 0o600
    assert input_file.read_bytes() == b"PK\x03\x04fake-docx"


def test_save_does_not_overwrite_input_bytes_on_subsequent_call(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    session = _session()
    store.save(session, input_bytes=b"original")
    store.save(session, input_bytes=b"overwritten?")  # must be ignored
    assert (tmp_path / session.id / "input.docx").read_bytes() == b"original"


def test_save_overwrites_manifest_on_subsequent_call(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    session = _session()
    store.save(session)
    session.decisions.append(ProposalDecision(proposal_id="abcdef012345", status="accept"))
    store.save(session)
    restored = store.load(session.id)
    assert restored.decisions == session.decisions


def test_load_round_trips_session(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    session = _session()
    session.decisions.append(ProposalDecision(proposal_id="abcdef012345", status="accept"))
    store.save(session)
    restored = store.load(session.id)
    assert restored.id == session.id
    assert restored.decisions == session.decisions
    assert restored.proposals == session.proposals


def test_load_missing_raises(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    with pytest.raises(ReviewSessionNotFoundError):
        store.load("does-not-exist")


def test_load_input_bytes(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    session = _session()
    store.save(session, input_bytes=b"hello")
    assert store.load_input_bytes(session.id) == b"hello"


def test_load_input_bytes_without_session_dir_raises(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    with pytest.raises(ReviewSessionNotFoundError):
        store.load_input_bytes("nope")


def test_load_input_bytes_without_stored_input_raises(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    session = _session()
    store.save(session)  # no input_bytes
    with pytest.raises(ReviewSessionNotFoundError):
        store.load_input_bytes(session.id)


def test_delete_removes_session_dir(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    session = _session()
    store.save(session)
    store.delete(session.id)
    assert not (tmp_path / session.id).exists()


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    store.delete("never-existed")  # must not raise


def test_exists_reflects_save_and_delete(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    session = _session()
    assert not store.exists(session.id)
    store.save(session)
    assert store.exists(session.id)
    store.delete(session.id)
    assert not store.exists(session.id)


def test_list_ids_enumerates_sessions(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    store.save(_session("aaa"))
    store.save(_session("bbb"))
    assert store.list_ids() == ["aaa", "bbb"]


def test_list_ids_empty_when_root_missing(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "nope")
    assert store.list_ids() == []
