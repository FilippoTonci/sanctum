from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class NlpSettings(BaseSettings):
    """NLP / NER model configuration.

    PERSON / ORGANIZATION / LOCATION / DATE_TIME and ID numbers come from the
    bundled GLiNER-PII ONNX model (``sanctum.analyzer.ner_model``), which
    replaces Presidio's spaCy NER. spaCy still runs, as ``spacy_model``, for
    the tokens and lemmas the pattern recognizers' context words need.

    ``ner_model_dir`` defaults to where the model is installed (next to the
    desktop sidecar, or ``~/.cache/sanctum/models/``). The model is never
    downloaded at runtime; if it is missing, engine construction fails with a
    ``ConfigurationError`` that says how to fetch it.

    ``ner_threshold`` is the model's own cut-off. GLiNER-PII is calibrated low:
    0.2 is its benchmarked sweet spot; at Presidio's usual 0.4 it misses
    three to four times as much.
    """

    # Unknown keys (e.g. SANCTUM_NLP__NER_BACKEND from an older desktop build)
    # are ignored rather than refusing to start.
    model_config = SettingsConfigDict(extra="ignore")

    spacy_model: str = "en_core_web_sm"
    ner_model_dir: Path | None = None
    ner_threshold: float = Field(default=0.2, gt=0.0, lt=1.0)


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
