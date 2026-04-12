#!/usr/bin/env python3
"""Fetch real-world office documents for local developer testing.

Pulls a curated set of public-domain / MIT-licensed office files into
``tests/fixtures/office/public/`` so you can manually exercise adapters
against "real" documents — not the deterministic generated ones checked
into git.

**This directory is gitignored.** CI never runs this script; it only reads
the committed ``tests/fixtures/office/`` fixtures. Keep it that way — the
fetch may hit the network, and we want the build to stay air-gapped.

Usage:
    python scripts/fetch_public_office_samples.py

Sources:
    - python-docx test fixtures  (MIT)
    - openpyxl test fixtures     (MIT)
    - python-pptx test fixtures  (MIT)
    - SEC EDGAR real-company filings (U.S. public record)

The script is idempotent: files already downloaded are skipped unless
``--refresh`` is passed. Every file lands next to a ``provenance.json``
describing where it came from and under what license.

Your own drop zone:
    After running once, a README appears under
    ``tests/fixtures/office/public/user_samples/``. Drop any anonymized
    real professional documents you want to test against into that dir.
    They stay local; the path is gitignored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pathlib
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger("fetch_public_office_samples")

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "office" / "public"

HTTP_TIMEOUT = 30  # seconds


@dataclass(frozen=True)
class Sample:
    """A single file to fetch."""

    name: str                # local filename under OUT/<source>/
    url: str
    license: str
    notes: str
    source: str              # subdir under OUT (e.g. "python-docx")


# -- Curated sample list ----------------------------------------------------
#
# Keep this list short and purposeful. These are dev-only samples; we are
# not trying to build a large corpus. Add one per use case; remove any
# that go 404 rather than letting the list rot.
#
# Attribution (spelled out in provenance.json):
#   python-docx: https://github.com/python-openxml/python-docx (MIT)
#   openpyxl:    https://foss.heptapod.net/openpyxl/openpyxl (MIT)
#   python-pptx: https://github.com/scanny/python-pptx (MIT)
#   SEC EDGAR:   public record, https://www.sec.gov/edgar
#
# If any upstream URL breaks, swap it — do not silently drop the sample;
# the whole point of this script is to anchor dev testing in real files.

SAMPLES: list[Sample] = [
    # ---- docx ----
    Sample(
        name="test.docx",
        url=(
            "https://raw.githubusercontent.com/python-openxml/python-docx/"
            "master/tests/test_files/test.docx"
        ),
        license="MIT (python-docx)",
        notes="Canonical python-docx test document; paragraphs + runs + basic styles.",
        source="python-docx",
    ),
    Sample(
        name="blk-inner-content.docx",
        url=(
            "https://raw.githubusercontent.com/python-openxml/python-docx/"
            "master/tests/test_files/blk-inner-content.docx"
        ),
        license="MIT (python-docx)",
        notes="Block-level inner content: mixed paragraphs and tables.",
        source="python-docx",
    ),
    # ---- xlsx / pptx ----
    Sample(
        name="testEXCEL.xlsx",
        url=(
            "https://raw.githubusercontent.com/apache/tika/main/"
            "tika-parsers/tika-parsers-standard/tika-parsers-standard-modules/"
            "tika-parser-microsoft-module/src/test/resources/test-documents/"
            "testEXCEL.xlsx"
        ),
        license="Apache-2.0 (Apache Tika test corpus)",
        notes="Minimal xlsx with mixed types; Tika's canonical Excel test file.",
        source="apache-tika",
    ),
    Sample(
        name="testPPT.pptx",
        url=(
            "https://raw.githubusercontent.com/apache/tika/main/"
            "tika-parsers/tika-parsers-standard/tika-parsers-standard-modules/"
            "tika-parser-microsoft-module/src/test/resources/test-documents/"
            "testPPT.pptx"
        ),
        license="Apache-2.0 (Apache Tika test corpus)",
        notes="Standard PPTX with multiple slides; Tika's canonical test file.",
        source="apache-tika",
    ),
    # ---- pdf ----
    # pypdf ships a variety of small real-world PDFs under resources/. The
    # AutoCad_Simple sample is the smallest text-bearing one; form and OCR
    # samples are also good for adapter exploration.
    Sample(
        name="AutoCad_Simple.pdf",
        url=(
            "https://raw.githubusercontent.com/py-pdf/pypdf/main/"
            "resources/AutoCad_Simple.pdf"
        ),
        license="BSD-3-Clause (pypdf resources)",
        notes="Small text-bearing PDF (~3 KB); smoke test for pdfplumber extraction.",
        source="pypdf",
    ),
    Sample(
        name="FormTestFromOo.pdf",
        url=(
            "https://raw.githubusercontent.com/py-pdf/pypdf/main/"
            "resources/FormTestFromOo.pdf"
        ),
        license="BSD-3-Clause (pypdf resources)",
        notes="PDF form fields — useful for exploring AcroForm extraction later.",
        source="pypdf",
    ),
]


USER_SAMPLES_README = """\
# User sample drop zone

Drop your own anonymized professional documents into this directory to
exercise Sanctum's adapters against real-world formatting. Files here are
gitignored — they never leave your machine via this repo.

Suggested layout:

    user_samples/
      docx/
      xlsx/
      pdf/
      pptx/

Guidelines:

- **Strip real client data first.** Even on a local-only machine, don't
  store documents you wouldn't want lingering in a `git clean` mishap.
- Keep files under ~5 MB for snappy iteration.
- If a file reveals a parser bug, consider distilling it into a minimal
  reproducer under `tests/fixtures/office/` (committed, deterministic).

This directory is *not* used by CI or by the evaluation harness. It's
purely for manual adapter exploration during development.
"""


def _http_get(url: str) -> bytes:
    """Fetch a URL with a short timeout and a helpful UA string."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "sanctum-dev-fixtures/0.1 (+local)"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


def _fetch_sample(sample: Sample, refresh: bool) -> dict[str, str] | None:
    """Download a single sample; return a provenance record or None on failure."""
    dest_dir = OUT / sample.source
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / sample.name

    if dest.exists() and not refresh:
        logger.info("skip %s (already present; pass --refresh to overwrite)", dest.relative_to(ROOT))
        data = dest.read_bytes()
    else:
        try:
            logger.info("fetch %s", sample.url)
            data = _http_get(sample.url)
        except (urllib.error.URLError, TimeoutError) as exc:
            logger.warning("skipped %s: %s", sample.name, exc)
            return None
        dest.write_bytes(data)

    return {
        "name": sample.name,
        "source": sample.source,
        "url": sample.url,
        "license": sample.license,
        "notes": sample.notes,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _ensure_user_samples_dir() -> None:
    d = OUT / "user_samples"
    d.mkdir(parents=True, exist_ok=True)
    readme = d / "README.md"
    if not readme.exists():
        readme.write_text(USER_SAMPLES_README, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download files that already exist locally.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    OUT.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, str]] = []
    for sample in SAMPLES:
        rec = _fetch_sample(sample, refresh=args.refresh)
        if rec is not None:
            records.append(rec)

    provenance = OUT / "provenance.json"
    provenance.write_text(
        json.dumps({"fetched": records}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info("wrote %s (%d records)", provenance.relative_to(ROOT), len(records))

    _ensure_user_samples_dir()
    logger.info("user drop zone ready: %s", (OUT / "user_samples").relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
