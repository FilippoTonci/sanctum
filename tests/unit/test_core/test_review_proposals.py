"""Tests for the review proposal builder."""

from __future__ import annotations

from pathlib import Path

import pytest
from sanctum.core.exceptions import ReviewError
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    StructuredDocument,
    TextSegment,
)
from sanctum.core.review.proposals import build_proposals


def _document(segments: list[TextSegment]) -> StructuredDocument:
    return StructuredDocument(
        source_path=Path("/tmp/input.docx"),
        format="docx",
        segments=segments,
    )


def test_empty_document_yields_no_proposals() -> None:
    doc = _document([])
    assert build_proposals(doc, {}, default_operator="replace") == []


def test_segments_without_detections_contribute_nothing() -> None:
    doc = _document([TextSegment(id="s0", text="plain text")])
    result = AnonymizationResult(
        original_text="plain text",
        anonymized_text="plain text",
        detections=[],
        operators_applied={},
    )
    assert build_proposals(doc, {"s0": result}, default_operator="replace") == []


def test_builds_one_proposal_per_detection() -> None:
    doc = _document([TextSegment(id="s0", text="Alice met Bob")])
    detections = [
        DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice"),
        DetectionResult(entity_type="PERSON", start=10, end=13, score=0.9, text_span="Bob"),
    ]
    result = AnonymizationResult(
        original_text="Alice met Bob",
        anonymized_text="[PERSON_1] met [PERSON_2]",
        detections=detections,
        operators_applied={"PERSON": "replace"},
        per_detection_replacements=["[PERSON_1]", "[PERSON_2]"],
    )
    proposals = build_proposals(doc, {"s0": result}, default_operator="replace")
    assert [p.original for p in proposals] == ["Alice", "Bob"]
    assert [p.replacement for p in proposals] == ["[PERSON_1]", "[PERSON_2]"]
    assert all(p.segment_anchor == "s0" for p in proposals)


def test_detection_ids_are_stable_across_calls() -> None:
    doc = _document([TextSegment(id="s0", text="Alice")])
    result = AnonymizationResult(
        original_text="Alice",
        anonymized_text="[PERSON_1]",
        detections=[
            DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        ],
        operators_applied={"PERSON": "replace"},
        per_detection_replacements=["[PERSON_1]"],
    )
    a = build_proposals(doc, {"s0": result}, default_operator="replace")
    b = build_proposals(doc, {"s0": result}, default_operator="replace")
    assert a[0].detection_id == b[0].detection_id


def test_detection_ids_differ_across_segments() -> None:
    """Same text in different segments must produce different ids."""
    doc = _document(
        [
            TextSegment(id="s0", text="Alice"),
            TextSegment(id="s1", text="Alice"),
        ]
    )
    result = AnonymizationResult(
        original_text="Alice",
        anonymized_text="[PERSON_1]",
        detections=[
            DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        ],
        operators_applied={"PERSON": "replace"},
        per_detection_replacements=["[PERSON_1]"],
    )
    proposals = build_proposals(doc, {"s0": result, "s1": result}, default_operator="replace")
    assert proposals[0].detection_id != proposals[1].detection_id


def test_missing_per_detection_replacements_raises() -> None:
    doc = _document([TextSegment(id="s0", text="Alice")])
    result = AnonymizationResult(
        original_text="Alice",
        anonymized_text="[PERSON]",
        detections=[
            DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        ],
        operators_applied={"PERSON": "replace"},
        per_detection_replacements=None,
    )
    with pytest.raises(ReviewError, match="per-detection replacements"):
        build_proposals(doc, {"s0": result}, default_operator="replace")


def test_uses_per_entity_operator_when_present() -> None:
    doc = _document([TextSegment(id="s0", text="John")])
    result = AnonymizationResult(
        original_text="John",
        anonymized_text="Sarah",
        detections=[
            DetectionResult(entity_type="PERSON", start=0, end=4, score=0.9, text_span="John")
        ],
        operators_applied={"PERSON": "hips"},
        per_detection_replacements=["Sarah"],
    )
    proposals = build_proposals(doc, {"s0": result}, default_operator="replace")
    assert proposals[0].operator == "hips"


def test_falls_back_to_default_operator_when_entity_missing() -> None:
    doc = _document([TextSegment(id="s0", text="John")])
    result = AnonymizationResult(
        original_text="John",
        anonymized_text="[PERSON]",
        detections=[
            DetectionResult(entity_type="PERSON", start=0, end=4, score=0.9, text_span="John")
        ],
        operators_applied={},
        per_detection_replacements=["[PERSON]"],
    )
    proposals = build_proposals(doc, {"s0": result}, default_operator="replace")
    assert proposals[0].operator == "replace"
