"""Concurrent decision PATCHes against one review session must not lose writes."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from flask import Flask
from sanctum.analyzer.adapter import PresidioAnalyzer
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.api.app import create_app
from sanctum.core.engine import SanctumEngine
from sanctum.core.review.store import SessionStore

pytestmark = pytest.mark.integration

_TOKEN = "integration-token-do-not-reuse"
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "office" / "nda_contract.docx"
_PORT = 8765
_HEADERS = {"Authorization": f"Bearer {_TOKEN}", "Host": f"127.0.0.1:{_PORT}"}


@pytest.fixture(scope="module")
def app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Flask]:
    if not _FIXTURE.is_file():
        pytest.skip(f"fixture missing: {_FIXTURE}")
    engine = SanctumEngine(analyzer=PresidioAnalyzer(), anonymizer=PresidioAnonymizer())
    yield create_app(
        token=_TOKEN,
        host="127.0.0.1",
        port=_PORT,
        engine=engine,
        session_store=SessionStore(root=tmp_path_factory.mktemp("sessions")),
    )


@pytest.fixture
def open_docx_session(app: Flask) -> tuple[str, list[str]]:
    with app.test_client() as c:
        r = c.post(
            "/review-sessions",
            json={"input_path": str(_FIXTURE), "default_operator": "replace"},
            headers=_HEADERS,
        )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    ids = [p["detection_id"] for p in body["proposals"]]
    assert len(ids) >= 10, f"fixture yielded only {len(ids)} proposals"
    return body["id"], ids


def test_parallel_patches_all_land(app: Flask, open_docx_session: tuple[str, list[str]]) -> None:
    session_id, proposal_ids = open_docx_session

    def patch(pid: str) -> int:
        with app.test_client() as c:
            r = c.patch(
                f"/review-sessions/{session_id}/decisions/{pid}",
                json={"status": "accept"},
                headers=_HEADERS,
            )
            return r.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(patch, proposal_ids))

    assert codes == [200] * len(proposal_ids)
    with app.test_client() as c:
        body = c.get(f"/review-sessions/{session_id}", headers=_HEADERS).get_json()
    decided = {d["proposal_id"] for d in body["decisions"] if d.get("kind") == "proposal"}
    assert decided == set(proposal_ids)


def test_patch_racing_commit_never_500s(
    app: Flask, open_docx_session: tuple[str, list[str]], tmp_path: Path
) -> None:
    session_id, proposal_ids = open_docx_session

    def patch(pid: str) -> int:
        with app.test_client() as c:
            return c.patch(
                f"/review-sessions/{session_id}/decisions/{pid}",
                json={"status": "accept"},
                headers=_HEADERS,
            ).status_code

    def commit() -> int:
        with app.test_client() as c:
            return c.post(
                f"/review-sessions/{session_id}/commit",
                json={"output_path": str(tmp_path / "out.docx"), "attested": True},
                headers=_HEADERS,
            ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(patch, pid) for pid in proposal_ids] + [pool.submit(commit)]
        codes = [f.result() for f in futures]

    assert 500 not in codes
    assert set(codes) <= {200, 409, 422}
