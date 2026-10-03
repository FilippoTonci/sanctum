from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sanctum.core.blocks import detect_blocks, fragment_words, splice
from sanctum.core.exceptions import (
    AnalysisError,
    AnonymizationError,
    DocumentError,
    LeakCheckError,
    ReviewSessionAlreadyCommittedError,
)
from sanctum.core.leak_check import verify_no_leaks
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
    OutputTextExtractor,
    StructuredDocumentReader,
    StructuredDocumentWriter,
)
from sanctum.core.review.previews import compute_preview
from sanctum.core.review.proposals import build_proposals_from_findings
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
    ) -> list[AnonymizationResult]:
        """Read a structured document, anonymize every finding, write back.

        Detection runs per block (see ``sanctum.core.blocks``): segments that
        share a ``block`` key (the runs of one paragraph) are joined and
        analysed together, so an entity that straddles two runs is caught.
        Each finding is rendered once over its whole text; the replacement
        lands in the first segment it covers and the rest of the finding is
        removed from the following segments, so runs, cells and shapes keep
        their formatting because we never flatten the document.

        Returns one AnonymizationResult per finding (its ``original_text`` is
        the finding's text, with a single detection covering it). Blocks
        whose text is empty after stripping are skipped silently.

        This is the fire-and-forget pipeline (CLI ``--no-review``, API
        ``review=false``). The human-in-the-loop review path lives on
        ``create_review_session`` / ``commit_review_session`` instead.
        """
        try:
            doc = reader.read(input_path)
        except Exception as exc:
            raise DocumentError(f"Failed to read {input_path}: {exc}") from exc

        findings = detect_blocks(
            doc.segments,
            lambda text: self.analyze(
                text, language=language, entities=entities, score_threshold=score_threshold
            ),
        )

        results: list[AnonymizationResult] = []
        edits: dict[str, list[tuple[int, int, str]]] = {}
        replaced_originals: list[str] = []
        for f in findings:
            whole = DetectionResult(
                entity_type=f.entity_type,
                start=0,
                end=len(f.original),
                score=f.score,
                text_span=f.original,
            )
            result = self.anonymize(
                f.original, detections=[whole], operator_policies=operator_policies
            )
            results.append(result)
            for i, piece in enumerate(f.pieces):
                edits.setdefault(piece.segment_id, []).append(
                    (piece.start, piece.end, result.anonymized_text if i == 0 else "")
                )
            replaced_originals.append(f.original)
            if len(f.pieces) > 1:
                for piece in f.pieces:
                    replaced_originals.extend(fragment_words(piece.text, f.original))
        new_segments = [
            seg.model_copy(update={"text": splice(seg.text, edits[seg.id])})
            if seg.id in edits
            else seg
            for seg in doc.segments
        ]

        mutated = doc.model_copy(update={"segments": new_segments})
        # Preserve the opaque raw handle: ``model_copy`` carries it through
        # because it was set via default, but Pydantic excludes fields with
        # ``exclude=True`` from dumps — not from in-memory copies.
        mutated.raw_handle = doc.raw_handle

        try:
            writer.write(mutated, output_path)
        except Exception as exc:
            raise DocumentError(f"Failed to write {output_path}: {exc}") from exc

        _run_leak_check(writer, output_path, replaced_originals)
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

        findings = detect_blocks(
            doc.segments,
            lambda text: self.analyze(
                text, language=language, entities=entities, score_threshold=score_threshold
            ),
        )
        proposals = build_proposals_from_findings(findings)

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
        - ``UserAddedDecision`` → anonymize the span at its recorded
          ``start``/``end`` (first occurrence of ``original`` only if those
          offsets no longer match the segment).

        Replacements are applied right-to-left so positions stay valid.
        The session dir is deleted on success — input bytes + plaintext
        proposals don't outlive the commit.

        ``mapping_store`` is the unlocked ``MappingStore`` used to
        persist pseudonyms for ``pseudonymize`` decisions. Any accepted
        or user-added decision whose resolved operator is
        ``pseudonymize`` will mint through ``get_or_create`` on the
        given store — non-pseudonymize decisions ignore the store. If
        no pseudonymize decisions are present, the argument is allowed
        to be ``None``; otherwise the anonymizer will raise when it
        tries to mint without one.
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
                mapping_store=mapping_store,
            )
            mutated = doc.model_copy(update={"segments": new_segments})
            mutated.raw_handle = doc.raw_handle

            try:
                writer.write(mutated, output_path)
            except Exception as exc:
                raise DocumentError(f"Failed to write {output_path}: {exc}") from exc

            _run_leak_check(writer, output_path, _originals_to_verify(session, new_segments))
        finally:
            tmp_path.unlink(missing_ok=True)

        commit_session(
            session,
            committed_at if committed_at is not None else datetime.now(timezone.utc),
        )
        session_store.save(session)
        session_store.shed_input(session_id)
        return output_path


def _originals_to_verify(session: ReviewSession, segments: list[TextSegment]) -> list[str]:
    """Originals the leak check must not find in the committed output.

    Every replaced span's original, minus strings the reviewer explicitly
    chose to keep somewhere else (a rejected proposal with the same
    original text): those are expected to survive. Undecided proposals are
    *not* exempt — an accepted value that also appears unreviewed is
    exactly the leak this check exists to catch.

    A linked finding is checked as a whole: its head piece's replacement
    carries the finding's ``group_original`` as ``leak_original`` and the
    tail pieces carry ``None`` (a tail piece can be as short as one space).
    Every piece, head included, also carries ``leak_extra``: its whole words
    that are whole words of the finding (see ``fragment_words``), so a piece
    the writer failed to replace ("Dear <PERSON>Martin") is still caught.
    Those words are kept, like the whole original, when the group was
    rejected.
    """
    rejected = {
        d.proposal_id
        for d in session.decisions
        if isinstance(d, ProposalDecision) and d.status == "reject"
    }
    kept: set[str] = set()
    for prop in session.proposals:
        if prop.detection_id not in rejected:
            continue
        kept.add(prop.group_original or prop.original)
        if prop.group_original is not None:
            kept.update(fragment_words(prop.original, prop.group_original))
    originals: list[str] = []
    for seg in segments:
        for rep in seg.metadata.get("replacements", []):
            value = rep.get("leak_original", rep["original"])
            candidates = [value, *rep.get("leak_extra", [])]
            originals.extend(v for v in candidates if v is not None and v not in kept)
    return originals


def _run_leak_check(
    writer: StructuredDocumentWriter, output_path: Path, originals: list[str]
) -> None:
    """Re-extract the written output and fail if a replaced original survives.

    Only writers that implement :class:`OutputTextExtractor` are checked;
    for the others this is a no-op (to be closed format by format, WS0.2).
    On failure the output file is deleted so a leaking document never
    stays on disk.
    """
    if not originals or not isinstance(writer, OutputTextExtractor):
        return
    try:
        text = writer.extract_text(output_path)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise DocumentError(f"Leak check could not re-read {output_path}: {exc}") from exc
    try:
        verify_no_leaks(text, originals, where=output_path.name)
    except LeakCheckError:
        output_path.unlink(missing_ok=True)
        raise


def _apply_decisions_to_segments(
    session: ReviewSession,
    segments: list[TextSegment],
    anonymizer: Anonymizer,
    mapping_store: MappingStore | None = None,
) -> list[TextSegment]:
    """Project Flow B decisions onto freshly-read segments.

    The session's proposals are grouped by ``segment_anchor``; within
    each segment, original-text offsets are reconstructed by walking the
    proposal list in order and ``str.find``-ing each ``original`` from a
    monotonically advancing cursor. That keeps duplicates (``"Alice met
    Alice"``) disambiguated — the analyzer emits detections in document
    order, and the session preserves that order.

    User-added decisions are placed at their recorded offsets when the
    segment text there still equals ``original``, else at the first
    occurrence. Overlap detection is intentionally out of
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

        # (start, end, replacement, leak_original, leak_extra) — see
        # _originals_to_verify.
        replacements: list[tuple[int, int, str, str | None, list[str]]] = []

        cursor = 0
        for prop in segment_proposals:
            if segment.text[prop.start : prop.end] == prop.original:
                # The recorded offsets still match: use them. Linked pieces
                # can be as short as a single space, which ``find`` would
                # resolve to the wrong place.
                idx = prop.start
            else:
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
            leak_original: str | None
            if prop.group_id is not None and prop.group_index > 0:
                # Tail of a linked finding: the head carries the replacement.
                replacement = ""
                leak_original = None
            else:
                replacement = _render_replacement(
                    entity_type=prop.entity_type,
                    original=prop.group_original or prop.original,
                    score=prop.score,
                    operator=operator,
                    operator_params=operator_params,
                    custom_replacement=decision.custom_replacement,
                    anonymizer=anonymizer,
                    mapping_store=mapping_store,
                )
                leak_original = prop.group_original or prop.original
            leak_extra = (
                fragment_words(prop.original, prop.group_original)
                if prop.group_original is not None
                else []
            )
            replacements.append((start, end, replacement, leak_original, leak_extra))

        for ua in segment_user_added:
            # Place the span where the reviewer marked it; ``find`` is only a
            # fallback for offsets that no longer match (it picks the first
            # occurrence, which may be a different, already-handled one).
            if segment.text[ua.start : ua.end] == ua.original:
                idx = ua.start
            else:
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
                mapping_store=mapping_store,
            )
            replacements.append((start, end, replacement, ua.original, []))

        new_text = splice(segment.text, ((s, e, r) for s, e, r, _, _ in replacements))

        # Exact spans (offsets into the *original* segment text) ride along
        # in metadata for writers that need geometry (PDF paints each span)
        # and for the post-write leak check. Never persisted: the session
        # keeps its own copy of the unmutated segments.
        metadata = dict(segment.metadata)
        metadata["replacements"] = [
            {
                "start": s,
                "end": e,
                "original": segment.text[s:e],
                "text": r,
                "leak_original": lo,
                "leak_extra": extra,
            }
            for s, e, r, lo, extra in sorted(replacements, key=lambda r: r[0])
        ]
        new_segments.append(segment.model_copy(update={"text": new_text, "metadata": metadata}))

    return new_segments


def _render_replacement(
    entity_type: str,
    original: str,
    score: float,
    operator: str,
    operator_params: dict[str, Any] | None,
    custom_replacement: str | None,
    anonymizer: Anonymizer,
    mapping_store: MappingStore | None = None,
) -> str:
    """Run a single detection through the anonymizer and return the replacement.

    Reuses ``compute_preview`` by building a synthetic ``ReviewProposal`` —
    the code paths for preview and commit must produce identical output
    for a given ``(operator, params, custom_replacement)`` triple, so we
    share the implementation rather than risk drift. ``mapping_store``
    is forwarded verbatim: commit passes the real store (to persist
    pseudonyms), preview passes a ``PreviewMappingStore`` wrapper.
    """
    shim = ReviewProposal(
        detection_id="commit-shim",
        entity_type=entity_type,
        score=score,
        original=original,
        # The shim covers the whole synthetic span — start/end are not
        # used by `compute_preview`, but ReviewProposal requires them
        # since real proposals carry segment-relative offsets.
        start=0,
        end=len(original),
    )
    return compute_preview(
        proposal=shim,
        operator=operator,
        operator_params=operator_params,
        custom_replacement=custom_replacement,
        anonymizer=anonymizer,
        mapping_store=mapping_store,
    )
