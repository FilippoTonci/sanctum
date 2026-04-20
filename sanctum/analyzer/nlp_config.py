from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from presidio_analyzer.nlp_engine import NerModelConfiguration, NlpEngineProvider

# Presidio's shipped `conf/default.yaml` puts ORGANIZATION in
# `labels_to_ignore` because spaCy's ORG label is noisy in general text.
# For Sanctum's domain (legal/consulting) firm names are critical PII —
# missing them is a privacy failure, while extra ORG hits are a tolerable
# utility hit. We reuse Presidio's noisy-label list verbatim *minus*
# ORGANIZATION so the rest of the suppression behaviour is preserved.
DEFAULT_LABELS_TO_IGNORE: tuple[str, ...] = (
    "CARDINAL",
    "EVENT",
    "LANGUAGE",
    "LAW",
    "MONEY",
    "ORDINAL",
    "PERCENT",
    "PRODUCT",
    "QUANTITY",
    "WORK_OF_ART",
)

# Natural-language GLiNER prompts → Sanctum taxonomy. GLiNER scores
# lowercase prompts higher than uppercase enum-style labels (confirmed in
# the sanctum-research benchmark), so the mapping is prompt → Sanctum
# entity and kept symmetric with Presidio's stock taxonomy.
_GLINER_ENTITY_MAPPING: dict[str, str] = {
    "person": "PERSON",
    "location": "LOCATION",
    "organization": "ORGANIZATION",
    "date": "DATE_TIME",
    "email address": "EMAIL_ADDRESS",
    "phone number": "PHONE_NUMBER",
    "url": "URL",
    "ip address": "IP_ADDRESS",
    "iban": "IBAN_CODE",
    "social security number": "US_SSN",
    "driver license number": "US_DRIVER_LICENSE",
    "bank account number": "US_BANK_NUMBER",
    "credit card number": "CREDIT_CARD",
}


def create_nlp_engine(
    model_name: str = "en_core_web_lg",
    labels_to_ignore: Sequence[str] = DEFAULT_LABELS_TO_IGNORE,
) -> Any:
    """Create a spaCy-backed NLP engine for Presidio analysis.

    Building this engine explicitly (rather than letting `AnalyzerEngine`
    fall through to its default) is what keeps Sanctum air-gapped: the
    default path can trigger `spacy.cli.download()` on a fresh install.
    Pre-installed models are required.
    """
    ner_config = NerModelConfiguration(labels_to_ignore=list(labels_to_ignore))
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": model_name}],
            "ner_model_configuration": ner_config.to_dict(),
        }
    )
    return provider.create_engine()


def create_gliner_recognizer(
    model_name: str = "urchade/gliner_medium-v2.1",
    threshold: float = 0.4,
) -> Any:
    """Build a Presidio `GLiNERRecognizer` wired to Sanctum's taxonomy.

    Imports the recognizer lazily so the `gliner` extra stays optional —
    callers that never hit this path (e.g. spaCy-only installs) don't pay
    the torch import cost. Raises a clear `ImportError` pointing to the
    extra if gliner isn't installed.
    """
    try:
        from presidio_analyzer.predefined_recognizers import GLiNERRecognizer
    except ImportError as exc:  # pragma: no cover — presidio always ships the class
        raise ImportError("GLiNERRecognizer not available in this presidio-analyzer build") from exc

    return GLiNERRecognizer(
        model_name=model_name,
        entity_mapping=_GLINER_ENTITY_MAPPING,
        threshold=threshold,
        flat_ner=True,
        multi_label=False,
        map_location="cpu",
    )
