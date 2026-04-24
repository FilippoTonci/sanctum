from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import (
    AnalysisError,
    AnonymizationError,
    ReviewSessionAlreadyCommittedError,
)
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    OperatorPolicy,
    ProposalDecision,
    StructuredDocument,
    TextSegment,
    UserAddedDecision,
)
from sanctum.core.review.session import commit as commit_session
from sanctum.core.review.store import SessionStore


class TestEngineAnalyze:
    def test_analyze_delegates_to_analyzer(
        self, engine: SanctumEngine, mock_analyzer: Mock, sample_text: str
    ) -> None:
        engine.analyze(sample_text, language="en", entities=["PERSON"], score_threshold=0.5)

        mock_analyzer.analyze.assert_called_once_with(
            sample_text,
            language="en",
            entities=["PERSON"],
            score_threshold=0.5,
        )

    def test_analyze_returns_analyzer_results(
        self, engine: SanctumEngine, mock_analyzer: Mock, sample_text: str
    ) -> None:
        results = engine.analyze(sample_text)
        assert results == mock_analyzer.analyze.return_value

    def test_analyze_wraps_exceptions(
        self, engine: SanctumEngine, mock_analyzer: Mock, sample_text: str
    ) -> None:
        mock_analyzer.analyze.side_effect = ValueError("NLP model failed")

        with pytest.raises(AnalysisError, match="Analysis failed"):
            engine.analyze(sample_text)


class TestEngineAnonymize:
    def test_anonymize_with_detections_skips_analysis(
        self, engine: SanctumEngine, mock_analyzer: Mock, mock_anonymizer: Mock, sample_text: str
    ) -> None:
        detections = [
            DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice"),
        ]
        engine.anonymize(sample_text, detections=detections)

        mock_analyzer.analyze.assert_not_called()
        mock_anonymizer.anonymize.assert_called_once()

    def test_anonymize_without_detections_runs_analysis(
        self, engine: SanctumEngine, mock_analyzer: Mock, mock_anonymizer: Mock, sample_text: str
    ) -> None:
        engine.anonymize(sample_text)

        mock_analyzer.analyze.assert_called_once()
        mock_anonymizer.anonymize.assert_called_once()

    def test_anonymize_wraps_exceptions(
        self, engine: SanctumEngine, mock_anonymizer: Mock, sample_text: str
    ) -> None:
        detections = [
            DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice"),
        ]
        mock_anonymizer.anonymize.side_effect = ValueError("Operator failed")

        with pytest.raises(AnonymizationError, match="Anonymization failed"):
            engine.anonymize(sample_text, detections=detections)


class TestEngineProcess:
    def test_process_chains_analyze_and_anonymize(
        self, engine: SanctumEngine, mock_analyzer: Mock, mock_anonymizer: Mock, sample_text: str
    ) -> None:
        result = engine.process(sample_text)

        mock_analyzer.analyze.assert_called_once()
        mock_anonymizer.anonymize.assert_called_once()
        assert result == mock_anonymizer.anonymize.return_value

    def test_process_passes_operator_policies(
        self, engine: SanctumEngine, mock_anonymizer: Mock, sample_text: str
    ) -> None:
        policies = {"PERSON": OperatorPolicy(operator_name="replace", params={"new_value": "X"})}
        engine.process(sample_text, operator_policies=policies)

        call_kwargs = mock_anonymizer.anonymize.call_args
        assert call_kwargs.kwargs.get("operator_policies") == policies or (
            call_kwargs[1].get("operator_policies") == policies
        )


# -------- Review-session engine methods (Phase 1.5 WS2 substep 5) -----------

_FIXED_NOW = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)


def _build_doc(segments: list[TextSegment], source: Path) -> StructuredDocument:
    return StructuredDocument(
        source_path=source,
        format="docx",
        segments=segments,
        raw_handle=object(),  # opaque — the test writer just echoes it back.
    )


def _make_reader(doc_factory: Callable[[Path], StructuredDocument]) -> Mock:
    """Reader mock that calls ``doc_factory(path)`` — lets tests swap the
    returned segments between create and commit if they want."""
    reader = Mock()
    reader.read.side_effect = doc_factory
    return reader


def _make_writer() -> Mock:
    """Writer mock that records the mutated document it was asked to write."""
    writer = Mock()
    writer.write = Mock()
    return writer


def _bracket_anonymizer() -> Mock:
    """Anonymizer mock that wraps each detection's span in ``[...]``.

    The ``compute_preview`` helper calls ``anonymize`` with a single
    synthetic detection covering ``text[0:len(original)]``; we replicate
    that contract here so ``_render_replacement`` returns a predictable
    value per entity type.
    """
    anonymizer = Mock()

    def _anon(
        text: str,
        detections: list[DetectionResult],
        operator_policies: dict[str, OperatorPolicy] | None = None,
    ) -> AnonymizationResult:
        # Single-detection contract — see compute_preview.
        det = detections[0]
        policy = (operator_policies or {}).get("DEFAULT")
        if policy and policy.params.get("new_value") is not None:
            anonymized = policy.params["new_value"]
        else:
            anonymized = f"[{det.entity_type}]"
        return AnonymizationResult(
            original_text=text,
            anonymized_text=text[: det.start] + anonymized + text[det.end :],
            detections=detections,
            operators_applied={det.entity_type: policy.operator_name if policy else "replace"},
        )

    anonymizer.anonymize.side_effect = _anon
    return anonymizer


def _engine(analyzer_results: list[DetectionResult]) -> tuple[SanctumEngine, Mock, Mock]:
    analyzer = Mock()
    analyzer.analyze.return_value = analyzer_results
    anonymizer = _bracket_anonymizer()
    return SanctumEngine(analyzer=analyzer, anonymizer=anonymizer), analyzer, anonymizer


class TestCreateReviewSession:
    def test_persists_session_with_proposals_and_input_bytes(self, tmp_path: Path) -> None:
        input_path = tmp_path / "input.docx"
        input_path.write_bytes(b"PK\x03\x04fake")

        segments = [
            TextSegment(id="s0", text="Alice met Bob"),
            TextSegment(id="s1", text="   "),  # whitespace-only — skipped.
            TextSegment(id="s2", text="Carol said hi"),
        ]
        # Analyzer fixture: every call returns a single PERSON at 0-5.
        # With text "Alice met Bob" / "Carol said hi" only the first
        # token's position is accurate; that's fine for the Flow B
        # proposal shape (original text is what matters, not offsets).
        det = DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        engine, analyzer, _ = _engine([det])

        reader = _make_reader(lambda p: _build_doc(segments, p))
        session_store = SessionStore(root=tmp_path / "sessions")

        session = engine.create_review_session(
            reader=reader,
            input_path=input_path,
            default_operator="hips",
            session_store=session_store,
            session_id="sess-1",
            created_at=_FIXED_NOW,
        )

        assert session.id == "sess-1"
        assert session.default_operator == "hips"
        assert session.default_operator_params == {}
        assert session.format == "docx"
        assert session.created_at == _FIXED_NOW
        assert [s.id for s in session.segments] == ["s0", "s1", "s2"]
        # Whitespace segment was skipped from analysis — two analyze calls.
        assert analyzer.analyze.call_count == 2
        # One proposal per analyzed segment.
        assert [p.segment_anchor for p in session.proposals] == ["s0", "s2"]
        assert [p.original for p in session.proposals] == ["Alice", "Alice"]

        # Persisted — manifest + input bytes on disk under 0700/0600.
        assert session_store.exists("sess-1")
        assert session_store.load_input_bytes("sess-1") == b"PK\x03\x04fake"

    def test_empty_analysis_yields_no_proposals(self, tmp_path: Path) -> None:
        input_path = tmp_path / "input.docx"
        input_path.write_bytes(b"bytes")
        segments = [TextSegment(id="s0", text="plain text")]
        engine, _, _ = _engine([])
        reader = _make_reader(lambda p: _build_doc(segments, p))
        session_store = SessionStore(root=tmp_path / "sessions")

        session = engine.create_review_session(
            reader=reader,
            input_path=input_path,
            default_operator="replace",
            session_store=session_store,
            session_id="sess-empty",
            created_at=_FIXED_NOW,
        )
        assert session.proposals == []

    def test_default_operator_params_passed_through(self, tmp_path: Path) -> None:
        input_path = tmp_path / "input.docx"
        input_path.write_bytes(b"bytes")
        segments = [TextSegment(id="s0", text="plain text")]
        engine, _, _ = _engine([])
        reader = _make_reader(lambda p: _build_doc(segments, p))
        session_store = SessionStore(root=tmp_path / "sessions")

        session = engine.create_review_session(
            reader=reader,
            input_path=input_path,
            default_operator="replace",
            session_store=session_store,
            default_operator_params={"new_value": "[X]"},
            session_id="sess-params",
            created_at=_FIXED_NOW,
        )
        assert session.default_operator_params == {"new_value": "[X]"}


class TestCommitReviewSession:
    def _setup(
        self,
        tmp_path: Path,
        segments: list[TextSegment],
        analyzer_results: list[DetectionResult],
    ) -> tuple[SanctumEngine, Mock, Mock, SessionStore, Path]:
        input_path = tmp_path / "input.docx"
        input_path.write_bytes(b"PK\x03\x04fake")
        engine, _, _ = _engine(analyzer_results)
        reader = _make_reader(lambda p: _build_doc(segments, p))
        session_store = SessionStore(root=tmp_path / "sessions")
        engine.create_review_session(
            reader=reader,
            input_path=input_path,
            default_operator="replace",
            session_store=session_store,
            session_id="sess-commit",
            created_at=_FIXED_NOW,
        )
        return engine, reader, _make_writer(), session_store, input_path

    def test_accepted_proposal_uses_default_operator(self, tmp_path: Path) -> None:
        segments = [TextSegment(id="s0", text="Alice met Bob")]
        det = DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        engine, reader, writer, store, _ = self._setup(tmp_path, segments, [det])
        session = store.load("sess-commit")
        accepted_id = session.proposals[0].detection_id

        session.decisions.append(ProposalDecision(proposal_id=accepted_id, status="accept"))
        store.save(session)

        out_path = tmp_path / "out.docx"
        returned = engine.commit_review_session(
            reader=reader,
            writer=writer,
            session_id="sess-commit",
            output_path=out_path,
            session_store=store,
            committed_at=_FIXED_NOW,
        )

        writer.write.assert_called_once()
        (doc, path) = writer.write.call_args.args
        assert path == out_path
        assert returned == out_path
        assert doc.segments[0].text == "[PERSON] met Bob"
        # Session dir is cleaned.
        assert not store.exists("sess-commit")

    def test_rejected_proposal_leaves_original(self, tmp_path: Path) -> None:
        segments = [TextSegment(id="s0", text="Alice met Bob")]
        det = DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        engine, reader, writer, store, _ = self._setup(tmp_path, segments, [det])
        session = store.load("sess-commit")
        session.decisions.append(
            ProposalDecision(proposal_id=session.proposals[0].detection_id, status="reject")
        )
        store.save(session)

        engine.commit_review_session(
            reader=reader,
            writer=writer,
            session_id="sess-commit",
            output_path=tmp_path / "out.docx",
            session_store=store,
            committed_at=_FIXED_NOW,
        )
        (doc, _) = writer.write.call_args.args
        assert doc.segments[0].text == "Alice met Bob"

    def test_custom_replacement_short_circuits_operator(self, tmp_path: Path) -> None:
        segments = [TextSegment(id="s0", text="Alice met Bob")]
        det = DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        engine, reader, writer, store, _ = self._setup(tmp_path, segments, [det])
        session = store.load("sess-commit")
        session.decisions.append(
            ProposalDecision(
                proposal_id=session.proposals[0].detection_id,
                status="accept",
                custom_replacement="[DEFENDANT]",
            )
        )
        store.save(session)

        engine.commit_review_session(
            reader=reader,
            writer=writer,
            session_id="sess-commit",
            output_path=tmp_path / "out.docx",
            session_store=store,
            committed_at=_FIXED_NOW,
        )
        (doc, _) = writer.write.call_args.args
        assert doc.segments[0].text == "[DEFENDANT] met Bob"

    def test_per_decision_operator_override(self, tmp_path: Path) -> None:
        segments = [TextSegment(id="s0", text="Alice met Bob")]
        det = DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        engine, reader, writer, store, _ = self._setup(tmp_path, segments, [det])
        session = store.load("sess-commit")
        # session default is "replace"; override to one with params, which
        # the bracket anonymizer picks up as ``new_value``.
        session.decisions.append(
            ProposalDecision(
                proposal_id=session.proposals[0].detection_id,
                status="accept",
                operator="replace",
                operator_params={"new_value": "[PERSON_1]"},
            )
        )
        store.save(session)

        engine.commit_review_session(
            reader=reader,
            writer=writer,
            session_id="sess-commit",
            output_path=tmp_path / "out.docx",
            session_store=store,
            committed_at=_FIXED_NOW,
        )
        (doc, _) = writer.write.call_args.args
        assert doc.segments[0].text == "[PERSON_1] met Bob"

    def test_user_added_span_is_replaced(self, tmp_path: Path) -> None:
        segments = [TextSegment(id="s0", text="Alice met Bob")]
        engine, reader, writer, store, _ = self._setup(tmp_path, segments, [])  # no proposals
        session = store.load("sess-commit")
        session.decisions.append(
            UserAddedDecision(
                segment_anchor="s0",
                entity_type="PERSON",
                original="Bob",
            )
        )
        store.save(session)

        engine.commit_review_session(
            reader=reader,
            writer=writer,
            session_id="sess-commit",
            output_path=tmp_path / "out.docx",
            session_store=store,
            committed_at=_FIXED_NOW,
        )
        (doc, _) = writer.write.call_args.args
        assert doc.segments[0].text == "Alice met [PERSON]"

    def test_multiple_replacements_apply_right_to_left(self, tmp_path: Path) -> None:
        segments = [TextSegment(id="s0", text="Alice met Bob")]
        detections = [
            DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice"),
            DetectionResult(entity_type="PERSON", start=10, end=13, score=0.9, text_span="Bob"),
        ]
        engine, reader, writer, store, _ = self._setup(tmp_path, segments, detections)
        # Because _engine's analyzer mock returns the same two detections
        # on every analyze call, both proposals exist — accept both.
        session = store.load("sess-commit")
        for prop in session.proposals:
            session.decisions.append(
                ProposalDecision(proposal_id=prop.detection_id, status="accept")
            )
        store.save(session)

        engine.commit_review_session(
            reader=reader,
            writer=writer,
            session_id="sess-commit",
            output_path=tmp_path / "out.docx",
            session_store=store,
            committed_at=_FIXED_NOW,
        )
        (doc, _) = writer.write.call_args.args
        assert doc.segments[0].text == "[PERSON] met [PERSON]"

    def test_double_commit_raises(self, tmp_path: Path) -> None:
        segments = [TextSegment(id="s0", text="plain text")]
        engine, reader, writer, store, _ = self._setup(tmp_path, segments, [])
        # Pre-mark the session committed on disk — simulates a second
        # call after a completed commit (session dir would be deleted,
        # but the state-machine rule still holds if a caller forces it).
        session = store.load("sess-commit")
        commit_session(session, _FIXED_NOW)
        store.save(session)

        with pytest.raises(ReviewSessionAlreadyCommittedError):
            engine.commit_review_session(
                reader=reader,
                writer=writer,
                session_id="sess-commit",
                output_path=tmp_path / "out.docx",
                session_store=store,
                committed_at=_FIXED_NOW,
            )
