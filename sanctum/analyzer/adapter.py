from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from presidio_analyzer import AnalyzerEngine
from sanctum.core.models import DetectionResult


class PresidioAnalyzer:
    """Wraps presidio-analyzer's AnalyzerEngine for PII detection."""

    def __init__(
        self,
        nlp_engine: Any = None,
        registry: Any = None,
        default_score_threshold: float = 0.35,
        default_language: str = "en",
        extra_recognizers: list[Any] | None = None,
        remove_recognizer_names: list[str] | None = None,
    ) -> None:
        """Build an `AnalyzerEngine` and optionally reshape its recognizer registry.

        `extra_recognizers` are appended post-construction so they share the
        same registry as the predefined set. `remove_recognizer_names` drops
        recognizers by their registered `name` — the intended use is pairing
        a GLiNER recognizer with `["SpacyRecognizer"]` to make GLiNER the
        default NER while leaving every pattern/context recognizer in place.
        """
        kwargs: dict = {}
        if nlp_engine is not None:
            kwargs["nlp_engine"] = nlp_engine
        if registry is not None:
            kwargs["registry"] = registry
        self._engine = AnalyzerEngine(**kwargs)

        for recognizer in extra_recognizers or []:
            self._engine.registry.add_recognizer(recognizer)
        for name in remove_recognizer_names or []:
            self._engine.registry.remove_recognizer(name)

        self._default_threshold = default_score_threshold
        self._default_language = default_language

    def analyze(
        self,
        text: str,
        language: str = "en",
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[DetectionResult]:
        raw_results = self._engine.analyze(
            text=text,
            language=language,
            entities=entities,
            score_threshold=score_threshold or self._default_threshold,
        )

        detections: list[DetectionResult] = []
        for r in raw_results:
            text_span = text[r.start : r.end]
            ctx_start = max(0, r.start - 40)
            ctx_end = min(len(text), r.end + 40)
            context = text[ctx_start:ctx_end]
            recognizer = r.recognition_metadata.get("recognizer_name", "")

            detections.append(
                DetectionResult(
                    entity_type=r.entity_type,
                    start=r.start,
                    end=r.end,
                    score=r.score,
                    text_span=text_span,
                    context=context,
                    recognizer_name=recognizer,
                )
            )

        return self._normalize_overlaps(detections, text)

    @staticmethod
    def _normalize_overlaps(
        detections: Sequence[DetectionResult],
        text: str,
    ) -> list[DetectionResult]:
        """Resolve overlaps so downstream anonymization sees disjoint spans.

        If one detection fully contains another, keep only the enclosing span.
        Otherwise, assign the overlapping region to the larger span and trim
        the smaller one so the final list is non-overlapping.
        """
        normalized = sorted(detections, key=lambda d: (d.start, d.end, -d.score))

        changed = True
        while changed:
            changed = False
            for i in range(len(normalized)):
                for j in range(i + 1, len(normalized)):
                    first = normalized[i]
                    second = normalized[j]
                    if first.end <= second.start:
                        break
                    if not PresidioAnalyzer._overlaps(first, second):
                        continue

                    replacement = PresidioAnalyzer._resolve_overlap(first, second, text)
                    normalized[i : j + 1] = replacement
                    normalized.sort(key=lambda d: (d.start, d.end, -d.score))
                    changed = True
                    break
                if changed:
                    break

        return normalized

    @staticmethod
    def _overlaps(first: DetectionResult, second: DetectionResult) -> bool:
        return first.start < second.end and second.start < first.end

    @staticmethod
    def _resolve_overlap(
        first: DetectionResult,
        second: DetectionResult,
        text: str,
    ) -> list[DetectionResult]:
        if PresidioAnalyzer._contains(first, second):
            return [first]
        if PresidioAnalyzer._contains(second, first):
            return [second]

        winner, loser = PresidioAnalyzer._pick_winner(first, second)
        trimmed_loser = PresidioAnalyzer._trim_overlap(loser, winner, text)
        if trimmed_loser is None:
            return [winner]
        return sorted([winner, trimmed_loser], key=lambda d: (d.start, d.end, -d.score))

    @staticmethod
    def _contains(container: DetectionResult, containee: DetectionResult) -> bool:
        return container.start <= containee.start and containee.end <= container.end

    @staticmethod
    def _pick_winner(
        first: DetectionResult,
        second: DetectionResult,
    ) -> tuple[DetectionResult, DetectionResult]:
        first_span = first.end - first.start
        second_span = second.end - second.start
        if first_span != second_span:
            return (first, second) if first_span > second_span else (second, first)
        if first.score != second.score:
            return (first, second) if first.score >= second.score else (second, first)
        return (first, second) if first.start <= second.start else (second, first)

    @staticmethod
    def _trim_overlap(
        loser: DetectionResult,
        winner: DetectionResult,
        text: str,
    ) -> DetectionResult | None:
        if loser.start < winner.start:
            new_start = loser.start
            new_end = winner.start
        else:
            new_start = winner.end
            new_end = loser.end

        if new_start >= new_end:
            return None

        ctx_start = max(0, new_start - 40)
        ctx_end = min(len(text), new_end + 40)
        return DetectionResult(
            entity_type=loser.entity_type,
            start=new_start,
            end=new_end,
            score=loser.score,
            text_span=text[new_start:new_end],
            context=text[ctx_start:ctx_end],
            recognizer_name=loser.recognizer_name,
        )
