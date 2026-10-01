"""Post-write leak check (Phase 3.5 WS0.2, prototyped in WS3).

After a document is written, re-extract every piece of text from the
output and make sure no replaced original survives. The *matching* logic
lives here in the core (pure stdlib) so every format shares one
definition of "survives"; *text extraction* is format-specific and lives
in the adapters behind the :class:`~sanctum.core.protocols.OutputTextExtractor`
port. The engine wires the two together after ``writer.write``.

Matching rules:

- Whitespace is normalized on both sides (any run of whitespace, including
  line breaks, becomes one space), so an original that the output wraps
  across two lines is still caught.
- Matching is case-sensitive (case-insensitive matching turns common-word
  names like "Mark" into constant false positives).
- If an original starts/ends with a letter or digit, the match must not be
  glued to another letter/digit on that side, so "Ann" does not match
  "Annual". Originals shorter than 2 non-space characters are ignored.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sanctum.core.exceptions import LeakCheckError

_WS = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    return _WS.sub(" ", text).strip()


def find_surviving_originals(output_text: str, originals: Iterable[str]) -> list[str]:
    """Return the originals (deduplicated, input order) found in ``output_text``."""
    haystack = normalize_whitespace(output_text)
    found: list[str] = []
    seen: set[str] = set()
    for original in originals:
        needle = normalize_whitespace(original)
        if len(needle.replace(" ", "")) < 2 or needle in seen:
            continue
        seen.add(needle)
        pattern = re.escape(needle)
        if needle[0].isalnum():
            pattern = r"(?<![^\W_])" + pattern
        if needle[-1].isalnum():
            pattern = pattern + r"(?![^\W_])"
        if re.search(pattern, haystack):
            found.append(original)
    return found


def verify_no_leaks(output_text: str, originals: Iterable[str], *, where: str) -> None:
    """Raise :class:`LeakCheckError` if any original survives in ``output_text``.

    The message names only how many values leaked, never the values
    themselves, so it is safe to log; the values are on ``exc.leaks``.
    """
    leaks = find_surviving_originals(output_text, originals)
    if leaks:
        raise LeakCheckError(
            f"Leak check failed for {where}: {len(leaks)} replaced value(s) still "
            "appear in the written output. The output was deleted; add a manual "
            "redaction for the remaining occurrence(s) and commit again.",
            leaks=leaks,
        )
