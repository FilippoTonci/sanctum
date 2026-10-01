"""Unit tests for the positioned PDF reader and flattening writer (Phase 3.5 WS3).

Replaces the Phase 1 tests (one segment per page, text-only reportlab
derivative), whose behavior was removed on purpose.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pdfplumber
import pytest
from pypdf import PdfReader
from sanctum.core.exceptions import DocumentError, PdfWriteRefusedError, UnsupportedPdfError
from sanctum.core.models import StructuredDocument
from sanctum.core.protocols import OutputTextExtractor
from sanctum.documents.pdf_adapter import Reader, Writer, build_layout, extract_all_text
from sanctum.documents.structured import build_document, build_segment

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "office" / "engagement_letter.pdf"


def _load_samples() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_pdf_samples", ROOT / "scripts" / "generate_pdf_samples.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


samples = _load_samples()


@pytest.fixture(scope="module")
def sample_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("pdf_samples")
    samples.main(str(out))
    return out


def replace_in_doc(doc: StructuredDocument, originals: dict[str, str]) -> StructuredDocument:
    """Mimic the engine: replace the first hit of each original per line and
    record the exact spans in ``metadata['replacements']``."""
    new_segments = []
    for seg in doc.segments:
        text = seg.text
        hits = sorted((text.find(o), o) for o in originals if o in text)
        reps: list[dict[str, Any]] = []
        out: list[str] = []
        cursor = 0
        for start, original in hits:
            if start < cursor:
                continue
            out += [text[cursor:start], originals[original]]
            reps.append(
                {
                    "start": start,
                    "end": start + len(original),
                    "original": original,
                    "text": originals[original],
                }
            )
            cursor = start + len(original)
        if not reps:
            new_segments.append(seg)
            continue
        out.append(text[cursor:])
        new_segments.append(
            seg.model_copy(
                update={"text": "".join(out), "metadata": {**seg.metadata, "replacements": reps}}
            )
        )
    mutated = doc.model_copy(update={"segments": new_segments})
    mutated.raw_handle = doc.raw_handle
    return mutated


def _page_texts(path: Path) -> list[str]:
    with pdfplumber.open(str(path)) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def _page_has_image(reader: PdfReader, i: int) -> bool:
    xobjects = reader.pages[i].get("/Resources", {}).get("/XObject", {})
    return any(x.get_object().get("/Subtype") == "/Image" for x in xobjects.values())


# ----- reader --------------------------------------------------------------


def test_reader_emits_one_segment_per_line() -> None:
    doc = Reader().read(FIXTURE)
    assert doc.format == "pdf"
    assert doc.segments[0].id == "page0/line0"
    assert [s.id for s in doc.segments] == [f"page0/line{j}" for j in range(len(doc.segments))]
    texts = [s.text for s in doc.segments]
    assert "Kimberly Sanchez" in texts
    assert all("\n" not in t for t in texts)


def test_reader_metadata_has_display_geometry() -> None:
    md = Reader().read(FIXTURE).segments[0].metadata
    assert set(md) >= {"page", "x", "y", "w", "h", "size", "page_width", "page_height"}
    assert (md["page_width"], md["page_height"]) == (612.0, 792.0)
    assert md["x"] == pytest.approx(78.0)
    assert md["y"] == pytest.approx(80.07, abs=0.01)
    assert md["size"] == 10.0


def test_reader_splits_columns_and_table_cells(sample_dir: Path) -> None:
    doc = Reader().read(sample_dir / "rich_letter.pdf")
    by_page: dict[int, list[Any]] = {}
    for s in doc.segments:
        by_page.setdefault(s.metadata["page"], []).append(s)
    # Two-column memo: lines exist in the right column, and no line spans
    # the gutter.
    right = [s for s in by_page[1] if s.metadata["x"] > 250]
    left = [s for s in by_page[1] if s.metadata["x"] <= 250 and s.metadata["y"] < 800]
    assert right and left
    gutter = min(s.metadata["x"] for s in right)
    assert all(s.metadata["x"] + s.metadata["w"] < gutter for s in left)
    # Table: every cell is its own line.
    texts = [s.text for s in by_page[2]]
    assert samples.CLIENT in texts
    assert samples.CLIENT_EMAIL in texts
    # 6 pt footnote words are still separated by spaces.
    assert any(f"shareholding of {samples.COUNTERPARTY} is held" in t for t in texts)


def test_reader_keeps_name_split_across_lines_as_two_lines(sample_dir: Path) -> None:
    texts = [s.text for s in Reader().read(sample_dir / "rich_letter.pdf").segments]
    first, last = samples.PARTNER.split()
    assert any(t.endswith(f"Dr {first}") for t in texts)
    assert any(t.startswith(f"{last},") for t in texts)


def test_reader_handles_rotated_and_cropped_page(sample_dir: Path) -> None:
    seg = Reader().read(sample_dir / "rotated_cropped.pdf").segments[0]
    assert seg.text == f"Landscape annex prepared for {samples.CLIENT}."
    # /Rotate 90 on a 515x762 CropBox displays as 762x515 landscape.
    assert (seg.metadata["page_width"], seg.metadata["page_height"]) == (762.0, 515.0)
    assert 0 <= seg.metadata["x"] < 762 and 0 <= seg.metadata["y"] < 515


def test_reader_raises_on_no_text_layer(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    scanned = tmp_path / "empty.pdf"
    c = canvas.Canvas(str(scanned))
    c.showPage()
    c.save()
    with pytest.raises(UnsupportedPdfError, match="no extractable text"):
        Reader().read(scanned)


def test_reader_wraps_garbage_as_document_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(DocumentError):
        Reader().read(bad)


# ----- layout --------------------------------------------------------------


def test_layout_matches_segments_one_to_one(sample_dir: Path) -> None:
    path = sample_dir / "rich_letter.pdf"
    doc = Reader().read(path)
    layout = build_layout(path)
    assert layout["format"] == "pdf"
    assert [p["index"] for p in layout["pages"]] == [0, 1, 2, 3]
    assert all(p["width"] == 595.28 and p["height"] == 841.89 for p in layout["pages"])
    items = [it for p in layout["pages"] for it in p["items"]]
    assert all(it["kind"] == "textline" for it in items)
    assert [it["segment_id"] for it in items] == [s.id for s in doc.segments]
    for it, seg in zip(items, doc.segments, strict=True):
        assert it["text"] == seg.text
        assert (it["x"], it["y"], it["w"], it["h"], it["size"]) == (
            seg.metadata["x"],
            seg.metadata["y"],
            seg.metadata["w"],
            seg.metadata["h"],
            seg.metadata["size"],
        )
    assert {"where": "page 1", "what": "1 image(s): text inside images is not scanned"} in (
        layout["unscanned"]
    )


def test_layout_reports_vertical_text_and_annotations(sample_dir: Path) -> None:
    rotated = build_layout(sample_dir / "rotated_cropped.pdf")
    assert any("rotated or vertical" in u["what"] for u in rotated["unscanned"])
    laden = build_layout(sample_dir / "metadata_laden.pdf")
    assert any("annotation" in u["what"] for u in laden["unscanned"])


# ----- writer --------------------------------------------------------------


def test_writer_implements_leak_check_port() -> None:
    assert isinstance(Writer(), OutputTextExtractor)


def test_writer_refuses_to_overwrite_source() -> None:
    doc = Reader().read(FIXTURE)
    with pytest.raises(PdfWriteRefusedError, match="overwrite source"):
        Writer().write(doc, FIXTURE)


def test_writer_requires_reader_handle(tmp_path: Path) -> None:
    doc = build_document(
        source_path=tmp_path / "src.pdf",
        fmt="pdf",
        segments=[build_segment("page0/line0", "x")],
        raw_handle=None,
    )
    with pytest.raises(DocumentError, match="raw handle"):
        Writer().write(doc, tmp_path / "out.pdf")


def test_writer_rejects_unknown_segment(tmp_path: Path) -> None:
    doc = Reader().read(FIXTURE)
    doc.segments.append(build_segment("page9/line9", "stray"))
    with pytest.raises(DocumentError, match="not a line"):
        Writer().write(doc, tmp_path / "out.pdf")


def test_unedited_write_keeps_every_page_vector(sample_dir: Path, tmp_path: Path) -> None:
    src = sample_dir / "rich_letter.pdf"
    out = tmp_path / "same.pdf"
    Writer().write(Reader().read(src), out)
    assert _page_texts(out) == _page_texts(src)
    reader = PdfReader(str(out))
    # Page 1's letterhead is the only image; nothing was rasterized.
    assert [_page_has_image(reader, i) for i in range(4)] == [True, False, False, False]


def test_edited_pages_are_flattened_and_others_copied(sample_dir: Path, tmp_path: Path) -> None:
    src = sample_dir / "rich_letter.pdf"
    mutated = replace_in_doc(
        Reader().read(src),
        {
            samples.CLIENT: "<PERSON>",
            samples.CLIENT_EMAIL: "<EMAIL_ADDRESS>",
            samples.COUNTERPARTY: "<PERSON>",
        },
    )
    out = tmp_path / "redacted.pdf"
    writer = Writer()
    writer.write(mutated, out)

    before, after = _page_texts(src), _page_texts(out)
    assert len(after) == 4
    # Pages 1-3 carry replacements; page 4 (boilerplate) is untouched vector.
    assert after[3] == before[3]
    reader = PdfReader(str(out))
    assert [_page_has_image(reader, i) for i in range(4)] == [True, True, True, False]
    # Flattened pages keep a searchable text layer, minus the originals.
    text = writer.extract_text(out)
    for original in (samples.CLIENT, samples.CLIENT_EMAIL, samples.COUNTERPARTY):
        assert original not in text
    assert "<PERSON>" in after[0] and "<EMAIL_ADDRESS>" in after[0]
    assert "Thank you for instructing us" in after[0]
    assert samples.ASSOCIATE in after[1]  # not replaced, still searchable


def test_redacted_pixels_are_really_gone(tmp_path: Path) -> None:
    import pypdfium2 as pdfium

    doc = Reader().read(FIXTURE)
    seg = next(s for s in doc.segments if s.text == "Kimberly Sanchez")
    out = tmp_path / "out.pdf"
    Writer().write(replace_in_doc(doc, {"Kimberly Sanchez": ""}), out)
    scale = 2.0
    md = seg.metadata
    box = tuple(int(v * scale) for v in (md["x"], md["y"], md["x"] + md["w"], md["y"] + md["h"]))
    before = pdfium.PdfDocument(str(FIXTURE))[0].render(scale=scale).to_pil().convert("L")
    after = pdfium.PdfDocument(str(out))[0].render(scale=scale).to_pil().convert("L")
    assert before.crop(box).getextrema()[0] < 100  # dark glyphs were there
    assert after.crop(box).getextrema()[0] > 240  # now blank


def test_diff_fallback_without_engine_metadata(tmp_path: Path) -> None:
    doc = Reader().read(FIXTURE)
    segs = [
        s.model_copy(update={"text": s.text.replace("Kimberly Sanchez", "<PERSON>")})
        for s in doc.segments
    ]
    mutated = doc.model_copy(update={"segments": segs})
    mutated.raw_handle = doc.raw_handle
    out = tmp_path / "out.pdf"
    Writer().write(mutated, out)
    text = extract_all_text(out.read_bytes())
    assert "Kimberly Sanchez" not in text and "<PERSON>" in text


def test_inconsistent_metadata_falls_back_to_whole_line(tmp_path: Path) -> None:
    doc = Reader().read(FIXTURE)
    segs = []
    for s in doc.segments:
        if s.text == "Kimberly Sanchez":
            bogus = [{"start": 0, "end": 3, "original": "WRONG", "text": "<PERSON>"}]
            s = s.model_copy(
                update={"text": "<PERSON>", "metadata": {**s.metadata, "replacements": bogus}}
            )
        segs.append(s)
    mutated = doc.model_copy(update={"segments": segs})
    mutated.raw_handle = doc.raw_handle
    out = tmp_path / "out.pdf"
    Writer().write(mutated, out)
    text = extract_all_text(out.read_bytes())
    # The standalone line was wiped whole; "Dear Kimberly Sanchez," was not edited.
    assert text.count("Kimberly") == 1 and "<PERSON>" in text


def test_rotated_page_redaction(sample_dir: Path, tmp_path: Path) -> None:
    doc = Reader().read(sample_dir / "rotated_cropped.pdf")
    out = tmp_path / "rot.pdf"
    Writer().write(replace_in_doc(doc, {samples.CLIENT: "<PERSON>"}), out)
    page = PdfReader(str(out)).pages[0]
    assert int(page.get("/Rotate", 0)) == 0
    assert (float(page.mediabox.width), float(page.mediabox.height)) == (762.0, 515.0)
    text = extract_all_text(out.read_bytes())
    assert samples.CLIENT not in text
    assert "Landscape annex prepared for <PERSON>" in text


def test_output_is_stripped_of_metadata_annotations_forms_and_files(
    sample_dir: Path, tmp_path: Path
) -> None:
    src = sample_dir / "metadata_laden.pdf"
    src_reader = PdfReader(str(src))
    assert src_reader.metadata and src_reader.attachments and src_reader.get_fields()

    out = tmp_path / "clean.pdf"
    Writer().write(replace_in_doc(Reader().read(src), {samples.CLIENT: "<PERSON>"}), out)

    reader = PdfReader(str(out))
    assert reader.metadata is None
    for key in ("/Metadata", "/AcroForm", "/Names", "/OpenAction"):
        assert key not in reader.root_object
    assert not reader.attachments
    assert all("/Annots" not in p for p in reader.pages)
    raw = out.read_bytes()
    for secret in (samples.PARTNER, samples.WITNESS, samples.COUNTERPARTY, samples.CLIENT):
        assert secret.encode() not in raw
    assert samples.CLIENT not in extract_all_text(raw)


def test_unedited_write_is_still_sanitized(sample_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "clean.pdf"
    Writer().write(Reader().read(sample_dir / "metadata_laden.pdf"), out)
    reader = PdfReader(str(out))
    assert reader.metadata is None and not reader.attachments
    text = extract_all_text(out.read_bytes())
    assert samples.PARTNER not in text and samples.WITNESS not in text


def test_extract_all_text_includes_metadata_and_annotations(sample_dir: Path) -> None:
    text = extract_all_text((sample_dir / "metadata_laden.pdf").read_bytes())
    assert samples.PARTNER in text  # author (info) + XMP creator
    assert samples.WITNESS in text  # sticky-note contents


def test_samples_are_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    samples.build_rich_letter(a)
    samples.build_rich_letter(b)
    assert a.read_bytes() == b.read_bytes()


def test_registry_dispatches_to_pdf_adapter() -> None:
    from sanctum.documents import adapter_for

    r, w = adapter_for(FIXTURE)
    assert isinstance(r, Reader)
    assert isinstance(w, Writer)
