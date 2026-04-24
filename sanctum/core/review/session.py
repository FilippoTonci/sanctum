"""State-machine rules for ``ReviewSession`` mutations.

Pure, stateless functions that mutate a ``ReviewSession`` in place while
enforcing its invariants:

- Only an ``open`` session accepts decisions or transitions.
- ``commit`` and ``abandon`` are one-shot; a second call raises.
- ``ProposalDecision.proposal_id`` must match an existing proposal.
- Decisions on the same proposal id are replaced (last-write-wins), so a
  PATCH can be sent without the caller knowing prior state.
"""

from __future__ import annotations

from datetime import datetime

from sanctum.core.exceptions import (
    ReviewSessionAlreadyCommittedError,
    ReviewSessionInvalidDecisionError,
)
from sanctum.core.models import ProposalDecision, ReviewSession, SessionDecision


def _require_open(session: ReviewSession) -> None:
    if session.status != "open":
        raise ReviewSessionAlreadyCommittedError(
            f"Session {session.id!r} is {session.status}; cannot mutate."
        )


def add_decision(session: ReviewSession, decision: SessionDecision) -> None:
    """Append or replace a decision on the session.

    A ``ProposalDecision`` whose ``proposal_id`` already carries a decision
    replaces the earlier one. ``UserAddedDecision`` always appends — user-
    added spans are addressed by identity, not id, so there's no natural
    overwrite key.
    """
    _require_open(session)
    if isinstance(decision, ProposalDecision):
        known = {p.detection_id for p in session.proposals}
        if decision.proposal_id not in known:
            raise ReviewSessionInvalidDecisionError(
                f"Proposal id {decision.proposal_id!r} not found in session " f"{session.id!r}."
            )
        session.decisions = [
            d
            for d in session.decisions
            if not (isinstance(d, ProposalDecision) and d.proposal_id == decision.proposal_id)
        ]
    session.decisions.append(decision)


def commit(session: ReviewSession, now: datetime) -> None:
    """Transition an open session to ``committed`` and stamp the time.

    ``now`` is caller-supplied (API / CLI / tests) so there is no hidden
    dependency on wall-clock: the session store serializes what the
    caller recorded.
    """
    _require_open(session)
    session.status = "committed"
    session.committed_at = now


def abandon(session: ReviewSession) -> None:
    """Transition an open session to ``abandoned``.

    Abandoned sessions never flush staged mappings — the pseudonymize
    commit path (WS4) only runs when ``status == "committed"``.
    """
    _require_open(session)
    session.status = "abandoned"
