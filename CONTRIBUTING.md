# Contributing to Sanctum

Thanks for your interest. Sanctum is a local-first PII anonymization tool for
legal and consulting professionals. Because the project handles privacy-sensitive
workflows, we lean on a strong quality gate — CI, linting, and types all run
on every PR.

## Getting set up

```bash
git clone https://github.com/your-username/sanctum.git
cd sanctum

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
python -m spacy download en_core_web_sm

pre-commit install
```

## Architectural ground rules

Sanctum follows a **hexagonal architecture**. Before you add code:

- `sanctum/core/` must stay framework-agnostic. It cannot import Presidio,
  Flask, openpyxl, transformers, `cryptography`, or any adapter-specific
  library. New capabilities plug in via Protocols in
  `sanctum/core/protocols.py`.
- **No runtime network calls.** Model downloads are install-time only, gated
  by config, and follow the pattern in `sanctum.analyzer.nlp_config`.
- **Round-trip fidelity** for document adapters: a pass-through read → write
  must produce byte-equivalent output.
- **Test pyramid:** unit tests under `tests/unit/`, integration tests under
  `tests/integration/`, evaluation scoring under `tests/evaluation/`. The
  directory layout mirrors `sanctum/`.

## Running the quality gate locally

```bash
pre-commit run --all-files         # ruff, mypy, yaml/whitespace, secrets
pytest -m "not integration"         # fast unit tests
pytest -m integration               # full Presidio engine tests
pytest -m evaluation                # fixture-corpus scoring (opt-in)
```

The CI matrix runs Python 3.10, 3.11, and 3.12. Make sure your change passes
on the version you developed against, at minimum.

### Type checking

`mypy` runs under a **Tier 2** configuration: strict on `sanctum/core/`
(full annotations required), lenient everywhere else. If you touch a
Protocol or a domain model, you must fully type the change. Adapters, CLI,
and config code are encouraged but not forced.

### Coverage

The CI gate is **85% line coverage on `sanctum/core` and
`sanctum/documents`**, and **70% overall**. If a new adapter or module drops
coverage below the gate, add tests before merging.

## Commit style

- One logical change per commit; small is better than large.
- Subject ≤ 72 characters, imperative mood ("Add xlsx adapter", not "Added").
- Body explains *why*, not *what* — the diff already shows what.
- Reference issues with `Fixes #123` / `Refs #123` when relevant.

## Pull requests

- Open PRs against `main`.
- Keep PRs focused. Each of the Phase 1 workstreams (CI, adapters, mapping
  store, API, transformer tier) is broken down further in `plans/` at the
  repo root — each adapter and each milestone should land as its own PR.
- The first PR that changes lint/type rules should be **config-only**; land
  the mechanical fixes it surfaces in a separate "baseline fix" PR so review
  stays legible.

## Adding fixtures

Fixtures live under `tests/fixtures/`. For Office formats (docx/xlsx/pdf/pptx),
generate binaries deterministically via `scripts/generate_office_fixtures.py`
so CI stays offline. Never commit fixtures that contain real client data or
anything exceeding 500 KB (the pre-commit hook will block it).

## Questions

Open a discussion or issue. For security-sensitive reports, please email the
maintainer rather than filing a public issue.
