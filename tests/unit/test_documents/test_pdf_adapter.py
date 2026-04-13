"""Unit tests for the .pdf reader and derivative writer."""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest
from sanctum.core.exceptions import PdfWriteRefusedError, UnsupportedPdfError
from sanctum.documents.pdf_adapter import Reader, Writer

FIXTURE = Path("tests/fixtures/office/engagement_letter.pdf")


@pytest.fixture()
def reader() -> Reader:
    return Reader()


@pytest.fixture()
def writer() -> Writer:
    return Writer()


def test_reader_emits_one_segment_per_page(reader: Reader) -> None:
    doc = reader.read(FIXTURE)
    assert doc.format == "pdf"
    assert len(doc.segments) >= 1
    assert all(seg.id.startswith("page") for seg in doc.segments)
    assert doc.raw_handle is None  # derivative writer builds from scratch


def test_reader_extracts_visible_text(reader: Reader) -> None:
    doc = reader.read(FIXTURE)
    joined = "\n".join(s.text for s in doc.segments)
    assert "Kimberly Sanchez" in joined


def test_reader_raises_on_no_text_layer(reader: Reader, tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    scanned = tmp_path / "empty.pdf"
    c = canvas.Canvas(str(scanned))
    c.showPage()  # one blank page, no text
    c.save()

    with pytest.raises(UnsupportedPdfError, match="no extractable text"):
        reader.read(scanned)


def test_writer_produces_readable_pdf(reader: Reader, writer: Writer, tmp_path: Path) -> None:
    doc = reader.read(FIXTURE)
    out = tmp_path / "derivative.pdf"
    writer.write(doc, out)
    assert out.exists() and out.stat().st_size > 0

    with pdfplumber.open(str(out)) as pdf:
        extracted = "\n".join(p.extract_text() or "" for p in pdf.pages)
    original_joined = "\n".join(s.text for s in doc.segments)
    # Pick a distinctive-ish token from the fixture and confirm it survived.
    for token in original_joined.split():
        if len(token) > 5 and token.isalpha():
            assert token in extracted
            break


def test_writer_refuses_to_overwrite_source(writer: Writer, reader: Reader) -> None:
    doc = reader.read(FIXTURE)
    with pytest.raises(PdfWriteRefusedError, match="overwrite source"):
        writer.write(doc, FIXTURE)


def test_writer_escapes_markup_chars(writer: Writer, tmp_path: Path) -> None:
    """Anonymized placeholders like ``<PERSON>`` must render literally,
    not be eaten by reportlab's mini-XML parser."""
    from sanctum.documents.structured import build_document, build_segment

    doc = build_document(
        source_path=tmp_path / "src.pdf",
        fmt="pdf",
        segments=[build_segment("page0", "Hello <PERSON> & <EMAIL>")],
        raw_handle=None,
    )
    out = tmp_path / "out.pdf"
    writer.write(doc, out)

    with pdfplumber.open(str(out)) as pdf:
        extracted = pdf.pages[0].extract_text() or ""
    assert "<PERSON>" in extracted
    assert "<EMAIL>" in extracted


def test_registry_dispatches_to_pdf_adapter() -> None:
    from sanctum.documents import adapter_for

    r, w = adapter_for(FIXTURE)
    assert isinstance(r, Reader)
    assert isinstance(w, Writer)
