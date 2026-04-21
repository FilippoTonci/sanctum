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


# --- review=True path -----------------------------------------------------


class _ReviewCapableWriter:
    """Minimal class that satisfies ReviewEmittingWriter structurally.

    Uses a real class (not a MagicMock) because runtime_checkable Protocols
    distinguish MagicMock-auto-attrs from genuine method definitions
    inconsistently — the isinstance check is the load-bearing gate.
    """

    def __init__(self) -> None:
        self.write_calls: list[tuple[object, Path]] = []
        self.emit_review_calls: list[tuple[object, Path, dict]] = []

    def write(self, doc: object, path: Path) -> None:  # pragma: no cover - unused
        self.write_calls.append((doc, path))

    def emit_review(
        self,
        doc: object,
        path: Path,
        results_by_segment: dict,
    ) -> None:
        self.emit_review_calls.append((doc, path, results_by_segment))


def test_process_document_review_true_raises_on_unsupported_writer(mock_reader, mock_writer):
    engine = SanctumEngine(analyzer=Mock(), anonymizer=Mock())
    # mock_writer (bare Mock) has an auto-attribute for emit_review, so
    # isinstance against the Protocol would incorrectly succeed; use an
    # object with no emit_review at all.
    writer_without_review = object()

    with pytest.raises(NotImplementedError, match="emit_review"):
        engine.process_document(
            mock_reader,
            writer_without_review,  # type: ignore[arg-type]
            Path("in.docx"),
            Path("out.docx"),
            review=True,
        )
    mock_reader.read.assert_not_called()


def test_process_document_review_true_routes_to_emit_review(mock_reader):
    analyzer = Mock()
    analyzer.analyze.side_effect = [[_detection("Alice")]]
    anonymizer = Mock()
    anonymizer.anonymize.return_value = _anon_result("Alice signed", "<PERSON> signed")
    mock_reader.read.return_value = StructuredDocument(
        source_path=Path("in.docx"),
        format="docx",
        segments=[_segment("body/p0/r0", "Alice signed")],
    )
    writer = _ReviewCapableWriter()
    engine = SanctumEngine(analyzer=analyzer, anonymizer=anonymizer)

    results = engine.process_document(
        mock_reader, writer, Path("in.docx"), Path("out.docx"), review=True
    )

    assert len(results) == 1
    assert len(writer.emit_review_calls) == 1
    assert writer.write_calls == []
    _, _, results_by_segment = writer.emit_review_calls[0]
    assert "body/p0/r0" in results_by_segment
    assert results_by_segment["body/p0/r0"].anonymized_text == "<PERSON> signed"


def test_process_document_review_false_keeps_legacy_write_path(mock_reader, mock_writer):
    """Default review=False must not break existing callers."""
    analyzer = Mock()
    analyzer.analyze.return_value = []
    mock_reader.read.return_value = StructuredDocument(
        source_path=Path("in.docx"),
        format="docx",
        segments=[_segment("body/p0/r0", "plain text")],
    )
    engine = SanctumEngine(analyzer=analyzer, anonymizer=Mock())

    engine.process_document(
        mock_reader, mock_writer, Path("in.docx"), Path("out.docx"), review=False
    )

    mock_writer.write.assert_called_once()


# --- commit_review --------------------------------------------------------


class _ReviewParsingReader:
    """Minimal class that satisfies ReviewParsingReader structurally."""

    def read(self, path: Path) -> object:  # pragma: no cover - unused
        return object()

    def read_review_decisions(self, path: Path) -> list:
        return []


def test_commit_review_rejects_non_parsing_reader(mock_writer):
    engine = SanctumEngine(analyzer=Mock(), anonymizer=Mock())
    reader_without_parse = object()
    store = Mock()

    with pytest.raises(NotImplementedError, match="read_review_decisions"):
        engine.commit_review(
            reader_without_parse,  # type: ignore[arg-type]
            mock_writer,
            Path("review.docx"),
            Path("final.docx"),
            store,
        )


def test_commit_review_raises_ws6_placeholder_when_parser_present(mock_writer):
    engine = SanctumEngine(analyzer=Mock(), anonymizer=Mock())
    reader = _ReviewParsingReader()
    store = Mock()

    with pytest.raises(NotImplementedError, match="WS6"):
        engine.commit_review(
            reader,  # type: ignore[arg-type]
            mock_writer,
            Path("review.docx"),
            Path("final.docx"),
            store,
        )
