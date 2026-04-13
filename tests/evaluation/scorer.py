from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sanctum.core.models import DetectionResult


@dataclass(frozen=True)
class EntityMetrics:
    """Precision, recall, and F1 for a single entity type or overall."""

    precision: float
    recall: float
    f1: float
    true_positives: int
    false_positives: int
    false_negatives: int


@dataclass(frozen=True)
class ScoringReport:
    """Full evaluation result across all entity types."""

    per_entity: dict[str, EntityMetrics]
    overall: EntityMetrics
    false_positives: list[DetectionResult]
    false_negatives: list[dict]


class EntityScorer:
    """Scores PII detection against ground truth annotations."""

    class MatchMode(Enum):
        EXACT = "exact"
        OVERLAP = "overlap"

    def score(
        self,
        predicted: list[DetectionResult],
        ground_truth: list[dict],
        mode: MatchMode = MatchMode.OVERLAP,
    ) -> ScoringReport:
        all_entity_types = set()
        for p in predicted:
            all_entity_types.add(p.entity_type)
        for gt in ground_truth:
            all_entity_types.add(gt["entity_type"])

        per_entity: dict[str, EntityMetrics] = {}
        all_fp_detections: list[DetectionResult] = []
        all_fn_annotations: list[dict] = []

        for entity_type in sorted(all_entity_types):
            preds = [p for p in predicted if p.entity_type == entity_type]
            gts = [gt for gt in ground_truth if gt["entity_type"] == entity_type]

            tp, fps, fns = self._match_entities(preds, gts, mode)

            all_fp_detections.extend(fps)
            all_fn_annotations.extend(fns)

            precision = tp / (tp + len(fps)) if (tp + len(fps)) > 0 else 0.0
            recall = tp / (tp + len(fns)) if (tp + len(fns)) > 0 else 0.0
            f1 = (
                (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
            )

            per_entity[entity_type] = EntityMetrics(
                precision=precision,
                recall=recall,
                f1=f1,
                true_positives=tp,
                false_positives=len(fps),
                false_negatives=len(fns),
            )

        overall = self._macro_average(per_entity)

        return ScoringReport(
            per_entity=per_entity,
            overall=overall,
            false_positives=all_fp_detections,
            false_negatives=all_fn_annotations,
        )

    def _match_entities(
        self,
        predicted: list[DetectionResult],
        ground_truth: list[dict],
        mode: MatchMode,
    ) -> tuple[int, list[DetectionResult], list[dict]]:
        """Match predicted against ground truth.

        Returns ``(tp_count, unmatched_preds, unmatched_gts)``.
        """
        # Sort predictions by score descending for greedy matching
        sorted_preds = sorted(predicted, key=lambda p: p.score, reverse=True)
        matched_gt_indices: set[int] = set()
        matched_pred_indices: set[int] = set()

        for pred_idx, pred in enumerate(sorted_preds):
            for gt_idx, gt in enumerate(ground_truth):
                if gt_idx in matched_gt_indices:
                    continue
                if self._is_match(pred, gt, mode):
                    matched_gt_indices.add(gt_idx)
                    matched_pred_indices.add(pred_idx)
                    break

        tp = len(matched_gt_indices)
        fps = [p for i, p in enumerate(sorted_preds) if i not in matched_pred_indices]
        fns = [gt for i, gt in enumerate(ground_truth) if i not in matched_gt_indices]

        return tp, fps, fns

    def _is_match(self, pred: DetectionResult, gt: dict, mode: MatchMode) -> bool:
        if mode == self.MatchMode.EXACT:
            return pred.start == gt["start"] and pred.end == gt["end"]
        else:
            # OVERLAP: [start, end) ranges must intersect
            return pred.start < gt["end"] and pred.end > gt["start"]

    def _macro_average(self, per_entity: dict[str, EntityMetrics]) -> EntityMetrics:
        if not per_entity:
            return EntityMetrics(
                precision=0.0,
                recall=0.0,
                f1=0.0,
                true_positives=0,
                false_positives=0,
                false_negatives=0,
            )

        total_tp = sum(m.true_positives for m in per_entity.values())
        total_fp = sum(m.false_positives for m in per_entity.values())
        total_fn = sum(m.false_negatives for m in per_entity.values())

        avg_precision = sum(m.precision for m in per_entity.values()) / len(per_entity)
        avg_recall = sum(m.recall for m in per_entity.values()) / len(per_entity)
        avg_f1 = sum(m.f1 for m in per_entity.values()) / len(per_entity)

        return EntityMetrics(
            precision=avg_precision,
            recall=avg_recall,
            f1=avg_f1,
            true_positives=total_tp,
            false_positives=total_fp,
            false_negatives=total_fn,
        )
