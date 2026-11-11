"""Role nouns and defined terms are not names (#55)."""

from __future__ import annotations

import pytest
from sanctum.core.role_terms import defined_terms, is_role_term

CONTRACT = (
    'Hall PLC (the "Buyer") and Acme Corporation Ltd ("Acme") and John Smith '
    '(each a "Party"; together the "Parties"). "Confidential Information" means '
    'anything disclosed. Jane Doe (hereinafter "the Consultant").'
)


def test_defined_terms_need_an_article() -> None:
    assert defined_terms([CONTRACT]) == {"buyer", "party", "confidential information", "consultant"}


@pytest.mark.parametrize(
    ("entity_type", "text"),
    [
        ("PERSON", "parties"),
        ("PERSON", "Authorized Representative"),
        ("ORGANIZATION", "the Board"),
        ("ORGANIZATION", "First Defendant"),
        ("ORGANIZATION", "Operations team"),
        ("ORGANIZATION", "HR"),
        ("ORGANIZATION", "CONFIDENTIAL INFORMATION"),
        ("PERSON", "Employee's"),
        ("PERSON", "Employees"),
    ],
)
def test_role_nouns_are_dropped(entity_type: str, text: str) -> None:
    assert is_role_term(entity_type, text)


@pytest.mark.parametrize(
    ("entity_type", "text"),
    [
        ("ORGANIZATION", "Acme"),  # short name defined without an article
        ("ORGANIZATION", "Hall PLC"),
        ("PERSON", "John Smith"),
        ("ORGANIZATION", "Target"),  # a real retailer unless the document defines it
        ("DATE_TIME", "Effective Date"),  # only PERSON / ORGANIZATION are filtered
        ("ORGANIZATION", "  "),
    ],
)
def test_names_and_other_types_are_kept(entity_type: str, text: str) -> None:
    assert not is_role_term(entity_type, text, defined_terms([CONTRACT]))


def test_document_defined_terms_extend_the_list() -> None:
    defined = defined_terms(['Rheinwerk GmbH (the "Target")'])
    assert is_role_term("ORGANIZATION", "the Target", defined)
    assert not is_role_term("ORGANIZATION", "Rheinwerk GmbH", defined)
