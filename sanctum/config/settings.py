from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class NlpSettings(BaseSettings):
    """SpaCy / NLP model configuration.

    `ner_backend` selects which recognizer drives PERSON / LOCATION /
    ORGANIZATION detection. `"spacy"` (default) uses Presidio's stock
    `SpacyRecognizer` on top of `spacy_model`; `"gliner"` swaps that out
    for a `GLiNERRecognizer`, keeping spaCy loaded as a tokenizer only
    so context-dependent pattern recognizers still work.

    GLiNER weights are fetched from HuggingFace on first load and cached
    under `~/.cache/huggingface/`. Treat that fetch as install-time, the
    same posture as `en_core_web_lg`; set `HF_HUB_OFFLINE=1` in airgapped
    environments to fail fast instead of attempting a network call.
    """

    spacy_model: str = "en_core_web_sm"
    ner_backend: Literal["spacy", "gliner"] = "spacy"
    gliner_model: str = "urchade/gliner_medium-v2.1"
    gliner_threshold: float = 0.4


class AnalyzerSettings(BaseSettings):
    """Defaults for the PII analyzer."""

    default_score_threshold: float = 0.35
    default_language: str = "en"


class AnonymizerSettings(BaseSettings):
    """Defaults for the anonymizer.

    Valid values for `default_operator` are listed with descriptions in
    `sanctum.anonymizer.operators.BUILTIN_OPERATOR_NAMES`.

    `hips` is the default: synthetic Faker-backed names preserve document
    readability ("Alice Smith" → "Madison Perez") which is the
    legal/consulting-reviewer's default ask. Users who want tagged
    placeholders instead can set `SANCTUM_ANONYMIZER__DEFAULT_OPERATOR=replace`
    (which produces `<PERSON>` etc.); numbered `<PERSON_1>` / `<PERSON_2>`
    placeholders are tracked in issue #19.
    """

    default_operator: str = "hips"


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

    @model_validator(mode="after")
    def _require_store_path_when_persistent(self) -> SecuritySettings:
        if not self.session_only and self.store_path is None:
            raise ValueError("security.store_path must be set when security.session_only is False")
        return self


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
