from __future__ import annotations

from pathlib import Path

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
    """Defaults for the anonymizer.

    Valid values for `default_operator` are listed with descriptions in
    `sanctum.anonymizer.operators.BUILTIN_OPERATOR_NAMES`.
    """

    default_operator: str = "replace"


class SecuritySettings(BaseSettings):
    """Mapping-store configuration for reversible pseudonymization.

    `session_only=True` (default) selects `InMemoryMappingStore`: nothing
    touches disk, nothing survives the process. Set `session_only=False`
    and provide `store_path` to use `EncryptedFileMappingStore`; the user
    supplies the passphrase at unlock time.

    KDF cost fields are surfaced here so low-end hardware can dial them
    down without a code change (defaults match `sanctum.security.keyring`).
    """

    session_only: bool = True
    store_path: Path | None = None
    kdf_time_cost: int = 3
    kdf_memory_cost: int = 128 * 1024  # KiB; 128 MiB default.
    kdf_parallelism: int = 1


class SanctumSettings(BaseSettings):
    """Root settings — all sub-sections are nested."""

    model_config = SettingsConfigDict(
        env_prefix="SANCTUM_",
        env_nested_delimiter="__",
    )

    nlp: NlpSettings = Field(default_factory=NlpSettings)
    analyzer: AnalyzerSettings = Field(default_factory=AnalyzerSettings)
    anonymizer: AnonymizerSettings = Field(default_factory=AnonymizerSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)


settings = SanctumSettings()
