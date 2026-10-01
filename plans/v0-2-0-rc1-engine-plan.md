# v0.2.0-rc.1 Engine Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assemble the engine half of `v0.2.0-rc.1` on `release/0.2.0`: merge the pptx and pdf-engine overnight branches, then add the session lock, the leak-check refinements, block-level detection with linked findings, the any-domain email recognizer, metadata stripping for docx/pptx and the hand-marked replacement fix.

**Architecture:** Hexagonal Python engine (`sanctum/core` is pure stdlib + Pydantic; adapters in `sanctum/documents`, `sanctum/analyzer`; Flask API in `sanctum/api`). Each task is one PR from a short-lived `integrate/eN-*` branch into `release/0.2.0`. Detection moves from per-segment to per-block: adapters tag segments with a `block` key, a new core module joins each block, analyses it once and projects findings back onto segments as linked pieces.

**Tech Stack:** Python 3.11+, Pydantic v2, Presidio (analyzer/anonymizer), python-docx, python-pptx, pdfplumber, pypdfium2, pypdf, Flask, pytest, ruff, mypy.

**Spec:** `plans/v0-2-0-rc1-integration-design.md` (read it first; decisions 4–5 and steps E5–E6 were amended on 2026-10-01).

## Global Constraints

- Work in `/Users/filippo/Desktop/sanctum` (a worktree on a fresh branch per task is fine). Python: `/Users/filippo/Desktop/sanctum/.venv/bin/python`; when running from a separate worktree prefix commands with `PYTHONPATH=$PWD`.
- `sanctum/core` imports only stdlib and Pydantic. No runtime network calls anywhere. Do not add PyMuPDF/fitz (AGPL).
- Only the `replace` operator matters for product behaviour; do not break the others.
- Values that were redacted are never written to logs; error messages carry counts only.
- Gate for every task (run from the repo root, all must pass before the PR):
  - `.venv/bin/python -m pytest -q -p no:cacheprovider` (until Task 3 lands, the two tests in `tests/integration/test_serve_sigterm.py` are the only allowed failures; after Task 3, none)
  - `.venv/bin/ruff check . && .venv/bin/ruff format --check .`
  - `.venv/bin/mypy sanctum`
  - if any API request/response shape changed: `.venv/bin/python scripts/generate_openapi.py` and commit `schema/openapi.json`
- PR protocol for every task:
  1. `git fetch origin && git switch -c integrate/<task-slug> origin/release/0.2.0`
  2. do the task, committing in small steps; messages are imperative sentences ending in `(v0.2.0-rc.1 E<n>)` and the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  3. `git push -u origin integrate/<task-slug>` then `gh pr create --base release/0.2.0 --title "<E-n: title>" --body-file <file>`; the body has sections **What and why** (link the spec decision), **How it was checked** (exact commands + results), **Judgement calls** (anything decided without asking), and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
  4. stop and wait for the product owner's approval; merge with `gh pr merge --merge` only after approval
- Never push to `main`, never force-push, never merge without approval.

## Review Focus

1. **A name split across three runs whose middle run is just a space** ("Jennifer" + " " + "Martin") → the saved text reads `<PERSON>` once, with no stray space or doubled token. Test in Task 6.
2. **The reviewer rejects (or edits) one piece of a linked finding** → every piece of that finding gets the same decision; the output never half-redacts a name. Test in Task 6.
3. **Sessions saved by rc.3** (manifests without `block`, `join_before`, `group_*` fields) → load, take decisions and commit exactly as before. Test in Task 6.
4. **A two-column PDF page** → lines from the left and right columns never join into one block, so findings never span columns. Test in Task 7.
5. **A decision PATCH racing a commit on the same session** → no 500, no lost decision; the commit sees either the before or after state. Test in Task 4.

---

### Task 0: Publish the integration branch

**Files:** none (git only)

- [ ] **Step 1: Push `release/0.2.0`** (it already holds the Phase 3.5 plan, the design and this plan)

```bash
cd /Users/filippo/Desktop/sanctum
git switch release/0.2.0
git branch --unset-upstream   # it was created tracking origin/main
git push -u origin release/0.2.0
```

Expected: `* [new branch] release/0.2.0 -> release/0.2.0`.

---

### Task 1 (E1): Merge `overnight/pptx`

**Files:** merge only; no hand edits expected.

**Interfaces:**
- Produces: `sanctum/documents/layout.py` with `supports_layout(fmt: str) -> bool` and `build_layout(fmt: str, data: bytes) -> dict[str, Any]`; `sanctum/documents/pptx_layout.py::build_layout(stream: BinaryIO) -> dict`; the layout schema classes in `sanctum/api/schemas.py` (`LayoutTextboxItem`, `LayoutTextlineItem`, `LayoutImageItem`, `LayoutShapeItem`, `LayoutAltText`, `LayoutParagraph`, `LayoutRun`, `LayoutPage`, `LayoutUnscanned`, `ReviewSessionLayoutResponse`); `GET /review-sessions/<id>/layout` returning 415 for formats without a builder and 410 once input bytes are shed.

- [ ] **Step 1: Branch and merge**

```bash
git fetch origin && git switch -c integrate/e1-pptx origin/release/0.2.0
git merge --no-ff origin/overnight/pptx -m "Merge the pptx overnight lane (v0.2.0-rc.1 E1)"
```

Expected: merge completes with no conflicts.

- [ ] **Step 2: Run the gate.** Expected: everything passes except the two SIGTERM tests.

- [ ] **Step 3: PR** per the protocol. In **How it was checked**, also list the pptx tests added by the lane: `pytest -q tests -k pptx` count.

---

### Task 2 (E2): Merge `overnight/pdf-engine` and unify `/layout`

**Files:**
- Modify (conflicts): `sanctum/api/routes/review_sessions.py`, `sanctum/api/schemas.py`, `schema/openapi.json`, `scripts/generate_openapi.py`
- Modify: `sanctum/documents/layout.py`, `sanctum/documents/pdf_adapter.py`, `sanctum/documents/registry.py`
- Test: `tests/unit/documents/test_layout_dispatch.py` (create), plus the existing layout route tests from both lanes

**Interfaces:**
- Consumes: Task 1's `layout.py` dispatch and schema classes.
- Produces: one `/layout` route: `build_layout(session.format, input_bytes)` for `pptx` and `pdf`; 415 for others; 410 after shedding. `pdf_adapter.build_layout(source: Path | BinaryIO) -> dict[str, Any]`. `registry.layout_builder_for` is deleted.

- [ ] **Step 1: Branch and merge**

```bash
git fetch origin && git switch -c integrate/e2-pdf-engine origin/release/0.2.0
git merge --no-ff origin/overnight/pdf-engine
```

Expected: CONFLICT in the four files listed above.

- [ ] **Step 2: Resolve `sanctum/api/schemas.py`.** Keep the pptx-side (`HEAD`) layout classes verbatim: they are a superset (anchor, alt, notes, `unscanned[].page`, nullable image `src`). Delete pdf-engine's duplicate classes (`LayoutTextLine`, `LayoutTextBox`, `LayoutImage`, `LayoutShape`, its `LayoutItem` alias). Keep any non-layout additions from pdf-engine (for example error/detail models used by the 422).

- [ ] **Step 3: Resolve `sanctum/api/routes/review_sessions.py`.** Keep pptx's `get_session_layout` body (uses `supports_layout` / `build_layout` from `sanctum.documents.layout`, returns 415 / 410). Drop pdf-engine's version and its `from sanctum.documents.registry import layout_builder_for` import and tempfile use. Keep every other hunk from pdf-engine (notably the `except LeakCheckError` → 422 block in `commit_session`). Update the module docstring's `/layout` bullet to: ``GET /review-sessions/{id}/layout`` — positioned layout (Phase 3.5 shared contract) for pptx and pdf; ``415`` for other formats, ``410`` once the input bytes are shed.

- [ ] **Step 4: Register PDF in the dispatch.** In `sanctum/documents/layout.py`:

```python
_LAYOUT_BUILDERS = {
    "pptx": "sanctum.documents.pptx_layout",
    "pdf": "sanctum.documents.pdf_adapter",
}
```

In `sanctum/documents/pdf_adapter.py` make `build_layout` accept bytes streams as well as paths (the dispatcher passes `BytesIO`):

```python
def build_layout(source: Path | BinaryIO) -> dict[str, Any]:
    """The ``GET /review-sessions/<id>/layout`` body for a PDF. (keep the rest of the docstring)"""
    if isinstance(source, Path):
        extraction = _extract_file(source)
    else:
        extraction = _extract_bytes(source.read(), name="<session input>")
    ...  # body unchanged from here
```

and split `_extract_file` so the bytes path shares its error handling:

```python
def _extract_file(path: Path) -> PdfExtraction:
    return _extract_bytes(Path(path).read_bytes(), name=str(path))


def _extract_bytes(data: bytes, *, name: str) -> PdfExtraction:
    try:
        extraction = extract(data)
    except Exception as exc:
        raise DocumentError(f"Could not parse PDF {name}: {exc}") from exc
    if not extraction.lines:
        raise UnsupportedPdfError(
            f"{name} has no extractable text layer — "
            "scanned/image-only PDFs need OCR, which is not supported yet."
        )
    return extraction
```

Add `from typing import BinaryIO` to the imports. Delete `layout_builder_for` and its `LayoutBuilder` alias from `sanctum/documents/registry.py`.

- [ ] **Step 5: Resolve `scripts/generate_openapi.py`** by taking the union of both sides' registered models (the layout models now come from one set of class names; remove references to the deleted pdf-engine class names). Then regenerate: `.venv/bin/python scripts/generate_openapi.py` and take the generated `schema/openapi.json` (do not hand-merge the JSON).

- [ ] **Step 6: Write the dispatch test** `tests/unit/documents/test_layout_dispatch.py`:

```python
from pathlib import Path

import pytest

from sanctum.api.schemas import ReviewSessionLayoutResponse
from sanctum.core.exceptions import UnsupportedDocumentFormatError
from sanctum.documents.layout import build_layout, supports_layout

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_pdf_and_pptx_have_layout_builders() -> None:
    assert supports_layout("pdf")
    assert supports_layout("pptx")
    assert not supports_layout("docx")


def test_pdf_layout_from_bytes_validates_against_the_shared_schema(sample_pdf_path: Path) -> None:
    payload = build_layout("pdf", sample_pdf_path.read_bytes())
    parsed = ReviewSessionLayoutResponse.model_validate(payload)
    assert parsed.format == "pdf"
    first = parsed.pages[0].items[0]
    assert first.kind == "textline"
    assert first.segment_id == "page0/line0"


def test_docx_has_no_layout() -> None:
    with pytest.raises(UnsupportedDocumentFormatError):
        build_layout("docx", b"")
```

`sample_pdf_path` must point at a small text PDF: reuse the fixture the pdf-engine lane's tests use (search `tests/` for `engagement_letter.pdf` or `generate_pdf_samples`) and expose it as a fixture in `tests/conftest.py` if it is not one already. If `LayoutUnscanned` validation fails because PDF `unscanned` entries use different keys than `where`/`what`, change the PDF extractor to emit `{"where": ..., "what": ..., "page": <int|None>}` (the shared contract) and update its tests.

- [ ] **Step 7: Run the new test, then the full gate.** Expected: all pass except the two SIGTERM tests. Existing pdf-engine tests that asserted `501` for non-PDF layout requests must be updated to `415` — note each one in the PR's **Judgement calls**.

- [ ] **Step 8: Commit the resolution and open the PR** (`E2: Merge pdf-engine and serve /layout for pptx and pdf`).

---

### Task 3 (E3): Make the SIGTERM tests self-contained

**Files:**
- Modify: `tests/integration/test_serve_sigterm.py:85-86,131-132`

**Interfaces:** none.

- [ ] **Step 1: Confirm the failure cause**

Run: `.venv/bin/python -m pytest -q --no-cov tests/integration/test_serve_sigterm.py`
Expected: 2 failed, with `FileNotFoundError: ... 'sanctum'` (no console script on PATH).

- [ ] **Step 2: Launch through the current interpreter.** Replace both command lists:

```python
        [sys.executable, "-m", "sanctum.cli", "serve", "--port", "0", "--token-stdin"],
```

and add `import sys` at the top. If `python -m sanctum.cli` is not runnable (no `sanctum/cli/__main__.py`), create it:

```python
"""Allow ``python -m sanctum.cli`` (used by tests that must not depend on PATH)."""

from sanctum.cli.commands import main

if __name__ == "__main__":
    main()
```

(Check the console-script target in `pyproject.toml` `[project.scripts]` and import that exact callable instead of `main` if it is named differently.)

- [ ] **Step 3: Run the two tests.** Expected: 2 passed.

- [ ] **Step 4: Full gate with zero failures, then PR** (`E3: Run the SIGTERM tests without a sanctum binary on PATH`).

---

### Task 4 (E4): Per-session lock and atomic manifest writes

**Files:**
- Modify: `sanctum/core/review/store.py`
- Modify: `sanctum/api/routes/review_sessions.py` (the five mutating routes)
- Test: `tests/unit/core/review/test_store_lock.py` (create), `tests/integration/test_api_concurrent_decisions.py` (create)

**Interfaces:**
- Produces: `SessionStore.locked(session_id: str) -> contextlib.AbstractContextManager[None]` (re-entrant, process-wide per `(root, session_id)`).

- [ ] **Step 1: Write the failing unit test** `tests/unit/core/review/test_store_lock.py`:

```python
import threading
from datetime import datetime, timezone
from pathlib import Path

from sanctum.core.models import ProposalDecision, ReviewProposal, ReviewSession
from sanctum.core.review.session import add_decision
from sanctum.core.review.store import SessionStore


def _session(n: int) -> ReviewSession:
    proposals = [
        ReviewProposal(
            detection_id=f"p{i:02d}", entity_type="PERSON", score=0.9,
            original=f"Name{i}", segment_anchor=f"body/p{i}/r0", start=0, end=5,
        )
        for i in range(n)
    ]
    return ReviewSession(
        id="s1", source_path=Path("in.docx"), format="docx",
        default_operator="replace", segments=[], proposals=proposals,
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
```

(Adjust `ReviewSession`/`ProposalDecision` constructor fields to the model's required fields if they differ; read `sanctum/core/models.py`.)

- [ ] **Step 2: Run it.** Expected: FAIL with `AttributeError: 'SessionStore' object has no attribute 'locked'`.

- [ ] **Step 3: Implement the lock and atomic save** in `sanctum/core/review/store.py`:

```python
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager

_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(root: Path, session_id: str) -> threading.RLock:
    key = (str(root.resolve()), session_id)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.RLock()
        return lock
```

and on `SessionStore`:

```python
    @contextmanager
    def locked(self, session_id: str) -> Iterator[None]:
        """Serialise load → mutate → save for one session within this process.

        Locks are process-wide and keyed by (store root, session id), so two
        ``SessionStore`` instances over the same directory share them. The
        engine serves one desktop over a single process, so a thread lock is
        sufficient; it is re-entrant so helpers may nest it.
        """
        with _lock_for(self._root, session_id):
            yield
```

In `save`, replace the direct manifest write with an atomic one:

```python
        manifest = self._manifest_path(session.id)
        fd, tmp = tempfile.mkstemp(dir=str(manifest.parent), prefix=".manifest-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(session.model_dump_json())
            os.chmod(tmp, 0o600)
            os.replace(tmp, manifest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
```

(keep the existing directory creation, permission handling and first-time `input_bytes` write around it; keep the existing JSON dump call if it differs from `model_dump_json()`).

- [ ] **Step 4: Run the unit test.** Expected: PASS.

- [ ] **Step 5: Lock the routes.** In `sanctum/api/routes/review_sessions.py`, wrap everything from `_load_session(...)` through `store.save(...)` (and through the engine call for commit) in `with store.locked(session_id):` in these handlers: `patch_proposal_decision`, `add_user_added_decision`, `delete_user_added_decision`, `commit_session`, `abandon`. Read-only handlers (`get_session`, `list_sessions`, `get_session_input`, `get_session_layout`) stay unlocked: atomic writes mean they always read a complete manifest.

- [ ] **Step 6: Write the API-level test** `tests/integration/test_api_concurrent_decisions.py`, reusing the app/session fixtures the existing review-session API tests use (find them with `grep -rn "def client\|create_app" tests/`):

```python
from concurrent.futures import ThreadPoolExecutor


def test_parallel_patches_all_land(app, auth_headers, open_docx_session) -> None:
    session_id, proposal_ids = open_docx_session  # a session with >= 10 proposals

    def patch(pid: str) -> int:
        with app.test_client() as c:
            r = c.patch(
                f"/review-sessions/{session_id}/decisions/{pid}",
                json={"status": "accept"}, headers=auth_headers,
            )
            return r.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(patch, proposal_ids))

    assert codes == [200] * len(proposal_ids)
    with app.test_client() as c:
        body = c.get(f"/review-sessions/{session_id}", headers=auth_headers).get_json()
    decided = {d["proposal_id"] for d in body["decisions"] if d.get("kind") == "proposal"}
    assert decided == set(proposal_ids)


def test_patch_racing_commit_never_500s(app, auth_headers, open_docx_session, tmp_path) -> None:
    session_id, proposal_ids = open_docx_session

    def patch(pid: str) -> int:
        with app.test_client() as c:
            return c.patch(
                f"/review-sessions/{session_id}/decisions/{pid}",
                json={"status": "accept"}, headers=auth_headers,
            ).status_code

    def commit() -> int:
        with app.test_client() as c:
            return c.post(
                f"/review-sessions/{session_id}/commit",
                json={"output_path": str(tmp_path / "out.docx")}, headers=auth_headers,
            ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(patch, pid) for pid in proposal_ids] + [pool.submit(commit)]
        codes = [f.result() for f in futures]

    assert 500 not in codes
    assert set(codes) <= {200, 409, 422}
```

Shape the fixture names, the decision `kind` key and the commit body to whatever the existing API tests use; the assertions are what matter.

- [ ] **Step 7: Run both new test files, then the gate.** Expected: PASS.

- [ ] **Step 8: PR** (`E4: Serialise changes to a review session`). Mention that the desktop's one-at-a-time client fix (studio `391f4d6`) stays as a second line of defence.

---

### Task 5 (E5): Leak-check refinements

**Files:**
- Modify: `sanctum/core/leak_check.py`, `sanctum/core/exceptions.py`, `sanctum/api/routes/review_sessions.py` (the `except LeakCheckError` block)
- Test: `tests/unit/core/test_leak_check.py` (extend), the pdf-engine API leak test (`grep -rn "422" tests/integration/test_api_pdf_review.py`)

**Interfaces:**
- Produces: `count_surviving_originals(output_text: str, originals: Iterable[str]) -> dict[str, int]` (original → occurrences, input order, only those with ≥1); `LeakCheckError.occurrences: dict[str, int]`; 422 body `{"error": str, "details": [{"leak": str, "occurrences": int}, ...]}`.

- [ ] **Step 1: Write failing tests** (append to `tests/unit/core/test_leak_check.py`):

```python
from sanctum.core.leak_check import count_surviving_originals, find_surviving_originals


def test_bare_two_digit_numbers_are_not_checked() -> None:
    assert find_surviving_originals("14 Harbour Lane", ["14"]) == []
    assert find_surviving_originals("Unit 7", ["7"]) == []


def test_three_digit_numbers_and_mixed_tokens_are_still_checked() -> None:
    assert find_surviving_originals("Room 214", ["214"]) == ["214"]
    assert find_surviving_originals("Flat 4B", ["4B"]) == ["4B"]


def test_occurrences_are_counted_per_original() -> None:
    text = "Priya met Priya Raghunathan. Raghunathan left."
    assert count_surviving_originals(text, ["Priya", "Raghunathan", "Absent"]) == {
        "Priya": 2,
        "Raghunathan": 2,
    }
```

- [ ] **Step 2: Run.** Expected: FAIL (`ImportError: count_surviving_originals`, and the "14" test returns `["14"]`).

- [ ] **Step 3: Implement** in `sanctum/core/leak_check.py` — factor the pattern builder, add the numeric exemption and the counter:

```python
def _pattern_for(needle: str) -> re.Pattern[str] | None:
    """Compiled matcher for one normalized original, or None if it is exempt."""
    compact = needle.replace(" ", "")
    if len(compact) < 2:
        return None
    if compact.isdigit() and len(compact) <= 2:
        return None  # a stray "14" must not match "14 Harbour Lane"
    pattern = re.escape(needle)
    if needle[0].isalnum():
        pattern = r"(?<![^\W_])" + pattern
    if needle[-1].isalnum():
        pattern = pattern + r"(?![^\W_])"
    return re.compile(pattern)


def count_surviving_originals(output_text: str, originals: Iterable[str]) -> dict[str, int]:
    """Original → number of times it still appears (only originals that appear)."""
    haystack = normalize_whitespace(output_text)
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for original in originals:
        needle = normalize_whitespace(original)
        if needle in seen:
            continue
        seen.add(needle)
        matcher = _pattern_for(needle)
        if matcher is None:
            continue
        n = len(matcher.findall(haystack))
        if n:
            counts[original] = n
    return counts


def find_surviving_originals(output_text: str, originals: Iterable[str]) -> list[str]:
    """Return the originals (deduplicated, input order) found in ``output_text``."""
    return list(count_surviving_originals(output_text, originals))
```

Update `verify_no_leaks` to compute `counts = count_surviving_originals(...)` and raise `LeakCheckError(message, leaks=list(counts), occurrences=counts)`. Add the 1–2 digit rule to the module docstring's "Matching rules" list. In `sanctum/core/exceptions.py`:

```python
    def __init__(
        self,
        message: str,
        leaks: list[str] | None = None,
        occurrences: dict[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.leaks: list[str] = list(leaks or [])
        self.occurrences: dict[str, int] = dict(occurrences or {})
```

In the commit route's `except LeakCheckError` block:

```python
        details = [{"leak": v, "occurrences": exc.occurrences.get(v, 1)} for v in exc.leaks]
        return {"error": str(exc), "details": details}, 422
```

- [ ] **Step 4: Extend the API 422 test** to assert every detail has an integer `occurrences >= 1`.

- [ ] **Step 5: Run the leak tests, then the gate.** Expected: PASS.

- [ ] **Step 6: PR** (`E5: Skip bare short numbers in the leak check and report occurrence counts`).

---

### Task 6 (E6a–c): Block-level detection and linked findings (core)

**Files:**
- Create: `sanctum/core/blocks.py`
- Modify: `sanctum/core/models.py` (`TextSegment`, `ReviewProposal`)
- Modify: `sanctum/core/review/proposals.py`, `sanctum/core/review/session.py`, `sanctum/core/review/previews.py`, `sanctum/core/engine.py`
- Modify: `sanctum/documents/structured.py`, `sanctum/documents/docx_adapter.py`, `sanctum/documents/pptx_adapter.py`
- Test: `tests/unit/core/test_blocks.py` (create), `tests/unit/core/review/test_linked_findings.py` (create), `tests/integration/test_split_runs.py` (create)

**Interfaces:**
- Produces:
  - `TextSegment.block: str | None = None`, `TextSegment.join_before: str = ""`
  - `ReviewProposal.group_id: str | None = None`, `group_index: int = 0`, `group_original: str | None = None`
  - `sanctum.core.blocks`: `Piece(segment_id: str, start: int, end: int, text: str)`, `BlockFinding(entity_type: str, score: float, original: str, pieces: tuple[Piece, ...])`, `detect_blocks(segments: Sequence[TextSegment], analyze: Callable[[str], list[DetectionResult]]) -> list[BlockFinding]`, `splice(text: str, edits: Iterable[tuple[int, int, str]]) -> str`
  - `sanctum.core.review.proposals.build_proposals_from_findings(findings: Sequence[BlockFinding]) -> list[ReviewProposal]`
  - `sanctum.documents.structured.build_segment(segment_id, text, *, block=None, join_before="", **metadata)` and `run_block(segment_id: str) -> str | None`
  - API JSON: every proposal gains `group_id`, `group_index`, `group_original` (null/0/null for single-piece findings). Task 7 (PDF) and desktop D7 consume these.

- [ ] **Step 1: Write failing tests for `blocks.py`** — `tests/unit/core/test_blocks.py`:

```python
from sanctum.core.blocks import BlockFinding, Piece, detect_blocks, splice
from sanctum.core.models import DetectionResult, TextSegment


def seg(sid: str, text: str, block: str | None, join: str = "") -> TextSegment:
    return TextSegment(id=sid, text=text, block=block, join_before=join)


def fake_analyze(entity: str, needle: str):
    def analyze(text: str) -> list[DetectionResult]:
        i = text.find(needle)
        if i < 0:
            return []
        return [DetectionResult(entity_type=entity, start=i, end=i + len(needle), score=0.9, text_span=needle)]
    return analyze


def test_name_split_across_runs_becomes_one_finding_with_pieces() -> None:
    segs = [seg("p0/r0", "Dear Jen", "p0"), seg("p0/r1", "nifer Mar", "p0"), seg("p0/r2", "tin,", "p0")]
    [f] = detect_blocks(segs, fake_analyze("PERSON", "Jennifer Martin"))
    assert f.original == "Jennifer Martin"
    assert f.pieces == (
        Piece("p0/r0", 5, 8, "Jen"),
        Piece("p0/r1", 0, 9, "nifer Mar"),
        Piece("p0/r2", 0, 3, "tin"),
    )


def test_whitespace_only_middle_run_is_a_piece() -> None:
    segs = [seg("p0/r0", "Jennifer", "p0"), seg("p0/r1", " ", "p0"), seg("p0/r2", "Martin", "p0")]
    [f] = detect_blocks(segs, fake_analyze("PERSON", "Jennifer Martin"))
    assert [p.segment_id for p in f.pieces] == ["p0/r0", "p0/r1", "p0/r2"]


def test_join_before_text_is_not_part_of_any_piece() -> None:
    segs = [seg("page0/line3", "Dr Evelyn", "page0/para1"), seg("page0/line4", "Marchetti said", "page0/para1", " ")]
    [f] = detect_blocks(segs, fake_analyze("PERSON", "Evelyn Marchetti"))
    assert f.original == "Evelyn Marchetti"
    assert [p.text for p in f.pieces] == ["Evelyn", "Marchetti"]


def test_segments_without_block_are_analysed_alone() -> None:
    segs = [seg("a", "Jennifer", None), seg("b", " Martin", None)]
    assert detect_blocks(segs, fake_analyze("PERSON", "Jennifer Martin")) == []


def test_blocks_group_by_key_not_adjacency() -> None:
    # two-column PDF order: left line, right line, left line
    segs = [seg("l0", "Evelyn", "colA", ""), seg("r0", "Total", "colB"), seg("l1", "Marchetti", "colA", " ")]
    [f] = detect_blocks(segs, fake_analyze("PERSON", "Evelyn Marchetti"))
    assert [p.segment_id for p in f.pieces] == ["l0", "l1"]


def test_splice_applies_edits_right_to_left() -> None:
    assert splice("Dear Jen", [(5, 8, "<PERSON>")]) == "Dear <PERSON>"
    assert splice("abc", [(0, 1, "X"), (2, 3, "")]) == "Xb"
```

- [ ] **Step 2: Run.** Expected: FAIL (`ModuleNotFoundError: sanctum.core.blocks`; `TextSegment` has no `block`).

- [ ] **Step 3: Add the model fields** in `sanctum/core/models.py`:

```python
class TextSegment(BaseModel):
    # ...existing docstring; add:
    #   ``block`` groups segments that form one paragraph for detection;
    #   ``join_before`` is the text placed between the previous segment of the
    #   same block and this one when the block is joined (e.g. " " between PDF
    #   lines, "" between Word runs). ``None`` = analysed alone.
    id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    block: str | None = None
    join_before: str = ""
```

and on `ReviewProposal` (after `end`), with a docstring paragraph explaining linked pieces:

```python
    # Linked pieces: a finding that spans several segments (a name split
    # across Word runs or PDF lines) becomes one proposal per segment piece,
    # all sharing ``group_id``. ``group_index`` 0 is the head: it renders the
    # replacement; the other pieces render "". ``group_original`` is the
    # whole finding's text. Single-piece findings leave these at defaults.
    group_id: str | None = None
    group_index: int = Field(default=0, ge=0)
    group_original: str | None = None
```

- [ ] **Step 4: Create `sanctum/core/blocks.py`:**

```python
"""Block-level detection: analyse whole paragraphs, project findings onto segments.

Segments are the grain at which documents are written back (a Word run, a PDF
line). Detection needs more context than that: Word splits a name across runs
whenever formatting, spell-check or tracked edits touch it, and PDF breaks
names across lines. Adapters tag segments with a ``block`` key; this module
joins each block's segments (with ``join_before`` between them), analyses the
joined text once, and maps every finding back to the segment pieces it covers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from sanctum.core.models import DetectionResult, TextSegment


@dataclass(frozen=True)
class Piece:
    segment_id: str
    start: int  # offsets into the segment's own text
    end: int
    text: str


@dataclass(frozen=True)
class BlockFinding:
    entity_type: str
    score: float
    original: str  # the joined text of the finding (may include join_before)
    pieces: tuple[Piece, ...]


@dataclass(frozen=True)
class _Block:
    segments: tuple[TextSegment, ...]
    offsets: tuple[int, ...]  # where each segment's text starts in ``text``
    text: str


def _group(segments: Sequence[TextSegment]) -> list[tuple[TextSegment, ...]]:
    groups: dict[object, list[TextSegment]] = {}
    for i, seg in enumerate(segments):
        key: object = ("block", seg.block) if seg.block is not None else ("solo", i)
        groups.setdefault(key, []).append(seg)
    return [tuple(g) for g in groups.values()]


def _join(segs: tuple[TextSegment, ...]) -> _Block:
    parts: list[str] = []
    offsets: list[int] = []
    pos = 0
    for i, seg in enumerate(segs):
        if i > 0 and seg.join_before:
            parts.append(seg.join_before)
            pos += len(seg.join_before)
        offsets.append(pos)
        parts.append(seg.text)
        pos += len(seg.text)
    return _Block(segs, tuple(offsets), "".join(parts))


def _project(block: _Block, start: int, end: int) -> tuple[Piece, ...]:
    pieces: list[Piece] = []
    for seg, off in zip(block.segments, block.offsets):
        s = max(start, off)
        e = min(end, off + len(seg.text))
        if s < e:
            pieces.append(Piece(seg.id, s - off, e - off, seg.text[s - off : e - off]))
    return tuple(pieces)


def detect_blocks(
    segments: Sequence[TextSegment],
    analyze: Callable[[str], list[DetectionResult]],
) -> list[BlockFinding]:
    """Findings in document order, each mapped onto the segments it covers."""
    findings: list[BlockFinding] = []
    for group in _group(segments):
        block = _join(group)
        if not block.text.strip():
            continue
        for det in analyze(block.text):
            pieces = _project(block, det.start, det.end)
            if not pieces:
                continue  # the span fell entirely inside join_before text
            findings.append(
                BlockFinding(det.entity_type, det.score, block.text[det.start : det.end], pieces)
            )
    return findings


def splice(text: str, edits: Iterable[tuple[int, int, str]]) -> str:
    """Apply non-overlapping ``(start, end, replacement)`` edits to ``text``."""
    out = text
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + repl + out[end:]
    return out
```

- [ ] **Step 5: Run `test_blocks.py`.** Expected: PASS.

- [ ] **Step 6: Write failing tests for proposals, decisions, previews and commit** — `tests/unit/core/review/test_linked_findings.py`:

```python
from sanctum.core.blocks import BlockFinding, Piece
from sanctum.core.models import ProposalDecision
from sanctum.core.review.proposals import build_proposals_from_findings
from sanctum.core.review.session import add_decision

SPLIT = BlockFinding(
    "PERSON", 0.9, "Jennifer Martin",
    (Piece("p0/r0", 0, 8, "Jennifer"), Piece("p0/r1", 0, 1, " "), Piece("p0/r2", 0, 6, "Martin")),
)
SOLO = BlockFinding("PERSON", 0.8, "Cameron", (Piece("p1/r0", 4, 11, "Cameron"),))


def test_single_piece_findings_keep_todays_ids() -> None:
    from sanctum.core.review.identifiers import make_detection_id
    [p] = build_proposals_from_findings([SOLO])
    assert p.detection_id == make_detection_id("PERSON", "Cameron", "p1/r0:4")
    assert p.group_id is None


def test_split_finding_becomes_linked_pieces() -> None:
    ps = build_proposals_from_findings([SPLIT])
    assert [p.group_index for p in ps] == [0, 1, 2]
    assert len({p.group_id for p in ps}) == 1 and ps[0].group_id is not None
    assert all(p.group_original == "Jennifer Martin" for p in ps)
    assert len({p.detection_id for p in ps}) == 3


def test_deciding_one_piece_decides_the_whole_group(make_session) -> None:
    session = make_session(build_proposals_from_findings([SPLIT, SOLO]))
    piece = session.proposals[1]
    add_decision(session, ProposalDecision(proposal_id=piece.detection_id, status="reject"))
    statuses = {d.proposal_id: d.status for d in session.decisions}
    assert statuses == {p.detection_id: "reject" for p in session.proposals[:3]}
```

(`make_session` is a small fixture in the same file or `conftest.py` that wraps a proposal list in an open `ReviewSession`; build it from the fields `models.py` requires.)

Add an engine-level test in the same file or `tests/integration/test_split_runs.py` that builds a real .docx with python-docx where "Jennifer Martin" is split into three runs (`"Jennifer"`, `" "` bold, `"Martin"`) plus a solo "Cameron Dean" paragraph, then:

```python
import json
from pathlib import Path

import docx

from sanctum.core.models import ProposalDecision
from sanctum.core.review.session import add_decision
from sanctum.core.review.store import SessionStore
from sanctum.documents.docx_adapter import Reader, Writer


def make_docx(path: Path, runs: list[str]) -> Path:
    d = docx.Document()
    para = d.add_paragraph()
    for i, text in enumerate(runs):
        para.add_run(text).bold = i % 2 == 1  # alternate formatting forces separate runs
    d.save(str(path))
    return path


def accept_all(store: SessionStore, session_id: str) -> None:
    with store.locked(session_id):
        s = store.load(session_id)
        for p in s.proposals:
            add_decision(s, ProposalDecision(proposal_id=p.detection_id, status="accept"))
        store.save(s)


def test_split_name_is_redacted_whole_in_docx(engine, tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    src = make_docx(tmp_path / "in.docx", ["Dear Jennifer", " ", "Martin, thanks."])
    session = engine.create_review_session(Reader(), src, session_store=store)
    assert {p.group_original for p in session.proposals if p.group_id} == {"Jennifer Martin"}
    accept_all(store, session.id)
    out = tmp_path / "out.docx"
    engine.commit_review_session(Reader(), Writer(), session.id, out, store)
    text = "".join(r.text for r in docx.Document(str(out)).paragraphs[0].runs)
    assert text == "Dear <PERSON>, thanks."


def test_rc3_manifest_without_new_fields_still_commits(engine, tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    src = make_docx(tmp_path / "in.docx", ["Dear Cameron Dean, thanks."])
    session = engine.create_review_session(Reader(), src, session_store=store)
    manifest = store.root / session.id / "manifest.json"
    raw = json.loads(manifest.read_text())
    for seg in raw["segments"]:
        seg.pop("block", None)
        seg.pop("join_before", None)
    for prop in raw["proposals"]:
        for key in ("group_id", "group_index", "group_original"):
            prop.pop(key, None)
    manifest.write_text(json.dumps(raw))

    accept_all(store, session.id)
    out = tmp_path / "out.docx"
    engine.commit_review_session(Reader(), Writer(), session.id, out, store)
    assert "Cameron" not in docx.Document(str(out)).paragraphs[0].text
```

`engine` is the real Presidio engine fixture the integration tests already use (`grep -rn "def engine" tests/conftest.py tests/integration/conftest.py`); match `create_review_session`'s actual keyword names (it may require `language`/`entities` arguments — pass the same values the existing integration tests pass).

- [ ] **Step 7: Run.** Expected: FAIL (`build_proposals_from_findings` missing; docx output still contains "Martin").

- [ ] **Step 8: Implement proposals** in `sanctum/core/review/proposals.py` (keep `build_proposals` for existing callers/tests):

```python
def build_proposals_from_findings(findings: Sequence[BlockFinding]) -> list[ReviewProposal]:
    """One proposal per single-segment finding; linked pieces for split findings."""
    proposals: list[ReviewProposal] = []
    for f in findings:
        if len(f.pieces) == 1:
            p = f.pieces[0]
            proposals.append(
                ReviewProposal(
                    detection_id=make_detection_id(f.entity_type, p.text, f"{p.segment_id}:{p.start}"),
                    entity_type=f.entity_type, score=f.score, original=p.text,
                    segment_anchor=p.segment_id, start=p.start, end=p.end,
                )
            )
            continue
        head = f.pieces[0]
        group_id = make_detection_id(f.entity_type, f.original, f"group:{head.segment_id}:{head.start}")
        for index, p in enumerate(f.pieces):
            proposals.append(
                ReviewProposal(
                    detection_id=make_detection_id(
                        f.entity_type, p.text, f"{p.segment_id}:{p.start}:{group_id}"
                    ),
                    entity_type=f.entity_type, score=f.score, original=p.text,
                    segment_anchor=p.segment_id, start=p.start, end=p.end,
                    group_id=group_id, group_index=index, group_original=f.original,
                )
            )
    return proposals
```

- [ ] **Step 9: Group-aware decisions** in `sanctum/core/review/session.py::add_decision` — after the "unknown proposal" check, expand a `ProposalDecision` to every member of its group:

```python
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
        session.decisions = [
            d for d in session.decisions
            if not (isinstance(d, ProposalDecision) and d.proposal_id in member_ids)
        ]
        session.decisions.extend(decision.model_copy(update={"proposal_id": pid}) for pid in member_ids)
        return
    session.decisions.append(decision)
```

In `apply_user_added_with_overlap_purge`, when a user-added span overlaps any piece of a group, purge every piece of that group (collect the overlapped proposals' `group_id`s, then remove all proposals with those ids and their decisions; include them in the returned removed ids).

- [ ] **Step 10: Head/tail rendering.** In `sanctum/core/review/previews.py::compute_preview` add at the top (after the docstring):

```python
    if proposal.group_id is not None and proposal.group_index > 0:
        return ""  # only the head piece of a linked finding carries the replacement
```

and run the head through the anonymizer with the whole finding's text: build the synthetic detection over `proposal.group_original or proposal.original`. In `sanctum/core/engine.py`'s commit loop (the `for prop ...` block that calls `_render_replacement`), pass `original=prop.group_original or prop.original` and short-circuit tail pieces:

```python
            if prop.group_id is not None and prop.group_index > 0:
                replacement = ""
            else:
                replacement = _render_replacement(
                    entity_type=prop.entity_type,
                    original=prop.group_original or prop.original,
                    ...  # unchanged arguments
                )
```

Record what the leak check must look for on each replacement entry: in the `metadata["replacements"]` list comprehension add `"leak_original"`, set per edit — the group's `group_original` for a head piece, `None` for tail pieces, the piece's own original otherwise (carry it in the `replacements` tuples as a 4th element). Then in `_originals_to_verify`:

```python
    kept = {
        prop.group_original or prop.original
        for prop in session.proposals
        for d in session.decisions
        if isinstance(d, ProposalDecision) and d.status == "reject" and d.proposal_id == prop.detection_id
    }
    originals: list[str] = []
    for seg in segments:
        for rep in seg.metadata.get("replacements", []):
            value = rep.get("leak_original", rep["original"])
            if value is not None and value not in kept:
                originals.append(value)
    return originals
```

- [ ] **Step 11: Engine detection on blocks.** In `SanctumEngine.create_review_session` replace the per-segment loop and `build_proposals(...)` with:

```python
        findings = detect_blocks(
            doc.segments,
            lambda text: self.analyze(
                text, language=language, entities=entities, score_threshold=score_threshold
            ),
        )
        proposals = build_proposals_from_findings(findings)
```

In `process_document` (fire-and-forget) replace the per-segment loop with the same `detect_blocks` call, then build edits per segment and splice:

```python
        edits: dict[str, list[tuple[int, int, str]]] = {}
        replaced_originals: list[str] = []
        for f in findings:
            whole = DetectionResult(
                entity_type=f.entity_type, start=0, end=len(f.original), score=f.score, text_span=f.original
            )
            rendered = self.anonymize(
                f.original, detections=[whole], operator_policies=operator_policies
            ).anonymized_text
            for i, piece in enumerate(f.pieces):
                edits.setdefault(piece.segment_id, []).append((piece.start, piece.end, rendered if i == 0 else ""))
            replaced_originals.append(f.original)
        new_segments = [
            seg.model_copy(update={"text": splice(seg.text, edits[seg.id])}) if seg.id in edits else seg
            for seg in doc.segments
        ]
```

Keep the `results: list[AnonymizationResult]` return contract: append one `AnonymizationResult` per finding (read its constructor in `models.py`; if callers only use the count, a per-finding result built from `rendered` is enough — note it in **Judgement calls**).

- [ ] **Step 12: Tag runs with blocks.** In `sanctum/documents/structured.py`:

```python
_RUN_SUFFIX = re.compile(r"/r\d+$")


def run_block(segment_id: str) -> str | None:
    """Paragraph key for a ``.../p{p}/r{r}`` run id; None for non-run segments."""
    return _RUN_SUFFIX.sub("", segment_id) if _RUN_SUFFIX.search(segment_id) else None


def build_segment(
    segment_id: str,
    text: str,
    *,
    block: str | None = None,
    join_before: str = "",
    **metadata: Any,
) -> TextSegment:
    return TextSegment(id=segment_id, text=text, metadata=dict(metadata), block=block, join_before=join_before)
```

In `docx_adapter.Reader.read` and in `pptx_adapter` (the `build_segment(seg_id, target.get())` call) pass `block=run_block(seg_id)`. Alt-text (`.../alt`) and other non-run segments get `None` and are analysed alone, as before.

- [ ] **Step 13: Run the new tests, then the gate.** Expected: PASS. Some existing tests assert exact proposal lists or per-segment `analyze` calls; update them only where the change is the intended one (more context per analysis, linked pieces) and list each in **Judgement calls**.

- [ ] **Step 14: PR** (`E6: Detect on whole paragraphs and link findings that span runs`). In **What and why**, state that this also fixes split-run misses in today's .docx flow.

---

### Task 7 (E6, PDF): Group PDF lines into paragraphs

**Files:**
- Modify: `sanctum/documents/_pdf_extract.py`, `sanctum/documents/pdf_adapter.py`, `sanctum/documents/_pdf_redact.py` (only if Step 5 fails)
- Test: `tests/unit/documents/test_pdf_paragraphs.py` (create), the pdf-engine API review test

**Interfaces:**
- Consumes: Task 6 `TextSegment.block` / `join_before`, linked proposals.
- Produces: `PdfLine.block: str` (`page{i}/para{k}`); PDF segments carry `block` and `join_before=" "` for every line after the first in its paragraph.

- [ ] **Step 1: Write failing tests** `tests/unit/documents/test_pdf_paragraphs.py` using reportlab to draw (a) a paragraph whose name breaks across lines ("…signed by Dr Evelyn" / "Marchetti on behalf…"), (b) a two-column block where the left and right columns have lines at the same heights, (c) two paragraphs separated by a blank line:

```python
def test_wrapped_lines_share_a_block(tmp_path) -> None:
    doc = Reader().read(draw_pdf(tmp_path, wrapped_paragraph=True))
    a, b = doc.segments[:2]
    assert a.block == b.block
    assert b.join_before == " "


def test_columns_are_separate_blocks(tmp_path) -> None:
    doc = Reader().read(draw_pdf(tmp_path, two_columns=True))
    left = {s.block for s in doc.segments if s.metadata["x"] < 200}
    right = {s.block for s in doc.segments if s.metadata["x"] >= 300}
    assert left.isdisjoint(right)


def test_blank_line_starts_a_new_block(tmp_path) -> None:
    doc = Reader().read(draw_pdf(tmp_path, two_paragraphs=True))
    assert doc.segments[0].block != doc.segments[-1].block
```

(`draw_pdf` is a local helper: reportlab `canvas.Canvas`, 11 pt Helvetica, 13.2 pt leading; columns at x=72 and x=320; paragraph gap of 2 leading.)

- [ ] **Step 2: Run.** Expected: FAIL (`block` is None).

- [ ] **Step 3: Assign paragraphs** in `_pdf_extract.py`, after a page's lines are built:

```python
def _assign_blocks(page_index: int, lines: list[PdfLine]) -> None:
    """Group a page's lines into paragraphs: same column, small vertical gap.

    A line joins an open paragraph when its x-range overlaps the paragraph's
    last line, it starts at most 0.8 line-heights below that line's bottom,
    and the font sizes are within 20%. Otherwise it opens a new paragraph.
    Grouping is by key, so interleaved column order does not matter.
    """
    open_paras: list[tuple[str, PdfLine]] = []
    count = 0
    for line in lines:
        x0, top, x1, _ = line.bbox
        chosen: str | None = None
        for i, (key, last) in enumerate(open_paras):
            lx0, _, lx1, lbottom = last.bbox
            height = max(last.bbox[3] - last.bbox[1], line.bbox[3] - line.bbox[1])
            overlaps = min(x1, lx1) - max(x0, lx0) > 0
            gap = top - lbottom
            similar = abs(line.size - last.size) <= 0.2 * max(line.size, last.size)
            if overlaps and -0.2 * height <= gap <= 0.8 * height and similar:
                chosen = key
                open_paras[i] = (key, line)
                break
        if chosen is None:
            chosen = f"page{page_index}/para{count}"
            count += 1
            open_paras.append((chosen, line))
        line.block = chosen
```

Add `block: str = ""` to the `PdfLine` dataclass and call `_assign_blocks(page_index, page_lines)` where each page's lines are finalised. In `pdf_adapter.Reader.read`, pass `block=line.block` and `join_before=" "` when the previous line seen with the same block exists (track with a `set` of blocks already started), else `""`, to `build_segment`.

- [ ] **Step 4: Run the paragraph tests.** Expected: PASS.

- [ ] **Step 5: Empty replacements in the PDF writer.** Add a unit test that commits a linked finding whose tail piece renders `""` and asserts the writer paints the tail box and draws nothing (no exception, output text lacks "Marchetti"). If it fails (for example a font-fit division by zero on an empty string), guard in `_pdf_redact.py`: skip drawing text when the replacement is empty, still paint the box.

- [ ] **Step 6: Re-run the API PDF review test on `rich_letter.pdf`** and assert "Marchetti" and "Raghunathan" are now proposed (as part of linked findings) and absent from the committed output.

- [ ] **Step 7: Gate, then PR** (`E6: Read PDF lines as paragraphs so names across line breaks are found`). Include before/after renders of the "Dr Evelyn / Marchetti" crop (reuse `reports/pdf-engine/` render code) in the PR body.

---

### Task 8 (E6d): Email addresses on any domain

**Files:**
- Create: `sanctum/analyzer/recognizers.py`
- Modify: `sanctum/cli/commands.py:85-100` (where `extra_recognizers` is built)
- Test: `tests/unit/analyzer/test_email_recognizer.py` (create)

**Interfaces:**
- Produces: `AnyDomainEmailRecognizer()` (Presidio `PatternRecognizer`, entity `EMAIL_ADDRESS`, name `AnyDomainEmailRecognizer`).

- [ ] **Step 1: Failing test:**

```python
import pytest

from sanctum.analyzer.recognizers import AnyDomainEmailRecognizer


@pytest.mark.parametrize(
    "text, expected",
    [
        ("mail a@firm.local now", "a@firm.local"),
        ("margaret.holloway@hollowayfinch.example", "margaret.holloway@hollowayfinch.example"),
        ("j.albrecht@northgate.internal.", "j.albrecht@northgate.internal"),
        ("m.holloway@hollowayfinch.co.uk", "m.holloway@hollowayfinch.co.uk"),
    ],
)
def test_matches_any_well_formed_domain(text: str, expected: str) -> None:
    [r] = AnyDomainEmailRecognizer().analyze(text, ["EMAIL_ADDRESS"])
    assert text[r.start : r.end] == expected


@pytest.mark.parametrize("text", ["not@an", "@firm.local", "a@.local", "a@b"])
def test_rejects_malformed(text: str) -> None:
    assert AnyDomainEmailRecognizer().analyze(text, ["EMAIL_ADDRESS"]) == []
```

- [ ] **Step 2: Run.** Expected: FAIL (module missing).

- [ ] **Step 3: Implement** `sanctum/analyzer/recognizers.py`:

```python
"""Recognizers Sanctum adds to Presidio's predefined set."""

from __future__ import annotations

from presidio_analyzer import Pattern, PatternRecognizer

# Presidio's EmailRecognizer validates the TLD against the public suffix list,
# so internal domains (.local, .internal, .corp) are missed. Law firms use them.
_EMAIL = (
    r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?![\w-])"
)


class AnyDomainEmailRecognizer(PatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="EMAIL_ADDRESS",
            name="AnyDomainEmailRecognizer",
            patterns=[Pattern("email, any domain", _EMAIL, 0.9)],
        )
```

In `sanctum/cli/commands.py`, before `PresidioAnalyzer(...)`: `extra_recognizers.append(AnyDomainEmailRecognizer())`, importing it from `sanctum.analyzer.recognizers`. Keep Presidio's own `EmailRecognizer` (the overlap normaliser keeps one span when both fire).

- [ ] **Step 4: Run the unit test.** Expected: PASS.

- [ ] **Step 5: Integration check:** add a case to `tests/integration/test_split_runs.py` (Task 6) with a .docx paragraph "Contact j.albrecht@northgate.local today" split as runs `"Contact j.al"`, `"brecht@northgate.local today"`; assert one finding of type `EMAIL_ADDRESS` with `group_original == "j.albrecht@northgate.local"` and no `URL` finding.

- [ ] **Step 6: Gate, then PR** (`E6: Detect email addresses on internal domains`).

---

### Task 9 (E7): Strip hidden data from docx and pptx, and leak-check them

**Files:**
- Modify: `sanctum/documents/docx_adapter.py` (`Writer`), `sanctum/documents/pptx_adapter.py` (`Writer`)
- Create: `sanctum/documents/_ooxml_scrub.py`
- Test: `tests/unit/documents/test_ooxml_scrub.py` (create), extend `tests/integration/test_split_runs.py`

**Interfaces:**
- Produces: `scrub_core_properties(core_props) -> None`, `remove_comments_part(package) -> None` in `_ooxml_scrub.py`; `Writer.extract_text(path: Path) -> str` on both writers (satisfies `sanctum.core.protocols.OutputTextExtractor`).

- [ ] **Step 1: Failing tests** — build a .docx with python-docx setting `core_properties.author = "Jennifer Martin"`, `last_modified_by`, `title`, `subject`, `keywords`, `comments`, `category`; add a Word comment (python-docx ≥ 1.2 `document.add_comment(runs, text="…", author="Jennifer Martin")`; if unavailable, inject a minimal `word/comments.xml` part with `docx.opc` APIs). Same for .pptx core properties (python-pptx `prs.core_properties`) and a comment-authors part if present. Assert after a commit:

```python
def test_docx_output_has_no_identifying_properties_or_comments(tmp_path) -> None:
    out = commit_docx_with_props(tmp_path)
    d = docx.Document(out)
    cp = d.core_properties
    assert (cp.author, cp.last_modified_by, cp.title, cp.subject, cp.keywords, cp.comments, cp.category) == ("",) * 7
    with zipfile.ZipFile(out) as z:
        assert not any(n.startswith("word/comments") for n in z.namelist())
        assert b"Jennifer Martin" not in b"".join(z.read(n) for n in z.namelist())
```

and the pptx equivalent (`ppt/comments/`, `ppt/commentAuthors.xml`, `docProps/core.xml`).

- [ ] **Step 2: Run.** Expected: FAIL.

- [ ] **Step 3: Implement `_ooxml_scrub.py`:**

```python
"""Remove hidden identifying data from Word and PowerPoint packages."""

from __future__ import annotations

from typing import Any

_TEXT_FIELDS = (
    "author", "last_modified_by", "title", "subject", "keywords",
    "comments", "category", "content_status", "identifier", "language", "version",
)
_COMMENT_RELTYPES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
    "http://schemas.microsoft.com/office/2011/relationships/commentsExtended",
    "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds",
    "http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible",
    "http://schemas.microsoft.com/office/2011/relationships/people",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/commentAuthors",
)


def scrub_core_properties(core_props: Any) -> None:
    for field in _TEXT_FIELDS:
        if hasattr(core_props, field):
            setattr(core_props, field, "")


def remove_comment_parts(part: Any) -> None:
    """Drop comment-related relationships from ``part`` (document or presentation part)."""
    for rel_id, rel in list(part.rels.items()):
        if rel.reltype in _COMMENT_RELTYPES:
            part.drop_rel(rel_id)
```

For Word, comment *anchors* also live in the body (`w:commentRangeStart`, `w:commentRangeEnd`, `w:commentReference`); remove those elements from `document.element.body` with an XPath (`.//w:commentRangeStart | .//w:commentRangeEnd | .//w:commentReference`) and delete each from its parent. For PowerPoint, comments hang off each slide part: call `remove_comment_parts(slide.part)` for every slide and on `prs.part`. Call `scrub_core_properties(...)` and the comment removal in each `Writer.write` before saving. If `drop_rel` does not remove the part from the saved package in the installed python-docx/pptx version, verify with the zip assertion and fall back to deleting the part from `package.parts` explicitly — record which was needed in **Judgement calls**.

- [ ] **Step 4: Add `extract_text`** to both writers (all text a reader of the output could see, including headers/footers for docx, notes and alt-text for pptx):

```python
    def extract_text(self, path: Path) -> str:
        doc = Reader().read(path)
        return "\n".join(seg.text for seg in doc.segments)
```

(If the docx `Reader` does not read headers/footers, also append `"\n".join(p.text for s in d.sections for p in (*s.header.paragraphs, *s.footer.paragraphs))` using python-docx directly.) This enables the engine's post-write leak check for both formats with no engine change.

- [ ] **Step 5: Run the scrub tests and the docx/pptx integration tests, then the gate.** Expected: PASS. If a previously-passing docx/pptx fixture now fails the leak check, that is a real leak: investigate and report it in the PR rather than loosening the check.

- [ ] **Step 6: PR** (`E7: Strip author, properties and comments from Word and PowerPoint output`). Note in **What and why** that this also affects .docx output in the rc.3 line.

---

### Task 10 (E8): Hand-marked text uses the session's replacement style

**Files:**
- Modify: `sanctum/core/review/previews.py`, `sanctum/core/engine.py` (`_render_replacement` callers for user-added)
- Test: `tests/unit/core/review/test_user_added_replacement.py` (create)

**Interfaces:**
- Produces: `effective_params(entity_type: str, operator: str, params: dict[str, Any] | None) -> dict[str, Any]` in `previews.py`.

- [ ] **Step 1: Failing test:**

```python
from sanctum.core.review.previews import effective_params


def test_user_added_type_token_becomes_redacted_marker() -> None:
    assert effective_params("USER_ADDED", "replace", {}) == {"new_value": "[REDACTED]"}


def test_explicit_new_value_wins() -> None:
    assert effective_params("USER_ADDED", "replace", {"new_value": "X"}) == {"new_value": "X"}


def test_other_entities_are_untouched() -> None:
    assert effective_params("PERSON", "replace", {}) == {}
```

plus an API test: add a user-added span in a session created with default params `{}` and assert the returned `preview == "[REDACTED]"`; with session params `{"new_value": "███"}` assert `"███"`.

- [ ] **Step 2: Run.** Expected: FAIL.

- [ ] **Step 3: Implement** in `previews.py`:

```python
USER_ADDED_ENTITY = "USER_ADDED"
USER_ADDED_DEFAULT = "[REDACTED]"


def effective_params(entity_type: str, operator: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """Operator params after Sanctum's defaults.

    Text the reviewer marks by hand has no detected type, so the ``replace``
    operator's type token would read ``<USER_ADDED>`` in the output. Use a
    neutral marker instead unless the session or decision sets ``new_value``.
    """
    merged = dict(params or {})
    if operator == "replace" and entity_type == USER_ADDED_ENTITY and "new_value" not in merged:
        merged["new_value"] = USER_ADDED_DEFAULT
    return merged
```

Use it in `compute_preview` (`params = effective_params(proposal.entity_type, operator, operator_params)` replacing `dict(operator_params or {})`). Commit already routes through `compute_preview` via `_render_replacement`, so check that the user-added branch in `engine.py` passes `entity_type=ua.entity_type` (it does) and needs no change.

- [ ] **Step 4: Run the tests, then the gate.** Expected: PASS.

- [ ] **Step 5: PR** (`E8: Replace hand-marked text with [REDACTED] instead of <USER_ADDED>`).

---

### Task 11: Engine release check

**Files:** none new (reports in the PR thread)

- [ ] **Step 1:** On `origin/release/0.2.0` after Tasks 1–10 are merged, run the full gate. Expected: zero failures.
- [ ] **Step 2:** Run `reports/pdf-engine/e2e_serve_check.py` from `~/Desktop/sanctum-overnight` against a real `sanctum serve` started from this checkout; expected: commit #1 now succeeds or fails only on genuine leaks; zero surviving values; server exits 0.
- [ ] **Step 3:** Run the pptx lane's `verify_output.py` against a fresh commit of `synthetic_briefing.pptx` through the API; expected: 0 leaks, and the previously missed `.example` emails are now proposed.
- [ ] **Step 4:** Post the results as a comment on a tracking issue `v0.2.0-rc.1 engine` (create it with `gh issue create`), then report to the product owner that the engine side is ready for the desktop steps D5–D7.
