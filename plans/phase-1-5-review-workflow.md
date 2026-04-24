# Sanctum — Phase 1.5 Implementation Plan: Human-in-the-loop Review Workflow

> **Reframe notice (issue #16, 2026-04-24).** The original Phase 1.5 plan made
> *native document comments* the canonical review surface: emit an
> anonymized `.docx` / `.xlsx` / `.pptx` / `.pdf` with a Word/Excel/PowerPoint/PDF
> comment per detection, reviewer works inside the native app, `commit-review`
> parses the edited file. WS1 and part of WS2 (DOCX) shipped on that model.
>
> That direction is wrong for the primary UX. Native comments give a poor
> accept/reject rhythm (too many clicks, weak batching, reviewer attention
> drifts to comment chrome instead of the document), and they split
> pseudonymize state awkwardly between file-resident trailers and the
> Sanctum-local mapping store. Round-tripping decisions out of edited Office
> files is mechanically possible but adds complexity that a Sanctum-owned
> surface avoids.
>
> **New direction.** Phase 1.5 is now organized around a **Sanctum-owned
> review surface backed by the localhost API**. The API server owns review
> state (sessions, proposals, decisions, staged pseudonym mappings); a
> minimal keyboard-first UI projects that state. Native Office comments
> become a *fallback / export / interop* path, not the workflow.
>
> What survives from the old plan: the review-domain models, per-detection
> replacement metadata, trailer helpers, the DOCX comment emit/read work
> (now a one-way export adapter), and the `--no-review` escape hatch.
> What's demoted: comment-first review as the canonical UX, `commit-review`
> via edited-Office-file parsing, and the per-format native-comment
> matrix (WS3-WS5 of the old plan).

---

## Context

Phase 1 ships document anonymization as a fire-and-forget pipeline: Reader → analyze → operator → Writer, and the output is the final file. That leaves no surface where a legal/consulting pro can verify Sanctum's detections or catch PII Sanctum missed *before* the document is shared.

Phase 1.5 inserts the review gate. It's a separate phase (not a Phase 1 workstream) for two reasons:

1. **Scope shape.** Review is a product surface with its own domain model (sessions, proposals, decisions, staged mappings) and its own client (a UI). Squeezing it into Phase 1 as a single workstream would make that WS larger than the rest of the phase combined.
2. **Dependencies.** It depends on Phase 1 WS2 (adapters), WS3 (mapping store), and WS4 (API) all being in place. Those are the contract surfaces Phase 1.5 extends.

### Why review matters — the failure mode

False negatives are the failure mode that actually hurts: a missed SSN is a privacy breach; a false positive is just noise. A checklist-style review UI (*"accept or reject these 47 detections"*) makes this **worse** — it collapses the reviewer's attention onto what Sanctum found, and anything Sanctum missed stays invisible. The review frame has to be *"verify this document is safe to share,"* not *"approve Sanctum's list."*

That means the review surface has to render the **document in context**, with detections highlighted *inline* and very low-friction actions for the reviewer's own marks (misses, edits). The native document rendered by Word/Excel/PowerPoint/PDF was one candidate for that context; the reframe moves to a Sanctum-rendered context instead, because the keyboard-first accept/reject/edit/mark-missed rhythm is where the review actually lives or dies.

### Shape of the flow (reframed)

1. `sanctum process-file <input> --review` (default on) parses the document, runs analysis, and produces **anonymization proposals** with structured metadata (`detection_id`, `entity_type`, `score`, `original`, `replacement`, stable anchor into the source segments).
2. Proposals land in a **server-side review session** keyed by a session id. The input document, its parsed segments, and the staged pseudonym mappings (if the operator is pseudonymize) all live in the session — not in the output file.
3. The user opens the Sanctum review UI (served by the same localhost API). UI renders each segment in context with detections highlighted. Keyboard-first actions: **Accept / Reject / Edit replacement / Mark missed span**.
4. User commits the session. `POST /review-sessions/{id}/commit` writes the final document (trailer-free, no Sanctum metadata leakage) and, for pseudonymize, flushes staged mappings into the encrypted `MappingStore`.
5. `--no-review` escape hatch preserves fire-and-forget behaviour for automation/CI pipelines — bypasses session creation, writes the final file directly, and (for pseudonymize) commits mappings immediately.

**Pseudonymize is the one operator whose final output includes local persistent state that must only be committed post-human-approval.** Non-persistent operators (`replace`, `redact`, `mask`, `hash`, `encrypt`, `hips`) also run through sessions for review, but their "commit" is just "write the file" — no mapping-store side effect.

### Native-comment export (demoted, still supported)

The DOCX emit/read work already done is repurposed: `sanctum export-review <session-id> --format docx-comments` produces a DOCX with native Word comments for interchange with reviewers who *want* to stay in Word, or for archival. This is **not** the canonical workflow; it's a one-way export. Round-tripping decisions back from edited Office comments is deferred indefinitely.

Tracked as issues **#11** (overall), **#16** (reframe). Track Changes (DOCX rejection ergonomics) deferred to **#12** for a future phase.

---

## Architectural Guardrails (inherit from Phase 1 + additions)

All Phase 1 guardrails apply — hexagonal purity, air-gap, round-trip fidelity, test pyramid, real binary fixtures. Additional rules specific to Phase 1.5:

- **Server owns review state.** The session (segments, proposals, decisions, staged mappings) lives in the API layer, not smuggled into the output file. The UI is a dumb projection.
- **Review-session store is local-only.** Session state persists in `~/.sanctum/sessions/<session-id>/` (JSON + the input doc's raw bytes). Treat it as sensitive plaintext: tighten permissions (0600), clear on commit, document the on-disk retention policy loudly.
- **Committed output is leakage-free.** The file written by `POST /commit` must contain zero Sanctum metadata — no trailers, no hidden comments, no residual annotations. Integration test greps for `sanctum:` and fails on any hit.
- **Single-commit semantics.** Each session commits exactly once. A second commit on the same session returns 409 Conflict with a clear error.
- **UI is a reference implementation.** The API contract is the committed surface; the bundled UI is a minimal reference client (keyboard-first, zero-dependency-ish) so the project isn't blocked on a larger frontend workstream to ship HITL review.
- **Comment export is one-way.** `export-review` emits Office comments for interchange; there is no supported path to re-ingest an edited Office file back into a session.

---

## Workstream 1 — Foundation *(shipped — WS1)*

*Status: merged in PR #13. Retained as-is; its outputs are the substrate the reframed workstreams build on.*

Format-agnostic plumbing. Shipped the `ReviewWriter` protocol (`emit_review` method on `StructuredDocumentWriter`), the `ReviewComment` and `StagedMapping` models, per-detection replacements threaded through `AnonymizationResult`, the `commit_review` engine method skeleton, the CLI `--review` flag, and the `/commit-review` API endpoint.

### What stays load-bearing after the reframe
- Review-domain models (`ReviewComment`, `StagedMapping`) — promoted to the session state schema (see WS2 below). `ReviewComment` is generalized to `ReviewProposal` in WS2 (rename, not a rewrite).
- Per-detection replacement metadata on `AnonymizationResult` — used unchanged.
- Trailer serialize / parse helpers (`sanctum/documents/review.py`) — now scoped to the native-comment export path (see WS5); not used on the server-backed review surface.
- Detection-id hashing — reused unchanged as the stable anchor id.

### What gets re-scoped by later workstreams
- `commit_review()` engine method — re-shaped in WS4 to consume a session id instead of a reviewed file path. Operator-mismatch and attestation semantics carry over.
- `/commit-review` API endpoint — folded into `/review-sessions/{id}/commit` in WS2 (keep the old path as a thin alias that 308-redirects through one release cycle if any caller depends on it).
- CLI `--no-review` / `--review` flags — unchanged.

---

## Workstream 2 — Review-session domain + API *(primary reframe WS)*

The server-side surface that makes reviews first-class. Land this before any UI work.

### Files
- `sanctum/core/models.py` — new `ReviewSession` (id, input_path, segments, proposals, decisions, staged_mappings, operator, status: `open|committed|abandoned`, timestamps). Rename `ReviewComment` → `ReviewProposal` for clarity (sessions don't emit comments on the primary path).
- `sanctum/core/review/` — NEW package for review-domain logic:
  - `session.py` — `ReviewSession` state transitions, invariants (one-commit, operator consistency, proposal ↔ decision matching).
  - `proposals.py` — build `ReviewProposal` list from an `AnonymizationResult` + `StructuredDocument` (uses the WS1 per-detection replacements).
  - `store.py` — local session persistence (`~/.sanctum/sessions/<id>/`, 0600 perms, JSON manifest + raw input bytes).
- `sanctum/core/engine.py` — `create_review_session(input, operator, ...) -> ReviewSession`; `commit_review_session(session_id) -> Path` (returns the final output path). Old `commit_review(file_path, store)` kept as a thin wrapper for one release cycle; emits a deprecation warning.
- `sanctum/core/exceptions.py` — `ReviewSessionNotFoundError`, `ReviewSessionAlreadyCommittedError`, `ReviewSessionInvalidDecisionError` (decision references unknown proposal id, etc.). Keep `CommitReviewOperatorMismatchError` from WS1.
- `sanctum/api/routes/review_sessions.py` — NEW: `POST /review-sessions`, `GET /review-sessions/{id}`, `PATCH /review-sessions/{id}/decisions/{proposal_id}`, `POST /review-sessions/{id}/decisions/user-added`, `DELETE /review-sessions/{id}/decisions/user-added/{id}`, `POST /review-sessions/{id}/commit`, `DELETE /review-sessions/{id}` (abandon).
- `sanctum/api/routes/process_file.py` — `review=true` default now creates a session and returns `{session_id, review_url}` instead of returning the anonymized file inline. `review=false` preserves Phase 1 inline-response behaviour.

### Approach
- **Session id.** UUID4. The on-disk session directory name is the id.
- **Proposal shape.** `{id, segment_anchor, entity_type, score, original, replacement, operator}`. `segment_anchor` is an opaque structural pointer (adapter-specific: paragraph+run index for docx, sheet+cell for xlsx, etc.) that the UI uses to render the detection inline against the segment text returned in the session payload.
- **Decision shape.** `{proposal_id, status: accept|reject|edit, edited_replacement?}` for Sanctum-originated proposals; `{kind: user_added, segment_anchor, entity_type, original, replacement}` for reviewer-contributed spans. Decisions overwrite prior decisions on the same proposal (last write wins).
- **Staged pseudonym mappings.** For pseudonymize sessions, each accepted/edited decision stages a `StagedMapping` in session state. `POST /commit` flushes all staged mappings to the `MappingStore` via `get_or_create` (so cross-document reuse just works) then writes the final file.
- **Operator guard** — the commit endpoint re-checks the session operator against the store semantics: pseudonymize flushes mappings; everything else just writes the file.
- **Air-gap.** Session store is local filesystem only. No network calls introduced.

### Tests
- `tests/unit/test_core/test_review_session.py` — state transitions, one-commit invariant, decision overwrite semantics, operator-mismatch error.
- `tests/unit/test_core/test_proposals.py` — proposals build deterministically from a fixed `AnonymizationResult` + `StructuredDocument`; proposal ids stable under re-run.
- `tests/unit/test_core/test_session_store.py` — directory perms (0600), round-trip serialize/deserialize, abandon cleans up.
- `tests/integration/test_api_review_sessions.py` — full HTTP flow: create session, list proposals, patch decisions, commit, verify final file matches decisions; verify `--no-review` bypass still works.

### Troubleshooting hotspots
- **Session store growth.** Input bytes live in the session dir until commit/abandon. Document the retention policy; optional `sanctum sessions prune --older-than 7d` CLI. Don't auto-delete without the user's say-so.
- **Concurrent decisions.** Two PATCHes to the same proposal — last-write-wins is fine for a single local user but document it. No locking.
- **Attestation.** `POST /commit` requires `attested: true` in the body; reject with 400 otherwise. Mirrors the WS1 contract.

### Verification
- `POST /review-sessions` with a fixture `.docx` returns a session id; `GET` returns the proposal list with per-detection replacements.
- `PATCH` each decision, then `POST /commit` → final file written, trailer-free (`grep sanctum:` → zero hits), pseudonymize store populated.
- `--no-review` on `process-file` still returns the final file inline; session never created.

---

## Workstream 3 — Minimal review UI

The keyboard-first reference client. Deliberately minimal — shipping HITL review must not be blocked on a larger frontend workstream.

### Files
- `sanctum/api/static/review/` — NEW: single-page UI (HTML + vanilla JS + small CSS; no build step). Served by the Flask app from the same localhost origin.
- `sanctum/api/routes/review_ui.py` — NEW: `GET /review/{session_id}` serves the SPA shell with the session id baked in.
- `sanctum/cli/commands.py` — `sanctum process-file ... --review` now prints the review URL (`http://127.0.0.1:<port>/review/<session-id>`) and, with `--open`, opens the default browser.

### Approach
- **Zero build step.** Plain HTML + ES-module JS + fetch. The project is Python-first; we do not pull in a frontend toolchain for the reference UI. If a future phase wants a richer client, it can replace `static/review/` without changing the API contract.
- **Layout.** Segment list on the left (document in context), detection detail + actions on the right. Keyboard shortcuts:
  - `j / k` — next / prev detection
  - `a` — accept
  - `r` — reject
  - `e` — edit replacement (opens input)
  - `m` — mark a selected span as a missed entity (opens entity-type picker)
  - `⌘↩ / Ctrl+Enter` — commit session (with attestation confirm modal)
- **Rendering in context.** Render the document segments as plaintext blocks with detection spans highlighted. Fidelity to the native Word rendering is not the goal — clarity of *what's being decided* is.
- **No server push.** Poll `GET /review-sessions/{id}` on focus, or fetch on demand after each action. Keep it simple.
- **Testing.** Playwright smoke test that drives the flow end-to-end against the fixture corpus. Keep the unit-test surface thin — the UI is deliberately simple logic.

### Tests
- `tests/integration/test_review_ui.py` — Playwright: open session, navigate, accept/reject/edit/mark-missed, commit; asserts on the final file match the decisions.
- `tests/unit/test_api/test_review_ui_route.py` — `GET /review/{id}` returns the SPA shell with the session id injected; 404 on unknown id.

### Troubleshooting hotspots
- **Browser opens to loopback.** Document the `127.0.0.1` vs `localhost` choice (Phase 1 WS4 pinned `127.0.0.1` for air-gap reasons — keep that).
- **Port collisions** — the API port is already configurable; re-use that setting.
- **Long documents.** Virtualize the segment list if the fixture corpus exposes a scrolling problem; otherwise leave it plain.

### Verification
- `sanctum process-file fixtures/sample.docx --review --open` launches Sanctum API, opens the browser to the review URL, user accepts/rejects a few, commits, final `.docx` lands at the output path trailer-free.
- Keyboard-only flow reaches commit without touching the mouse.
- Playwright test green.

---

## Workstream 4 — Pseudonymize commit via session

Wires the session-commit path to the encrypted `MappingStore` so pseudonymize mappings are persisted only after human approval.

### Files
- `sanctum/core/engine.py` — `commit_review_session()` (introduced in WS2) grows the pseudonymize branch: flush `session.staged_mappings` through `MappingStore.get_or_create` before writing the final file.
- `sanctum/anonymizer/operators/pseudonymize.py` — review-mode behaviour: in `review=True` paths, generate the pseudonym deterministically and stash it in the `StagedMapping` on the session; do **not** touch `MappingStore` yet.
- `sanctum/cli/commands.py` — `sanctum commit-review <session-id>` subcommand (session-scoped, not file-scoped). Old file-scoped `commit-review <file>` kept as a thin shim for one release with a deprecation warning.

### Approach
- **Deterministic pseudonym generation** — Faker seed derived from `(document_hash, entity_type, original)`. Reproducible across re-runs of `process-file` on the same input before commit.
- **Cross-document reuse.** `MappingStore.get_or_create` guarantees that if `(entity_type, original)` already has a pseudonym from a prior committed session, the new session reuses it on accept. Edits override.
- **User-added spans in UI.** When the reviewer marks a missed span as `PERSON` (for example), the session records a `user_added` decision; on commit, pseudonymize sessions generate a pseudonym using the same seed family and persist it through the same `get_or_create` path.
- **Attestation** — required at commit time (WS2 contract). Non-interactive attestation flows (CI, automation) must pass `attested=true` explicitly; log a flagged telemetry event.
- **Dedupe.** If a user-added span's `(entity_type, original)` duplicates a Sanctum-originated proposal in the same session, the Sanctum proposal wins; warn in the response.

### Tests
- `tests/integration/test_pseudonymize_session_commit.py`:
  1. `POST /review-sessions` with pseudonymize on a fixture — store unchanged on disk.
  2. PATCH decisions (accept / reject / edit / user-added).
  3. `POST /commit` — store populated with accepted + edited + user-added; rejected decisions absent; final file trailer-free.
  4. Second session on a second fixture sharing entities — reuses existing pseudonyms from the store.
  5. `grep sanctum:` on the final output → zero matches.
- `tests/unit/test_anonymizer/test_pseudonymize_review_mode.py` — review-mode staging never touches `MappingStore`.

### Troubleshooting hotspots
- **Double-commit.** `POST /commit` on an already-committed session returns 409.
- **Session abandoned after staging.** Abandon must delete the session dir and leave `MappingStore` untouched. Integration test for this.
- **Clock skew on session timestamps** — use UTC consistently; document the field.

### Verification
- Full session flow on pseudonymize: `process-file --operator pseudonymize --store /tmp/map.sanctum --review` → session URL → UI actions → commit → store populated, file trailer-free.
- `sanctum mapping reverse <pseudonym>` retrieves the original after commit.
- Abandoning a session before commit leaves `MappingStore` untouched.

---

## Workstream 5 — Native-comment export *(demoted interop path)*

Repurposes the DOCX comment emit/read work (partial in PR #14) as a **one-way export** for interchange with reviewers who want to stay in Word, and for archival. Not the canonical review surface.

### Files
- `sanctum/documents/docx_adapter.py` — keep `DocxWriter.emit_review`. **Remove** `DocxReader.read_review_decisions` (and its planned XLSX/PPTX/PDF siblings) — there is no supported path to re-ingest an edited Office file.
- `sanctum/cli/commands.py` — `sanctum export-review <session-id> --format docx-comments --out <path>` subcommand. Reads the committed session's final document + decisions and emits the native-comment view for the supported formats.
- `sanctum/api/routes/review_sessions.py` — `GET /review-sessions/{id}/export?format=docx-comments` (post-commit only).

### Approach
- **Supported formats on day one.** DOCX only (WS2 of the old plan shipped most of the code). XLSX/PPTX/PDF native-comment export is **deferred** — their value post-reframe is lower, and the OOXML / annotation complexity in the old WS4/WS5 is no longer worth paying before a user actually asks for it.
- **Export is derivative.** Export never mutates the committed file; it writes a new artifact at `--out`. No in-place edits.
- **Trailer format retained** for the export view (so an external tool *could* parse an exported DOCX's comments if needed), but Sanctum itself does not round-trip from an edited export.

### Tests
- `tests/integration/test_docx_comment_export.py` — committed session → `export-review --format docx-comments` → Word-openable file with one comment per accepted/edited decision, anonymized body.
- The existing DOCX review unit tests from PR #14 migrate to the `export` namespace; rejection-parse and user-added-parse tests are removed (no re-ingest path).

### Troubleshooting hotspots
- **PR #14 in flight.** Close out PR #14's remaining substeps by re-scoping them as export (not review): keep emit, drop parse. Migrate tests. Update PR title and body.
- **Existing Word comments on input** — export must still pass them through unmodified.

### Verification
- `sanctum export-review <id> --format docx-comments --out /tmp/out.docx` → opens in Word; anonymized text + one Sanctum comment per decision; any existing Word comments preserved.
- XLSX/PPTX/PDF export returns a clear `UnsupportedExportFormatError` pointing to the roadmap.

---

## Critical Files Being Modified (summary)

- `sanctum/core/models.py` — add `ReviewSession`, `ReviewProposal` (rename from `ReviewComment`), keep `StagedMapping` (WS2).
- `sanctum/core/review/` — NEW package: `session.py`, `proposals.py`, `store.py` (WS2).
- `sanctum/core/engine.py` — `create_review_session`, `commit_review_session` (WS2, WS4); keep/wrap `commit_review` one release.
- `sanctum/core/exceptions.py` — new session errors (WS2).
- `sanctum/api/routes/review_sessions.py` — NEW endpoint family (WS2); export route (WS5).
- `sanctum/api/routes/process_file.py` — review-on default returns session id + URL (WS2).
- `sanctum/api/routes/review_ui.py` + `sanctum/api/static/review/` — reference UI (WS3).
- `sanctum/anonymizer/operators/pseudonymize.py` — session-scoped staging (WS4).
- `sanctum/documents/docx_adapter.py` — keep `emit_review`, drop `read_review_decisions` (WS5).
- `sanctum/documents/review.py` — trailer helpers now scoped to export only (WS5).
- `sanctum/cli/commands.py` — `--review`/`--no-review` (kept), `--open`, `commit-review` session-scoped, new `export-review`, deprecation shim for file-scoped commit (WS2, WS3, WS4, WS5).
- `README.md` — Phase 1.5 roadmap rewritten (separate commit).
- `tests/integration/test_api_review_sessions.py`, `test_review_ui.py`, `test_pseudonymize_session_commit.py`, `test_docx_comment_export.py`.

---

## Order of Execution & Milestones *(revised)*

1. **M0 — Foundation *(shipped)*.** WS1 — protocol, models, engine scaffolding, CLI/API plumbing, per-detection replacements. Merged in PR #13.
2. **M1 — WS2 rescope.** Before writing new code:
   - Close out **PR #14** by re-scoping it: keep `DocxWriter.emit_review`; drop `DocxReader.read_review_decisions` and the round-trip parse tests. Update PR title/body to "DOCX native-comment export (Phase 1.5 WS5)". Land it.
   - Rename `ReviewComment` → `ReviewProposal`.
3. **M2 — Review-session domain + API (WS2 new).** Ship session lifecycle, endpoints, persistence, `process-file` integration. Unblocks UI + commit.
4. **M3 — Minimal UI (WS3).** Ship the reference client. Once this lands the primary review UX is usable.
5. **M4 — Pseudonymize commit (WS4).** Wire session commit to `MappingStore` + user-added pseudonym generation.
6. **M5 — DOCX comment export (WS5).** Expose the preserved emit path via `export-review`.

Each milestone closes with: updated README roadmap checkboxes, a CHANGELOG entry, and an evaluation-harness run to confirm no regression on the Phase 1 corpus.

Deferred (out of Phase 1.5 unless a user asks): XLSX / PPTX / PDF native-comment export. PPTX was already the highest-risk item in the old plan (no python-pptx comment API, OOXML work); pausing it here is consistent.

---

## Verification (end-to-end, post-Phase-1.5)

1. `pip install -e ".[documents,security,api,ci]"` — clean install, no new extras introduced.
2. `pytest` — unit + integration green; coverage ≥ gate.
3. `pytest tests/evaluation/ -m evaluation` — no regression vs Phase 1 baseline.
4. **Session happy path (replace):** `sanctum process-file fixtures/sample.docx /tmp/out.docx --operator replace --review --open` → browser opens to review URL → accept all → commit → `/tmp/out.docx` is anonymized and trailer-free.
5. **Session pseudonymize round-trip:** same, `--operator pseudonymize --store /tmp/map.sanctum`. Before commit: store unchanged. After commit: store populated; `sanctum mapping reverse <pseudonym>` returns original.
6. **Reject path:** reject a proposal in the UI → commit → final file keeps the original text at that detection; no mapping recorded.
7. **User-added span:** mark a missed name as `PERSON` in the UI → commit → pseudonymize sessions record the user-added mapping; non-persistent operators just apply the chosen replacement.
8. **Escape hatch:** `--no-review` produces byte-equivalent output to Phase 1 WS2 on the existing fixtures.
9. **Leakage check:** grep the final committed file for `sanctum:` — zero matches. Integration test enforces.
10. **Air-gap check:** full Phase 1.5 pipeline runs with network disabled.
11. **Comment export:** post-commit `sanctum export-review <id> --format docx-comments --out /tmp/reviewed.docx` opens in Word with one comment per accepted decision; original input comments preserved.

---

## Relationship to other work

- **Phase 1 WS2 (Document Adapters)** is the contract Phase 1.5 extends. Parsing stays authoritative; review doesn't re-implement segment walking.
- **Phase 1 WS3 (Mapping Store)** is written to at session commit. No store schema changes expected.
- **Phase 1 WS4 (Flask API)** hosts the new session endpoints and the reference UI static files.
- **Issue #11** — overall Phase 1.5 workstream.
- **Issue #16** — the reframe (this plan). Lands as part of the WS2-new commit that renames `ReviewComment → ReviewProposal`.
- **Issue #12** (Track Changes for DOCX) — was justified primarily to compensate for comment-rejection ergonomics. The reframed UI removes that pressure; #12 remains a future enhancement to the *export* path, lower priority than originally scoped.
