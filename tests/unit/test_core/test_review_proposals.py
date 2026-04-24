"""Tests for the Flow B review proposal builder."""

from __future__ import annotations

from pathlib import Path

from sanctum.core.models import DetectionResult, StructuredDocument, TextSegment
from sanctum.core.review.proposals import build_proposals


def _document(segments: list[TextSegment]) -> StructuredDocument:
    return StructuredDocument(
        source_path=Path("/tmp/input.docx"),
        format="docx",
        segments=segments,
    )


def test_empty_document_yields_no_proposals() -> None:
    doc = _document([])
    assert build_proposals(doc, {}) == []


def test_segments_without_detections_contribute_nothing() -> None:
    doc = _document([TextSegment(id="s0", text="plain text")])
    assert build_proposals(doc, {"s0": []}) == []


def test_segments_missing_from_detections_dict_contribute_nothing() -> None:
    """A segment with no key in detections_by_segment is treated as zero detections."""
    doc = _document([TextSegment(id="s0", text="plain text")])
    assert build_proposals(doc, {}) == []


def test_builds_one_proposal_per_detection() -> None:
    doc = _document([TextSegment(id="s0", text="Alice met Bob")])
    detections = [
        DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice"),
        DetectionResult(entity_type="PERSON", start=10, end=13, score=0.9, text_span="Bob"),
    ]
    proposals = build_proposals(doc, {"s0": detections})
    assert [p.original for p in proposals] == ["Alice", "Bob"]
    assert all(p.segment_anchor == "s0" for p in proposals)
    assert [p.entity_type for p in proposals] == ["PERSON", "PERSON"]


def test_proposals_carry_no_replacement_or_operator() -> None:
    """Flow B proposals are pure detections — replacement/operator live on decisions."""
    doc = _document([TextSegment(id="s0", text="Alice")])
    detections = [
        DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
    ]
    proposal = build_proposals(doc, {"s0": detections})[0]
    assert not hasattr(proposal, "replacement")
    assert not hasattr(proposal, "operator")


def test_detection_ids_are_stable_across_calls() -> None:
    doc = _document([TextSegment(id="s0", text="Alice")])
    detections = [
        DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
    ]
    a = build_proposals(doc, {"s0": detections})
    b = build_proposals(doc, {"s0": detections})
    assert a[0].detection_id == b[0].detection_id


def test_detection_ids_differ_across_segments() -> None:
    """Same detection in different segments must produce different ids."""
    doc = _document(
        [
            TextSegment(id="s0", text="Alice"),
            TextSegment(id="s1", text="Alice"),
        ]
    )
    detections = [
        DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
    ]
    proposals = build_proposals(doc, {"s0": detections, "s1": detections})
    assert proposals[0].detection_id != proposals[1].detection_id


def test_preserves_segment_and_detection_order() -> None:
    doc = _document(
        [
            TextSegment(id="s0", text="Alice met Bob"),
            TextSegment(id="s1", text="later, Carol arrived"),
        ]
    )
    s0_detections = [
        DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice"),
        DetectionResult(entity_type="PERSON", start=10, end=13, score=0.9, text_span="Bob"),
    ]
    s1_detections = [
        DetectionResult(entity_type="PERSON", start=7, end=12, score=0.9, text_span="Carol"),
    ]
    proposals = build_proposals(doc, {"s0": s0_detections, "s1": s1_detections})
    assert [p.original for p in proposals] == ["Alice", "Bob", "Carol"]
    assert [p.segment_anchor for p in proposals] == ["s0", "s0", "s1"]
