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

from collections.abc import Sequence

from sanctum.core.blocks import BlockFinding
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


def build_proposals_from_findings(findings: Sequence[BlockFinding]) -> list[ReviewProposal]:
    """One proposal per single-segment finding; linked pieces for split findings.

    A single-piece finding gets exactly the id ``build_proposals`` would
    have given it, so sessions and comment exports keep today's ids. A
    finding spanning several segments becomes one proposal per piece, all
    sharing a ``group_id``; the first piece (``group_index`` 0) is the head
    that renders the replacement for the whole ``group_original``.
    """
    proposals: list[ReviewProposal] = []
    for f in findings:
        if len(f.pieces) == 1:
            p = f.pieces[0]
            proposals.append(
                ReviewProposal(
                    detection_id=make_detection_id(
                        f.entity_type, p.text, f"{p.segment_id}:{p.start}"
                    ),
                    entity_type=f.entity_type,
                    score=f.score,
                    original=p.text,
                    segment_anchor=p.segment_id,
                    start=p.start,
                    end=p.end,
                )
            )
            continue
        head = f.pieces[0]
        group_id = make_detection_id(
            f.entity_type, f.original, f"group:{head.segment_id}:{head.start}"
        )
        for index, p in enumerate(f.pieces):
            proposals.append(
                ReviewProposal(
                    detection_id=make_detection_id(
                        f.entity_type, p.text, f"{p.segment_id}:{p.start}:{group_id}"
                    ),
                    entity_type=f.entity_type,
                    score=f.score,
                    original=p.text,
                    segment_anchor=p.segment_id,
                    start=p.start,
                    end=p.end,
                    group_id=group_id,
                    group_index=index,
                    group_original=f.original,
                )
            )
    return proposals
