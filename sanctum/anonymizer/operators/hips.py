from __future__ import annotations

import hashlib
from typing import ClassVar

from faker import Faker
from presidio_anonymizer.operators import Operator, OperatorType


class HipsOperator(Operator):
    """Replaces PII with contextually plausible synthetic values using Faker.

    The fake value is a pure function of ``(salt, entity_type, original)``:
    the same original always gets the same fake, and different originals
    get different ones. It can't rely on state kept on the instance,
    because Presidio builds a new operator for every finding and the review
    commit renders each finding on its own.

    ``salt`` (in ``params``) should be a secret, random value per document or
    review session. With a fixed salt anyone with Sanctum could fake a list
    of candidate names and match them against the output, which would undo
    the anonymization, and the same person would get the same fake in
    unrelated documents. ``PresidioAnonymizer`` adds a fresh salt per call
    when the caller doesn't set one; review sessions carry their own, so
    previews and the commit agree.
    """

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

    def operate(self, text: str = "", params: dict | None = None) -> str:
        params = params or {}
        entity_type = params.get("entity_type", "PERSON")
        salt = str(params.get("salt", ""))
        key = "\x00".join((salt, entity_type, text)).encode("utf-8")
        self._fake.seed_instance(int.from_bytes(hashlib.sha256(key).digest()[:16], "big"))

        generator = self._ENTITY_GENERATORS.get(entity_type)
        if generator:
            return str(getattr(self._fake, generator)())
        return str(self._fake.bothify("????-####"))

    def validate(self, params: dict | None = None) -> None:
        pass

    def operator_name(self) -> str:
        return "hips"

    def operator_type(self) -> OperatorType:
        return OperatorType.Anonymize
