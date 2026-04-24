# Sanctum — Phase 3 Implementation Plan: Desktop UI (sanctum-desktop Electron app)

> **Repo boundary (load-bearing).** Phase 3 ships the end-user GUI. The GUI
> lives in a **separate repository** — `sanctum-desktop` — with its own
> release cadence, its own CI, its own issue tracker, and its own
> `CODEOWNERS`. **No Electron / TypeScript / Node code ever lands in this
> repo.** This repo (`sanctum`) contributes to Phase 3 only via its
> existing Python surface: a versioned HTTP API contract, an OpenAPI spec
> published from the Flask app, and a `sanctum serve` CLI shaped for an
> Electron sidecar to spawn.
>
> WS1 is the only workstream that touches this repo. WS2 through WS6 are
> opened as issues + PRs against `sanctum-desktop` once it exists. This
> plan is committed here because Phase 3 depends on — and reshapes — the
> `sanctum` API surface, and the split between the two repos is itself a
> decision the plan has to record.

> **MVP scope (load-bearing).** Phase 3 MVP ships **.docx review only**.
> `.xlsx`, `.pdf`, `.pptx` stay disabled in the UI (the API accepts them,
> but the desktop file picker filters them out and surfaces a "format
> coming in Phase 3.5" banner if a user drags one in). The review surface,
> the annotation model, and the packaging work are all validated against
> `.docx` first. Other formats are deferred to Phase 3.5.

---

## Context

Phase 1 ships the anonymization pipeline; Phase 1.5 ships the
review-session API (sessions, proposals, decisions, server-computed
previews, commit-time anonymization). Phase 1.5's original WS3 — a
minimal keyboard-first reference UI served by the Flask app — was
dropped (2026-04-24) in favour of this phase: a proper Electron desktop
app that consumes `/review-sessions` directly.

Phase 3 is the product's first public-facing surface. What ships here is
what a legal or consulting professional actually downloads, installs,
and runs. Everything Phase 0–1.5 built is internal plumbing until this
phase wraps a GUI around it.

### Why a separate repo

Three reasons, in order of weight:

1. **Toolchains diverge.** The `sanctum` repo is Python + pytest +
   mypy + ruff + pre-commit, with a coverage bar enforced against
   `sanctum/core/*`. The desktop repo is Node + TypeScript + Vite +
   Playwright, with its own lint/format chain. Mixing them forces CI
   matrices, dependency resolvers, and editor configs to fight each
   other for no architectural gain.
2. **Release cadence diverges.** The Python backend ships as an
   installable package + a bundled Python runtime. The Electron shell
   ships as signed platform installers (`.dmg`, `.msi`, `.deb`,
   `.AppImage`) through an auto-update channel. Those move at different
   speeds and go through different signing/notarization pipelines.
3. **Security boundary.** Keeping the renderer code — the only part of
   Sanctum that renders arbitrary user DOCX — in a separate repo forces
   the backend to treat it as an untrusted client. The Flask API's
   bearer-token + Host/Origin guards already do this; a single-repo
   layout tempts shortcuts.

### Why Electron, not Tauri

Decided in pre-phase research (see WS2 substep 1 for the full
justification). Short version: Sanctum's installer is dominated by the
Python sidecar + NLP models (~1.4 GB for spaCy large + GLiNER), so
Tauri's bundle-size advantage evaporates. "Same Chromium everywhere"
removes a class of .docx rendering bugs that would be hard to diagnose
across WebView2 / WKWebView / WebKitGTK. And the team is a Python shop —
a Rust main process is a maintenance tax with no offsetting benefit.

### Why docx-preview, not an editor model

The review surface renders the user's `.docx` **read-only**, with
detections highlighted inline and decisions captured on the side. Two
architectural paths were considered:

- **Path A — render-then-overlay.** A fidelity-preserving renderer
  (`docx-preview`) outputs HTML; a thin custom layer uses the CSS
  Custom Highlight API to paint accept/reject/pending highlights over
  the rendered runs. The backend remains the only source of truth for
  the document.
- **Path B — editor model.** A full editor (TipTap / Lexical / SuperDoc)
  parses the `.docx` into its own document model, uses its own
  comment/mark system, then re-emits the `.docx` on commit.

Path B violates Sanctum's hexagonal architecture: it duplicates the
Python structured-document pipeline in TypeScript and requires a
lossless DOCX↔editor-model round-trip that no OSS library
achieves today. Path A keeps the backend's per-run segments as the
single document model and restricts the renderer to paint-only.
**Phase 3 MVP ships Path A.**

SuperDoc was evaluated and rejected: AGPL-3.0 is incompatible with
Sanctum's commercial-distribution requirement, and the commercial
license is priced per-deal.

### Operating envelope (inherited from Phase 0–1.5)

- **Airgap.** No runtime network calls. Model downloads are install-time
  only, via a channel the user explicitly triggers. `HF_HUB_OFFLINE=1`
  and `TRANSFORMERS_OFFLINE=1` are set in the spawned sidecar env so a
  missing cache fails fast rather than attempting a network call.
- **Loopback-only backend.** The Flask API binds `127.0.0.1`. The
  Electron app's renderer process talks to it via HTTP with a bearer
  token supplied by the main process.
- **Local-only session state.** Review sessions (plaintext input bytes +
  proposals) live under `~/.sanctum/sessions/<id>/` with 0700/0600
  perms, unchanged from Phase 1.5. The desktop app does not introduce
  new on-disk state categories.

### Shape of the happy path (MVP)

1. User launches `sanctum-desktop`. The Electron main process picks a
   free TCP port, generates a random bearer token, spawns the bundled
   `sanctum serve` sidecar, and waits for a machine-readable "ready"
   line on stdout.
2. User drags a `.docx` into the app (or uses File → Open). The
   renderer posts `POST /review-sessions` with the file's path; the
   backend parses the document, runs analysis, and returns the
   session + proposals + seeded previews under the session default
   operator.
3. The renderer displays the document using `docx-preview` (patched to
   emit `data-segment-id` attributes on run-level spans). The Overlay
   layer walks the rendered DOM, maps each proposal's
   `segment_anchor + offset` to a DOM `Range`, and paints it via the
   CSS Custom Highlight API.
4. User reviews detection-by-detection with keyboard shortcuts
   (`j`/`k` step, `a` accept, `r` reject, `e` edit replacement,
   `m` mark missed span). Each action PATCHes the session; the
   response carries a fresh preview which updates the ghost text
   beside the highlight.
5. User clicks Commit. Renderer posts `POST /review-sessions/{id}/commit`
   with `attested: true` and an output path; backend writes the
   anonymized `.docx` and the session is deleted. The desktop app
   confirms completion and offers "open containing folder" /
   "review another document".

### Non-goals for Phase 3

- No in-app editing of the original document. The review surface is
  read-only; edits happen only to the replacement text of individual
  decisions.
- No cross-document workflows (batch processing, queues). One document
  per review session; one session at a time in the UI.
- No collaboration (multi-user, comments, assignments). Single local
  user.
- No cloud sync, telemetry, or analytics. Airgap invariant.
- No .pdf / .xlsx / .pptx review. Deferred to Phase 3.5.

---

## Architectural Guardrails

All Phase 0–1.5 guardrails apply unchanged. Additional rules specific to
Phase 3:

- **Repo boundary is one-way.** The desktop repo depends on the
  `sanctum` API contract; `sanctum` does **not** depend on, link to, or
  import anything from `sanctum-desktop`. No tests in this repo spin up
  the Electron app; no build step in this repo reaches into the
  desktop repo. The OpenAPI spec published by this repo is the only
  shared artefact.
- **API is the contract, not the implementation.** Breaking changes to
  any route under `/review-sessions`, `/mapping`, `/process-file`,
  `/analyze`, `/anonymize`, or `/health` require a major version bump
  and a deprecation window. The OpenAPI spec is regenerated in CI on
  every backend PR; any uncommitted diff fails the build.
- **Renderer is paint-only.** The Electron renderer never mutates the
  `.docx` locally. It renders, it captures decisions, it posts them to
  the backend, and the backend writes the output file. No in-browser
  DOCX editor.
- **Single document model.** The backend's per-run `TextSegment` +
  `RecognizerResult` offsets are the single document model. The
  renderer's DOM is a projection; it never produces offsets the
  backend did not issue.
- **Sidecar lifecycle is the main process's job.** The renderer never
  starts the Python backend directly, never reads `~/.sanctum/api-token`
  directly, and never talks to a backend it did not spawn. Main
  generates the token, passes it into the renderer via `contextBridge`,
  and kills the sidecar on quit.
- **Every long operation has a cancel path.** Document parsing,
  analysis, and commit can all take seconds-to-tens-of-seconds. Every
  UI state that waits on an HTTP call must expose a cancel button
  that aborts the `fetch` (via `AbortController`) and returns the
  session to `open` with no partial state.
- **i18n from day one.** No English strings in component source.
  `react-i18next` from the first commit; French catalog stubbed
  alongside English. The legal/consulting EU market is load-bearing for
  Sanctum's positioning.
- **Signed installers only.** No unsigned `.dmg` / `.msi` / `.AppImage`
  ever ships to users, even in pre-release channels. Windows
  SmartScreen + macOS Gatekeeper make unsigned builds effectively
  unusable.

---

## Workstream 1 — Backend contract hardening *(this repo)*

The only Phase 3 workstream in `sanctum`. Ships the API stability
guarantees and sidecar-friendly `serve` behaviour the desktop app
depends on.

### Substep list

1. **API versioning policy.** Decide and document (ADR in
   `resources/adr-0003-api-versioning.md`) whether the API is versioned
   via URL prefix (`/v1/...`) or via a `Sanctum-API-Version` request
   header. Apply to all existing routes in a single PR. Add a changelog
   entry format for future breaking changes.
2. **OpenAPI spec emission.** Generate `resources/openapi.json` from
   the Pydantic schemas + Flask route registration. Commit the
   generated file. CI re-generates and diffs; any uncommitted diff
   fails the build. This is the artefact the desktop repo consumes.
3. **`sanctum serve` ready-signal + port=0 support.** Add `--port 0`
   handling: bind an OS-allocated port, read it back, emit a
   machine-readable line `SANCTUM_READY host=127.0.0.1 port=<N>
   token_path=<path>` to stdout before entering the waitress loop.
   Keep the human-readable rich-console lines on stderr so Electron's
   stdout parser stays clean. Document in the CLI help.
4. **Token delivery over stdin.** Add `--token-stdin` flag: instead of
   reading/writing `~/.sanctum/api-token`, read the token from stdin on
   startup. This keeps the token out of process lists (`ps auxf`) and
   off disk when spawned by Electron. Existing `--token-path` behaviour
   is preserved for CLI users.
5. **SIGTERM cleanup audit.** Extend the existing SIGTERM handler to
   cover the new failure modes (sidecar killed mid-commit). Ensure
   session directories for in-flight commits are cleaned up or marked
   recoverable; ensure mapping-store flock is always released. Add an
   integration test that sends SIGTERM mid-request and verifies no
   locked state remains.
6. **Deprecation / compatibility harness.** Lightweight runner script
   `scripts/check_api_compat.py` that loads the committed `openapi.json`
   from `main` and compares it to the current branch's spec; flags
   removed endpoints, removed request fields, added required request
   fields, narrowed response fields. Wire into CI as a warning at
   first, an error after the first `sanctum-desktop` release.

### Files (this repo)

- `sanctum/api/app.py` — add OpenAPI spec generation endpoint (dev-only,
  gated on `SANCTUM_DEV=1`) and versioning middleware.
- `sanctum/api/schemas.py` — unchanged; already Pydantic, already the
  OpenAPI source of truth.
- `sanctum/cli/commands.py::serve` — add `--port 0` handling,
  `--token-stdin` flag, stdout ready-signal emission. Keep existing
  flags working.
- `sanctum/api/server.py` — expose the bound port back to the caller
  after `listen()` so `serve` can print the allocated port.
- `resources/openapi.json` — NEW, generated.
- `resources/adr-0003-api-versioning.md` — NEW, decision record.
- `scripts/generate_openapi.py` — NEW, emits the spec file.
- `scripts/check_api_compat.py` — NEW, CI helper.
- `.github/workflows/ci.yml` — add OpenAPI-generation diff check and
  compat check.

### Approach

- **Versioning**: URL prefix (`/v1/...`). Header versioning is more
  elegant but harder for the desktop app to debug in a browser devtools
  panel. Breaking changes move to `/v2/...` with a one-minor-release
  overlap window where both paths are live.
- **OpenAPI emission**: use `apispec[marshmallow]` or, preferable,
  Pydantic v2's `model_json_schema()` composed into an OpenAPI envelope
  by a small script. Do **not** adopt FastAPI to get this for free —
  the migration cost isn't worth it.
- **Port=0**: waitress accepts `port=0` and picks a free port; read it
  back via `server.effective_port` or equivalent after binding. The
  existing `assert_loopback(host)` path is unchanged.
- **Ready signal**: one line, key=value pairs, terminated with `\n`,
  flushed immediately. The desktop app parses it with a simple regex.
  If the process exits before emitting the line, the desktop app
  surfaces a sidecar-failed-to-start error.
- **Token over stdin**: close stdin after the read so a crashed main
  process can't accidentally leak the token on resume. Log only
  `token=<redacted>` in the ready line; the token itself never appears
  in logs.

### Tests

- `tests/unit/test_cli/test_serve_ready_signal.py` — subprocess test:
  spawn `sanctum serve --port 0 --token-stdin`, feed a token, parse
  the ready line, hit `/health` with the parsed port and the supplied
  token. Kill the process.
- `tests/unit/test_api/test_openapi_spec.py` — assert
  `resources/openapi.json` matches the live-generated spec; exercised
  in CI.
- `tests/integration/test_api_sigterm.py` — start server, begin a
  long-running commit, SIGTERM, assert session dir is either cleanly
  deleted or flagged recoverable and mapping-store flock is released.

### Verification

- `sanctum serve --port 0 --token-stdin <<< $(openssl rand -hex 32)`
  prints one `SANCTUM_READY host=... port=... token_path=...` line on
  stdout, nothing else, before blocking on requests.
- `python scripts/generate_openapi.py` produces a stable, diffable
  JSON file. Running it twice without source changes produces
  byte-identical output.
- `scripts/check_api_compat.py resources/openapi.json main` exits 0 on
  a no-op branch and exits non-zero if a required field is added or a
  route is removed.
- `grep -R "Sanctum-API-Version" sanctum/ resources/` surfaces the
  versioning header / prefix consistently.

### Troubleshooting hotspots

- **waitress + port 0 + loopback guard.** `assert_loopback` runs
  before the port is bound; port-allocation happens in waitress
  internals. Validate that the port we read back is actually bound
  and not a race against the loopback check.
- **OpenAPI generator drift.** Pydantic v2 emits slightly different
  JSON Schema between patch versions. Pin Pydantic in `pyproject.toml`
  and regenerate the spec deliberately on Pydantic bumps.
- **Token over stdin + Windows subprocesses.** `subprocess.Popen` on
  Windows handles stdin pipes correctly, but closing stdin too early
  can race the Python side's read. Write + close + flush in one shot
  from Electron main.

---

## Workstream 2 — sanctum-desktop repo scaffold *(new repo)*

Stand up `sanctum-desktop` and scaffold the Electron + Vite + React +
TypeScript shell. No product features yet — this WS is pure plumbing
so subsequent WSes have a working build, CI, and signing story.

### Substep list

1. **Repo bootstrap.** `sanctum-desktop` created under the same GitHub
   org as `sanctum`. `LICENSE` (MIT), `README.md` (positioning mirrors
   `sanctum` README), `CODEOWNERS`, `.gitignore`, `.editorconfig`,
   `CONTRIBUTING.md`.
2. **`electron-vite` scaffold.** `npm create @quick-start/electron` with
   the React + TypeScript template. Prune the boilerplate to a single
   empty window rendering a placeholder React component. Confirm
   `contextIsolation: true`, `nodeIntegration: false`,
   `sandbox: true` in `BrowserWindow` options.
3. **Lint / format / typecheck.** ESLint (`@typescript-eslint`,
   `eslint-plugin-react-hooks`, `eslint-plugin-jsx-a11y`) + Prettier +
   `tsc --noEmit` in pre-commit (husky + lint-staged) and in CI.
4. **CI pipeline.** GitHub Actions: `lint`, `typecheck`, `test-unit`
   (Vitest), `build` (cross-platform matrix: `macos-latest`,
   `windows-latest`, `ubuntu-latest`). Build uploads unsigned
   artefacts to job outputs for manual sanity checks.
5. **E2E harness.** Playwright + `electron` test API. A single smoke
   test: launch the app, assert the window opens, assert the
   placeholder component renders.
6. **Code-signing secrets wiring.** GitHub repo secrets placeholders
   (`APPLE_ID`, `APPLE_ID_PASSWORD`, `APPLE_TEAM_ID`,
   `AZURE_TRUSTED_SIGNING_CLIENT_ID`, etc.). Signing itself lands in
   WS6; this substep only wires the secret plumbing so WS6 isn't
   blocked.
7. **Procurement kickoff.** Open issues to start: Apple Developer
   Program enrollment ($99/year), Azure Trusted Signing eligibility
   check (US/Canada only as of late 2025 — if ineligible, fall back
   to a Sectigo/DigiCert EV cert with a YubiKey, 2–3 week lead
   time). Procurement is WS6's blocker; start it here.

### Files (sanctum-desktop repo, not this repo)

- `package.json` — `electron`, `electron-vite`, `electron-builder`,
  `react@19`, `react-dom@19`, `@types/react`, `vite`, `typescript`
  (pinned to latest stable), `eslint`, `prettier`, `vitest`,
  `@playwright/test`, `husky`, `lint-staged`.
- `electron.vite.config.ts` — three-pane config (main, preload,
  renderer).
- `electron-builder.yml` — NSIS (Windows), DMG (macOS), AppImage +
  deb (Linux). Target app IDs: `com.sanctum.desktop`.
- `src/main/index.ts` — single window, empty shell.
- `src/preload/index.ts` — empty `contextBridge.exposeInMainWorld`
  stub.
- `src/renderer/index.html`, `src/renderer/src/App.tsx` — placeholder
  UI.
- `.github/workflows/ci.yml` — lint, typecheck, unit, build matrix.
- `.github/workflows/release.yml` — draft workflow for WS6.
- `tests/e2e/smoke.spec.ts` — Playwright smoke test.
- `README.md`, `LICENSE`, `CODEOWNERS`, `CONTRIBUTING.md`.

### Approach

- **`electron-vite`**, not Electron Forge. Forge's Vite plugin is still
  flagged experimental as of Forge 7.5; `electron-vite` + `electron-builder`
  is the incumbent pair with 10 years of production use.
- **React 19**, stable as of Dec 2024. No RSC (there's no server), just
  the new-stable concurrent features and the `use()` hook for data
  loading where it fits.
- **No UI library yet.** Don't pick MUI / Chakra / Radix in this WS —
  that decision lands in WS4 where the review surface design actually
  drives it. Use plain CSS for the placeholder.
- **Playwright over Spectron**: Spectron is deprecated. `@playwright/test`
  with `electronApp.launch()` is the 2026 baseline.

### Tests

- `tests/e2e/smoke.spec.ts` — app launches, window shows, placeholder
  text visible. One test. Runs on all three OS in CI.
- No unit tests in this WS; real unit tests start with WS3.

### Verification

- `npm run dev` opens a window with "Sanctum — coming soon" or
  equivalent placeholder.
- `npm run build` produces `dist/` with a renderable bundle.
- `npm run make` (electron-builder) produces unsigned installers for
  the current OS.
- `npm run test:e2e` passes the smoke test locally.
- GitHub Actions CI green on a fresh PR.

### Troubleshooting hotspots

- **Sandbox + preload.** With `sandbox: true`, preload scripts cannot
  `require` Node modules. Use the `electron-vite` preload build
  pipeline which bundles a safe subset.
- **ESM vs CJS.** Electron main is CJS by default; `electron-vite`
  handles the ESM renderer cleanly but `electron` itself imports are
  CJS. Keep main-side imports CJS-compatible.
- **Node versions.** Pin Node via `.nvmrc` and `engines` in
  `package.json`. electron-builder is picky about minor versions.

---

## Workstream 3 — Python sidecar integration

Package the `sanctum` Python backend, spawn it from Electron main, and
expose the allocated port + bearer token to the renderer. Gates the
entire rest of Phase 3.

### Substep list

1. **PyInstaller onedir build.** Add a `scripts/build-sidecar.sh`
   (or equivalent) that runs PyInstaller against a dedicated entry
   point (`sanctum serve` wrapped) in **onedir** mode. Onefile is
   explicitly rejected because it re-extracts the bundle on each
   launch — unacceptable when models push the payload to hundreds of
   megabytes.
2. **Model bundling vs. download.** Standard tier: bundle spaCy
   `en_core_web_sm` (~15 MB) with the sidecar for a small default
   install. Professional tier: `en_core_web_lg` (~560 MB) + GLiNER
   (~820 MB) fetched on first-launch via a user-confirmed download
   flow (see substep 6). No models reach out to HuggingFace at
   runtime — `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` set in
   the spawned env.
3. **Sidecar lifecycle manager.** `src/main/sidecar.ts` in the
   desktop repo: `spawnSidecar()` picks a free port via
   `net.createServer().listen(0)`, generates a 32-byte random token,
   spawns the sidecar executable with `--port <n> --token-stdin`,
   pipes the token into stdin + closes it, parses the
   `SANCTUM_READY` line from stdout, and resolves with
   `{host, port, token}`. `killSidecar()` sends SIGTERM, waits 5 s,
   then SIGKILL.
4. **Health polling + splash screen.** After the ready line, poll
   `GET /health` with the bearer token until 200 OK (models may not
   be loaded yet; the ready line signals HTTP-up, `/health` signals
   engine-ready). Show a splash screen with progress while this
   happens — cold start can exceed 30 s on first launch with
   `en_core_web_lg`.
5. **Renderer exposure via contextBridge.** `src/preload/index.ts`
   exposes `window.sanctum = { baseUrl, token }`. No other sidecar
   surface reaches the renderer. The renderer constructs its own
   `fetch` calls using this config.
6. **Model download flow.** `src/main/models.ts`: if the user enables
   Pro tier in settings, trigger a one-shot HTTPS download from a
   Sanctum-owned CDN (**not** HuggingFace at runtime — we control the
   availability and version). Progress UI in a dedicated settings
   pane; written to `userData/models/`. This is the one exception to
   "no runtime network calls" and it requires an explicit user
   click.
7. **Graceful shutdown hooks.** `app.on('before-quit', ...)` waits
   for the sidecar to exit before allowing the app to close. If the
   sidecar hangs, surface an error dialog with "force quit" option
   that sends SIGKILL.
8. **Dev mode: use installed `sanctum`.** When `ELECTRON_DEV=1`,
   spawn `python -m sanctum.cli serve ...` from the parent
   `sanctum` repo checkout (via a relative path configured in
   `.env.local`) instead of the packaged sidecar. Unblocks backend
   iteration without rebuilding the sidecar binary.

### Files (sanctum-desktop repo)

- `src/main/sidecar.ts` — spawn / health-check / kill.
- `src/main/models.ts` — download / verify / extract model archives.
- `src/main/paths.ts` — `app.getPath('userData')`, `app.getPath('logs')`,
  `extraResources/sidecar/` path resolution.
- `src/preload/index.ts` — `contextBridge.exposeInMainWorld('sanctum', ...)`.
- `src/renderer/src/api/client.ts` — typed fetch wrapper built on
  `window.sanctum.baseUrl` + `window.sanctum.token`.
- `scripts/build-sidecar.sh` — PyInstaller onedir build invocation,
  runs from a pinned `sanctum` checkout (via git submodule or a
  Python venv install from a commit SHA).
- `electron-builder.yml` — `extraResources` entry pointing at the
  onedir output.
- `.env.example` — `ELECTRON_DEV=1`, `SANCTUM_DEV_REPO=../sanctum`.

### Approach

- **Onedir, not onefile.** Ship the PyInstaller output directory as-is
  under `extraResources/sidecar/`. electron-builder copies the whole
  tree into the installer. First launch reads files from disk
  directly — no extraction, no `/tmp` dance, startup measured in
  seconds not tens-of-seconds.
- **Token delivery**: stdin, not CLI flag. The token never appears in
  `ps auxf` or `lsof` output.
- **Port selection**: main process calls `server.listen(0)` on a
  dummy `net.createServer`, reads the allocated port, closes the
  server, then passes the port to the sidecar. There's a theoretical
  race where another process steals the port; acceptable for a
  single-user desktop app. If it becomes a real issue, the sidecar
  retries with `--port 0` and reports the OS-allocated port back via
  the ready line.
- **Sidecar binary per platform**: one PyInstaller build per
  `macos-x64`, `macos-arm64`, `windows-x64`, `linux-x64`. Built in the
  `sanctum-desktop` CI matrix, pulling a pinned `sanctum` commit.
  Not built in the `sanctum` repo — that repo does not know about
  desktop packaging.
- **Model download UX**: never auto-download. Always require an
  explicit "Download Professional-tier models (1.4 GB)" click with a
  disk-space + data-usage warning.

### Tests

- `tests/unit/main/sidecar.test.ts` — mock `child_process.spawn`,
  assert ready-line parsing, token-stdin write, kill-on-quit.
- `tests/integration/sidecar-live.test.ts` — requires a real sidecar
  binary on `$PATH`; skipped in CI unit lane. Spawns, posts to
  `/health`, kills. Runs in the build matrix after the sidecar is
  built.
- `tests/e2e/cold-start.spec.ts` — launch app, wait for splash to
  clear (max 60 s), assert the main window renders with
  "Ready" state.

### Verification

- First-launch splash shows, `/health` returns 200, splash closes.
- `window.sanctum.baseUrl` and `window.sanctum.token` are set in the
  renderer.
- Quitting the app kills the Python process (verified via `ps` on
  Linux/macOS, Process Explorer on Windows).
- Force-kill of the Electron main process leaves an orphaned Python
  process for at most 5 seconds (SIGTERM timeout).
- Cold-start time < 40 s on a reference laptop (Windows 10, 8 GB RAM,
  SSD) with `en_core_web_lg` bundled.

### Troubleshooting hotspots

- **PyInstaller + spaCy.** spaCy's data files are often missed by the
  default collector. Use `--collect-all spacy` and
  `--collect-data en_core_web_lg` (or whichever model is bundled).
- **PyInstaller + Presidio.** Presidio dynamically discovers
  recognizers. Use `--collect-submodules presidio_analyzer` +
  `--copy-metadata presidio_analyzer`.
- **Windows antivirus false positives.** PyInstaller-bundled
  executables frequently trip heuristic AV scanners. The EV
  code-signing cert (WS6) is the primary mitigation. As a short-term
  workaround, UPX-compression is off and the binary name is
  `sanctum-sidecar.exe` — not `sanctum.exe` — to reduce collision
  with other tools.
- **SIGTERM on Windows.** Windows doesn't have real SIGTERM; Node's
  `child.kill()` on Windows sends `WM_CLOSE` but Python processes
  don't honour it. The sidecar installs a Windows console control
  handler (in WS1 substep 5) that maps CTRL_CLOSE_EVENT to the
  SIGTERM cleanup path. Verify in the integration test.
- **Model download resume.** A 1.4 GB download on a flaky hotel
  Wi-Fi will fail partway. Use HTTP Range requests + `got` or
  `electron-dl` with resume support. No silent retries.

---

## Workstream 4 — .docx review surface

The product. Render a `.docx`, paint highlights, run the review loop,
commit. This is the most substantial WS by a wide margin and carries
the highest risk.

### Substep list

1. **docx-preview integration.** Install `docx-preview`, render a
   fixture `.docx` into a container div in a smoke test. Validate
   that tables, images, headers/footers, lists, and tracked changes
   render without crashing on ~10 representative fixtures (reuse
   existing Sanctum test fixtures under `tests/fixtures/docx/`).
2. **patch-package for segment-id emission.** Apply a local patch
   that makes docx-preview emit `data-segment-id="body/p0/r3"` (or
   the path-style ids the Python adapter uses — see
   `sanctum/documents/docx_adapter.py`) on each run-level `<span>`.
   This is the critical mapping hook: without it, nothing else in
   this WS works. Carry the patch in `patches/` and regenerate on
   upstream bumps. Spike this in the first 2 days of the WS; if
   docx-preview's internal structure makes it intractable, escalate
   to a fork.
3. **Segment-id ⇄ DOM map.** `src/renderer/src/review/segmentMap.ts`
   — after render, walk the container, collect all
   `[data-segment-id]` spans into a `Map<segmentId, HTMLSpanElement>`.
   Build a cumulative-offset index per span so a
   `(segment_anchor, start, end)` triple from the backend maps to a
   precise `Range` over the containing text nodes.
4. **CSS Custom Highlight API overlay.** `src/renderer/src/review/highlights.ts`
   — maintain four `Highlight` objects keyed by decision status:
   `pending`, `accepted`, `rejected`, `focused`. On each session
   update, recompute membership and reassign ranges. Styled via
   `::highlight(sanctum-pending) { background: rgba(...) }` etc.
5. **Detection tooltip.** `src/renderer/src/review/DetectionTooltip.tsx`
   — positioned with `@floating-ui/react` (MIT) anchored to the
   current focused `Range.getBoundingClientRect()`. Shows entity
   type, score, current replacement preview, accept/reject/edit
   buttons. Editing the replacement PATCHes the session with
   `custom_replacement: <literal>`.
6. **Detection list sidebar.** `src/renderer/src/review/DetectionList.tsx`
   — scrollable list of all proposals, grouped by entity type or
   by segment (toggleable). Clicking an item focuses the detection
   in the document and scrolls it into view. Shows per-item status
   badge.
7. **Keyboard navigation.** `src/renderer/src/review/keymap.ts` —
   `j`/`k` step focused detection, `a` accept, `r` reject,
   `e` open edit in tooltip, `m` mark missed span (enters
   select-mode; user selects text, entity-type picker opens),
   `u` undo last decision (client-only — issues a PATCH that
   reverts). All shortcuts registered at the window level with a
   single React hook, suspended while an input is focused.
8. **Mark-missed-span flow.** `src/renderer/src/review/MarkMissed.tsx`
   — when `m` is pressed, convert the current text selection into
   a `(segment_anchor, original_text, entity_type)` via the
   segment-id map; POST `/review-sessions/{id}/decisions/user-added`.
   The resulting user-added decision gets painted with the same
   highlight machinery as a proposal.
9. **Operator picker.** `src/renderer/src/review/OperatorPicker.tsx`
   — inline dropdown in the tooltip: `hips`, `replace`, `redact`,
   `mask`, `encrypt`, `pseudonymize`. Pseudonymize is disabled
   unless the mapping store is unlocked (`/health` returns
   `mapping_store_unlocked: true`); clicking it opens the unlock
   flow (WS5 substep 4).
10. **Commit flow.** `src/renderer/src/review/CommitPanel.tsx` —
    summary counts (N accepted, M rejected, K user-added),
    "Attestation" checkbox that must be checked, output-path
    picker (file-save dialog via Electron main), "Commit" button
    that POSTs `/review-sessions/{id}/commit` with
    `{output_path, attested: true}`. Success → "Open containing
    folder" + "Review another document" options.

### Files (sanctum-desktop repo)

- `src/renderer/src/review/` — one component per substep:
  - `DocxRenderer.tsx` — thin wrapper over docx-preview, manages
    the render container lifecycle.
  - `segmentMap.ts` — offset → Range mapping.
  - `highlights.ts` — CSS Highlight API management.
  - `DetectionTooltip.tsx`, `DetectionList.tsx`, `CommitPanel.tsx`,
    `MarkMissed.tsx`, `OperatorPicker.tsx`.
  - `keymap.ts` — keyboard shortcuts.
  - `store.ts` — Zustand store for the current session + focused
    detection + pending PATCHes.
- `patches/docx-preview+<version>.patch` — segment-id emission
  patch (via `patch-package`).
- `src/renderer/src/api/client.ts` — generated from
  `resources/openapi.json` in the `sanctum` repo (fetched via
  `npm run fetch-openapi`, which pulls from a pinned commit).
- `src/renderer/src/api/schemas.ts` — Zod schemas co-generated from
  the OpenAPI spec for runtime validation.

### Approach

- **State model (Zustand)**:
  ```ts
  type ReviewStore = {
    session: ReviewSession | null;
    focusedDetectionId: string | null;
    pendingPatches: Map<string, AbortController>;
    patchDecision: (id: string, patch: DecisionPatch) => Promise<void>;
    focusNext: () => void;
    focusPrev: () => void;
    ...
  };
  ```
  PATCHes are optimistic — the local store updates immediately, then
  rolls back if the server returns an error. Cancellation via
  `AbortController` if the user moves on before the server responds.
- **Highlight rendering is idempotent.** On any state change, wipe
  the four `Highlight` sets and rebuild from the current session.
  This is cheap (few hundred ranges worst case; Chromium's
  Highlight implementation is built for syntax-highlighter-scale
  workloads).
- **Render fidelity**: docx-preview renders into its own CSS
  scope; wrap it in a `shadow DOM` only if style leakage becomes a
  real problem. Default: plain div container, scope our styles with
  a CSS module.
- **Accessibility**: every tooltip is keyboard-dismissible (`Esc`);
  ARIA roles on the detection list; focus ring visible on the
  focused detection (a `Range` style layered over the highlight).

### Tests

- `tests/unit/review/segmentMap.test.ts` — synthetic DOM with
  `data-segment-id` attributes; assert `(segment, offset) → Range`
  maps correctly across single-node and multi-node spans.
- `tests/unit/review/highlights.test.ts` — assert
  `CSS.highlights.get('sanctum-pending')` contains the expected
  `Range` set after a decision change.
- `tests/unit/review/keymap.test.ts` — simulate key events,
  assert store actions invoked.
- `tests/e2e/review-happy-path.spec.ts` — full flow with a real
  sidecar: open fixture `.docx`, step through detections, accept
  some, reject some, mark one missed span, commit, assert output
  file exists and contains the expected replacements (use the
  Python CLI to re-parse and diff).

### Verification

- Render the 10-fixture suite; visual inspection confirms
  docx-preview output matches Word's rendering "close enough"
  (fidelity bar: tables, images, headers, lists all present;
  per-page layout approximate is acceptable).
- Highlights paint over the correct text on all 10 fixtures (not
  off by a character, not straddling a span boundary incorrectly).
- Keyboard shortcuts work with no input focused; shortcuts do not
  fire inside the tooltip's input field.
- Commit produces a file that opens in Word without errors and
  contains the expected changes.

### Troubleshooting hotspots

- **docx-preview's run-level DOM may not map cleanly.** Some runs
  produce multiple spans (e.g. with tab characters); some spans
  contain text from multiple runs when upstream merges them. The
  patch has to cover all cases. **Prototype this in the first 2 days
  of the WS — it is the single highest risk in Phase 3.**
- **CSS Highlight API + transformed ancestors.** docx-preview
  positions pages with `transform: scale(...)` at some zoom levels.
  Verify `::highlight` paints correctly under transform and that
  `getBoundingClientRect()` on the `Range` returns screen
  coordinates that `@floating-ui/react` can anchor to.
- **Optimistic PATCHes + preview drift.** The server recomputes the
  preview on every PATCH. If the user clicks fast (accept-accept-accept),
  the third optimistic state might show a preview derived from the
  first response. Track pending PATCHes per decision and only render
  the server's returned preview once it arrives; the optimistic
  state shows "…" until then.
- **Large documents**: a 200-page contract might have 2000+
  detections. Virtualize the detection list (react-window or similar)
  but keep all highlights painted — the CSS Highlight API handles
  that volume natively.

---

## Workstream 5 — Session workflow UI

The surrounding UX: file picker, session create/resume, mapping-store
unlock, settings, error states. Everything between "user double-clicks
the app icon" and "user is inside the review surface".

### Substep list

1. **App chrome + routing.** `src/renderer/src/App.tsx` with
   react-router: routes for `/`, `/review/:sessionId`,
   `/settings`, `/mapping`. Basic layout shell (title bar, main
   content, status bar).
2. **Landing page.** `src/renderer/src/pages/Landing.tsx` — drop
   zone for `.docx`, "Open file..." button, recent sessions list
   (pulled from a local Electron `Store`-backed file under
   `userData/`). Non-.docx drops show a "Phase 3.5" banner.
3. **Session creation flow.** On file drop: show spinner, POST
   `/review-sessions`, handle errors (engine not ready, path
   rejected, analysis failed) with specific error toasts.
   Navigate to `/review/:sessionId` on success.
4. **Mapping-store unlock UX.** `src/renderer/src/pages/Mapping.tsx`
   — file picker for the store path, password input, unlock/lock
   buttons. Status badge in the title bar reflects
   `mapping_store_unlocked` from `/health` (polled every 5 s or
   pushed via a WebSocket if WS1 adds one — not in scope for
   Phase 3). "Rotate passphrase" action. "Reverse lookup"
   subpanel for the pseudonym→original flow.
5. **Settings page.** `src/renderer/src/pages/Settings.tsx` —
   surfaces the backend config subset that matters for end users:
   default operator (dropdown), NER backend (spaCy / GLiNER, Pro
   only), score threshold (slider), language (dropdown). Writes
   persist to the backend via an endpoint to be added — or to a
   local settings file that overrides the sidecar's env on next
   spawn. **Decide in substep 5 itself** whether to push config to
   the backend or restart the sidecar with new env vars; tentative
   preference: env-restart, since it keeps the backend stateless
   from the UI's perspective.
6. **Error surfaces.** Consistent toast/dialog system for backend
   errors. Particular cases: 409 Conflict on a session (already
   committed/abandoned — offer to create a new one), 413 Payload
   Too Large on a big file, 415 Unsupported Media Type (shouldn't
   happen given the file-picker filter, but guard), 503 engine
   not ready (show the splash again).
7. **Session abandonment.** "Discard" button in the commit panel
   calls `DELETE /review-sessions/{id}`. Confirmation modal.
   Navigate back to landing.
8. **Recent sessions + resume.** Store session metadata locally
   (id, source_path, created_at, preview). Landing page shows
   recent ones with "Resume" buttons. Resuming issues `GET
   /review-sessions/{id}`; if the session is gone (404 or
   committed), remove it from the local list and show a toast.

### Files (sanctum-desktop repo)

- `src/renderer/src/pages/` — `Landing.tsx`, `Mapping.tsx`,
  `Settings.tsx`.
- `src/renderer/src/layout/` — `AppShell.tsx`, `StatusBar.tsx`,
  `TitleBar.tsx`.
- `src/renderer/src/state/settings.ts` — Zustand slice for app
  settings persisted to disk.
- `src/renderer/src/state/recentSessions.ts` — Zustand slice for
  the local recent-sessions list.
- `src/main/settings.ts` — persist settings to
  `userData/settings.json`; apply as sidecar env on next spawn.
- `src/renderer/src/components/Toast.tsx`,
  `src/renderer/src/components/Modal.tsx` — primitives.
- `src/renderer/src/i18n/` — `en.json`, `fr.json`.

### Approach

- **react-router over a home-grown router.** It's the boring
  choice and the footprint is tiny.
- **Settings propagation**: settings edit → persist to
  `userData/settings.json` → main restarts the sidecar with new
  env vars. Kills and respawns the Python process. This is
  simpler than adding a runtime config endpoint and matches the
  "settings are init-time" posture the Python side already has.
- **Recent sessions** live client-side only. If the sidecar
  reboots, the backend still has the session dirs on disk
  (`~/.sanctum/sessions/<id>/`); we just re-GET to check.
- **Mapping-store UX**: the passphrase is held only in memory on
  the renderer for the duration of the unlock action (sent once to
  `/mapping/unlock`, then cleared). Never persisted in Zustand,
  never logged.

### Tests

- `tests/unit/state/settings.test.ts` — settings round-trip via
  the main-side IPC shim.
- `tests/e2e/landing-to-review.spec.ts` — drop a fixture, land on
  the review page with the session loaded.
- `tests/e2e/mapping-unlock.spec.ts` — unlock a test store, confirm
  the title bar badge flips, lock again.

### Verification

- Drag a `.docx` onto the landing page: review surface opens in
  < 5 s (excluding sidecar warm-up).
- Non-.docx drop shows the "coming in Phase 3.5" banner and does
  not post to the backend.
- Mapping-store unlock badge tracks `/health` state.
- Setting the default operator to `redact` and restarting the
  sidecar results in `redact` being used as the session default on
  the next `/review-sessions` create.
- Recent sessions list persists across app restarts.

### Troubleshooting hotspots

- **Sidecar restart on settings change** leaves in-flight review
  sessions unreachable temporarily. Warn the user if they have an
  open session when they change settings; offer "save and restart
  later" vs "restart now".
- **Resume flow** has to handle the case where the session dir was
  deleted out-of-band (user manually cleared `~/.sanctum/sessions/`).
  404 is the clean case; anything else is a bug.

---

## Workstream 6 — Polish, signing, release

The last mile. Signed installers, auto-update, diagnostic bundle,
launch checklist.

### Substep list

1. **i18n completion.** Every string in a component is keyed.
   French catalog complete (human translator, not ML). Language
   toggle in settings.
2. **Accessibility audit.** Keyboard-only navigation through every
   screen. Screen-reader labels on all interactive elements. Focus
   management on modal open/close. Contrast check against
   WCAG AA.
3. **Diagnostic bundle.** `Help → Export diagnostics` — copies
   `app.getPath('logs')`, the `userData/settings.json` (passwords
   redacted), the sidecar's last-N log lines (if captured), and
   the OpenAPI spec version into a zip on the desktop. No
   automated upload. Encourages users to attach it to GitHub
   issues.
4. **macOS signing + notarization.** Apple Developer ID cert
   imported into GitHub Actions secrets. `electron-builder` config
   wired to sign + notarize via `notarytool` in the release
   workflow.
5. **Windows signing.** Via Azure Trusted Signing if eligible,
   Sectigo/DigiCert EV + YubiKey-unlocked cert otherwise.
   Signing runs on a self-hosted Windows runner because hardware
   tokens don't work from GitHub-hosted runners. Document the
   release-runner setup in `RELEASE.md`.
6. **Linux AppImage + deb.** Signed with a GPG key. Key published
   on keyserver; docs explain how to verify. AppImage embedded
   update support via `AppImageUpdate`.
7. **Auto-update split channels.** electron-updater configured for
   the shell (small, frequent). A separate in-app flow handles
   model updates (rare, large, user-confirmed) via the same
   Sanctum-owned CDN introduced in WS3 substep 6. Do **not** use
   electron-updater for models; its differential-update story on
   Windows is unreliable for 700 MB+ payloads.
8. **Release checklist.** `RELEASE.md` — pre-flight (API spec
   regenerated, OpenAPI compat check clean, E2E suite green on
   all three OS, Playwright smoke on a signed build),
   git-tag-driven release workflow, post-flight (GitHub release
   notes, update channel promotion).

### Files (sanctum-desktop repo)

- `src/renderer/src/i18n/fr.json` — full catalog.
- `src/main/diagnostics.ts` — bundle assembly.
- `electron-builder.yml` — sign / publish config per OS.
- `.github/workflows/release.yml` — full release pipeline with
  matrix, signing, GitHub Release upload.
- `RELEASE.md` — checklist + runbook.
- `scripts/verify-installer.sh` — post-build sanity checks
  (installers exist, sizes reasonable, signatures valid).

### Approach

- **Release cadence**: tag-driven. `v0.1.0` tag triggers the
  release workflow; pre-release tags (`v0.1.0-rc.1`) go to a
  `pre-release` update channel that opt-in users can subscribe
  to.
- **Update channel split is non-negotiable.** Shell updates
  must not force a model re-download. Model updates must not
  force a shell restart without the user's consent.
- **Windows runner**: a personally-owned Windows workstation
  with the EV YubiKey attached, registered as a self-hosted
  GitHub runner with a `self-hosted, Windows, signing` label.
  Protected by repo-level runner groups. Documented in
  `RELEASE.md` including the boot-up checklist (unlock
  YubiKey, start runner agent).

### Tests

- E2E full happy path on a signed release build (not a dev
  build) on all three OS before each release.
- Signature verification: `codesign --verify --deep --strict`
  on macOS, `signtool verify /pa` on Windows.
- Smoke test the auto-update flow against a fake update server
  serving a version-bumped installer.

### Verification

- `Help → About` shows version matching the git tag.
- macOS: first launch does not trigger Gatekeeper warning
  ("unidentified developer").
- Windows: first launch does not trigger SmartScreen warning.
- Linux: AppImage runs on Ubuntu 22.04, Fedora 40, Arch.
- Auto-update: installed `v0.1.0` receives `v0.1.1` update
  notification, downloads, restarts, runs `v0.1.1`.
- Diagnostic bundle contains logs, settings (redacted), and
  spec version; does not contain the bearer token or the
  mapping-store passphrase.

### Troubleshooting hotspots

- **Notarization delays.** Apple's `notarytool` can take
  minutes to tens of minutes. Build workflow has to wait; make
  the wait async so concurrent release builds for other OSes
  aren't blocked.
- **Azure Trusted Signing eligibility.** US/Canada only as of
  late 2025. If the developer entity is EU-based, the fallback
  is Sectigo/DigiCert EV + YubiKey, which is strictly more
  work. Start this process in WS2 substep 7.
- **AppImage updates on distros without fuse3.** Fall back to
  "download the new AppImage manually" if the in-place update
  path errors out. Don't block release on this.
- **SmartScreen reputation**. Even a signed installer will
  sometimes trip SmartScreen in its first weeks — reputation
  accrues over time. Document this in `RELEASE.md` so the first
  release's user reports are expected.

---

## Cross-cutting concerns

### Telemetry and analytics

**None ship in Phase 3.** The airgap invariant forbids hosted
telemetry. A future Phase 4 may introduce opt-in local-only crash
reporting (`@sentry/electron` in offline mode, dumps to
`userData/crashes/`) with an explicit "Export crashes" button. Not
MVP.

### Security review

Before WS6 ships:

- Electron fuses review — disable Node integration, enable
  sandboxing, enable `ASAR` integrity, enforce `contextIsolation`.
- CSP on the renderer: `default-src 'self'; img-src 'self' data:;
  style-src 'self' 'unsafe-inline'` (unsafe-inline needed for
  docx-preview's inline styles; evaluate removing in Phase 3.5).
- Never `shell.openExternal` on a URL received from the backend
  without scheme-allowlisting.
- `webRequest` filter blocking any network destination other than
  `127.0.0.1` — a belt for the airgap suspenders.

### Roll-forward story

If a Phase 3.1 breaks the `/review-sessions` contract, old
installed desktops break silently. Mitigations:

1. API versioning from WS1 substep 1 — old clients stay on `/v1/...`
   indefinitely.
2. Desktop displays a backend-version compat warning on startup if
   the bundled sidecar reports an OpenAPI version newer than the
   UI was built against.
3. Release workflow enforces that desktop releases pin a specific
   `sanctum` commit; that commit is the one the sidecar binary is
   built from. No mismatched pairs ship.

### Phase 3.5 roadmap (out of scope for MVP, captured here)

- `.pdf` review surface (PDF.js + overlay on the text layer).
- `.xlsx` review surface (SheetJS parse + custom cell-grid render).
- `.pptx` review surface (hardest — likely "convert slides to
  PNGs via the backend, overlay on the images").
- Full in-app settings for recognizer configuration (custom
  regex recognizers, context words).
- Opt-in self-hosted crash reporting.
- Batch processing: queue multiple documents, review each.
- Enterprise features: SSO unlock for mapping store, shared
  mapping stores over SMB/NFS (still local, still airgap).

---

## Timeline estimate (rough)

| WS | Effort | Parallelizable with |
|---|---|---|
| WS1 | 1 week | — (gates everything) |
| WS2 | 1 week | WS1 |
| WS3 | 2 weeks | WS4 (after scaffold) |
| WS4 | 3–4 weeks | WS3, WS5 |
| WS5 | 2 weeks | WS4 |
| WS6 | 2 weeks | — (gates release; procurement started in WS2) |

Sequenced end-to-end: ~9–10 weeks. With WS3/4/5 in parallel after
WS1+WS2 land: ~7 weeks. Procurement for Windows signing (2–3 weeks
lead time) must be kicked off no later than WS2.

---

## Open decisions to confirm before WS1 starts

1. **Versioning: URL prefix vs header.** Tentative: URL prefix
   (`/v1/...`). Easier to debug in devtools, easier to pin in the
   OpenAPI spec. **Confirm or override in the WS1 substep 1 ADR.**
2. **Tier at launch.** Phase 3 MVP ships Standard tier (spaCy sm,
   ~15 MB models) with Pro tier gated behind a settings toggle
   and a download. Is that the right default, or should the
   installer bundle `en_core_web_lg` upfront? Tradeoff: ~560 MB
   extra installer vs. a worse out-of-box detection quality.
3. **Update server**. Sanctum needs a static file host for
   installers + models. S3 + CloudFront? Self-hosted on Hetzner?
   Both? Decide before WS6.
4. **Public beta**: do we ship an unsigned / non-notarized
   pre-release to a small cohort to validate the review UX
   before investing in signing infrastructure? Tempting but
   risks Windows SmartScreen + macOS Gatekeeper burning user
   trust.
5. **Dev-mode backend spawn**: WS3 substep 8 assumes a sibling
   `../sanctum` checkout. Is that how contributors will actually
   work, or do we want a `pip install -e .` path that points at
   a venv instead? Pick the one-line dev-setup experience.
