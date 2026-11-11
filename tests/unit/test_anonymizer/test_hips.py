"""HIPS: one fake value per original, different fakes for different originals.

Presidio builds a fresh operator instance for every finding, and the review
commit renders each finding on its own, so any state kept on the instance is
lost between findings. The value must follow from ``(salt, entity_type,
original)`` alone.
"""

from __future__ import annotations

import pytest
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.anonymizer.operators.hips import HipsOperator
from sanctum.core.models import DetectionResult, OperatorPolicy, ReplacementContext


def _fake(
    text: str,
    entity_type: str = "PERSON",
    salt: str | None = "s1",
    avoid: tuple[str, ...] = (),
) -> str:
    params: dict[str, object] = {"entity_type": entity_type, "avoid": avoid}
    if salt is not None:
        params["salt"] = salt
    return HipsOperator().operate(text, params)


class TestHipsOperator:
    def test_same_original_same_fake_across_instances(self) -> None:
        assert _fake("John Smith") == _fake("John Smith")

    def test_distinct_people_get_distinct_fakes(self) -> None:
        people = ["John Smith", "Jane Doe", "Mario Rossi", "Ana Lopez", "Wei Chen"]
        fakes = {_fake(p) for p in people}
        assert len(fakes) == len(people)

    @pytest.mark.parametrize(
        ("entity_type", "originals"),
        [
            ("DATE_TIME", ["12 March 2024", "1 April 2023", "5 May 2020"]),
            ("IBAN_CODE", ["GB82WEST12345698765432", "DE89370400440532013000"]),
            ("USER_ADDED", ["Project Falcon", "Clause 7.2"]),
        ],
    )
    def test_distinct_originals_distinct_fakes_for_other_types(
        self, entity_type: str, originals: list[str]
    ) -> None:
        fakes = {_fake(o, entity_type) for o in originals}
        assert len(fakes) == len(originals)

    def test_salt_changes_the_fake(self) -> None:
        # Without a secret salt anyone with Sanctum could hash candidate names
        # and match them against the fake, so the salt must matter.
        assert _fake("John Smith", salt="a") != _fake("John Smith", salt="b")

    def test_entity_type_is_part_of_the_key(self) -> None:
        assert _fake("Paris", "LOCATION") != _fake("Paris", "PERSON")

    def test_never_returns_the_original(self) -> None:
        assert _fake("John Smith") != "John Smith"

    def test_fake_never_contains_another_original(self) -> None:
        # A fake for Noah Rhodes that reads "Crystal Miller" puts a real
        # surname from the same document back in, and fails the leak check.
        first = _fake("Noah Rhodes")
        surname = first.split()[-1]
        rerolled = _fake("Noah Rhodes", avoid=("Noah Rhodes", surname))
        assert surname not in rerolled.split()
        assert rerolled == _fake("Noah Rhodes", avoid=("Noah Rhodes", surname))

    def test_avoid_does_not_change_fakes_that_are_already_clean(self) -> None:
        assert _fake("Noah Rhodes", avoid=("Zzyzx",)) == _fake("Noah Rhodes")

    def test_fakes_avoid_every_original_across_many_salts(self) -> None:
        originals = (
            "Rachel Moore",
            "Noah Rhodes",
            "Henry Santiago",
            "Miller",
            "Hall PLC",
            "Miller, Henderson and Johnson",
            "Henderson and Johnson",
            "New York",
        )
        from sanctum.core.leak_check import build_matcher

        matcher = build_matcher(originals)
        assert matcher is not None
        for i in range(300):
            for original in originals:
                fake = _fake(original, "PERSON", salt=f"s{i}", avoid=originals)
                assert not matcher.search(fake), (original, fake)

    def test_missing_salt_still_works(self) -> None:
        assert _fake("John Smith", salt=None) not in ("", "John Smith")

    @pytest.mark.parametrize(
        ("original", "pattern"),
        [
            ("December 22, 2025", r"^[A-Z][a-z]+ \d{1,2}, \d{4}$"),
            ("3 March 2024", r"^\d{1,2} [A-Z][a-z]+ \d{4}$"),
            ("2025-12-22", r"^\d{4}-\d{2}-\d{2}$"),
            ("22/12/2025", r"^\d{2}/\d{2}/\d{4}$"),
        ],
    )
    def test_dates_keep_their_format(self, original: str, pattern: str) -> None:
        import re

        fake = _fake(original, "DATE_TIME")
        assert fake != original
        assert re.match(pattern, fake), fake

    def test_dates_in_a_document_keep_their_order_and_gaps(self) -> None:
        """Effective date -> term end must not flip (#58)."""
        from datetime import datetime

        fmt = "%B %d, %Y"
        start, end = "December 22, 2025", "May 13, 2027"
        for salt in ("a", "b", "c", "d"):
            fs = datetime.strptime(_fake(start, "DATE_TIME", salt=salt), fmt)
            fe = datetime.strptime(_fake(end, "DATE_TIME", salt=salt), fmt)
            assert fe - fs == datetime.strptime(end, fmt) - datetime.strptime(start, fmt)

    def test_unparseable_dates_still_get_a_fake(self) -> None:
        assert _fake("next Monday", "DATE_TIME") not in ("", "next Monday")

    @pytest.mark.parametrize("original", ["801-922-6916", "GB82WEST12345698765432", "HRB 88213"])
    def test_numbers_keep_their_shape(self, original: str) -> None:
        import re

        def shape(s: str) -> str:
            return re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", s)))

        fake = _fake(original, "ID_NUMBER")
        assert fake != original
        assert shape(fake) == shape(original)


@pytest.fixture(scope="module")
def anonymizer() -> PresidioAnonymizer:
    return PresidioAnonymizer(default_operator="hips")


class TestAdapterSaltsHips:
    @staticmethod
    def _detections(text: str, names: list[str]) -> list[DetectionResult]:
        out = []
        for name in names:
            start = text.index(name)
            out.append(
                DetectionResult(
                    entity_type="PERSON",
                    start=start,
                    end=start + len(name),
                    score=0.9,
                    text_span=name,
                )
            )
        return out

    def test_one_call_keeps_repeats_consistent_and_people_distinct(
        self, anonymizer: PresidioAnonymizer
    ) -> None:
        text = "John Smith met Jane Doe. John Smith signed."
        names = ["John Smith", "Jane Doe"]
        detections = self._detections(text, names)
        second = text.rindex("John Smith")
        detections.append(
            DetectionResult(
                entity_type="PERSON", start=second, end=second + 10, score=0.9, text_span=""
            )
        )
        # A fixed salt lets the test predict each fake, so it never has to
        # parse them back out of the text (fakes like "Mr. Ronald Ortiz"
        # contain the punctuation a split would rely on).
        avoid = ("John Smith", "Jane Doe")
        ctx = ReplacementContext(salt="doc", avoid=avoid)
        result = anonymizer.anonymize(text, detections, context=ctx).anonymized_text
        fake_a = _fake("John Smith", salt="doc", avoid=avoid)
        fake_b = _fake("Jane Doe", salt="doc", avoid=avoid)
        assert fake_a != fake_b
        assert result == f"{fake_a} met {fake_b}. {fake_a} signed."

    def test_unsalted_calls_do_not_share_a_seed(self, anonymizer: PresidioAnonymizer) -> None:
        # Each call without an explicit salt draws a fresh one, so the same
        # name in two unrelated documents doesn't link them.
        text = "John Smith"
        detections = self._detections(text, [text])
        outs = {anonymizer.anonymize(text, detections).anonymized_text for _ in range(5)}
        assert len(outs) > 1

    def test_explicit_salt_is_respected(self, anonymizer: PresidioAnonymizer) -> None:
        text = "John Smith"
        detections = self._detections(text, [text])
        policies = {"DEFAULT": OperatorPolicy(operator_name="hips", params={"salt": "fixed"})}
        a = anonymizer.anonymize(text, detections, policies).anonymized_text
        b = anonymizer.anonymize(text, detections, policies).anonymized_text
        assert a == b

    def test_context_salt_and_avoid_reach_hips(self, anonymizer: PresidioAnonymizer) -> None:
        text = "John Smith"
        detections = self._detections(text, [text])
        ctx = ReplacementContext(salt="doc", avoid=(text,))
        a = anonymizer.anonymize(text, detections, context=ctx).anonymized_text
        b = anonymizer.anonymize(text, detections, context=ctx).anonymized_text
        assert a == b == _fake(text, salt="doc", avoid=(text,))

    def test_context_does_not_touch_other_operators(self, anonymizer: PresidioAnonymizer) -> None:
        text = "John Smith"
        detections = self._detections(text, [text])
        policies = {"DEFAULT": OperatorPolicy(operator_name="replace")}
        ctx = ReplacementContext(salt="doc", avoid=(text,))
        assert anonymizer.anonymize(text, detections, policies, ctx).anonymized_text == "<PERSON>"
