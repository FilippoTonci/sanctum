from __future__ import annotations

from pathlib import Path

import pytest
from sanctum.analyzer.gliner_onnx import GlinerEntity
from sanctum.analyzer.gliner_recognizer import (
    KNOWLEDGATOR_PROMPTS,
    GlinerOnnxRecognizer,
    chunk_text,
)


class FakeModel:
    """Finds fixed substrings in each chunk; records what it was asked."""

    def __init__(self, hits: dict[str, tuple[str, float]]) -> None:
        self.hits = hits  # substring -> (label, score)
        self.calls: list[tuple[str, list[str], float]] = []

    def predict(self, text: str, labels: list[str], threshold: float) -> list[GlinerEntity]:
        self.calls.append((text, labels, threshold))
        out = []
        for needle, (label, score) in self.hits.items():
            i = text.find(needle)
            if i >= 0 and label in labels and score > threshold:
                out.append(GlinerEntity(i, i + len(needle), label, score))
        return out


def _rec(hits: dict[str, tuple[str, float]], **kw: object) -> GlinerOnnxRecognizer:
    return GlinerOnnxRecognizer(Path("/unused"), model=FakeModel(hits), **kw)


def test_maps_labels_to_sanctum_types_with_absolute_offsets() -> None:
    text = "Line one.\n" + "x" * 995 + "\nPassport 553219876 for Jane Doe."
    rec = _rec({"553219876": ("passport number", 0.6), "Jane Doe": ("name", 0.9)})
    results = rec.analyze(text, entities=["PERSON", "ID_NUMBER"])
    found = {(r.entity_type, text[r.start : r.end]) for r in results}
    assert found == {("ID_NUMBER", "553219876"), ("PERSON", "Jane Doe")}
    assert all(r.recognition_metadata["recognizer_name"] == "GlinerOnnxRecognizer" for r in results)


def test_only_prompts_for_requested_entities_are_sent() -> None:
    rec = _rec({})
    rec.analyze("Some text", entities=["LOCATION"])
    (_, labels, threshold) = rec._model.calls[0]
    assert labels == ["location address", "location city", "location country"]
    assert threshold == 0.2


def test_no_requested_entity_supported_skips_the_model() -> None:
    rec = _rec({})
    assert rec.analyze("Some text", entities=["CRYPTO"]) == []
    assert rec._model.calls == []


def test_empty_entities_means_everything() -> None:
    rec = _rec({})
    rec.analyze("Some text", entities=[])
    assert rec._model.calls[0][1] == list(KNOWLEDGATOR_PROMPTS)


def test_scores_rescaled_onto_presidio_floor() -> None:
    rec = _rec({"Ann": ("name", 0.2 + 1e-9), "Bob": ("name", 0.6), "Cy": ("name", 1.0)})
    by_text = {r.start: r.score for r in rec.analyze("Ann Bob Cy", entities=["PERSON"])}
    assert by_text[0] == pytest.approx(0.35)
    assert by_text[4] == pytest.approx(0.35 + 0.65 * 0.5)
    assert by_text[8] == pytest.approx(1.0)


def test_custom_floor_and_threshold() -> None:
    rec = _rec({"Bob": ("name", 0.55)}, threshold=0.1, score_floor=0.5)
    (r,) = rec.analyze("Bob", entities=["PERSON"])
    assert r.score == pytest.approx(0.5 + 0.5 * (0.45 / 0.9))


def test_spans_trimmed_of_punctuation_and_dropped_if_empty() -> None:
    text = '"Acme Ltd." said (".")'
    rec = _rec({'"Acme Ltd."': ("organization", 0.9), '"."': ("name", 0.9)})
    results = rec.analyze(text, entities=["ORGANIZATION", "PERSON"])
    assert [text[r.start : r.end] for r in results] == ["Acme Ltd"]


def test_invalid_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="threshold"):
        GlinerOnnxRecognizer(Path("/unused"), threshold=0.0, model=FakeModel({}))


def test_supported_entities_include_id_number() -> None:
    rec = _rec({})
    assert "ID_NUMBER" in rec.supported_entities
    assert "PERSON" in rec.supported_entities


def test_load_builds_model_from_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[Path] = []

    class Stub:
        def __init__(self, model_dir: Path) -> None:
            built.append(model_dir)

    monkeypatch.setattr("sanctum.analyzer.gliner_recognizer.GlinerOnnx", Stub)
    rec = GlinerOnnxRecognizer(Path("/models/kg"))
    assert built == [Path("/models/kg")]
    assert isinstance(rec._model, Stub)


class TestChunkText:
    def test_short_text_is_one_chunk(self) -> None:
        assert chunk_text("a\nb") == [(0, "a\nb")]

    def test_packs_lines_up_to_limit(self) -> None:
        text = "aaaa\nbbbb\ncccc"
        assert chunk_text(text, max_chars=9) == [(0, "aaaa\nbbbb"), (10, "cccc")]

    def test_long_line_split_at_sentence_end(self) -> None:
        text = "One two. Three four five."
        chunks = chunk_text(text, max_chars=12)
        assert chunks[0] == (0, "One two. ")
        assert "".join(c for _, c in chunks) == text

    def test_long_line_without_sentence_end_split_hard(self) -> None:
        text = "x" * 25
        assert [len(c) for _, c in chunk_text(text, max_chars=10)] == [10, 10, 5]

    def test_offsets_index_the_original(self) -> None:
        text = "\n\nfirst line\n" + "y" * 30 + "\nlast"
        for off, chunk in chunk_text(text, max_chars=20):
            assert text[off : off + len(chunk)] == chunk

    def test_blank_text(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("\n\n") == []
