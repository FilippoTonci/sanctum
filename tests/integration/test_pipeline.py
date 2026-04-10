from __future__ import annotations

import pytest

from sanctum.analyzer.adapter import PresidioAnalyzer
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.anonymizer.operators.hips import HipsOperator
from sanctum.core.engine import SanctumEngine
from sanctum.core.models import OperatorPolicy

pytestmark = pytest.mark.integration


@pytest.fixture()
def analyzer() -> PresidioAnalyzer:
    return PresidioAnalyzer()


@pytest.fixture()
def anonymizer() -> PresidioAnonymizer:
    return PresidioAnonymizer()


@pytest.fixture()
def pipeline(analyzer: PresidioAnalyzer, anonymizer: PresidioAnonymizer) -> SanctumEngine:
    return SanctumEngine(analyzer=analyzer, anonymizer=anonymizer)


class TestAnalyzerDetection:
    def test_analyze_finds_person_name(self, analyzer: PresidioAnalyzer) -> None:
        text = "My name is John Smith and I live in New York."
        results = analyzer.analyze(text)
        entity_types = {r.entity_type for r in results}
        assert "PERSON" in entity_types
        person_results = [r for r in results if r.entity_type == "PERSON"]
        assert any("John Smith" in r.text_span for r in person_results)

    def test_analyze_finds_email(self, analyzer: PresidioAnalyzer) -> None:
        text = "Please email me at john.smith@example.com for details."
        results = analyzer.analyze(text)
        entity_types = {r.entity_type for r in results}
        assert "EMAIL_ADDRESS" in entity_types

    def test_analyze_finds_phone(self, analyzer: PresidioAnalyzer) -> None:
        text = "Call me at 555-123-4567 to discuss the matter."
        results = analyzer.analyze(text)
        entity_types = {r.entity_type for r in results}
        assert "PHONE_NUMBER" in entity_types


class TestFullPipeline:
    def test_full_pipeline_redact(self, pipeline: SanctumEngine) -> None:
        text = "My name is John Smith and my email is john@example.com."
        result = pipeline.process(text)

        assert "John Smith" not in result.anonymized_text
        assert "john@example.com" not in result.anonymized_text
        assert result.original_text == text
        assert len(result.detections) > 0

    def test_full_pipeline_hips(self, analyzer: PresidioAnalyzer) -> None:
        anonymizer = PresidioAnonymizer(default_operator="hips")
        anonymizer._engine.add_anonymizer(HipsOperator)

        pipeline = SanctumEngine(analyzer=analyzer, anonymizer=anonymizer)

        text = "My name is John Smith and my email is john@example.com."

        policies = {
            "PERSON": OperatorPolicy(operator_name="hips"),
            "EMAIL_ADDRESS": OperatorPolicy(operator_name="hips"),
        }
        result = pipeline.process(text, operator_policies=policies)

        assert "John Smith" not in result.anonymized_text
        assert "john@example.com" not in result.anonymized_text
        assert result.original_text == text
        # HIPS replaces with synthetic values, not entity type tags
        assert "<PERSON>" not in result.anonymized_text
