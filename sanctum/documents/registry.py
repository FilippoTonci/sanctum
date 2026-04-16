"""Path → (reader, writer) dispatch for structured office documents.

The registry stays lazy: concrete adapters are imported only when their
format is requested. This keeps plain-text callers from paying the
python-docx/openpyxl/pdfplumber/python-pptx import cost.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sanctum.core.exceptions import UnsupportedDocumentFormatError

if TYPE_CHECKING:
    from sanctum.core.protocols import StructuredDocumentReader, StructuredDocumentWriter


AdapterPair = tuple["StructuredDocumentReader", "StructuredDocumentWriter"]


_SUFFIX_MAP = {
    ".docx": "sanctum.documents.docx_adapter",
    ".xlsx": "sanctum.documents.xlsx_adapter",
    ".pdf": "sanctum.documents.pdf_adapter",
    ".pptx": "sanctum.documents.pptx_adapter",
}


def adapter_for(path: Path) -> AdapterPair:
    """Return ``(reader, writer)`` for the given file's extension.

    Raises:
        UnsupportedDocumentFormatError: if no adapter is registered for the
            file's suffix, or the module exists but does not yet expose
            ``Reader``/``Writer`` classes (i.e., adapter not implemented).
    """
    suffix = path.suffix.lower()
    module_name = _SUFFIX_MAP.get(suffix)
    if module_name is None:
        raise UnsupportedDocumentFormatError(
            f"No adapter registered for '{suffix}' files. Supported: {sorted(_SUFFIX_MAP)}"
        )

    import importlib

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise UnsupportedDocumentFormatError(
            f"Adapter module '{module_name}' not importable: {exc}"
        ) from exc

    try:
        reader_cls = module.Reader
        writer_cls = module.Writer
    except AttributeError as exc:
        raise UnsupportedDocumentFormatError(
            f"Adapter '{module_name}' missing Reader/Writer: {exc}"
        ) from exc

    return reader_cls(), writer_cls()
