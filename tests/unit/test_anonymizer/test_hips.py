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
from sanctum.core.models import DetectionResult, OperatorPolicy


def _fake(text: str, entity_type: str = "PERSON", salt: str | None = "s1") -> str:
    params: dict[str, object] = {"entity_type": entity_type}
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

    def test_missing_salt_still_works(self) -> None:
        assert _fake("John Smith", salt=None) not in ("", "John Smith")


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
        result = anonymizer.anonymize(text, detections).anonymized_text
        fake_a, rest = result.split(" met ")
        fake_b, tail = rest.split(". ")
        assert fake_a != fake_b
        assert tail == f"{fake_a} signed."

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
