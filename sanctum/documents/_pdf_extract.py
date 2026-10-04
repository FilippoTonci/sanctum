"""Positioned line extraction for PDFs.

Turns a PDF into one :class:`PdfLine` per extracted text line, each with
a per-character box so the writer can paint over an exact span.

Coordinate space
----------------
Everything is in **display space**: points, top-left origin, relative to
the page's CropBox, *after* the page's ``/Rotate`` is applied — i.e. what
a viewer shows. It is the space of the layout contract, of PDF.js's
rendering, and of ``pypdfium2``'s default rendering, so the writer paints
boxes on the raster with no transforms.

pdfplumber gets CropBoxes and rotated pages with a non-zero box origin
wrong (it measures from the MediaBox and mis-rotates offsets), so we
extract from a normalized copy where each page's content is translated so
the CropBox starts at (0, 0) and ``MediaBox = CropBox = [0 0 w h]``;
``/Rotate`` is kept. Text that is upright on screen is scanned, including
landscape pages stored as rotated portrait pages; text that is vertical on
screen is reported as unscanned.
"""

from __future__ import annotations

import io
import itertools
import statistics
from dataclasses import dataclass, field
from typing import Any

import pdfplumber
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

Box = tuple[float, float, float, float]  # x0, top, x1, bottom

# pdfplumber keeps ligature glyphs as single characters when
# ``expand_ligatures=False``; we expand them ourselves so every code point
# of the segment text keeps a box (split evenly across the glyph).
_LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "ft",
    "ﬆ": "st",
}

# A horizontal gap wider than this many font sizes splits a visual row into
# separate lines (column gutters, table cells). Normal word spacing is
# ~0.25-0.35 em; loose justified text rarely exceeds ~0.8 em.
_LINE_SPLIT_GAP_EM = 1.0
# Character gap (as a fraction of font size) above which pdfplumber starts
# a new word. Scales with the font so 6 pt footnotes still split words.
_WORD_GAP_RATIO = 0.15


@dataclass
class PdfLine:
    """One extracted text line, in display space (see module docstring)."""

    segment_id: str
    page: int
    text: str
    char_boxes: list[Box]
    bbox: Box
    size: float
    block: str = ""  # paragraph key, ``page{i}/para{k}`` (set by _assign_blocks)


@dataclass
class PdfPageGeom:
    """Per-page geometry in display space (CropBox, after ``/Rotate``)."""

    index: int
    width: float
    height: float
    rotation: int  # 0 / 90 / 180 / 270, clockwise, as in /Rotate (informational)


@dataclass
class PdfExtraction:
    """Everything the reader learns about a PDF, kept as the raw handle."""

    source_bytes: bytes
    pages: list[PdfPageGeom]
    lines: list[PdfLine]
    unscanned: list[dict[str, str]] = field(default_factory=list)

    def lines_by_id(self) -> dict[str, PdfLine]:
        return {line.segment_id: line for line in self.lines}


def normalized_copy(source_bytes: bytes) -> tuple[bytes, list[int]]:
    """Return the PDF with each page's CropBox moved to the origin.

    Also returns the per-page rotations (normalized to 0..270).
    """
    reader = PdfReader(io.BytesIO(source_bytes))
    writer = PdfWriter()
    rotations: list[int] = []
    for page in reader.pages:
        rotations.append(int(page.get("/Rotate", 0) or 0) % 360)
        writer.add_page(page)
    for page in writer.pages:
        cb = page.cropbox
        x0, y0 = float(cb.left), float(cb.bottom)
        w, h = float(cb.right) - x0, float(cb.top) - y0
        if x0 or y0:
            page.add_transformation(Transformation().translate(-x0, -y0))
        box = RectangleObject((0.0, 0.0, w, h))
        page.mediabox = box
        page.cropbox = box
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), rotations


def extract(source_bytes: bytes) -> PdfExtraction:
    """Extract positioned lines from ``source_bytes``."""
    normalized, rotations = normalized_copy(source_bytes)
    pages: list[PdfPageGeom] = []
    lines: list[PdfLine] = []
    unscanned: list[dict[str, str]] = []

    with pdfplumber.open(io.BytesIO(normalized)) as pdf:
        for i, page in enumerate(pdf.pages):
            ox, oy = float(page.bbox[0]), float(page.bbox[1])
            geom = PdfPageGeom(
                index=i,
                width=float(page.width),
                height=float(page.height),
                rotation=rotations[i] if i < len(rotations) else 0,
            )
            pages.append(geom)

            words = page.extract_words(
                return_chars=True,
                expand_ligatures=False,
                keep_blank_chars=False,
                x_tolerance_ratio=_WORD_GAP_RATIO,
            )
            upright = [w for w in words if w.get("upright", True)]
            skipped = len(words) - len(upright)
            page_lines = _build_lines(i, upright, ox, oy)
            _assign_blocks(i, page_lines)
            lines.extend(page_lines)

            where = f"page {i + 1}"
            if skipped:
                unscanned.append(
                    {
                        "where": where,
                        "what": f"{skipped} rotated or vertical text fragment(s) not scanned",
                    }
                )
            if page.images:
                unscanned.append(
                    {
                        "where": where,
                        "what": f"{len(page.images)} image(s): text inside images is not scanned",
                    }
                )
            if not page_lines and not skipped:
                unscanned.append(
                    {"where": where, "what": "no text layer (scanned page?) - not scanned"}
                )
            annots = page.annots or []
            if annots:
                unscanned.append(
                    {
                        "where": where,
                        "what": (
                            f"{len(annots)} annotation(s) / form field(s): not scanned, "
                            "removed from the redacted output"
                        ),
                    }
                )

    return PdfExtraction(source_bytes=source_bytes, pages=pages, lines=lines, unscanned=unscanned)


def _build_lines(
    page_index: int, words: list[dict[str, Any]], ox: float, oy: float
) -> list[PdfLine]:
    """Group pdfplumber words into rows, then split rows at wide gaps."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))

    rows: list[list[dict[str, Any]]] = []
    row_top = row_bottom = 0.0
    for w in ordered:
        top, bottom = float(w["top"]), float(w["bottom"])
        if rows:
            overlap = min(bottom, row_bottom) - max(top, row_top)
            smaller = max(min(bottom - top, row_bottom - row_top), 0.01)
            if overlap / smaller >= 0.5:
                rows[-1].append(w)
                row_top, row_bottom = min(row_top, top), max(row_bottom, bottom)
                continue
        rows.append([w])
        row_top, row_bottom = top, bottom

    lines: list[PdfLine] = []
    for row in rows:
        row.sort(key=lambda w: float(w["x0"]))
        groups: list[list[dict[str, Any]]] = [[row[0]]]
        for prev, cur in itertools.pairwise(row):
            size = max(_word_size(prev), _word_size(cur), 1.0)
            if float(cur["x0"]) - float(prev["x1"]) > _LINE_SPLIT_GAP_EM * size:
                groups.append([cur])
            else:
                groups[-1].append(cur)
        for group in groups:
            line = _make_line(page_index, len(lines), group, ox, oy)
            if line.text.strip():
                lines.append(line)
    return lines


def _word_size(word: dict[str, Any]) -> float:
    sizes = [float(c.get("size") or 0) for c in word["chars"]]
    sizes = [s for s in sizes if s > 0]
    if sizes:
        return statistics.median(sizes)
    return float(word["bottom"]) - float(word["top"])


def _assign_blocks(page_index: int, lines: list[PdfLine]) -> None:
    """Group a page's lines into paragraphs: same column, small vertical gap.

    A line joins an open paragraph when its x-range overlaps the paragraph's
    last line, it starts at most 0.8 line-heights below that line's bottom,
    and the font sizes are within 20%. Otherwise it opens a new paragraph.
    Grouping is by key, so interleaved column order does not matter.
    """
    open_paras: list[tuple[str, PdfLine]] = []
    count = 0
    for line in lines:
        x0, top, x1, _ = line.bbox
        chosen: str | None = None
        for i, (key, last) in enumerate(open_paras):
            lx0, _, lx1, lbottom = last.bbox
            height = max(last.bbox[3] - last.bbox[1], line.bbox[3] - line.bbox[1])
            overlaps = min(x1, lx1) - max(x0, lx0) > 0
            gap = top - lbottom
            similar = abs(line.size - last.size) <= 0.2 * max(line.size, last.size)
            if overlaps and -0.2 * height <= gap <= 0.8 * height and similar:
                chosen = key
                open_paras[i] = (key, line)
                break
        if chosen is None:
            chosen = f"page{page_index}/para{count}"
            count += 1
            open_paras.append((chosen, line))
        line.block = chosen


def _make_line(
    page_index: int, j: int, words: list[dict[str, Any]], ox: float, oy: float
) -> PdfLine:
    top = min(float(w["top"]) for w in words) - oy
    bottom = max(float(w["bottom"]) for w in words) - oy
    text_parts: list[str] = []
    boxes: list[Box] = []
    prev_x1: float | None = None
    for w in words:
        if prev_x1 is not None:
            text_parts.append(" ")
            boxes.append((prev_x1, top, float(w["x0"]) - ox, bottom))
        for ch in w["chars"]:
            raw = str(ch.get("text", ""))
            expanded = _LIGATURES.get(raw, raw)
            if not expanded:
                continue
            x0, x1 = float(ch["x0"]) - ox, float(ch["x1"]) - ox
            ctop, cbottom = float(ch["top"]) - oy, float(ch["bottom"]) - oy
            step = (x1 - x0) / len(expanded)
            for k, cp in enumerate(expanded):
                text_parts.append(cp)
                boxes.append((x0 + k * step, ctop, x0 + (k + 1) * step, cbottom))
        prev_x1 = float(w["x1"]) - ox
    x0 = min(float(w["x0"]) for w in words) - ox
    x1 = max(float(w["x1"]) for w in words) - ox
    sizes = [_word_size(w) for w in words]
    return PdfLine(
        segment_id=f"page{page_index}/line{j}",
        page=page_index,
        text="".join(text_parts),
        char_boxes=boxes,
        bbox=(x0, top, x1, bottom),
        size=round(statistics.median(sizes), 2),
    )
