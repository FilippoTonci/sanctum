from __future__ import annotations

import tempfile
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanctum.core.exceptions import (
    AnalysisError,
    AnonymizationError,
    DocumentError,
    ReviewSessionAlreadyCommittedError,
)
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    OperatorPolicy,
    ProposalDecision,
    ReviewProposal,
    ReviewSession,
    TextSegment,
    UserAddedDecision,
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
from sanctum.core.review.previews import compute_preview
from sanctum.core.review.proposals import build_proposals
from sanctum.core.review.session import commit as commit_session

if TYPE_CHECKING:
    from sanctum.core.review.store import SessionStore


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

    def create_review_session(
        self,
        reader: StructuredDocumentReader,
        input_path: Path,
        default_operator: str,
        session_store: SessionStore,
        default_operator_params: dict[str, Any] | None = None,
        session_id: str | None = None,
        created_at: datetime | None = None,
        language: str = "en",
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> ReviewSession:
        """Analyze-only first pass: build a review session, persist it.

        Reads ``input_path`` once via ``reader`` to populate the session's
        segments + proposals, reads it again as raw bytes so the on-disk
        session carries an authoritative copy for the commit pass (the
        original path may move or be deleted before commit).

        ``default_operator`` seeds the fallback used when a decision omits
        its own operator. Proposals themselves carry no operator under
        Flow B — the reviewer picks per-decision at commit time.

        ``session_id`` / ``created_at`` accept caller-supplied values so
        tests and the API layer can pin them; default to a fresh UUID4
        and current UTC time.
        """
        try:
            doc = reader.read(input_path)
        except Exception as exc:
            raise DocumentError(f"Failed to read {input_path}: {exc}") from exc

        detections_by_segment: dict[str, list[DetectionResult]] = {}
        for segment in doc.segments:
            if not segment.text.strip():
                continue
            detections = self.analyze(
                segment.text,
                language=language,
                entities=entities,
                score_threshold=score_threshold,
            )
            if detections:
                detections_by_segment[segment.id] = detections

        proposals = build_proposals(doc, detections_by_segment)

        session = ReviewSession(
            id=session_id if session_id is not None else str(uuid.uuid4()),
            source_path=input_path,
            format=doc.format,
            default_operator=default_operator,
            default_operator_params=default_operator_params or {},
            segments=list(doc.segments),
            proposals=proposals,
            created_at=created_at if created_at is not None else datetime.now(timezone.utc),
        )

        try:
            input_bytes = input_path.read_bytes()
        except OSError as exc:
            raise DocumentError(f"Failed to snapshot {input_path}: {exc}") from exc
        session_store.save(session, input_bytes=input_bytes)

        return session

    def commit_review_session(
        self,
        reader: StructuredDocumentReader,
        writer: StructuredDocumentWriter,
        session_id: str,
        output_path: Path,
        session_store: SessionStore,
        mapping_store: MappingStore | None = None,
        committed_at: datetime | None = None,
    ) -> Path:
        """Apply decisions to the session's source doc, write the final file.

        Loads the session + its persisted input bytes, re-parses via
        ``reader`` (fresh ``raw_handle`` needed by the writer), then for
        each segment computes per-decision replacements:

        - Accepted ``ProposalDecision`` → anonymize the span under the
          decision's operator (falling back to the session default).
          ``custom_replacement`` short-circuits the operator.
        - Rejected ``ProposalDecision`` → original text untouched.
        - ``UserAddedDecision`` → anonymize the first occurrence of the
          span's ``original`` within its anchored segment.

        Replacements are applied right-to-left so positions stay valid.
        The session dir is deleted on success — input bytes + plaintext
        proposals don't outlive the commit.

        ``mapping_store`` is accepted for forward-compat with WS4 (the
        pseudonymize commit path plumbs it through to the anonymizer);
        under Flow B the anonymizer adapter owns its own store wiring,
        so it's currently unused at this layer.
        """
        session = session_store.load(session_id)
        if session.status != "open":
            raise ReviewSessionAlreadyCommittedError(
                f"Session {session_id!r} is {session.status}; cannot commit."
            )

        input_bytes = session_store.load_input_bytes(session_id)

        # reader.read takes a Path — the session-stored bytes win over the
        # (possibly-moved) source_path so commit is self-contained.
        with tempfile.NamedTemporaryFile(suffix=f".{session.format}", delete=False) as tmp:
            tmp.write(input_bytes)
            tmp_path = Path(tmp.name)

        try:
            try:
                doc = reader.read(tmp_path)
            except Exception as exc:
                raise DocumentError(
                    f"Failed to re-read session {session_id!r} input: {exc}"
                ) from exc

            new_segments = _apply_decisions_to_segments(
                session=session,
                segments=doc.segments,
                anonymizer=self._anonymizer,
            )
            mutated = doc.model_copy(update={"segments": new_segments})
            mutated.raw_handle = doc.raw_handle

            try:
                writer.write(mutated, output_path)
            except Exception as exc:
                raise DocumentError(f"Failed to write {output_path}: {exc}") from exc
        finally:
            tmp_path.unlink(missing_ok=True)

        commit_session(
            session,
            committed_at if committed_at is not None else datetime.now(timezone.utc),
        )
        session_store.delete(session_id)
        return output_path

    def commit_review(
        self,
        reader: StructuredDocumentReader,
        writer: StructuredDocumentWriter,
        input_path: Path,
        output_path: Path,
        mapping_store: MappingStore,
    ) -> None:
        """Deprecated file-scoped commit (Phase 1.5 WS1 shim).

        Flow B moved review commit behind a session id — see
        ``commit_review_session``. This entry point survives one release
        for callers still on the file-scoped shape; it always raises
        ``NotImplementedError`` since the trailer-parse path was dropped
        along with the native-comment review UX (issue #16).
        """
        warnings.warn(
            "SanctumEngine.commit_review(reader, writer, input_path, output_path, "
            "store) is deprecated; use commit_review_session(session_id, ...) "
            "instead. The file-scoped entry point is scheduled for removal "
            "after Phase 1.5 WS5.",
            DeprecationWarning,
            stacklevel=2,
        )
        if not isinstance(reader, ReviewParsingReader):
            raise NotImplementedError(
                f"{type(reader).__name__} does not yet implement read_review_decisions; "
                "per-format commit-review support lands in Phase 1.5 WS2-5."
            )
        raise NotImplementedError(
            "commit_review reconciliation lands in Phase 1.5 WS6; the protocol "
            "surface is in place but the store-write path is intentionally unwired."
        )


def _apply_decisions_to_segments(
    session: ReviewSession,
    segments: list[TextSegment],
    anonymizer: Anonymizer,
) -> list[TextSegment]:
    """Project Flow B decisions onto freshly-read segments.

    The session's proposals are grouped by ``segment_anchor``; within
    each segment, original-text offsets are reconstructed by walking the
    proposal list in order and ``str.find``-ing each ``original`` from a
    monotonically advancing cursor. That keeps duplicates (``"Alice met
    Alice"``) disambiguated — the analyzer emits detections in document
    order, and the session preserves that order.

    User-added decisions are anchored by segment id + original text; we
    take the first occurrence. Overlap detection is intentionally out of
    scope here — the API layer can surface a 400 at PATCH time if needed.
    """
    proposals_by_segment: dict[str, list[ReviewProposal]] = {}
    for prop in session.proposals:
        if prop.segment_anchor is None:
            continue
        proposals_by_segment.setdefault(prop.segment_anchor, []).append(prop)

    accepted_proposal_decisions: dict[str, ProposalDecision] = {}
    user_added_by_segment: dict[str, list[UserAddedDecision]] = {}
    for sd in session.decisions:
        if isinstance(sd, ProposalDecision):
            if sd.status == "accept":
                accepted_proposal_decisions[sd.proposal_id] = sd
        else:
            user_added_by_segment.setdefault(sd.segment_anchor, []).append(sd)

    new_segments: list[TextSegment] = []
    for segment in segments:
        segment_proposals = proposals_by_segment.get(segment.id, [])
        segment_user_added = user_added_by_segment.get(segment.id, [])

        if not segment_proposals and not segment_user_added:
            new_segments.append(segment)
            continue

        replacements: list[tuple[int, int, str]] = []

        cursor = 0
        for prop in segment_proposals:
            idx = segment.text.find(prop.original, cursor)
            if idx < 0:
                # Session went stale against the stored bytes — skip
                # rather than corrupt. The store.py invariant is that
                # input bytes are frozen on first save, so this is a
                # programming error, not a user-editable gap.
                continue
            start = idx
            end = idx + len(prop.original)
            cursor = end

            decision = accepted_proposal_decisions.get(prop.detection_id)
            if decision is None:
                continue  # rejected or undecided — leave original.

            operator = decision.operator or session.default_operator
            operator_params = (
                decision.operator_params
                if decision.operator_params is not None
                else session.default_operator_params
            )
            replacement = _render_replacement(
                entity_type=prop.entity_type,
                original=prop.original,
                score=prop.score,
                operator=operator,
                operator_params=operator_params,
                custom_replacement=decision.custom_replacement,
                anonymizer=anonymizer,
            )
            replacements.append((start, end, replacement))

        for ua in segment_user_added:
            idx = segment.text.find(ua.original)
            if idx < 0:
                continue
            start = idx
            end = idx + len(ua.original)

            operator = ua.operator or session.default_operator
            operator_params = (
                ua.operator_params
                if ua.operator_params is not None
                else session.default_operator_params
            )
            replacement = _render_replacement(
                entity_type=ua.entity_type,
                original=ua.original,
                score=1.0,
                operator=operator,
                operator_params=operator_params,
                custom_replacement=ua.custom_replacement,
                anonymizer=anonymizer,
            )
            replacements.append((start, end, replacement))

        replacements.sort(key=lambda r: r[0], reverse=True)
        new_text = segment.text
        for start, end, repl in replacements:
            new_text = new_text[:start] + repl + new_text[end:]

        new_segments.append(segment.model_copy(update={"text": new_text}))

    return new_segments


def _render_replacement(
    entity_type: str,
    original: str,
    score: float,
    operator: str,
    operator_params: dict[str, Any] | None,
    custom_replacement: str | None,
    anonymizer: Anonymizer,
) -> str:
    """Run a single detection through the anonymizer and return the replacement.

    Reuses ``compute_preview`` by building a synthetic ``ReviewProposal`` —
    the code paths for preview and commit must produce identical output
    for a given ``(operator, params, custom_replacement)`` triple, so we
    share the implementation rather than risk drift.
    """
    shim = ReviewProposal(
        detection_id="commit-shim",
        entity_type=entity_type,
        score=score,
        original=original,
    )
    return compute_preview(
        proposal=shim,
        operator=operator,
        operator_params=operator_params,
        custom_replacement=custom_replacement,
        anonymizer=anonymizer,
    )
