"""PDF reader / redacting writer.

* **Read**: one segment per extracted text line, id ``page{i}/line{j}``
  (``i`` = 0-based page, ``j`` = 0-based line within the page), built from
  pdfplumber word boxes. Segment metadata carries the line's box in
  display space (points, top-left origin, CropBox-relative, after
  ``/Rotate``) plus the page size — the same numbers ``/layout`` serves
  as ``textline`` items. The raw handle keeps per-character boxes for the
  writer.
* **Write**: pages with at least one replaced span are *flattened*: the
  page is rasterized with pypdfium2, every replaced span is painted over
  and its replacement drawn in as real text, and the page is rebuilt as an
  image page (the page's untouched text is kept as an invisible,
  searchable text layer). Pages with no replacements are copied as vector.
  The output is then stripped of document info, XMP, annotations, form
  fields and embedded files. The source is never overwritten.
* **Leak check**: :meth:`Writer.extract_text` implements the core
  ``OutputTextExtractor`` port; the engine uses it after every write.
* **Scanned PDFs**: if no page has extractable text we raise
  :class:`UnsupportedPdfError` (OCR is later work).
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

import pdfplumber

from sanctum.core.exceptions import DocumentError, PdfWriteRefusedError, UnsupportedPdfError
from sanctum.documents._pdf_extract import PdfExtraction, extract
from sanctum.documents._pdf_redact import DEFAULT_DPI, flatten, plan_edits, sanitize
from sanctum.documents.structured import build_document, build_segment

if TYPE_CHECKING:
    from sanctum.core.models import StructuredDocument, TextSegment


def _r(value: float) -> float:
    return round(value, 2)


class Reader:
    """One segment per extracted line, with display-space geometry."""

    def read(self, path: Path) -> StructuredDocument:
        extraction = _extract_file(path)
        segments: list[TextSegment] = []
        started: set[str] = set()
        for line in extraction.lines:
            geom = extraction.pages[line.page]
            x0, top, x1, bottom = line.bbox
            join = " " if line.block in started else ""
            started.add(line.block)
            segments.append(
                build_segment(
                    line.segment_id,
                    line.text,
                    block=line.block,
                    join_before=join,
                    page=line.page,
                    x=_r(x0),
                    y=_r(top),
                    w=_r(x1 - x0),
                    h=_r(bottom - top),
                    size=line.size,
                    page_width=_r(geom.width),
                    page_height=_r(geom.height),
                )
            )
        return build_document(
            source_path=path,
            fmt="pdf",
            segments=segments,
            raw_handle=extraction,
        )


def _extract_file(path: Path) -> PdfExtraction:
    return _extract_bytes(Path(path).read_bytes(), name=str(path))


def _extract_bytes(data: bytes, *, name: str) -> PdfExtraction:
    try:
        extraction = extract(data)
    except Exception as exc:
        raise DocumentError(f"Could not parse PDF {name}: {exc}") from exc
    if not extraction.lines:
        raise UnsupportedPdfError(
            f"{name} has no extractable text layer — "
            "scanned/image-only PDFs need OCR, which is not supported yet."
        )
    return extraction


class Writer:
    """Flatten pages that carry replacements; copy the rest as vector."""

    def __init__(self, dpi: int = DEFAULT_DPI) -> None:
        self.dpi = dpi

    def write(self, doc: StructuredDocument, path: Path) -> None:
        if Path(path).resolve() == Path(doc.source_path).resolve():
            raise PdfWriteRefusedError(
                f"Refusing to overwrite source PDF at {path}. "
                "Redacted PDFs are always written to a new file."
            )
        extraction = doc.raw_handle
        if not isinstance(extraction, PdfExtraction):
            raise DocumentError(
                "PDF writer needs the raw handle produced by the PDF Reader "
                "(re-read the source before writing)."
            )
        edits = plan_edits(doc.segments, extraction)
        output = sanitize(flatten(extraction, edits, dpi=self.dpi))
        _atomic_write(Path(path), output)

    def extract_text(self, path: Path) -> str:
        """All text a reader of ``path`` could get at: pages + metadata."""
        return extract_all_text(Path(path).read_bytes())


def extract_all_text(pdf_bytes: bytes) -> str:
    """Page text (pdfplumber) plus annotation contents and document-info / XMP values."""
    from pypdf import PdfReader

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
            for annot in page.annots or []:
                contents = (annot.get("data") or {}).get("Contents")
                if contents:
                    parts.append(str(contents))
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for value in (reader.metadata or {}).values():
        parts.append(str(value))
    xmp = reader.root_object.get("/Metadata")
    if xmp is not None:
        parts.append(xmp.get_object().get_data().decode("utf-8", "replace"))
    return "\n".join(parts)


def build_layout(source: Path | BinaryIO) -> dict[str, Any]:
    """The ``GET /review-sessions/<id>/layout`` body for a PDF.

    Pages carry only ``textline`` items: the viewer paints the page itself
    with PDF.js and overlays these lines, so no ``image`` items are sent.
    Segment ids are produced by the same extraction the :class:`Reader`
    uses, so they match the session's segments exactly.
    """
    if isinstance(source, Path):
        extraction = _extract_file(source)
    else:
        extraction = _extract_bytes(source.read(), name="<session input>")
    pages: list[dict[str, Any]] = [
        {
            "index": g.index,
            "width": _r(g.width),
            "height": _r(g.height),
            "items": [],
        }
        for g in extraction.pages
    ]
    for line in extraction.lines:
        x0, top, x1, bottom = line.bbox
        pages[line.page]["items"].append(
            {
                "kind": "textline",
                "x": _r(x0),
                "y": _r(top),
                "w": _r(x1 - x0),
                "h": _r(bottom - top),
                "segment_id": line.segment_id,
                "text": line.text,
                "size": line.size,
            }
        )
    return {"format": "pdf", "pages": pages, "unscanned": list(extraction.unscanned)}


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".sanctum-", suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
