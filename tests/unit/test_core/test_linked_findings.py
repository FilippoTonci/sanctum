"""Linked findings: a finding split across segments is decided and rendered as one."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.core.blocks import BlockFinding, Piece
from sanctum.core.engine import _apply_decisions_to_segments, _originals_to_verify
from sanctum.core.models import (
    ProposalDecision,
    ReviewProposal,
    ReviewSession,
    TextSegment,
    UserAddedDecision,
)
from sanctum.core.review.identifiers import make_detection_id
from sanctum.core.review.previews import compute_preview
from sanctum.core.review.proposals import build_proposals_from_findings
from sanctum.core.review.session import add_decision, apply_user_added_with_overlap_purge

SPLIT = BlockFinding(
    "PERSON",
    0.9,
    "Jennifer Martin",
    (Piece("p0/r0", 0, 8, "Jennifer"), Piece("p0/r1", 0, 1, " "), Piece("p0/r2", 0, 6, "Martin")),
)
SOLO = BlockFinding("PERSON", 0.8, "Cameron", (Piece("p1/r0", 4, 11, "Cameron"),))


@pytest.fixture()
def make_session() -> Callable[[list[ReviewProposal]], ReviewSession]:
    def make(proposals: list[ReviewProposal]) -> ReviewSession:
        return ReviewSession(
            id="sess-linked",
            source_path=Path("/tmp/input.docx"),
            format="docx",
            default_operator="replace",
            segments=[
                TextSegment(id="p0/r0", text="Jennifer", block="p0"),
                TextSegment(id="p0/r1", text=" ", block="p0"),
                TextSegment(id="p0/r2", text="Martin", block="p0"),
                TextSegment(id="p1/r0", text="Hi, Cameron", block="p1"),
            ],
            proposals=proposals,
            created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )

    return make


@pytest.fixture(scope="module")
def anonymizer() -> PresidioAnonymizer:
    return PresidioAnonymizer()


def test_single_piece_findings_keep_todays_ids() -> None:
    [p] = build_proposals_from_findings([SOLO])
    assert p.detection_id == make_detection_id("PERSON", "Cameron", "p1/r0:4")
    assert p.group_id is None


def test_split_finding_becomes_linked_pieces() -> None:
    ps = build_proposals_from_findings([SPLIT])
    assert [p.group_index for p in ps] == [0, 1, 2]
    assert len({p.group_id for p in ps}) == 1 and ps[0].group_id is not None
    assert all(p.group_original == "Jennifer Martin" for p in ps)
    assert len({p.detection_id for p in ps}) == 3


def test_deciding_one_piece_decides_the_whole_group(
    make_session: Callable[[list[ReviewProposal]], ReviewSession],
) -> None:
    session = make_session(build_proposals_from_findings([SPLIT, SOLO]))
    piece = session.proposals[1]
    add_decision(session, ProposalDecision(proposal_id=piece.detection_id, status="reject"))
    statuses = {d.proposal_id: d.status for d in session.decisions}
    assert statuses == {p.detection_id: "reject" for p in session.proposals[:3]}


def test_redeciding_a_group_replaces_every_piece_decision(
    make_session: Callable[[list[ReviewProposal]], ReviewSession],
) -> None:
    session = make_session(build_proposals_from_findings([SPLIT, SOLO]))
    head, _, tail = session.proposals[:3]
    add_decision(session, ProposalDecision(proposal_id=head.detection_id, status="reject"))
    add_decision(
        session,
        ProposalDecision(proposal_id=tail.detection_id, status="accept", custom_replacement="J."),
    )
    assert len(session.decisions) == 3
    assert all(isinstance(d, ProposalDecision) and d.status == "accept" for d in session.decisions)
    assert all(
        isinstance(d, ProposalDecision) and d.custom_replacement == "J." for d in session.decisions
    )


def test_user_added_over_the_tail_keeps_the_head_decided(
    make_session: Callable[[list[ReviewProposal]], ReviewSession],
) -> None:
    # only the overlapped piece goes; the rest of the group keeps
    # its decision. The whitespace-only piece carries no PII and goes too.
    session = make_session(build_proposals_from_findings([SPLIT, SOLO]))
    head, space, tail = session.proposals[:3]
    add_decision(session, ProposalDecision(proposal_id=head.detection_id, status="accept"))
    ua = UserAddedDecision(
        segment_anchor="p0/r2", entity_type="PERSON", original="Martin", start=0, end=6
    )
    removed = apply_user_added_with_overlap_purge(session, ua)
    assert sorted(removed) == sorted([space.detection_id, tail.detection_id])
    assert [p.original for p in session.proposals] == ["Jennifer", "Cameron"]
    assert session.proposals[0].group_index == 0
    statuses = {
        d.proposal_id: d.status for d in session.decisions if isinstance(d, ProposalDecision)
    }
    assert statuses == {head.detection_id: "accept"}
    assert session.decisions[-1] == ua


def test_user_added_over_the_head_promotes_the_rest_of_the_group(
    anonymizer: PresidioAnonymizer,
) -> None:
    # C1: "Dear Jennifer" + " Martin, thanks." accepted as one group, then the
    # user hand-marks "Jennifer". "Martin" must still be replaced and checked.
    finding = BlockFinding(
        "PERSON",
        0.9,
        "Jennifer Martin",
        (Piece("p0/r0", 5, 13, "Jennifer"), Piece("p0/r1", 0, 7, " Martin")),
    )
    session = ReviewSession(
        id="sess-c1",
        source_path=Path("/tmp/input.docx"),
        format="docx",
        default_operator="replace",
        segments=[
            TextSegment(id="p0/r0", text="Dear Jennifer", block="p0"),
            TextSegment(id="p0/r1", text=" Martin, thanks.", block="p0"),
        ],
        proposals=build_proposals_from_findings([finding]),
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    head, tail = session.proposals
    add_decision(session, ProposalDecision(proposal_id=head.detection_id, status="accept"))
    ua = UserAddedDecision(
        segment_anchor="p0/r0", entity_type="PERSON", original="Jennifer", start=5, end=13
    )
    removed = apply_user_added_with_overlap_purge(session, ua)
    assert removed == [head.detection_id]
    [survivor] = session.proposals
    assert survivor.detection_id == tail.detection_id
    assert survivor.group_index == 0
    assert (survivor.original, survivor.start, survivor.end) == ("Martin", 1, 7)

    segments = _apply_decisions_to_segments(session, session.segments, anonymizer)
    out = "".join(s.text for s in segments)
    assert "Martin" not in out
    assert "Jennifer" not in out
    assert out == "Dear <PERSON> <PERSON>, thanks."
    originals = _originals_to_verify(session, segments)
    assert "Martin" in originals
    assert "Jennifer Martin" in originals


def test_head_preview_renders_the_whole_finding_and_tail_renders_empty(
    anonymizer: PresidioAnonymizer,
) -> None:
    head, middle, tail = build_proposals_from_findings([SPLIT])
    kwargs = {"operator_params": None, "custom_replacement": None, "anonymizer": anonymizer}
    assert compute_preview(head, "replace", **kwargs) == "<PERSON>"  # type: ignore[arg-type]
    assert compute_preview(middle, "replace", **kwargs) == ""  # type: ignore[arg-type]
    assert compute_preview(tail, "replace", **kwargs) == ""  # type: ignore[arg-type]


def test_head_preview_sees_the_group_original(anonymizer: PresidioAnonymizer) -> None:
    head = build_proposals_from_findings([SPLIT])[0]
    preview = compute_preview(
        head,
        "mask",
        {"masking_char": "*", "chars_to_mask": 100, "from_end": False},
        None,
        anonymizer,
    )
    assert preview == "*" * len("Jennifer Martin")
