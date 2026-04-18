from __future__ import annotations

from unittest.mock import patch

from sanctum.analyzer.nlp_config import DEFAULT_LABELS_TO_IGNORE, create_nlp_engine


class TestCreateNlpEngine:
    def test_organization_not_in_default_ignore_list(self) -> None:
        # The whole point of overriding Presidio's defaults: ORGANIZATION
        # must stay live so legal/consulting docs flag firm names.
        assert "ORGANIZATION" not in DEFAULT_LABELS_TO_IGNORE

    def test_passes_model_name_through_to_provider(self) -> None:
        with patch("sanctum.analyzer.nlp_config.NlpEngineProvider") as provider_cls:
            create_nlp_engine(model_name="en_core_web_sm")

        cfg = provider_cls.call_args.kwargs["nlp_configuration"]
        assert cfg["models"] == [{"lang_code": "en", "model_name": "en_core_web_sm"}]
        assert cfg["nlp_engine_name"] == "spacy"

    def test_ignore_list_is_propagated_to_ner_model_config(self) -> None:
        with patch("sanctum.analyzer.nlp_config.NlpEngineProvider") as provider_cls:
            create_nlp_engine(labels_to_ignore=("FOO", "BAR"))

        cfg = provider_cls.call_args.kwargs["nlp_configuration"]
        assert set(cfg["ner_model_configuration"]["labels_to_ignore"]) == {"FOO", "BAR"}
