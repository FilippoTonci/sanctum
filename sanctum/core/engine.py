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
    MappingStore,
    ReviewEmittingWriter,
    ReviewParsingReader,
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
        except AnonymizationError:
            # Already in our exception taxonomy (including subclasses like
            # InvalidOperatorParamsError) — let it through without the
            # generic rewrap so the HTTP layer can distinguish.
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
        review: bool = False,
    ) -> list[AnonymizationResult]:
        """Read a structured document, anonymize each text segment, write back.

        Each segment is analyzed and anonymized independently. This trades
        cross-segment detection coverage (an entity that straddles two runs
        won't be caught) for structural fidelity: runs, cells, and shapes
        all keep their formatting because we never flatten the document.

        Returns one AnonymizationResult per non-empty segment. Segments
        whose text is empty after stripping are skipped silently — they
        cost nothing and produce no useful detections.

        When ``review`` is True, the output is written via the adapter's
        ``emit_review`` path — the document carries anonymized text *and* a
        native comment per detection so a human can verify the changes
        before sharing. Requires ``writer`` to satisfy the
        ``ReviewEmittingWriter`` protocol; raises ``NotImplementedError``
        otherwise. Default is False during Phase 1.5; the CLI / API layers
        flip the user-facing default to True once WS2-5 light up review
        for every format.
        """
        if review and not isinstance(writer, ReviewEmittingWriter):
            raise NotImplementedError(
                f"{type(writer).__name__} does not yet implement emit_review; "
                "pass review=False or wait for the adapter to land in Phase 1.5 WS2-5."
            )

        try:
            doc = reader.read(input_path)
        except Exception as exc:
            raise DocumentError(f"Failed to read {input_path}: {exc}") from exc

        results: list[AnonymizationResult] = []
        results_by_segment: dict[str, AnonymizationResult] = {}
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
            results_by_segment[segment.id] = result
            new_segments.append(segment.model_copy(update={"text": result.anonymized_text}))

        mutated = doc.model_copy(update={"segments": new_segments})
        # Preserve the opaque raw handle: ``model_copy`` carries it through
        # because it was set via default, but Pydantic excludes fields with
        # ``exclude=True`` from dumps — not from in-memory copies.
        mutated.raw_handle = doc.raw_handle

        try:
            if review:
                # isinstance check above guarantees this call is safe.
                writer.emit_review(mutated, output_path, results_by_segment)  # type: ignore[attr-defined]
            else:
                writer.write(mutated, output_path)
        except Exception as exc:
            raise DocumentError(f"Failed to write {output_path}: {exc}") from exc

        return results

    def commit_review(
        self,
        reader: StructuredDocumentReader,
        writer: StructuredDocumentWriter,
        input_path: Path,
        output_path: Path,
        mapping_store: MappingStore,
    ) -> None:
        """Reconcile a reviewed file into the mapping store, emit a finalized copy.

        Reads staged pseudonym mappings and reviewer decisions out of the
        reviewed document, writes accepted + user-added entries into
        ``mapping_store``, skips rejected ones, and emits a shareable copy
        with all ``sanctum:`` trailers stripped.

        WS1 lands only the method signature; reconciliation semantics live
        in Phase 1.5 WS6 once every adapter implements
        ``read_review_decisions``. For WS1 this always raises
        ``NotImplementedError`` — the CLI ``commit-review`` subcommand
        and API ``POST /commit-review`` endpoint use the same error path
        so users get a consistent "not yet wired" message.
        """
        if not isinstance(reader, ReviewParsingReader):
            raise NotImplementedError(
                f"{type(reader).__name__} does not yet implement read_review_decisions; "
                "per-format commit-review support lands in Phase 1.5 WS2-5."
            )
        raise NotImplementedError(
            "commit_review reconciliation lands in Phase 1.5 WS6; the protocol "
            "surface is in place but the store-write path is intentionally unwired."
        )
