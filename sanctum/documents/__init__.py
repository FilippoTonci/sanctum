"""Document reader/writer adapters.

Office-format adapters (docx/xlsx/pdf/pptx) are dispatched via
:func:`sanctum.documents.registry.adapter_for`.
"""

from __future__ import annotations

from sanctum.documents.registry import adapter_for
from sanctum.documents.structured import build_document, build_segment

__all__ = [
    "adapter_for",
    "build_document",
    "build_segment",
]
