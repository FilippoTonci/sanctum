"""Generate SYNTHETIC PDFs that exercise the PDF redaction path (Phase 3.5 WS3).

Every name, address, email and number below is invented. Output is
deterministic (reportlab ``invariant=1``), so tests can build these into a
temp dir instead of committing binaries.

    python scripts/generate_pdf_samples.py OUT_DIR

Samples:

- ``rich_letter.pdf`` — 4 pages: letterhead image + address block + a name
  broken across two lines (p1), two-column memo (p2), a table and 6 pt
  footnote (p3), boilerplate with no PII (p4).
- ``rotated_cropped.pdf`` — one landscape page stored as portrait with
  ``/Rotate 90`` and a CropBox offset from the MediaBox.
- ``metadata_laden.pdf`` — document info, XMP, a sticky-note annotation,
  a filled form field, and an embedded file, all carrying PII.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

# Synthetic identities used by the samples (and asserted on by tests).
CLIENT = "Jonathan Whitfield"
CLIENT_EMAIL = "j.whitfield@example.org"
CLIENT_PHONE = "+44 20 7946 0321"
PARTNER = "Evelyn Marchetti"
ASSOCIATE = "Tobias Lindqvist"
WITNESS = "Priya Raghunathan"
COUNTERPARTY = "Margaret Okonkwo"


def _letterhead_png() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 120), (18, 52, 86))
    d = ImageDraw.Draw(img)
    d.ellipse((20, 15, 110, 105), fill=(214, 170, 60))
    d.rectangle((140, 50, 860, 58), fill=(214, 170, 60))
    d.text((140, 20), "NORTHWIND & CALLOWAY  -  SOLICITORS", fill=(255, 255, 255))
    d.text((140, 70), "1 Example Square, Testville", fill=(200, 210, 220))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_rich_letter(path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        NextPageTemplate,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=6, leading=7.5)
    h = styles["Heading2"]
    logo = ImageReader(io.BytesIO(_letterhead_png()))
    width, height = A4
    margin = 20 * mm

    def letterhead(canvas, _doc):  # type: ignore[no-untyped-def]
        canvas.drawImage(logo, margin, height - margin - 20 * mm, width - 2 * margin, 20 * mm)

    def footer(canvas, doc):  # type: ignore[no-untyped-def]
        canvas.setFont("Helvetica", 7)
        canvas.drawString(margin, 10 * mm, f"Northwind & Calloway - page {doc.page}")

    doc = BaseDocTemplate(str(path), pagesize=A4, invariant=1, title="", author="")
    full = Frame(margin, margin, width - 2 * margin, height - 2 * margin - 24 * mm, id="full")
    plain = Frame(margin, margin, width - 2 * margin, height - 2 * margin, id="plain")
    col_w = (width - 2 * margin - 10 * mm) / 2
    left = Frame(margin, margin, col_w, height - 2 * margin, id="left")
    right = Frame(margin + col_w + 10 * mm, margin, col_w, height - 2 * margin, id="right")
    doc.addPageTemplates(
        [
            PageTemplate(
                "letter", frames=[full], onPage=lambda c, d: (letterhead(c, d), footer(c, d))
            ),
            PageTemplate("twocol", frames=[left, right], onPage=footer),
            PageTemplate("plain", frames=[plain], onPage=footer),
        ]
    )

    story = [
        Paragraph("Private &amp; Confidential", h),
        Paragraph(f"Mr {CLIENT}", body),
        Paragraph("14 Harbour Lane, Portsmouth PO1 3AX", body),
        Paragraph(f"Email: {CLIENT_EMAIL} - Tel: {CLIENT_PHONE}", body),
        Spacer(1, 10),
        Paragraph(f"Dear Mr {CLIENT},", body),
        Paragraph(
            "Thank you for instructing us in connection with the proposed share purchase. "
            "This letter confirms the scope of our engagement and the team that will act for you.",
            body,
        ),
        Paragraph(
            f"The matter will be supervised by Dr {PARTNER.split()[0]}<br/>"
            f"{PARTNER.split()[1]}, partner in our corporate group, assisted by "
            f"{ASSOCIATE}.",
            body,
        ),
        Paragraph(
            f"Please send signed documents to {CLIENT_EMAIL} or call {CLIENT_PHONE}.",
            body,
        ),
        NextPageTemplate("twocol"),
        PageBreak(),
        Paragraph("Internal memo - due diligence notes", h),
    ]
    for k in range(14):
        story.append(
            Paragraph(
                f"Note {k + 1}. The data room index was reviewed by {ASSOCIATE} on behalf of "
                f"{CLIENT}. Board minutes record that {COUNTERPARTY} abstained on the vote "
                "concerning the lease renewal and the related guarantee.",
                body,
            )
        )
        story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"Witness statements were taken from {WITNESS}; her contact is "
            "p.raghunathan@example.net.",
            body,
        )
    )
    story += [NextPageTemplate("plain"), PageBreak(), Paragraph("Schedule 1 - Parties", h)]
    table = Table(
        [
            ["Role", "Name", "Email", "Shares"],
            ["Buyer", CLIENT, CLIENT_EMAIL, "12,500"],
            ["Seller", COUNTERPARTY, "m.okonkwo@example.com", "12,500"],
            ["Witness", WITNESS, "p.raghunathan@example.net", "-"],
        ],
        colWidths=[25 * mm, 45 * mm, 60 * mm, 25 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.85, 0.9, 0.95)),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story += [
        table,
        Spacer(1, 12),
        Paragraph(
            f"* Footnote: the shareholding of {COUNTERPARTY} is held through a nominee; "
            "see the register kept at the registered office.",
            small,
        ),
        PageBreak(),
        Paragraph("Schedule 2 - Standard terms", h),
    ]
    for k in range(5):
        story.append(
            Paragraph(
                f"{k + 1}. Our fees are charged on the basis of time spent. Invoices are payable "
                "within thirty days. These terms are governed by the law of England and Wales.",
                body,
            )
        )
    doc.build(story)


def build_rotated_cropped(path: Path) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842), invariant=1)
    # A landscape page stored as portrait + /Rotate 90: the content is
    # counter-rotated so it reads upright on screen (how scanners and
    # print-to-PDF drivers emit landscape pages).
    c.saveState()
    c.translate(595, 0)
    c.rotate(90)
    c.setFont("Helvetica", 12)
    c.drawString(80, 480, f"Landscape annex prepared for {CLIENT}.")
    c.drawString(80, 460, "This line has no personal data.")
    c.restoreState()
    # Vertical on screen: reported as unscanned, not silently dropped.
    c.setFont("Helvetica", 8)
    c.drawString(60, 300, "Ref NW-2026-0042")
    c.showPage()
    c.save()
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    page = writer.pages[0]
    page.cropbox = RectangleObject([40, 40, 555, 802])
    page.rotate(90)
    with open(path, "wb") as fh:
        writer.write(fh)


def build_metadata_laden(path: Path) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842), invariant=1)
    c.setAuthor(PARTNER)
    c.setTitle(f"Engagement - {CLIENT}")
    c.setSubject("Confidential")
    c.setFont("Helvetica", 12)
    c.drawString(72, 760, f"Client: {CLIENT}")
    c.drawString(72, 740, "Matter: share purchase")
    c.textAnnotation(f"Call {WITNESS} before filing", Rect=(300, 700, 340, 740))
    c.acroForm.textfield(name="signatory", value=COUNTERPARTY, x=72, y=600, width=200, height=20)
    c.showPage()
    c.save()
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    writer = PdfWriter(clone_from=reader)
    writer.add_attachment("notes.txt", f"Private notes about {CLIENT}".encode())
    xmp = DecodedStreamObject()
    xmp.set_data(
        (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:creator>{PARTNER}</dc:creator></rdf:Description></rdf:RDF></x:xmpmeta>"
        ).encode()
    )
    xmp.update(
        {NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")}
    )
    writer._root_object[NameObject("/Metadata")] = writer._add_object(xmp)
    with open(path, "wb") as fh:
        writer.write(fh)


SAMPLES = {
    "rich_letter.pdf": build_rich_letter,
    "rotated_cropped.pdf": build_rotated_cropped,
    "metadata_laden.pdf": build_metadata_laden,
}


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, build in SAMPLES.items():
        build(out / name)
        print(out / name)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
