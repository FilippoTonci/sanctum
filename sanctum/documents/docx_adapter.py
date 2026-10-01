"""Word (.docx) structured reader/writer backed by python-docx.

Segment granularity is the *run* — the atomic unit that owns its own
formatting (bold, italic, color, font). Run-level round-tripping keeps
every visual property intact; the only cost is that entities which
straddle two runs (e.g. ``Alice`` bolded, trailing ``Smith`` not) won't
be detected.

Segment IDs:
    body/p{i}/r{j}                                    body paragraph run
    table/t{t}/row{r}/cell{c}/p{p}/r{j}               table cell run
    hf/s{s}/{kind}/p{p}/r{j}                          header/footer run
    hf/s{s}/{kind}/t{t}/row{r}/cell{c}/p{p}/r{j}      header/footer table cell run

``{kind}`` is one of ``header``, ``first_page_header``, ``even_page_header``,
``footer``, ``first_page_footer``, ``even_page_footer``; a header/footer
linked to the previous section has no content of its own and is skipped.
Reader, Writer and ``extract_text`` all walk :func:`_iter_document_runs`,
so the ids the Writer patches are the ids the Reader produced.

Not read: footnotes, endnotes, text boxes. They are preserved in the raw
handle but not anonymized.

The Writer strips hidden identifying data before saving: the text core
properties (author, last modified by, ...), Company/Manager in the app
properties, every comment and the thumbnail (see ``_ooxml_scrub``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

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


def _iter_container_runs(
    container: Any, para_prefix: str, table_prefix: str
) -> Iterator[tuple[str, Run]]:
    """Runs of a container's paragraphs, then of its tables' cell paragraphs."""
    for i, para in enumerate(container.paragraphs):
        yield from _iter_paragraph_runs(para, f"{para_prefix}p{i}")
    for t, table in enumerate(container.tables):
        for r, row in enumerate(table.rows):
            for c, cell in enumerate(row.cells):
                for p, para in enumerate(cell.paragraphs):
                    yield from _iter_paragraph_runs(para, f"{table_prefix}t{t}/row{r}/cell{c}/p{p}")


_HEADER_FOOTER_KINDS = (
    "header",
    "first_page_header",
    "even_page_header",
    "footer",
    "first_page_footer",
    "even_page_footer",
)


def _iter_header_footers(handle: DocxDocument) -> Iterator[tuple[str, _Header | _Footer]]:
    """``(id_prefix, header_or_footer)`` for every header/footer with its own content."""
    for s, section in enumerate(handle.sections):
        for kind in _HEADER_FOOTER_KINDS:
            part: _Header | _Footer = getattr(section, kind)
            if part.is_linked_to_previous:
                continue  # no definition of its own; reading it would create one
            yield f"hf/s{s}/{kind}", part


def _iter_document_runs(handle: DocxDocument) -> Iterator[tuple[str, Run]]:
    """Every run the adapter reads and writes, in document order, with its segment id."""
    yield from _iter_container_runs(handle, "body/", "table/")
    for prefix, part in _iter_header_footers(handle):
        yield from _iter_container_runs(part, f"{prefix}/", f"{prefix}/")


class Reader:
    """Read a .docx file into a StructuredDocument keyed by run."""

    def read(self, path: Path) -> StructuredDocument:
        doc: DocxDocument = Document(str(path))
        segments: list[TextSegment] = [
            build_segment(seg_id, run.text, block=run_block(seg_id))
            for seg_id, run in _iter_document_runs(doc)
        ]
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
        """Every piece of text the Reader sees in ``path`` (body, tables, headers, footers).

        One paragraph per line; runs of a paragraph are joined the way
        detection joins them, so a name split across runs reads back whole.
        """
        return "\n".join(block_texts(Reader().read(path).segments))

    @staticmethod
    def _build_run_index(handle: DocxDocument) -> dict[str, Run]:
        """Rebuild the same id → run map the reader produced."""
        return dict(_iter_document_runs(handle))
