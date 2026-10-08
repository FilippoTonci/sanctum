"""Tests for the `_create_engine` composition root.

The bundled GLiNER-PII recognizer replaces `SpacyRecognizer`, is built from
the configured model dir / threshold, and a missing model fails closed with a
`ConfigurationError` instead of anything that could reach the network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sanctum.core.exceptions import ConfigurationError


def _settings(model_dir: Path | None) -> MagicMock:
    s = MagicMock()
    s.nlp.spacy_model = "en_core_web_sm"
    s.nlp.ner_model_dir = model_dir
    s.nlp.ner_threshold = 0.2
    s.analyzer.default_score_threshold = 0.35
    s.analyzer.default_language = "en"
    s.anonymizer.default_operator = "replace"
    return s


def test_create_engine_wires_gliner_in_place_of_spacy_ner(tmp_path: Path) -> None:
    fake_recognizer = MagicMock(name="gliner_recognizer")
    with (
        patch("sanctum.analyzer.nlp_config.create_nlp_engine"),
        patch("sanctum.analyzer.ner_model.resolve_model_dir", return_value=tmp_path) as resolve,
        patch(
            "sanctum.analyzer.gliner_recognizer.GlinerOnnxRecognizer",
            return_value=fake_recognizer,
        ) as rec_cls,
        patch("sanctum.anonymizer.adapter.PresidioAnonymizer"),
        patch("sanctum.analyzer.adapter.PresidioAnalyzer") as mock_analyzer_cls,
        patch("sanctum.cli.commands.settings", _settings(tmp_path)),
    ):
        from sanctum.cli.commands import _create_engine

        _create_engine()

    resolve.assert_called_once_with(tmp_path)
    rec_cls.assert_called_once_with(tmp_path, threshold=0.2, score_floor=0.35)
    _, kwargs = mock_analyzer_cls.call_args
    extra = kwargs["extra_recognizers"]
    assert [type(r).__name__ for r in extra] == ["AnyDomainEmailRecognizer", "MagicMock"]
    assert extra[1] is fake_recognizer
    assert kwargs["remove_recognizer_names"] == ["SpacyRecognizer"]


def test_create_engine_fails_closed_without_the_model(tmp_path: Path) -> None:
    with (
        patch("sanctum.analyzer.nlp_config.create_nlp_engine"),
        patch("sanctum.cli.commands.settings", _settings(tmp_path / "nowhere")),
    ):
        from sanctum.cli.commands import _create_engine

        with pytest.raises(ConfigurationError, match="fetch_ner_model.py"):
            _create_engine()
