# Sanctum - Phase 0 Architecture & Project Foundation Plan

## Context

Sanctum is a greenfield local-first PII anonymization desktop app built on Microsoft Presidio. The repo currently contains only a README.md and .gitignore. Before writing any feature code, we need to establish a scalable project structure, define clean layer boundaries, and build a comprehensive test corpus that validates anonymization quality from day one.

The user's priority is **organization over code** — get the scaffold, interfaces, and test suite right so future development is scalable and easily fixable.

---

## Directory Structure

```
sanctum/
├── src/
│   └── sanctum/
│       ├── __init__.py                    # Version info
│       │
│       ├── core/                          # Domain layer — ZERO framework imports
│       │   ├── __init__.py
│       │   ├── protocols.py               # typing.Protocol interfaces (Analyzer, Anonymizer, DocumentReader, MappingStore)
│       │   ├── models.py                  # Pydantic data models (DetectionResult, AnonymizationResult, OperatorPolicy)
│       │   ├── engine.py                  # SanctumEngine orchestrator (composes analyzer + anonymizer via DI)
│       │   └── exceptions.py              # Domain-specific exceptions
│       │
│       ├── analyzer/                      # Presidio AnalyzerEngine adapter
│       │   ├── __init__.py
│       │   ├── adapter.py                 # Wraps AnalyzerEngine, converts RecognizerResult → DetectionResult
│       │   ├── nlp_config.py              # NlpEngineProvider setup (spaCy config)
│       │   └── recognizers/               # Custom recognizers (empty for now, ready for legal-domain)
│       │       └── __init__.py
│       │
│       ├── anonymizer/                    # Presidio AnonymizerEngine adapter
│       │   ├── __init__.py
│       │   ├── adapter.py                 # Wraps AnonymizerEngine, converts domain models ↔ Presidio types
│       │   └── operators/                 # Custom operators
│       │       ├── __init__.py
│       │       └── hips.py                # HIPS surrogate replacement via Faker (consistent per-session mapping)
│       │
│       ├── documents/                     # Document format adapters
│       │   ├── __init__.py
│       │   ├── base.py                    # DocumentReader/DocumentWriter protocol definitions
│       │   └── text.py                    # Plain text adapter (Phase 0 only)
│       │
│       ├── config/                        # Configuration management
│       │   ├── __init__.py
│       │   └── settings.py                # Pydantic Settings with SANCTUM_ env prefix, .env support
│       │
│       └── cli/                           # Click-based CLI (dev/testing interface)
│           ├── __init__.py
│           └── commands.py                # analyze, anonymize, config commands (Rich output)
│
├── tests/
│   ├── conftest.py                        # Shared pytest fixtures
│   │
│   ├── unit/                              # Fast, mocked — no real Presidio
│   │   ├── test_core/
│   │   │   ├── test_models.py
│   │   │   └── test_engine.py
│   │   ├── test_analyzer/
│   │   │   └── test_adapter.py
│   │   └── test_anonymizer/
│   │       └── test_adapter.py
│   │
│   ├── integration/                       # Real Presidio engines, real spaCy
│   │   ├── test_analyzer_integration.py
│   │   ├── test_anonymizer_integration.py
│   │   └── test_pipeline.py              # End-to-end: text in → anonymized text out
│   │
│   ├── evaluation/                        # Precision/recall/F1 scoring harness
│   │   ├── scorer.py                      # EntityScorer with EXACT and OVERLAP matching
│   │   ├── test_corpus.py                 # Runs all fixtures through scorer, reports metrics
│   │   └── conftest.py                    # Evaluation-specific fixtures and marks
│   │
│   └── fixtures/                          # Test document corpus
│       ├── README.md                      # Documents provenance, generation instructions
│       ├── schema.json                    # JSON Schema for annotation files
│       │
│       ├── legal/
│       │   ├── nda_contract.txt           # Synthetic NDA (Faker-generated)
│       │   ├── nda_contract.json          # Ground truth annotations
│       │   ├── engagement_letter.txt      # Synthetic engagement letter
│       │   ├── engagement_letter.json
│       │   ├── court_filing.txt           # Real public domain (CourtListener)
│       │   ├── court_filing.json
│       │   ├── cuad_contract.txt          # Excerpt from CUAD dataset
│       │   └── cuad_contract.json
│       │
│       ├── financial/
│       │   ├── sec_10k_excerpt.txt        # Real public domain (SEC EDGAR)
│       │   ├── sec_10k_excerpt.json
│       │   ├── bank_statement.txt         # Synthetic
│       │   ├── bank_statement.json
│       │   ├── invoice.txt                # Synthetic
│       │   └── invoice.json
│       │
│       ├── medical/
│       │   ├── discharge_summary.txt      # Synthetic (HIPAA 18 identifiers coverage)
│       │   ├── discharge_summary.json
│       │   ├── referral_letter.txt        # Synthetic
│       │   └── referral_letter.json
│       │
│       ├── hr/
│       │   ├── resume.txt                 # Synthetic
│       │   ├── resume.json
│       │   ├── offer_letter.txt           # Synthetic
│       │   └── offer_letter.json
│       │
│       ├── correspondence/
│       │   ├── attorney_client_email.txt  # Synthetic
│       │   ├── attorney_client_email.json
│       │   ├── internal_memo.txt          # Synthetic
│       │   └── internal_memo.json
│       │
│       └── edge_cases/
│           ├── max_density_pii.txt        # Hand-crafted: every PII type in one paragraph
│           ├── max_density_pii.json
│           ├── zero_pii_technical.txt     # Hand-crafted: no PII (false positive test)
│           └── zero_pii_technical.json
│
├── scripts/
│   ├── generate_fixtures.py              # Faker-based fixture generator (fixed seed for reproducibility)
│   └── fetch_public_docs.py             # Fetches SEC EDGAR + CourtListener samples
│
├── pyproject.toml                        # Project metadata, dependencies, tool config, entry points
├── .env.example                          # Config template
├── .gitignore
└── README.md
```

---

## Architectural Decisions

### 1. Protocol-Based Boundaries (core/protocols.py)

The core domain defines `typing.Protocol` interfaces — not ABC inheritance. Any object with the right shape satisfies the protocol. This makes testing trivial and keeps the core completely independent of Presidio.

```
Protocols to define:
- Analyzer: analyze(text, language, entities, score_threshold) → list[DetectionResult]
- Anonymizer: anonymize(text, detections, operator_policies) → AnonymizationResult
- DocumentReader: read(path) → str
- DocumentWriter: write(path, content) → None
- MappingStore: save/load/delete mappings (deferred to Phase 1)
```

### 2. Domain Models (core/models.py)

Presidio's `RecognizerResult` only carries `entity_type, start, end, score`. Our adapter enriches it into `DetectionResult` which also includes the actual text span and surrounding context window. This means downstream consumers (CLI, future GUI) never need to re-read the source text.

```
Key models:
- DetectionResult: entity_type, start, end, score, text_span, context, recognizer_name
- AnonymizationResult: original_text, anonymized_text, detections, operator_applied
- OperatorPolicy: entity_type → operator_name + params mapping
```

### 3. Human-in-the-Loop Seam

`SanctumEngine.anonymize()` accepts an optional `detections` parameter:
- When `None`: runs detection internally (batch mode)
- When provided: skips detection, uses supplied detections (after human review)

This is the architectural insertion point for the future GUI review workflow.

### 4. Composition Root Pattern

Only the CLI (and later Flask API, later GUI) instantiates adapters and wires them together. The core engine never imports Presidio, Click, or Flask directly. This means we can swap Presidio for another engine, or swap CLI for GUI, without touching domain logic.

### 5. HIPS Operator Consistency

The custom HIPS operator uses an in-memory `dict[str, str]` so "John Smith" always maps to the same Faker replacement within a session. Persistent encrypted mapping store is deferred to Phase 1.

---

## Test Suite Design

### Ground Truth Annotation Format

Each `.json` annotation file alongside its `.txt` document:

```json
{
  "document": "nda_contract.txt",
  "provenance": "synthetic",
  "generator": "scripts/generate_fixtures.py",
  "seed": 42,
  "entities": [
    {
      "entity_type": "PERSON",
      "start": 47,
      "end": 57,
      "text": "John Smith",
      "context": "between the parties, John Smith (hereinafter"
    }
  ],
  "expected_counts": {
    "PERSON": 4,
    "LOCATION": 2,
    "EMAIL_ADDRESS": 1,
    "PHONE_NUMBER": 1
  }
}
```

Validated by `tests/fixtures/schema.json` (JSON Schema).

### Evaluation Scorer

`tests/evaluation/scorer.py` implements `EntityScorer`:
- **EXACT mode**: span boundaries must match precisely
- **OVERLAP mode** (default): any character overlap counts as a match (Presidio often detects slightly different boundaries)
- Reports per-entity-type: precision, recall, F1
- Outputs a confusion list for debugging false positives/negatives
- Runs as a separate pytest mark (`@pytest.mark.evaluation`) so it doesn't slow down unit tests

### 15 Test Documents

| # | Category | Document | Source | Key Entity Types |
|---|----------|----------|--------|-----------------|
| 1 | Legal | NDA contract | Synthetic (Faker) | PERSON, ORG, DATE, LOCATION, EMAIL |
| 2 | Legal | Engagement letter | Synthetic (Faker) | PERSON, ORG, PHONE, EMAIL, DATE |
| 3 | Legal | Court filing | Public domain (CourtListener) | PERSON, LOCATION, DATE, case numbers |
| 4 | Legal | CUAD contract excerpt | Public domain (CUAD dataset) | PERSON, ORG, DATE, LOCATION |
| 5 | Financial | SEC 10-K excerpt | Public domain (SEC EDGAR) | PERSON, ORG, LOCATION, financial figures |
| 6 | Financial | Bank statement | Synthetic (Faker) | PERSON, US_BANK_NUMBER, DATE, LOCATION |
| 7 | Financial | Invoice | Synthetic (Faker) | PERSON, ORG, EMAIL, PHONE, IBAN |
| 8 | Medical | Discharge summary | Synthetic (Faker) | PERSON, DATE, LOCATION, US_SSN, medical terms |
| 9 | Medical | Referral letter | Synthetic (Faker) | PERSON, ORG, PHONE, DATE, LOCATION |
| 10 | HR | Resume | Synthetic (Faker) | PERSON, EMAIL, PHONE, LOCATION, URL |
| 11 | HR | Offer letter | Synthetic (Faker) | PERSON, ORG, DATE, LOCATION, salary |
| 12 | Correspondence | Attorney-client email | Synthetic (Faker) | PERSON, EMAIL, ORG, DATE, case refs |
| 13 | Correspondence | Internal memo | Synthetic (Faker) | PERSON, ORG, DATE, PHONE |
| 14 | Edge case | Max-density PII | Hand-crafted | ALL entity types in one paragraph |
| 15 | Edge case | Zero-PII technical doc | Hand-crafted | NONE (false positive testing) |

---

## Implementation Sequence (9 Steps)

Each step is independently testable. Steps 1-7 are the project scaffold. Steps 8-9 are the test corpus.

1. **Project scaffold** — Create directory structure + `pyproject.toml` with all dependencies
2. **Core domain models** — `protocols.py` + `models.py` (Pydantic models, Protocol interfaces)
3. **Presidio analyzer adapter** — `analyzer/adapter.py` + `nlp_config.py` (wraps AnalyzerEngine)
4. **Presidio anonymizer adapter** — `anonymizer/adapter.py` + `operators/hips.py` (wraps AnonymizerEngine + HIPS)
5. **SanctumEngine orchestrator** — `core/engine.py` (composes analyzer + anonymizer, human-in-the-loop seam)
6. **Configuration system** — `config/settings.py` (Pydantic Settings)
7. **CLI skeleton** — `cli/commands.py` (Click + Rich, analyze/anonymize commands)
8. **Test fixtures** — Generate synthetic documents + fetch public domain samples + hand-craft edge cases
9. **Evaluation harness** — `evaluation/scorer.py` + `test_corpus.py` (precision/recall/F1)

---

## What NOT to Build Yet

| Deferred | Why | When |
|----------|-----|------|
| Flask API | No GUI consumer yet; CLI is sufficient for Phase 0 | Phase 1 (v0.2) |
| Electron/native GUI | Depends on stable API layer | Phase 2 (v0.3) |
| Encrypted SQLite mapping store | HIPS in-memory dict is sufficient for testing | Phase 1 (v0.2) |
| Document format adapters (docx/xlsx/pdf) | Plain text exercises the full pipeline; formats are an adapter concern | Phase 1 (v0.2) |
| PDF burn-in redaction | Complex, depends on stable anonymizer | Phase 2 (v0.4) |
| Transformer NER tier | spaCy sm is sufficient for architecture validation | Phase 3 (v1.0) |
| Custom legal recognizers | Need evaluation data first to know what's missing | After test suite is stable |
| Risk scoring | Needs research into identifiability metrics | Phase 1 (v0.2) |
| PyInstaller packaging | Premature until the app is functional | Phase 2 (v0.3) |
| CI/CD pipeline | Set up after first meaningful test suite exists | After Step 9 |

---

## Verification

After implementation, verify the foundation works end-to-end:

1. **Install**: `pip install -e ".[dev]"` succeeds, `python -m spacy download en_core_web_sm` completes
2. **Unit tests**: `pytest tests/unit/ -v` — all pass with mocked Presidio
3. **Integration tests**: `pytest tests/integration/ -v` — real Presidio pipeline processes text
4. **CLI smoke test**: `sanctum analyze "My SSN is 123-45-6789 and my name is John Smith"` — outputs detected entities with scores
5. **CLI anonymize**: `sanctum anonymize "..." --operator redact` — outputs anonymized text
6. **Fixture generation**: `python scripts/generate_fixtures.py` — produces all 10 synthetic documents with annotations
7. **Public doc fetch**: `python scripts/fetch_public_docs.py` — downloads SEC EDGAR + CourtListener samples
8. **Evaluation**: `pytest tests/evaluation/ -v` — runs scorer against full corpus, reports per-entity F1
