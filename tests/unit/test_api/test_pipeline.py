"""Unit tests for `/analyze` + `/anonymize` — auth, validation, error mapping."""

from __future__ import annotations

from typing import Any

import pytest
from sanctum.api.app import create_app
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import AnalysisError, AnonymizationError
from sanctum.core.models import AnonymizationResult, DetectionResult, OperatorPolicy

LOOPBACK = {"Host": "127.0.0.1:8765"}
AUTH = {"Authorization": "Bearer t"}


class _FakeAnalyzer:
    def __init__(self, detections: list[DetectionResult] | Exception) -> None:
        self._detections = detections
        self.calls: list[dict[str, Any]] = []

    def analyze(
        self,
        text: str,
        language: str = "en",
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[DetectionResult]:
        self.calls.append(
            {
                "text": text,
                "language": language,
                "entities": entities,
                "score_threshold": score_threshold,
            }
        )
        if isinstance(self._detections, Exception):
            raise self._detections
        return list(self._detections)


class _FakeAnonymizer:
    def __init__(self, result: AnonymizationResult | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def anonymize(
        self,
        text: str,
        detections: list[DetectionResult],
        operator_policies: dict[str, OperatorPolicy] | None = None,
    ) -> AnonymizationResult:
        self.calls.append(
            {"text": text, "detections": detections, "operator_policies": operator_policies}
        )
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _engine(
    *,
    detections: list[DetectionResult] | Exception | None = None,
    result: AnonymizationResult | Exception | None = None,
) -> tuple[SanctumEngine, _FakeAnalyzer, _FakeAnonymizer]:
    analyzer = _FakeAnalyzer(detections if detections is not None else [])
    anonymizer = _FakeAnonymizer(
        result
        if result is not None
        else AnonymizationResult(
            original_text="",
            anonymized_text="",
            detections=[],
            operators_applied={},
        )
    )
    return SanctumEngine(analyzer=analyzer, anonymizer=anonymizer), analyzer, anonymizer  # type: ignore[arg-type]


def _client(engine: SanctumEngine | None):
    app = create_app(token="t", host="127.0.0.1", port=8765, engine=engine)
    return app.test_client()


# ---------- /analyze ----------


def test_analyze_requires_bearer_token():
    engine, *_ = _engine()
    client = _client(engine)
    r = client.post("/analyze", headers=LOOPBACK, json={"text": "hi"})
    assert r.status_code == 401


def test_analyze_503_when_engine_unconfigured():
    client = _client(None)
    r = client.post("/analyze", headers={**LOOPBACK, **AUTH}, json={"text": "hi"})
    assert r.status_code == 503
    assert "engine" in r.get_json()["error"]


def test_analyze_400_on_missing_body():
    engine, *_ = _engine()
    client = _client(engine)
    r = client.post("/analyze", headers={**LOOPBACK, **AUTH})
    assert r.status_code == 400


def test_analyze_400_on_non_object_body():
    engine, *_ = _engine()
    client = _client(engine)
    r = client.post("/analyze", headers={**LOOPBACK, **AUTH}, json="just a string")
    assert r.status_code == 400


def test_analyze_400_on_empty_text():
    engine, *_ = _engine()
    client = _client(engine)
    r = client.post("/analyze", headers={**LOOPBACK, **AUTH}, json={"text": ""})
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "invalid request"
    assert any("text" in d["loc"] for d in body["details"])


def test_analyze_400_on_unknown_field():
    engine, *_ = _engine()
    client = _client(engine)
    r = client.post(
        "/analyze",
        headers={**LOOPBACK, **AUTH},
        json={"text": "hi", "bogus": True},
    )
    assert r.status_code == 400


def test_analyze_400_on_threshold_out_of_range():
    engine, *_ = _engine()
    client = _client(engine)
    r = client.post(
        "/analyze",
        headers={**LOOPBACK, **AUTH},
        json={"text": "hi", "score_threshold": 1.5},
    )
    assert r.status_code == 400


def test_analyze_happy_path_passes_through_to_engine():
    detection = DetectionResult(
        entity_type="PERSON",
        start=0,
        end=5,
        score=0.99,
        text_span="Alice",
    )
    engine, analyzer, _ = _engine(detections=[detection])
    client = _client(engine)
    r = client.post(
        "/analyze",
        headers={**LOOPBACK, **AUTH},
        json={
            "text": "Alice was here",
            "language": "en",
            "entities": ["PERSON"],
            "score_threshold": 0.5,
        },
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["count"] == 1
    assert body["detections"][0]["entity_type"] == "PERSON"
    assert body["detections"][0]["text_span"] == "Alice"
    # Engine knobs were threaded through verbatim.
    call = analyzer.calls[0]
    assert call["language"] == "en"
    assert call["entities"] == ["PERSON"]
    assert call["score_threshold"] == 0.5


def test_analyze_500_on_analysis_error():
    engine, *_ = _engine(detections=AnalysisError("boom"))
    client = _client(engine)
    r = client.post("/analyze", headers={**LOOPBACK, **AUTH}, json={"text": "hi"})
    assert r.status_code == 500
    assert "analysis failed" in r.get_json()["error"]


# ---------- /anonymize ----------


def test_anonymize_requires_bearer_token():
    engine, *_ = _engine()
    client = _client(engine)
    r = client.post("/anonymize", headers=LOOPBACK, json={"text": "hi"})
    assert r.status_code == 401


def test_anonymize_503_when_engine_unconfigured():
    client = _client(None)
    r = client.post("/anonymize", headers={**LOOPBACK, **AUTH}, json={"text": "hi"})
    assert r.status_code == 503


def test_anonymize_400_on_missing_body():
    engine, *_ = _engine()
    client = _client(engine)
    r = client.post("/anonymize", headers={**LOOPBACK, **AUTH})
    assert r.status_code == 400


def test_anonymize_rejects_pseudonymize_until_ws47():
    engine, *_ = _engine()
    client = _client(engine)
    r = client.post(
        "/anonymize",
        headers={**LOOPBACK, **AUTH},
        json={"text": "Alice", "operator": "pseudonymize"},
    )
    assert r.status_code == 400
    assert "mapping store" in r.get_json()["error"]


def test_anonymize_happy_path_returns_full_result():
    detection = DetectionResult(
        entity_type="PERSON",
        start=0,
        end=5,
        score=0.99,
        text_span="Alice",
    )
    result = AnonymizationResult(
        original_text="Alice was here",
        anonymized_text="<PERSON> was here",
        detections=[detection],
        operators_applied={"PERSON": "replace"},
    )
    engine, _, anonymizer = _engine(detections=[detection], result=result)
    client = _client(engine)
    r = client.post(
        "/anonymize",
        headers={**LOOPBACK, **AUTH},
        json={"text": "Alice was here", "operator": "replace"},
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["original_text"] == "Alice was here"
    assert body["anonymized_text"] == "<PERSON> was here"
    assert body["operators_applied"] == {"PERSON": "replace"}
    # Operator threaded into the policy under the DEFAULT key.
    policies = anonymizer.calls[0]["operator_policies"]
    assert policies is not None
    assert policies["DEFAULT"].operator_name == "replace"


def test_anonymize_no_operator_uses_engine_default():
    """`operator` omitted ⇒ no policies dict ⇒ engine falls back to its own default."""
    result = AnonymizationResult(
        original_text="hi", anonymized_text="hi", detections=[], operators_applied={}
    )
    engine, _, anonymizer = _engine(result=result)
    client = _client(engine)
    r = client.post("/anonymize", headers={**LOOPBACK, **AUTH}, json={"text": "hi"})
    assert r.status_code == 200
    assert anonymizer.calls[0]["operator_policies"] is None


@pytest.mark.parametrize(
    "exc",
    [AnalysisError("upstream blew up"), AnonymizationError("operator failed")],
)
def test_anonymize_500_on_pipeline_errors(exc: Exception):
    engine, *_ = _engine(result=exc)
    client = _client(engine)
    r = client.post("/anonymize", headers={**LOOPBACK, **AUTH}, json={"text": "hi"})
    assert r.status_code == 500
    assert "pipeline failed" in r.get_json()["error"]
