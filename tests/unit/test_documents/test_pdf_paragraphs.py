"""PDF lines are grouped into paragraphs so wrapped names are one block."""

from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfgen import canvas
from sanctum.core.blocks import detect_blocks
from sanctum.core.models import DetectionResult
from sanctum.documents._pdf_redact import flatten, plan_edits
from sanctum.documents.pdf_adapter import Reader, _extract_file

LEADING = 13.2
TOP = 720.0


def draw_pdf(
    tmp_path: Path,
    *,
    wrapped_paragraph: bool = False,
    two_columns: bool = False,
    two_paragraphs: bool = False,
) -> Path:
    path = tmp_path / "p.pdf"
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 11)

    def put(x: float, row: float, text: str) -> None:
        c.drawString(x, TOP - row * LEADING, text)

    if wrapped_paragraph:
        put(72, 0, "This agreement was signed by Dr Evelyn")
        put(72, 1, "Marchetti on behalf of the partnership.")
    if two_columns:
        for r in range(3):
            put(72, r, f"Left column line {r}")
            put(320, r, f"Right column line {r}")
    if two_paragraphs:
        put(72, 0, "First paragraph line one")
        put(72, 1, "first paragraph line two")
        put(72, 4, "Second paragraph starts here")
        put(72, 5, "and continues here")
    c.save()
    return path


def test_wrapped_lines_share_a_block(tmp_path: Path) -> None:
    doc = Reader().read(draw_pdf(tmp_path, wrapped_paragraph=True))
    a, b = doc.segments[:2]
    assert a.block is not None
    assert a.block == b.block
    assert a.join_before == ""
    assert b.join_before == " "


def test_columns_are_separate_blocks(tmp_path: Path) -> None:
    doc = Reader().read(draw_pdf(tmp_path, two_columns=True))
    left = {s.block for s in doc.segments if s.metadata["x"] < 200}
    right = {s.block for s in doc.segments if s.metadata["x"] >= 300}
    assert left and right
    assert left.isdisjoint(right)


def test_blank_line_starts_a_new_block(tmp_path: Path) -> None:
    doc = Reader().read(draw_pdf(tmp_path, two_paragraphs=True))
    assert doc.segments[0].block != doc.segments[-1].block
    assert doc.segments[0].block == doc.segments[1].block


def test_wrapped_name_is_detected_across_the_break(tmp_path: Path) -> None:
    doc = Reader().read(draw_pdf(tmp_path, wrapped_paragraph=True))
    name = "Evelyn Marchetti"

    def analyze(text: str) -> list[DetectionResult]:
        i = text.find(name)
        if i < 0:
            return []
        return [
            DetectionResult(
                entity_type="PERSON", start=i, end=i + len(name), score=0.9, text_span=name
            )
        ]

    findings = detect_blocks(doc.segments, analyze)
    assert len(findings) == 1
    assert [p.text for p in findings[0].pieces] == ["Evelyn", "Marchetti"]


def test_writer_draws_nothing_for_an_empty_tail_piece(tmp_path: Path) -> None:
    src = draw_pdf(tmp_path, wrapped_paragraph=True)
    doc = Reader().read(src)
    first, second = doc.segments[:2]
    first.text = first.text.replace("Evelyn", "<PERSON>")
    second.text = second.text.replace("Marchetti ", "")
    extraction = _extract_file(src)
    edits = plan_edits(doc.segments, extraction)
    out = flatten(extraction, edits)
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(out)).pages)
    assert "Marchetti" not in text
    assert "Evelyn" not in text
    assert "<PERSON>" in text
