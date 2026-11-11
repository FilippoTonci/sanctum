from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import ClassVar

from faker import Faker
from presidio_anonymizer.operators import Operator, OperatorType
from sanctum.core.leak_check import build_matcher, normalize_whitespace

# Rerolls before giving up on a clean fake. Even a dense document leaves most
# Faker draws clean, so this is never reached in practice; if it is, the last
# draw is returned and the leak check still fails closed on it.
_MAX_ATTEMPTS = 32


# Formats a date is recognised in and written back in. Month-name formats come
# first; for 03/04/2025 day-first wins, which only matters for validity (the
# output keeps the input's layout either way).
_DATE_FORMATS = (
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%d %B, %Y",
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d-%m-%Y", "%B %Y", "%b %Y",
)  # fmt: skip
# Number-like types whose fake keeps the original's shape (A1B-22 -> Q7F-90).
_SHAPED_TYPES = frozenset(
    {
        "ID_NUMBER", "US_SSN", "US_ITIN", "US_PASSPORT", "US_DRIVER_LICENSE",
        "US_BANK_NUMBER", "CREDIT_CARD", "IBAN_CODE", "UK_NHS", "MEDICAL_LICENSE",
    }
)  # fmt: skip


def _date_offset(salt: str) -> timedelta:
    """One shift for every date sharing ``salt``: 2-5 years, either way."""
    h = int.from_bytes(hashlib.sha256(("date\x00" + salt).encode()).digest()[:8], "big")
    days = 730 + h % 1096
    return timedelta(days=days if h >> 63 else -days)


def _shift_date(text: str, salt: str, attempt: int) -> str | None:
    """``text`` moved by the document's offset, in its own format; else None.

    Every date in a document moves by the same amount, so their order and the
    intervals between them (term start -> end) survive (#58).
    """
    stripped = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed: date = datetime.strptime(stripped, fmt).date()
        except ValueError:
            continue
        shifted = parsed + _date_offset(salt) + timedelta(days=attempt)
        out = shifted.strftime(fmt)
        if "%B" in fmt or "%b" in fmt:
            # Keep "May 3, 2026" unpadded if the original was.
            day = re.search(r"\b(\d{1,2})\b", stripped)
            if day and not day.group(1).startswith("0"):
                out = re.sub(r"\b0(\d)\b", r"\1", out, count=1)
        return out
    return None


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
        if entity_type == "DATE_TIME":
            shifted = _shift_date(text, salt, attempt)
            if shifted is not None:
                return shifted
        if entity_type in _SHAPED_TYPES:
            return self._reshape(text)
        generator = self._ENTITY_GENERATORS.get(entity_type)
        if generator:
            return str(getattr(self._fake, generator)())
        return str(self._fake.bothify("????-####"))

    def _reshape(self, text: str) -> str:
        """Same layout, new characters: digits for digits, letters for letters."""
        rnd = self._fake.random
        out = []
        for c in text:
            if c.isdigit():
                out.append(str(rnd.randrange(10)))
            elif c.isalpha() and c.isascii():
                letter = chr(ord("a") + rnd.randrange(26))
                out.append(letter.upper() if c.isupper() else letter)
            else:
                out.append(c)
        return "".join(out)

    def validate(self, params: dict | None = None) -> None:
        pass

    def operator_name(self) -> str:
        return "hips"

    def operator_type(self) -> OperatorType:
        return OperatorType.Anonymize
