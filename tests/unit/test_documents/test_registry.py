"""Tests for :func:`sanctum.documents.registry.adapter_for` dispatch."""
from __future__ import annotations

from pathlib import Path

import pytest
from sanctum.core.exceptions import UnsupportedDocumentFormatError
from sanctum.documents.registry import adapter_for


def test_unknown_extension_raises():
    with pytest.raises(UnsupportedDocumentFormatError, match="No adapter registered"):
        adapter_for(Path("/tmp/thing.odt"))


def test_dispatch_case_insensitive_on_suffix(monkeypatch):
    """A .DOCX path should hit the docx dispatcher. We don't need the real
    adapter loaded — we only care the registry normalizes the suffix."""
    import importlib

    seen: list[str] = []

    def _fake_import(name):
        seen.append(name)
        raise ImportError("stub")

    monkeypatch.setattr(importlib, "import_module", _fake_import)

    with pytest.raises(UnsupportedDocumentFormatError):
        adapter_for(Path("/tmp/Case.DOCX"))

    assert seen == ["sanctum.documents.docx_adapter"]
