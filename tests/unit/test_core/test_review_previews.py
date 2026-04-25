"""Tests for per-decision preview computation (Flow B)."""

from __future__ import annotations

import pytest
from sanctum.anonymizer.adapter import PresidioAnonymizer
from sanctum.core.models import ReviewProposal
from sanctum.core.review.preview_store import PreviewMappingStore
from sanctum.core.review.previews import compute_preview
from sanctum.security.mapping_store import InMemoryMappingStore


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
        "start": 0,
        "end": 11,
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


# ---- pseudonymize previews (WS4) -----------------------------------------


def test_pseudonymize_preview_requires_mapping_store(
    anonymizer: PresidioAnonymizer,
) -> None:
    """Without a store the preview call must fail loudly, not silently mint."""
    with pytest.raises(ValueError, match="mapping_store"):
        compute_preview(
            proposal=_proposal(),
            operator="pseudonymize",
            operator_params=None,
            custom_replacement=None,
            anonymizer=anonymizer,
        )


def test_pseudonymize_preview_does_not_persist(
    anonymizer: PresidioAnonymizer,
) -> None:
    """Preview through PreviewMappingStore must leave the real store untouched."""
    real = InMemoryMappingStore()
    preview_store = PreviewMappingStore(real)

    preview = compute_preview(
        proposal=_proposal(),
        operator="pseudonymize",
        operator_params=None,
        custom_replacement=None,
        anonymizer=anonymizer,
        mapping_store=preview_store,
    )
    assert preview != "Alice Smith"
    # The real store must have seen zero writes.
    assert real.peek("Alice Smith", "PERSON") is None
    assert real.reverse(preview, "PERSON") is None


def test_pseudonymize_preview_matches_commit_for_fresh_pseudonym(
    anonymizer: PresidioAnonymizer,
) -> None:
    """Preview (non-persisting) and commit (persisting) mint the same value.

    Deterministic Faker seeding in the operator is the load-bearing bit —
    without it the two call paths drift and the reviewer sees one value
    pre-commit and a different one in the committed file.
    """
    # Separate stores so the preview cannot leak into the commit path.
    preview_real = InMemoryMappingStore()
    commit_real = InMemoryMappingStore()

    preview = compute_preview(
        proposal=_proposal(),
        operator="pseudonymize",
        operator_params=None,
        custom_replacement=None,
        anonymizer=anonymizer,
        mapping_store=PreviewMappingStore(preview_real),
    )
    commit = compute_preview(
        proposal=_proposal(),
        operator="pseudonymize",
        operator_params=None,
        custom_replacement=None,
        anonymizer=anonymizer,
        mapping_store=commit_real,
    )
    assert preview == commit
    # Commit path persists; preview path does not.
    assert commit_real.peek("Alice Smith", "PERSON") == commit
    assert preview_real.peek("Alice Smith", "PERSON") is None


def test_pseudonymize_preview_reuses_existing_mapping(
    anonymizer: PresidioAnonymizer,
) -> None:
    """An already-minted pseudonym shows up verbatim in the preview."""
    real = InMemoryMappingStore()
    # Simulate a prior committed session having minted a pseudonym for
    # the same (entity_type, original).
    real.get_or_create("Alice Smith", "PERSON", lambda: "Priya Patel")

    preview = compute_preview(
        proposal=_proposal(),
        operator="pseudonymize",
        operator_params=None,
        custom_replacement=None,
        anonymizer=anonymizer,
        mapping_store=PreviewMappingStore(real),
    )
    assert preview == "Priya Patel"
