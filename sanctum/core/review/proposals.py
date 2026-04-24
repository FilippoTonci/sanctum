"""Build ``ReviewProposal`` lists from engine output.

The proposals builder bridges the Phase 1 analysis / anonymization
pipeline and the Phase 1.5 session surface: given the parsed segments
and the per-segment ``AnonymizationResult``, it produces the flat
proposal list that lives on ``ReviewSession.proposals``.

Stable proposal ids come from ``make_detection_id`` — the same hash the
WS5 comment-trailer export uses, so a document reviewed through the UI
and later exported to Word carries matching ids on both sides.
"""

from __future__ import annotations

from sanctum.core.exceptions import ReviewError
from sanctum.core.models import (
    AnonymizationResult,
    ReviewProposal,
    StructuredDocument,
)
from sanctum.core.review.identifiers import make_detection_id


def build_proposals(
    document: StructuredDocument,
    results_by_segment: dict[str, AnonymizationResult],
    default_operator: str,
) -> list[ReviewProposal]:
    """Collapse per-segment anonymization output into session proposals.

    Preserves segment order and within-segment detection order. Segments
    without detections contribute nothing.

    Raises ``ReviewError`` if any segment's ``AnonymizationResult`` lacks
    per-detection replacements — review sessions refuse to present a
    proposal list when the adapter couldn't recover the individual
    replacement strings (e.g. merged items in Presidio's ``EngineResult``).
    """
    proposals: list[ReviewProposal] = []
    for segment in document.segments:
        result = results_by_segment.get(segment.id)
        if result is None or not result.detections:
            continue
        if result.per_detection_replacements is None:
            raise ReviewError(
                f"Segment {segment.id!r} has detections but no per-detection "
                f"replacements; review mode cannot build proposals."
            )
        for det, repl in zip(result.detections, result.per_detection_replacements, strict=True):
            op = result.operators_applied.get(det.entity_type, default_operator)
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
                    replacement=repl,
                    operator=op,
                    segment_anchor=segment.id,
                )
            )
    return proposals
