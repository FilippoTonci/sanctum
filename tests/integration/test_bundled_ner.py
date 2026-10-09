"""The production engine with the real bundled GLiNER-PII model.

Needs the model on disk (``python scripts/fetch_ner_model.py``; CI caches it).
Skipped, with the reason, when it is not there.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
from docx import Document
from sanctum.analyzer.ner_model import checksum_mismatches, default_model_dir
from sanctum.cli.commands import _create_engine
from sanctum.config.settings import settings
from sanctum.core.engine import SanctumEngine
from sanctum.core.models import OperatorPolicy
from sanctum.documents.registry import adapter_for

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (settings.nlp.ner_model_dir or default_model_dir()).is_dir(),
        reason="NER model not installed; run `python scripts/fetch_ner_model.py`",
    ),
]

FIXTURES = Path(__file__).parent.parent / "fixtures" / "office"
TEXT = (
    "Dwayne Kowalczyk (passport 553219876) met Verdana Pharmaceuticals at "
    "14 Rue de Rivoli, Paris on 3 May 2025. Later Dwayne emailed d.k@verdana.local."
)


@pytest.fixture(scope="module")
def engine() -> SanctumEngine:
    return _create_engine()


def test_installed_model_matches_the_pins() -> None:
    assert checksum_mismatches(settings.nlp.ner_model_dir or default_model_dir()) == []


def test_finds_names_ids_orgs_places_and_repeats(engine: SanctumEngine) -> None:
    found = {(d.entity_type, d.text_span) for d in engine.analyze(TEXT)}
    assert {
        ("PERSON", "Dwayne Kowalczyk"),
        ("ID_NUMBER", "553219876"),
        ("ORGANIZATION", "Verdana Pharmaceuticals"),
        ("DATE_TIME", "3 May 2025"),
        ("PERSON", "Dwayne"),  # the repeat, by name propagation
        ("EMAIL_ADDRESS", "d.k@verdana.local"),
    } <= found
    assert any(t == "LOCATION" and "Rue de Rivoli" in s for t, s in found)


def test_detections_are_disjoint_and_within_text(engine: SanctumEngine) -> None:
    dets = engine.analyze(TEXT)
    for a, b in pairwise(dets):
        assert a.end <= b.start
    assert all(TEXT[d.start : d.end] == d.text_span for d in dets)


def test_nda_docx_round_trip_passes_the_leak_check(engine: SanctumEngine, tmp_path: Path) -> None:
    src = FIXTURES / "nda_contract.docx"
    dst = tmp_path / "out.docx"
    reader, writer = adapter_for(src)
    results = engine.process_document(
        reader,
        writer,
        src,
        dst,
        operator_policies={"DEFAULT": OperatorPolicy(operator_name="replace")},
    )
    assert results
    text = "\n".join(p.text for p in Document(str(dst)).paragraphs)
    assert "Miller" not in text
    assert "<PERSON>" in text
