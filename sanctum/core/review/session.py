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
from sanctum.core.models import (
    ProposalDecision,
    ReviewProposal,
    ReviewSession,
    SessionDecision,
    UserAddedDecision,
)


def _require_open(session: ReviewSession) -> None:
    if session.status != "open":
        raise ReviewSessionAlreadyCommittedError(
            f"Session {session.id!r} is {session.status}; cannot mutate."
        )


def add_decision(session: ReviewSession, decision: SessionDecision) -> None:
    """Append or replace a decision on the session.

    A ``ProposalDecision`` whose ``proposal_id`` already carries a decision
    replaces the earlier one. A decision on any piece of a linked finding
    (``ReviewProposal.group_id``) is recorded for every piece of the group,
    so the pieces are always decided together. ``UserAddedDecision`` always appends — user-
    added spans are addressed by identity, not id, so there's no natural
    overwrite key.
    """
    _require_open(session)
    if isinstance(decision, ProposalDecision):
        by_id = {p.detection_id: p for p in session.proposals}
        target = by_id.get(decision.proposal_id)
        if target is None:
            raise ReviewSessionInvalidDecisionError(
                f"Proposal id {decision.proposal_id!r} not found in session {session.id!r}."
            )
        member_ids = (
            [p.detection_id for p in session.proposals if p.group_id == target.group_id]
            if target.group_id is not None
            else [target.detection_id]
        )
        members = set(member_ids)
        session.decisions = [
            d
            for d in session.decisions
            if not (isinstance(d, ProposalDecision) and d.proposal_id in members)
        ]
        session.decisions.extend(
            decision.model_copy(update={"proposal_id": pid}) for pid in member_ids
        )
        return
    session.decisions.append(decision)


def apply_user_added_with_overlap_purge(session: ReviewSession, ua: UserAddedDecision) -> list[str]:
    """Append a user-added decision; drop model proposals it overlaps.

    The reviewer's span is treated as authoritative: any
    ``ReviewProposal`` on the same ``segment_anchor`` whose half-open
    range ``[p.start, p.end)`` overlaps ``[ua.start, ua.end)`` is removed
    from ``session.proposals``, along with any ``ProposalDecision`` that
    referenced it. Adjacent ranges (``p.end == ua.start`` or
    ``p.start == ua.end``) share no characters and are left alone. When an
    overlapped proposal is a piece of a linked finding, every piece of that
    finding is removed with it.

    No record of the removed proposals is kept — the user-added
    decision is the new source of truth for that span. Removing the UA
    later (``DELETE …/decisions/user-added/{ua_id}``) does *not*
    resurrect the purged proposals; treat this as one-way.

    Returns the list of removed ``detection_id``s so the route can echo
    them in the response and the desktop can update without refetching.
    """
    _require_open(session)

    overlapped = [
        p
        for p in session.proposals
        if p.segment_anchor == ua.segment_anchor and p.start < ua.end and p.end > ua.start
    ]
    hit_ids = {p.detection_id for p in overlapped}
    hit_groups = {p.group_id for p in overlapped if p.group_id is not None}
    removed: list[str] = []
    survivors: list[ReviewProposal] = []
    for p in session.proposals:
        if p.detection_id in hit_ids or (p.group_id is not None and p.group_id in hit_groups):
            removed.append(p.detection_id)
        else:
            survivors.append(p)

    if removed:
        session.proposals = survivors
        removed_ids = set(removed)
        session.decisions = [
            d
            for d in session.decisions
            if not (isinstance(d, ProposalDecision) and d.proposal_id in removed_ids)
        ]
    session.decisions.append(ua)
    return removed


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
