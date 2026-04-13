from __future__ import annotations

from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig, RecognizerResult
from sanctum.core.models import AnonymizationResult, DetectionResult, OperatorPolicy


class PresidioAnonymizer:
    """Wraps presidio-anonymizer's AnonymizerEngine for PII redaction."""

    def __init__(self, default_operator: str = "replace") -> None:
        self._engine = AnonymizerEngine()
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

        operators_applied: dict[str, str] = {}
        for d in detections:
            if operator_policies and d.entity_type in operator_policies:
                operators_applied[d.entity_type] = operator_policies[d.entity_type].operator_name
            else:
                operators_applied[d.entity_type] = self._default_operator

        return AnonymizationResult(
            original_text=text,
            anonymized_text=result.text,
            detections=detections,
            operators_applied=operators_applied,
        )
