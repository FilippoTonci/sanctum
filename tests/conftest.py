from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from sanctum.core.engine import SanctumEngine
from sanctum.core.models import AnonymizationResult, DetectionResult

SAMPLE_TEXT = (
    "My name is John Smith and my SSN is 123-45-6789. "
    "Contact me at john.smith@email.com or call 555-123-4567."
)


@pytest.fixture()
def sample_text() -> str:
    return SAMPLE_TEXT


@pytest.fixture()
def mock_analyzer(sample_text: str) -> Mock:
    analyzer = Mock()
    analyzer.analyze.return_value = [
        DetectionResult(
            entity_type="PERSON",
            start=11,
            end=21,
            score=0.85,
            text_span="John Smith",
            context=sample_text[:61],
            recognizer_name="SpacyRecognizer",
        ),
        DetectionResult(
            entity_type="US_SSN",
            start=36,
            end=47,
            score=0.95,
            text_span="123-45-6789",
            context=sample_text[:87],
            recognizer_name="UsSsnRecognizer",
        ),
        DetectionResult(
            entity_type="EMAIL_ADDRESS",
            start=64,
            end=84,
            score=0.90,
            text_span="john.smith@email.com",
            context=sample_text[24:],
            recognizer_name="EmailRecognizer",
        ),
        DetectionResult(
            entity_type="PHONE_NUMBER",
            start=93,
            end=105,
            score=0.75,
            text_span="555-123-4567",
            context=sample_text[53:],
            recognizer_name="PhoneRecognizer",
        ),
    ]
    return analyzer


@pytest.fixture()
def mock_anonymizer(sample_text: str) -> Mock:
    anonymizer = Mock()
    anonymizer.anonymize.return_value = AnonymizationResult(
        original_text=sample_text,
        anonymized_text=(
            "My name is <PERSON> and my SSN is <US_SSN>. "
            "Contact me at <EMAIL_ADDRESS> or call <PHONE_NUMBER>."
        ),
        detections=[
            DetectionResult(
                entity_type="PERSON",
                start=11,
                end=21,
                score=0.85,
                text_span="John Smith",
            ),
            DetectionResult(
                entity_type="US_SSN",
                start=36,
                end=47,
                score=0.95,
                text_span="123-45-6789",
            ),
            DetectionResult(
                entity_type="EMAIL_ADDRESS",
                start=64,
                end=84,
                score=0.90,
                text_span="john.smith@email.com",
            ),
            DetectionResult(
                entity_type="PHONE_NUMBER",
                start=93,
                end=105,
                score=0.75,
                text_span="555-123-4567",
            ),
        ],
        operators_applied={
            "PERSON": "replace",
            "US_SSN": "replace",
            "EMAIL_ADDRESS": "replace",
            "PHONE_NUMBER": "replace",
        },
    )
    return anonymizer


@pytest.fixture()
def engine(mock_analyzer: Mock, mock_anonymizer: Mock) -> SanctumEngine:
    return SanctumEngine(analyzer=mock_analyzer, anonymizer=mock_anonymizer)


@pytest.fixture()
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture()
def sample_pdf_path(fixture_dir: Path) -> Path:
    """A small PDF with a text layer (the pdf-engine lane's fixture)."""
    return fixture_dir / "office" / "engagement_letter.pdf"


# ---------- synthetic .pptx ----------
#
# Built in-test rather than committed as a binary: groups (nested), a
# table, speaker notes, a picture with alt-text, a chart and a solid
# background. Every name / address below is invented.

RICH_PPTX_PEOPLE = (
    "Margaret Holloway",
    "Daniel Okafor",
    "Jonas Albrecht",
    "Samuel Achterberg",
    "Olivia Brandt",
)


def build_rich_pptx(path: Path) -> Path:
    import io

    from PIL import Image
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt

    prs = Presentation()
    # slide 0: title + subtitle with a bold run, notes
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Briefing"
    p = s.placeholders[1].text_frame.paragraphs[0]
    p.add_run().text = "Prepared for "
    bold = p.add_run()
    bold.text = "Margaret Holloway"
    bold.font.bold = True
    s.notes_slide.notes_text_frame.text = "Call Daniel Okafor first."
    # slide 1: table, no notes
    s = prs.slides.add_slide(prs.slide_layouts[6])
    tbl = s.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(1)).table
    tbl.cell(0, 0).text = "Name"
    tbl.cell(0, 1).text = "Role"
    tbl.cell(1, 0).text = "Jonas Albrecht"
    tbl.cell(1, 1).text = "Witness"
    # slide 2: nested groups, filled shape, picture with alt-text, notes
    s = prs.slides.add_slide(prs.slide_layouts[6])
    grp = s.shapes.add_group_shape()
    card = grp.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(3), Inches(1))
    card.fill.solid()
    card.fill.fore_color.rgb = RGBColor(0x12, 0x34, 0x56)
    card.text_frame.text = "GC: Samuel Achterberg"
    card.text_frame.paragraphs[0].runs[0].font.size = Pt(20)
    inner = grp.shapes.add_group_shape()
    inner.shapes.add_textbox(
        Inches(1), Inches(2.5), Inches(3), Inches(0.5)
    ).text_frame.text = "Deputy: Olivia Brandt"
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), (10, 20, 30)).save(buf, format="PNG")
    buf.seek(0)
    pic = s.shapes.add_picture(buf, Inches(5), Inches(1), Inches(2), Inches(1.5))
    pic._element.nvPicPr.cNvPr.set("descr", "Photo of Samuel Achterberg")
    s.notes_slide.notes_text_frame.text = "Olivia Brandt joined in 2024."
    # slide 3: background + chart (unscanned)
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = RGBColor(0xFA, 0xF6, 0xEE)
    data = CategoryChartData()
    data.categories = ["Q1", "Q2"]
    data.add_series("Hours", (1, 2))
    s.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1), Inches(4), Inches(3), data
    )
    prs.save(str(path))
    return path


@pytest.fixture()
def rich_pptx(tmp_path: Path) -> Path:
    return build_rich_pptx(tmp_path / "rich.pptx")
