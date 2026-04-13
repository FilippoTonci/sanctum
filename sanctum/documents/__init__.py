"""Document reader/writer adapters.

Plain-text adapters live in :mod:`sanctum.documents.base`. Office-format
adapters (docx/xlsx/pdf/pptx) live alongside them and are dispatched via
:func:`sanctum.documents.registry.adapter_for`.
"""

from __future__ import annotations

from sanctum.documents.base import TextDocumentReader, TextDocumentWriter
from sanctum.documents.registry import adapter_for
from sanctum.documents.structured import build_document, build_segment

__all__ = [
    "TextDocumentReader",
    "TextDocumentWriter",
    "adapter_for",
    "build_document",
    "build_segment",
]
