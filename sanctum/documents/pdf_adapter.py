"""PDF structured reader/derivative writer.

Phase 1 approach:

* **Read**: pdfplumber extracts text page-by-page. Each page becomes
  one segment (``page{i}``). We intentionally do *not* split into
  lines — reconstructing line-level layout on the writer side would
  require proper positional rewriting, which is a Phase 3 concern
  (`burn-in` redaction on the original raster).
* **Write**: reportlab emits a *new* text-only PDF. Overwriting the
  source is refused because the original's images, forms, and layout
  would silently disappear.
* **Scanned PDFs**: if every page returns no extractable text, we
  raise :class:`UnsupportedPdfError` — OCR is out of scope for
  Phase 1.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pdfplumber

from sanctum.core.exceptions import PdfWriteRefusedError, UnsupportedPdfError
from sanctum.documents.structured import build_document, build_segment

if TYPE_CHECKING:
    from sanctum.core.models import StructuredDocument, TextSegment


class Reader:
    """Extract page text with pdfplumber; one segment per page."""

    def read(self, path: Path) -> StructuredDocument:
        segments: list[TextSegment] = []
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                segments.append(build_segment(f"page{i}", text))

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

        doc_template = SimpleDocTemplate(str(path), pagesize=letter)
        flowables = []
        for idx, segment in enumerate(doc.segments):
            # reportlab's Paragraph treats ``<`` and ``&`` as markup —
            # escape them so anonymized placeholders like ``<PERSON>``
            # render literally.
            safe = segment.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            for chunk in safe.split("\n"):
                if chunk:
                    flowables.append(Paragraph(chunk, body_style))
                else:
                    flowables.append(Spacer(1, 6))
            if idx < len(doc.segments) - 1:
                flowables.append(PageBreak())

        doc_template.build(flowables)
