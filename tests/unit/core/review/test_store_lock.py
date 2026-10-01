import threading
from datetime import datetime, timezone
from pathlib import Path

from sanctum.core.models import ProposalDecision, ReviewProposal, ReviewSession
from sanctum.core.review.session import add_decision
from sanctum.core.review.store import SessionStore


def _session(n: int) -> ReviewSession:
    proposals = [
        ReviewProposal(
            detection_id=f"p{i:02d}",
            entity_type="PERSON",
            score=0.9,
            original=f"Name{i}",
            segment_anchor=f"body/p{i}/r0",
            start=0,
            end=5,
        )
        for i in range(n)
    ]
    return ReviewSession(
        id="s1",
        source_path=Path("in.docx"),
        format="docx",
        default_operator="replace",
        segments=[],
        proposals=proposals,
        created_at=datetime.now(timezone.utc),
    )


def test_concurrent_load_mutate_save_loses_nothing(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    store.save(_session(40), input_bytes=b"x")

    def decide(i: int) -> None:
        with SessionStore(root=tmp_path).locked("s1"):  # separate instance, same lock
            s = store.load("s1")
            add_decision(s, ProposalDecision(proposal_id=f"p{i:02d}", status="accept"))
            store.save(s)

    threads = [threading.Thread(target=decide, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(store.load("s1").decisions) == 40


def test_lock_is_reentrant(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    with store.locked("s1"), store.locked("s1"):
        pass


def test_save_leaves_no_temp_files(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path)
    store.save(_session(2), input_bytes=b"x")
    names = sorted(p.name for p in (tmp_path / "s1").iterdir())
    assert names == ["input.docx", "manifest.json"]
    assert (tmp_path / "s1" / "manifest.json").stat().st_mode & 0o777 == 0o600
