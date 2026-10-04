"""Unit tests for the format-agnostic post-write leak check."""

from __future__ import annotations

import pytest
from sanctum.core.exceptions import DocumentError, LeakCheckError
from sanctum.core.leak_check import (
    count_surviving_originals,
    find_surviving_originals,
    verify_no_leaks,
)


def test_finds_exact_survivor() -> None:
    assert find_surviving_originals("Dear Jane Doe,", ["Jane Doe"]) == ["Jane Doe"]


def test_clean_output_has_no_survivors() -> None:
    assert find_surviving_originals("Dear <PERSON>,", ["Jane Doe"]) == []


def test_whitespace_and_line_breaks_are_normalized() -> None:
    assert find_surviving_originals("signed by Jane\n  Doe today", ["Jane Doe"]) == ["Jane Doe"]
    assert find_surviving_originals("Jane Doe", ["Jane\tDoe"]) == ["Jane\tDoe"]


def test_word_boundaries_prevent_false_positives() -> None:
    assert find_surviving_originals("Annual report", ["Ann"]) == []
    assert find_surviving_originals("to Ann.", ["Ann"]) == ["Ann"]
    assert find_surviving_originals("id_Ann", ["Ann"]) == ["Ann"]


def test_non_alnum_edges_match_anywhere() -> None:
    # Emails and phone numbers start/end with alnum; a trailing '.' in the
    # original does not demand a boundary.
    assert find_surviving_originals("mail x@y.org.", ["x@y.org"]) == ["x@y.org"]
    assert find_surviving_originals("(+44) 20", ["(+44)"]) == ["(+44)"]


def test_case_sensitive() -> None:
    assert find_surviving_originals("jane doe", ["Jane Doe"]) == []


def test_ignores_trivial_and_duplicate_originals() -> None:
    assert find_surviving_originals("a b", ["a", " ", ""]) == []
    assert find_surviving_originals("Jane Doe", ["Jane Doe", "Jane Doe"]) == ["Jane Doe"]


def test_verify_raises_without_leaking_values_in_message() -> None:
    with pytest.raises(LeakCheckError) as info:
        verify_no_leaks("Dear Jane Doe", ["Jane Doe", "Bob"], where="out.pdf")
    assert info.value.leaks == ["Jane Doe"]
    assert "Jane Doe" not in str(info.value)
    assert "1 replaced value" in str(info.value)
    assert isinstance(info.value, DocumentError)


def test_verify_passes_on_clean_output() -> None:
    verify_no_leaks("Dear <PERSON>", ["Jane Doe"], where="out.pdf")


def test_bare_two_digit_numbers_are_not_checked() -> None:
    assert find_surviving_originals("14 Harbour Lane", ["14"]) == []
    assert find_surviving_originals("Unit 7", ["7"]) == []


def test_three_digit_numbers_and_mixed_tokens_are_still_checked() -> None:
    assert find_surviving_originals("Room 214", ["214"]) == ["214"]
    assert find_surviving_originals("Flat 4B", ["4B"]) == ["4B"]


def test_occurrences_are_counted_per_original() -> None:
    text = "Priya met Priya Raghunathan. Raghunathan left."
    assert count_surviving_originals(text, ["Priya", "Raghunathan", "Absent"]) == {
        "Priya": 2,
        "Raghunathan": 2,
    }
