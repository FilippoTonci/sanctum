from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sanctum.core.models import DetectionResult


class TestPresidioAnalyzer:
    @pytest.fixture()
    def mock_presidio_engine(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture()
    def adapter(self, mock_presidio_engine: MagicMock) -> object:
        with patch("sanctum.analyzer.adapter.AnalyzerEngine", return_value=mock_presidio_engine):
            from sanctum.analyzer.adapter import PresidioAnalyzer

            analyzer = PresidioAnalyzer(default_score_threshold=0.4)
        # Replace the internal engine with our mock so calls go through it
        analyzer._engine = mock_presidio_engine
        return analyzer

    def _make_recognizer_result(
        self, entity_type: str, start: int, end: int, score: float, recognizer_name: str = ""
    ) -> MagicMock:
        result = MagicMock()
        result.entity_type = entity_type
        result.start = start
        result.end = end
        result.score = score
        result.recognition_metadata = {"recognizer_name": recognizer_name}
        return result

    def test_converts_recognizer_result_to_detection_result(
        self, adapter: object, mock_presidio_engine: MagicMock
    ) -> None:
        text = "Hello John Smith from NYC"
        mock_presidio_engine.analyze.return_value = [
            self._make_recognizer_result("PERSON", 6, 16, 0.85, "SpacyRecognizer"),
            self._make_recognizer_result("LOCATION", 22, 25, 0.6, "SpacyRecognizer"),
        ]

        results = adapter.analyze(text)

        assert len(results) == 2
        assert all(isinstance(r, DetectionResult) for r in results)

        person = results[0]
        assert person.entity_type == "PERSON"
        assert person.start == 6
        assert person.end == 16
        assert person.text_span == "John Smith"
        assert person.score == 0.85
        assert person.recognizer_name == "SpacyRecognizer"
        assert "John Smith" in person.context

        location = results[1]
        assert location.entity_type == "LOCATION"
        assert location.text_span == "NYC"

    def test_uses_default_threshold(
        self, adapter: object, mock_presidio_engine: MagicMock
    ) -> None:
        mock_presidio_engine.analyze.return_value = []
        adapter.analyze("some text")

        call_kwargs = mock_presidio_engine.analyze.call_args[1]
        assert call_kwargs["score_threshold"] == 0.4

    def test_uses_custom_threshold(
        self, adapter: object, mock_presidio_engine: MagicMock
    ) -> None:
        mock_presidio_engine.analyze.return_value = []
        adapter.analyze("some text", score_threshold=0.8)

        call_kwargs = mock_presidio_engine.analyze.call_args[1]
        assert call_kwargs["score_threshold"] == 0.8
