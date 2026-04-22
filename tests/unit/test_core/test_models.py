from __future__ import annotations

import pytest
from pydantic import ValidationError
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    OperatorPolicy,
    ReviewComment,
    ReviewDecision,
)


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

    def test_per_detection_replacements_defaults_to_none(self) -> None:
        """Phase 1.5 WS2 — review emission requires per-detection replacements;
        callers that don't set them get None and review mode refuses."""
        result = AnonymizationResult(
            original_text="x",
            anonymized_text="x",
            detections=[],
            operators_applied={},
        )
        assert result.per_detection_replacements is None

    def test_per_detection_replacements_round_trip(self) -> None:
        detection = DetectionResult(
            entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice"
        )
        result = AnonymizationResult(
            original_text="Alice",
            anonymized_text="<PERSON>",
            detections=[detection],
            operators_applied={"PERSON": "replace"},
            per_detection_replacements=["<PERSON>"],
        )
        restored = AnonymizationResult.model_validate(result.model_dump())
        assert restored.per_detection_replacements == ["<PERSON>"]


class TestReviewDecision:
    def _staged(self) -> ReviewComment:
        return ReviewComment(
            detection_id="abcdef012345",
            entity_type="PERSON",
            score=0.9,
            original="Alice",
            replacement="[PERSON_1]",
            operator="replace",
        )

    def test_accepted_carries_staged_trailer(self) -> None:
        decision = ReviewDecision(kind="accepted", staged=self._staged())
        assert decision.kind == "accepted"
        assert decision.staged is not None
        assert decision.user_comment_body is None

    def test_user_added_carries_body_and_anchor(self) -> None:
        decision = ReviewDecision(
            kind="user_added",
            user_comment_body="#PERSON reviewer flag",
            user_anchor_text="Priya Patel",
        )
        assert decision.staged is None
        assert decision.user_anchor_text == "Priya Patel"

    def test_frozen_rejects_attribute_assignment(self) -> None:
        decision = ReviewDecision(kind="rejected", staged=self._staged())
        with pytest.raises(ValidationError):
            decision.kind = "accepted"

    def test_rejects_invalid_kind(self) -> None:
        with pytest.raises(ValidationError):
            ReviewDecision(kind="maybe")  # type: ignore[arg-type]
