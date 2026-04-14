"""Anonymization operators available to Sanctum.

This module is the canonical list of operator names that can be used as
`default_operator` in settings or as `OperatorPolicy.operator_name` per
entity type. To add a new operator, extend `BUILTIN_OPERATOR_NAMES` below
and (for non-Presidio operators) register it with `AnonymizerEngine`.

Built-in operators (name → behaviour, with example on "John Smith"):

- redact      Removes the PII span entirely.             → ""
- replace     Swaps in the entity tag.                   → "<PERSON>"
- mask        Overlays a char (configurable length/dir). → "**** *****"
- hash        Deterministic cryptographic hash.          → "a41c4f…"
- encrypt     Reversible symmetric encryption (AES).     → "J8nAq…" (decrypt w/ key)
- keep        Pass-through; leaves the span untouched.   → "John Smith"
- custom      User-supplied lambda (via OperatorConfig params).
- hips        Sanctum's Faker-based synthetic replacement
              (contextually plausible fake data).        → "Michael Jones"
- pseudonymize Consistent, reversible Faker replacement
               backed by a MappingStore. Requires
               `params={"store": MappingStore}`.          → "Michael Jones"
               (same input → same output across calls)
"""

from __future__ import annotations

from sanctum.anonymizer.operators.hips import HipsOperator
from sanctum.anonymizer.operators.pseudonymize import PseudonymizeOperator

BUILTIN_OPERATOR_NAMES: tuple[str, ...] = (
    "redact",
    "replace",
    "mask",
    "hash",
    "encrypt",
    "keep",
    "custom",
    "hips",
    "pseudonymize",
)

__all__ = ["BUILTIN_OPERATOR_NAMES", "HipsOperator", "PseudonymizeOperator"]
