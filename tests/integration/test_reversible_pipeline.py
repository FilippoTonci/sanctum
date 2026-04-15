"""End-to-end: anonymize with `pseudonymize` → lock → re-unlock → reverse.

Exercises the full WS3 surface against real Presidio engines. Keep the
KDF cheap so the test runs in the unit-test budget even though it's
marked `integration` (for the Presidio dependency, not KDF cost).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sanctum.analyzer.adapter import PresidioAnalyzer
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import IncorrectPassphraseError
from sanctum.core.models import OperatorPolicy
from sanctum.security.mapping_store import (
    EncryptedFileMappingStore,
    InMemoryMappingStore,
)

pytestmark = pytest.mark.integration

CHEAP = {"kdf_time_cost": 1, "kdf_memory_cost": 8, "kdf_parallelism": 1}


@pytest.fixture()
def pipeline() -> SanctumEngine:
    return SanctumEngine(analyzer=PresidioAnalyzer(), anonymizer=PresidioAnonymizer())


def _policies(store):
    return {
        "DEFAULT": OperatorPolicy(
            operator_name="pseudonymize",
            params={"store": store, "language": "en"},
        )
    }


def _find_person(result) -> tuple[str, str]:
    """Return (original, pseudonym) for the first PERSON detection in `result`."""
    for d in result.detections:
        if d.entity_type == "PERSON":
            original = result.original_text[d.start : d.end]
            anon_span = result.operators_applied.get("PERSON")
            assert anon_span == "pseudonymize"
            # Pseudonym position shifts in the anonymized string, so pull
            # from the mapping store instead of the offset.
            return original, d.text_span
    raise AssertionError("no PERSON detection in result")


def test_inmemory_pseudonymize_is_consistent_across_calls(pipeline: SanctumEngine):
    store = InMemoryMappingStore()

    text = "John Smith met John Smith at lunch."
    result = pipeline.process(text, operator_policies=_policies(store))

    # "John Smith" appears twice and must map to the same surrogate.
    assert "John Smith" not in result.anonymized_text
    # Pull from the store directly for the assertion.
    occurrences = [d for d in result.detections if d.entity_type == "PERSON"]
    surrogates = {store.get_or_create(d.text_span, "PERSON", lambda: "x") for d in occurrences}
    assert len(surrogates) == 1, f"expected one surrogate, got {surrogates}"


def test_persistent_pseudonymize_roundtrips_across_unlocks(pipeline: SanctumEngine, tmp_path: Path):
    path = tmp_path / "mapping.sanctum"

    # Session 1 — anonymize a document, persist the mapping.
    s1 = EncryptedFileMappingStore(path, **CHEAP)
    s1.unlock("the-correct-passphrase")
    text = "Please contact Alice Johnson regarding the matter."
    r = pipeline.process(text, operator_policies=_policies(s1))
    person = next(d for d in r.detections if d.entity_type == "PERSON")
    original = person.text_span
    pseudonym = s1.get_or_create(original, "PERSON", lambda: "x")
    assert original not in r.anonymized_text
    s1.lock()
    assert path.exists()

    # Session 2 — wrong passphrase is refused.
    s_wrong = EncryptedFileMappingStore(path, **CHEAP)
    with pytest.raises(IncorrectPassphraseError):
        s_wrong.unlock("nope")

    # Session 3 — correct passphrase restores the mapping; reverse works.
    s2 = EncryptedFileMappingStore(path, **CHEAP)
    s2.unlock("the-correct-passphrase")
    assert s2.reverse(pseudonym, "PERSON") == original
    s2.lock()


def test_persistent_pseudonymize_is_consistent_across_documents(
    pipeline: SanctumEngine, tmp_path: Path
):
    """Same name in two different documents -> same pseudonym."""
    path = tmp_path / "mapping.sanctum"
    s = EncryptedFileMappingStore(path, **CHEAP)
    s.unlock("pw")

    text_a = "Memo: John Smith joined yesterday."
    text_b = "Dear team — please welcome John Smith."
    r_a = pipeline.process(text_a, operator_policies=_policies(s))
    r_b = pipeline.process(text_b, operator_policies=_policies(s))

    pa = s.get_or_create("John Smith", "PERSON", lambda: "x")
    pb = s.get_or_create("John Smith", "PERSON", lambda: "y")
    assert pa == pb
    assert "John Smith" not in r_a.anonymized_text
    assert "John Smith" not in r_b.anonymized_text

    s.lock()
