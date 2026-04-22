"""Lightweight Faker-backed pseudonym generation helpers."""

from __future__ import annotations

from collections.abc import Callable

from faker import Faker

_LANGUAGE_TO_LOCALE: dict[str, str] = {
    "en": "en_US",
    "it": "it_IT",
    "fr": "fr_FR",
    "de": "de_DE",
    "es": "es_ES",
    "pt": "pt_PT",
    "nl": "nl_NL",
}

_ENTITY_GENERATORS: dict[str, str] = {
    "PERSON": "name",
    "LOCATION": "city",
    "PHONE_NUMBER": "phone_number",
    "EMAIL_ADDRESS": "email",
    "ORGANIZATION": "company",
    "ORG": "company",
    "DATE_TIME": "date",
}


def resolve_locale(language: str | None) -> str:
    if language is None:
        return "en_US"
    if "_" in language:
        return language
    return _LANGUAGE_TO_LOCALE.get(language, "en_US")


def pseudonym_factory(entity_type: str, language: str | None = None) -> Callable[[], str]:
    """Build a Faker-backed pseudonym factory for ``entity_type``."""
    fake = Faker(resolve_locale(language))
    generator = _ENTITY_GENERATORS.get(entity_type)

    def factory() -> str:
        if generator:
            return str(getattr(fake, generator)())
        return str(fake.bothify("????-####"))

    return factory
