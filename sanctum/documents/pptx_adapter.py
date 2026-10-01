"""PowerPoint (.pptx) structured reader/writer backed by python-pptx.

Segment granularity is the *run* inside a shape's text frame, same
trade-off as the .docx adapter — formatting survives, cross-run
entities don't.

Segment IDs:
    slide{i}/shape{j}/p{p}/r{r}                       regular text-frame run
    slide{i}/shape{j}/row{r}/col{c}/p{p}/r{r}         run inside a table cell
    slide{i}/shape{j}/group{k}/...                    child ``k`` of group shape ``j``
                                                      (recursive: ``group{k}/group{m}/...``)
    slide{i}/shape{j}/alt                             picture alt-text (``cNvPr@descr``)
    slide{i}/notes/p{p}/r{r}                          speaker-notes run

Everything that walks a slide (the Reader, the Writer's target index and
the review ``/layout`` builder in ``pptx_layout.py``) goes through
:func:`iter_slide_shapes` + :func:`shape_targets`, so the ids the review
UI renders are the ids the Writer patches, by construction.

Not scanned (reported by ``pptx_layout.unscanned_content``): charts,
SmartArt, embedded OLE objects, comments, text on slide masters/layouts,
and document properties.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pptx import Presentation

from sanctum.documents.structured import build_document, build_segment, run_block

if TYPE_CHECKING:
    from pptx.presentation import Presentation as PptxPresentation
    from pptx.text.text import TextFrame, _Run

    from sanctum.core.models import StructuredDocument, TextSegment

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


class RunTarget:
    """A text run: the classic segment."""

    __slots__ = ("run",)

    def __init__(self, run: _Run) -> None:
        self.run = run

    def get(self) -> str:
        return str(self.run.text)

    def set(self, text: str) -> None:
        self.run.text = text


class AltTarget:
    """A picture's alt-text, stored on ``p:cNvPr/@descr``."""

    __slots__ = ("c_nv_pr",)

    def __init__(self, c_nv_pr: Any) -> None:
        self.c_nv_pr = c_nv_pr

    def get(self) -> str:
        return str(self.c_nv_pr.get("descr", ""))

    def set(self, text: str) -> None:
        # Only touch the XML on a real change so an unedited round trip
        # leaves the attribute exactly as it was.
        if text != self.get():
            self.c_nv_pr.set("descr", text)


Target = RunTarget | AltTarget


@dataclass(frozen=True)
class Transform:
    """Affine map from a shape tree's coordinate space to slide EMU.

    Top-level shapes use the identity. Children of a group are expressed
    in the group's child space (``chOff``/``chExt``), which maps onto the
    group's own ``off``/``ext``.
    """

    dx: float = 0.0
    dy: float = 0.0
    sx: float = 1.0
    sy: float = 1.0

    def apply(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        return (self.dx + x * self.sx, self.dy + y * self.sy, w * self.sx, h * self.sy)

    def child(self, group: Any) -> Transform:
        """Compose with a group shape's child-space mapping."""
        xfrm = group._element.find(f"{{{P_NS}}}grpSpPr/{{{A_NS}}}xfrm")
        if xfrm is None:
            return self

        def _pair(tag: str, a: str, b: str) -> tuple[float, float] | None:
            el = xfrm.find(f"{{{A_NS}}}{tag}")
            if el is None:
                return None
            return float(el.get(a, 0)), float(el.get(b, 0))

        off = _pair("off", "x", "y")
        ext = _pair("ext", "cx", "cy")
        ch_off = _pair("chOff", "x", "y") or off
        ch_ext = _pair("chExt", "cx", "cy") or ext
        if off is None or ext is None or ch_off is None or ch_ext is None:
            return self
        sx = ext[0] / ch_ext[0] if ch_ext[0] else 1.0
        sy = ext[1] / ch_ext[1] if ch_ext[1] else 1.0
        # child point c -> group-parent point off + (c - chOff) * s -> slide via self
        return Transform(
            dx=self.dx + (off[0] - ch_off[0] * sx) * self.sx,
            dy=self.dy + (off[1] - ch_off[1] * sy) * self.sy,
            sx=self.sx * sx,
            sy=self.sy * sy,
        )


def is_group(shape: Any) -> bool:
    return bool(shape._element.tag == f"{{{P_NS}}}grpSp")


def iter_slide_shapes(slide: Any, s_idx: int) -> Iterator[tuple[Any, str, Transform]]:
    """Yield ``(shape, id_prefix, transform)`` for every shape, depth-first.

    Group shapes are yielded themselves (callers usually skip them),
    followed by their children with prefix ``{group_prefix}/group{k}``.
    Order is the slide's paint order, back to front.
    """

    def _walk(
        shapes: Any, base: str, label: str, xf: Transform
    ) -> Iterator[tuple[Any, str, Transform]]:
        for idx, shape in enumerate(shapes):
            prefix = f"{base}/{label}{idx}"
            yield shape, prefix, xf
            if is_group(shape):
                yield from _walk(shape.shapes, prefix, "group", xf.child(shape))

    yield from _walk(slide.shapes, f"slide{s_idx}", "shape", Transform())


def run_id(prefix: str, p_idx: int, r_idx: int) -> str:
    """The one place the ``.../p{p}/r{r}`` suffix is spelled."""
    return f"{prefix}/p{p_idx}/r{r_idx}"


def iter_text_frame_runs(text_frame: TextFrame, prefix: str) -> list[tuple[str, _Run]]:
    pairs: list[tuple[str, _Run]] = []
    for p_idx, para in enumerate(text_frame.paragraphs):
        for r_idx, run in enumerate(para.runs):
            pairs.append((run_id(prefix, p_idx, r_idx), run))
    return pairs


def picture_c_nv_pr(shape: Any) -> Any | None:
    """Return the ``p:cNvPr`` of a picture shape, or ``None`` for non-pictures."""
    return shape._element.find(f"{{{P_NS}}}nvPicPr/{{{P_NS}}}cNvPr")


def alt_text(shape: Any) -> str:
    c_nv_pr = picture_c_nv_pr(shape)
    return "" if c_nv_pr is None else str(c_nv_pr.get("descr", ""))


def shape_targets(shape: Any, prefix: str) -> list[tuple[str, Target]]:
    """Every scannable text target on a single (non-group) shape."""
    pairs: list[tuple[str, Target]] = []
    if shape.has_text_frame:
        pairs.extend(
            (sid, RunTarget(run)) for sid, run in iter_text_frame_runs(shape.text_frame, prefix)
        )
    if shape.has_table:
        for r_idx, row in enumerate(shape.table.rows):
            for c_idx, cell in enumerate(row.cells):
                cell_prefix = f"{prefix}/row{r_idx}/col{c_idx}"
                pairs.extend(
                    (sid, RunTarget(run))
                    for sid, run in iter_text_frame_runs(cell.text_frame, cell_prefix)
                )
    if alt_text(shape).strip():
        pairs.append((f"{prefix}/alt", AltTarget(picture_c_nv_pr(shape))))
    return pairs


def notes_text_frame(slide: Any) -> TextFrame | None:
    """The slide's notes body, without creating a notes slide as a side effect."""
    if not slide.has_notes_slide:
        return None
    frame: TextFrame | None = slide.notes_slide.notes_text_frame
    return frame


def notes_targets(slide: Any, s_idx: int) -> list[tuple[str, Target]]:
    frame = notes_text_frame(slide)
    if frame is None:
        return []
    return [
        (sid, RunTarget(run)) for sid, run in iter_text_frame_runs(frame, f"slide{s_idx}/notes")
    ]


def iter_presentation_targets(prs: PptxPresentation) -> Iterator[tuple[str, Target]]:
    """Every scannable target in document order: slide shapes, then that slide's notes."""
    for s_idx, slide in enumerate(prs.slides):
        for shape, prefix, _xf in iter_slide_shapes(slide, s_idx):
            if is_group(shape):
                continue
            yield from shape_targets(shape, prefix)
        yield from notes_targets(slide, s_idx)


class Reader:
    """Flatten every slide's run-level text into segments."""

    def read(self, path: Path) -> StructuredDocument:
        prs: PptxPresentation = Presentation(str(path))
        segments: list[TextSegment] = [
            build_segment(seg_id, target.get(), block=run_block(seg_id))
            for seg_id, target in iter_presentation_targets(prs)
        ]
        return build_document(
            source_path=path,
            fmt="pptx",
            segments=segments,
            raw_handle=prs,
        )


class Writer:
    """Re-apply segment text to the raw Presentation and save."""

    def write(self, doc: StructuredDocument, path: Path) -> None:
        prs: PptxPresentation | None = doc.raw_handle
        if prs is None:
            raise ValueError(
                "PptxWriter requires the StructuredDocument.raw_handle from PptxReader"
            )

        index = dict(iter_presentation_targets(prs))
        for segment in doc.segments:
            target = index.get(segment.id)
            if target is None:
                continue
            target.set(segment.text)

        prs.save(str(path))
