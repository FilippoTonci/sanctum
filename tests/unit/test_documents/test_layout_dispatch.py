from pathlib import Path

import pytest
from sanctum.api.schemas import ReviewSessionLayoutResponse
from sanctum.core.exceptions import UnsupportedDocumentFormatError
from sanctum.documents.layout import build_layout, supports_layout

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_pdf_and_pptx_have_layout_builders() -> None:
    assert supports_layout("pdf")
    assert supports_layout("pptx")
    assert not supports_layout("docx")


def test_pdf_layout_from_bytes_validates_against_the_shared_schema(sample_pdf_path: Path) -> None:
    payload = build_layout("pdf", sample_pdf_path.read_bytes())
    parsed = ReviewSessionLayoutResponse.model_validate(payload)
    assert parsed.format == "pdf"
    first = parsed.pages[0].items[0]
    assert first.kind == "textline"
    assert first.segment_id == "page0/line0"


def test_docx_has_no_layout() -> None:
    with pytest.raises(UnsupportedDocumentFormatError):
        build_layout("docx", b"")
