from __future__ import annotations

from unittest.mock import Mock

import pytest

from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import AnalysisError, AnonymizationError
from sanctum.core.models import DetectionResult, OperatorPolicy


class TestEngineAnalyze:
    def test_analyze_delegates_to_analyzer(
        self, engine: SanctumEngine, mock_analyzer: Mock, sample_text: str
    ) -> None:
        engine.analyze(sample_text, language="en", entities=["PERSON"], score_threshold=0.5)

        mock_analyzer.analyze.assert_called_once_with(
            sample_text,
            language="en",
            entities=["PERSON"],
            score_threshold=0.5,
        )

    def test_analyze_returns_analyzer_results(
        self, engine: SanctumEngine, mock_analyzer: Mock, sample_text: str
    ) -> None:
        results = engine.analyze(sample_text)
        assert results == mock_analyzer.analyze.return_value

    def test_analyze_wraps_exceptions(
        self, engine: SanctumEngine, mock_analyzer: Mock, sample_text: str
    ) -> None:
        mock_analyzer.analyze.side_effect = ValueError("NLP model failed")

        with pytest.raises(AnalysisError, match="Analysis failed"):
            engine.analyze(sample_text)


class TestEngineAnonymize:
    def test_anonymize_with_detections_skips_analysis(
        self, engine: SanctumEngine, mock_analyzer: Mock, mock_anonymizer: Mock, sample_text: str
    ) -> None:
        detections = [
            DetectionResult(
                entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice"
            ),
        ]
        engine.anonymize(sample_text, detections=detections)

        mock_analyzer.analyze.assert_not_called()
        mock_anonymizer.anonymize.assert_called_once()

    def test_anonymize_without_detections_runs_analysis(
        self, engine: SanctumEngine, mock_analyzer: Mock, mock_anonymizer: Mock, sample_text: str
    ) -> None:
        engine.anonymize(sample_text)

        mock_analyzer.analyze.assert_called_once()
        mock_anonymizer.anonymize.assert_called_once()

    def test_anonymize_wraps_exceptions(
        self, engine: SanctumEngine, mock_anonymizer: Mock, sample_text: str
    ) -> None:
        detections = [
            DetectionResult(
                entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice"
            ),
        ]
        mock_anonymizer.anonymize.side_effect = ValueError("Operator failed")

        with pytest.raises(AnonymizationError, match="Anonymization failed"):
            engine.anonymize(sample_text, detections=detections)


class TestEngineProcess:
    def test_process_chains_analyze_and_anonymize(
        self, engine: SanctumEngine, mock_analyzer: Mock, mock_anonymizer: Mock, sample_text: str
    ) -> None:
        result = engine.process(sample_text)

        mock_analyzer.analyze.assert_called_once()
        mock_anonymizer.anonymize.assert_called_once()
        assert result == mock_anonymizer.anonymize.return_value

    def test_process_passes_operator_policies(
        self, engine: SanctumEngine, mock_anonymizer: Mock, sample_text: str
    ) -> None:
        policies = {"PERSON": OperatorPolicy(operator_name="replace", params={"new_value": "X"})}
        engine.process(sample_text, operator_policies=policies)

        call_kwargs = mock_anonymizer.anonymize.call_args
        assert call_kwargs.kwargs.get("operator_policies") == policies or (
            call_kwargs[1].get("operator_policies") == policies
        )
