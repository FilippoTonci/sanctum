"""Tests for the `_create_engine` composition root.

Covers the NER-backend switch: default `spacy` path must not touch the
GLiNER factory at all, and the `gliner` path must (a) call the factory
with the configured model/threshold and (b) tell `PresidioAnalyzer` to
remove `SpacyRecognizer` from the registry.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_create_engine_defaults_to_spacy_backend() -> None:
    """Default backend does not import or build a GLiNER recognizer."""
    with (
        patch("sanctum.cli.commands.PresidioAnalyzer", create=True) as mock_analyzer_cls,
        patch("sanctum.cli.commands.PresidioAnonymizer", create=True),
        patch("sanctum.analyzer.nlp_config.create_gliner_recognizer") as mock_factory,
    ):
        # Re-import the commands module with the patches in place so the
        # local imports inside _create_engine pick up the mocks.
        from sanctum.analyzer.adapter import PresidioAnalyzer as _Real
        from sanctum.cli.commands import _create_engine

        mock_analyzer_cls.__module__ = _Real.__module__
        with patch("sanctum.analyzer.adapter.PresidioAnalyzer", mock_analyzer_cls):
            _create_engine()

    mock_factory.assert_not_called()
    # The analyzer was instantiated with empty extra/remove lists.
    _, kwargs = mock_analyzer_cls.call_args
    assert kwargs["extra_recognizers"] == []
    assert kwargs["remove_recognizer_names"] == []


def test_create_engine_gliner_backend_swaps_recognizer() -> None:
    """When ner_backend=gliner, SpacyRecognizer is removed and GLiNER added."""
    fake_recognizer = MagicMock(name="gliner_recognizer")

    with (
        patch(
            "sanctum.analyzer.nlp_config.create_gliner_recognizer",
            return_value=fake_recognizer,
        ) as mock_factory,
        patch("sanctum.analyzer.nlp_config.create_nlp_engine"),
        patch("sanctum.anonymizer.adapter.PresidioAnonymizer"),
        patch("sanctum.analyzer.adapter.PresidioAnalyzer") as mock_analyzer_cls,
        patch("sanctum.cli.commands.settings") as mock_settings,
    ):
        mock_settings.nlp.ner_backend = "gliner"
        mock_settings.nlp.spacy_model = "en_core_web_lg"
        mock_settings.nlp.gliner_model = "urchade/gliner_medium-v2.1"
        mock_settings.nlp.gliner_threshold = 0.4
        mock_settings.analyzer.default_score_threshold = 0.35
        mock_settings.analyzer.default_language = "en"
        mock_settings.anonymizer.default_operator = "replace"

        from sanctum.cli.commands import _create_engine

        _create_engine()

    mock_factory.assert_called_once_with(model_name="urchade/gliner_medium-v2.1", threshold=0.4)
    _, kwargs = mock_analyzer_cls.call_args
    assert kwargs["extra_recognizers"] == [fake_recognizer]
    assert kwargs["remove_recognizer_names"] == ["SpacyRecognizer"]
