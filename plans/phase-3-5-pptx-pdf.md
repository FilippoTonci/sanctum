# Sanctum — Phase 3.5 Plan: PowerPoint and PDF review

> **Status: draft (2026-09-30).** Written after Phase 3 shipped `.docx`-only
> review. Overnight prototypes on the `overnight/*` branches test the risky
> parts of this plan; nothing here is enforced until a workstream starts.

> **Product scope (load-bearing).** The only anonymization operator in use
> is `replace`. The encrypted mapping store is not used. Neither format
> below may depend on the mapping store, and the review UI for them does not
> offer an operator picker.

---

## Context

The review surface in `sanctum-desktop` is format-agnostic past the render
step. Highlights, the detection sidebar, keyboard navigation, accept/reject
and commit all resolve detections through `[data-segment-id]` elements and
`(segmentId, start, end)` offsets (`src/renderer/src/review/segments.ts`).
The `.docx` view is a patched `docx-preview` that stamps each run's segment
id onto its rendered span. The review-session API already accepts `.pptx`
and `.pdf`; the desktop `DropZone` rejects them.

So each new format needs exactly two things:

1. An engine adapter whose segment ids are stable and match what the view renders.
2. A view that renders the document as DOM where every segment is an
   element carrying `data-segment-id`, whose `textContent` equals the
   segment's text.

Where the two formats differ is *output*: `.pptx` round-trips through
python-pptx with formatting intact; `.pdf` today emits a text-only
derivative (`pdf_adapter.py`), which is not acceptable for legal use.

## Shared layout contract

Both formats render from a layout description served by the engine, so the
renderer never parses the binary itself (except PDF page rasters, below).

```
GET /review-sessions/<id>/layout
200 {
  "format": "pptx" | "pdf",
  "pages": [                                   // slides or PDF pages, in order
    {
      "index": 0,
      "width": 720.0, "height": 540.0,         // points (1/72 in); pptx EMU / 12700
      "items": [ Item, ... ]                   // paint order, back to front
    }
  ]
}

Item =
  { "kind": "textbox", "x", "y", "w", "h",    // pptx shape / table cell text frame
    "paragraphs": [ { "align": "left"|"center"|"right"|"justify",
                      "runs": [ { "segment_id", "text", "size", "bold", "italic",
                                  "color", "font" } ] } ] }
| { "kind": "textline", "x", "y", "w", "h",   // pdf: one extracted line
    "segment_id", "text", "size" }
| { "kind": "image", "x", "y", "w", "h", "src" }   // data: URI
| { "kind": "shape", "x", "y", "w", "h", "fill" }  // background rects, pptx only
```

Coordinates are top-left origin, points. Every `segment_id` in the layout is
a segment in the session, and vice versa for every segment that can carry a
detection. Unscanned content (see WS2) is reported in a sibling
`"unscanned": [{ "where", "what" }]` array so the UI can name it.

## WS0 — Shared groundwork (~1 week)

1. `DocumentView` interface in the renderer: `{ file, layout?, detections,
   focusedId, onRendered(root) }`. `DocxView` becomes the first
   implementation; `App` picks the view by session format; `DropZone`
   accepts the formats the running engine advertises.
2. Post-commit leak check (engine): re-open the written output, extract all
   text, fail the commit if any accepted detection's original string
   survives. Applies to every format, including `.docx`.
3. Metadata scrub (engine): core/app properties (author, last modified by,
   company, title), comments, custom XML/XMP. Applies to every format,
   including `.docx`.

## WS1 — PowerPoint engine (~1 week)

1. Close adapter gaps in `pptx_adapter.py`: speaker notes, grouped shapes
   (recursive), chart titles and data labels, picture alt-text, comments.
   New segment-id prefixes: `slide{i}/notes/p{p}/r{r}`,
   `slide{i}/shape{j}/group{k}/...`.
2. Report what is still not scanned (embedded OLE objects, SmartArt) in
   `unscanned`, never silently.
3. `/layout` for pptx: shape geometry, text frames as `textbox` items,
   pictures as `image` items, slide background.

## WS2 — PowerPoint review surface (~2 weeks)

1. First slice: outline view — slides stacked as unpositioned text boxes.
   Shippable on its own.
2. `PptxView`: slides drawn from `/layout` as absolutely positioned HTML,
   scaled to fit. Not pixel-perfect; text stays real text so the existing
   highlight machinery works unchanged.
3. Slide thumbnails rail; sidebar grouped by slide; notes shown under each slide.

## WS3 — PDF engine (~2–3 weeks)

1. Positioned segmentation: one segment per extracted line
   (`page{i}/line{j}`) from pdfplumber word boxes. No new dependency.
2. Redacted output by **flattening affected pages**: every page with an
   accepted detection is rasterized (pypdfium2, Apache/BSD), each detection
   box is painted over and its replacement text drawn in, and the page is
   rebuilt as an image page. Pages without detections are copied as-is
   (vector, selectable). Guarantees removal without an AGPL dependency.
3. Strip annotations, form fields, attachments, XMP and document info.
4. Leak check (WS0.2) runs on the output.
5. Decision recorded: PyMuPDF (`apply_redactions`, true in-place redaction,
   selectable text preserved) is AGPL; revisit with a commercial Artifex
   licence once there is revenue.

## WS4 — PDF review surface (~2 weeks)

1. `PdfView`: each page rendered to canvas with PDF.js (Apache-2.0, bundled,
   offline, worker from the local bundle).
2. Over each canvas, a transparent layer of `textline` spans from `/layout`
   carrying `data-segment-id`. The engine's layer is used instead of PDF.js's
   own text layer because the two extract text differently and offsets
   would not match.
3. Page thumbnails rail; zoom.

## Later

- Scanned PDFs: bundled Tesseract OCR feeding the same `textline` layer.
- `.xlsx` review surface.

## Timeline (rough)

WS0 → WS1+WS2 (PowerPoint, ~3 weeks) → WS3+WS4 (PDF, ~4–5 weeks).
WS1 and WS3 can run in parallel once WS0 lands.
