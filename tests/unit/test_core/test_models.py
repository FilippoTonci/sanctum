from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    OperatorPolicy,
    ProposalDecision,
    ReviewDecision,
    ReviewProposal,
    ReviewSession,
    SessionDecision,
    StagedMapping,
    TextSegment,
    UserAddedDecision,
)


class TestDetectionResult:
    def test_frozen_rejects_attribute_assignment(self) -> None:
        result = DetectionResult(
            entity_type="PERSON",
            start=0,
            end=10,
            score=0.9,
            text_span="John Smith",
        )
        with pytest.raises(ValidationError):
            result.entity_type = "EMAIL_ADDRESS"

    def test_serialization_round_trip(self) -> None:
        result = DetectionResult(
            entity_type="US_SSN",
            start=5,
            end=16,
            score=0.95,
            text_span="123-45-6789",
            context="SSN is 123-45-6789 here",
            recognizer_name="UsSsnRecognizer",
        )
        data = result.model_dump()
        restored = DetectionResult.model_validate(data)
        assert restored == result

    def test_json_round_trip(self) -> None:
        result = DetectionResult(
            entity_type="EMAIL_ADDRESS",
            start=10,
            end=30,
            score=0.88,
            text_span="user@example.com",
        )
        json_str = result.model_dump_json()
        restored = DetectionResult.model_validate_json(json_str)
        assert restored == result

    def test_default_values(self) -> None:
        result = DetectionResult(
            entity_type="PERSON",
            start=0,
            end=5,
            score=0.7,
            text_span="Alice",
        )
        assert result.context == ""
        assert result.recognizer_name == ""


class TestOperatorPolicy:
    def test_mutable_allows_assignment(self) -> None:
        policy = OperatorPolicy(operator_name="redact")
        policy.operator_name = "replace"
        assert policy.operator_name == "replace"

    def test_default_params_is_empty_dict(self) -> None:
        policy = OperatorPolicy(operator_name="redact")
        assert policy.params == {}

    def test_params_are_stored(self) -> None:
        policy = OperatorPolicy(
            operator_name="replace",
            params={"new_value": "<MASKED>"},
        )
        assert policy.params["new_value"] == "<MASKED>"


class TestAnonymizationResult:
    def test_frozen_rejects_attribute_assignment(self) -> None:
        result = AnonymizationResult(
            original_text="hello",
            anonymized_text="<PERSON>",
            detections=[],
            operators_applied={},
        )
        with pytest.raises(ValidationError):
            result.anonymized_text = "changed"

    def test_serialization_round_trip(self) -> None:
        detection = DetectionResult(
            entity_type="PERSON",
            start=0,
            end=5,
            score=0.9,
            text_span="Alice",
        )
        result = AnonymizationResult(
            original_text="Alice went home",
            anonymized_text="<PERSON> went home",
            detections=[detection],
            operators_applied={"PERSON": "replace"},
        )
        data = result.model_dump()
        restored = AnonymizationResult.model_validate(data)
        assert restored == result


def _proposal(**overrides: object) -> ReviewProposal:
    defaults: dict[str, object] = {
        "detection_id": "abcdef012345",
        "entity_type": "PERSON",
        "score": 0.9,
        "original": "Alice",
        "replacement": "[PERSON_1]",
        "operator": "replace",
    }
    defaults.update(overrides)
    return ReviewProposal(**defaults)  # type: ignore[arg-type]


class TestReviewProposal:
    def test_segment_anchor_defaults_to_none(self) -> None:
        proposal = _proposal()
        assert proposal.segment_anchor is None

    def test_segment_anchor_accepts_string(self) -> None:
        proposal = _proposal(segment_anchor="body/p0/r1:0")
        assert proposal.segment_anchor == "body/p0/r1:0"

    def test_frozen_rejects_attribute_assignment(self) -> None:
        proposal = _proposal()
        with pytest.raises(ValidationError):
            proposal.entity_type = "ORG"


class TestReviewDecision:
    """Legacy comment-parse decision shape (retained for WS5 export path)."""

    def test_accepted_carries_staged_trailer(self) -> None:
        decision = ReviewDecision(kind="accepted", staged=_proposal())
        assert decision.kind == "accepted"
        assert decision.staged is not None
        assert decision.user_comment_body is None

    def test_user_added_carries_body_and_anchor(self) -> None:
        decision = ReviewDecision(
            kind="user_added",
            user_comment_body="#PERSON reviewer flag",
            user_anchor_text="Priya Patel",
        )
        assert decision.staged is None
        assert decision.user_anchor_text == "Priya Patel"

    def test_frozen_rejects_attribute_assignment(self) -> None:
        decision = ReviewDecision(kind="rejected", staged=_proposal())
        with pytest.raises(ValidationError):
            decision.kind = "accepted"

    def test_rejects_invalid_kind(self) -> None:
        with pytest.raises(ValidationError):
            ReviewDecision(kind="maybe")  # type: ignore[arg-type]


class TestProposalDecision:
    def test_accept_does_not_require_edited_replacement(self) -> None:
        decision = ProposalDecision(proposal_id="abc123", status="accept")
        assert decision.kind == "proposal"
        assert decision.edited_replacement is None

    def test_reject_does_not_require_edited_replacement(self) -> None:
        decision = ProposalDecision(proposal_id="abc123", status="reject")
        assert decision.edited_replacement is None

    def test_edit_requires_edited_replacement(self) -> None:
        with pytest.raises(ValidationError, match="edited_replacement is required"):
            ProposalDecision(proposal_id="abc123", status="edit")

    def test_edit_accepts_edited_replacement(self) -> None:
        decision = ProposalDecision(
            proposal_id="abc123",
            status="edit",
            edited_replacement="[CUSTOM]",
        )
        assert decision.edited_replacement == "[CUSTOM]"

    def test_rejects_invalid_status(self) -> None:
        with pytest.raises(ValidationError):
            ProposalDecision(proposal_id="abc123", status="maybe")  # type: ignore[arg-type]


class TestUserAddedDecision:
    def test_all_fields_required(self) -> None:
        decision = UserAddedDecision(
            segment_anchor="sheet1/A5",
            entity_type="PERSON",
            original="Priya Patel",
            replacement="[PERSON_2]",
        )
        assert decision.kind == "user_added"

    def test_missing_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            UserAddedDecision(  # type: ignore[call-arg]
                segment_anchor="sheet1/A5",
                entity_type="PERSON",
                original="Priya Patel",
            )


class TestSessionDecision:
    """Discriminated union dispatches on ``kind``."""

    def _adapter(self) -> TypeAdapter[SessionDecision]:
        return TypeAdapter(SessionDecision)

    def test_proposal_kind_parses_to_proposal_decision(self) -> None:
        decision = self._adapter().validate_python(
            {"kind": "proposal", "proposal_id": "x", "status": "accept"}
        )
        assert isinstance(decision, ProposalDecision)

    def test_user_added_kind_parses_to_user_added_decision(self) -> None:
        decision = self._adapter().validate_python(
            {
                "kind": "user_added",
                "segment_anchor": "body/p0",
                "entity_type": "PERSON",
                "original": "Jane Doe",
                "replacement": "[PERSON_1]",
            }
        )
        assert isinstance(decision, UserAddedDecision)

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValidationError):
            self._adapter().validate_python({"kind": "rumor", "proposal_id": "x"})


class TestReviewSession:
    def _session(self, **overrides: object) -> ReviewSession:
        defaults: dict[str, object] = {
            "id": "sess-0001",
            "source_path": Path("/tmp/input.docx"),
            "format": "docx",
            "operator": "replace",
            "segments": [TextSegment(id="body/p0/r0", text="Alice went home")],
            "proposals": [_proposal()],
            "created_at": datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
        }
        defaults.update(overrides)
        return ReviewSession(**defaults)  # type: ignore[arg-type]

    def test_defaults_empty_decisions_and_mappings_and_open_status(self) -> None:
        session = self._session()
        assert session.decisions == []
        assert session.staged_mappings == []
        assert session.status == "open"
        assert session.committed_at is None

    def test_mutable_allows_decision_append(self) -> None:
        session = self._session()
        session.decisions.append(ProposalDecision(proposal_id="abcdef012345", status="accept"))
        assert len(session.decisions) == 1

    def test_mutable_allows_status_transition(self) -> None:
        session = self._session()
        session.status = "committed"
        session.committed_at = datetime(2026, 4, 24, 13, 0, tzinfo=timezone.utc)
        assert session.status == "committed"

    def test_rejects_invalid_status(self) -> None:
        with pytest.raises(ValidationError):
            self._session(status="archived")

    def test_round_trip_preserves_discriminated_decisions(self) -> None:
        session = self._session()
        session.decisions.extend(
            [
                ProposalDecision(proposal_id="abcdef012345", status="accept"),
                UserAddedDecision(
                    segment_anchor="body/p0/r0",
                    entity_type="PERSON",
                    original="Bob",
                    replacement="[PERSON_2]",
                ),
            ]
        )
        restored = ReviewSession.model_validate_json(session.model_dump_json())
        assert isinstance(restored.decisions[0], ProposalDecision)
        assert isinstance(restored.decisions[1], UserAddedDecision)

    def test_staged_mappings_accepts_entries(self) -> None:
        session = self._session()
        session.staged_mappings.append(
            StagedMapping(
                detection_id="abcdef012345",
                entity_type="PERSON",
                original="Alice",
                pseudonym="Priya Patel",
            )
        )
        assert session.staged_mappings[0].pseudonym == "Priya Patel"
