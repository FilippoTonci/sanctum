"""Pseudonymize operator — consistent, reversible Faker-backed replacement.

Differs from ``HipsOperator`` in two ways:

* **Consistency across calls** — the same ``(entity_type, original)``
  always maps to the same pseudonym, because the mapping lives in a
  ``MappingStore`` passed through ``params``. Two documents mentioning
  the same person get the same alias.
* **Reversible** — the store can be ``reverse()``'d to recover the
  original given the pseudonym (when unlocked).

Split of state: the ``MappingStore`` is request-scoped (different CLI
invocations unlock different stores) and arrives via ``params``; a
``Faker`` cache keyed by locale is operator-lifetime and lives on the
instance, mirroring ``HipsOperator``. Locale is also passed via
``params`` so the surrogate data matches the text's language.
"""

from __future__ import annotations

from typing import ClassVar

from faker import Faker
from presidio_anonymizer.operators import Operator, OperatorType
from sanctum.core.protocols import MappingStore

_LANGUAGE_TO_LOCALE: dict[str, str] = {
    "en": "en_US",
    "it": "it_IT",
    "fr": "fr_FR",
    "de": "de_DE",
    "es": "es_ES",
    "pt": "pt_PT",
    "nl": "nl_NL",
}


def _resolve_locale(language: str | None) -> str:
    if language is None:
        return "en_US"
    if "_" in language:
        return language  # already a Faker locale like "en_GB"
    return _LANGUAGE_TO_LOCALE.get(language, "en_US")


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

    def __init__(self) -> None:
        super().__init__()
        self._fakers: dict[str, Faker] = {}

    def _faker(self, locale: str) -> Faker:
        fake = self._fakers.get(locale)
        if fake is None:
            fake = Faker(locale)
            self._fakers[locale] = fake
        return fake

    def operate(self, text: str = "", params: dict | None = None) -> str:
        params = params or {}
        store = params.get("store")
        if store is None:
            raise ValueError("pseudonymize operator requires a `store` param")
        entity_type = params.get("entity_type")
        if not entity_type:
            raise ValueError("pseudonymize operator requires an `entity_type` param")
        locale = _resolve_locale(params.get("language"))
        fake = self._faker(locale)
        generator = self._ENTITY_GENERATORS.get(entity_type)

        def factory() -> str:
            # Seed per call so preview (through a non-persisting façade)
            # and commit (through the real store) mint the *same*
            # pseudonym on first encounter — without this, Faker's
            # shared PRNG state drifts between the two code paths and
            # preview/commit divergence leaks into the final file.
            fake.seed_instance(f"{entity_type}\x00{text}")
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
