"""Role nouns and defined terms: words that name a role, not a person or firm.

GLiNER tags contract vocabulary as PERSON / ORGANIZATION ("the parties",
"Authorized Representative", "the Supplier", "the Board", "HR"). In the
sanctum-research benchmark these were most of the over-redaction, and with
realistic fake data they get invented names ("the parties" -> "the Rodriguez
Group"). Two deterministic guards, applied before name propagation so a role
word never spreads:

* a built-in list of role nouns and department names;
* the document's own defined terms: a quoted term introduced with an article,
  ``(the "Supplier")``, ``(each a "Party")``, or ``"Confidential Information"
  means``. Without an article, ``Acme Corporation Ltd ("Acme")`` defines a
  short name for a real company, so it is left alone (#55).

Only PERSON and ORGANIZATION findings are filtered. Stdlib only, so it lives in
core.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

ROLE_TYPES = frozenset({"PERSON", "ORGANIZATION"})

ROLE_NOUNS = frozenset(
    {
        "party", "parties", "disclosing party", "receiving party", "discloser", "recipient",
        "authorized representative", "authorised representative", "representative",
        "employee", "employer", "supplier", "customer", "client", "contractor",
        "subcontractor", "consultant", "buyer", "seller", "purchaser", "vendor", "lessor",
        "lessee", "landlord", "tenant", "licensor", "licensee", "borrower", "lender",
        "guarantor", "company", "board", "board of directors", "directors", "shareholders",
        "founders", "investor", "investors", "claimant", "defendant", "respondent",
        "appellant", "applicant", "expert", "agreement", "confidential information",
        "effective date", "order", "services", "deliverables", "affiliate", "affiliates",
        "subsidiary", "provider", "service provider", "processor", "controller",
        "data controller", "data processor", "data subject",
        # Departments: "HR", "the Operations team", "logistics division".
        "hr", "human resources", "engineering", "operations", "finance", "legal", "sales",
        "marketing", "procurement", "compliance", "it", "product development", "logistics",
    }
)  # fmt: skip

# Quotes (straight and typographic) and punctuation around a finding.
_STRIP = "\"'.,;:()" + "\u201c\u201d\u2018\u2019"
_POSSESSIVE = re.compile("['\u2019]s$")
_LEADING = re.compile(r"^(?:the|a|an|each|any|our|your|its|their|such)\s+")
_ORDINAL = re.compile(r"^(?:first|second|third|fourth|1st|2nd|3rd|4th)\s+")
_UNIT = re.compile(r"\s+(?:team|department|dept|division|unit|function)$")
# (the "Supplier"), (each a "Party"), (together, the "Parties"), (hereinafter "the Company")
_DEFINED_IN_PARENS = re.compile(
    r"\((?:[^()\"“”]{0,40}?\b)?(?:the|each\s+a|each\s+an|a|an)\s+"
    r"[\"“]([^\"”]{2,60})[\"”]"
    r"|\(\s*hereinafter\s+(?:referred\s+to\s+as\s+)?[\"“](?:the\s+)([^\"”]{2,60})[\"”]",
    re.IGNORECASE,
)
# "Confidential Information" means ... / shall mean / has the meaning
_DEFINED_MEANS = re.compile(
    r"[\"“]([^\"”]{2,60})[\"”]\s+(?:means|shall\s+mean|has\s+the\s+meaning)",
    re.IGNORECASE,
)


def _normalise(text: str) -> str:
    t = " ".join(text.strip().strip(_STRIP).lower().split())
    t = _POSSESSIVE.sub("", t)
    t = _LEADING.sub("", t)
    t = _ORDINAL.sub("", t)
    return _UNIT.sub("", t)


def defined_terms(texts: Iterable[str]) -> frozenset[str]:
    """Normalised role-style terms the document defines for itself."""
    found: set[str] = set()
    for text in texts:
        for m in _DEFINED_IN_PARENS.finditer(text):
            found.add(_normalise(m.group(1) or m.group(2)))
        for m in _DEFINED_MEANS.finditer(text):
            found.add(_normalise(m.group(1)))
    return frozenset(t for t in found if t)


def is_role_term(entity_type: str, text: str, defined: frozenset[str] = frozenset()) -> bool:
    """True when a PERSON / ORGANIZATION finding is a role, not a name."""
    if entity_type not in ROLE_TYPES:
        return False
    t = _normalise(text)
    if not t:
        return False
    terms = ROLE_NOUNS | defined
    return t in terms or (t.endswith("s") and t[:-1] in terms)
