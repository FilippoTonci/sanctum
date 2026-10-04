"""Redacted PDF output by flattening affected pages.

Pipeline (see ``Writer`` in :mod:`sanctum.documents.pdf_adapter`):

1. :func:`plan_edits` turns mutated segments into per-page span edits
   (display-space boxes plus replacement text).
2. :func:`flatten` rasterizes every page that has an edit with pypdfium2
   (annotations and form widgets are *not* drawn), paints each edited
   span with the surrounding background colour, and rebuilds the page as
   an image page with reportlab. The replacement text is drawn on top as
   real (visible, selectable) text, and the *unchanged* text of every
   line on the page is laid down as an invisible text layer so the page
   stays searchable. Pages without edits are copied as-is (vector).
3. :func:`sanitize` strips document info, XMP, annotations, form fields,
   embedded files, page thumbnails and page-level actions.

Nothing here uses PyMuPDF/fitz (AGPL) — pypdfium2 is Apache-2.0/BSD.
"""

from __future__ import annotations

import difflib
import io
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject

from sanctum.core.exceptions import DocumentError
from sanctum.documents._pdf_extract import Box, PdfExtraction, PdfLine

# Raster resolution for flattened pages. 200 dpi keeps 6 pt footnotes
# legible without making files balloon (see the WS3 report for sizes).
DEFAULT_DPI = 200
# Extra margin (points) painted around each redacted span so anti-aliased
# glyph edges and descenders are fully covered. Horizontal padding stays
# small so punctuation glued to the span (``Sanchez,``) survives.
_PAD_X = 0.4
_PAD_Y = 1.0
# Visible replacement text never shrinks below this fraction of the line's
# font size; beyond that it is compressed horizontally instead.
_MIN_FONT_FRACTION = 0.7
_FONT = "Helvetica"

# Page keys removed from every output page.
_PAGE_KEYS_TO_STRIP = ("/Annots", "/Metadata", "/PieceInfo", "/AA", "/B", "/Thumb")


@dataclass(frozen=True)
class SpanEdit:
    """One replaced span of one line."""

    line: PdfLine
    start: int
    end: int
    text: str

    @property
    def room(self) -> float:
        """Width the replacement may occupy: the span plus trailing blank space.

        A replacement is often longer than what it replaces (``Evelyn`` →
        ``<PERSON>``). It may run on into the whitespace after the span, up
        to 0.25 em short of the next glyph on the line, or 1 em past the end
        of the line (line splitting guarantees a gap of at least 1 em before
        any neighbouring column).
        """
        x1 = self.box[2]
        size = self.line.size
        nxt = self.end
        text = self.line.text
        while nxt < len(text) and text[nxt] == " ":
            nxt += 1
        if nxt >= len(text):
            limit = self.line.bbox[2] + size
        elif nxt == self.end:
            limit = x1
        else:
            limit = max(self.line.char_boxes[nxt][0] - 0.25 * size, x1)
        return max(limit - self.box[0], 1.0)

    @property
    def box(self) -> Box:
        boxes = self.line.char_boxes[self.start : self.end] or [self.line.bbox]
        return (
            min(b[0] for b in boxes),
            self.line.bbox[1],
            max(b[2] for b in boxes),
            self.line.bbox[3],
        )


# ----- 1. planning -----------------------------------------------------------


def plan_edits(segments: Iterable[Any], extraction: PdfExtraction) -> dict[int, list[SpanEdit]]:
    """Map mutated segments to span edits, grouped by page index.

    Uses the exact spans the engine recorded in
    ``segment.metadata["replacements"]`` when they are consistent with the
    line; otherwise falls back to a character diff; and if even that does
    not reproduce the new text, replaces the whole line (fail safe: over-
    redact rather than leave original glyphs behind).
    """
    lines = extraction.lines_by_id()
    edits: dict[int, list[SpanEdit]] = {}
    for seg in segments:
        line = lines.get(seg.id)
        if line is None:
            raise DocumentError(f"segment {seg.id!r} is not a line of this PDF; refusing to write")
        if seg.text == line.text:
            continue
        spans = _spans_from_metadata(line, seg) or _spans_from_diff(line.text, seg.text)
        if _apply(line.text, spans) != seg.text:
            spans = [(0, len(line.text), seg.text)]
        edits.setdefault(line.page, []).extend(
            SpanEdit(line=line, start=s, end=e, text=t) for s, e, t in spans
        )
    return edits


def _spans_from_metadata(line: PdfLine, seg: Any) -> list[tuple[int, int, str]] | None:
    raw = (getattr(seg, "metadata", None) or {}).get("replacements")
    if not raw:
        return None
    spans: list[tuple[int, int, str]] = []
    for r in raw:
        start, end, text = int(r["start"]), int(r["end"]), str(r["text"])
        original = r.get("original")
        if not (0 <= start <= end <= len(line.text)):
            return None
        if original is not None and line.text[start:end] != original:
            return None
        spans.append((start, end, text))
    return spans


def _spans_from_diff(old: str, new: str) -> list[tuple[int, int, str]]:
    """Diff-based fallback, coalescing edits separated by <3 equal chars."""
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    spans: list[list[Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if spans and i1 - spans[-1][1] < 3:
            prev = spans[-1]
            prev[2] = prev[2] + new[prev[4] : j2]
            prev[1], prev[4] = i2, j2
        else:
            spans.append([i1, i2, new[j1:j2], j1, j2])
    return [(s[0], s[1], s[2]) for s in spans]


def _apply(text: str, spans: list[tuple[int, int, str]]) -> str:
    for start, end, repl in sorted(spans, key=lambda s: s[0], reverse=True):
        text = text[:start] + repl + text[end:]
    return text


# ----- 2. flattening ---------------------------------------------------------


def flatten(
    extraction: PdfExtraction,
    edits: dict[int, list[SpanEdit]],
    dpi: int = DEFAULT_DPI,
) -> bytes:
    """Return the full output PDF (unsanitized) with edited pages flattened."""
    import pypdfium2 as pdfium
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    source = PdfReader(io.BytesIO(extraction.source_bytes))
    if not edits:
        return _copy_pages(source, {})

    scale = dpi / 72.0
    flat_buf = io.BytesIO()
    c = rl_canvas.Canvas(flat_buf, pageCompression=1)
    c.setAuthor("")
    c.setTitle("")
    c.setProducer("")
    c.setCreator("")
    flat_order: list[int] = sorted(edits)
    lines_by_page: dict[int, list[PdfLine]] = {}
    for line in extraction.lines:
        lines_by_page.setdefault(line.page, []).append(line)

    pdf = pdfium.PdfDocument(extraction.source_bytes)
    try:
        for page_index in flat_order:
            geom = extraction.pages[page_index]
            page = pdf[page_index]
            bitmap = page.render(scale=scale, may_draw_forms=False, draw_annots=False)
            image = bitmap.to_pil().convert("RGB")
            page.close()

            page_edits = edits[page_index]
            fills = _paint(image, page_edits, scale)
            if _is_grayscale(image):
                image = image.convert("L")

            c.setPageSize((geom.width, geom.height))
            c.drawImage(ImageReader(image), 0, 0, geom.width, geom.height)
            _draw_invisible_text(c, lines_by_page.get(page_index, []), page_edits, geom.height)
            for edit, fill in zip(page_edits, fills, strict=True):
                _draw_replacement(c, edit, fill, geom.height)
            c.showPage()
    finally:
        pdf.close()
    c.save()

    flat = PdfReader(io.BytesIO(flat_buf.getvalue()))
    replacements = {idx: flat.pages[k] for k, idx in enumerate(flat_order)}
    return _copy_pages(source, replacements)


def _copy_pages(source: PdfReader, replacements: dict[int, Any]) -> bytes:
    """Source pages in order, with flattened pages swapped in.

    Flattened pages are rendered in display space (rotation applied), so
    they carry no ``/Rotate`` of their own.
    """
    writer = PdfWriter()
    for i, page in enumerate(source.pages):
        new = replacements.get(i, page)
        _strip_page(new)
        writer.add_page(new)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _px(box: Box, scale: float) -> tuple[int, int, int, int]:
    x0, top, x1, bottom = box
    return (
        int((x0 - _PAD_X) * scale),
        int((top - _PAD_Y) * scale),
        int((x1 + _PAD_X) * scale + 0.999),
        int((bottom + _PAD_Y) * scale + 0.999),
    )


def _paint(image: Any, edits: list[SpanEdit], scale: float) -> list[tuple[int, int, int]]:
    """Paint each span with the dominant colour of a ring around it.

    All backgrounds are sampled before any painting so neighbouring
    spans do not sample each other's paint. Returns the fills used.
    """
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    fills = [_background(image, _px(e.box, scale)) for e in edits]
    for edit, fill in zip(edits, fills, strict=True):
        draw.rectangle(_px(edit.box, scale), fill=fill)
    return fills


def _background(image: Any, rect: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """Most common colour on a 2 px ring just outside ``rect``."""
    w, h = image.size
    x0, y0, x1, y1 = rect
    ring = (max(x0 - 3, 0), max(y0 - 3, 0), min(x1 + 3, w), min(y1 + 3, h))
    region = image.crop(ring)
    rw, rh = region.size
    pixels = region.load()
    counts: dict[tuple[int, int, int], int] = {}
    for x in range(rw):
        for y in range(rh):
            inside = 3 <= x < rw - 3 and 3 <= y < rh - 3
            if inside:
                continue
            px = pixels[x, y]
            counts[px] = counts.get(px, 0) + 1
    if not counts:
        return (255, 255, 255)
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _is_grayscale(image: Any) -> bool:
    from PIL import ImageChops

    r, g, b = image.split()
    return (
        ImageChops.difference(r, g).getbbox() is None
        and ImageChops.difference(g, b).getbbox() is None
    )


def _baseline(line_box: Box) -> float:
    """Approximate baseline (top-origin y) from a line's box."""
    top, bottom = line_box[1], line_box[3]
    return bottom - 0.21 * (bottom - top)


def _draw_replacement(c: Any, edit: SpanEdit, bg: tuple[int, int, int], page_h: float) -> None:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    if not edit.text:
        return
    x0 = edit.box[0]
    width = edit.room
    size = edit.line.size
    natural = stringWidth(edit.text, _FONT, size)
    horiz = 100.0
    if natural > width:
        size = max(size * width / natural, edit.line.size * _MIN_FONT_FRACTION)
        fitted = stringWidth(edit.text, _FONT, size)
        if fitted > width:
            horiz = 100.0 * width / fitted
    luminance = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    fill = 0.0 if luminance > 128 else 1.0

    t = c.beginText()
    t.setTextRenderMode(0)
    t.setFont(_FONT, size)
    t.setHorizScale(horiz)
    t.setFillGray(fill)
    t.setTextOrigin(x0, page_h - _baseline(edit.line.bbox))
    t.textOut(edit.text)
    # Text render mode is graphics state, not text-object state: isolate
    # it so the invisible layer's Tr 3 can never hide a replacement.
    c.saveState()
    c.drawText(t)
    c.restoreState()


def _draw_invisible_text(
    c: Any, lines: list[PdfLine], edits: list[SpanEdit], page_h: float
) -> None:
    """Lay the *unedited* text of each line down as invisible text (Tr 3).

    Edited spans are skipped entirely — they exist only as the visible
    replacement text — so no original string of a replaced span reaches
    the output's text layer.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    cut: dict[str, list[tuple[int, int]]] = {}
    for e in edits:
        cut.setdefault(e.line.segment_id, []).append((e.start, e.end))
    for line in lines:
        removed = sorted(cut.get(line.segment_id, []))
        for start, end in _kept_runs(len(line.text), removed):
            piece = line.text[start:end]
            if not piece.strip():
                continue
            boxes = line.char_boxes[start:end]
            x0, x1 = boxes[0][0], boxes[-1][2]
            natural = stringWidth(piece, _FONT, line.size) or 1.0
            t = c.beginText()
            t.setTextRenderMode(3)
            t.setFont(_FONT, line.size)
            t.setHorizScale(max(100.0 * (x1 - x0) / natural, 1.0))
            t.setTextOrigin(x0, page_h - _baseline(line.bbox))
            t.textOut(piece)
            c.saveState()
            c.drawText(t)
            c.restoreState()


def _kept_runs(length: int, removed: list[tuple[int, int]]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    cursor = 0
    for start, end in removed:
        if start > cursor:
            runs.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < length:
        runs.append((cursor, length))
    return runs


# ----- 3. sanitizing ---------------------------------------------------------


def _strip_page(page: Any) -> None:
    for key in _PAGE_KEYS_TO_STRIP:
        if key in page:
            del page[key]
    resources = page.get("/Resources")
    if resources is not None:
        _strip_xobject_metadata(resources.get_object(), set())


def _strip_xobject_metadata(resources: Any, seen: set[int]) -> None:
    xobjects = resources.get("/XObject") if isinstance(resources, DictionaryObject) else None
    if xobjects is None:
        return
    xobjects = xobjects.get_object()
    for name in list(xobjects.keys()):
        ref = xobjects.raw_get(name)
        key = id(ref.get_object())
        if key in seen:
            continue
        seen.add(key)
        xobj = ref.get_object()
        for k in ("/Metadata", "/PieceInfo"):
            if k in xobj:
                del xobj[k]
        nested = xobj.get("/Resources")
        if nested is not None:
            _strip_xobject_metadata(nested.get_object(), seen)


def sanitize(pdf_bytes: bytes) -> bytes:
    """Rebuild the PDF from its pages only, dropping document-level extras.

    A fresh writer copies nothing from the source catalog, so /Names
    (embedded files, JavaScript), /AcroForm, /OpenAction, /Outlines,
    /Metadata (XMP) and the /Info dictionary are all left behind. Page-level
    annotations, XMP and actions are removed by :func:`_strip_page`.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        _strip_page(page)
        writer.add_page(page)
    # pypdf always stamps /Producer; drop the whole Info dictionary.
    writer.metadata = None
    try:
        writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
    except TypeError:  # pypdf < 6 spells the flags differently
        writer.compress_identical_objects(remove_identicals=False, remove_orphans=True)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
