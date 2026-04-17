from __future__ import annotations

from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig, RecognizerResult
from sanctum.anonymizer.operators.hips import HipsOperator
from sanctum.anonymizer.operators.pseudonymize import PseudonymizeOperator
from sanctum.core.models import AnonymizationResult, DetectionResult, OperatorPolicy


class PresidioAnonymizer:
    """Wraps presidio-anonymizer's AnonymizerEngine for PII redaction."""

    def __init__(self, default_operator: str = "replace") -> None:
        self._engine = AnonymizerEngine()
        # Register every Sanctum-custom operator up front. Forgetting one
        # here surfaces as an "Invalid operator class" 500 only when a
        # caller asks for it — exactly how HipsOperator silently regressed.
        self._engine.add_anonymizer(PseudonymizeOperator)
        self._engine.add_anonymizer(HipsOperator)
        self._default_operator = default_operator

    def anonymize(
        self,
        text: str,
        detections: list[DetectionResult],
        operator_policies: dict[str, OperatorPolicy] | None = None,
    ) -> AnonymizationResult:
        recognizer_results = [
            RecognizerResult(
                entity_type=d.entity_type,
                start=d.start,
                end=d.end,
                score=d.score,
            )
            for d in detections
        ]

        if operator_policies:
            operator_configs = {
                key: OperatorConfig(policy.operator_name, policy.params)
                for key, policy in operator_policies.items()
            }
        else:
            operator_configs = {"DEFAULT": OperatorConfig(self._default_operator)}

        result = self._engine.anonymize(
            text=text,
            analyzer_results=recognizer_results,
            operators=operator_configs,
        )

        # When a caller passes a single `{"DEFAULT": ...}` policy (the shape
        # the HTTP routes use for a per-request `operator`), that policy is
        # what Presidio actually applies to every detection. Treat it as the
        # effective default for telemetry — otherwise `operators_applied`
        # would keep reporting `self._default_operator` (usually "replace")
        # and lie about what the engine just did.
        effective_default = self._default_operator
        if operator_policies is not None and "DEFAULT" in operator_policies:
            effective_default = operator_policies["DEFAULT"].operator_name

        operators_applied: dict[str, str] = {}
        for d in detections:
            if operator_policies and d.entity_type in operator_policies:
                operators_applied[d.entity_type] = operator_policies[d.entity_type].operator_name
            else:
                operators_applied[d.entity_type] = effective_default

        return AnonymizationResult(
            original_text=text,
            anonymized_text=result.text,
            detections=detections,
            operators_applied=operators_applied,
        )
