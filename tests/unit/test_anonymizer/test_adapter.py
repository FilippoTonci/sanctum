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


class TestPerDetectionReplacements:
    """Phase 1.5 WS2 — review emission needs per-detection replacement text.

    Presidio returns ``EngineResult.items`` in reverse-start order (right-
    to-left processing). The adapter zips those back into ``detections``
    order so downstream review-emitting writers can render one trailer per
    detection. The zip correctness is the load-bearing contract; these
    tests pin it.
    """

    @pytest.fixture()
    def adapter(self):  # type: ignore[no-untyped-def]
        with patch("sanctum.anonymizer.adapter.AnonymizerEngine"):
            from sanctum.anonymizer.adapter import PresidioAnonymizer

            return PresidioAnonymizer()

    def _mock_item(self, text: str) -> object:
        item = MagicMock()
        item.text = text
        return item

    def test_zips_items_back_to_original_detection_order(self, adapter: object) -> None:
        """Detections in the input order [PERSON@0, EMAIL@20]; Presidio emits
        items reversed (EMAIL first). The returned list must be in input
        order: [PERSON replacement, EMAIL replacement]."""
        detections = [
            DetectionResult(
                entity_type="PERSON", start=0, end=10, score=0.9, text_span="John Smith"
            ),
            DetectionResult(
                entity_type="EMAIL_ADDRESS",
                start=20,
                end=40,
                score=0.9,
                text_span="john@example.com",
            ),
        ]
        engine_result = MagicMock()
        engine_result.items = [
            self._mock_item("<EMAIL>"),
            self._mock_item("<PERSON>"),
        ]
        got = adapter._replacements_for(detections, engine_result)
        assert got == ["<PERSON>", "<EMAIL>"]

    def test_single_detection_maps_to_single_item(self, adapter: object) -> None:
        detections = [
            DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        ]
        engine_result = MagicMock()
        engine_result.items = [self._mock_item("[PERSON_1]")]
        assert adapter._replacements_for(detections, engine_result) == ["[PERSON_1]"]

    def test_empty_detections_returns_empty_list(self, adapter: object) -> None:
        engine_result = MagicMock()
        engine_result.items = []
        assert adapter._replacements_for([], engine_result) == []

    def test_mismatched_counts_falls_back_to_none(self, adapter: object) -> None:
        """If Presidio merged two detections into one item, we can't recover
        a 1:1 mapping — refuse to guess. Downstream review emission treats
        None as 'do not emit trailers'."""
        detections = [
            DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice"),
            DetectionResult(entity_type="PERSON", start=5, end=10, score=0.9, text_span="Smith"),
        ]
        engine_result = MagicMock()
        engine_result.items = [self._mock_item("<PERSON>")]
        assert adapter._replacements_for(detections, engine_result) is None

    def test_missing_items_attr_returns_none(self, adapter: object) -> None:
        class _NoItems:
            pass

        detections = [
            DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        ]
        assert adapter._replacements_for(detections, _NoItems()) is None
