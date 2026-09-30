"""PDF structured reader/derivative writer.

.. note::
   STUB FOR VIEWER WORK (Phase 3.5 WS4, branch ``overnight/pdf-view``).
   The line-level segmentation and :func:`layout` below exist only so the
   desktop ``PdfView`` has real ``page{i}/line{j}`` segments and ``textline``
   layout items to render against. They are superseded by the positioned
   reader + redacting writer on ``overnight/pdf-engine`` (Phase 3.5 WS3).
   Keep this small; do not grow it.

Approach:

* **Read**: pdfplumber word boxes are grouped into visual lines; each line
  becomes one segment (``page{i}/line{j}``) whose text is the line's words
  joined by single spaces. Words on the same baseline separated by a gap
  wider than one em (two columns, table cells) start a new line, so every
  segment is one contiguous horizontal run on the page.
* **Write**: reportlab emits a *new* text-only PDF. Overwriting the
  source is refused because the original's images, forms, and layout
  would silently disappear.
* **Scanned PDFs**: if every page returns no extractable text, we
  raise :class:`UnsupportedPdfError` — OCR is out of scope for
  Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pdfplumber

from sanctum.core.exceptions import PdfWriteRefusedError, UnsupportedPdfError
from sanctum.documents.structured import build_document, build_segment

if TYPE_CHECKING:
    from sanctum.core.models import StructuredDocument, TextSegment


# Words whose tops differ by less than this many points share a line.
_LINE_TOLERANCE = 2.0
# A horizontal gap wider than this many ems splits a line (columns, cells).
_SPLIT_GAP_EM = 1.0


@dataclass(frozen=True)
class _Line:
    segment_id: str
    text: str
    x: float
    y: float
    w: float
    h: float
    size: float
    font: str


def _page_lines(page: Any, page_index: int) -> list[_Line]:
    words = page.extract_words(extra_attrs=["size", "fontname"], use_text_flow=False)
    # Cluster into rows by top, then split rows on wide gaps.
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if rows and abs(rows[-1][0]["top"] - word["top"]) <= _LINE_TOLERANCE:
            rows[-1].append(word)
        else:
            rows.append([word])
    runs: list[list[dict[str, Any]]] = []
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        current = [row[0]]
        for word in row[1:]:
            gap = word["x0"] - current[-1]["x1"]
            if gap > _SPLIT_GAP_EM * max(word["size"], current[-1]["size"]):
                runs.append(current)
                current = [word]
            else:
                current.append(word)
        runs.append(current)

    lines: list[_Line] = []
    for j, run in enumerate(runs):
        x0 = min(w["x0"] for w in run)
        x1 = max(w["x1"] for w in run)
        top = min(w["top"] for w in run)
        bottom = max(w["bottom"] for w in run)
        # Majority font by character count stands for the whole line.
        weights: dict[str, int] = {}
        for w in run:
            weights[w["fontname"]] = weights.get(w["fontname"], 0) + len(w["text"])
        font = max(weights, key=lambda k: weights[k])
        lines.append(
            _Line(
                segment_id=f"page{page_index}/line{j}",
                text=" ".join(w["text"] for w in run),
                x=round(x0, 2),
                y=round(top, 2),
                w=round(x1 - x0, 2),
                h=round(bottom - top, 2),
                size=round(max(w["size"] for w in run), 2),
                font=font,
            )
        )
    return lines


def layout(path: Path) -> dict[str, Any]:
    """Return the ``/layout`` payload (shared layout contract, ``textline`` only).

    Stub for viewer work; superseded by ``overnight/pdf-engine``. Segment ids
    are produced by the same routine :class:`Reader` uses, so they match the
    session's segments by construction.
    """
    pages: list[dict[str, Any]] = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages):
            items = [
                {
                    "kind": "textline",
                    "x": ln.x,
                    "y": ln.y,
                    "w": ln.w,
                    "h": ln.h,
                    "segment_id": ln.segment_id,
                    "text": ln.text,
                    "size": ln.size,
                    # Contract addition (optional): PDF base font name, so
                    # the viewer can pick a metric-compatible CSS family.
                    "font": ln.font,
                }
                for ln in _page_lines(page, i)
            ]
            pages.append(
                {
                    "index": i,
                    "width": float(page.width),
                    "height": float(page.height),
                    "items": items,
                }
            )
    return {"format": "pdf", "pages": pages, "unscanned": []}


class Reader:
    """Extract positioned lines with pdfplumber; one segment per line."""

    def read(self, path: Path) -> StructuredDocument:
        segments: list[TextSegment] = []
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages):
                for ln in _page_lines(page, i):
                    segments.append(
                        build_segment(
                            ln.segment_id,
                            ln.text,
                            page=i,
                            bbox=[ln.x, ln.y, ln.w, ln.h],
                        )
                    )

        if not any(seg.text.strip() for seg in segments):
            raise UnsupportedPdfError(
                f"{path} has no extractable text layer — "
                "Phase 1 cannot anonymize scanned/image-only PDFs."
            )

        # No raw handle: the source PDF is closed. The writer builds
        # a derivative document from scratch.
        return build_document(
            source_path=path,
            fmt="pdf",
            segments=segments,
            raw_handle=None,
        )


class Writer:
    """Emit a text-only derivative PDF with reportlab.

    Refuses to overwrite the source PDF (see module docstring). The
    output is explicitly a *new* document — callers should treat it
    as the anonymized counterpart, not a redaction of the original.
    """

    def write(self, doc: StructuredDocument, path: Path) -> None:
        if path.resolve() == doc.source_path.resolve():
            raise PdfWriteRefusedError(
                f"Refusing to overwrite source PDF at {path}. "
                "Phase 1 emits derivative PDFs only — choose a different output path."
            )
        self._render(doc, path)

    @staticmethod
    def _render(doc: StructuredDocument, path: Path) -> None:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
        )

        styles = getSampleStyleSheet()
        body_style = styles["BodyText"]

        # Segments are ``page{i}/line{j}`` (or bare ``page{i}``); group the
        # lines of each page back together so one page stays one page.
        pages: dict[str, list[str]] = {}
        for segment in doc.segments:
            pages.setdefault(segment.id.split("/", 1)[0], []).append(segment.text)

        doc_template = SimpleDocTemplate(str(path), pagesize=letter)
        flowables = []
        for idx, texts in enumerate(pages.values()):
            # reportlab's Paragraph treats ``<`` and ``&`` as markup —
            # escape them so anonymized placeholders like ``<PERSON>``
            # render literally.
            for text in texts:
                safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                for chunk in safe.split("\n"):
                    if chunk:
                        flowables.append(Paragraph(chunk, body_style))
                    else:
                        flowables.append(Spacer(1, 6))
            if idx < len(pages) - 1:
                flowables.append(PageBreak())

        doc_template.build(flowables)
