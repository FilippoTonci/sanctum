from __future__ import annotations

from sanctum.core.blocks import detect_blocks
from sanctum.core.models import DetectionResult, TextSegment
from sanctum.core.propagation import (
    PROPAGATION_RECOGNIZER,
    Seed,
    collect_seeds,
    find_mentions,
    propagate,
)


def _det(text: str, needle: str, etype: str = "PERSON", score: float = 0.8) -> DetectionResult:
    start = text.index(needle)
    return DetectionResult(
        entity_type=etype, start=start, end=start + len(needle), score=score, text_span=needle
    )


def _spans(text: str, dets: list[DetectionResult]) -> list[tuple[str, str]]:
    return [(text[d.start : d.end], d.entity_type) for d in dets]


class TestCollectSeeds:
    def test_person_full_name_and_tokens_without_titles(self) -> None:
        seeds = collect_seeds([("PERSON", "Dr Mark van Price", 0.9)])
        assert set(seeds) == {"dr mark van price", "mark", "price"}

    def test_short_person_tokens_skipped(self) -> None:
        assert set(collect_seeds([("PERSON", "Al Wu", 0.9)])) == {"al wu"}

    def test_organisation_distinctive_first_word(self) -> None:
        seeds = collect_seeds([("ORGANIZATION", "Verdana Pharmaceuticals Ltd", 0.7)])
        assert set(seeds) == {"verdana pharmaceuticals ltd", "verdana"}

    def test_organisation_generic_or_short_first_word_not_split(self) -> None:
        assert set(collect_seeds([("ORGANIZATION", "First Capital Bank", 0.7)])) == {
            "first capital bank"
        }
        assert set(collect_seeds([("ORGANIZATION", "IBM Corp", 0.7)])) == {"ibm corp"}
        assert set(collect_seeds([("ORGANIZATION", "Acme", 0.7)])) == {"acme"}

    def test_other_types_ignored(self) -> None:
        assert collect_seeds([("LOCATION", "Rome", 0.9), ("EMAIL_ADDRESS", "a@b.c", 0.9)]) == {}

    def test_higher_score_decides_type(self) -> None:
        seeds = collect_seeds([("ORGANIZATION", "Jordan", 0.5), ("PERSON", "Jordan", 0.9)])
        assert seeds["jordan"] == Seed("PERSON", 0.9, frozenset({"Jordan"}))
        seeds = collect_seeds([("PERSON", "Jordan", 0.9), ("ORGANIZATION", "JORDAN", 0.5)])
        assert seeds["jordan"] == Seed("PERSON", 0.9, frozenset({"Jordan", "JORDAN"}))


class TestFindMentions:
    def test_longest_seed_wins(self) -> None:
        text = "Mark Price signed. Price agreed."
        seeds = collect_seeds([("PERSON", "Mark Price", 0.9)])
        found = [(text[m.start : m.end]) for m in find_mentions(text, seeds, [(0, 10)])]
        assert found == ["Price"]

    def test_whole_words_only(self) -> None:
        text = "Price and Pricewaterhouse and price-fixing"
        seeds = {"price": Seed("PERSON", 0.9)}
        found = [text[m.start : m.end] for m in find_mentions(text, seeds, [])]
        assert found == ["Price"]

    def test_single_word_must_look_like_a_name(self) -> None:
        text = "Will Smith said he will sign. WILL agreed."
        seeds = collect_seeds([("PERSON", "Will Smith", 0.9)])
        found = [text[m.start : m.end] for m in find_mentions(text, seeds, [(0, 10)])]
        assert found == ["WILL"]

    def test_verbatim_repeat_of_a_lowercase_hit_is_marked(self) -> None:
        # The leak check is case-sensitive: if "parties" was replaced once, every
        # other "parties" would fail the document, so propagation marks them.
        text = "WHEREAS the parties agree. The Parties sign. Both parties."
        seeds = collect_seeds([("ORGANIZATION", "parties", 0.4)])
        found = [text[m.start : m.end] for m in find_mentions(text, seeds, [(12, 19)])]
        assert found == ["Parties", "parties"]

    def test_lowercase_chat_allows_lowercase_names(self) -> None:
        text = "ok so dwayne said he'd call back later"
        seeds = {"dwayne": Seed("PERSON", 0.9)}
        assert [text[m.start : m.end] for m in find_mentions(text, seeds, [])] == ["dwayne"]

    def test_multi_word_seed_matches_case_insensitively(self) -> None:
        text = "JANE DOE, the claimant"
        seeds = {"jane doe": Seed("PERSON", 0.9)}
        assert [(m.start, m.end) for m in find_mentions(text, seeds, [])] == [(0, 8)]

    def test_covered_spans_are_not_reused(self) -> None:
        text = "Contact Jane at jane@x.com"
        seeds = {"jane": Seed("PERSON", 0.9)}
        assert [(m.start, m.end) for m in find_mentions(text, seeds, [(16, 26)])] == [(8, 12)]


class TestPropagate:
    def test_adds_repeat_mentions_with_seed_type_and_score(self) -> None:
        text = "Dwayne Kowalczyk joined. Later, Dwayne and Mr Kowalczyk left."
        out = propagate(text, [_det(text, "Dwayne Kowalczyk", score=0.7)])
        assert _spans(text, out) == [
            ("Dwayne Kowalczyk", "PERSON"),
            ("Dwayne", "PERSON"),
            ("Kowalczyk", "PERSON"),
        ]
        assert [d.score for d in out] == [0.7, 0.7, 0.7]
        assert out[1].recognizer_name == PROPAGATION_RECOGNIZER
        assert "Later, Dwayne" in out[1].context

    def test_no_seeds_returns_input(self) -> None:
        text = "Email a@b.co"
        dets = [_det(text, "a@b.co", "EMAIL_ADDRESS")]
        assert propagate(text, dets) == dets

    def test_existing_overlaps_untouched(self) -> None:
        text = "Verdana Pharmaceuticals and Verdana Labs"
        dets = [
            _det(text, "Verdana Pharmaceuticals", "ORGANIZATION"),
            _det(text, "Verdana Labs", "ORGANIZATION"),
        ]
        assert propagate(text, dets) == sorted(dets, key=lambda d: d.start)


class TestDocumentLevel:
    @staticmethod
    def _seg(i: int, text: str) -> TextSegment:
        return TextSegment(id=f"p{i}", text=text, block=f"p{i}")

    def test_name_from_one_block_found_in_another(self) -> None:
        segs = [
            self._seg(0, "Agreement with Oluwaseun Adeyemi."),
            self._seg(1, "Adeyemi will report weekly."),
            self._seg(2, "Nothing here."),
        ]

        def analyze(text: str) -> list[DetectionResult]:
            return [_det(text, "Oluwaseun Adeyemi")] if "Oluwaseun" in text else []

        findings = detect_blocks(segs, analyze)
        assert [(f.original, f.pieces[0].segment_id) for f in findings] == [
            ("Oluwaseun Adeyemi", "p0"),
            ("Adeyemi", "p1"),
        ]
        assert findings[1].entity_type == "PERSON"

    def test_propagates_backwards_and_keeps_document_order(self) -> None:
        segs = [self._seg(0, "Kim met Lee Park."), self._seg(1, "Park thanked Kim Cho.")]

        def analyze(text: str) -> list[DetectionResult]:
            if text.startswith("Kim met"):
                return [_det(text, "Lee Park")]
            return [_det(text, "Kim Cho")]

        findings = detect_blocks(segs, analyze)
        assert [(f.original, f.pieces[0].segment_id) for f in findings] == [
            ("Kim", "p0"),  # seeded by "Kim Cho" in the *next* block
            ("Lee Park", "p0"),
            ("Park", "p1"),
            ("Kim Cho", "p1"),
        ]
