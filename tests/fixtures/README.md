# Test Fixtures

Ground-truth corpus for evaluating Sanctum's PII detection and anonymization.

## Directory Structure

```
tests/fixtures/
  legal/              NDA, engagement letter, court filing, CUAD excerpt
  financial/          Bank statement, invoice, SEC 10-K excerpt
  medical/            Discharge summary, referral letter
  hr/                 Resume, offer letter
  correspondence/     Attorney-client email, internal memo
  edge_cases/         Max-density PII, zero-PII technical text
  schema.json         JSON Schema for annotation files
```

## Annotation Format

Every `.txt` document has a companion `.json` file containing exact character
offsets for each PII entity.  See `schema.json` for the full specification.

## Regenerating Synthetic Fixtures

Synthetic documents are generated with Faker (seed=42) for reproducibility:

```bash
python scripts/generate_fixtures.py
```

This overwrites the 10 synthetic `.txt` and `.json` files.  Deterministic
seeding ensures identical output across runs.

## Fetching Public-Domain Documents

Three documents are sourced from public repositories (SEC EDGAR,
CourtListener, CUAD).  Fetch or refresh them with:

```bash
python scripts/fetch_public_docs.py
```

Public-domain documents require **manual annotation review** -- the script
creates stub `.json` files with empty entity lists that must be populated by
hand after inspecting the fetched text.

## Notes

- `.json` files are the ground truth used by evaluation tests.
- Character offsets (`start`/`end`) are zero-indexed, half-open intervals
  matching Python slice semantics: `text[start:end] == entity["text"]`.
- Synthetic fixtures should not be edited by hand; modify
  `scripts/generate_fixtures.py` instead and re-run.
