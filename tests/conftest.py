from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from sanctum.core.engine import SanctumEngine
from sanctum.core.models import AnonymizationResult, DetectionResult


SAMPLE_TEXT = (
    "My name is John Smith and my SSN is 123-45-6789. "
    "Contact me at john.smith@email.com or call 555-123-4567."
)


@pytest.fixture()
def sample_text() -> str:
    return SAMPLE_TEXT


@pytest.fixture()
def mock_analyzer(sample_text: str) -> Mock:
    analyzer = Mock()
    analyzer.analyze.return_value = [
        DetectionResult(
            entity_type="PERSON",
            start=11,
            end=21,
            score=0.85,
            text_span="John Smith",
            context=sample_text[:61],
            recognizer_name="SpacyRecognizer",
        ),
        DetectionResult(
            entity_type="US_SSN",
            start=36,
            end=47,
            score=0.95,
            text_span="123-45-6789",
            context=sample_text[:87],
            recognizer_name="UsSsnRecognizer",
        ),
        DetectionResult(
            entity_type="EMAIL_ADDRESS",
            start=64,
            end=84,
            score=0.90,
            text_span="john.smith@email.com",
            context=sample_text[24:],
            recognizer_name="EmailRecognizer",
        ),
        DetectionResult(
            entity_type="PHONE_NUMBER",
            start=93,
            end=105,
            score=0.75,
            text_span="555-123-4567",
            context=sample_text[53:],
            recognizer_name="PhoneRecognizer",
        ),
    ]
    return analyzer


@pytest.fixture()
def mock_anonymizer(sample_text: str) -> Mock:
    anonymizer = Mock()
    anonymizer.anonymize.return_value = AnonymizationResult(
        original_text=sample_text,
        anonymized_text=(
            "My name is <PERSON> and my SSN is <US_SSN>. "
            "Contact me at <EMAIL_ADDRESS> or call <PHONE_NUMBER>."
        ),
        detections=[
            DetectionResult(
                entity_type="PERSON",
                start=11,
                end=21,
                score=0.85,
                text_span="John Smith",
            ),
            DetectionResult(
                entity_type="US_SSN",
                start=36,
                end=47,
                score=0.95,
                text_span="123-45-6789",
            ),
            DetectionResult(
                entity_type="EMAIL_ADDRESS",
                start=64,
                end=84,
                score=0.90,
                text_span="john.smith@email.com",
            ),
            DetectionResult(
                entity_type="PHONE_NUMBER",
                start=93,
                end=105,
                score=0.75,
                text_span="555-123-4567",
            ),
        ],
        operators_applied={
            "PERSON": "replace",
            "US_SSN": "replace",
            "EMAIL_ADDRESS": "replace",
            "PHONE_NUMBER": "replace",
        },
    )
    return anonymizer


@pytest.fixture()
def engine(mock_analyzer: Mock, mock_anonymizer: Mock) -> SanctumEngine:
    return SanctumEngine(analyzer=mock_analyzer, anonymizer=mock_anonymizer)


@pytest.fixture()
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"
