# Sanctum — Phase 1 Implementation Plan

## Context

Sanctum's Phase 0 (foundation) is complete: hexagonal core (`SanctumEngine`, Protocol-based `Analyzer`/`Anonymizer`/`DocumentReader`/`DocumentWriter`), Presidio-backed adapters, spaCy NLP, `redact`/`replace`/`hash`/`encrypt`/`hips` operators, Pydantic settings, Click+Rich CLI, plain-text document adapter, and an evaluation harness with 12 annotated synthetic fixtures across 6 domains.

Phase 1 turns Sanctum from a text-only CLI into something that can (a) ingest real professional documents, (b) support reversible pseudonymization workflows, (c) be driven headlessly by a future GUI, (d) scale to a higher-accuracy NER tier, and (e) enforce quality gates in CI. We explicitly **defer** custom legal recognizers and keep the existing architectural rule: the core domain stays framework-agnostic, everything new goes behind a Protocol.

**In-scope (selected):**
1. Document format adapters: `.docx`, `.xlsx`, `.pdf`, `.pptx`
2. Encrypted mapping store (AES-256 + local SQLite) for reversible pseudonymization
3. Flask localhost API (background service for future GUI)
4. Transformer-based NER adapter (Professional tier scaffold, model deferred)
5. CI/CD + pre-commit hooks + lint/type enforcement

**Sequencing:** foundation-first — CI/lint lands first (so everything after merges through a green gate), then document adapters, then mapping store, then Flask API, then transformer tier.

**Explicitly deferred to Phase 1.5:** human-in-the-loop review workflow. Tracked in its own plan (`phase-1-5-review-workflow.md`) because it introduces a whole product surface (review sessions, staged mappings, a minimal review UI) on top of the Phase 1 adapter/store/API contracts. See issue #11. *Note: Phase 1.5 was originally scoped around native document comments as the canonical review surface; issue #16 reframed it around a Sanctum-owned review UI backed by the localhost API, with native Office comments demoted to a one-way export / interop path.*

---

## Architectural Guardrails (apply to every workstream)

- **Hexagonal purity:** `sanctum/core/` must remain import-free of Presidio, Flask, openpyxl, transformers, cryptography. New capabilities plug in via Protocols in `sanctum/core/protocols.py`.
- **Air-gap:** no workstream may introduce a runtime network call. Model downloads are install-time only, gated by config, and covered by the same pattern `nlp_config.create_nlp_engine` already uses.
- **Round-trip fidelity:** document adapters must read → write identical bytes for a pass-through (no transformations). This is the invariant that will keep formatting preservation honest once we wire anonymization through.
- **Test pyramid:** every adapter gets unit tests (with golden fixtures), integration tests (engine end-to-end), and — where applicable — an evaluation harness entry. Test ordering mirrors the directory under `tests/`.
- **Test fixtures for Office formats must be real binary files** (checked into `tests/fixtures/office/` under ~100 KB each, generated from public-domain sources). Use the Anthropic skills at `~/.claude/skills/{docx,xlsx,pdf,pptx}` as reference for construction patterns; we generate our own fixture binaries with `scripts/generate_office_fixtures.py` so CI stays offline and deterministic.

---

## Workstream 1 — CI/CD, Pre-commit, Lint/Type Enforcement

Lands first; every later workstream benefits from the gate.

### Files
- `.pre-commit-config.yaml` (new)
- `.github/workflows/ci.yml` (new)
- `.github/workflows/release.yml` (new, stub for Phase 3)
- `pyproject.toml` — extend `[tool.ruff.lint]`, add `[tool.mypy]` strictness, add `[tool.pytest.ini_options]` coverage opts, add `[project.optional-dependencies] ci` group
- `CONTRIBUTING.md` (new) — referenced by README but missing

### Approach
- **Pre-commit hooks:** `ruff` (lint + format), `mypy` (Tier 2 config — see below), `check-yaml`, `end-of-file-fixer`, `trailing-whitespace`, `check-added-large-files` (block >500 KB except fixtures dir). `detect-secrets` baseline so we never commit keys.
- **CI matrix:** Python 3.10, 3.11, 3.12 × ubuntu-latest. Cache pip + spaCy model. Steps: install, `pre-commit run --all-files`, `pytest -m "not integration"`, `pytest -m integration` (with `en_core_web_sm` downloaded in a dedicated step), coverage upload.
- **Coverage gate:** 85% line on `sanctum/core` and `sanctum/documents`, 70% overall. Published via `pytest-cov` → GitHub summary.
- **Ruff:** extend rule set — add `B`, `UP`, `SIM`, `RUF`, `ANN` (minus `ANN101`/`ANN102`). Keep `line-length = 100`.

**mypy — Tier 2 (lenient, domain-strict):**

Rationale: full `--strict` everywhere is overkill for a codebase this size and adds friction on every PR. But mypy pays for itself on the pieces that enforce the hexagonal boundary (Protocols in `sanctum/core/`) and the compliance-critical paths. So: strict where it matters, lenient elsewhere, escape hatches for untyped third-party libs.

```toml
[tool.mypy]
python_version = "3.10"
warn_return_any = true
warn_unused_configs = true
warn_unused_ignores = true
plugins = ["pydantic.mypy"]

# Strict on the domain — this is where Protocol satisfaction
# and compliance invariants live.
[[tool.mypy.overrides]]
module = "sanctum.core.*"
disallow_untyped_defs = true
disallow_incomplete_defs = true
disallow_untyped_decorators = true

# Everything else runs under the default (lenient) settings.
# Annotations encouraged but not enforced; focus is on catching
# real bugs, not chasing 100% coverage of type hints.

# Third-party libs without type stubs — tell mypy to stop complaining.
[[tool.mypy.overrides]]
module = [
    "presidio_analyzer.*",
    "presidio_anonymizer.*",
    "faker.*",
    "pdfplumber.*",
    "openpyxl.*",
    "docx.*",
    "pptx.*",
    "reportlab.*",
]
ignore_missing_imports = true

# Tests are lenient — they use mocks and don't need strictness.
[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
check_untyped_defs = false
```

What this buys:
- **`sanctum/core/`** — every function signature must be fully typed. This is where the hexagon's Protocols live; mypy verifies adapters actually satisfy them.
- **Adapters, CLI, API, config** — annotations encouraged (and mypy will check the ones you write), but missing annotations don't fail the build.
- **Tests** — no friction; mocks and fixtures can stay untyped.
- **Third-party noise** — silenced at the boundary, not leaked into domain code.

If pain points emerge later (a bug slips through adapters, a refactor breaks callers silently), ratchet strictness up one module at a time. Easier than loosening after everyone's gotten used to strict.

### Troubleshooting hotspots
- **spaCy model download in CI:** must run `python -m spacy download en_core_web_sm` before integration tests. Use a dedicated step with its own cache key.
- **Pre-commit + Windows contributors:** pin hook versions; avoid shell-only hooks.
- **mypy on Pydantic v2:** requires `plugins = ["pydantic.mypy"]`.
- **Ruff + existing code:** first run will surface violations — include a baseline-fix commit separately from the config commit so review is legible.

### Verification
- `pre-commit run --all-files` passes locally.
- `gh workflow run ci.yml` green on a throwaway PR.
- Coverage gate trips correctly when a test is removed.

---

## Workstream 2 — Document Format Adapters (`.docx`, `.xlsx`, `.pdf`, `.pptx`)

The hardest workstream. Real documents carry structure Presidio can't see: runs, cells, shapes, pages. We must extract text, run anonymization, and write back without shredding formatting.

### Design: the `StructuredDocument` abstraction

Presidio sees flat text. Real documents are trees of *text runs* with formatting. Introduce a new Protocol pair in `sanctum/core/protocols.py`:

```python
class StructuredDocumentReader(Protocol):
    def read(self, path: Path) -> StructuredDocument: ...

class StructuredDocumentWriter(Protocol):
    def write(self, doc: StructuredDocument, path: Path) -> None: ...
```

And a new domain type `sanctum/core/models.py::StructuredDocument`:

```python
class TextSegment(BaseModel):
    id: str                       # stable per-run identifier ("body/p3/r1", "sheet=Sheet1/A2", "slide=2/shape=3/run=0")
    text: str
    metadata: dict[str, Any]      # style, formula flag, cell type — adapter-specific, opaque to core

class StructuredDocument(BaseModel):
    source_path: Path
    format: Literal["docx", "xlsx", "pdf", "pptx"]
    segments: list[TextSegment]
    raw_handle: Any = Field(exclude=True)   # underlying lib object (docx.Document, openpyxl.Workbook, …) kept for write-back
```

A new orchestrator method on `SanctumEngine`:

```python
def process_document(
    self,
    reader: StructuredDocumentReader,
    writer: StructuredDocumentWriter,
    input_path: Path,
    output_path: Path,
    **analysis_kwargs,
) -> list[AnonymizationResult]
```

Segment-by-segment anonymization preserves structure: we analyze each segment independently (spaCy handles short strings fine), apply operators, mutate the segment's `text`, and the writer projects changes back onto the handle. No format-specific code enters core.

### Files & layout
```
sanctum/documents/
├── base.py                      # existing TextDocumentReader/Writer — keep
├── structured.py                # NEW: StructuredDocument, TextSegment helpers
├── docx_adapter.py              # NEW
├── xlsx_adapter.py              # NEW
├── pdf_adapter.py               # NEW
├── pptx_adapter.py              # NEW
└── registry.py                  # NEW: path → adapter dispatch
```

Plus:
- `sanctum/core/protocols.py` — add StructuredDocument protocols
- `sanctum/core/models.py` — add `TextSegment`, `StructuredDocument`
- `sanctum/core/engine.py` — add `process_document()`
- `sanctum/cli/commands.py` — add `sanctum process-file <in> <out>` command
- `pyproject.toml` — move `python-docx`, `openpyxl`, `pdfplumber` from `[docs]` extra to a new required `[project.optional-dependencies] documents = [...]` (rename existing `docs` group, which was misnamed). Add `python-pptx`, `pypdf`, `reportlab`.

### Per-format implementation notes

**docx (`python-docx`):**
- Walk `document.paragraphs`, then `document.tables` (recurse — tables can contain paragraphs containing tables). For each `Paragraph`, iterate `paragraph.runs` — runs are the atomic formatting unit.
- Segment id: `body/p{idx}/r{run_idx}` or `body/tbl{t}/row{r}/cell{c}/p{p}/r{run}`.
- Headers/footers: `section.header.paragraphs`, `section.footer.paragraphs`.
- Comments/tracked changes: Phase 1 we do *not* touch — just flag in metadata and warn.
- **Pitfall:** a single visual sentence can span multiple runs. Detections may cross run boundaries → lose the entire span. **Solution:** offer two modes: `run-preserving` (default, skips cross-run detections with a warning) and `coalesce` (joins runs in a paragraph into one run with merged formatting from the first run). Make it a param; document trade-off. The Anthropic docx skill notes this exact issue — reference their approach.

**xlsx (`openpyxl`):**
- Walk `wb.sheetnames` → `ws.iter_rows(values_only=False)` → for each `Cell`, read `cell.value`. Skip cells where `cell.data_type == 'f'` (formulas) by default — anonymizing formulas breaks them. Add `--include-formulas` flag to coerce string output.
- Segment id: `sheet={name}/{A1}` (use `openpyxl.utils.get_column_letter`).
- Data-type preservation: if cell was numeric, we still extract to string but tag `metadata.original_type`. On write, if the anonymized value is still numerically parseable, cast back; otherwise store as string and flag.
- **Pitfall:** merged cells — only the top-left has value; iterate but skip the rest. Shared strings are handled by openpyxl transparently.

**pdf (read-only text extraction in Phase 1):**
- Use `pdfplumber` for extraction. Segment id: `page={n}/block={m}` using `page.extract_text_lines()` coordinates.
- **Write path is deliberately out-of-scope.** True burn-in redaction is Phase 3. Phase 1 writer writes a *derivative* text-only `.txt` or a flat reportlab PDF, clearly labeled "DERIVATIVE". Explicit in the API: `PdfAdapter.writer_mode = "derivative"` — anyone attempting to overwrite the source PDF gets `PdfWriteRefused`.
- **Pitfall:** scanned PDFs → pdfplumber returns empty. Detect (no text on any page) and raise `UnsupportedPdfError("scanned; OCR required — Phase 2")`.

**pptx (`python-pptx`):**
- Walk `prs.slides` → `slide.shapes` → shapes that have `.text_frame` → `text_frame.paragraphs` → `paragraph.runs`. Also handle `shape.has_table` (cell text frames) and speaker notes (`slide.notes_slide.notes_text_frame`).
- Segment id: `slide={n}/shape={id}/p{p}/r{r}` or `.../notes/p{p}/r{r}`.
- **Pitfall:** grouped shapes — recurse into `shape.shapes` when `shape.shape_type == MSO_SHAPE_TYPE.GROUP`. SmartArt and chart text are *not* safely mutable via python-pptx; extract read-only and warn on anonymize attempt.

### Fixture strategy

Create `tests/fixtures/office/` with real binary files, generated deterministically by a new script `scripts/generate_office_fixtures.py` that uses `python-docx`, `openpyxl`, `python-pptx`, `reportlab` to build tiny canonical files embedding the same PII content as the existing plain-text fixtures. This keeps CI offline and fixtures diff-readable (re-run to regenerate).

Additionally, `scripts/fetch_public_office_samples.py` (new, opt-in, not run in CI): downloads 2–3 public-domain office documents (SEC EDGAR has .htm with embedded xls links; GPO and data.gov publish open .docx/.xlsx; public slide decks from open-sourced conferences for .pptx). Stores under `tests/fixtures/office/public/` with provenance JSON, **not committed to git** (`.gitignore` entry). This is the "fetch online examples" step — used for manual/eval runs, never in automated CI.

Reference: the Anthropic skills at `~/.claude/skills/{docx,xlsx,pdf,pptx}/` document canonical construction (`SKILL.md`, `scripts/`). Use them as implementation reference — they highlight the exact edge cases (run-splitting in docx, formula vs value in xlsx, subscript rendering in PDF) we must respect.

### Tests
- `tests/unit/test_documents/test_docx_adapter.py` — round-trip fidelity (read → write no-op → byte equality on `zipfile` member list), segment-id stability, header/footer extraction, run-split detection.
- Same layout for xlsx / pdf / pptx.
- `tests/integration/test_document_pipeline.py` — full `engine.process_document()` with mock analyzer, asserts output file opens cleanly in the corresponding library.
- `tests/fixtures/office/` — 4 small canonical files + their annotation JSON.

### Troubleshooting hotspots
- **DOCX run-splitting** (see mode design above).
- **XLSX formula mutation** — never anonymize formula strings; only evaluated values go through the analyzer.
- **PDF write semantics** — be explicit that Phase 1 produces derivatives, not burn-in.
- **PPTX grouped/SmartArt** — recursion + graceful skip.
- **Charset/encoding** — python-docx stores text as `str`; no bytes handling needed. pdfplumber can emit replacement chars for unusual CID fonts; tag segments with `metadata.extraction_confidence`.

### Verification
- `pytest tests/unit/test_documents/` and `tests/integration/test_document_pipeline.py` green.
- `sanctum process-file tests/fixtures/office/sample.docx /tmp/out.docx` produces a valid, openable .docx with PII replaced.
- Round-trip no-op: `read → write` produces byte-identical zip members (minus timestamps).

---

## Workstream 3 — Mapping Store (session-only + encrypted file)

Needed for reversible pseudonymization: the same `John Smith` must map to the same `Alex Doe` across detections, and (when persisted) across sessions.

### Why encryption at all, given the air-gap invariant?

Air-gap defeats **network** threats. It does **not** defeat laptop theft, cloud-synced filesystems (iCloud/Dropbox/Time Machine/corporate backup silently copying files), shared machines, accidental sharing (emailing the mapping with anonymized docs, committing to git, bundling into discovery), or USB/phishing malware. The mapping file is the one artifact that reverses every pseudonym the user has ever generated — it must not leak even if one of those things happens. So: **session-only by default** (zero on-disk retention) and **passphrase-encrypted at rest** when persisted.

### Scope decision: keep it small

We deliberately **reject** the SQLite-per-row AEAD + HMAC-blind-index design. That's a tokenization-service shape (millions of rows, random access, per-row rotation audit). A legal/consulting pro has on the order of 10²–10⁴ entities total — a single dict in memory. The simpler design below is ~120 LOC instead of ~400, covers the same threat model, and keeps the `MappingStore` Protocol boundary intact so we can swap in a SQLite impl later if scale ever demands it (see deferred F4).

### Files
```
sanctum/security/
├── __init__.py
├── cipher.py            # ChaCha20-Poly1305 AEAD wrapper
├── keyring.py           # Argon2id(passphrase, salt) → 32-byte key
└── mapping_store.py     # InMemoryMappingStore + EncryptedFileMappingStore
```
Plus:
- `sanctum/core/protocols.py` — add `MappingStore` Protocol
- `sanctum/core/exceptions.py` — `MappingStoreError`, `IncorrectPassphraseError`, `MappingStoreLockedError`
- `sanctum/anonymizer/operators/pseudonymize.py` — NEW operator that uses `MappingStore`
- `sanctum/config/settings.py` — add `SecuritySettings` (store path, session-only flag, kdf params)
- `pyproject.toml` — new `[security]` extra: `cryptography>=42`, `argon2-cffi>=23`

### File format (encrypted persistence)

One self-contained file. No SQLite, no WAL, no index tables.

```
magic: b"SANCTUM1"              8 bytes  — format sentinel + version
kdf_salt: bytes                16 bytes  — per-store, random on first save
kdf_params: JSON (len-prefixed) — {"algo":"argon2id","t":3,"m":131072,"p":1}
nonce: bytes                   24 bytes  — XChaCha20 nonce, random per save
ciphertext+tag: bytes          rest      — ChaCha20-Poly1305(JSON payload)
```

The JSON payload is:

```json
{
  "version": 1,
  "entries": {
    "PERSON::John Smith": {"pseudonym": "Alex Doe", "created_at": "..."},
    "EMAIL_ADDRESS::j@x.com": {"pseudonym": "m@y.com", "created_at": "..."}
  }
}
```

Lookup key is `f"{entity_type}::{original}"` — plain dict access, no blind index needed because the entire dict is already behind one AEAD.

### Key management
- **Derivation:** `Argon2id(passphrase, salt, t=3, m=128 MiB, p=1) → 32 bytes`. RFC 9106 params.
- **AEAD:** ChaCha20-Poly1305 (stdlib via `cryptography` — constant-time, no AES-NI dep, 192-bit nonce after our extended-nonce wrap).
- **Master key never touches disk.** Lives only in the `EncryptedFileMappingStore` instance between `unlock()` and `lock()`.
- **Session-only mode:** use `InMemoryMappingStore` — no passphrase, no disk, dict dies with the process.
- **Rotation:** re-encrypt on `lock()` with a new passphrase via `rotate_passphrase(old, new)`. Trivial — it's one file.

### API
```python
class MappingStore(Protocol):
    def unlock(self, passphrase: str | None = None) -> None: ...
    def lock(self) -> None: ...
    def get_or_create(self, original: str, entity_type: str, factory: Callable[[], str]) -> str: ...
    def reverse(self, pseudonym: str, entity_type: str) -> str | None: ...
    def rotate_passphrase(self, old: str, new: str) -> None: ...  # file store only; in-memory raises
```

`InMemoryMappingStore.unlock` ignores the passphrase. `EncryptedFileMappingStore.unlock` decrypts the file into an in-memory dict; `lock()` re-encrypts and writes atomically (tmpfile + rename).

`PseudonymizeOperator` takes a `MappingStore` and the Faker factory map (reuses `HipsOperator._ENTITY_GENERATORS`) and calls `store.get_or_create`. Consistency across documents falls out for free.

### Tests
- `tests/unit/test_security/test_cipher.py` — ChaCha20-Poly1305 round-trip, tampered-ciphertext raises `InvalidTag`.
- `tests/unit/test_security/test_keyring.py` — Argon2id determinism given same salt+passphrase; different salt → different key.
- `tests/unit/test_security/test_mapping_store.py` —
  - `InMemoryMappingStore`: `get_or_create` idempotency, reverse, lock wipes dict.
  - `EncryptedFileMappingStore`: save → reopen same passphrase → reverse works; wrong passphrase raises `IncorrectPassphraseError`; tamper one byte in file → unlock raises; `rotate_passphrase` preserves entries.
- `tests/integration/test_reversible_pipeline.py` — anonymize → lock → re-unlock → `reverse()` restores original text.

### Troubleshooting hotspots
- **Wrong passphrase:** AEAD tag fails → catch `InvalidTag` and raise `IncorrectPassphraseError` (explicit, not cryptic).
- **Pseudonym collisions:** two different originals → same Faker output. Low probability at our scale, but detect on insert (scan existing pseudonyms per entity_type); regenerate with a retry loop (max 5); raise if exhausted.
- **Atomic writes:** write to `<path>.tmp`, fsync, `os.replace` — never corrupt an existing store on crash mid-save.
- **Argon2id cost on low-end hardware:** tunable via `SecuritySettings.kdf_memory_cost`; doc-only for Phase 1, no auto-calibration.

### Verification
- `pytest tests/unit/test_security/ tests/integration/test_reversible_pipeline.py` green.
- `sanctum anonymize --operator pseudonymize` works in session-only mode without any passphrase prompt.
- `sanctum mapping unlock <path>` + `sanctum anonymize --operator pseudonymize --store <path>` → `sanctum mapping lock` → re-unlock → `reverse` returns original.
- Tamper test: flip one byte in the mapping file → unlock raises `IncorrectPassphraseError`.

---

## Workstream 4 — Flask Localhost API

Thin REST wrapper so a future Electron GUI (Phase 3) has a stable boundary today.

### Files
```
sanctum/api/
├── __init__.py
├── app.py              # Flask app factory
├── routes/
│   ├── __init__.py
│   ├── analyze.py
│   ├── anonymize.py
│   ├── mapping.py       # unlock, lock, rotate, reverse
│   └── health.py
├── schemas.py           # Pydantic request/response models
├── auth.py              # localhost-only + bearer token
└── server.py            # gunicorn/waitress entry, CLI wrapper
```
Plus:
- `sanctum/cli/commands.py` — `sanctum serve` command
- `pyproject.toml` — `flask>=3`, `waitress>=3` (Windows-friendly WSGI), to a new `[api]` extra

### Design
- **Bind `127.0.0.1` only, refuse any other `host`.** Enforce at startup; emit loud log line.
- **Bearer token auth:** generated on `serve` startup, written to `~/.sanctum/api-token` with `0600` perms, printed once. GUI reads the file.
- **Origin check:** reject requests whose `Origin` header is set and non-local. Allows same-machine `http://localhost` but blocks drive-by browser calls.
- **CORS:** strictly disabled by default (the API is local-only; GUI uses a desktop webview, not a browser).
- **Synchronous endpoints** for Phase 1 (no job queue). Large documents go through `/process-file` with a path parameter — the GUI hands paths, not bytes. This keeps payloads small.

### Endpoints
| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{status, version, mapping_store_unlocked}` |
| POST | `/analyze` | `{text, language?, entities?, threshold?}` | `[DetectionResult]` |
| POST | `/anonymize` | `{text, operator_policies?}` | `AnonymizationResult` |
| POST | `/process-file` | `{input_path, output_path, operator_policies?}` | `{segments_processed, detections_total, output_path}` |
| POST | `/mapping/unlock` | `{master_password}` | `{unlocked: true}` |
| POST | `/mapping/lock` | — | `{unlocked: false}` |
| POST | `/mapping/reverse` | `{pseudonym, entity_type}` | `{original}` |
| POST | `/mapping/rotate-key` | `{old_password, new_password}` | `{rotated: true}` |

### Tests
- `tests/unit/test_api/` — Flask test client per route, covers 200/400/401/422 paths.
- `tests/integration/test_api_pipeline.py` — boot `waitress` in a thread, curl against it, assert end-to-end.

### Troubleshooting hotspots
- **Master password over HTTP:** mitigated by localhost-only bind + token + `0600` token file. Document that anyone with local user access *can* read the token — not a multi-user threat model.
- **Memory pressure:** no streaming for v1 — document limit in spec (say, 50 MB). Reject larger with 413.
- **Windows path handling:** `input_path` / `output_path` — always treat as `Path` server-side; reject UNC paths unless explicitly enabled.
- **Concurrent requests:** single-process `waitress` with N threads; Presidio engines are thread-safe after warm-up, but first call per worker spikes RAM — preload at boot.

### Verification
- `sanctum serve --port 8765` starts, prints token.
- `curl -H "Authorization: Bearer $(cat ~/.sanctum/api-token)" http://127.0.0.1:8765/health` → 200.
- External IP bind attempt raises.

---

## Workstream 5 — Transformer NER Adapter (Professional Tier, scaffold only)

Goal: the adapter and config slot exist; the model choice is deferred until we can benchmark on the fixture corpus + any new legal recognizers (Phase 1.5).

### Files
- `sanctum/analyzer/nlp_config.py` — add `create_transformers_nlp_engine(model_name)` alongside existing `create_nlp_engine`
- `sanctum/config/settings.py` — add `nlp_tier: Literal["standard","professional"]` and `transformer_model: str | None`
- `sanctum/analyzer/adapter.py` — accept tier param, build correct engine
- `pyproject.toml` — `transformers`, `torch` in new `[transformers]` extra (heavy, opt-in only)

### Troubleshooting hotspots
- **Model download on first use:** exactly the same air-gap pitfall as spaCy. Pre-download via `sanctum models fetch --tier professional` — explicit, user-initiated, logged. No implicit downloads at runtime.
- **CPU-only torch wheel:** CI and default install use `torch --index-url https://download.pytorch.org/whl/cpu` to avoid CUDA bloat. Document GPU opt-in.
- **RAM spike:** Transformer pipelines pin ~2–4 GB. Add a startup check that warns if <6 GB free.
- **Deferred model:** a benchmark harness entry in `tests/evaluation/` runs per-tier scoring against the fixture corpus — picks a winner with data, not guess.

### Verification
- `sanctum config --tier professional` reflects in settings.
- With a model pre-downloaded to HF cache, integration tests run green under `[transformers]` extra.
- Without the model, fail-fast with a clear error — no network call attempted.

---

## Critical Files Being Modified (summary)

- `sanctum/core/protocols.py` — add structured-doc + mapping-store protocols
- `sanctum/core/models.py` — add `StructuredDocument`, `TextSegment`
- `sanctum/core/engine.py` — add `process_document()`
- `sanctum/analyzer/adapter.py` — tier-aware engine construction
- `sanctum/analyzer/nlp_config.py` — transformer engine factory
- `sanctum/anonymizer/operators/pseudonymize.py` — NEW, uses MappingStore
- `sanctum/documents/*` — four new adapters + structured base
- `sanctum/security/*` — NEW module (cipher, keyring, mapping store)
- `sanctum/api/*` — NEW Flask app
- `sanctum/cli/commands.py` — `process-file`, `serve`, `mapping`, `models` commands
- `sanctum/config/settings.py` — new `SecuritySettings`, `ApiSettings`, tier fields
- `pyproject.toml` — reorganized extras: `documents`, `api`, `security`, `transformers`, `ci`
- `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `CONTRIBUTING.md` — NEW
- `scripts/generate_office_fixtures.py`, `scripts/fetch_public_office_samples.py` — NEW
- `tests/fixtures/office/` — NEW binary fixtures
- `tests/unit/test_{documents,security,api}/` — NEW test directories
- `tests/integration/test_{document_pipeline,reversible_pipeline,api_pipeline}.py` — NEW

Existing utilities to **reuse** (not re-implement):
- `sanctum.core.exceptions.SanctumError` base — all new errors extend it
- `sanctum.core.engine.SanctumEngine` composition pattern — new endpoints/adapters feed into the same engine
- `sanctum.analyzer.nlp_config.create_nlp_engine` — mirror its pattern for transformers
- `sanctum.anonymizer.operators.hips.HipsOperator._ENTITY_GENERATORS` — reuse for Pseudonymize operator's Faker factory

---

## Order of Execution & Milestones

1. **M1 — CI green on current code.** Land WS1. Merge fixes any lint/type debt from Phase 0 in a separate PR so the gate commit is config-only.
2. **M2 — Document adapters.** Land WS2. Each adapter is its own PR; pptx last (most complex structure).
3. **M3 — Mapping store.** Land WS3. Includes `pseudonymize` operator.
4. **M4 — Flask API.** Land WS4. Depends on M2 + M3 (uses both).
5. **M5 — Transformer scaffold.** Land WS5. Independent of M4; can interleave.

Each milestone closes with: updated README roadmap checkboxes, a CHANGELOG entry, and an evaluation-harness run to confirm no regression on the existing corpus.

**Phase 1.5 (separate plan)** opens once M2 + M3 + M4 have landed — it depends on the adapter contract, mapping store, and API surface all being in place. See `phase-1-5-review-workflow.md`.

---

## Verification (end-to-end, post-Phase-1)

1. `pip install -e ".[documents,security,api,ci]"` — clean install on a fresh venv.
2. `python -m spacy download en_core_web_sm` — model ready.
3. `pre-commit run --all-files` — green.
4. `pytest` — all unit + integration tests green; coverage ≥ gate.
5. `pytest tests/evaluation/ -m evaluation` — no regression vs Phase 0 baseline.
6. `sanctum process-file tests/fixtures/office/sample.docx /tmp/out.docx --operator pseudonymize` — valid output; opens in Word; PII replaced consistently.
7. `sanctum serve --port 8765` + `curl` through `/health`, `/analyze`, `/anonymize`, `/process-file` — all 200.
8. Unlock mapping store, anonymize, lock, re-unlock with same password, `reverse()` returns original — confirms persistence + key derivation determinism.
9. Air-gap check: `sudo iptables -A OUTPUT -j REJECT` (or offline VM) → re-run the full pipeline. Must complete with zero network errors.

---

## Deferred Follow-ups (captured from WS3 research, 2026-04-13)

Logged here so they survive context compaction. All are explicit non-goals of WS3 and should land as their own workstreams (Phase 1.5 or later).

### F1 — Format-Preserving Encryption operator (sibling to Pseudonymize)
- **What:** FF1 (NIST SP 800-38G Rev 1) via `mysto/python-fpe`, exposed as a new `FpeOperator` for shape-preserving reversibility on numeric/structured PII: `PHONE_NUMBER`, `CREDIT_CARD`, `US_SSN`, `IBAN_CODE`.
- **Why it's attractive:** stateless — no mapping row needed. Deterministic with just the key. Preserves shape (10-digit phone → 10-digit phone), which keeps downstream parsers happy.
- **Why it's deferred:** WS3 must land first to establish the key-derivation story (FPE key would be another HKDF-derived subkey off the master). Needs a separate crypto review; NIST withdrew FF3/FF3-1 in Feb 2025 after the Beyne attack, so we must stay on FF1 and justify the choice in docs.
- **Scope when picked up:** new `anonymizer/operators/fpe.py`, entity-type → alphabet map, integration test alongside pseudonymize. No mapping store involvement.

### F2 — OS keyring integration for master-key caching
- **What:** optional `python-keyring` layer that stores a *wrapping key* (not the master passphrase) in macOS Keychain / Windows Credential Manager / Secret Service, so the user isn't re-prompted for the passphrase every session.
- **Why it's attractive:** matches 1Password/Bitwarden UX; real usability win for desktop pros who anonymize dozens of documents a day.
- **Why it's deferred:** WS3 builds the passphrase-only path first — the simpler, more portable baseline. Keyring is additive and introduces platform-specific code paths best reviewed in isolation.
- **Scope when picked up:** `sanctum/security/os_keyring.py`, gated behind `SecuritySettings.use_os_keyring: bool = False`, with a clear opt-out for users who don't trust the OS keyring. Must not break the air-gap invariant.

### F3 — SQLCipher evaluation (only if metadata leakage becomes a stated concern)
- **What:** adopt SQLCipher for whole-database encryption if/when we move to the SQLite-backed mapping store (see F4), so table structure, row counts, `entity_type` distribution, and `created_at` timestamps are also encrypted at rest.
- **Why it's deferred:** current `EncryptedFileMappingStore` has *no* metadata leak — the whole payload (keys, values, timestamps) is inside one AEAD blob. SQLCipher only becomes relevant after F4.
- **Scope when picked up:** evaluated together with F4. Python binding `pysqlcipher3` is less maintained than stdlib `sqlite3`, which complicates the airgap install story — factor in.

### F4 — SQLite-backed MappingStore with per-row AEAD + HMAC blind index
- **What:** a third `MappingStore` impl using SQLite, per-row ChaCha20-Poly1305 on the original, HMAC blind index for lookup, HKDF-separated `k_enc`/`k_blind` subkeys. The design originally sketched for WS3, demoted after scope review.
- **Why it's attractive:** scales to millions of rows, supports random access without loading the full mapping into memory, enables per-row audit timestamps and fine-grained rotation.
- **Why it's deferred:** WS3 targets 10²–10⁴ entities for legal/consulting users — a single in-memory dict is both sufficient and dramatically simpler. Paying the SQLite + blind-index complexity tax now would violate the "no abstractions beyond what the task requires" rule.
- **Scope when picked up:** new `SqliteMappingStore` behind the existing `MappingStore` Protocol. No caller changes. Trigger: a stated requirement for >10⁵ entities or per-row audit. Crypto choices to adopt then — **XChaCha20-Poly1305** (not AES-GCM), **Argon2id `t=3, m=128 MiB, p=1`**, **HKDF-SHA256** split into `k_enc` + `k_blind`, **HMAC-SHA256(`k_blind`, original)** blind index with `UNIQUE(original_hash, entity_type)`, process-local lock for thread-safety (Presidio's pseudonymization sample flags this as caller's problem).
