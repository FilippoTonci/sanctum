from __future__ import annotations

import pytest
from sanctum.anonymizer.operators.pseudonymize import PseudonymizeOperator
from sanctum.security.mapping_store import InMemoryMappingStore


def _unlocked_store() -> InMemoryMappingStore:
    return InMemoryMappingStore()


def test_same_original_produces_same_pseudonym():
    op = PseudonymizeOperator()
    store = _unlocked_store()
    params = {"store": store, "entity_type": "PERSON"}

    first = op.operate("Alice Smith", params)
    second = op.operate("Alice Smith", params)
    assert first == second


def test_different_originals_get_different_pseudonyms():
    op = PseudonymizeOperator()
    params = {"store": _unlocked_store(), "entity_type": "PERSON"}
    assert op.operate("Alice", params) != op.operate("Bob", params)


def test_entity_type_scopes_store():
    op = PseudonymizeOperator()
    store = _unlocked_store()
    p1 = op.operate("1234", {"store": store, "entity_type": "PERSON"})
    p2 = op.operate("1234", {"store": store, "entity_type": "PHONE_NUMBER"})
    assert store.reverse(p1, "PERSON") == "1234"
    assert store.reverse(p1, "PHONE_NUMBER") is None
    assert store.reverse(p2, "PHONE_NUMBER") == "1234"


def test_missing_store_raises():
    op = PseudonymizeOperator()
    with pytest.raises(ValueError):
        op.operate("Alice", {"entity_type": "PERSON"})


def test_missing_entity_type_raises():
    op = PseudonymizeOperator()
    with pytest.raises(ValueError):
        op.operate("Alice", {"store": _unlocked_store()})


def test_locale_is_taken_from_language_param():
    """Italian input should produce italian-locale surrogates."""
    op = PseudonymizeOperator()
    store = _unlocked_store()
    op.operate("Mario Rossi", {"store": store, "entity_type": "PERSON", "language": "it"})
    op.operate("Jean Dupont", {"store": store, "entity_type": "PERSON", "language": "fr"})
    # Fakers for both locales are cached on the operator instance.
    assert "it_IT" in op._fakers
    assert "fr_FR" in op._fakers


def test_validate_accepts_store_and_rejects_missing():
    op = PseudonymizeOperator()
    op.validate({"store": _unlocked_store()})
    with pytest.raises(ValueError):
        op.validate({})
    with pytest.raises(ValueError):
        op.validate({"store": "not a store"})


def test_operator_metadata():
    op = PseudonymizeOperator()
    assert op.operator_name() == "pseudonymize"
    # OperatorType.Anonymize is the right category; just confirm it isn't Deanonymize.
    assert "Anon" in str(op.operator_type())
