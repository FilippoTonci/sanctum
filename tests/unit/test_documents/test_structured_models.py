"""Unit tests for the StructuredDocument domain models."""
from __future__ import annotations

from pathlib import Path

from sanctum.core.models import StructuredDocument, TextSegment
from sanctum.documents import build_document, build_segment


def test_text_segment_defaults_metadata_empty():
    seg = TextSegment(id="body/p0/r0", text="hello")
    assert seg.metadata == {}


def test_build_segment_packs_metadata():
    seg = build_segment("sheet=Sheet1/A1", "value", data_type="n", original_type="int")
    assert seg.id == "sheet=Sheet1/A1"
    assert seg.metadata == {"data_type": "n", "original_type": "int"}


def test_structured_document_holds_raw_handle_in_memory():
    """``raw_handle`` stays on the instance but is excluded from dumps."""
    sentinel = object()
    doc = build_document(
        source_path=Path("/tmp/fake.docx"),
        fmt="docx",
        segments=[build_segment("body/p0/r0", "hello")],
        raw_handle=sentinel,
    )
    assert doc.raw_handle is sentinel
    # Pydantic dump must exclude the handle (it's opaque, not serializable).
    dumped = doc.model_dump()
    assert "raw_handle" not in dumped
    assert dumped["format"] == "docx"


def test_structured_document_allows_arbitrary_raw_handle():
    """The handle field is Any — classes like docx.Document must fit."""

    class _Anything:
        pass

    doc = StructuredDocument(
        source_path=Path("x.docx"),
        format="docx",
        segments=[],
    )
    doc.raw_handle = _Anything()
    assert isinstance(doc.raw_handle, _Anything)
