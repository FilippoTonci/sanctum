"""Unit tests for the review-session endpoint family (Phase 1.5 WS2)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from sanctum.api.app import create_app
from sanctum.core.engine import SanctumEngine
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    OperatorPolicy,
    StructuredDocument,
    TextSegment,
)
from sanctum.core.review.store import SessionStore

LOOPBACK = {"Host": "127.0.0.1:8765"}
AUTH = {"Authorization": "Bearer t"}


# ---------- fake analyzer / anonymizer ----------


class _FakeAnalyzer:
    """Returns the same detection list for every call.

    Tests set ``detections`` to shape the proposals built on create; the
    analyzer is invoked once per non-empty segment.
    """

    def __init__(self, detections: list[DetectionResult] | None = None) -> None:
        self.detections = list(detections or [])
        self.calls: list[str] = []

    def analyze(
        self,
        text: str,
        language: str = "en",
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[DetectionResult]:
        self.calls.append(text)
        return list(self.detections)


class _BracketAnonymizer:
    """Single-detection anonymizer that wraps spans in ``[ENTITY]``.

    Matches the contract used by ``compute_preview`` — one detection at a
    time — so both the preview and commit paths return predictable values.
    ``operator_params['new_value']`` wins over the bracket default when
    set, so tests can assert on per-decision operator overrides.
    """

    def anonymize(
        self,
        text: str,
        detections: list[DetectionResult],
        operator_policies: dict[str, OperatorPolicy] | None = None,
    ) -> AnonymizationResult:
        det = detections[0]
        policy = (operator_policies or {}).get("DEFAULT")
        if policy and policy.params.get("new_value") is not None:
            replacement = policy.params["new_value"]
        else:
            replacement = f"[{det.entity_type}]"
        return AnonymizationResult(
            original_text=text,
            anonymized_text=text[: det.start] + replacement + text[det.end :],
            detections=detections,
            operators_applied={det.entity_type: policy.operator_name if policy else "replace"},
        )


# ---------- fixtures ----------


def _doc_factory(segments: list[TextSegment]) -> Any:
    def _make(path: Path) -> StructuredDocument:
        return StructuredDocument(
            source_path=path,
            format="docx",
            segments=[s.model_copy() for s in segments],
            raw_handle=object(),
        )

    return _make


@pytest.fixture()
def tmp_input_path(tmp_path: Path) -> Path:
    p = tmp_path / "input.docx"
    p.write_bytes(b"PK\x03\x04fake")
    return p


@pytest.fixture()
def tmp_output_path(tmp_path: Path) -> Path:
    # Must not exist (validate_local_path on out_path wants must_exist=False).
    return tmp_path / "out.docx"


@pytest.fixture()
def session_store(tmp_path: Path) -> SessionStore:
    return SessionStore(root=tmp_path / "sessions")


@pytest.fixture()
def analyzer() -> _FakeAnalyzer:
    return _FakeAnalyzer(
        detections=[
            DetectionResult(
                entity_type="PERSON",
                start=0,
                end=5,
                score=0.9,
                text_span="Alice",
            )
        ]
    )


@pytest.fixture()
def anonymizer() -> _BracketAnonymizer:
    return _BracketAnonymizer()


@pytest.fixture()
def engine(analyzer: _FakeAnalyzer, anonymizer: _BracketAnonymizer) -> SanctumEngine:
    return SanctumEngine(analyzer=analyzer, anonymizer=anonymizer)  # type: ignore[arg-type]


@pytest.fixture()
def fake_reader() -> Mock:
    reader = Mock()
    reader.read.side_effect = _doc_factory(
        [
            TextSegment(id="s0", text="Alice met Bob"),
            TextSegment(id="s1", text="Carol said hi"),
        ]
    )
    return reader


@pytest.fixture()
def fake_writer() -> Mock:
    writer = Mock()
    writer.write = Mock()
    return writer


@pytest.fixture()
def patched_adapter(
    monkeypatch: pytest.MonkeyPatch, fake_reader: Mock, fake_writer: Mock
) -> tuple[Mock, Mock]:
    """Route-level adapter_for swap so tests don't need real Office files.

    Both ``create_review_session`` and ``commit_review_session`` go through
    the route's ``adapter_for`` shim, so a single monkeypatch covers the
    create + commit call sites.
    """

    def _fake_adapter(_: Path) -> tuple[Mock, Mock]:
        return fake_reader, fake_writer

    monkeypatch.setattr("sanctum.api.routes.review_sessions.adapter_for", _fake_adapter)
    return fake_reader, fake_writer


@pytest.fixture()
def client(engine: SanctumEngine, session_store: SessionStore) -> Any:
    app = create_app(
        token="t", host="127.0.0.1", port=8765, engine=engine, session_store=session_store
    )
    return app.test_client()


# ---------- helper to create a session inline ----------


def _create_session(
    client: Any, input_path: Path, default_operator: str = "replace"
) -> dict[str, Any]:
    r = client.post(
        "/review-sessions",
        headers={**LOOPBACK, **AUTH},
        json={
            "input_path": str(input_path),
            "default_operator": default_operator,
        },
    )
    assert r.status_code == 201, r.get_json()
    return r.get_json()


# ================ POST /review-sessions ====================================


class TestCreateSession:
    def test_requires_bearer_token(self, client: Any, tmp_input_path: Path) -> None:
        r = client.post(
            "/review-sessions",
            headers=LOOPBACK,
            json={"input_path": str(tmp_input_path), "default_operator": "replace"},
        )
        assert r.status_code == 401

    def test_503_when_engine_unconfigured(
        self, session_store: SessionStore, tmp_input_path: Path
    ) -> None:
        app = create_app(
            token="t", host="127.0.0.1", port=8765, engine=None, session_store=session_store
        )
        c = app.test_client()
        r = c.post(
            "/review-sessions",
            headers={**LOOPBACK, **AUTH},
            json={"input_path": str(tmp_input_path), "default_operator": "replace"},
        )
        assert r.status_code == 503

    def test_400_on_missing_body(self, client: Any) -> None:
        r = client.post("/review-sessions", headers={**LOOPBACK, **AUTH})
        assert r.status_code == 400

    def test_400_on_relative_path(self, client: Any, patched_adapter: Any) -> None:
        r = client.post(
            "/review-sessions",
            headers={**LOOPBACK, **AUTH},
            json={"input_path": "relative/thing.docx", "default_operator": "replace"},
        )
        assert r.status_code == 400
        assert "absolute" in r.get_json()["error"]

    def test_400_on_missing_file(self, client: Any, tmp_path: Path) -> None:
        r = client.post(
            "/review-sessions",
            headers={**LOOPBACK, **AUTH},
            json={
                "input_path": str(tmp_path / "nope.docx"),
                "default_operator": "replace",
            },
        )
        assert r.status_code == 400

    def test_400_on_custom_operator(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        """`custom` isn't encodable over HTTP — schema validator rejects it."""
        r = client.post(
            "/review-sessions",
            headers={**LOOPBACK, **AUTH},
            json={"input_path": str(tmp_input_path), "default_operator": "custom"},
        )
        assert r.status_code == 400

    def test_happy_path_returns_session_with_previews(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        body = _create_session(client, tmp_input_path, default_operator="replace")
        assert body["status"] == "open"
        assert body["default_operator"] == "replace"
        assert body["format"] == "docx"
        assert len(body["segments"]) == 2
        # Two segments, both non-empty — one PERSON detection each.
        assert len(body["proposals"]) == 2
        # Previews under the session default (replace → ``<PERSON>``-like
        # output from the bracket anonymizer).
        assert all(
            p in body["previews"] for p in (prop["detection_id"] for prop in body["proposals"])
        )
        for detection_id in (prop["detection_id"] for prop in body["proposals"]):
            assert body["previews"][detection_id] == "[PERSON]"


# ================ GET /review-sessions (list) ==============================


class TestListSessions:
    def test_requires_bearer_token(self, client: Any) -> None:
        r = client.get("/review-sessions", headers=LOOPBACK)
        assert r.status_code == 401

    def test_empty_when_no_sessions_exist(self, client: Any) -> None:
        r = client.get("/review-sessions", headers={**LOOPBACK, **AUTH})
        assert r.status_code == 200
        assert r.get_json() == {"sessions": []}

    def test_lists_open_session_with_zero_decisions(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)

        r = client.get("/review-sessions", headers={**LOOPBACK, **AUTH})
        assert r.status_code == 200
        body = r.get_json()
        assert len(body["sessions"]) == 1
        entry = body["sessions"][0]
        assert entry["id"] == created["id"]
        assert entry["status"] == "open"
        assert entry["format"] == "docx"
        assert entry["accepted_count"] == 0
        assert entry["rejected_count"] == 0
        assert entry["pending_count"] == len(created["proposals"])
        assert entry["committed_at"] is None

    def test_counts_reflect_decisions(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        pids = [p["detection_id"] for p in created["proposals"]]
        # accept the first, reject nothing, leave the second pending.
        client.patch(
            f"/review-sessions/{created['id']}/decisions/{pids[0]}",
            headers={**LOOPBACK, **AUTH},
            json={"status": "accept"},
        )
        # add one user-added decision (which always counts as accepted).
        client.post(
            f"/review-sessions/{created['id']}/decisions/user-added",
            headers={**LOOPBACK, **AUTH},
            json={
                "segment_anchor": "s0",
                "entity_type": "PERSON",
                "original": "Bob",
                "start": 10,
                "end": 13,
            },
        )

        r = client.get("/review-sessions", headers={**LOOPBACK, **AUTH})
        body = r.get_json()
        entry = next(e for e in body["sessions"] if e["id"] == created["id"])
        assert entry["accepted_count"] == 2  # 1 proposal + 1 user-added
        assert entry["rejected_count"] == 0
        assert entry["pending_count"] == len(pids) - 1

    def test_orders_newest_first(
        self,
        client: Any,
        tmp_input_path: Path,
        patched_adapter: Any,
        session_store: SessionStore,
    ) -> None:
        # create three sessions; mutate stored created_at to known offsets so
        # the assertion is deterministic across the route's wall-clock.
        ids = []
        for _ in range(3):
            created = _create_session(client, tmp_input_path)
            ids.append(created["id"])
        for i, sid in enumerate(ids):
            session = session_store.load(sid)
            session.created_at = datetime(2026, 4, 25 - i, 12, 0, tzinfo=timezone.utc)
            session_store.save(session)

        r = client.get("/review-sessions", headers={**LOOPBACK, **AUTH})
        ordered_ids = [e["id"] for e in r.get_json()["sessions"]]
        # ids[0] got the most-recent created_at (April 25), so it sorts first.
        assert ordered_ids == [ids[0], ids[1], ids[2]]


# ================ GET /review-sessions/{id} ================================


class TestGetSession:
    def test_404_on_unknown_id(self, client: Any) -> None:
        r = client.get("/review-sessions/nope", headers={**LOOPBACK, **AUTH})
        assert r.status_code == 404

    def test_returns_session_with_previews(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        r = client.get(
            f"/review-sessions/{created['id']}",
            headers={**LOOPBACK, **AUTH},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["id"] == created["id"]
        assert body["previews"] == created["previews"]


# ================ PATCH decisions ==========================================


class TestPatchDecision:
    def test_accept_with_default_operator(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        pid = created["proposals"][0]["detection_id"]
        r = client.patch(
            f"/review-sessions/{created['id']}/decisions/{pid}",
            headers={**LOOPBACK, **AUTH},
            json={"status": "accept"},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["decision"]["status"] == "accept"
        assert body["preview"] == "[PERSON]"

    def test_reject_preview_is_original(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        pid = created["proposals"][0]["detection_id"]
        r = client.patch(
            f"/review-sessions/{created['id']}/decisions/{pid}",
            headers={**LOOPBACK, **AUTH},
            json={"status": "reject"},
        )
        assert r.status_code == 200
        # bracket anonymizer's detection was "Alice"; rejection preview
        # is the raw original text.
        assert r.get_json()["preview"] == "Alice"

    def test_custom_replacement_short_circuits(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        pid = created["proposals"][0]["detection_id"]
        r = client.patch(
            f"/review-sessions/{created['id']}/decisions/{pid}",
            headers={**LOOPBACK, **AUTH},
            json={"status": "accept", "custom_replacement": "[DEFENDANT]"},
        )
        assert r.status_code == 200
        assert r.get_json()["preview"] == "[DEFENDANT]"

    def test_per_decision_operator_override(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        pid = created["proposals"][0]["detection_id"]
        r = client.patch(
            f"/review-sessions/{created['id']}/decisions/{pid}",
            headers={**LOOPBACK, **AUTH},
            json={
                "status": "accept",
                "operator": "replace",
                "operator_params": {"new_value": "[OVERRIDE]"},
            },
        )
        assert r.status_code == 200
        assert r.get_json()["preview"] == "[OVERRIDE]"

    def test_patch_overwrites_prior_decision(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        pid = created["proposals"][0]["detection_id"]
        client.patch(
            f"/review-sessions/{created['id']}/decisions/{pid}",
            headers={**LOOPBACK, **AUTH},
            json={"status": "accept"},
        )
        r = client.patch(
            f"/review-sessions/{created['id']}/decisions/{pid}",
            headers={**LOOPBACK, **AUTH},
            json={"status": "reject"},
        )
        assert r.status_code == 200
        assert r.get_json()["decision"]["status"] == "reject"

    def test_404_on_unknown_session(self, client: Any) -> None:
        r = client.patch(
            "/review-sessions/nope/decisions/whatever",
            headers={**LOOPBACK, **AUTH},
            json={"status": "accept"},
        )
        assert r.status_code == 404

    def test_404_on_unknown_proposal(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        r = client.patch(
            f"/review-sessions/{created['id']}/decisions/missing000000",
            headers={**LOOPBACK, **AUTH},
            json={"status": "accept"},
        )
        assert r.status_code == 404

    def test_400_on_missing_status(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        pid = created["proposals"][0]["detection_id"]
        r = client.patch(
            f"/review-sessions/{created['id']}/decisions/{pid}",
            headers={**LOOPBACK, **AUTH},
            json={},
        )
        assert r.status_code == 400


# ================ POST user-added decisions ================================


class TestUserAddedDecisions:
    def test_add_happy_path(self, client: Any, tmp_input_path: Path, patched_adapter: Any) -> None:
        created = _create_session(client, tmp_input_path)
        r = client.post(
            f"/review-sessions/{created['id']}/decisions/user-added",
            headers={**LOOPBACK, **AUTH},
            json={
                "segment_anchor": "s0",
                "entity_type": "PERSON",
                "original": "Bob",
                "start": 10,
                "end": 13,
            },
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body["decision"]["segment_anchor"] == "s0"
        assert body["decision"]["entity_type"] == "PERSON"
        assert body["decision"]["start"] == 10
        assert body["decision"]["end"] == 13
        assert "id" in body["decision"]
        assert body["preview"] == "[PERSON]"

    def test_400_on_unknown_anchor(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        r = client.post(
            f"/review-sessions/{created['id']}/decisions/user-added",
            headers={**LOOPBACK, **AUTH},
            json={
                "segment_anchor": "nonexistent",
                "entity_type": "PERSON",
                "original": "Bob",
                "start": 0,
                "end": 3,
            },
        )
        assert r.status_code == 400

    def test_400_on_offsets_disagreeing_with_segment_text(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        # Segment s0 is "Alice met Bob"; original="Bob" but start/end pointing
        # at "Alice" should be rejected so a stale offset can't silently flag
        # the wrong span at commit time.
        created = _create_session(client, tmp_input_path)
        r = client.post(
            f"/review-sessions/{created['id']}/decisions/user-added",
            headers={**LOOPBACK, **AUTH},
            json={
                "segment_anchor": "s0",
                "entity_type": "PERSON",
                "original": "Bob",
                "start": 0,
                "end": 5,
            },
        )
        assert r.status_code == 400
        assert "does not match" in r.get_json()["error"]

    def test_400_on_offsets_past_segment_end(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        r = client.post(
            f"/review-sessions/{created['id']}/decisions/user-added",
            headers={**LOOPBACK, **AUTH},
            json={
                "segment_anchor": "s0",
                "entity_type": "PERSON",
                "original": "ZZZ",
                "start": 10,
                "end": 999,
            },
        )
        assert r.status_code == 400

    def test_delete_happy_path(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        r_add = client.post(
            f"/review-sessions/{created['id']}/decisions/user-added",
            headers={**LOOPBACK, **AUTH},
            json={
                "segment_anchor": "s0",
                "entity_type": "PERSON",
                "original": "Bob",
                "start": 10,
                "end": 13,
            },
        )
        ua_id = r_add.get_json()["decision"]["id"]
        r_del = client.delete(
            f"/review-sessions/{created['id']}/decisions/user-added/{ua_id}",
            headers={**LOOPBACK, **AUTH},
        )
        assert r_del.status_code == 204

    def test_delete_404_on_unknown_id(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        r = client.delete(
            f"/review-sessions/{created['id']}/decisions/user-added/not-a-real-id",
            headers={**LOOPBACK, **AUTH},
        )
        assert r.status_code == 404


# ================ POST /commit =============================================


class TestCommit:
    def test_happy_path_writes_file_and_keeps_manifest(
        self,
        client: Any,
        tmp_input_path: Path,
        tmp_output_path: Path,
        patched_adapter: Any,
        fake_writer: Mock,
        session_store: SessionStore,
    ) -> None:
        created = _create_session(client, tmp_input_path)
        pid = created["proposals"][0]["detection_id"]
        client.patch(
            f"/review-sessions/{created['id']}/decisions/{pid}",
            headers={**LOOPBACK, **AUTH},
            json={"status": "accept"},
        )
        r = client.post(
            f"/review-sessions/{created['id']}/commit",
            headers={**LOOPBACK, **AUTH},
            json={"output_path": str(tmp_output_path), "attested": True},
        )
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["session_id"] == created["id"]
        assert body["output_path"] == str(tmp_output_path)
        assert body["committed_at"] is not None
        fake_writer.write.assert_called_once()
        # Manifest persists with status=committed; only input bytes shed.
        assert session_store.exists(created["id"])
        restored = session_store.load(created["id"])
        assert restored.status == "committed"
        from sanctum.core.exceptions import ReviewSessionNotFoundError

        with pytest.raises(ReviewSessionNotFoundError):
            session_store.load_input_bytes(created["id"])

    def test_400_without_attested(
        self,
        client: Any,
        tmp_input_path: Path,
        tmp_output_path: Path,
        patched_adapter: Any,
    ) -> None:
        created = _create_session(client, tmp_input_path)
        r = client.post(
            f"/review-sessions/{created['id']}/commit",
            headers={**LOOPBACK, **AUTH},
            json={"output_path": str(tmp_output_path)},
        )
        assert r.status_code == 400
        assert "attested" in r.get_json()["error"]

    def test_404_on_unknown_session(self, client: Any, tmp_output_path: Path) -> None:
        r = client.post(
            "/review-sessions/nope/commit",
            headers={**LOOPBACK, **AUTH},
            json={"output_path": str(tmp_output_path), "attested": True},
        )
        assert r.status_code == 404

    def test_400_on_relative_output_path(
        self, client: Any, tmp_input_path: Path, patched_adapter: Any
    ) -> None:
        created = _create_session(client, tmp_input_path)
        r = client.post(
            f"/review-sessions/{created['id']}/commit",
            headers={**LOOPBACK, **AUTH},
            json={"output_path": "relative/out.docx", "attested": True},
        )
        assert r.status_code == 400

    def test_409_on_double_commit(
        self,
        client: Any,
        tmp_input_path: Path,
        tmp_output_path: Path,
        patched_adapter: Any,
    ) -> None:
        created = _create_session(client, tmp_input_path)
        client.post(
            f"/review-sessions/{created['id']}/commit",
            headers={**LOOPBACK, **AUTH},
            json={"output_path": str(tmp_output_path), "attested": True},
        )
        # Second call — manifest survives (Recent Sessions audit trail);
        # status check on the route surfaces the mismatch as 409.
        r = client.post(
            f"/review-sessions/{created['id']}/commit",
            headers={**LOOPBACK, **AUTH},
            json={"output_path": str(tmp_output_path), "attested": True},
        )
        assert r.status_code == 409
        assert "committed" in r.get_json()["error"]


# ================ DELETE (abandon) =========================================


class TestAbandon:
    def test_happy_path(
        self,
        client: Any,
        tmp_input_path: Path,
        patched_adapter: Any,
        session_store: SessionStore,
    ) -> None:
        created = _create_session(client, tmp_input_path)
        r = client.delete(
            f"/review-sessions/{created['id']}",
            headers={**LOOPBACK, **AUTH},
        )
        assert r.status_code == 204
        # Manifest persists with status=abandoned so the desktop's
        # Recent Sessions list can show the audit trail; only the
        # input bytes are shed.
        assert session_store.exists(created["id"])
        restored = session_store.load(created["id"])
        assert restored.status == "abandoned"
        from sanctum.core.exceptions import ReviewSessionNotFoundError

        with pytest.raises(ReviewSessionNotFoundError):
            session_store.load_input_bytes(created["id"])

    def test_404_on_unknown_session(self, client: Any) -> None:
        r = client.delete("/review-sessions/nope", headers={**LOOPBACK, **AUTH})
        assert r.status_code == 404
