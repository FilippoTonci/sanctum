from __future__ import annotations

from pathlib import Path

import pytest
from sanctum.analyzer.adapter import PresidioAnalyzer
from tests.evaluation.scorer import EntityScorer, ScoringReport

pytestmark = pytest.mark.evaluation


@pytest.fixture()
def analyzer() -> PresidioAnalyzer:
    return PresidioAnalyzer()


class TestCorpusScoring:
    def test_corpus_scoring(
        self,
        analyzer: PresidioAnalyzer,
        fixture_pairs: list[tuple[str, dict]],
        scorer: EntityScorer,
    ) -> None:
        """Smoke test: run analyzer on all fixtures and score against ground truth."""
        assert len(fixture_pairs) > 0, "No fixture pairs found"

        all_reports: list[tuple[str, ScoringReport]] = []

        for text, annotations in fixture_pairs:
            doc_name = annotations.get("document", "<unknown>")
            ground_truth = annotations["entities"]

            predicted = analyzer.analyze(text)
            report = scorer.score(predicted, ground_truth)
            all_reports.append((doc_name, report))

        # Print summary table
        print("\n" + "=" * 80)
        print(f"{'Document':<35} {'Precision':>10} {'Recall':>10} {'F1':>10}")
        print("-" * 80)
        for doc_name, report in all_reports:
            o = report.overall
            print(f"{doc_name:<35} {o.precision:>10.3f} {o.recall:>10.3f} {o.f1:>10.3f}")

        # Print per-entity aggregate
        entity_tp: dict[str, int] = {}
        entity_fp: dict[str, int] = {}
        entity_fn: dict[str, int] = {}
        for _, report in all_reports:
            for etype, metrics in report.per_entity.items():
                entity_tp[etype] = entity_tp.get(etype, 0) + metrics.true_positives
                entity_fp[etype] = entity_fp.get(etype, 0) + metrics.false_positives
                entity_fn[etype] = entity_fn.get(etype, 0) + metrics.false_negatives

        print("\n" + "=" * 80)
        print(f"{'Entity Type':<25} {'TP':>6} {'FP':>6} {'FN':>6} {'F1':>10}")
        print("-" * 80)
        for etype in sorted(entity_tp.keys() | entity_fp.keys() | entity_fn.keys()):
            tp = entity_tp.get(etype, 0)
            fp = entity_fp.get(etype, 0)
            fn = entity_fn.get(etype, 0)
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            print(f"{etype:<25} {tp:>6} {fp:>6} {fn:>6} {f1:>10.3f}")
        print("=" * 80)

        # Smoke test: at least some entities should be detected across the corpus
        total_tp = sum(entity_tp.values())
        assert total_tp > 0, "Analyzer detected zero true positives across the entire corpus"


class TestZeroPii:
    def test_zero_pii_false_positives(self, analyzer: PresidioAnalyzer) -> None:
        """Technical text with no PII should produce zero or very few detections."""
        fixture_path = (
            Path(__file__).parent.parent / "fixtures" / "edge_cases" / "zero_pii_technical.txt"
        )
        text = fixture_path.read_text(encoding="utf-8")

        results = analyzer.analyze(text)

        # Allow a tolerance for false positives — Presidio's NER may flag
        # technical terms (e.g. "asyncio" as LOCATION, "loop.run" as URL).
        assert len(results) <= 6, (
            f"Expected very few detections on PII-free text, got {len(results)}: "
            f"{[(r.entity_type, r.text_span) for r in results]}"
        )
