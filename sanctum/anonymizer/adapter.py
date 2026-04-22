from __future__ import annotations

from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import InvalidParamError, OperatorConfig, RecognizerResult
from sanctum.anonymizer.operators.hips import HipsOperator
from sanctum.anonymizer.operators.pseudonymize import PseudonymizeOperator
from sanctum.core.exceptions import InvalidOperatorParamsError
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

        try:
            result = self._engine.anonymize(
                text=text,
                analyzer_results=recognizer_results,
                operators=operator_configs,
            )
        except InvalidParamError as exc:
            # Presidio raises this for bad operator params — e.g. `mask`
            # missing `masking_char`, `encrypt` with the wrong key length.
            # Promote to our own subclass so the HTTP layer can map it to
            # a 400 instead of the generic AnonymizationError → 500.
            raise InvalidOperatorParamsError(str(exc)) from exc

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
            per_detection_replacements=self._replacements_for(detections, result),
        )

    @staticmethod
    def _replacements_for(
        detections: list[DetectionResult],
        engine_result: object,
    ) -> list[str] | None:
        """Line up Presidio's per-item replacement text with the input detections.

        Presidio processes detections right-to-left (end of text first) and
        returns ``EngineResult.items`` in that same reverse-start order, one
        entry per input detection. Sorting the original detections by
        ``start`` descending and zipping gives us the correct pairing back —
        which is what review-mode writers need to build a per-detection
        trailer.

        Returns ``None`` if the engine result doesn't expose an iterable
        ``items`` of the right length; the only caller (review emission)
        treats that as "refuse to emit" rather than guessing replacements.
        """
        raw_items = getattr(engine_result, "items", None)
        try:
            items = list(raw_items) if raw_items is not None else []
        except TypeError:
            return None
        if len(items) != len(detections):
            return None

        sorted_idx = sorted(range(len(detections)), key=lambda i: detections[i].start, reverse=True)
        replacements: list[str] = [""] * len(detections)
        for k, item in enumerate(items):
            replacements[sorted_idx[k]] = getattr(item, "text", "")
        return replacements
