"""Build ``ReviewProposal`` lists from analyzer output (Flow B).

Flow B analyzes pre-review and anonymizes at commit, so the proposal
builder doesn't consume anonymization results — it takes bare
``DetectionResult`` lists keyed by segment id. Replacements come from
``sanctum.core.review.previews.compute_preview`` in the UI path and
from the anonymizer in the commit path.

Stable proposal ids come from ``make_detection_id`` — the same hash the
WS5 comment-trailer export uses, so a document reviewed through the UI
and later exported to Word carries matching ids on both sides.
"""

from __future__ import annotations

from sanctum.core.models import DetectionResult, ReviewProposal, StructuredDocument
from sanctum.core.review.identifiers import make_detection_id


def build_proposals(
    document: StructuredDocument,
    detections_by_segment: dict[str, list[DetectionResult]],
) -> list[ReviewProposal]:
    """Collapse per-segment detections into session proposals.

    Preserves segment order and within-segment detection order. Segments
    without detections contribute nothing. Proposals carry no
    replacement or operator — those become decisions on the session.
    """
    proposals: list[ReviewProposal] = []
    for segment in document.segments:
        detections = detections_by_segment.get(segment.id) or []
        for det in detections:
            proposals.append(
                ReviewProposal(
                    detection_id=make_detection_id(
                        det.entity_type,
                        det.text_span,
                        f"{segment.id}:{det.start}",
                    ),
                    entity_type=det.entity_type,
                    score=det.score,
                    original=det.text_span,
                    segment_anchor=segment.id,
                    start=det.start,
                    end=det.end,
                )
            )
    return proposals
