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

    def test_uses_default_threshold(self, adapter: object, mock_presidio_engine: MagicMock) -> None:
        mock_presidio_engine.analyze.return_value = []
        adapter.analyze("some text")

        call_kwargs = mock_presidio_engine.analyze.call_args[1]
        assert call_kwargs["score_threshold"] == 0.4

    def test_uses_custom_threshold(self, adapter: object, mock_presidio_engine: MagicMock) -> None:
        mock_presidio_engine.analyze.return_value = []
        adapter.analyze("some text", score_threshold=0.8)

        call_kwargs = mock_presidio_engine.analyze.call_args[1]
        assert call_kwargs["score_threshold"] == 0.8

    def test_extra_recognizers_added_to_registry(self, mock_presidio_engine: MagicMock) -> None:
        extra = MagicMock(name="fake_gliner_recognizer")
        with patch("sanctum.analyzer.adapter.AnalyzerEngine", return_value=mock_presidio_engine):
            from sanctum.analyzer.adapter import PresidioAnalyzer

            PresidioAnalyzer(extra_recognizers=[extra])

        mock_presidio_engine.registry.add_recognizer.assert_called_once_with(extra)
        mock_presidio_engine.registry.remove_recognizer.assert_not_called()

    def test_remove_recognizer_names_forwarded_to_registry(
        self, mock_presidio_engine: MagicMock
    ) -> None:
        with patch("sanctum.analyzer.adapter.AnalyzerEngine", return_value=mock_presidio_engine):
            from sanctum.analyzer.adapter import PresidioAnalyzer

            PresidioAnalyzer(remove_recognizer_names=["SpacyRecognizer"])

        mock_presidio_engine.registry.remove_recognizer.assert_called_once_with("SpacyRecognizer")
        mock_presidio_engine.registry.add_recognizer.assert_not_called()

    def test_keeps_enclosing_span_when_one_detection_contains_another(
        self, adapter: object, mock_presidio_engine: MagicMock
    ) -> None:
        text = "alice@example.org"
        mock_presidio_engine.analyze.return_value = [
            self._make_recognizer_result("EMAIL_ADDRESS", 0, 17, 0.9, "EmailRecognizer"),
            self._make_recognizer_result("URL", 6, 17, 0.8, "UrlRecognizer"),
        ]

        results = adapter.analyze(text)

        assert [(r.entity_type, r.start, r.end) for r in results] == [("EMAIL_ADDRESS", 0, 17)]

    def test_partial_overlap_trims_smaller_span(
        self, adapter: object, mock_presidio_engine: MagicMock
    ) -> None:
        text = "ABCDEFGHIJKLMNO"
        mock_presidio_engine.analyze.return_value = [
            self._make_recognizer_result("PERSON", 0, 6, 0.8, "LeftRecognizer"),
            self._make_recognizer_result("LOCATION", 4, 14, 0.9, "RightRecognizer"),
        ]

        results = adapter.analyze(text)

        assert [(r.entity_type, r.start, r.end, r.text_span) for r in results] == [
            ("PERSON", 0, 4, "ABCD"),
            ("LOCATION", 4, 14, "EFGHIJKLMN"),
        ]

    def test_non_overlapping_results_are_preserved(
        self, adapter: object, mock_presidio_engine: MagicMock
    ) -> None:
        text = "alice@example.org and https://example.org"
        mock_presidio_engine.analyze.return_value = [
            self._make_recognizer_result("EMAIL_ADDRESS", 0, 17, 0.9, "EmailRecognizer"),
            self._make_recognizer_result("URL", 22, 41, 0.8, "UrlRecognizer"),
        ]

        results = adapter.analyze(text)

        assert [(r.entity_type, r.start, r.end) for r in results] == [
            ("EMAIL_ADDRESS", 0, 17),
            ("URL", 22, 41),
        ]
