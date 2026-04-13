"""Unit tests for ``SanctumEngine.process_document``.

Exercises the core orchestrator against in-memory mock reader/writer
implementations. Real adapter behaviour is covered under the per-format
test files.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import DocumentError
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    StructuredDocument,
    TextSegment,
)


def _segment(seg_id: str, text: str) -> TextSegment:
    return TextSegment(id=seg_id, text=text)


@pytest.fixture()
def mock_reader():
    return Mock()


@pytest.fixture()
def mock_writer():
    return Mock()


def _detection(text_span: str) -> DetectionResult:
    return DetectionResult(
        entity_type="PERSON",
        start=0,
        end=len(text_span),
        score=0.9,
        text_span=text_span,
    )


def _anon_result(original: str, anonymized: str) -> AnonymizationResult:
    return AnonymizationResult(
        original_text=original,
        anonymized_text=anonymized,
        detections=[_detection(original)],
        operators_applied={"PERSON": "replace"},
    )


def test_process_document_skips_empty_segments(mock_reader, mock_writer):
    analyzer = Mock()
    analyzer.analyze.return_value = []
    anonymizer = Mock()

    mock_reader.read.return_value = StructuredDocument(
        source_path=Path("in.docx"),
        format="docx",
        segments=[_segment("body/p0/r0", "   "), _segment("body/p1/r0", "")],
    )
    engine = SanctumEngine(analyzer=analyzer, anonymizer=anonymizer)

    results = engine.process_document(mock_reader, mock_writer, Path("in.docx"), Path("out.docx"))

    assert results == []
    analyzer.analyze.assert_not_called()
    anonymizer.anonymize.assert_not_called()
    mock_writer.write.assert_called_once()


def test_process_document_anonymizes_each_segment(mock_reader, mock_writer):
    analyzer = Mock()
    analyzer.analyze.side_effect = [[_detection("Alice")], [_detection("Bob")]]
    anonymizer = Mock()
    anonymizer.anonymize.side_effect = [
        _anon_result("Alice signed", "<PERSON> signed"),
        _anon_result("Bob witnessed", "<PERSON> witnessed"),
    ]
    mock_reader.read.return_value = StructuredDocument(
        source_path=Path("in.docx"),
        format="docx",
        segments=[
            _segment("body/p0/r0", "Alice signed"),
            _segment("body/p1/r0", "Bob witnessed"),
        ],
    )
    engine = SanctumEngine(analyzer=analyzer, anonymizer=anonymizer)

    results = engine.process_document(mock_reader, mock_writer, Path("in.docx"), Path("out.docx"))

    assert len(results) == 2
    written_doc, _ = mock_writer.write.call_args.args
    assert [s.text for s in written_doc.segments] == [
        "<PERSON> signed",
        "<PERSON> witnessed",
    ]


def test_process_document_preserves_segments_with_no_detections(mock_reader, mock_writer):
    analyzer = Mock()
    analyzer.analyze.return_value = []
    anonymizer = Mock()
    mock_reader.read.return_value = StructuredDocument(
        source_path=Path("in.xlsx"),
        format="xlsx",
        segments=[_segment("sheet=S/A1", "just some data")],
    )
    engine = SanctumEngine(analyzer=analyzer, anonymizer=anonymizer)

    results = engine.process_document(mock_reader, mock_writer, Path("in.xlsx"), Path("out.xlsx"))

    assert results == []
    anonymizer.anonymize.assert_not_called()
    written_doc, _ = mock_writer.write.call_args.args
    assert written_doc.segments[0].text == "just some data"


def test_process_document_wraps_read_failure(mock_reader, mock_writer):
    mock_reader.read.side_effect = OSError("disk on fire")
    engine = SanctumEngine(analyzer=Mock(), anonymizer=Mock())

    with pytest.raises(DocumentError, match="Failed to read"):
        engine.process_document(mock_reader, mock_writer, Path("in.docx"), Path("out.docx"))
    mock_writer.write.assert_not_called()


def test_process_document_wraps_write_failure(mock_reader, mock_writer):
    analyzer = Mock()
    analyzer.analyze.return_value = []
    mock_reader.read.return_value = StructuredDocument(
        source_path=Path("in.docx"),
        format="docx",
        segments=[_segment("body/p0/r0", "x")],
    )
    mock_writer.write.side_effect = PermissionError("read-only fs")
    engine = SanctumEngine(analyzer=analyzer, anonymizer=Mock())

    with pytest.raises(DocumentError, match="Failed to write"):
        engine.process_document(mock_reader, mock_writer, Path("in.docx"), Path("out.docx"))


def test_process_document_preserves_raw_handle(mock_reader, mock_writer):
    """The writer must see the same raw_handle the reader emitted."""
    analyzer = Mock()
    analyzer.analyze.return_value = []
    doc = StructuredDocument(
        source_path=Path("in.docx"),
        format="docx",
        segments=[_segment("body/p0/r0", "hi")],
    )
    handle = object()
    doc.raw_handle = handle
    mock_reader.read.return_value = doc
    engine = SanctumEngine(analyzer=analyzer, anonymizer=Mock())

    engine.process_document(mock_reader, mock_writer, Path("in.docx"), Path("out.docx"))
    written_doc, _ = mock_writer.write.call_args.args
    assert written_doc.raw_handle is handle
