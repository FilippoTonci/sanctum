from __future__ import annotations

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
        results = self._engine.analyze(
            text=text,
            language=language,
            entities=entities,
            score_threshold=score_threshold or self._default_threshold,
        )

        detections: list[DetectionResult] = []
        for r in results:
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

        return detections
