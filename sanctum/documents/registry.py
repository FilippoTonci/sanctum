"""Path → (reader, writer) dispatch for structured office documents.

The registry stays lazy: concrete adapters are imported only when their
format is requested. This keeps plain-text callers from paying the
python-docx/openpyxl/pdfplumber/python-pptx import cost.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

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


LayoutBuilder = Callable[[Path], dict[str, Any]]


def _import_adapter(path: Path) -> Any:
    suffix = path.suffix.lower()
    module_name = _SUFFIX_MAP.get(suffix)
    if module_name is None:
        raise UnsupportedDocumentFormatError(
            f"No adapter registered for '{suffix}' files. Supported: {sorted(_SUFFIX_MAP)}"
        )

    import importlib

    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise UnsupportedDocumentFormatError(
            f"Adapter module '{module_name}' not importable: {exc}"
        ) from exc


def layout_builder_for(path: Path) -> LayoutBuilder:
    """Return the adapter's ``build_layout(path) -> dict`` for ``path``'s format.

    Adapters opt in to the review-surface layout contract (``GET
    /review-sessions/<id>/layout``) by exporting a module-level
    ``build_layout``. Raises :class:`UnsupportedDocumentFormatError` when
    the format has no adapter or the adapter has no layout builder yet.
    """
    module = _import_adapter(path)
    builder = getattr(module, "build_layout", None)
    if builder is None:
        raise UnsupportedDocumentFormatError(
            f"No layout builder for '{path.suffix.lower()}' files yet."
        )
    return builder  # type: ignore[no-any-return]


def adapter_for(path: Path) -> AdapterPair:
    """Return ``(reader, writer)`` for the given file's extension.

    Raises:
        UnsupportedDocumentFormatError: if no adapter is registered for the
            file's suffix, or the module exists but does not yet expose
            ``Reader``/``Writer`` classes (i.e., adapter not implemented).
    """
    module = _import_adapter(path)
    module_name = module.__name__

    try:
        reader_cls = module.Reader
        writer_cls = module.Writer
    except AttributeError as exc:
        raise UnsupportedDocumentFormatError(
            f"Adapter '{module_name}' missing Reader/Writer: {exc}"
        ) from exc

    return reader_cls(), writer_cls()
