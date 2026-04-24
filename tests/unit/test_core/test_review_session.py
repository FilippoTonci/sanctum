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
        )
        b = UserAddedDecision(
            segment_anchor="body/p0/r0",
            entity_type="PERSON",
            original="Chris",
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
