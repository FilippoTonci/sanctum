"""API-level PDF review flow with real Presidio.

create session -> GET /layout -> accept detections -> commit -> redacted
PDF, then check the output: replaced originals are gone from the text
layer (the engine's leak check also ran), untouched pages stay vector,
and metadata is stripped.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader
from sanctum.analyzer.adapter import PresidioAnalyzer
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.api.app import create_app
from sanctum.core.engine import SanctumEngine
from sanctum.core.review.store import SessionStore
from sanctum.documents.pdf_adapter import extract_all_text

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
HEADERS = {"Host": "127.0.0.1:8765", "Authorization": "Bearer t"}


def _samples() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_pdf_samples", ROOT / "scripts" / "generate_pdf_samples.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


samples = _samples()


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    engine = SanctumEngine(analyzer=PresidioAnalyzer(), anonymizer=PresidioAnonymizer())
    app = create_app(
        token="t",
        host="127.0.0.1",
        port=8765,
        engine=engine,
        session_store=SessionStore(root=tmp_path_factory.mktemp("sessions")),
    )
    yield app.test_client()


@pytest.fixture()
def letter(tmp_path: Path) -> Path:
    path = tmp_path / "rich_letter.pdf"
    samples.build_rich_letter(path)
    return path


def _create(client: Any, path: Path) -> dict[str, Any]:
    r = client.post(
        "/review-sessions",
        headers=HEADERS,
        json={"input_path": str(path), "default_operator": "replace"},
    )
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_full_pdf_review_flow(client: Any, letter: Path, tmp_path: Path) -> None:
    session = _create(client, letter)
    assert session["format"] == "pdf"
    seg_ids = {s["id"] for s in session["segments"]}
    assert "page0/line0" in seg_ids

    # Layout: textline items, one per session segment, same ids.
    r = client.get(f"/review-sessions/{session['id']}/layout", headers=HEADERS)
    assert r.status_code == 200, r.get_json()
    layout = r.get_json()
    assert layout["format"] == "pdf" and len(layout["pages"]) == 4
    layout_ids = [it["segment_id"] for p in layout["pages"] for it in p["items"]]
    assert set(layout_ids) == seg_ids and len(layout_ids) == len(seg_ids)

    # Accept every detection.
    proposals = session["proposals"]
    persons = {p["original"] for p in proposals if p["entity_type"] == "PERSON"}
    assert samples.CLIENT in persons, persons
    for p in proposals:
        r = client.patch(
            f"/review-sessions/{session['id']}/decisions/{p['detection_id']}",
            headers=HEADERS,
            json={"status": "accept"},
        )
        assert r.status_code == 200

    out = tmp_path / "redacted.pdf"
    r = client.post(
        f"/review-sessions/{session['id']}/commit",
        headers=HEADERS,
        json={"output_path": str(out), "attested": True},
    )
    assert r.status_code == 200, r.get_json()

    text = extract_all_text(out.read_bytes())
    for original in {p["original"] for p in proposals}:
        assert original not in text, original
    assert "<PERSON>" in text
    reader = PdfReader(str(out))
    assert len(reader.pages) == 4
    assert reader.metadata is None

    # Layout needs the input bytes, which commit shed.
    r = client.get(f"/review-sessions/{session['id']}/layout", headers=HEADERS)
    assert r.status_code == 410


def test_commit_fails_with_422_when_a_replaced_value_survives(
    client: Any, letter: Path, tmp_path: Path
) -> None:
    """Accept only the first CLIENT detection: other occurrences of the same
    name stay in the output, so the leak check must refuse the commit."""
    session = _create(client, letter)
    first = next(p for p in session["proposals"] if p["original"] == samples.CLIENT)
    client.patch(
        f"/review-sessions/{session['id']}/decisions/{first['detection_id']}",
        headers=HEADERS,
        json={"status": "accept"},
    )
    out = tmp_path / "leaky.pdf"
    r = client.post(
        f"/review-sessions/{session['id']}/commit",
        headers=HEADERS,
        json={"output_path": str(out), "attested": True},
    )
    assert r.status_code == 422, r.get_json()
    details = r.get_json()["details"]
    assert [d["leak"] for d in details] == [samples.CLIENT]
    assert all(isinstance(d["occurrences"], int) and d["occurrences"] >= 1 for d in details)
    assert not out.exists()
    r = client.get(f"/review-sessions/{session['id']}", headers=HEADERS)
    assert r.get_json()["status"] == "open"


def test_layout_unsupported_for_docx(client: Any) -> None:
    fixture = ROOT / "tests" / "fixtures" / "office" / "nda_contract.docx"
    session = _create(client, fixture)
    r = client.get(f"/review-sessions/{session['id']}/layout", headers=HEADERS)
    assert r.status_code == 415


def test_names_broken_across_lines_are_proposed_and_removed(
    client: Any, letter: Path, tmp_path: Path
) -> None:
    """ "Dr Evelyn / Marchetti" and "Priya / Raghunathan" wrap across lines;
    paragraph grouping finds them as linked findings and commit removes them."""
    session = _create(client, letter)
    proposals = session["proposals"]
    for surname in ("Marchetti", "Raghunathan"):
        linked = [p for p in proposals if surname in p["original"] and p.get("group_id")]
        assert linked, surname
    for p in proposals:
        client.patch(
            f"/review-sessions/{session['id']}/decisions/{p['detection_id']}",
            headers=HEADERS,
            json={"status": "accept"},
        )
    out = tmp_path / "redacted.pdf"
    r = client.post(
        f"/review-sessions/{session['id']}/commit",
        headers=HEADERS,
        json={"output_path": str(out), "attested": True},
    )
    assert r.status_code == 200, r.get_json()
    text = extract_all_text(out.read_bytes())
    assert "Marchetti" not in text and "Raghunathan" not in text
