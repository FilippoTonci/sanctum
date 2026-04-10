from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

from tests.evaluation.scorer import EntityScorer


@pytest.fixture()
def fixture_pairs() -> list[tuple[str, dict]]:
    """Scans tests/fixtures/ for all .txt/.json pairs and returns (text, annotations) tuples."""
    fixtures_root = Path(__file__).parent.parent / "fixtures"
    pairs: list[tuple[str, dict]] = []

    for json_path in sorted(fixtures_root.rglob("*.json")):
        if json_path.name == "schema.json":
            continue
        txt_path = json_path.with_suffix(".txt")
        if not txt_path.exists():
            continue
        text = txt_path.read_text(encoding="utf-8")
        annotations = json.loads(json_path.read_text(encoding="utf-8"))
        pairs.append((text, annotations))

    return pairs


@pytest.fixture()
def scorer() -> EntityScorer:
    return EntityScorer()
