"""Per-decision preview computation for review sessions (Flow B).

The UI displays a ghosted "what the committed replacement would look
like" next to each proposal. That preview is computed server-side —
never by the UI — by running the user-chosen operator against a single
synthetic detection. Custom replacements short-circuit the operator
(the literal *is* the preview).

**Preview must not mint.** For pseudonymize, calling this function with
the standard anonymizer path today writes to ``MappingStore`` as a side
effect. WS4 tightens that by either plumbing a read-only peek or using
a detached seeded Faker so the preview returns the pseudonym that
commit *will* produce, without persisting. Until then, callers should
be aware that previewing pseudonymize in a hot loop is not side-effect-
free.
"""

from __future__ import annotations

from typing import Any

from sanctum.core.models import DetectionResult, OperatorPolicy, ReviewProposal
from sanctum.core.protocols import Anonymizer


def compute_preview(
    proposal: ReviewProposal,
    operator: str,
    operator_params: dict[str, Any] | None,
    custom_replacement: str | None,
    anonymizer: Anonymizer,
) -> str:
    """Return the replacement the UI should ghost next to ``proposal``.

    If ``custom_replacement`` is set, it *is* the preview — the operator
    is bypassed entirely.

    Otherwise, runs ``anonymizer`` against a single synthetic detection
    covering the whole of ``proposal.original`` under the chosen
    operator, and returns the anonymized text.
    """
    if custom_replacement is not None:
        return custom_replacement

    synthetic = DetectionResult(
        entity_type=proposal.entity_type,
        start=0,
        end=len(proposal.original),
        score=proposal.score,
        text_span=proposal.original,
    )
    policy = OperatorPolicy(operator_name=operator, params=operator_params or {})
    result = anonymizer.anonymize(
        text=proposal.original,
        detections=[synthetic],
        operator_policies={"DEFAULT": policy},
    )
    return result.anonymized_text
