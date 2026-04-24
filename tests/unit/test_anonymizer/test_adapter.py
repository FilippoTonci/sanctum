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

    def test_operators_applied_reflects_default_policy(
        self, adapter: object, mock_presidio_engine: MagicMock, sample_detections: list
    ) -> None:
        """When a caller passes `{"DEFAULT": OperatorPolicy(...)}`, the
        telemetry must report that operator — not `self._default_operator`.

        Guards against the bug where `/anonymize?operator=redact` returned
        `operators_applied={"PERSON":"replace", ...}` because the adapter
        only looked up entity-type keys, missed `DEFAULT`, and fell back
        to the constructor default.
        """
        policies = {"DEFAULT": OperatorPolicy(operator_name="hash")}
        result = adapter.anonymize(
            "John Smith says john@example.com",
            detections=sample_detections,
            operator_policies=policies,
        )
        assert result.operators_applied == {"PERSON": "hash", "EMAIL_ADDRESS": "hash"}

    def test_default_policy_coexists_with_per_entity_override(
        self, adapter: object, mock_presidio_engine: MagicMock, sample_detections: list
    ) -> None:
        """Per-entity policy wins over the DEFAULT for its own type."""
        policies = {
            "DEFAULT": OperatorPolicy(operator_name="hash"),
            "PERSON": OperatorPolicy(operator_name="replace"),
        }
        result = adapter.anonymize(
            "John Smith says john@example.com",
            detections=sample_detections,
            operator_policies=policies,
        )
        assert result.operators_applied == {"PERSON": "replace", "EMAIL_ADDRESS": "hash"}

    def test_hips_operator_is_registered(self) -> None:
        """HipsOperator must be added to the engine at construction time.

        Without this registration, `operator="hips"` returns a 500 from
        Presidio with `Invalid operator class 'hips'`. Check by name so
        the test stays stable if AnonymizerEngine's internals move.
        """
        from sanctum.anonymizer.adapter import PresidioAnonymizer

        adapter = PresidioAnonymizer()
        # Presidio stores registered custom operators on OperatorsFactory;
        # the public-ish `get_anonymizers` returns {name: class}.
        registered = adapter._engine.get_anonymizers()
        assert "hips" in registered
        assert "pseudonymize" in registered
