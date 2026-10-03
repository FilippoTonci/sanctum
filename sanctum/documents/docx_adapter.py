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

Any ``.../p{p}`` above may end in ``/n{k}`` instead of ``/r{j}``: a run
nested in a ``w:hyperlink``, tracked insertion (``w:ins``/``w:moveTo``),
inline content control (``w:sdt``), ``w:smartTag``, ``w:customXml`` or
``w:fldSimple``. ``r{j}`` counts only the paragraph's direct runs (what
``Paragraph.runs`` returns), so adding nested runs never renumbers them;
``n{k}`` counts the nested runs. Paragraphs inside a block-level content
control get ``.../sdt{k}/p{i}`` in place of ``.../p{i}`` (``k`` indexes
the container's direct ``w:sdt`` children, ``i`` the paragraphs inside it,
nested controls flattened), listed after the container's own paragraphs
and tables. Runs inside a tracked deletion are not read: the output
accepts every tracked change.

``{kind}`` is one of ``header``, ``first_page_header``, ``even_page_header``,
``footer``, ``first_page_footer``, ``even_page_footer``; a header/footer
linked to the previous section has no content of its own and is skipped.
Reader, Writer and ``extract_text`` all walk :func:`_iter_document_runs`,
so the ids the Writer patches are the ids the Reader produced.

Not read: footnotes, endnotes, text boxes, tables inside content
controls. They are preserved in the raw handle but not anonymized; the
leak check still sees them, because ``extract_text`` reads every text node
of every ``word/*.xml`` part on its own rather than through the Reader.

The Writer strips hidden identifying data before saving: the text core
properties (author, last modified by, ...), Company/Manager in the app
properties, every comment, the thumbnail and every tracked change, which
it accepts (see ``_ooxml_scrub``). A hyperlink whose text was redacted
loses its target, which usually repeats that text (``mailto:``).
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from lxml import etree  # type: ignore[import-untyped]  # python-docx dependency

from sanctum.documents._ooxml_scrub import accept_tracked_changes, scrub_package
from sanctum.documents.structured import build_document, build_segment, run_block

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from docx.section import _Footer, _Header

    from sanctum.core.models import StructuredDocument, TextSegment


_W_P = qn("w:p")
_W_R = qn("w:r")
_W_SDT = qn("w:sdt")
_W_SDT_CONTENT = qn("w:sdtContent")
# Inline wrappers whose runs are live paragraph text. Not w:del / w:moveFrom:
# deleted text is dropped when the writer accepts tracked changes.
_TRANSPARENT = frozenset(
    qn(f"w:{tag}")
    for tag in (
        "hyperlink",
        "ins",
        "moveTo",
        "sdt",
        "sdtContent",
        "smartTag",
        "customXml",
        "fldSimple",
    )
)


def _iter_paragraph_runs(paragraph: Paragraph, prefix: str) -> list[tuple[str, Run]]:
    """Return ``[(segment_id, run), ...]`` for every live run in ``paragraph``, in order.

    Direct runs are ``r{j}`` (the index ``Paragraph.runs`` gives them); runs
    nested in a transparent wrapper are ``n{k}``.
    """
    p = paragraph._p
    out: list[tuple[str, Run]] = []
    direct = nested = 0
    for r in p.iter(_W_R):
        parent = r.getparent()
        if parent is p:
            out.append((f"{prefix}/r{direct}", Run(r, paragraph)))
            direct += 1
            continue
        while parent is not p and parent.tag in _TRANSPARENT:
            parent = parent.getparent()
        if parent is p:
            out.append((f"{prefix}/n{nested}", Run(r, paragraph)))
            nested += 1
    return out


def _sdt_paragraphs(sdt: Any) -> Iterator[Any]:
    """``w:p`` elements of a block-level content control, nested controls flattened."""
    for content in sdt.iterchildren(_W_SDT_CONTENT):
        for child in content:
            if child.tag == _W_P:
                yield child
            elif child.tag == _W_SDT:
                yield from _sdt_paragraphs(child)


def _iter_block_sdt_runs(element: Any, parent: Any, prefix: str) -> Iterator[tuple[str, Run]]:
    """Runs of paragraphs inside ``element``'s direct block-level ``w:sdt`` children."""
    for k, sdt in enumerate(element.iterchildren(_W_SDT)):
        for i, p in enumerate(_sdt_paragraphs(sdt)):
            yield from _iter_paragraph_runs(Paragraph(p, parent), f"{prefix}sdt{k}/p{i}")


def _iter_container_runs(
    container: Any, para_prefix: str, table_prefix: str
) -> Iterator[tuple[str, Run]]:
    """Runs of a container's paragraphs, then of its tables' cell paragraphs.

    Paragraphs inside block-level content controls come last (after the
    tables, and after each cell's own paragraphs) so the ids and order of
    everything else are what they were before content controls were read.
    """
    for i, para in enumerate(container.paragraphs):
        yield from _iter_paragraph_runs(para, f"{para_prefix}p{i}")
    for t, table in enumerate(container.tables):
        for r, row in enumerate(table.rows):
            for c, cell in enumerate(row.cells):
                cell_prefix = f"{table_prefix}t{t}/row{r}/cell{c}/"
                for p, para in enumerate(cell.paragraphs):
                    yield from _iter_paragraph_runs(para, f"{cell_prefix}p{p}")
                yield from _iter_block_sdt_runs(cell._tc, cell, cell_prefix)
    yield from _iter_block_sdt_runs(_block_element(container), container, para_prefix)


def _block_element(container: Any) -> Any:
    """The XML element whose children are the container's paragraphs and tables."""
    body = getattr(getattr(container, "element", None), "body", None)
    return body if body is not None else container._element


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
            if run is None or run.text == segment.text:
                # Unchanged: leave it alone. Setting ``run.text`` rewrites the
                # run's content and would drop an inline picture or field.
                continue
            run.text = segment.text
            _unlink_hyperlink(run)

        accept_tracked_changes(handle.part.package)
        scrub_package(handle.part.package, handle.core_properties)
        handle.save(str(path))

    def extract_text(self, path: Path) -> str:
        """Every text node of every ``word/*.xml`` part, plus external link targets.

        Deliberately independent of the Reader (final-review C2): text the
        Reader does not read (footnotes, text boxes, a wrapper it does not
        know, deleted text that survived) must still be leak-checked. One
        paragraph per line, its ``w:t`` / ``w:delText`` joined with no
        separator the way detection joins runs, so a name split across runs
        reads back whole. Text of a nested paragraph (a text box) is its own
        line. Relationship targets of ``word/`` parts (a ``mailto:`` link)
        follow, one per line.
        """
        lines: list[str] = []
        with zipfile.ZipFile(path) as z:
            for name in sorted(z.namelist()):
                if not name.startswith("word/"):
                    continue
                if name.endswith(".xml"):
                    lines.extend(_paragraph_texts(z.read(name)))
                elif name.endswith(".rels"):
                    lines.extend(_external_targets(z.read(name)))
        return "\n".join(lines)

    @staticmethod
    def _build_run_index(handle: DocxDocument) -> dict[str, Run]:
        """Rebuild the same id → run map the reader produced."""
        return dict(_iter_document_runs(handle))


_W_HYPERLINK = qn("w:hyperlink")
_R_ID = qn("r:id")


def _unlink_hyperlink(run: Run) -> None:
    """Drop the target of the hyperlink holding ``run``, whose text was just redacted.

    A link's target usually repeats its text (``mailto:jane@...``); the
    relationship would carry the original past the redaction. The link
    text stays, as plain text.
    """
    link = next((a for a in run._r.iterancestors(_W_HYPERLINK) if a.get(_R_ID) is not None), None)
    if link is None:
        return
    rid = link.attrib.pop(_R_ID)
    run.part.drop_rel(rid)  # dropped only when nothing else in the part refers to it


_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
_TEXT_TAGS = {qn("w:t"): None, qn("w:delText"): None}
_RUN_CHARS = {qn("w:tab"): "\t", qn("w:ptab"): "\t", qn("w:br"): "\n", qn("w:cr"): "\n"}
_RUN_CHARS[qn("w:noBreakHyphen")] = "-"


def _paragraph_texts(xml: bytes) -> list[str]:
    """The text of every ``w:p`` in one part, nested paragraphs separately."""
    root = etree.fromstring(xml, _PARSER)
    texts: list[str] = []
    for p in root.iter(_W_P):
        parts: list[str] = []
        for el in p.iter(*_TEXT_TAGS, *_RUN_CHARS):
            if next(el.iterancestors(_W_P)) is not p:
                continue  # belongs to a nested paragraph (text box), read on its own
            parts.append(el.text or "" if el.tag in _TEXT_TAGS else _RUN_CHARS[el.tag])
        if parts:
            texts.append("".join(parts))
    return texts


_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"


def _external_targets(xml: bytes) -> list[str]:
    root = etree.fromstring(xml, _PARSER)
    return [rel.get("Target", "") for rel in root.iter(_REL) if rel.get("TargetMode") == "External"]
