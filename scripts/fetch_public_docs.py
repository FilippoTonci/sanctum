#!/usr/bin/env python3
"""Fetch public-domain documents for manual PII annotation.

Usage:
    python scripts/fetch_public_docs.py

Downloads excerpts from publicly available legal and financial filings and
saves them under tests/fixtures/ with stub annotation files.  The annotation
JSON files contain provenance metadata but require manual entity review.
"""

from __future__ import annotations

import json
import logging
import pathlib
import textwrap
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def _write_stub_annotation(txt_path: pathlib.Path, provenance: str = "public_domain") -> None:
    """Write a stub .json annotation that flags manual review is needed."""
    json_path = txt_path.with_suffix(".json")
    annotation = {
        "document": txt_path.name,
        "provenance": provenance,
        "generator": "scripts/fetch_public_docs.py",
        "seed": None,
        "entities": [],
        "expected_counts": {},
        "_note": "Entities must be annotated manually. Run the document through "
        "Presidio and then hand-verify the results to populate this file.",
    }
    json_path.write_text(
        json.dumps(annotation, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote stub annotation %s", json_path.relative_to(ROOT))


# ---------------------------------------------------------------------------
# 1. SEC 10-K excerpt (Apple Inc. 2023 10-K, publicly available on EDGAR)
# ---------------------------------------------------------------------------

SEC_10K_URL = (
    "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106" "/aapl-20230930.htm"
)


def fetch_sec_10k() -> None:
    """Fetch a ~500-word excerpt from a public SEC 10-K filing."""
    dest = FIXTURES / "financial" / "sec_10k_excerpt.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Fetching SEC 10-K filing from EDGAR...")
    try:
        req = urllib.request.Request(
            SEC_10K_URL,
            headers={"User-Agent": "SanctumFixtures/0.1 (research)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        # Extract a plain-text excerpt.  The HTML is large; take a middle
        # slice, strip tags naively, and truncate to ~500 words.
        import re

        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        words = text.split()

        # Find a chunk starting around the 2000th word (past boilerplate)
        start = min(2000, max(0, len(words) - 600))
        excerpt = " ".join(words[start : start + 500])

        dest.write_text(
            f"SEC 10-K FILING EXCERPT\n"
            f"Source: {SEC_10K_URL}\n"
            f"{'=' * 70}\n\n"
            f"{textwrap.fill(excerpt, width=80)}\n",
            encoding="utf-8",
        )
        logger.info("Wrote %s", dest.relative_to(ROOT))
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("Could not fetch SEC filing: %s", exc)
        dest.write_text(
            f"[PLACEHOLDER] SEC 10-K excerpt could not be fetched.\n"
            f"Source URL: {SEC_10K_URL}\n"
            f"Manually download and paste a ~500-word excerpt here.\n",
            encoding="utf-8",
        )
        logger.info("Wrote placeholder %s", dest.relative_to(ROOT))

    _write_stub_annotation(dest)


# ---------------------------------------------------------------------------
# 2. Court filing from CourtListener
# ---------------------------------------------------------------------------

# CourtListener publishes court opinions under permissive terms.
# We use the search API to find a recent opinion.
COURTLISTENER_API = "https://www.courtlistener.com/api/rest/v4/search/"


def fetch_court_filing() -> None:
    """Fetch a ~500-word excerpt from a public court opinion."""
    dest = FIXTURES / "legal" / "court_filing.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Fetching court opinion from CourtListener...")
    try:
        params = "?q=contract+breach&type=o&order_by=dateFiled+desc&page_size=1"
        req = urllib.request.Request(
            COURTLISTENER_API + params,
            headers={
                "User-Agent": "SanctumFixtures/0.1 (research)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = data.get("results", [])
        if not results:
            raise ValueError("No results returned from CourtListener API")

        opinion = results[0]
        import re

        # The search result snippet or full text
        text_html = opinion.get("text", "") or opinion.get("snippet", "")
        text = re.sub(r"<[^>]+>", " ", text_html)
        text = re.sub(r"\s+", " ", text).strip()

        words = text.split()
        excerpt = " ".join(words[:500]) if len(words) > 500 else text

        case_name = opinion.get("caseName", "Unknown Case")
        court = opinion.get("court", "Unknown Court")
        date_filed = opinion.get("dateFiled", "Unknown Date")

        dest.write_text(
            f"COURT OPINION EXCERPT\n"
            f"Case: {case_name}\n"
            f"Court: {court}\n"
            f"Date Filed: {date_filed}\n"
            f"Source: CourtListener (courtlistener.com)\n"
            f"{'=' * 70}\n\n"
            f"{textwrap.fill(excerpt, width=80)}\n",
            encoding="utf-8",
        )
        logger.info("Wrote %s", dest.relative_to(ROOT))
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        logger.warning("Could not fetch court filing: %s", exc)
        dest.write_text(
            f"[PLACEHOLDER] Court opinion could not be fetched.\n"
            f"API: {COURTLISTENER_API}\n"
            f"Manually search CourtListener for a breach-of-contract opinion\n"
            f"and paste a ~500-word excerpt here.\n",
            encoding="utf-8",
        )
        logger.info("Wrote placeholder %s", dest.relative_to(ROOT))

    _write_stub_annotation(dest)


# ---------------------------------------------------------------------------
# 3. CUAD contract excerpt (placeholder — dataset requires download)
# ---------------------------------------------------------------------------

CUAD_URL = "https://www.atticusprojectai.org/cuad"
CUAD_HF_URL = "https://huggingface.co/datasets/cuad"


def create_cuad_placeholder() -> None:
    """Create a placeholder for CUAD contract excerpts.

    The CUAD (Contract Understanding Atticus Dataset) contains 510 commercial
    contracts with expert annotations.  It must be downloaded from the Atticus
    Project or Hugging Face; automated fetching is not practical for this
    fixture script.
    """
    dest = FIXTURES / "legal" / "cuad_contract_excerpt.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)

    dest.write_text(
        f"[PLACEHOLDER] CUAD Contract Excerpt\n\n"
        f"The CUAD dataset must be downloaded manually:\n"
        f"  - Atticus Project: {CUAD_URL}\n"
        f"  - Hugging Face:    {CUAD_HF_URL}\n\n"
        f"Instructions:\n"
        f"1. Download the dataset from one of the above sources.\n"
        f"2. Select a contract and extract a ~500-word excerpt.\n"
        f"3. Replace this placeholder with the excerpt text.\n"
        f"4. Annotate the corresponding .json file with entity spans.\n",
        encoding="utf-8",
    )
    logger.info("Wrote placeholder %s", dest.relative_to(ROOT))
    _write_stub_annotation(dest)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger.info("Fetching public-domain documents for fixture corpus...")

    fetch_sec_10k()
    fetch_court_filing()
    create_cuad_placeholder()

    logger.info("Done. Review stub .json annotations and add entity spans manually.")


if __name__ == "__main__":
    main()
