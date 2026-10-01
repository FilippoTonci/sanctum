"""Recognizers Sanctum adds to Presidio's predefined set."""

from __future__ import annotations

from presidio_analyzer import Pattern, PatternRecognizer

# Presidio's EmailRecognizer validates the TLD against the public suffix list,
# so internal domains (.local, .internal, .corp) are missed. Law firms use them.
_EMAIL = (
    r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?![\w-])"
)


class AnyDomainEmailRecognizer(PatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="EMAIL_ADDRESS",
            name="AnyDomainEmailRecognizer",
            patterns=[Pattern("email, any domain", _EMAIL, 0.9)],
        )
