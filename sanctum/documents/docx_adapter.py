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
loses its target, which usually repeats that text (``mailto:``); a field
(``HYPERLINK "mailto:..."``) whose result was redacted becomes its plain
result text, without the field code; and picture/shape alt text
(``descr``/``title``/``name``, VML ``alt``/``o:title``) or a link tooltip
that names a replaced value is emptied.
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

from sanctum.core.leak_check import count_surviving_originals
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
        changed: list[Run] = []
        for segment in doc.segments:
            run = run_index.get(segment.id)
            if run is None or run.text == segment.text:
                # Unchanged: leave it alone. Setting ``run.text`` rewrites the
                # run's content and would drop an inline picture or field.
                continue
            run.text = segment.text
            changed.append(run)

        # A redacted run can sit behind hidden text that repeats it: a link
        # target, a field code (HYPERLINK "mailto:..."), a picture's alt text.
        for run in changed:
            _unlink_hyperlink(run)
            _unwrap_simple_field(run)
        _unwrap_complex_fields(changed)
        _blank_alt_text(handle.part.package, _replaced_originals(doc.segments))

        accept_tracked_changes(handle.part.package)
        scrub_package(handle.part.package, handle.core_properties)
        handle.save(str(path))

    def extract_text(self, path: Path) -> str:
        """Every piece of text in every ``word/*.xml`` part, plus external link targets.

        Deliberately independent of the Reader (final-review C2, NB1): text
        the Reader does not read must still be leak-checked. That is
        footnotes, text boxes, wrappers it does not know, deleted text that
        survived, field codes and picture alt text.

        One paragraph per line, its ``w:t`` / ``w:delText`` joined with no
        separator the way detection joins runs, so a name split across runs
        reads back whole; a nested paragraph (a text box) is its own line.
        Then, one per line: each paragraph's field code (``w:instrText`` /
        ``w:delInstrText`` joined), each ``w:fldSimple/@w:instr``, each
        hyperlink tooltip, the ``descr`` / ``title`` / ``name`` of every
        ``docPr`` / ``cNvPr``, every VML ``alt`` / ``o:title``, and the
        external relationship targets of the ``word/`` parts (a ``mailto:``
        link).
        """
        lines: list[str] = []
        with zipfile.ZipFile(path) as z:
            for name in sorted(z.namelist()):
                if not name.startswith("word/"):
                    continue
                if name.endswith(".xml"):
                    lines.extend(_part_texts(z.read(name)))
                elif name.endswith(".rels"):
                    lines.extend(_external_targets(z.read(name)))
        return "\n".join(lines)

    @staticmethod
    def _build_run_index(handle: DocxDocument) -> dict[str, Run]:
        """Rebuild the same id → run map the reader produced."""
        return dict(_iter_document_runs(handle))


_W_HYPERLINK = qn("w:hyperlink")
_W_TOOLTIP = qn("w:tooltip")
_R_ID = qn("r:id")
_W_FLD_SIMPLE = qn("w:fldSimple")
_W_INSTR = qn("w:instr")
_W_FLD_CHAR = qn("w:fldChar")
_W_FLD_CHAR_TYPE = qn("w:fldCharType")
_W_INSTR_TEXT = qn("w:instrText")
_W_DEL_INSTR_TEXT = qn("w:delInstrText")
_W_RPR = qn("w:rPr")
_ALT_ELEMENTS = ("{*}docPr", "{*}cNvPr")
_ALT_ATTRS = ("descr", "title", "name")
# Legacy VML pictures (w:pict): v:shape@alt, v:imagedata@o:title, on any v:* element.
_VML_ANY = "{urn:schemas-microsoft-com:vml}*"
_VML_ATTRS = ("alt", "{urn:schemas-microsoft-com:office:office}title")


def _hidden_attributes(root: Any) -> Iterator[tuple[Any, str]]:
    """``(element, attribute)`` for every attribute that can hide a name.

    Picture/shape alt text (DrawingML and VML) and hyperlink tooltips. Read
    by ``extract_text`` and blanked by the Writer when it names a replaced
    value, so the two always cover the same attributes.
    """
    for el in root.iter(*_ALT_ELEMENTS):
        for attr in _ALT_ATTRS:
            yield el, attr
    for el in root.iter(_VML_ANY):
        for attr in _VML_ATTRS:
            yield el, attr
    for el in root.iter(_W_HYPERLINK):
        yield el, _W_TOOLTIP


def _unlink_hyperlink(run: Run) -> None:
    """Drop the target of the hyperlink holding ``run``, whose text was just redacted.

    A link's target usually repeats its text (``mailto:jane@...``); the
    relationship would carry the original past the redaction. The link
    text stays, as plain text, and so does the relationship while another
    element of the part still uses it (the leak check then sees its target).
    """
    link = next((a for a in run._r.iterancestors(_W_HYPERLINK) if a.get(_R_ID) is not None), None)
    if link is None:
        return
    rid = link.attrib.pop(_R_ID)
    link.attrib.pop(_W_TOOLTIP, None)  # the screen tip often repeats the address too
    part = run.part
    still_used = rid in part.element.xpath("//@r:id")
    if not still_used and rid in part.rels:
        del part.rels[rid]


def _unwrap(element: Any) -> None:
    """Replace ``element`` by its children."""
    parent = element.getparent()
    if parent is None:
        return
    index = parent.index(element)
    for child in reversed(list(element)):
        parent.insert(index, child)
    parent.remove(element)


def _unwrap_simple_field(run: Run) -> None:
    """Turn a ``w:fldSimple`` whose result holds ``run`` into its plain result text."""
    for field in list(run._r.iterancestors(_W_FLD_SIMPLE)):
        _unwrap(field)


def _unwrap_complex_fields(changed: list[Run]) -> None:
    """Turn every complex field whose result holds a redacted run into plain result text.

    A complex field is ``fldChar begin``, the instruction (``w:instrText``),
    ``fldChar separate``, the result runs, ``fldChar end``; it can span
    paragraphs and nest. The field-code elements of a field (and of fields
    nested in it) are removed, and a run left with nothing but properties
    goes too, so only the result text remains.
    """
    changed_runs = {run._r for run in changed}
    roots = {id(root): root for root in (r.getroottree().getroot() for r in changed_runs)}
    for root in roots.values():
        doomed: list[Any] = []
        stack: list[dict[str, Any]] = []
        for r in root.iter(_W_R):
            for child in r:
                if child.tag == _W_FLD_CHAR:
                    kind = child.get(_W_FLD_CHAR_TYPE)
                    if kind == "begin":
                        stack.append({"codes": [child], "result": False, "redacted": False})
                    elif stack and kind == "separate":
                        stack[-1]["codes"].append(child)
                        stack[-1]["result"] = True
                    elif stack and kind == "end":
                        field = stack.pop()
                        field["codes"].append(child)
                        if field["redacted"]:
                            doomed.extend(field["codes"])
                        if stack:  # nested codes go with the enclosing field
                            stack[-1]["codes"].extend(field["codes"])
                elif stack and child.tag in (_W_INSTR_TEXT, _W_DEL_INSTR_TEXT):
                    stack[-1]["codes"].append(child)
            if r in changed_runs:
                for field in stack:
                    if field["result"]:
                        field["redacted"] = True
        for code in doomed:
            run = code.getparent()
            if run is None:
                continue
            run.remove(code)
            if run.getparent() is not None and all(c.tag == _W_RPR for c in run):
                run.getparent().remove(run)


def _replaced_originals(segments: list[TextSegment]) -> list[str]:
    """The values the leak check will look for (see ``engine._originals_to_verify``)."""
    out: list[str] = []
    for seg in segments:
        for rep in seg.metadata.get("replacements", []):
            value = rep.get("leak_original", rep.get("original"))
            out.extend(v for v in (value, *rep.get("leak_extra", [])) if v)
    return out


def _blank_alt_text(package: Any, originals: list[str]) -> None:
    """Empty every alt text or tooltip that names a replaced value.

    Covers DrawingML ``docPr``/``cNvPr`` ``descr``/``title``/``name``, VML
    ``alt`` / ``o:title`` and ``w:hyperlink@w:tooltip`` (any link, internal
    or external), using the leak check's matcher. The attribute is kept,
    empty, because ``name`` is required by the schema.
    """
    if not originals:
        return
    for part in package.iter_parts():
        element = getattr(part, "_element", None)
        if element is None:
            continue
        for el, attr in _hidden_attributes(element):
            value = el.get(attr)
            if value and count_surviving_originals(value, originals):
                el.set(attr, "")


_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
_TEXT_TAGS = {qn("w:t"): None, qn("w:delText"): None}
_RUN_CHARS = {qn("w:tab"): "\t", qn("w:ptab"): "\t", qn("w:br"): "\n", qn("w:cr"): "\n"}
_RUN_CHARS[qn("w:noBreakHyphen")] = "-"


def _part_texts(xml: bytes) -> list[str]:
    """Visible paragraph text, then field codes, tooltips and alt text, of one part."""
    root = etree.fromstring(xml, _PARSER)
    texts: list[str] = []
    codes: list[str] = []
    for p in root.iter(_W_P):
        parts: list[str] = []
        instr: list[str] = []
        for el in p.iter(*_TEXT_TAGS, *_RUN_CHARS, _W_INSTR_TEXT, _W_DEL_INSTR_TEXT):
            if next(el.iterancestors(_W_P)) is not p:
                continue  # belongs to a nested paragraph (text box), read on its own
            if el.tag in (_W_INSTR_TEXT, _W_DEL_INSTR_TEXT):
                instr.append(el.text or "")
            else:
                parts.append(el.text or "" if el.tag in _TEXT_TAGS else _RUN_CHARS[el.tag])
        if parts:
            texts.append("".join(parts))
        if instr:
            codes.append("".join(instr))
    codes.extend(el.get(_W_INSTR, "") for el in root.iter(_W_FLD_SIMPLE))
    codes.extend(el.get(attr, "") for el, attr in _hidden_attributes(root))
    return texts + [c for c in codes if c]


_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"


def _external_targets(xml: bytes) -> list[str]:
    root = etree.fromstring(xml, _PARSER)
    return [rel.get("Target", "") for rel in root.iter(_REL) if rel.get("TargetMode") == "External"]
