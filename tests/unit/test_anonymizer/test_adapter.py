from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sanctum.core.models import DetectionResult, OperatorPolicy


class TestPresidioAnonymizer:
    @pytest.fixture()
    def mock_presidio_engine(self) -> MagicMock:
        engine = MagicMock()
        result = MagicMock()
        result.text = "redacted output"
        engine.anonymize.return_value = result
        return engine

    @pytest.fixture()
    def adapter(self, mock_presidio_engine: MagicMock) -> object:
        with patch(
            "sanctum.anonymizer.adapter.AnonymizerEngine", return_value=mock_presidio_engine
        ):
            from sanctum.anonymizer.adapter import PresidioAnonymizer

            anonymizer = PresidioAnonymizer(default_operator="redact")
        anonymizer._engine = mock_presidio_engine
        return anonymizer

    @pytest.fixture()
    def sample_detections(self) -> list[DetectionResult]:
        return [
            DetectionResult(
                entity_type="PERSON", start=0, end=10, score=0.9, text_span="John Smith"
            ),
            DetectionResult(
                entity_type="EMAIL_ADDRESS",
                start=20,
                end=40,
                score=0.85,
                text_span="john@example.com",
            ),
        ]

    def test_converts_detections_to_recognizer_results(
        self, adapter: object, mock_presidio_engine: MagicMock, sample_detections: list
    ) -> None:
        adapter.anonymize("John Smith says john@example.com", detections=sample_detections)

        call_kwargs = mock_presidio_engine.anonymize.call_args[1]
        analyzer_results = call_kwargs["analyzer_results"]

        assert len(analyzer_results) == 2
        assert analyzer_results[0].entity_type == "PERSON"
        assert analyzer_results[0].start == 0
        assert analyzer_results[0].end == 10
        assert analyzer_results[0].score == 0.9
        assert analyzer_results[1].entity_type == "EMAIL_ADDRESS"

    def test_applies_default_operator(
        self, adapter: object, mock_presidio_engine: MagicMock, sample_detections: list
    ) -> None:
        adapter.anonymize("John Smith says john@example.com", detections=sample_detections)

        call_kwargs = mock_presidio_engine.anonymize.call_args[1]
        operators = call_kwargs["operators"]

        assert "DEFAULT" in operators
        assert operators["DEFAULT"].operator_name == "redact"

    def test_applies_custom_operator_policies(
        self, adapter: object, mock_presidio_engine: MagicMock, sample_detections: list
    ) -> None:
        policies = {
            "PERSON": OperatorPolicy(operator_name="replace", params={"new_value": "<NAME>"}),
            "EMAIL_ADDRESS": OperatorPolicy(operator_name="mask", params={"chars_to_mask": 5}),
        }
        adapter.anonymize(
            "John Smith says john@example.com",
            detections=sample_detections,
            operator_policies=policies,
        )

        call_kwargs = mock_presidio_engine.anonymize.call_args[1]
        operators = call_kwargs["operators"]

        assert "PERSON" in operators
        assert operators["PERSON"].operator_name == "replace"
        assert "EMAIL_ADDRESS" in operators
        assert operators["EMAIL_ADDRESS"].operator_name == "mask"
        assert "DEFAULT" not in operators
