"""Unit tests for `/process-file` — auth, path safety, size cap, format dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sanctum.api.app import create_app
from sanctum.api.routes.pipeline import MAX_INPUT_BYTES
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import (
    AnonymizationError,
    InvalidOperatorParamsError,
    UnsupportedDocumentFormatError,
)
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    StructuredDocument,
    TextSegment,
)

LOOPBACK = {"Host": "127.0.0.1:8765"}
AUTH = {"Authorization": "Bearer t"}


class _FakeAnalyzer:
    def __init__(self, detections: list[DetectionResult]) -> None:
        self._detections = detections

    def analyze(self, text: str, **_: Any) -> list[DetectionResult]:
        return list(self._detections)


class _FakeAnonymizer:
    def __init__(self, result: AnonymizationResult) -> None:
        self._result = result

    def anonymize(self, text: str, **_: Any) -> AnonymizationResult:
        return self._result


def _engine(detections: list[DetectionResult], result: AnonymizationResult) -> SanctumEngine:
    return SanctumEngine(
        analyzer=_FakeAnalyzer(detections),  # type: ignore[arg-type]
        anonymizer=_FakeAnonymizer(result),  # type: ignore[arg-type]
    )


def _client(engine: SanctumEngine | None):
    app = create_app(token="t", host="127.0.0.1", port=8765, engine=engine)
    return app.test_client()


class _StubReader:
    def __init__(self, doc: StructuredDocument) -> None:
        self._doc = doc

    def read(self, _path: Path) -> StructuredDocument:
        return self._doc


class _StubWriter:
    def __init__(self) -> None:
        self.written: tuple[StructuredDocument, Path] | None = None

    def write(self, doc: StructuredDocument, path: Path) -> None:
        self.written = (doc, path)


# ---------- auth + body ----------


def test_process_file_requires_bearer_token(tmp_path: Path):
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    out = tmp_path / "out.docx"
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers=LOOPBACK,
        json={"input_path": str(src), "output_path": str(out)},
    )
    assert r.status_code == 401


def test_process_file_503_when_engine_unconfigured(tmp_path: Path):
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    client = _client(None)
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={"input_path": str(src), "output_path": str(tmp_path / "out.docx")},
    )
    assert r.status_code == 503


def test_process_file_400_on_missing_body():
    client = _client(_engine([], _empty_result()))
    r = client.post("/process-file", headers={**LOOPBACK, **AUTH})
    assert r.status_code == 400


def test_process_file_400_on_unknown_field(tmp_path: Path):
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={
            "input_path": str(tmp_path / "in.docx"),
            "output_path": str(tmp_path / "out.docx"),
            "bogus": True,
        },
    )
    assert r.status_code == 400


# ---------- path safety ----------


@pytest.mark.parametrize(
    "bad_input",
    ["", "relative/path.docx", "//share/file.docx", "\\\\server\\share\\file.docx"],
)
def test_process_file_rejects_unsafe_input_paths(bad_input: str, tmp_path: Path):
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={"input_path": bad_input, "output_path": str(tmp_path / "out.docx")},
    )
    assert r.status_code == 400
    assert "input_path" in r.get_json()["error"]


@pytest.mark.parametrize(
    "bad_output",
    ["", "relative/path.docx", "//share/out.docx", "\\\\server\\share\\out.docx"],
)
def test_process_file_rejects_unsafe_output_paths(bad_output: str, tmp_path: Path):
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={"input_path": str(src), "output_path": bad_output},
    )
    assert r.status_code == 400
    assert "output_path" in r.get_json()["error"]


def test_process_file_400_when_input_missing(tmp_path: Path):
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={
            "input_path": str(tmp_path / "nope.docx"),
            "output_path": str(tmp_path / "out.docx"),
        },
    )
    assert r.status_code == 400
    assert "does not exist" in r.get_json()["error"]


def test_process_file_400_when_input_is_directory(tmp_path: Path):
    src = tmp_path / "adir"
    src.mkdir()
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={"input_path": str(src), "output_path": str(tmp_path / "out.docx")},
    )
    assert r.status_code == 400


# ---------- size + format ----------


def test_process_file_413_when_input_exceeds_cap(tmp_path: Path):
    src = tmp_path / "huge.docx"
    # Sparse file — instant on POSIX, no real allocation needed.
    with open(src, "wb") as fh:
        fh.seek(MAX_INPUT_BYTES + 1)
        fh.write(b"\x00")
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={"input_path": str(src), "output_path": str(tmp_path / "out.docx")},
    )
    assert r.status_code == 413
    assert "exceeds" in r.get_json()["error"]


def test_process_file_415_on_unsupported_format(tmp_path: Path):
    src = tmp_path / "in.weird"
    src.write_bytes(b"x")
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={"input_path": str(src), "output_path": str(tmp_path / "out.weird")},
    )
    assert r.status_code == 415


def test_process_file_rejects_pseudonymize_when_store_locked(tmp_path: Path):
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={
            "input_path": str(src),
            "output_path": str(tmp_path / "out.docx"),
            "operator": "pseudonymize",
        },
    )
    assert r.status_code == 400
    assert "mapping store" in r.get_json()["error"]


# ---------- happy path + error mapping ----------


def _empty_result() -> AnonymizationResult:
    return AnonymizationResult(
        original_text="", anonymized_text="", detections=[], operators_applied={}
    )


def _doc(tmp_path: Path) -> StructuredDocument:
    return StructuredDocument(
        source_path=tmp_path / "in.docx",
        format="docx",
        segments=[TextSegment(id="r1", text="Alice was here")],
    )


def test_process_file_happy_path(tmp_path: Path):
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    out = tmp_path / "out.docx"

    detection = DetectionResult(entity_type="PERSON", start=0, end=5, score=0.99, text_span="Alice")
    result = AnonymizationResult(
        original_text="Alice was here",
        anonymized_text="<PERSON> was here",
        detections=[detection],
        operators_applied={"PERSON": "replace"},
    )
    engine = _engine([detection], result)
    writer = _StubWriter()
    with patch(
        "sanctum.api.routes.pipeline.adapter_for",
        return_value=(_StubReader(_doc(tmp_path)), writer),
    ):
        client = _client(engine)
        r = client.post(
            "/process-file",
            headers={**LOOPBACK, **AUTH},
            json={
                "input_path": str(src),
                "output_path": str(out),
                "operator": "replace",
            },
        )

    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body == {
        "output_path": str(out),
        "segments_changed": 1,
        "entities_replaced": 1,
    }
    assert writer.written is not None
    _, written_path = writer.written
    assert written_path == out


def test_process_file_500_on_document_error(tmp_path: Path):
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")

    class _BoomReader:
        def read(self, _p: Path) -> StructuredDocument:
            raise RuntimeError("read blew up")

    with patch(
        "sanctum.api.routes.pipeline.adapter_for",
        return_value=(_BoomReader(), _StubWriter()),
    ):
        client = _client(_engine([], _empty_result()))
        r = client.post(
            "/process-file",
            headers={**LOOPBACK, **AUTH},
            json={"input_path": str(src), "output_path": str(tmp_path / "out.docx")},
        )

    assert r.status_code == 500
    assert "document" in r.get_json()["error"]


def test_process_file_500_on_pipeline_error(tmp_path: Path):
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")

    detection = DetectionResult(entity_type="PERSON", start=0, end=5, score=0.99, text_span="Alice")

    class _ExplodingAnonymizer:
        def anonymize(self, *_a: Any, **_k: Any) -> AnonymizationResult:
            raise AnonymizationError("operator down")

    engine = SanctumEngine(
        analyzer=_FakeAnalyzer([detection]),  # type: ignore[arg-type]
        anonymizer=_ExplodingAnonymizer(),  # type: ignore[arg-type]
    )
    with patch(
        "sanctum.api.routes.pipeline.adapter_for",
        return_value=(_StubReader(_doc(tmp_path)), _StubWriter()),
    ):
        client = _client(engine)
        r = client.post(
            "/process-file",
            headers={**LOOPBACK, **AUTH},
            json={"input_path": str(src), "output_path": str(tmp_path / "out.docx")},
        )

    assert r.status_code == 500
    assert "pipeline" in r.get_json()["error"]


def test_unsupported_format_path_uses_correct_status(tmp_path: Path):
    """Unsupported format from adapter_for itself (not from extension) → 415."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")

    def _raise(_: Path):
        raise UnsupportedDocumentFormatError("no go")

    with patch("sanctum.api.routes.pipeline.adapter_for", side_effect=_raise):
        client = _client(_engine([], _empty_result()))
        r = client.post(
            "/process-file",
            headers={**LOOPBACK, **AUTH},
            json={"input_path": str(src), "output_path": str(tmp_path / "out.docx")},
        )
    assert r.status_code == 415


@pytest.mark.skipif(
    __import__("os").name == "nt",
    reason="POSIX symlink semantics",
)
def test_process_file_rejects_symlinked_input(tmp_path: Path):
    """A symlink under a user-owned dir pointing at a network mount would
    defeat the airgap — reject any path where realpath != abspath."""
    real_src = tmp_path / "real.docx"
    real_src.write_bytes(b"x")
    link = tmp_path / "link.docx"
    link.symlink_to(real_src)

    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={"input_path": str(link), "output_path": str(tmp_path / "out.docx")},
    )
    assert r.status_code == 400
    assert "symlink" in r.get_json()["error"]


def test_max_content_length_caps_body(tmp_path: Path):
    """A body bigger than MAX_INPUT_BYTES should be refused at the WSGI layer."""
    client = _client(_engine([], _empty_result()))
    big = "a" * (MAX_INPUT_BYTES + 1)
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH, "Content-Type": "application/json"},
        data=big,
    )
    # Werkzeug returns 413 for over-cap bodies.
    assert r.status_code == 413


def test_process_file_400_on_api_unsupported_operator(tmp_path: Path):
    """Same schema-level rejection applies to /process-file — `custom` only."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    out = tmp_path / "out.docx"
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={"input_path": str(src), "output_path": str(out), "operator": "custom"},
    )
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "invalid request"
    assert any("operator" in d["loc"] for d in body["details"])


def test_process_file_400_when_operator_params_without_operator(tmp_path: Path):
    """`operator_params` without `operator` is rejected at validation."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    client = _client(_engine([], _empty_result()))
    r = client.post(
        "/process-file",
        headers={**LOOPBACK, **AUTH},
        json={
            "input_path": str(src),
            "output_path": str(tmp_path / "out.docx"),
            "operator_params": {"masking_char": "*"},
        },
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid request"


def test_process_file_review_true_returns_501_when_unsupported(tmp_path: Path):
    """WS1 ships /process-file's review flag, but no adapter implements
    emit_review yet — that path must surface a 501 instead of 500."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    out = tmp_path / "out.docx"

    with patch(
        "sanctum.api.routes.pipeline.adapter_for",
        return_value=(_StubReader(_doc(tmp_path)), _StubWriter()),
    ):
        client = _client(_engine([], _empty_result()))
        r = client.post(
            "/process-file",
            headers={**LOOPBACK, **AUTH},
            json={
                "input_path": str(src),
                "output_path": str(out),
                "review": True,
            },
        )
    assert r.status_code == 501, r.get_json()
    assert "review not yet supported" in r.get_json()["error"]


def test_process_file_400_on_invalid_operator_params(tmp_path: Path):
    """InvalidOperatorParamsError from the pipeline → 400, not 500."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")

    detection = DetectionResult(entity_type="PERSON", start=0, end=5, score=0.99, text_span="Alice")

    class _BadParamAnonymizer:
        def anonymize(self, *_a: Any, **_k: Any) -> AnonymizationResult:
            raise InvalidOperatorParamsError("masking_char must be a single char")

    engine = SanctumEngine(
        analyzer=_FakeAnalyzer([detection]),  # type: ignore[arg-type]
        anonymizer=_BadParamAnonymizer(),  # type: ignore[arg-type]
    )
    with patch(
        "sanctum.api.routes.pipeline.adapter_for",
        return_value=(_StubReader(_doc(tmp_path)), _StubWriter()),
    ):
        client = _client(engine)
        r = client.post(
            "/process-file",
            headers={**LOOPBACK, **AUTH},
            json={
                "input_path": str(src),
                "output_path": str(tmp_path / "out.docx"),
                "operator": "mask",
                "operator_params": {"chars_to_mask": -1},
            },
        )
    assert r.status_code == 400
    assert "invalid operator_params" in r.get_json()["error"]
