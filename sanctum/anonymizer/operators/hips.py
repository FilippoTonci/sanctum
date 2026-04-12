from __future__ import annotations

from typing import ClassVar

from faker import Faker
from presidio_anonymizer.operators import Operator, OperatorType


class HipsOperator(Operator):
    """Replaces PII with contextually plausible synthetic values using Faker."""

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
        self._fake = Faker()
        self._fake.seed_instance(42)
        self._mapping: dict[str, str] = {}

    def operate(self, text: str = "", params: dict | None = None) -> str:
        if text in self._mapping:
            return self._mapping[text]

        params = params or {}
        entity_type = params.get("entity_type", "PERSON")
        generator = self._ENTITY_GENERATORS.get(entity_type)

        if generator:
            replacement = str(getattr(self._fake, generator)())
        else:
            replacement = str(self._fake.bothify("????-####"))

        self._mapping[text] = replacement
        return replacement

    def validate(self, params: dict | None = None) -> None:
        pass

    def operator_name(self) -> str:
        return "hips"

    def operator_type(self) -> OperatorType:
        return OperatorType.Anonymize
