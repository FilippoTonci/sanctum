# Changelog

All notable changes to the Sanctum Python backend are documented in this
file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project follows semantic versioning once a stable release is cut (currently
pre-1.0: breaking changes can land in any `0.x.y` bump, but every PR still
records its API impact here).

This changelog covers **the Python backend** in this repository
(`sanctum`). The Phase 3 Electron desktop app (`sanctum-desktop`) maintains
its own changelog in its own repository. Each `sanctum-desktop` release
pins a specific `sanctum` commit — see `plans/phase-3-desktop-ui.md` for
the atomic-installer contract.

### Guidance for contributors

Every pull request that changes the HTTP API, the CLI, the on-disk
session/mapping-store format, or any other externally-visible contract
MUST update this file under `## [Unreleased]` with an entry in the
appropriate category. Internal refactors and test-only changes can omit.

Categories (from Keep a Changelog):

- **Added** — new features, new endpoints, new fields, new CLI subcommands.
- **Changed** — non-breaking behaviour changes to existing features.
- **Deprecated** — features still present but planned for removal.
- **Removed** — features taken out this release.
- **Fixed** — bug fixes visible to callers.
- **Security** — vulnerability fixes or hardening measures.

The contract-compat check (Phase 3 WS1 substep 6) refuses PRs that remove
endpoints, remove request fields, add required request fields, or narrow
response fields without a corresponding `## [Unreleased]` entry explaining
the break.

---

## [Unreleased]

### Changed

- **BREAKING (request)**: `POST /review-sessions/{id}/decisions/user-added`
  now requires `start: int` and `end: int` fields on the request body.
  Old clients that send only `original` will be rejected with 400. The
  offsets describe where in the segment text the user-added span lies;
  the route also validates that `segment.text[start:end] == original`
  so a stale offset cannot silently flag the wrong span at commit time.
  Surfaced by Phase 3 WS4 (desktop review surface): an `original` string
  alone is ambiguous when the segment contains the same string twice
  (e.g. *"Smith met Smith"*) and the commit-time `str.find` would always
  pick the first occurrence regardless of which one the user marked.
  (Phase 3 WS1.5 substep 1.)

### Added

- `GET /review-sessions` — newest-first index of every persisted
  review session, projected into a thin `ReviewSessionIndexEntry`
  shape (id, source_path, format, status, created_at, committed_at,
  accepted/rejected/pending counts). Used by the desktop's Recent
  Sessions landing page; one round-trip replaces the alternative
  of caching session ids client-side and N+1 GET-by-id calls. A
  manifest that fails to load is logged and skipped — one bad
  session won't blank the whole listing. (Phase 3 WS1.5 substep 2.)
- `start: int` and `end: int` fields on `ReviewProposal` and
  `UserAddedDecision` (response side). Carried verbatim from the
  originating `DetectionResult` for analyzer-derived proposals; supplied
  by the request body for user-added decisions. Lets the UI highlight
  the exact span without re-running `str.find(original)`. (Phase 3
  WS1.5 substep 1.)
- `/health` now returns `sanctum_commit`: the build-time commit SHA of
  the bundled backend (or `"dev"` when unset). The Phase 3 desktop app
  compares this against the SHA it was built with to detect corrupt
  installs or manually-swapped sidecars. Source: `SANCTUM_COMMIT`
  environment variable, consumed by `sanctum._build_info.commit()`.
  (Phase 3 WS1 substep 1.)
- `/health` now returns `openapi_digest`: a 12-char SHA-256 prefix of
  the committed `schema/openapi.json`. The desktop compares this to
  the digest of the spec it generated its TypeScript client from; a
  mismatch flags a contract-drift bug before the user hits it.
  (Phase 3 WS1 substep 2.)
- `schema/openapi.json`: OpenAPI 3.1 description of the Sanctum HTTP
  API, generated from the Pydantic request/response models and a
  single declarative route list in `scripts/generate_openapi.py`.
  Consumed by `sanctum-desktop` to build its typed HTTP client.
  CI regenerates and diffs; any drift fails the build.
  (Phase 3 WS1 substep 2.)
- `CHANGELOG.md` itself — this file. (Phase 3 WS1 substep 1.)
- `sanctum serve --port 0` now OS-allocates a free port and reports it
  on the `SANCTUM_READY host=... port=... token_path=...` line emitted
  to **stdout** before the accept loop starts. Human-readable status
  lines (the rich-console banners) moved to **stderr** so stdout stays
  clean for subprocess parsers — chiefly the Electron sidecar
  lifecycle in the Phase 3 desktop app. (Phase 3 WS1 substep 3.)

- `sanctum serve --token-stdin`: read the bearer token from stdin
  (one line, stripped) instead of generating/reading
  `~/.sanctum/api-token`. Stdin is closed after the read so a crashed
  parent can't re-leak the token on pipe resume. Mutually exclusive
  with `--token-path`. Under this mode the ready line reports
  `token_source=stdin` instead of a `token_path=`. Used by the Phase 3
  Electron sidecar lifecycle to keep the token off disk and out of
  process lists. (Phase 3 WS1 substep 4.)

- `scripts/check_api_compat.py`: compares a baseline OpenAPI spec
  against the current one and exits non-zero on any break (removed
  route, removed schema, removed property, newly-required request
  field). CI runs this against `origin/<base_ref>:schema/openapi.json`
  on every PR; passes silently when the contract is unchanged or
  strictly extended. Additive changes (new routes, new optional
  fields, new response fields) are allowed without fanfare.
  (Phase 3 WS1 substep 6.)

### Fixed

- `sanctum serve` now has integration-test coverage for SIGTERM:
  the server exits cleanly within 10 s, releases its bound socket,
  and runs the `finally` block that locks any unlocked mapping
  store. Regression safety net for the WS1 refactor to
  `waitress.server.create_server + server.run()`. (Phase 3 WS1 substep 5.)

### Changed

- `sanctum.api.server.run()` now accepts an `on_ready` callback that
  fires after the listener binds its socket but before the accept
  loop begins. Existing callers that don't pass the kwarg are
  unaffected. Internal refactor from `waitress.serve` to
  `waitress.server.create_server + server.run()` to enable this. The
  HTTP contract is unchanged.

---

## [0.1.0] — unreleased

Initial pre-release. Covers Phase 0 (foundation), Phase 1 (document
processing + API), and Phase 1.5 (review workflow). See `plans/` for
the phase-by-phase breakdown.
