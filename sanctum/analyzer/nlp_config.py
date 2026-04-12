from __future__ import annotations

from typing import Any

from presidio_analyzer.nlp_engine import NlpEngineProvider


def create_nlp_engine(model_name: str = "en_core_web_sm") -> Any:
    """Create a spaCy-backed NLP engine for Presidio analysis."""
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": model_name}],
        }
    )
    return provider.create_engine()
