"""Unit tests for the shared review-trailer helpers."""

from __future__ import annotations

import pytest
from sanctum.core.exceptions import StagedMappingParseError
from sanctum.core.models import REVIEW_TRAILER_VERSION, ReviewProposal
from sanctum.documents.review import (
    format_comment_body,
    make_detection_id,
    parse_trailer,
    parse_trailers,
    serialize_trailer,
    strip_trailers,
)


def _proposal(**overrides: object) -> ReviewProposal:
    defaults = {
        "detection_id": "abcdef012345",
        "entity_type": "PERSON",
        "score": 0.92,
        "original": "John Smith",
        "replacement": "[PERSON_1]",
        "operator": "replace",
    }
    defaults.update(overrides)
    return ReviewProposal(**defaults)  # type: ignore[arg-type]


# --- detection id ---------------------------------------------------------


def test_detection_id_is_deterministic():
    a = make_detection_id("PERSON", "John Smith", 42)
    b = make_detection_id("PERSON", "John Smith", 42)
    assert a == b
    assert len(a) == 12
    assert all(ch in "0123456789abcdef" for ch in a)


def test_detection_id_differs_on_position():
    a = make_detection_id("PERSON", "John Smith", 42)
    b = make_detection_id("PERSON", "John Smith", 43)
    assert a != b


def test_detection_id_differs_on_entity_type():
    a = make_detection_id("PERSON", "Acme", 0)
    b = make_detection_id("ORG", "Acme", 0)
    assert a != b


def test_detection_id_accepts_string_position():
    """Adapters may pass a segment id, not a numeric offset."""
    a = make_detection_id("PERSON", "x", "body/p0/r1:0")
    b = make_detection_id("PERSON", "x", "body/p0/r1:1")
    assert a != b


# --- serialize / parse round-trip ----------------------------------------


def test_trailer_round_trip_basic():
    comment = _proposal()
    parsed = parse_trailer(serialize_trailer(comment))
    # score is serialized with 4 decimal places — compare with tolerance.
    assert parsed.detection_id == comment.detection_id
    assert parsed.entity_type == comment.entity_type
    assert parsed.original == comment.original
    assert parsed.replacement == comment.replacement
    assert parsed.operator == comment.operator
    assert parsed.score == pytest.approx(comment.score, abs=1e-3)


def test_trailer_round_trip_with_quote_in_name():
    """O'Brien-class: apostrophes need no escape; double-quotes do."""
    comment = _proposal(original='He said "hi".', replacement="[PERSON_1]")
    parsed = parse_trailer(serialize_trailer(comment))
    assert parsed.original == 'He said "hi".'


def test_trailer_round_trip_with_backslash():
    comment = _proposal(original=r"path\to\file", replacement="[REDACTED]")
    parsed = parse_trailer(serialize_trailer(comment))
    assert parsed.original == r"path\to\file"


def test_trailer_round_trip_with_newlines():
    """Original may contain newlines — trailer must stay single-line."""
    comment = _proposal(original="line1\nline2", replacement="[REDACTED]")
    trailer = serialize_trailer(comment)
    assert "\n" not in trailer
    parsed = parse_trailer(trailer)
    assert parsed.original == "line1\nline2"


def test_trailer_round_trip_with_cr_and_tab():
    """CR + tab get C-style escapes so the trailer stays single-line."""
    comment = _proposal(original="col1\tcol2\rend", replacement="[REDACTED]")
    trailer = serialize_trailer(comment)
    assert "\n" not in trailer and "\r" not in trailer and "\t" not in trailer
    parsed = parse_trailer(trailer)
    assert parsed.original == "col1\tcol2\rend"


def test_trailer_round_trip_with_unicode():
    """Non-ASCII encodes as \\uXXXX so the trailer stays ASCII-only."""
    comment = _proposal(original="Zoë Müller", replacement="[PERSON_1]")
    trailer = serialize_trailer(comment)
    assert trailer.isascii()
    parsed = parse_trailer(trailer)
    assert parsed.original == "Zoë Müller"


def test_trailer_embeds_configured_version():
    trailer = serialize_trailer(_proposal())
    assert f"v={REVIEW_TRAILER_VERSION}" in trailer


# --- version mismatch ----------------------------------------------------


def test_parse_rejects_unknown_version():
    bad = (
        "<!-- sanctum:v=99 detection_id=abcdef012345 entity=PERSON "
        'score=0.9000 original="x" replacement="y" operator=replace -->'
    )
    with pytest.raises(StagedMappingParseError, match="version"):
        parse_trailer(bad)


# --- parse_trailer / parse_trailers contracts ----------------------------


def test_parse_trailer_raises_when_empty():
    with pytest.raises(StagedMappingParseError, match="No sanctum trailer"):
        parse_trailer("just a reviewer comment, no trailer")


def test_parse_trailer_raises_when_multiple():
    text = "\n".join([serialize_trailer(_proposal()), serialize_trailer(_proposal())])
    with pytest.raises(StagedMappingParseError, match="Expected exactly one"):
        parse_trailer(text)


def test_parse_trailers_returns_empty_on_plain_text():
    assert parse_trailers("a reviewer wrote this, no trailer") == []


def test_parse_trailers_extracts_multiple_from_one_block():
    """XLSX packs all detections for a cell into one comment body."""
    a = _proposal(detection_id="aaaaaaaaaaaa", original="Alice")
    b = _proposal(detection_id="bbbbbbbbbbbb", original="Bob", entity_type="PERSON")
    block = f"Some prose.\n{serialize_trailer(a)}\n\nMore prose.\n{serialize_trailer(b)}"
    parsed = parse_trailers(block)
    assert [p.detection_id for p in parsed] == [a.detection_id, b.detection_id]


# --- format_comment_body -------------------------------------------------


def test_format_comment_body_includes_human_text_and_trailer():
    comment = _proposal()
    body = format_comment_body(comment)
    assert comment.entity_type in body
    assert comment.original in body
    assert comment.replacement in body
    assert "sanctum:v=" in body
    # Round-trip: the trailer inside the body must still parse.
    parsed = parse_trailer(body)
    assert parsed.detection_id == comment.detection_id


# --- strip_trailers ------------------------------------------------------


def test_strip_removes_trailer_and_nothing_else():
    body = format_comment_body(_proposal())
    stripped = strip_trailers(body)
    assert "sanctum:" not in stripped
    assert "Sanctum applied" in stripped


def test_strip_preserves_non_sanctum_html_comments():
    """Reviewer-authored comments with HTML-comment-like content stay intact."""
    text = (
        "Reviewer note: <!-- this is not a sanctum marker -->\n"
        f"{serialize_trailer(_proposal())}\n"
        "<!-- another reviewer note -->"
    )
    stripped = strip_trailers(text)
    assert "this is not a sanctum marker" in stripped
    assert "another reviewer note" in stripped
    assert "sanctum:" not in stripped


def test_strip_handles_future_schema_trailer():
    """strip must be permissive: v=99 trailers also get removed on commit."""
    future = "<!-- sanctum:v=99 foo=bar -->"
    assert strip_trailers(f"body\n{future}") == "body\n"


def test_strip_is_idempotent():
    body = format_comment_body(_proposal())
    once = strip_trailers(body)
    twice = strip_trailers(once)
    assert once == twice
