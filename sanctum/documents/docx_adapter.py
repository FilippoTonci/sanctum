"""Word (.docx) structured reader/writer backed by python-docx.

Segment granularity is the *run* — the atomic unit that owns its own
formatting (bold, italic, color, font). Run-level round-tripping keeps
every visual property intact; the only cost is that entities which
straddle two runs (e.g. ``Alice`` bolded, trailing ``Smith`` not) won't
be detected.

Segment IDs:
    body/p{i}/r{j}                                    body paragraph run
    table/t{t}/row{r}/cell{c}/p{p}/r{j}               table cell run

Phase 1 does not read headers, footers or footnotes — they are
preserved in the raw handle but not anonymized. This is an explicit
scope cut; lifting it is a Phase 2 task. Headers and footers *are* part
of :meth:`Writer.extract_text`, so the post-write leak check sees them.

The Writer strips hidden identifying data before saving: the text core
properties (author, last modified by, ...), Company/Manager in the app
properties, every comment and the thumbnail (see ``_ooxml_scrub``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from docx import Document

from sanctum.core.blocks import block_texts
from sanctum.documents._ooxml_scrub import scrub_package
from sanctum.documents.structured import build_document, build_segment, run_block

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from docx.section import _Footer, _Header
    from docx.text.paragraph import Paragraph
    from docx.text.run import Run

    from sanctum.core.models import StructuredDocument, TextSegment


def _iter_paragraph_runs(paragraph: Paragraph, prefix: str) -> list[tuple[str, Run]]:
    """Return ``[(segment_id, run), ...]`` for every run in ``paragraph``."""
    return [(f"{prefix}/r{j}", run) for j, run in enumerate(paragraph.runs)]


def _header_footer_lines(handle: DocxDocument) -> list[str]:
    """Paragraph texts of every header and footer, including their tables."""
    lines: list[str] = []
    for section in handle.sections:
        parts: tuple[_Header | _Footer, ...] = (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        )
        for part in parts:
            if part.is_linked_to_previous:
                continue  # no definition of its own; reading would create one
            lines.extend(p.text for p in part.paragraphs)
            for table in part.tables:
                for row in table.rows:
                    for cell in row.cells:
                        lines.extend(p.text for p in cell.paragraphs)
    return lines


class Reader:
    """Read a .docx file into a StructuredDocument keyed by run."""

    def read(self, path: Path) -> StructuredDocument:
        doc: DocxDocument = Document(str(path))
        segments: list[TextSegment] = []

        for i, para in enumerate(doc.paragraphs):
            for seg_id, run in _iter_paragraph_runs(para, f"body/p{i}"):
                segments.append(build_segment(seg_id, run.text, block=run_block(seg_id)))

        for t, table in enumerate(doc.tables):
            for r, row in enumerate(table.rows):
                for c, cell in enumerate(row.cells):
                    for p, para in enumerate(cell.paragraphs):
                        prefix = f"table/t{t}/row{r}/cell{c}/p{p}"
                        for seg_id, run in _iter_paragraph_runs(para, prefix):
                            segments.append(
                                build_segment(seg_id, run.text, block=run_block(seg_id))
                            )

        return build_document(
            source_path=path,
            fmt="docx",
            segments=segments,
            raw_handle=doc,
        )


class Writer:
    """Project a mutated StructuredDocument back to a .docx file.

    Mutates the raw python-docx handle in place by matching segment IDs
    back to their owning runs, then saves to ``path``. A missing
    ``raw_handle`` is a programmer error — the document must come from
    the matching :class:`Reader`.
    """

    def write(self, doc: StructuredDocument, path: Path) -> None:
        handle: DocxDocument | None = doc.raw_handle
        if handle is None:
            raise ValueError(
                "DocxWriter requires the StructuredDocument.raw_handle from DocxReader"
            )

        run_index = self._build_run_index(handle)
        for segment in doc.segments:
            run = run_index.get(segment.id)
            if run is None:
                continue
            run.text = segment.text

        scrub_package(handle.part.package, handle.core_properties)
        handle.save(str(path))

    def extract_text(self, path: Path) -> str:
        """Every piece of text a reader of ``path`` could see, one paragraph per line.

        Runs of a paragraph are joined the way detection joins them, so a
        name split across runs reads back whole. Headers and footers are
        included even though the Reader does not anonymize them yet.
        """
        doc = Reader().read(path)
        lines = block_texts(doc.segments)
        lines.extend(_header_footer_lines(doc.raw_handle))
        return "\n".join(lines)

    @staticmethod
    def _build_run_index(handle: DocxDocument) -> dict[str, Run]:
        """Rebuild the same id → run map the reader produced."""
        index: dict[str, Run] = {}

        for i, para in enumerate(handle.paragraphs):
            for seg_id, run in _iter_paragraph_runs(para, f"body/p{i}"):
                index[seg_id] = run

        for t, table in enumerate(handle.tables):
            for r, row in enumerate(table.rows):
                for c, cell in enumerate(row.cells):
                    for p, para in enumerate(cell.paragraphs):
                        prefix = f"table/t{t}/row{r}/cell{c}/p{p}"
                        for seg_id, run in _iter_paragraph_runs(para, prefix):
                            index[seg_id] = run

        return index
