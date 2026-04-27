# Architecture — Sanctum

A map of the codebase: which directory owns what, how layers depend on each
other, and where to look when adding a feature. Pair this with
[CONTRIBUTING.md](CONTRIBUTING.md) (workflow, commits, tests) and
[README.md](README.md) (what Sanctum is and how to install it).

## Top-level layout

```
sanctum/                ← the Python package (the only thing shipped)
  analyzer/             ← detect PII (wraps Microsoft Presidio)
  anonymizer/           ← rewrite detected spans (operators)
  api/                  ← Flask HTTP API + auth
  cli/                  ← Click CLI (entry point: `sanctum`)
  config/               ← pydantic-settings (env-driven config)
  core/                 ← domain models, engine, review session
  documents/            ← format adapters (docx / pdf / pptx / xlsx / text)
  security/             ← encrypted mapping store + Argon2 KDF
tests/                  ← unit / integration / evaluation
plans/                  ← phase + workstream tracking (versioned)
schema/                 ← generated OpenAPI snapshot (contract compat gate)
scripts/                ← fixture generation, OpenAPI export, compat check
notebooks/              ← exploratory analysis (not shipped)
resources/              ← packaged data files
```

## Layered architecture

The package is layered top-down — each layer depends only on layers below
it. **Don't add upward edges.**

```
   cli/        api/                ← entry points (CLI, HTTP)
        \    /
         core/                     ← engine + review session state machine
        /  |  \
analyzer  anonymizer  documents    ← detection, rewriting, format I/O
        \  |  /
       security/                   ← mapping store, KDF
       config/                     ← settings (used everywhere)
```

## What lives in each module

### `sanctum/analyzer/` — Detection
- `adapter.py` — `PresidioAnalyzer` wraps Presidio's `AnalyzerEngine`,
  threading our score threshold and registry overrides through.
- `nlp_config.py` — builds the spaCy / GLiNER NER backend per the
  configured tier. **Switching backends is a config knob, not a code change.**

### `sanctum/anonymizer/` — Rewriting
- `adapter.py` — picks an `Operator` per detection and applies it.
- `operators/` — one file per operator strategy. The two non-trivial ones:
  - `hips.py` — Hide-In-Plain-Sight: realistic Faker-generated stand-ins.
  - `pseudonymize.py` — deterministic substitution backed by the unlocked
    mapping store (`security/mapping_store.py`). All other operators
    (`replace`, `redact`, `mask`, `encrypt`) are simple enough to live in
    `adapter.py` directly.

### `sanctum/api/` — HTTP surface
- `app.py` — Flask app factory; binds blueprints + auth + error mappers.
- `auth.py` — bearer token + loopback-only enforcement.
- `server.py` — waitress runner used by `sanctum serve`.
- `_internal.py` — the singleton engine + session store the routes share.
- `schemas.py` — pydantic request/response models for every endpoint.
- `routes/`
  - `health.py` — `/health` (also reports mapping-store lock state).
  - `mapping.py` — `/mapping/{lock,unlock}` for the encrypted store.
  - `pipeline.py` — `/process-file` shortcut (one-shot or hand-off to review).
  - `review_sessions.py` — full review-session lifecycle (POST / GET /
    PATCH decision / POST commit / DELETE abandon). This is the chunkiest
    file because it owns the most state transitions.

### `sanctum/cli/` — Command line
- `commands.py` — Click group with `process-file`, `serve`, `config`,
  `version`. The desktop's PyInstaller bundle re-uses this same entry
  via `scripts/sidecar_entry.py` in the desktop repo.

### `sanctum/config/` — Settings
- `settings.py` — pydantic-settings classes. Env-driven via the prefix
  `SANCTUM_<SECTION>__<KEY>` (double underscore separates nesting).
  Every runtime knob — score threshold, NER backend, default operator,
  bind address, session-store path — flows through this module.

### `sanctum/core/` — Domain heart
- `models.py` — pydantic types: `DetectionResult`, `ReviewProposal`,
  `ProposalDecision`, `UserAddedDecision`, `ReviewSession`,
  `TextSegment`, `StructuredDocument`. **Zero IO; pure data.**
- `protocols.py` — typing.Protocols for `Analyzer`, `Anonymizer`, document
  adapters. Lets the engine accept fakes in tests without inheritance.
- `engine.py` — `SanctumEngine` orchestrates analyze → anonymize → write,
  and owns the `commit_review_session` flow.
- `exceptions.py` — every domain error (`ReviewSessionNotFoundError`,
  `ReviewSessionAlreadyCommittedError`, `AnonymizationError`, …).
- `review/` — review-session state machine, kept off `engine.py` to keep
  it readable:
  - `proposals.py` — analyzer detections → `ReviewProposal`s.
  - `session.py` — `add_decision`, `commit`, `abandon` (the state-machine
    transitions; raises `*AlreadyCommittedError` on illegal moves).
  - `store.py` — on-disk persistence under `~/.sanctum/sessions/<id>/`
    (manifest + input bytes, 0700/0600). `shed_input` drops the input
    bytes on terminal transitions while keeping the manifest for the
    desktop's Recent Sessions list.
  - `previews.py` + `preview_store.py` — generate ghost-text previews
    for the desktop's overlay UI.
  - `identifiers.py` — opaque ID generation.

### `sanctum/documents/` — Format adapters
One reader/writer pair per format, behind a uniform `DocumentAdapter`
protocol so the engine doesn't branch on format.

- `base.py` — protocol + shared helpers.
- `registry.py` — picks the adapter for a given extension.
- `docx_adapter.py`, `pdf_adapter.py`, `pptx_adapter.py`,
  `xlsx_adapter.py`, `text.py` — per-format implementations.
- `structured.py` — the in-memory `StructuredDocument` that flows
  between reader → engine → writer.

### `sanctum/security/` — Mapping store
- `mapping_store.py` — encrypted on-disk store (ChaCha20-Poly1305 AEAD)
  for pseudonymize↔original mappings. Locked by default.
- `keyring.py` — Argon2id KDF that turns a passphrase + salt into a
  32-byte AEAD key. RFC 9106 interactive-use parameters.
- `cipher.py` — thin AEAD wrapper.

## Tests

```
tests/
  unit/         ← per-module, no network, no real backends
    test_analyzer/  test_anonymizer/  test_api/  test_cli/
    test_core/      test_documents/   test_security/
  integration/  ← real Flask + real engine + on-disk session store
                  (fixtures live under tests/fixtures/)
  evaluation/   ← detection-quality benchmarks (out of CI)
  fixtures/     ← sample input documents (docx, pdf, pptx, xlsx)
```

Unit tests **must not hit the network or filesystem outside `tmp_path`.**
Integration tests boot the real Flask app via the test client and exercise
the full route → engine → store path. Evaluation tests are slow and live
outside CI; they're the truth-set for "did detection quality regress?".

## Scripts (`scripts/`)

- `generate_openapi.py` — exports the live spec to `schema/openapi.json`.
  Run after every API change; the generated file is committed.
- `check_api_compat.py` — diffs the committed spec against `main` and flags
  breaking changes. Wired into CI; relaxed during pre-`0.0.1`-tag beta.
- `generate_fixtures.py`, `generate_office_fixtures.py` — synthesize the
  document fixtures used in tests (no real PII checked in).
- `fetch_public_docs.py`, `fetch_public_office_samples.py` — pull
  third-party reference samples for evaluation.

## Where to start when adding…

| Goal | First file to open |
| --- | --- |
| New PII entity type | `analyzer/adapter.py` (custom recognizer) |
| New anonymization operator | `anonymizer/operators/<your_op>.py` + register in `adapter.py` |
| New document format | `documents/<format>_adapter.py` + register in `registry.py` |
| New API endpoint | `api/routes/<area>.py` + matching schema in `api/schemas.py` |
| New review-session transition | `core/review/session.py` + a route in `api/routes/review_sessions.py` |
| New runtime knob | `config/settings.py` (and use it from where it's needed) |
| New CLI command | `cli/commands.py` |

## Invariants worth knowing before changing things

- **No runtime network calls.** Sanctum is local-first. The mapping store,
  spaCy model, and any user data must come from disk. CI enforces
  `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` for the bundled-model path.
- **Mapping-store lock is always-on.** `pseudonymize` is the only operator
  that needs it; every other operator works locked. Routes that touch the
  store eagerly check lock state and return 409 rather than silently
  failing deeper.
- **Review-session state machine is one-way.** `open` → (`committed` |
  `abandoned`). Both terminal transitions shed `input.*` from disk but
  keep `manifest.json` so the desktop's Recent Sessions list survives.
- **Bearer token in stdin, never in argv.** `sanctum serve --token-stdin`
  is the only path the desktop main process uses. Loopback-only by default.
- **Schema changes need a contract-compat run.** `scripts/check_api_compat.py`
  catches breakages; regenerate `schema/openapi.json` when intentionally
  changing the API.
