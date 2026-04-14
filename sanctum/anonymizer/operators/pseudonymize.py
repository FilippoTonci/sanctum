"""Pseudonymize operator — consistent, reversible Faker-backed replacement.

Differs from ``HipsOperator`` in two ways:

* **Consistency across calls** — the same ``(entity_type, original)``
  always maps to the same pseudonym, because the mapping lives in a
  ``MappingStore`` passed through ``params``. Two documents mentioning
  the same person get the same alias.
* **Reversible** — the store can be ``reverse()``'d to recover the
  original given the pseudonym (when unlocked).

Presidio instantiates operators with no args, so state (the store, the
Faker instance) comes in via ``params``. The operator itself is
stateless between calls.
"""

from __future__ import annotations

from typing import ClassVar

from faker import Faker
from presidio_anonymizer.operators import Operator, OperatorType
from sanctum.core.protocols import MappingStore


class PseudonymizeOperator(Operator):
    """Consistent, reversible Faker replacement backed by a ``MappingStore``."""

    _ENTITY_GENERATORS: ClassVar[dict[str, str]] = {
        "PERSON": "name",
        "LOCATION": "city",
        "PHONE_NUMBER": "phone_number",
        "EMAIL_ADDRESS": "email",
        "ORGANIZATION": "company",
        "ORG": "company",
        "DATE_TIME": "date",
    }

    def operate(self, text: str = "", params: dict | None = None) -> str:
        params = params or {}
        store = params.get("store")
        if store is None:
            raise ValueError("pseudonymize operator requires a `store` param")
        entity_type = params.get("entity_type", "PERSON")
        fake: Faker = params.get("faker") or Faker()
        generator = self._ENTITY_GENERATORS.get(entity_type)

        def factory() -> str:
            if generator:
                return str(getattr(fake, generator)())
            return str(fake.bothify("????-####"))

        result: str = store.get_or_create(text, entity_type, factory)
        return result

    def validate(self, params: dict | None = None) -> None:
        params = params or {}
        if not isinstance(params.get("store"), MappingStore):
            raise ValueError("pseudonymize operator requires a `store` MappingStore param")

    def operator_name(self) -> str:
        return "pseudonymize"

    def operator_type(self) -> OperatorType:
        return OperatorType.Anonymize
