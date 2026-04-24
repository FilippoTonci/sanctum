"""Tests for per-decision preview computation (Flow B)."""

from __future__ import annotations

import pytest
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.core.models import ReviewProposal
from sanctum.core.review.previews import compute_preview


@pytest.fixture(scope="module")
def anonymizer() -> PresidioAnonymizer:
    return PresidioAnonymizer()


def _proposal(**overrides: object) -> ReviewProposal:
    defaults: dict[str, object] = {
        "detection_id": "abcdef012345",
        "entity_type": "PERSON",
        "score": 0.9,
        "original": "Alice Smith",
        "segment_anchor": "s0",
    }
    defaults.update(overrides)
    return ReviewProposal(**defaults)  # type: ignore[arg-type]


def test_custom_replacement_short_circuits_anonymizer(
    anonymizer: PresidioAnonymizer,
) -> None:
    """Custom replacement literal is the preview — operator isn't consulted."""
    preview = compute_preview(
        proposal=_proposal(),
        operator="hips",  # would produce a synthetic name
        operator_params=None,
        custom_replacement="[DEFENDANT]",
        anonymizer=anonymizer,
    )
    assert preview == "[DEFENDANT]"


def test_replace_returns_entity_tag(anonymizer: PresidioAnonymizer) -> None:
    """Default replace operator produces the ``<ENTITY>`` tag."""
    preview = compute_preview(
        proposal=_proposal(),
        operator="replace",
        operator_params=None,
        custom_replacement=None,
        anonymizer=anonymizer,
    )
    assert preview == "<PERSON>"


def test_replace_with_new_value_param(anonymizer: PresidioAnonymizer) -> None:
    """Operator params pass through to the anonymizer."""
    preview = compute_preview(
        proposal=_proposal(),
        operator="replace",
        operator_params={"new_value": "[REDACTED]"},
        custom_replacement=None,
        anonymizer=anonymizer,
    )
    assert preview == "[REDACTED]"


def test_redact_blanks_the_span(anonymizer: PresidioAnonymizer) -> None:
    preview = compute_preview(
        proposal=_proposal(),
        operator="redact",
        operator_params=None,
        custom_replacement=None,
        anonymizer=anonymizer,
    )
    assert preview == ""


def test_mask_respects_masking_char(anonymizer: PresidioAnonymizer) -> None:
    preview = compute_preview(
        proposal=_proposal(),
        operator="mask",
        operator_params={
            "masking_char": "*",
            "chars_to_mask": 5,
            "from_end": True,
        },
        custom_replacement=None,
        anonymizer=anonymizer,
    )
    # "Alice Smith" → last five masked → "Alice *****"
    assert preview.endswith("*****")
    assert preview[:-5] == "Alice Smith"[:-5]


def test_hips_returns_a_synthetic_name(anonymizer: PresidioAnonymizer) -> None:
    """HIPS is deterministic per-session once seeded; we assert shape not exact value."""
    preview = compute_preview(
        proposal=_proposal(),
        operator="hips",
        operator_params=None,
        custom_replacement=None,
        anonymizer=anonymizer,
    )
    # HIPS should produce *something* that isn't the original or an entity tag.
    assert preview != "Alice Smith"
    assert preview != "<PERSON>"
    assert preview != ""


def test_empty_custom_replacement_is_respected(
    anonymizer: PresidioAnonymizer,
) -> None:
    """An explicit empty string is a valid custom replacement (full redaction)."""
    preview = compute_preview(
        proposal=_proposal(),
        operator="replace",
        operator_params=None,
        custom_replacement="",
        anonymizer=anonymizer,
    )
    assert preview == ""
