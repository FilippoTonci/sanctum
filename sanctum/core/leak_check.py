"""Post-write leak check.

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
- Bare numbers of one or two digits ("14", "7") are ignored: they would match
  unrelated numbers such as a house number. Three or more digits, and mixed
  tokens such as "4B", are still checked.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sanctum.core.exceptions import LeakCheckError

_WS = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _pattern_for(needle: str) -> re.Pattern[str] | None:
    """Compiled matcher for one normalized original, or None if it is exempt."""
    compact = needle.replace(" ", "")
    if len(compact) < 2:
        return None
    if compact.isdigit() and len(compact) <= 2:
        return None  # a stray "14" must not match "14 Harbour Lane"
    pattern = re.escape(needle)
    if needle[0].isalnum():
        pattern = r"(?<![^\W_])" + pattern
    if needle[-1].isalnum():
        pattern = pattern + r"(?![^\W_])"
    return re.compile(pattern)


def count_surviving_originals(output_text: str, originals: Iterable[str]) -> dict[str, int]:
    """Original -> number of times it still appears (only originals that appear)."""
    haystack = normalize_whitespace(output_text)
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for original in originals:
        needle = normalize_whitespace(original)
        if needle in seen:
            continue
        seen.add(needle)
        matcher = _pattern_for(needle)
        if matcher is None:
            continue
        n = len(matcher.findall(haystack))
        if n:
            counts[original] = n
    return counts


def find_surviving_originals(output_text: str, originals: Iterable[str]) -> list[str]:
    """Return the originals (deduplicated, input order) found in ``output_text``."""
    return list(count_surviving_originals(output_text, originals))


def verify_no_leaks(output_text: str, originals: Iterable[str], *, where: str) -> None:
    """Raise :class:`LeakCheckError` if any original survives in ``output_text``.

    The message names only how many values leaked, never the values
    themselves, so it is safe to log; the values are on ``exc.leaks``.
    """
    counts = count_surviving_originals(output_text, originals)
    leaks = list(counts)
    if leaks:
        raise LeakCheckError(
            f"Leak check failed for {where}: {len(leaks)} replaced value(s) still "
            "appear in the written output. The output was deleted; add a manual "
            "redaction for the remaining occurrence(s) and commit again.",
            leaks=leaks,
            occurrences=counts,
        )
