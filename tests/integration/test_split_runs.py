"""Names split across Word / PowerPoint runs are found and redacted whole.

Real Presidio stack. Every name below is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import docx
import pytest
from pptx import Presentation
from pptx.util import Inches
from sanctum.analyzer.adapter import PresidioAnalyzer
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.core.engine import SanctumEngine
from sanctum.core.models import OperatorPolicy, ProposalDecision
from sanctum.core.review.session import add_decision
from sanctum.core.review.store import SessionStore
from sanctum.documents import pptx_adapter
from sanctum.documents.docx_adapter import Reader, Writer

pytestmark = pytest.mark.integration

SPLIT_RUNS = ["Dear Jennifer", " ", "Martin, thanks."]


@pytest.fixture(scope="module")
def engine() -> SanctumEngine:
    return SanctumEngine(analyzer=PresidioAnalyzer(), anonymizer=PresidioAnonymizer())


def make_docx(path: Path, runs: list[str]) -> Path:
    d = docx.Document()
    para = d.add_paragraph()
    for i, text in enumerate(runs):
        para.add_run(text).bold = i % 2 == 1  # alternate formatting forces separate runs
    d.save(str(path))
    return path


def make_pptx(path: Path, runs: list[str]) -> Path:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    para = box.text_frame.paragraphs[0]
    for i, text in enumerate(runs):
        run = para.add_run()
        run.text = text
        run.font.bold = i % 2 == 1
    prs.save(str(path))
    return path


def accept_all(store: SessionStore, session_id: str) -> None:
    with store.locked(session_id):
        s = store.load(session_id)
        for p in s.proposals:
            add_decision(s, ProposalDecision(proposal_id=p.detection_id, status="accept"))
        store.save(s)


def test_split_name_is_redacted_whole_in_docx(engine: SanctumEngine, tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    src = make_docx(tmp_path / "in.docx", SPLIT_RUNS)
    session = engine.create_review_session(
        Reader(), src, default_operator="replace", session_store=store
    )
    assert {p.group_original for p in session.proposals if p.group_id} == {"Jennifer Martin"}
    accept_all(store, session.id)
    out = tmp_path / "out.docx"
    engine.commit_review_session(Reader(), Writer(), session.id, out, store)
    text = "".join(r.text for r in docx.Document(str(out)).paragraphs[0].runs)
    assert text == "Dear <PERSON>, thanks."


def test_rejected_split_name_survives_and_passes_the_leak_check(
    engine: SanctumEngine, tmp_path: Path
) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    src = make_docx(tmp_path / "in.docx", SPLIT_RUNS)
    session = engine.create_review_session(
        Reader(), src, default_operator="replace", session_store=store
    )
    tail = next(p for p in session.proposals if p.group_id and p.group_index > 0)
    with store.locked(session.id):
        s = store.load(session.id)
        add_decision(s, ProposalDecision(proposal_id=tail.detection_id, status="reject"))
        store.save(s)
    out = tmp_path / "out.docx"
    engine.commit_review_session(Reader(), Writer(), session.id, out, store)
    assert docx.Document(str(out)).paragraphs[0].text == "".join(SPLIT_RUNS)


def test_split_name_is_redacted_whole_in_docx_without_review(
    engine: SanctumEngine, tmp_path: Path
) -> None:
    src = make_docx(tmp_path / "in.docx", SPLIT_RUNS)
    out = tmp_path / "out.docx"
    replace = {"DEFAULT": OperatorPolicy(operator_name="replace")}
    engine.process_document(Reader(), Writer(), src, out, operator_policies=replace)
    assert docx.Document(str(out)).paragraphs[0].text == "Dear <PERSON>, thanks."


def test_split_name_is_redacted_whole_in_pptx(engine: SanctumEngine, tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    src = make_pptx(tmp_path / "in.pptx", SPLIT_RUNS)
    reader, writer = pptx_adapter.Reader(), pptx_adapter.Writer()
    session = engine.create_review_session(
        reader, src, default_operator="replace", session_store=store
    )
    assert {p.group_original for p in session.proposals if p.group_id} == {"Jennifer Martin"}
    accept_all(store, session.id)
    out = tmp_path / "out.pptx"
    engine.commit_review_session(reader, writer, session.id, out, store)
    shape = Presentation(str(out)).slides[0].shapes[0]
    assert shape.text_frame.text == "Dear <PERSON>, thanks."


def test_rc3_manifest_without_new_fields_still_commits(
    engine: SanctumEngine, tmp_path: Path
) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    src = make_docx(tmp_path / "in.docx", ["Dear Cameron Dean, thanks."])
    session = engine.create_review_session(
        Reader(), src, default_operator="replace", session_store=store
    )
    manifest = store.root / session.id / "manifest.json"
    raw = json.loads(manifest.read_text())
    for seg in raw["segments"]:
        seg.pop("block", None)
        seg.pop("join_before", None)
    for prop in raw["proposals"]:
        for key in ("group_id", "group_index", "group_original"):
            prop.pop(key, None)
    manifest.write_text(json.dumps(raw))

    accept_all(store, session.id)
    out = tmp_path / "out.docx"
    engine.commit_review_session(Reader(), Writer(), session.id, out, store)
    assert "Cameron" not in docx.Document(str(out)).paragraphs[0].text
