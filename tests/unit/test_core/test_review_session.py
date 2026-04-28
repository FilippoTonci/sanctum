"""State-machine invariants for ``ReviewSession`` mutations."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sanctum.core.exceptions import (
    ReviewSessionAlreadyCommittedError,
    ReviewSessionInvalidDecisionError,
)
from sanctum.core.models import (
    ProposalDecision,
    ReviewProposal,
    ReviewSession,
    TextSegment,
    UserAddedDecision,
)
from sanctum.core.review import session as session_fn


def _session(**overrides: object) -> ReviewSession:
    defaults: dict[str, object] = {
        "id": "sess-test",
        "source_path": Path("/tmp/input.docx"),
        "format": "docx",
        "default_operator": "replace",
        "segments": [TextSegment(id="body/p0/r0", text="Alice went home")],
        "proposals": [
            ReviewProposal(
                detection_id="abcdef012345",
                entity_type="PERSON",
                score=0.9,
                original="Alice",
                start=0,
                end=5,
            )
        ],
        "created_at": datetime(2026, 4, 24, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return ReviewSession(**defaults)  # type: ignore[arg-type]


class TestAddDecision:
    def test_appends_proposal_decision(self) -> None:
        session = _session()
        decision = ProposalDecision(proposal_id="abcdef012345", status="accept")
        session_fn.add_decision(session, decision)
        assert session.decisions == [decision]

    def test_replaces_prior_proposal_decision_on_same_id(self) -> None:
        session = _session()
        first = ProposalDecision(proposal_id="abcdef012345", status="accept")
        second = ProposalDecision(proposal_id="abcdef012345", status="reject")
        session_fn.add_decision(session, first)
        session_fn.add_decision(session, second)
        assert session.decisions == [second]

    def test_user_added_always_appends(self) -> None:
        session = _session()
        a = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="PERSON",
            original="Bob",
            start=0,
            end=3,
        )
        b = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="PERSON",
            original="Chris",
            start=4,
            end=9,
        )
        session_fn.add_decision(session, a)
        session_fn.add_decision(session, b)
        assert session.decisions == [a, b]

    def test_rejects_unknown_proposal_id(self) -> None:
        session = _session()
        with pytest.raises(ReviewSessionInvalidDecisionError, match="not found"):
            session_fn.add_decision(
                session,
                ProposalDecision(proposal_id="missing000000", status="accept"),
            )

    def test_rejects_mutation_after_commit(self) -> None:
        session = _session()
        session_fn.commit(session, datetime(2026, 4, 24, 12, tzinfo=timezone.utc))
        with pytest.raises(ReviewSessionAlreadyCommittedError):
            session_fn.add_decision(
                session,
                ProposalDecision(proposal_id="abcdef012345", status="accept"),
            )

    def test_rejects_mutation_after_abandon(self) -> None:
        session = _session()
        session_fn.abandon(session)
        with pytest.raises(ReviewSessionAlreadyCommittedError):
            session_fn.add_decision(
                session,
                ProposalDecision(proposal_id="abcdef012345", status="accept"),
            )


def _proposal(
    detection_id: str,
    *,
    segment_anchor: str = "body/p0/r0",
    start: int,
    end: int,
    entity_type: str = "PERSON",
    original: str = "x",
) -> ReviewProposal:
    return ReviewProposal(
        detection_id=detection_id,
        entity_type=entity_type,
        score=0.9,
        original=original,
        segment_anchor=segment_anchor,
        start=start,
        end=end,
    )


class TestApplyUserAddedWithOverlapPurge:
    """Sanctum#31 — UA spans subsume overlapping model detections.

    The reviewer's span wins outright: any model proposal whose char
    range overlaps the UA on the same segment is dropped, along with
    any decision attached to it. No bookkeeping for restoration —
    deleting the UA later does not bring the proposals back.
    """

    def _seg_session(self, proposals: list[ReviewProposal]) -> ReviewSession:
        return _session(
            segments=[
                TextSegment(
                    id="body/p0/r0",
                    text="Party A: Rachel Moore on behalf of Miller, Henderson and Johnson",
                )
            ],
            proposals=proposals,
        )

    def test_purges_fully_contained_proposals(self) -> None:
        session = self._seg_session(
            proposals=[
                _proposal("p_miller", start=35, end=41, original="Miller"),
                _proposal("p_henderson", start=43, end=52, original="Henderson"),
                _proposal("p_johnson", start=57, end=64, original="Johnson"),
            ]
        )
        ua = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="ORGANIZATION",
            original="Miller, Henderson and Johnson",
            start=35,
            end=64,
        )
        removed = session_fn.apply_user_added_with_overlap_purge(session, ua)
        assert sorted(removed) == ["p_henderson", "p_johnson", "p_miller"]
        assert session.proposals == []
        assert session.decisions == [ua]

    def test_purges_partially_overlapping_proposal(self) -> None:
        session = self._seg_session(proposals=[_proposal("p_partial", start=30, end=40)])
        ua = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="ORGANIZATION",
            original="x",
            start=35,
            end=50,
        )
        removed = session_fn.apply_user_added_with_overlap_purge(session, ua)
        assert removed == ["p_partial"]
        assert session.proposals == []

    def test_leaves_adjacent_proposal_at_right_edge(self) -> None:
        # proposal ends exactly where UA starts — no shared characters.
        session = self._seg_session(proposals=[_proposal("p_left", start=30, end=35)])
        ua = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="ORGANIZATION",
            original="x",
            start=35,
            end=50,
        )
        removed = session_fn.apply_user_added_with_overlap_purge(session, ua)
        assert removed == []
        assert [p.detection_id for p in session.proposals] == ["p_left"]

    def test_leaves_adjacent_proposal_at_left_edge(self) -> None:
        # proposal starts exactly where UA ends.
        session = self._seg_session(proposals=[_proposal("p_right", start=50, end=55)])
        ua = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="ORGANIZATION",
            original="x",
            start=35,
            end=50,
        )
        removed = session_fn.apply_user_added_with_overlap_purge(session, ua)
        assert removed == []
        assert [p.detection_id for p in session.proposals] == ["p_right"]

    def test_leaves_proposal_on_different_segment_anchor(self) -> None:
        session = _session(
            segments=[
                TextSegment(id="body/p0/r0", text="line one"),
                TextSegment(id="body/p1/r0", text="line two"),
            ],
            proposals=[_proposal("p_other", segment_anchor="body/p1/r0", start=35, end=45)],
        )
        ua = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="ORGANIZATION",
            original="x",
            start=30,
            end=50,
        )
        removed = session_fn.apply_user_added_with_overlap_purge(session, ua)
        assert removed == []
        assert [p.detection_id for p in session.proposals] == ["p_other"]

    def test_purges_orphan_decision_on_overlapped_proposal(self) -> None:
        # An accept decision on a now-overlapped proposal would otherwise
        # be a dangling reference. Drop the decision alongside the proposal.
        session = self._seg_session(proposals=[_proposal("p_accepted", start=35, end=41)])
        prior = ProposalDecision(proposal_id="p_accepted", status="accept")
        session_fn.add_decision(session, prior)
        assert session.decisions == [prior]

        ua = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="ORGANIZATION",
            original="x",
            start=30,
            end=50,
        )
        removed = session_fn.apply_user_added_with_overlap_purge(session, ua)
        assert removed == ["p_accepted"]
        assert session.proposals == []
        # only the UA survives; the orphaned ProposalDecision is gone.
        assert session.decisions == [ua]

    def test_no_overlap_appends_ua_and_returns_empty(self) -> None:
        session = self._seg_session(proposals=[_proposal("p_kept", start=0, end=8)])
        ua = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="ORGANIZATION",
            original="x",
            start=20,
            end=30,
        )
        removed = session_fn.apply_user_added_with_overlap_purge(session, ua)
        assert removed == []
        assert [p.detection_id for p in session.proposals] == ["p_kept"]
        assert session.decisions == [ua]

    def test_rejects_after_commit(self) -> None:
        session = self._seg_session(proposals=[])
        session_fn.commit(session, datetime(2026, 4, 24, 12, tzinfo=timezone.utc))
        ua = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="ORGANIZATION",
            original="x",
            start=0,
            end=5,
        )
        with pytest.raises(ReviewSessionAlreadyCommittedError):
            session_fn.apply_user_added_with_overlap_purge(session, ua)


class TestCommit:
    def test_transitions_status_and_stamps_committed_at(self) -> None:
        session = _session()
        now = datetime(2026, 4, 24, 12, tzinfo=timezone.utc)
        session_fn.commit(session, now)
        assert session.status == "committed"
        assert session.committed_at == now

    def test_rejects_double_commit(self) -> None:
        session = _session()
        now = datetime(2026, 4, 24, 12, tzinfo=timezone.utc)
        session_fn.commit(session, now)
        with pytest.raises(ReviewSessionAlreadyCommittedError):
            session_fn.commit(session, now)

    def test_rejects_commit_after_abandon(self) -> None:
        session = _session()
        session_fn.abandon(session)
        with pytest.raises(ReviewSessionAlreadyCommittedError):
            session_fn.commit(session, datetime(2026, 4, 24, tzinfo=timezone.utc))


class TestAbandon:
    def test_transitions_status(self) -> None:
        session = _session()
        session_fn.abandon(session)
        assert session.status == "abandoned"
        assert session.committed_at is None

    def test_rejects_abandon_after_commit(self) -> None:
        session = _session()
        session_fn.commit(session, datetime(2026, 4, 24, tzinfo=timezone.utc))
        with pytest.raises(ReviewSessionAlreadyCommittedError):
            session_fn.abandon(session)
