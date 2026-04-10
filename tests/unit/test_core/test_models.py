from __future__ import annotations

import pytest
from pydantic import ValidationError

from sanctum.core.models import AnonymizationResult, DetectionResult, OperatorPolicy


class TestDetectionResult:
    def test_frozen_rejects_attribute_assignment(self) -> None:
        result = DetectionResult(
            entity_type="PERSON",
            start=0,
            end=10,
            score=0.9,
            text_span="John Smith",
        )
        with pytest.raises(ValidationError):
            result.entity_type = "EMAIL_ADDRESS"

    def test_serialization_round_trip(self) -> None:
        result = DetectionResult(
            entity_type="US_SSN",
            start=5,
            end=16,
            score=0.95,
            text_span="123-45-6789",
            context="SSN is 123-45-6789 here",
            recognizer_name="UsSsnRecognizer",
        )
        data = result.model_dump()
        restored = DetectionResult.model_validate(data)
        assert restored == result

    def test_json_round_trip(self) -> None:
        result = DetectionResult(
            entity_type="EMAIL_ADDRESS",
            start=10,
            end=30,
            score=0.88,
            text_span="user@example.com",
        )
        json_str = result.model_dump_json()
        restored = DetectionResult.model_validate_json(json_str)
        assert restored == result

    def test_default_values(self) -> None:
        result = DetectionResult(
            entity_type="PERSON",
            start=0,
            end=5,
            score=0.7,
            text_span="Alice",
        )
        assert result.context == ""
        assert result.recognizer_name == ""


class TestOperatorPolicy:
    def test_mutable_allows_assignment(self) -> None:
        policy = OperatorPolicy(operator_name="redact")
        policy.operator_name = "replace"
        assert policy.operator_name == "replace"

    def test_default_params_is_empty_dict(self) -> None:
        policy = OperatorPolicy(operator_name="redact")
        assert policy.params == {}

    def test_params_are_stored(self) -> None:
        policy = OperatorPolicy(
            operator_name="replace",
            params={"new_value": "<MASKED>"},
        )
        assert policy.params["new_value"] == "<MASKED>"


class TestAnonymizationResult:
    def test_frozen_rejects_attribute_assignment(self) -> None:
        result = AnonymizationResult(
            original_text="hello",
            anonymized_text="<PERSON>",
            detections=[],
            operators_applied={},
        )
        with pytest.raises(ValidationError):
            result.anonymized_text = "changed"

    def test_serialization_round_trip(self) -> None:
        detection = DetectionResult(
            entity_type="PERSON",
            start=0,
            end=5,
            score=0.9,
            text_span="Alice",
        )
        result = AnonymizationResult(
            original_text="Alice went home",
            anonymized_text="<PERSON> went home",
            detections=[detection],
            operators_applied={"PERSON": "replace"},
        )
        data = result.model_dump()
        restored = AnonymizationResult.model_validate(data)
        assert restored == result
