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
