"""Unit tests for `scripts/check_api_compat.py`."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "check_api_compat.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("_sanctum_check_api_compat", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _base_spec() -> dict[str, Any]:
    return {
        "paths": {
            "/health": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/analyze": {"post": {"responses": {"200": {"description": "OK"}}}},
        },
        "components": {
            "schemas": {
                "AnalyzeRequest": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "language": {"type": "string"}},
                    "required": ["text"],
                },
                "AnalyzeResponse": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                },
            },
        },
    }


def test_identical_specs_produce_no_breaks() -> None:
    gen = _load()
    assert gen.find_breaks(_base_spec(), _base_spec()) == []


def test_adding_a_route_is_not_a_break() -> None:
    gen = _load()
    current = _base_spec()
    current["paths"]["/anonymize"] = {"post": {"responses": {"200": {"description": "OK"}}}}
    assert gen.find_breaks(_base_spec(), current) == []


def test_adding_optional_request_field_is_not_a_break() -> None:
    gen = _load()
    current = _base_spec()
    current["components"]["schemas"]["AnalyzeRequest"]["properties"]["threshold"] = {
        "type": "number"
    }
    # Not in `required`, so additive.
    assert gen.find_breaks(_base_spec(), current) == []


def test_adding_response_field_is_not_a_break() -> None:
    gen = _load()
    current = _base_spec()
    current["components"]["schemas"]["AnalyzeResponse"]["properties"]["count_by_type"] = {
        "type": "object"
    }
    assert gen.find_breaks(_base_spec(), current) == []


def test_removed_route_is_a_break() -> None:
    gen = _load()
    current = _base_spec()
    del current["paths"]["/analyze"]
    breaks = gen.find_breaks(_base_spec(), current)
    assert any("route removed" in b and "/analyze" in b for b in breaks)


def test_removed_schema_is_a_break() -> None:
    gen = _load()
    current = _base_spec()
    del current["components"]["schemas"]["AnalyzeResponse"]
    breaks = gen.find_breaks(_base_spec(), current)
    assert any("schema removed" in b and "AnalyzeResponse" in b for b in breaks)


def test_removed_property_is_a_break() -> None:
    gen = _load()
    current = _base_spec()
    del current["components"]["schemas"]["AnalyzeRequest"]["properties"]["language"]
    breaks = gen.find_breaks(_base_spec(), current)
    assert any("property removed" in b and "AnalyzeRequest.language" in b for b in breaks)


def test_new_required_request_field_is_a_break() -> None:
    """Adding a required field to a request schema breaks existing clients."""
    gen = _load()
    current = _base_spec()
    current["components"]["schemas"]["AnalyzeRequest"]["properties"]["api_version"] = {
        "type": "string"
    }
    current["components"]["schemas"]["AnalyzeRequest"]["required"].append("api_version")
    breaks = gen.find_breaks(_base_spec(), current)
    assert any("new required request field" in b and "api_version" in b for b in breaks)


def test_making_optional_field_required_is_a_break() -> None:
    """Pre-existing optional field flipped to required — clients that
    omitted it will now 400."""
    gen = _load()
    current = _base_spec()
    current["components"]["schemas"]["AnalyzeRequest"]["required"].append("language")
    breaks = gen.find_breaks(_base_spec(), current)
    assert any("new required request field" in b and "language" in b for b in breaks)


def test_new_required_field_on_response_schema_is_not_a_break() -> None:
    """We only flag newly-required fields on request schemas — adding a
    guaranteed field to a response is server-side additive."""
    gen = _load()
    current = _base_spec()
    current["components"]["schemas"]["AnalyzeResponse"]["properties"]["detections"] = {
        "type": "array"
    }
    current["components"]["schemas"]["AnalyzeResponse"]["required"] = ["detections"]
    assert gen.find_breaks(_base_spec(), current) == []


def test_main_exits_zero_on_compatible_spec(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: write both specs, invoke main, assert rc=0."""
    import json

    gen = _load()
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    base.write_text(json.dumps(_base_spec()))
    cur.write_text(json.dumps(_base_spec()))
    monkeypatch.setattr(
        sys, "argv", ["check_api_compat.py", "--base", str(base), "--current", str(cur)]
    )
    assert gen.main() == 0


def test_main_exits_one_on_break(tmp_path: Path, monkeypatch) -> None:
    import json

    gen = _load()
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    base.write_text(json.dumps(_base_spec()))
    broken = _base_spec()
    del broken["paths"]["/analyze"]
    cur.write_text(json.dumps(broken))
    monkeypatch.setattr(
        sys, "argv", ["check_api_compat.py", "--base", str(base), "--current", str(cur)]
    )
    assert gen.main() == 1
