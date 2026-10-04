from __future__ import annotations

from sanctum.core.blocks import Piece, block_texts, detect_blocks, fragment_words, splice
from sanctum.core.models import DetectionResult, TextSegment


def seg(sid: str, text: str, block: str | None, join: str = "") -> TextSegment:
    return TextSegment(id=sid, text=text, block=block, join_before=join)


def fake_analyze(entity: str, needle: str):  # type: ignore[no-untyped-def]
    def analyze(text: str) -> list[DetectionResult]:
        i = text.find(needle)
        if i < 0:
            return []
        return [
            DetectionResult(
                entity_type=entity, start=i, end=i + len(needle), score=0.9, text_span=needle
            )
        ]

    return analyze


def test_name_split_across_runs_becomes_one_finding_with_pieces() -> None:
    segs = [
        seg("p0/r0", "Dear Jen", "p0"),
        seg("p0/r1", "nifer Mar", "p0"),
        seg("p0/r2", "tin,", "p0"),
    ]
    [f] = detect_blocks(segs, fake_analyze("PERSON", "Jennifer Martin"))
    assert f.original == "Jennifer Martin"
    assert f.pieces == (
        Piece("p0/r0", 5, 8, "Jen"),
        Piece("p0/r1", 0, 9, "nifer Mar"),
        Piece("p0/r2", 0, 3, "tin"),
    )


def test_whitespace_only_middle_run_is_a_piece() -> None:
    segs = [seg("p0/r0", "Jennifer", "p0"), seg("p0/r1", " ", "p0"), seg("p0/r2", "Martin", "p0")]
    [f] = detect_blocks(segs, fake_analyze("PERSON", "Jennifer Martin"))
    assert [p.segment_id for p in f.pieces] == ["p0/r0", "p0/r1", "p0/r2"]


def test_join_before_text_is_not_part_of_any_piece() -> None:
    segs = [
        seg("page0/line3", "Dr Evelyn", "page0/para1"),
        seg("page0/line4", "Marchetti said", "page0/para1", " "),
    ]
    [f] = detect_blocks(segs, fake_analyze("PERSON", "Evelyn Marchetti"))
    assert f.original == "Evelyn Marchetti"
    assert [p.text for p in f.pieces] == ["Evelyn", "Marchetti"]


def test_segments_without_block_are_analysed_alone() -> None:
    segs = [seg("a", "Jennifer", None), seg("b", " Martin", None)]
    assert detect_blocks(segs, fake_analyze("PERSON", "Jennifer Martin")) == []


def test_blocks_group_by_key_not_adjacency() -> None:
    # two-column PDF order: left line, right line, left line
    segs = [
        seg("l0", "Evelyn", "colA", ""),
        seg("r0", "Total", "colB"),
        seg("l1", "Marchetti", "colA", " "),
    ]
    [f] = detect_blocks(segs, fake_analyze("PERSON", "Evelyn Marchetti"))
    assert [p.segment_id for p in f.pieces] == ["l0", "l1"]


def test_splice_applies_edits_right_to_left() -> None:
    assert splice("Dear Jen", [(5, 8, "<PERSON>")]) == "Dear <PERSON>"
    assert splice("abc", [(0, 1, "X"), (2, 3, "")]) == "Xb"


def test_fragment_words_are_whole_words_shared_with_the_finding() -> None:
    assert fragment_words("Jennifer", "Jennifer Martin") == ["Jennifer"]
    assert fragment_words(" ", "Jennifer Martin") == []
    assert fragment_words("Martin", "Jennifer Martin") == ["Martin"]
    # cut mid-word by run boundaries: nothing new to look for
    assert fragment_words("Jen", "Jennifer Martin") == []
    assert fragment_words("nifer Mar", "Jennifer Martin") == []
    assert fragment_words("tin", "Jennifer Martin") == []
    # words under three characters are left out
    assert fragment_words("Li Wei", "Li Wei") == ["Wei"]


def test_block_texts_join_each_block_with_join_before_in_document_order() -> None:
    segments = [
        seg("p0/r0", "Dear Jen", "p0"),
        seg("p0/r1", "nifer", "p0"),
        seg("solo", "alone", None),
        seg("l0", "first line", "para"),
        seg("l1", "second line", "para", join=" "),
    ]
    assert block_texts(segments) == ["Dear Jennifer", "alone", "first line second line"]
