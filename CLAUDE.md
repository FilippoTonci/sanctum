# CLAUDE.md — Sanctum

Local-first PII anonymization for legal/consulting pros. Built on Microsoft
Presidio. **All processing must be offline** — no runtime network calls.

## Session start
- Run `pre-commit install` to wire git hooks in this clone.

## Architecture — hexagonal (ports & adapters)

```
sanctum/core/        domain: engine, models, protocols, exceptions  (framework-agnostic)
sanctum/analyzer/    adapter over presidio-analyzer
sanctum/anonymizer/  adapter over presidio-anonymizer (+ custom HIPS operator via Faker)
sanctum/documents/   structured doc adapters (.docx .xlsx .pdf .pptx) + registry
sanctum/cli/         Click entrypoint + composition root (_create_engine)
sanctum/config/      Pydantic BaseSettings, env prefix SANCTUM__
```

Hard rule: `sanctum/core/` imports **only** stdlib + Pydantic. No Presidio,
no spaCy, no docx/xlsx/pdf/pptx libs, no HTTP clients. New capabilities plug
in via Protocols in `sanctum/core/protocols.py`. The composition root is
`sanctum/cli/commands.py::_create_engine`.

## Airgap invariant

No runtime network calls. Model downloads are install-time only.

**Known latent gap**: `sanctum/analyzer/adapter.py::PresidioAnalyzer.__init__`
constructs `AnalyzerEngine()` with no explicit NLP config when `nlp_engine` is
not passed. Presidio's default is `en_core_web_lg` (560 MB), and if the model
is missing it will call `spacy.cli.download()` — breaking the airgap. The fix
is to wire `sanctum/analyzer/nlp_config.py::create_nlp_engine` into the
default path. Do not add code that silently downloads models.

See `resources/presidio-architecture.md` for the full network-call audit and
Presidio component breakdown — read it before touching the analyzer layer.

## Document adapters

Registry at `sanctum/documents/registry.py` maps file suffix → adapter module
(lazy import; plain-text workflows stay light). Each module exports `Reader`
and `Writer`. Protocols in `core/protocols.py`; shared model
`StructuredDocument` in `core/models.py` carries `segments: list[TextSegment]`
plus an opaque `raw_handle` the matching Writer uses to project mutations
back.

Segment granularity (by design, affects detection):
- docx: per-run  •  xlsx: per-string-cell  •  pdf: per-page  •  pptx: per-text-frame

**Round-trip fidelity is a hard constraint**: read → write with no edits must
be byte-equivalent. Add an integration test for any new adapter.

Adding a format: (1) new `sanctum/documents/{fmt}_adapter.py` with `Reader`
and `Writer`, (2) register suffix in `registry._SUFFIX_MAP`, (3) integration
test in `tests/integration/test_document_pipeline.py`, (4) use the
`build_segment`/`build_document` helpers from `documents/structured.py`.

## Quality gates (enforced, don't weaken without asking)

- Coverage: **85% on `sanctum/core/*` and `sanctum/documents/*`**, 70% overall.
- mypy: strict on `sanctum.core.*` (`disallow_untyped_defs`); lenient elsewhere.
- ruff rules include `ANN` (annotations) — tests/scripts exempted.
- Fixture hook caps binary fixtures at 500 KB — regenerate via
  `scripts/generate_office_fixtures.py` rather than committing bigger blobs.

## Running things

```bash
pytest -m "not integration"     # fast unit tests
pytest -m integration           # real Presidio engines on fixtures
pytest -m evaluation            # opt-in corpus scoring
pre-commit run --all-files
sanctum analyze|anonymize|process-file|config   # CLI (see commands.py)
```

## Conventions

- Exceptions end in `Error` (`AnalysisError`, `DocumentError`).
- Adapters expose `Reader` / `Writer` — don't prefix with the format name.
- Settings: nested Pydantic `BaseSettings`, one class per subsystem.
- Presidio operator naming trap: `redact` removes text; `replace` swaps in the
  entity tag. `HipsOperator` (Faker-based) is the project's custom operator.

## Where to look before duplicating work

- `README.md` — architecture diagram, compliance mapping, phased roadmap.
- `CONTRIBUTING.md` — ground rules, quality gates, fixture guidelines.
- `resources/presidio-architecture.md` — authoritative Presidio audit + gotchas.
- `.github/workflows/ci.yml` — CI matrix, coverage, spaCy model caching.
