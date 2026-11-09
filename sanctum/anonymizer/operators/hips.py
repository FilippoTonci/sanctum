from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from typing import ClassVar

from faker import Faker
from presidio_anonymizer.operators import Operator, OperatorType
from sanctum.core.leak_check import build_matcher, normalize_whitespace

# Rerolls before giving up on a clean fake. Even a dense document leaves most
# Faker draws clean, so this is never reached in practice; if it is, the last
# draw is returned and the leak check still fails closed on it.
_MAX_ATTEMPTS = 32


@lru_cache(maxsize=16)
def _avoid_matcher(avoid: tuple[str, ...]) -> re.Pattern[str] | None:
    # Cached across instances: Presidio builds one operator per finding, and
    # every finding in a session passes the same ``avoid`` tuple.
    return build_matcher(avoid)


class HipsOperator(Operator):
    """Replaces PII with contextually plausible synthetic values using Faker.

    The fake value is a pure function of ``(salt, entity_type, original)``
    (plus ``avoid``): the same original always gets the same fake, and
    different originals almost always get different ones (two can still
    draw the same value by chance, most often dates). It can't rely on
    state kept on the instance,
    because Presidio builds a new operator for every finding and the review
    commit renders each finding on its own.

    ``salt`` (in ``params``) should be a secret, random value per document or
    review session. With a fixed salt anyone with Sanctum could fake a list
    of candidate names and match them against the output, which would undo
    the anonymization, and the same person would get the same fake in
    unrelated documents. ``PresidioAnonymizer`` fills ``salt`` and ``avoid``
    from the call's ``ReplacementContext``.

    ``avoid`` (a tuple of originals) lists every original in the document.
    A fake that contains one, or contains the finding's own text, is
    rerolled with the next attempt number in the key: "Noah Rhodes" must
    not become "Crystal Miller" when "Miller" is a real name nearby. Matching
    follows the leak check's rules (``sanctum.core.leak_check``).
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
        avoid = tuple(params.get("avoid") or ())
        # The document-wide matcher is shared by every finding; the finding's
        # own text is normally in ``avoid`` already, so only add it if not.
        matchers = [
            m
            for m in (_avoid_matcher(avoid), None if text in avoid else build_matcher((text,)))
            if m is not None
        ]
        fake = ""
        for attempt in range(_MAX_ATTEMPTS):
            fake = self._draw(salt, entity_type, text, attempt)
            clean = normalize_whitespace(fake)
            if not any(m.search(clean) for m in matchers):
                break
        return fake

    def _draw(self, salt: str, entity_type: str, text: str, attempt: int) -> str:
        key = "\x00".join((salt, entity_type, text, str(attempt))).encode("utf-8")
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
