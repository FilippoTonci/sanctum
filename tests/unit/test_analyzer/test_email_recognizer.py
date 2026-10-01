import pytest
from sanctum.analyzer.recognizers import AnyDomainEmailRecognizer


@pytest.mark.parametrize(
    "text, expected",
    [
        ("mail a@firm.local now", "a@firm.local"),
        ("margaret.holloway@hollowayfinch.example", "margaret.holloway@hollowayfinch.example"),
        ("j.albrecht@northgate.internal.", "j.albrecht@northgate.internal"),
        ("m.holloway@hollowayfinch.co.uk", "m.holloway@hollowayfinch.co.uk"),
    ],
)
def test_matches_any_well_formed_domain(text: str, expected: str) -> None:
    [r] = AnyDomainEmailRecognizer().analyze(text, ["EMAIL_ADDRESS"])
    assert text[r.start : r.end] == expected


@pytest.mark.parametrize("text", ["not@an", "@firm.local", "a@.local", "a@b"])
def test_rejects_malformed(text: str) -> None:
    assert AnyDomainEmailRecognizer().analyze(text, ["EMAIL_ADDRESS"]) == []
