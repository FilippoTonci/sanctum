# Sanctum Implementation Plan — Phase 0 Foundation

## 1. Directory Structure

```
sanctum/
├── .claude/
├── .github/
│   └── workflows/           # CI later — skip for now
├── .gitignore
├── README.md
├── LICENSE
├── pyproject.toml
├── conftest.py              # Root conftest — shared pytest fixtures
│
├── src/
│   └── sanctum/
│       ├── __init__.py      # Package version, top-level imports
│       ├── py.typed         # PEP 561 marker
│       │
│       ├── core/            # Domain layer — zero framework imports
│       │   ├── __init__.py
│       │   ├── protocols.py # typing.Protocol interfaces (Analyzer, Anonymizer, DocumentReader, MappingStore)
│       │   ├── models.py    # Pydantic data models (DetectionResult, AnonymizationResult, EntityPolicy, etc.)
│       │   └── engine.py    # SanctumEngine — orchestrator that composes analyzer + anonymizer
│       │
│       ├── analyzer/        # Presidio AnalyzerEngine adapter
│       │   ├── __init__.py
│       │   ├── adapter.py   # PresidioAnalyzerAdapter (implements core.protocols.Analyzer)
│       │   ├── recognizers/
│       │   │   ├── __init__.py
│       │   │   └── legal.py # Custom legal-domain recognizers (case numbers, bar IDs, Bates numbers)
│       │   └── nlp_config.py  # NlpEngineProvider configuration (spaCy tier vs transformer tier)
│       │
│       ├── anonymizer/      # Presidio AnonymizerEngine adapter
│       │   ├── __init__.py
│       │   ├── adapter.py   # PresidioAnonymizerAdapter (implements core.protocols.Anonymizer)
│       │   └── operators/
│       │       ├── __init__.py
│       │       └── hips.py  # HIPS Faker-based replacement operator
│       │
│       ├── documents/       # Document format adapters (Phase 0: plain text only)
│       │   ├── __init__.py
│       │   ├── base.py      # DocumentReader/DocumentWriter protocols + TextDocument model
│       │   └── text.py      # PlainTextAdapter — trivial, but establishes the pattern
│       │   # Future: docx.py, xlsx.py, pdf.py
│       │
│       ├── config/          # Configuration system
│       │   ├── __init__.py
│       │   └── settings.py  # Pydantic Settings — SanctumSettings with env var support
│       │
│       └── cli/             # Click CLI — development/testing interface
│           ├── __init__.py
│           └── main.py      # Click group: analyze, anonymize, batch, config
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py          # Test-scoped fixtures: engine instances, sample texts
│   │
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── test_models.py
│   │   │   └── test_engine.py
│   │   ├── analyzer/
│   │   │   ├── __init__.py
│   │   │   ├── test_adapter.py
│   │   │   └── test_recognizers.py
│   │   ├── anonymizer/
│   │   │   ├── __init__.py
│   │   │   ├── test_adapter.py
│   │   │   └── test_operators.py
│   │   └── documents/
│   │       ├── __init__.py
│   │       └── test_text.py
│   │
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── test_pipeline.py       # Full analyze → anonymize pipeline
│   │   └── test_cli.py            # Click CLI runner tests
│   │
│   ├── evaluation/
│   │   ├── __init__.py
│   │   ├── conftest.py            # Evaluation-specific fixtures
│   │   ├── test_precision_recall.py  # Parameterized P/R/F1 by entity type
│   │   ├── scorer.py              # Scoring harness (not a test, a utility)
│   │   └── annotations.py         # Ground truth loader
│   │
│   └── fixtures/
│       ├── README.md              # Documents provenance + licensing notes
│       ├── annotations/           # Ground truth JSON files
│       │   ├── schema.json        # JSON Schema for annotation format
│       │   └── *.json             # One per fixture document
│       ├── texts/                 # Plain text fixtures
│       │   ├── legal/
│       │   ├── financial/
│       │   ├── medical/
│       │   ├── hr/
│       │   ├── correspondence/
│       │   └── edge_cases/
│       └── generated/             # Faker-generated synthetic docs (gitignored, regenerable)
│           └── .gitkeep
```

### Rationale for structure decisions:

- **`src/` layout**: Prevents accidental import of the source tree from the repo root during testing. The installed package path is `sanctum.*`, not `src.sanctum.*`.
- **`core/` has zero framework imports**: `core.protocols` and `core.models` import only from `typing`, `pydantic`, and stdlib. Never from `presidio_analyzer` or `presidio_anonymizer`. This is the hexagonal architecture boundary.
- **`analyzer/` and `anonymizer/` are separate adapter packages**: They each wrap one Presidio engine. They import from `core` and from `presidio_*`, but never from each other.
- **`documents/` starts with text-only**: Phase 0 only processes plain text strings. The `text.py` adapter is trivial but establishes the interface that `docx.py`, `pdf.py`, `xlsx.py` will implement later.
- **No `api/` or `gui/` directories yet**: Flask API and Electron GUI are future phases. Creating empty directories signals intent but wastes attention.
- **`tests/evaluation/` is separate from `tests/unit/`**: Evaluation tests are slow (load full NLP models, process real documents) and measure quality metrics, not correctness. They should run under a separate pytest mark.
- **`tests/fixtures/` is a data directory, not a Python package**: No `__init__.py`. Contains text files and JSON annotations, not code.

---

## 2. Phase 0 Deliverables — Detailed Specification

### 2.1 pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "sanctum"
version = "0.1.0"
description = "Local-first PII anonymization for professionals"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
dependencies = [
    # Core engine
    "presidio-analyzer>=2.2.350",
    "presidio-anonymizer>=2.2.350",
    
    # NLP — standard tier
    "spacy>=3.4.4,!=3.7.0",
    
    # Synthetic data replacement
    "faker>=20.0",
    
    # Configuration
    "pydantic>=2.0,<3.0",
    "pydantic-settings>=2.0,<3.0",
    
    # CLI
    "click>=8.0",
    "rich>=13.0",          # Pretty terminal output for detection results
]

[project.optional-dependencies]
# Document processing — not needed for Phase 0 text-only
docs = [
    "python-docx>=1.0",
    "openpyxl>=3.1",
    "pdfplumber>=0.10",
]
# Professional NLP tier
pro = [
    "transformers>=4.30",
    "torch>=2.0",
]
# API server — future
api = [
    "flask>=3.0",
]
# Development
dev = [
    "pytest>=8.0",
    "pytest-cov>=4.0",
    "pytest-mock>=3.12",
    "ruff>=0.4",
    "mypy>=1.8",
    "pre-commit>=3.6",
]

[project.scripts]
sanctum = "sanctum.cli.main:cli"

[tool.hatch.build.targets.wheel]
packages = ["src/sanctum"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "slow: marks tests that load full NLP models",
    "evaluation: marks precision/recall evaluation tests",
]
addopts = "-m 'not slow and not evaluation'"
pythonpath = ["src"]

[tool.ruff]
src = ["src"]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "SIM", "TCH"]

[tool.mypy]
python_version = "3.10"
strict = true
packages = ["sanctum"]
mypy_path = "src"
```

**Key decisions:**
- Hatchling over Poetry: simpler, PEP 621 native, no lock file drama for a solo developer.
- `rich` for CLI output: detection results need colored, tabular display. Rich is lightweight and Click-compatible.
- Document processing (`python-docx`, etc.) as optional deps: keeps base install small for Phase 0.
- Separate `[dev]` extras: `pip install -e ".[dev]"` for development.
- Default pytest excludes `slow` and `evaluation` markers: fast feedback loop.

### 2.2 Core Domain Layer

#### `core/protocols.py` — The Hexagonal Boundary

```python
from typing import Protocol, Sequence
from sanctum.core.models import DetectionResult, AnonymizationResult, OperatorPolicy

class Analyzer(Protocol):
    """Port: PII detection engine."""
    def analyze(
        self,
        text: str,
        *,
        entities: Sequence[str] | None = None,
        language: str = "en",
        score_threshold: float = 0.0,
    ) -> list[DetectionResult]: ...

class Anonymizer(Protocol):
    """Port: PII transformation engine."""
    def anonymize(
        self,
        text: str,
        detections: Sequence[DetectionResult],
        *,
        operators: dict[str, OperatorPolicy] | None = None,
    ) -> AnonymizationResult: ...

class DocumentReader(Protocol):
    """Port: reads a file into processable text segments."""
    def read(self, path: str) -> list[str]: ...
    def supported_extensions(self) -> set[str]: ...

class DocumentWriter(Protocol):
    """Port: writes anonymized content back to a file format."""
    def write(self, path: str, segments: list[str]) -> None: ...

class MappingStore(Protocol):
    """Port: persistent pseudonym-to-original mapping store."""
    def get(self, entity_type: str, original: str) -> str | None: ...
    def put(self, entity_type: str, original: str, replacement: str) -> None: ...
    def clear(self) -> None: ...
```

**Why Protocol, not ABC:** Structural subtyping. Adapters don't need to inherit from anything; they just need to have the right methods. This makes testing trivial — any object with the right shape works as a mock.

#### `core/models.py` — Domain Data Models

```python
from pydantic import BaseModel, Field
from enum import StrEnum

class OperatorType(StrEnum):
    REDACT = "redact"
    REPLACE = "replace"
    HIPS = "hips"
    HASH = "hash"
    ENCRYPT = "encrypt"
    MASK = "mask"
    KEEP = "keep"

class DetectionResult(BaseModel):
    """A single PII detection — decoupled from Presidio's RecognizerResult."""
    entity_type: str
    text: str                        # The actual detected text span
    start: int
    end: int
    score: float = Field(ge=0.0, le=1.0)
    recognizer: str = ""             # Which recognizer found this
    context: str = ""                # Surrounding context for decision tracing

class OperatorPolicy(BaseModel):
    """Configuration for how to handle a specific entity type."""
    operator: OperatorType = OperatorType.REDACT
    params: dict = Field(default_factory=dict)

class AnonymizationItem(BaseModel):
    """One replacement made during anonymization."""
    entity_type: str
    original_text: str
    replacement_text: str
    operator: OperatorType
    start: int                       # Position in OUTPUT text
    end: int                         # Position in OUTPUT text
    score: float

class AnonymizationResult(BaseModel):
    """Complete result of an anonymization operation."""
    text: str                        # The anonymized text
    items: list[AnonymizationItem]   # What was replaced and how
    
    @property
    def entity_count(self) -> int:
        return len(self.items)
    
    @property
    def entities_by_type(self) -> dict[str, list[AnonymizationItem]]:
        result: dict[str, list[AnonymizationItem]] = {}
        for item in self.items:
            result.setdefault(item.entity_type, []).append(item)
        return result
```

**Why Pydantic, not dataclasses:** Validation (score must be 0-1), serialization to JSON for the API layer, and Settings integration later. The overhead is minimal and the payoff is large.

**Why decouple from RecognizerResult:** `DetectionResult` includes `text` (the actual span) and `context` (surrounding text) that Presidio's `RecognizerResult` doesn't carry. It also avoids leaking Presidio types into the core domain.

#### `core/engine.py` — The Orchestrator

```python
class SanctumEngine:
    """Composes an Analyzer and Anonymizer into a single pipeline.
    
    This is the primary entry point for the domain layer.
    Injected dependencies, no framework imports.
    """
    def __init__(
        self,
        analyzer: Analyzer,
        anonymizer: Anonymizer,
        *,
        default_policies: dict[str, OperatorPolicy] | None = None,
        score_threshold: float = 0.35,
        language: str = "en",
    ) -> None: ...
    
    def detect(self, text: str, **kwargs) -> list[DetectionResult]:
        """Run detection only — for human-in-the-loop review step."""
        ...
    
    def anonymize(
        self,
        text: str,
        *,
        detections: list[DetectionResult] | None = None,
        policies: dict[str, OperatorPolicy] | None = None,
    ) -> AnonymizationResult:
        """Run full pipeline, or anonymize pre-reviewed detections."""
        ...
```

**Critical design: the `detections` parameter.** If `None`, the engine runs detection internally. If provided (from human review), it skips detection and goes straight to anonymization. This is the seam where the GUI's human-in-the-loop review will plug in.

### 2.3 Presidio Adapters

#### `analyzer/adapter.py`

```python
class PresidioAnalyzerAdapter:
    """Adapter: wraps presidio_analyzer.AnalyzerEngine to implement core.protocols.Analyzer."""
    
    def __init__(
        self,
        nlp_engine: NlpEngine | None = None,
        registry: RecognizerRegistry | None = None,
        supported_languages: list[str] | None = None,
    ) -> None:
        # Build AnalyzerEngine from injected or default components
        ...
    
    def analyze(self, text, *, entities=None, language="en", score_threshold=0.0):
        # Call self._engine.analyze(...)
        # Convert List[RecognizerResult] → List[DetectionResult]
        # Enrich with text spans and context
        ...
```

**Conversion logic:** The adapter extracts the actual text span (`text[result.start:result.end]`) and a context window (e.g., 50 chars each side) and packages them into `DetectionResult`. This enrichment happens at the adapter boundary, not in the core.

#### `anonymizer/adapter.py`

```python
class PresidioAnonymizerAdapter:
    """Adapter: wraps presidio_anonymizer.AnonymizerEngine to implement core.protocols.Anonymizer."""
    
    def __init__(self) -> None:
        self._engine = AnonymizerEngine()
        # Register custom operators (HIPS, etc.)
        ...
    
    def anonymize(self, text, detections, *, operators=None):
        # Convert DetectionResult → RecognizerResult (reverse mapping)
        # Convert OperatorPolicy → OperatorConfig
        # Call self._engine.anonymize(...)
        # Convert EngineResult → AnonymizationResult
        ...
```

#### `anonymizer/operators/hips.py` — Faker-Based Replacement

```python
class HipsOperator(Operator):
    """Custom Presidio operator: replaces PII with contextually plausible Faker data.
    
    Maintains a mapping dict for consistency within a session —
    same input always produces same output.
    """
    
    ENTITY_TO_FAKER = {
        "PERSON": "name",
        "PHONE_NUMBER": "phone_number",
        "EMAIL_ADDRESS": "email",
        "LOCATION": "city",
        "ORGANIZATION": "company",
        "US_SSN": "ssn",
        "CREDIT_CARD": "credit_card_number",
        "DATE_TIME": "date",
        "URL": "url",
        "IP_ADDRESS": "ipv4",
        "IBAN_CODE": "iban",
        "US_DRIVER_LICENSE": "bothify",  # Pattern-based
    }
    
    # NOT thread-safe — documented limitation, matches Presidio's own warning
    _mapping: dict[str, str]
    _faker: Faker
```

### 2.4 Configuration System

#### `config/settings.py`

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class NlpSettings(BaseSettings):
    engine: str = "spacy"                       # "spacy" | "transformers"
    model: str = "en_core_web_sm"               # spaCy model name
    language: str = "en"

class AnalyzerSettings(BaseSettings):
    score_threshold: float = 0.35               # Default confidence threshold
    entities: list[str] | None = None           # None = all entities
    custom_recognizers_path: str | None = None  # Path to YAML recognizer config

class AnonymizerSettings(BaseSettings):
    default_operator: str = "redact"
    hips_locale: str = "en_US"                  # Faker locale for HIPS
    hips_seed: int | None = None                # Reproducible Faker output for testing

class SanctumSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SANCTUM_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
    
    nlp: NlpSettings = NlpSettings()
    analyzer: AnalyzerSettings = AnalyzerSettings()
    anonymizer: AnonymizerSettings = AnonymizerSettings()
    
    log_level: str = "INFO"
    data_dir: str = "~/.sanctum"               # Mapping store, logs, config
```

**Environment variable examples:**
- `SANCTUM_NLP__ENGINE=transformers`
- `SANCTUM_ANALYZER__SCORE_THRESHOLD=0.5`
- `SANCTUM_ANONYMIZER__DEFAULT_OPERATOR=hips`

### 2.5 CLI Skeleton

#### `cli/main.py`

```python
import click
from rich.console import Console
from rich.table import Table

@click.group()
@click.option("--config", type=click.Path(), help="Path to .env config file")
@click.option("--threshold", type=float, default=None, help="Override score threshold")
@click.pass_context
def cli(ctx, config, threshold):
    """Sanctum — Local PII anonymization for professionals."""
    # Load SanctumSettings, build engine, store in ctx.obj
    ...

@cli.command()
@click.argument("text", required=False)
@click.option("--file", "-f", type=click.Path(exists=True))
@click.option("--entities", "-e", multiple=True)
@click.option("--json-output", is_flag=True)
def analyze(text, file, entities, json_output):
    """Detect PII entities in text or a file."""
    # Display results as Rich table or JSON
    ...

@cli.command()
@click.argument("text", required=False)
@click.option("--file", "-f", type=click.Path(exists=True))
@click.option("--operator", "-o", type=click.Choice(["redact", "hips", "hash", "mask", "encrypt"]))
@click.option("--output", "-O", type=click.Path())
def anonymize(text, file, operator, output):
    """Anonymize PII in text or a file."""
    ...

@cli.command()
def config():
    """Show current configuration."""
    ...
```

**CLI usage examples (what Phase 0 enables):**

```bash
# Detect PII in a string
sanctum analyze "Call John Smith at 555-0123 about case #2024-CV-1234"

# Detect PII in a file
sanctum analyze -f contract.txt --entities PERSON PHONE_NUMBER

# Anonymize with default (redact)
sanctum anonymize "John Smith, SSN 123-45-6789"

# Anonymize with HIPS replacement
sanctum anonymize -f deposition.txt -o clean.txt --operator hips

# Show config
sanctum config
```

---

## 3. Test Suite Architecture

### 3.1 Test Fixture Organization

#### Ground Truth Annotation Format

Each fixture document has a companion JSON annotation file:

```json
{
  "$schema": "./schema.json",
  "document_id": "legal-001-nda",
  "source": "synthetic/faker",
  "category": "legal",
  "subcategory": "nda",
  "language": "en",
  "text_file": "../texts/legal/nda-basic.txt",
  "entities": [
    {
      "entity_type": "PERSON",
      "text": "John A. Smith",
      "start": 145,
      "end": 158,
      "context": "This Agreement is entered into by John A. Smith (\"Disclosing Party\")"
    },
    {
      "entity_type": "ORGANIZATION",
      "text": "Acme Industries, LLC",
      "start": 203,
      "end": 223
    },
    {
      "entity_type": "DATE_TIME",
      "text": "January 15, 2024",
      "start": 78,
      "end": 94
    },
    {
      "entity_type": "PHONE_NUMBER",
      "text": "(555) 867-5309",
      "start": 412,
      "end": 426
    }
  ],
  "expected_entities_count": {
    "PERSON": 4,
    "ORGANIZATION": 2,
    "DATE_TIME": 3,
    "PHONE_NUMBER": 2,
    "EMAIL_ADDRESS": 2,
    "LOCATION": 1
  },
  "notes": "Standard bilateral NDA. Names, company, dates, contact info."
}
```

**Why this format:**
- `text_file` points to the actual document, keeping annotation separate from content.
- `entities` array with character offsets enables exact span matching for evaluation.
- `expected_entities_count` enables quick smoke tests without full span matching.
- `source` field tracks provenance (synthetic vs. public domain vs. scraped).
- `context` is optional but helps debugging false negatives.

#### JSON Schema (`tests/fixtures/annotations/schema.json`)

Define a JSON Schema for the annotation format to validate fixtures automatically. A conftest fixture should validate all annotation files against the schema on test collection.

### 3.2 Scoring Harness

#### `tests/evaluation/scorer.py`

```python
class EntityScorer:
    """Measures detection quality by entity type.
    
    Supports two matching modes:
    - EXACT: start and end offsets must match exactly
    - OVERLAP: any character overlap counts as a match (more lenient)
    """
    
    def score(
        self,
        predictions: list[DetectionResult],
        ground_truth: list[AnnotatedEntity],
        mode: MatchMode = MatchMode.OVERLAP,
    ) -> ScoreReport:
        """Returns per-entity-type and aggregate P/R/F1."""
        ...

class ScoreReport(BaseModel):
    overall: Metrics          # precision, recall, f1
    by_entity: dict[str, Metrics]
    confusion: list[ConfusionEntry]  # FP/FN details for debugging
    
class Metrics(BaseModel):
    precision: float
    recall: float
    f1: float
    support: int              # Number of ground truth instances
```

**Why overlap matching as default:** Presidio often detects "John Smith" as positions 10-20 while ground truth marks "John A. Smith" as 10-23. Exact matching would report this as both a false positive and a false negative, which misrepresents the model's capability. Overlap matching counts it as a true positive with a note about span mismatch.

#### `tests/evaluation/test_precision_recall.py`

```python
@pytest.mark.evaluation
@pytest.mark.parametrize("fixture_path", glob("tests/fixtures/annotations/*.json"))
def test_detection_quality(fixture_path, sanctum_engine, scorer):
    """Parameterized evaluation: one test per fixture document."""
    annotation = load_annotation(fixture_path)
    text = Path(annotation.text_file).read_text()
    
    predictions = sanctum_engine.detect(text)
    report = scorer.score(predictions, annotation.entities)
    
    # Log metrics for human review — don't hard-fail on thresholds yet
    log_metrics(annotation.document_id, report)
    
    # Soft assertions: warn on regression, fail on catastrophic drops
    assert report.overall.recall > 0.3, f"Catastrophic recall drop on {annotation.document_id}"
```

**Why soft thresholds:** Phase 0 is establishing baselines, not enforcing them. The test logs metrics to a report file; hard thresholds come after tuning.

### 3.3 Concrete Test Document Corpus (15 documents)

#### Category 1: Legal (4 documents)

| ID | Document | Source | Key Entities | Why This Document |
|----|----------|--------|-------------|-------------------|
| `legal-001-nda` | Bilateral NDA | Faker-generated synthetic | PERSON x4, ORG x2, DATE x3, PHONE x2, EMAIL x2, LOCATION x1 | Baseline legal contract; controlled PII density |
| `legal-002-engagement` | Attorney engagement letter | Faker-generated synthetic | PERSON x3, ORG x2, US_SSN x1, PHONE x2, EMAIL x2, DATE x4, LOCATION x2, CREDIT_CARD x1 | Attorney-client context; tests privilege-sensitive content |
| `legal-003-court-filing` | Civil complaint (public court filing) | PACER/RECAP Archive (free public PACER opinions) or CourtListener API | PERSON x8+, ORG x4+, DATE x6+, LOCATION x3+, PHONE x1+, case numbers (custom) | Real legal language; complex sentence structures; party names in context |
| `legal-004-contract-cuad` | Commercial license agreement excerpt | CUAD dataset (public, CC BY 4.0) | PERSON x2, ORG x6+, DATE x10+, LOCATION x2+, PHONE x1 | Real contract language; tests dense date detection and org disambiguation |

#### Category 2: Financial (3 documents)

| ID | Document | Source | Key Entities | Why This Document |
|----|----------|--------|-------------|-------------------|
| `fin-001-10k-excerpt` | 10-K annual report excerpt (risk factors section) | SEC EDGAR (public domain, zero restrictions) | ORG x10+, PERSON x3+ (officers), DATE x5+, LOCATION x2+, financial figures | Real-world corporate disclosure; dense org/person co-reference |
| `fin-002-bank-statement` | Personal bank statement | Faker-generated synthetic | PERSON x1, US_BANK_NUMBER x1, CREDIT_CARD x1, IBAN x1, DATE x6+, PHONE x1, LOCATION x2 | Tests financial identifiers: account numbers, credit cards, IBANs |
| `fin-003-invoice` | Business invoice | Faker-generated synthetic | PERSON x2, ORG x2, EMAIL x2, PHONE x2, LOCATION x4, DATE x2, US_BANK_NUMBER x1 | Structured data in prose; addresses, payment details |

#### Category 3: Medical (2 documents)

| ID | Document | Source | Key Entities | Why This Document |
|----|----------|--------|-------------|-------------------|
| `med-001-discharge` | Hospital discharge summary | Faker-generated (using HIPAA 18 identifiers checklist) | PERSON x3, DATE x5+, MEDICAL_LICENSE x1, PHONE x2, LOCATION x2, US_SSN x1, age, MRN (custom) | Covers all 18 HIPAA safe harbor identifiers; medical context |
| `med-002-referral` | Physician referral letter | Faker-generated synthetic | PERSON x4, ORG x2, DATE x3, PHONE x2, MEDICAL_LICENSE x1, EMAIL x1 | Doctor-to-doctor communication; professional medical language |

#### Category 4: HR (2 documents)

| ID | Document | Source | Key Entities | Why This Document |
|----|----------|--------|-------------|-------------------|
| `hr-001-resume` | Professional resume/CV | Faker-generated synthetic | PERSON x1, EMAIL x1, PHONE x1, LOCATION x1, ORG x4+, DATE x6+, URL x2 | Dense PII in structured-ish format; LinkedIn URLs, education dates |
| `hr-002-offer-letter` | Employment offer letter | Faker-generated synthetic | PERSON x2, ORG x1, DATE x3, LOCATION x1, PHONE x1, EMAIL x1, US_SSN x1, salary (custom) | Sensitive HR document; compensation figures as quasi-identifiers |

#### Category 5: Correspondence (2 documents)

| ID | Document | Source | Key Entities | Why This Document |
|----|----------|--------|-------------|-------------------|
| `email-001-client` | Attorney-client email thread | Faker-generated synthetic | PERSON x4+, EMAIL x3+, PHONE x2, DATE x3, ORG x2, case references | Email headers + body; reply threading; mixed formality levels |
| `email-002-internal` | Internal team memo with attachments ref | Faker-generated synthetic | PERSON x5+, ORG x1, DATE x2, LOCATION x1, EMAIL x3, IP_ADDRESS x1, URL x2 | Corporate memo language; IP addresses, URLs as PII |

#### Category 6: Edge Cases (2 documents)

| ID | Document | Source | Key Entities | Why This Document |
|----|----------|--------|-------------|-------------------|
| `edge-001-dense` | Paragraph with maximum PII density | Hand-crafted | Every supported entity type at least once | Stress test: adjacent entities, overlapping patterns, ambiguous spans |
| `edge-002-clean` | Technical documentation with zero PII | Hand-crafted (e.g., a Python docstring or API spec) | None — expect zero detections | False positive baseline: numbers that look like SSNs, company-like words that aren't orgs |

### 3.4 Test Document Acquisition Strategy

**Synthetic documents (10 of 15):** Use a Python script (`scripts/generate_fixtures.py`) that:
1. Uses Faker with a fixed seed (reproducible) to generate realistic PII values
2. Inserts them into handcrafted document templates
3. Automatically generates the annotation JSON with exact character offsets
4. This script is committed; generated documents are committed too (they're small text files)

**Public domain documents (3 of 15):**
- `legal-003-court-filing`: Download from CourtListener.com REST API (free, no auth needed for public opinions) or manually save a public PACER opinion
- `legal-004-contract-cuad`: Download from the CUAD dataset on GitHub/HuggingFace (CC BY 4.0)
- `fin-001-10k-excerpt`: Download from SEC EDGAR full-text search (public domain, no restrictions)

**Hand-crafted documents (2 of 15):**
- `edge-001-dense` and `edge-002-clean`: Written by hand to test specific edge cases

**Annotation for public documents:** These require manual annotation. Create the annotation JSON by:
1. Running Sanctum's own analyzer on the text
2. Manually reviewing and correcting the output
3. Saving as the ground truth annotation

This is the "bootstrap" problem — the first annotations are manual, but they become the baseline for all future regression testing.

### 3.5 Fixture conftest.py

```python
# tests/conftest.py

@pytest.fixture(scope="session")
def nlp_model():
    """Load spaCy model once for the entire test session."""
    import spacy
    return spacy.load("en_core_web_sm")

@pytest.fixture(scope="session")
def sanctum_engine(nlp_model):
    """Build a fully configured SanctumEngine for integration tests."""
    from sanctum.analyzer.adapter import PresidioAnalyzerAdapter
    from sanctum.anonymizer.adapter import PresidioAnonymizerAdapter
    from sanctum.core.engine import SanctumEngine
    
    analyzer = PresidioAnalyzerAdapter()
    anonymizer = PresidioAnonymizerAdapter()
    return SanctumEngine(analyzer=analyzer, anonymizer=anonymizer)

@pytest.fixture
def sample_text_with_pii():
    """Quick inline fixture for unit tests that don't need file I/O."""
    return "John Smith called 555-867-5309 about account #1234567890."

@pytest.fixture
def mock_analyzer():
    """Returns a mock Analyzer that returns canned DetectionResults."""
    ...

@pytest.fixture
def mock_anonymizer():
    """Returns a mock Anonymizer that returns canned AnonymizationResults."""
    ...
```

---

## 4. Layer Separation — Interface Contracts

### Layer Diagram

```
┌─────────────────────────────────────────────────┐
│  CLI (click)  │  API (flask, future)  │  GUI    │
│               │                       │ (future)│
│  Presentation layer — thin, calls engine only   │
└──────────────────────┬──────────────────────────┘
                       │ uses
┌──────────────────────▼──────────────────────────┐
│              core.engine.SanctumEngine           │
│                                                  │
│  Depends ONLY on core.protocols + core.models    │
│  No Presidio imports, no Flask imports, no Click │
└────────┬─────────────────────────┬──────────────┘
         │ implements              │ implements
┌────────▼────────┐    ┌──────────▼──────────────┐
│ analyzer/       │    │ anonymizer/              │
│ adapter.py      │    │ adapter.py               │
│                 │    │ operators/hips.py         │
│ Imports:        │    │                          │
│ - presidio_*    │    │ Imports:                 │
│ - core.*        │    │ - presidio_*             │
│                 │    │ - core.*                 │
│                 │    │ - faker                  │
└─────────────────┘    └──────────────────────────┘
         │                         │
┌────────▼─────────────────────────▼──────────────┐
│              documents/                          │
│  base.py — DocumentReader / DocumentWriter       │
│  text.py, docx.py (future), pdf.py (future)     │
│                                                  │
│  Imports: core.* + format-specific libs          │
└─────────────────────────────────────────────────┘
```

### Dependency Rules (enforced by convention, later by import linting):

1. **`core/`** imports ONLY from: `typing`, `enum`, `pydantic`, stdlib
2. **`analyzer/`** imports from: `core/`, `presidio_analyzer`, `spacy`
3. **`anonymizer/`** imports from: `core/`, `presidio_anonymizer`, `faker`
4. **`documents/`** imports from: `core/`, format libraries (`python-docx`, etc.)
5. **`cli/`** imports from: `core/`, `config/`, `click`, `rich` — and creates adapters
6. **`config/`** imports from: `pydantic_settings`, stdlib
7. **No lateral imports**: `analyzer/` never imports from `anonymizer/`, `documents/` never imports from `cli/`, etc.

### Wiring Point: The CLI (and later API/GUI) is the composition root

The CLI's `@cli.group()` callback is where everything gets wired together:

```python
# cli/main.py — the composition root
def build_engine(settings: SanctumSettings) -> SanctumEngine:
    analyzer = PresidioAnalyzerAdapter(
        # configured from settings.nlp and settings.analyzer
    )
    anonymizer = PresidioAnonymizerAdapter(
        # configured from settings.anonymizer
    )
    return SanctumEngine(
        analyzer=analyzer,
        anonymizer=anonymizer,
        score_threshold=settings.analyzer.score_threshold,
    )
```

**Why this matters for the future GUI:** When the Electron/Flask API layer arrives, it will have its OWN composition root that builds the same `SanctumEngine` with the same adapters. The engine, adapters, and core models are completely reusable. The CLI was never embedded in the domain logic.

### Testing Each Layer Independently

| Layer | Test Strategy | Dependencies |
|-------|--------------|-------------|
| `core/models.py` | Pure unit tests: construct models, validate fields, test properties | Zero — just Pydantic |
| `core/engine.py` | Unit tests with mock Analyzer + mock Anonymizer | Zero external — mocks only |
| `analyzer/adapter.py` | Integration tests that load real spaCy model | Marked `@pytest.mark.slow` |
| `anonymizer/adapter.py` | Unit tests with canned `DetectionResult` inputs | Just `presidio_anonymizer` |
| `anonymizer/operators/hips.py` | Unit tests checking Faker consistency | Just `faker` |
| `cli/main.py` | Click `CliRunner` tests | Full stack, but fast (mock engine for unit, real for integration) |
| `evaluation/*` | Marked `@pytest.mark.evaluation`, run separately | Full stack + fixtures |

---

## 5. What NOT to Build Yet (and Why)

| Component | Status | Reason to Defer |
|-----------|--------|-----------------|
| **Flask API** | Defer to Phase 1 | CLI is sufficient for development and testing. The API is just another composition root over the same engine — trivial to add once the engine is solid. |
| **Electron/Native GUI** | Defer to Phase 3+ | Massive scope. The README describes it but the core engine needs to be trustworthy first. GUI without a reliable engine is a liability. |
| **Encrypted mapping store (SQLite + AES-256)** | Defer to Phase 1 | Phase 0 HIPS operator uses an in-memory dict. Persistence and encryption add crypto complexity (key management, KDF, IV handling) that shouldn't block the engine foundation. |
| **Document format adapters (docx/xlsx/pdf)** | Defer to Phase 1 | Phase 0 processes plain text. The `DocumentReader` protocol is defined now so the interface is ready, but `python-docx` etc. are optional deps. |
| **PDF burn-in redaction** | Defer to Phase 3+ | Requires manipulating PDF structure, not just text extraction. Completely different problem domain. |
| **PyInstaller packaging** | Defer to Phase 3+ | Packaging a spaCy model + Presidio + Electron into a single installer is a multi-week project. No value until the product works. |
| **Transformer NER backend** | Defer to Phase 1 | `en_core_web_sm` is the standard tier. Transformer support is the "Professional" tier — a configuration option, not a foundation requirement. |
| **Custom legal recognizers (case numbers, Bates numbers)** | Defer to Phase 1 | The `recognizers/` directory exists and the registry pattern supports YAML config. But writing accurate regex patterns for legal identifiers requires the evaluation harness to measure quality — build the harness first. |
| **Risk scoring** | Defer to Phase 2 | Requires a model of residual identifiability that doesn't exist yet. Log detection confidence for now; score later. |
| **HIPAA mode / locale recognizers** | Defer to Phase 2+ | Presidio already has US/UK/IN recognizers. "HIPAA mode" is a policy preset, not new code. Build it when there are real users requesting it. |
| **Multi-language support** | Defer to Phase 4+ | English first. Multi-language requires separate spaCy models per language and recognizer localization. |
| **Audit log / decision trace export** | Defer to Phase 2 | `AnonymizationResult.items` already contains the trace data. Export to PDF/JSON is formatting work, not architecture. |
| **CI/CD pipeline** | Defer to Phase 0.5 | Set up GitHub Actions after the test suite exists and has something to run. A CI pipeline with no tests is ceremony. |

### Phase Sequencing Summary

```
Phase 0 (NOW):  Project scaffold + core engine + CLI + test infrastructure + fixtures
Phase 0.5:      CI pipeline + pre-commit hooks + ruff/mypy enforcement
Phase 1:        Document adapters (docx/xlsx/pdf) + Flask API + mapping store + custom recognizers
Phase 2:        HIPAA mode + risk scoring + audit trail + confidence tuning
Phase 3:        GUI (Electron wrapper around Flask API) + PyInstaller packaging
Phase 4:        Store submission + multi-language + transformer tier
```

---

## 6. Implementation Sequence for Phase 0

Build in this order. Each step is independently testable.

### Step 1: Project scaffold
- `pyproject.toml`
- `src/sanctum/__init__.py` (version string only)
- `src/sanctum/py.typed`
- All `__init__.py` files for packages
- `conftest.py` (root)
- `tests/conftest.py`
- `pip install -e ".[dev]"` and `python -m spacy download en_core_web_sm`
- Verify: `python -c "from sanctum import __version__; print(__version__)"` works

### Step 2: Core domain models
- `core/models.py` — all Pydantic models
- `core/protocols.py` — all Protocol definitions
- Tests: `tests/unit/core/test_models.py` — construction, validation, serialization
- Verify: `pytest tests/unit/core/` passes

### Step 3: Presidio analyzer adapter
- `analyzer/adapter.py`
- `analyzer/nlp_config.py`
- Tests: `tests/unit/analyzer/test_adapter.py` (with mocked NLP engine for speed)
- Tests: one `@pytest.mark.slow` test that loads real spaCy model
- Verify: adapter converts RecognizerResult to DetectionResult correctly

### Step 4: Presidio anonymizer adapter + HIPS operator
- `anonymizer/adapter.py`
- `anonymizer/operators/hips.py`
- Tests: `tests/unit/anonymizer/test_adapter.py`, `test_operators.py`
- Verify: HIPS operator produces consistent replacements, redact/hash/mask work

### Step 5: SanctumEngine orchestrator
- `core/engine.py`
- Tests: `tests/unit/core/test_engine.py` with mock analyzer + anonymizer
- Tests: `tests/integration/test_pipeline.py` with real Presidio (marked slow)
- Verify: full detect-then-anonymize pipeline works end-to-end

### Step 6: Configuration system
- `config/settings.py`
- Tests: test env var override, test defaults, test nested config
- Verify: `SANCTUM_ANALYZER__SCORE_THRESHOLD=0.8` overrides work

### Step 7: CLI skeleton
- `cli/main.py`
- Tests: `tests/integration/test_cli.py` using Click's CliRunner
- Verify: `sanctum analyze "John Smith"` produces tabular output

### Step 8: Test fixtures + evaluation harness
- `scripts/generate_fixtures.py` — Faker-based fixture generator
- Generate all 10 synthetic documents + annotations
- Manually acquire 3 public domain documents
- Hand-craft 2 edge case documents
- `tests/evaluation/scorer.py`
- `tests/evaluation/annotations.py`
- `tests/evaluation/test_precision_recall.py`
- Verify: `pytest -m evaluation` runs all 15 documents and produces a metrics report

### Step 9: Plain text document adapter
- `documents/base.py` — protocols
- `documents/text.py` — trivial implementation
- Tests: `tests/unit/documents/test_text.py`
- Wire into CLI: `sanctum anonymize -f input.txt -o output.txt`
