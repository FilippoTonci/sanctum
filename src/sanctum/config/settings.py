from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class NlpSettings(BaseSettings):
    """SpaCy / NLP model configuration."""

    spacy_model: str = "en_core_web_sm"


class AnalyzerSettings(BaseSettings):
    """Defaults for the PII analyzer."""

    default_score_threshold: float = 0.35
    default_language: str = "en"


class AnonymizerSettings(BaseSettings):
    """Defaults for the anonymizer."""

    default_operator: str = "redact"


class SanctumSettings(BaseSettings):
    """Root settings — all sub-sections are nested."""

    model_config = SettingsConfigDict(
        env_prefix="SANCTUM_",
        env_nested_delimiter="__",
    )

    nlp: NlpSettings = Field(default_factory=NlpSettings)
    analyzer: AnalyzerSettings = Field(default_factory=AnalyzerSettings)
    anonymizer: AnonymizerSettings = Field(default_factory=AnonymizerSettings)


settings = SanctumSettings()
