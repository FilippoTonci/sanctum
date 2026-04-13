from __future__ import annotations

from pathlib import Path

from sanctum.core.exceptions import AnalysisError, AnonymizationError, DocumentError
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    OperatorPolicy,
    TextSegment,
)
from sanctum.core.protocols import (
    Analyzer,
    Anonymizer,
    StructuredDocumentReader,
    StructuredDocumentWriter,
)


class SanctumEngine:
    """Main orchestrator that coordinates analysis and anonymization."""

    def __init__(self, analyzer: Analyzer, anonymizer: Anonymizer) -> None:
        self._analyzer = analyzer
        self._anonymizer = anonymizer

    def analyze(
        self,
        text: str,
        language: str = "en",
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[DetectionResult]:
        """Run PII detection on the given text."""
        try:
            return self._analyzer.analyze(
                text,
                language=language,
                entities=entities,
                score_threshold=score_threshold,
            )
        except Exception as exc:
            raise AnalysisError(f"Analysis failed: {exc}") from exc

    def anonymize(
        self,
        text: str,
        detections: list[DetectionResult] | None = None,
        operator_policies: dict[str, OperatorPolicy] | None = None,
    ) -> AnonymizationResult:
        """Anonymize text, optionally auto-detecting PII first."""
        if detections is None:
            detections = self.analyze(text)
        try:
            return self._anonymizer.anonymize(
                text,
                detections=detections,
                operator_policies=operator_policies,
            )
        except AnalysisError:
            raise
        except Exception as exc:
            raise AnonymizationError(f"Anonymization failed: {exc}") from exc

    def process(
        self,
        text: str,
        language: str = "en",
        entities: list[str] | None = None,
        score_threshold: float | None = None,
        operator_policies: dict[str, OperatorPolicy] | None = None,
    ) -> AnonymizationResult:
        """Convenience method: analyze then anonymize in one call."""
        detections = self.analyze(
            text,
            language=language,
            entities=entities,
            score_threshold=score_threshold,
        )
        return self.anonymize(text, detections=detections, operator_policies=operator_policies)

    def process_document(
        self,
        reader: StructuredDocumentReader,
        writer: StructuredDocumentWriter,
        input_path: Path,
        output_path: Path,
        language: str = "en",
        entities: list[str] | None = None,
        score_threshold: float | None = None,
        operator_policies: dict[str, OperatorPolicy] | None = None,
    ) -> list[AnonymizationResult]:
        """Read a structured document, anonymize each text segment, write back.

        Each segment is analyzed and anonymized independently. This trades
        cross-segment detection coverage (an entity that straddles two runs
        won't be caught) for structural fidelity: runs, cells, and shapes
        all keep their formatting because we never flatten the document.

        Returns one AnonymizationResult per non-empty segment. Segments
        whose text is empty after stripping are skipped silently — they
        cost nothing and produce no useful detections.
        """
        try:
            doc = reader.read(input_path)
        except Exception as exc:
            raise DocumentError(f"Failed to read {input_path}: {exc}") from exc

        results: list[AnonymizationResult] = []
        new_segments: list[TextSegment] = []
        for segment in doc.segments:
            if not segment.text.strip():
                new_segments.append(segment)
                continue

            detections = self.analyze(
                segment.text,
                language=language,
                entities=entities,
                score_threshold=score_threshold,
            )
            if not detections:
                new_segments.append(segment)
                continue

            result = self.anonymize(
                segment.text,
                detections=detections,
                operator_policies=operator_policies,
            )
            results.append(result)
            new_segments.append(segment.model_copy(update={"text": result.anonymized_text}))

        mutated = doc.model_copy(update={"segments": new_segments})
        # Preserve the opaque raw handle: ``model_copy`` carries it through
        # because it was set via default, but Pydantic excludes fields with
        # ``exclude=True`` from dumps — not from in-memory copies.
        mutated.raw_handle = doc.raw_handle

        try:
            writer.write(mutated, output_path)
        except Exception as exc:
            raise DocumentError(f"Failed to write {output_path}: {exc}") from exc

        return results
