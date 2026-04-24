"""Shared helpers for the Phase 1.5 review workflow.

Every per-format adapter emits and parses the same HTML-comment-style
trailer so that ``commit-review`` can reconcile decisions uniformly across
``.docx`` / ``.xlsx`` / ``.pptx`` / ``.pdf``. The format is deliberately
line-oriented, ASCII-only, and version-tagged so it survives the
round-tripping that Office apps do when a reviewer saves the file.

Trailer shape:

    <!-- sanctum:v=1 detection_id=<hex12> entity=<TYPE> score=<float>
         original="<escaped>" replacement="<escaped>" operator=<name> -->

(written on a single line — the wrap above is documentation only).

Leakage note: the trailer embeds the original plaintext. For
pseudonymize, that plaintext is sensitive; ``strip_trailers`` is the
last-line defensive sweep during ``commit-review`` finalization.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from sanctum.core.exceptions import StagedMappingParseError
from sanctum.core.models import REVIEW_TRAILER_VERSION

# Re-exported for callers that historically imported it from here (tests,
# and future WS5 comment emission). The canonical home is
# ``sanctum.core.review.identifiers`` so the session surface can reach it
# without the adapter layer crossing into core.
from sanctum.core.review.identifiers import make_detection_id

__all__ = [
    "CommentTrailer",
    "format_comment_body",
    "make_detection_id",
    "parse_trailer",
    "parse_trailers",
    "serialize_trailer",
    "strip_trailers",
]


class CommentTrailer(BaseModel):
    """Payload of one ``<!-- sanctum:... -->`` trailer.

    Distinct from ``CommentTrailer`` because the trailer is an *output*
    artifact of the WS5 native-comment export: it carries the
    post-anonymization replacement and the operator the reviewer
    actually chose, neither of which live on the Flow B review
    proposal (proposals are pure detections; replacement + operator
    live on committed decisions). WS5 builds a ``CommentTrailer`` from
    a session's committed state when it writes a DOCX for interop.
    """

    model_config = {"frozen": True}

    detection_id: str
    entity_type: str
    score: float
    original: str
    replacement: str
    operator: str


_ENTITY_RE = r"[A-Z][A-Z0-9_]*"
_OPERATOR_RE = r"[a-zA-Z][a-zA-Z0-9_]*"
_HEX_RE = r"[0-9a-f]+"

# One compiled regex for parsing a single trailer. Quoted fields use the
# standard ``(?:[^"\\]|\\.)*`` idiom to tolerate escaped quotes/backslashes
# inside the value without the engine backtracking catastrophically.
_TRAILER_RE = re.compile(
    r"<!--\s*sanctum:"
    r"v=(?P<v>\d+)\s+"
    rf"detection_id=(?P<detection_id>{_HEX_RE})\s+"
    rf"entity=(?P<entity>{_ENTITY_RE})\s+"
    r"score=(?P<score>[0-9]+(?:\.[0-9]+)?)\s+"
    r'original="(?P<original>(?:[^"\\]|\\.)*)"\s+'
    r'replacement="(?P<replacement>(?:[^"\\]|\\.)*)"\s+'
    rf"operator=(?P<operator>{_OPERATOR_RE})"
    r"\s*-->"
)

# Deliberately looser: matches *any* sanctum-prefixed HTML comment so that
# future-schema trailers (v=2+) still get stripped. Used only by
# strip_trailers; parse must use the strict regex above.
_STRIP_RE = re.compile(r"[ \t]*<!--\s*sanctum:[^\n]*?-->[ \t]*(?:\r?\n)?")


def _escape(s: str) -> str:
    """Escape into the trailer's quoted-string form.

    Produces ASCII-only output: backslash/quote/newline/CR/tab get C-style
    escapes, and everything else outside printable ASCII is encoded as
    ``\\uXXXX``. That keeps the trailer robust against the Unicode
    normalization that Word / Excel sometimes apply on save.
    """
    buf: list[str] = []
    for ch in s:
        if ch == "\\":
            buf.append("\\\\")
        elif ch == '"':
            buf.append('\\"')
        elif ch == "\n":
            buf.append("\\n")
        elif ch == "\r":
            buf.append("\\r")
        elif ch == "\t":
            buf.append("\\t")
        else:
            cp = ord(ch)
            if 0x20 <= cp <= 0x7E:
                buf.append(ch)
            else:
                buf.append(f"\\u{cp:04x}")
    return "".join(buf)


_UNESCAPE_RE = re.compile(r'\\(?:u([0-9a-fA-F]{4})|(["\\nrt]))')
_SIMPLE_UNESCAPE = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}


def _unescape(s: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        u, simple = match.groups()
        if u is not None:
            return chr(int(u, 16))
        return _SIMPLE_UNESCAPE[simple]

    return _UNESCAPE_RE.sub(_sub, s)


def serialize_trailer(trailer: CommentTrailer) -> str:
    """Render a CommentTrailer as a single-line HTML-comment trailer."""
    return (
        f"<!-- sanctum:v={REVIEW_TRAILER_VERSION} "
        f"detection_id={trailer.detection_id} "
        f"entity={trailer.entity_type} "
        f"score={trailer.score:.4f} "
        f'original="{_escape(trailer.original)}" '
        f'replacement="{_escape(trailer.replacement)}" '
        f"operator={trailer.operator} -->"
    )


def format_comment_body(trailer: CommentTrailer) -> str:
    """Full human-readable comment body with the machine trailer appended.

    The adapter hands this string to the native comment API verbatim; the
    reviewer sees the first two lines and Office silently carries the HTML
    comment through saves.
    """
    return (
        f"Sanctum applied {trailer.operator!r} to {trailer.entity_type} "
        f"(score {trailer.score:.2f}): "
        f'"{trailer.original}" → "{trailer.replacement}".\n'
        f"To reject: restore the original text and delete this comment.\n"
        f"{serialize_trailer(trailer)}"
    )


def parse_trailers(text: str) -> list[CommentTrailer]:
    """Extract every well-formed sanctum trailer from a text block.

    Empty list is a valid result — e.g. for a reviewer-authored comment
    that carries no trailer, or a comment whose trailer was deleted to
    signal rejection. Malformed ``sanctum:`` prefixes are *not* errors
    here: a reviewer might paste a broken fragment of a trailer somewhere,
    and that should show up as user-added, not as a hard parse failure.
    ``parse_trailer`` is the strict variant for contexts that expect
    exactly one trailer.
    """
    results: list[CommentTrailer] = []
    for match in _TRAILER_RE.finditer(text):
        version = int(match.group("v"))
        if version != REVIEW_TRAILER_VERSION:
            raise StagedMappingParseError(
                f"Unsupported sanctum trailer version {version}; "
                f"this build expects v={REVIEW_TRAILER_VERSION}."
            )
        results.append(
            CommentTrailer(
                detection_id=match.group("detection_id"),
                entity_type=match.group("entity"),
                score=float(match.group("score")),
                original=_unescape(match.group("original")),
                replacement=_unescape(match.group("replacement")),
                operator=match.group("operator"),
            )
        )
    return results


def parse_trailer(text: str) -> CommentTrailer:
    """Parse exactly one trailer out of ``text``.

    Raises ``StagedMappingParseError`` if zero or more than one trailer is
    found. Use this from call sites that own a single comment body and
    want a crisp error on malformed input; use ``parse_trailers`` from
    call sites (like XLSX) that concatenate multiple detections into one
    comment.
    """
    trailers = parse_trailers(text)
    if len(trailers) == 0:
        raise StagedMappingParseError("No sanctum trailer found in text.")
    if len(trailers) > 1:
        raise StagedMappingParseError(
            f"Expected exactly one sanctum trailer, found {len(trailers)}."
        )
    return trailers[0]


def strip_trailers(text: str) -> str:
    """Remove every sanctum-prefixed HTML comment from ``text``.

    Defensive sweep run by ``commit-review`` as the final file is written.
    Matches *any* ``<!-- sanctum:... -->`` — including malformed or future
    schemas — so a schema drift doesn't leave leaked trailers in the
    shareable artifact. Non-sanctum HTML comments pass through
    untouched.
    """
    return _STRIP_RE.sub("", text)
