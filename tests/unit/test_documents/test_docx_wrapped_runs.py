"""Runs inside w:hyperlink, w:ins, w:sdt and w:smartTag are read, redacted and leak-checked.

Final-review C2 / Ruling 13. ``Paragraph.runs`` only returns direct ``w:r``
children, so a name inside a mailto link, a tracked insertion or a content
control used to be neither detected nor leak-checked. Tracked changes are
accepted in the output, which also removes deleted text and revision
authors. Every name below is invented.
"""

from __future__ import annotations

import re
import struct
import zipfile
import zlib
from pathlib import Path

import docx
import pytest
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import LeakCheckError
from sanctum.core.models import DetectionResult, OperatorPolicy
from sanctum.documents.docx_adapter import Reader, Writer

NAME = "Jane Doe"
EMAIL = "jane.doe@example.com"
AUTHOR = "Rob Reviewer"
_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)
_DATE = 'w:date="2026-01-01T00:00:00Z"'
REPLACE = {"DEFAULT": OperatorPolicy(operator_name="replace")}


def _run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def _fragments(rid: str) -> dict[str, str]:
    """Body-level XML for each wrapper kind; every one hides NAME or EMAIL."""
    return {
        "hyperlink": (
            f"<w:p {_NS}>{_run('Email ')}"
            f'<w:hyperlink r:id="{rid}">{_run(EMAIL)}</w:hyperlink>{_run(" today.")}</w:p>'
        ),
        "ins": (
            f"<w:p {_NS}>{_run('Signed by ')}"
            f'<w:ins w:id="11" w:author="{AUTHOR}" {_DATE}>{_run(NAME)}</w:ins></w:p>'
        ),
        "sdt_inline": (
            f"<w:p {_NS}>{_run('Client: ')}"
            f'<w:sdt><w:sdtPr><w:alias w:val="Client"/></w:sdtPr>'
            f"<w:sdtContent>{_run(NAME)}</w:sdtContent></w:sdt></w:p>"
        ),
        "smart_tag": (
            f"<w:p {_NS}>{_run('Contact ')}"
            f'<w:smartTag w:uri="urn:x" w:element="PersonName">{_run(NAME)}</w:smartTag></w:p>'
        ),
        "sdt_block": (
            f"<w:sdt {_NS}><w:sdtPr/><w:sdtContent>"
            f"<w:p>{_run('Party: ' + NAME)}</w:p></w:sdtContent></w:sdt>"
        ),
    }


_DELETION = (
    f"<w:p {_NS}>{_run('Witness: ')}"
    f'<w:del w:id="12" w:author="{AUTHOR}" {_DATE}>'
    f'<w:r><w:delText xml:space="preserve">{NAME}</w:delText></w:r></w:del>'
    f"{_run('none')}</w:p>"
)
_FORMAT_CHANGE = (
    f'<w:p {_NS}><w:r><w:rPr><w:b/><w:rPrChange w:id="13" w:author="{AUTHOR}" {_DATE}>'
    f"<w:rPr/></w:rPrChange></w:rPr><w:t>Bold now.</w:t></w:r></w:p>"
)


def make_docx(path: Path, kinds: list[str], *, deletion: bool = False) -> Path:
    d = docx.Document()
    d.add_paragraph(f"Dear {NAME}, see {EMAIL}.")
    rid = (
        d.part.relate_to(f"mailto:{EMAIL}", RT.HYPERLINK, is_external=True)
        if "hyperlink" in kinds
        else ""
    )
    body = d.element.body
    sect = body[-1]  # w:sectPr stays last
    fragments = _fragments(rid)
    xml = [fragments[k] for k in kinds]
    if deletion:
        xml += [_DELETION, _FORMAT_CHANGE]
    for fragment in xml:
        sect.addprevious(parse_xml(fragment))
    d.save(str(path))
    return path


class _FindAll:
    """Finds NAME and EMAIL wherever they appear (only text starting "Dear" if body_only)."""

    def __init__(self, body_only: bool = False) -> None:
        self.body_only = body_only

    def analyze(
        self,
        text: str,
        language: str = "en",
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[DetectionResult]:
        if self.body_only and not text.startswith("Dear"):
            return []
        out = []
        for value, kind in ((NAME, "PERSON"), (EMAIL, "EMAIL_ADDRESS")):
            for m in re.finditer(re.escape(value), text):
                out.append(
                    DetectionResult(
                        entity_type=kind, start=m.start(), end=m.end(), score=0.9, text_span=value
                    )
                )
        return out


def _engine(body_only: bool = False) -> SanctumEngine:
    return SanctumEngine(analyzer=_FindAll(body_only), anonymizer=PresidioAnonymizer())


def _zip_bytes(path: Path) -> bytes:
    with zipfile.ZipFile(path) as z:
        return b"".join(z.read(n) for n in z.namelist())


ALL_KINDS = ["hyperlink", "ins", "sdt_inline", "smart_tag", "sdt_block"]


def test_reader_reads_runs_inside_wrappers(tmp_path: Path) -> None:
    src = make_docx(tmp_path / "in.docx", ALL_KINDS, deletion=True)
    segs = {s.id: s for s in Reader().read(src).segments}
    texts = [s.text for s in segs.values()]
    assert texts.count(NAME) == 3  # ins, inline sdt, smart tag
    assert EMAIL in texts  # hyperlink
    assert f"Party: {NAME}" in texts  # block-level content control
    # Plain runs keep the ids paragraph.runs would give them; wrapped runs get /n{k}.
    assert segs["body/p1/r0"].text == "Email "
    assert segs["body/p1/n0"].text == EMAIL
    assert segs["body/p1/r1"].text == " today."
    assert segs["body/p1/n0"].block == segs["body/p1/r0"].block == "body/p1"
    assert segs["body/sdt0/p0/r0"].text == f"Party: {NAME}"
    # Deleted text is not a segment: the output accepts the deletion.
    assert segs["body/p5/r0"].text == "Witness: "
    assert segs["body/p5/r1"].text == "none"
    assert not any(t == NAME for i, t in ((i, s.text) for i, s in segs.items()) if "p5" in i)


def test_plain_run_ids_are_unchanged(tmp_path: Path) -> None:
    d = docx.Document()
    para = d.add_paragraph()
    for i, text in enumerate(["one ", "two ", "three"]):
        para.add_run(text).bold = i % 2 == 1
    d.add_table(rows=1, cols=1).cell(0, 0).text = "cell"
    d.sections[0].header.paragraphs[0].text = "head"
    path = tmp_path / "plain.docx"
    d.save(str(path))
    ids = [s.id for s in Reader().read(path).segments]
    assert ids == [
        "body/p0/r0",
        "body/p0/r1",
        "body/p0/r2",
        "table/t0/row0/cell0/p0/r0",
        "hf/s0/header/p0/r0",
    ]


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_wrapped_value_is_redacted_when_detected(tmp_path: Path, kind: str) -> None:
    src = make_docx(tmp_path / "in.docx", [kind])
    out = tmp_path / "out.docx"
    _engine().process_document(Reader(), Writer(), src, out, operator_policies=REPLACE)
    raw = _zip_bytes(out)
    assert NAME.encode() not in raw
    assert EMAIL.encode() not in raw  # also the mailto: relationship target
    docx.Document(str(out))  # still opens


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_wrapped_value_missed_by_detection_fails_closed(tmp_path: Path, kind: str) -> None:
    src = make_docx(tmp_path / "in.docx", [kind])
    out = tmp_path / "out.docx"
    with pytest.raises(LeakCheckError) as exc_info:
        _engine(body_only=True).process_document(
            Reader(), Writer(), src, out, operator_policies=REPLACE
        )
    assert set(exc_info.value.leaks) & {NAME, EMAIL}
    assert not out.exists()


def test_tracked_deletion_and_authors_are_gone_from_the_output(tmp_path: Path) -> None:
    src = make_docx(tmp_path / "in.docx", ["ins"], deletion=True)
    out = tmp_path / "out.docx"
    _engine().process_document(Reader(), Writer(), src, out, operator_policies=REPLACE)
    raw = _zip_bytes(out)
    assert NAME.encode() not in raw
    assert AUTHOR.encode() not in raw
    for tag in (b"<w:del ", b"<w:ins ", b"w:delText", b"w:rPrChange"):
        assert tag not in raw
    texts = [p.text for p in docx.Document(str(out)).paragraphs]
    assert "Witness: none" in texts
    assert "Signed by <PERSON>" in texts
    assert "Bold now." in texts


def test_extract_text_reads_every_text_node_independently_of_the_reader(tmp_path: Path) -> None:
    src = make_docx(tmp_path / "in.docx", ALL_KINDS, deletion=True)
    lines = Writer().extract_text(src).splitlines()
    assert f"Email {EMAIL} today." in lines
    assert f"Signed by {NAME}" in lines
    assert f"Client: {NAME}" in lines
    assert f"Contact {NAME}" in lines
    assert f"Party: {NAME}" in lines
    assert f"Witness: {NAME}none" in lines  # deleted text still counts if it survives


def test_unchanged_runs_keep_inline_pictures(tmp_path: Path) -> None:
    # Setting Run.text clears the run's content; the writer now skips runs whose
    # text did not change, so a picture next to a redacted name survives.
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )
    (tmp_path / "dot.png").write_bytes(png)
    d = docx.Document()
    d.add_paragraph(f"Dear {NAME},")
    d.add_paragraph().add_run().add_picture(str(tmp_path / "dot.png"))
    src = tmp_path / "in.docx"
    d.save(str(src))
    out = tmp_path / "out.docx"
    _engine().process_document(Reader(), Writer(), src, out, operator_policies=REPLACE)
    assert len(docx.Document(str(out)).inline_shapes) == 1
