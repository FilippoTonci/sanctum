from __future__ import annotations

from pathlib import Path


class TextDocumentReader:
    """Reads plain-text documents from disk."""

    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")


class TextDocumentWriter:
    """Writes plain-text documents to disk."""

    def write(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
