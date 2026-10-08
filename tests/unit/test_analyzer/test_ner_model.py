from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from sanctum.analyzer import ner_model
from sanctum.config.settings import NlpSettings, SanctumSettings
from sanctum.core.exceptions import ConfigurationError


def _install(model_dir: Path, names: tuple[str, ...] = ner_model.REQUIRED_FILES) -> None:
    for name in names:
        (model_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (model_dir / name).write_bytes(b"x")


def test_resolve_returns_configured_dir_when_complete(tmp_path: Path) -> None:
    _install(tmp_path)
    assert ner_model.resolve_model_dir(tmp_path) == tmp_path


def test_resolve_names_missing_files_and_the_fetch_command(tmp_path: Path) -> None:
    _install(tmp_path, ("tokenizer.json",))
    with pytest.raises(ConfigurationError) as exc:
        ner_model.resolve_model_dir(tmp_path)
    msg = str(exc.value)
    assert "onnx/model_quint8.onnx" in msg and "gliner_config.json" in msg
    assert "tokenizer.json," not in msg
    assert f"fetch_ner_model.py --dest {tmp_path}" in msg
    assert "never downloads" in msg


def test_resolve_defaults_to_default_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ner_model, "default_model_dir", lambda: tmp_path)
    _install(tmp_path)
    assert ner_model.resolve_model_dir(None) == tmp_path


def test_default_dir_dev_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    expected = tmp_path / ".cache" / "sanctum" / "models" / ner_model.DIR_NAME
    assert ner_model.default_model_dir() == expected


def test_default_dir_frozen_sidecar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "sanctum-sidecar"))
    expected = tmp_path.resolve() / "models" / ner_model.DIR_NAME
    assert ner_model.default_model_dir() == expected


def test_checksum_mismatches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = b"model bytes"
    monkeypatch.setattr(
        ner_model,
        "FILES",
        {"a.bin": hashlib.sha256(good).hexdigest(), "b.bin": "0" * 64, "c.bin": "0" * 64},
    )
    (tmp_path / "a.bin").write_bytes(good)
    (tmp_path / "b.bin").write_bytes(b"tampered")
    assert ner_model.checksum_mismatches(tmp_path) == ["b.bin", "c.bin"]


def test_pins_cover_required_files() -> None:
    assert set(ner_model.REQUIRED_FILES) <= set(ner_model.FILES)
    assert all(len(h) == 64 for h in ner_model.FILES.values())
    assert len(ner_model.REVISION) == 40


class TestNlpSettings:
    def test_defaults(self) -> None:
        s = NlpSettings()
        assert s.ner_model_dir is None
        assert s.ner_threshold == 0.2

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("SANCTUM_NLP__NER_MODEL_DIR", str(tmp_path))
        monkeypatch.setenv("SANCTUM_NLP__NER_THRESHOLD", "0.3")
        s = SanctumSettings()
        assert s.nlp.ner_model_dir == tmp_path
        assert s.nlp.ner_threshold == 0.3

    def test_old_backend_switch_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Desktop builds before the bundled model still send this on respawn.
        monkeypatch.setenv("SANCTUM_NLP__NER_BACKEND", "gliner")
        assert SanctumSettings().nlp.ner_threshold == 0.2

    def test_threshold_bounds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANCTUM_NLP__NER_THRESHOLD", "1.5")
        with pytest.raises(ValueError, match="ner_threshold"):
            SanctumSettings()
