"""Per-decision preview computation for review sessions (Flow B).

The UI displays a ghosted "what the committed replacement would look
like" next to each proposal. That preview is computed server-side —
never by the UI — by running the user-chosen operator against a single
synthetic detection. Custom replacements short-circuit the operator
(the literal *is* the preview).

For pseudonymize, the caller passes a ``MappingStore`` (typically a
``PreviewMappingStore`` wrapping the real encrypted store) so the
lookup can reuse an already-minted pseudonym without persisting any
new ones. The same function is reused from the commit path in the
engine — passing the *real* store there — so preview and commit share
exactly one rendering code path.
"""

from __future__ import annotations

from typing import Any

from sanctum.core.models import DetectionResult, OperatorPolicy, ReviewProposal
from sanctum.core.protocols import Anonymizer, MappingStore


def compute_preview(
    proposal: ReviewProposal,
    operator: str,
    operator_params: dict[str, Any] | None,
    custom_replacement: str | None,
    anonymizer: Anonymizer,
    mapping_store: MappingStore | None = None,
) -> str:
    """Return the replacement the UI should ghost next to ``proposal``.

    If ``custom_replacement`` is set, it *is* the preview — the operator
    is bypassed entirely.

    Otherwise, runs ``anonymizer`` against a single synthetic detection
    covering the whole of ``proposal.original`` under the chosen
    operator, and returns the anonymized text. Operator ``pseudonymize``
    additionally requires ``mapping_store`` — pass a
    ``PreviewMappingStore`` for preview (non-persisting) or the real
    store for commit.
    """
    if custom_replacement is not None:
        return custom_replacement

    params: dict[str, Any] = dict(operator_params or {})
    if operator == "pseudonymize":
        if mapping_store is None:
            raise ValueError(
                "compute_preview requires a `mapping_store` when operator is 'pseudonymize'"
            )
        params.setdefault("store", mapping_store)

    synthetic = DetectionResult(
        entity_type=proposal.entity_type,
        start=0,
        end=len(proposal.original),
        score=proposal.score,
        text_span=proposal.original,
    )
    policy = OperatorPolicy(operator_name=operator, params=params)
    result = anonymizer.anonymize(
        text=proposal.original,
        detections=[synthetic],
        operator_policies={"DEFAULT": policy},
    )
    return result.anonymized_text
