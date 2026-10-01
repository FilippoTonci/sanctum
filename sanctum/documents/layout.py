"""Format → review-layout builder dispatch (Phase 3.5 shared layout contract).

Lazy like :mod:`sanctum.documents.registry`: the builder module for a
format is imported only when a layout for that format is requested.
"""

from __future__ import annotations

import importlib
from io import BytesIO
from typing import Any

from sanctum.core.exceptions import UnsupportedDocumentFormatError

_LAYOUT_BUILDERS = {
    "pptx": "sanctum.documents.pptx_layout",
}


def supports_layout(fmt: str) -> bool:
    return fmt in _LAYOUT_BUILDERS


def build_layout(fmt: str, data: bytes) -> dict[str, Any]:
    """Return the layout payload for a document of ``fmt`` given its bytes.

    Raises:
        UnsupportedDocumentFormatError: no layout builder for ``fmt``.
    """
    module_name = _LAYOUT_BUILDERS.get(fmt)
    if module_name is None:
        raise UnsupportedDocumentFormatError(
            f"No review layout for '{fmt}' documents. Supported: {sorted(_LAYOUT_BUILDERS)}"
        )
    module = importlib.import_module(module_name)
    result: dict[str, Any] = module.build_layout(BytesIO(data))
    return result
