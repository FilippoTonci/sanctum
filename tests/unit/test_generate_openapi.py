"""Sanity tests for `scripts/generate_openapi.py`.

These run in the normal unit suite (not integration) because the
generator only imports Pydantic schemas — no Flask app, no engine, no
network. Keeping it in `tests/unit/` means the CI lint lane catches
generator regressions before the full suite runs.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR = _REPO_ROOT / "scripts" / "generate_openapi.py"
_SPEC = _REPO_ROOT / "schema" / "openapi.json"


def _load_generator() -> Any:
    """Import the generator script as a module without executing `main()`."""
    spec = importlib.util.spec_from_file_location("_sanctum_generate_openapi", _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generator_output_is_deterministic() -> None:
    """Two back-to-back calls to `build()` produce structurally equal dicts.

    If this fails, the CI diff check will fail intermittently and the
    desktop's types will drift on every release. The generator must be
    a pure function of the Pydantic schemas + the ROUTES list."""
    gen = _load_generator()
    first = gen.build()
    second = gen.build()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_committed_spec_matches_source() -> None:
    """The committed `schema/openapi.json` is regenerated from the same
    build() used here, so they must match byte-for-byte modulo JSON
    whitespace. Local equivalent of the CI diff check — catches the
    forgetful "I edited a schema but didn't regen" mistake before push."""
    gen = _load_generator()
    fresh = gen.build()
    committed = json.loads(_SPEC.read_text())
    assert fresh == committed, (
        "schema/openapi.json is out of date — run "
        "'python scripts/generate_openapi.py' and commit the result"
    )


def test_spec_has_expected_top_level_shape() -> None:
    """OpenAPI 3.1 envelope sanity — catches broken JSON or a
    regressed generator that e.g. drops the `components` block."""
    spec = json.loads(_SPEC.read_text())
    assert spec["openapi"].startswith("3.")
    assert "title" in spec["info"]
    assert "version" in spec["info"]
    assert len(spec["paths"]) > 0
    assert "components" in spec
    assert "schemas" in spec["components"]
    assert "bearerAuth" in spec["components"]["securitySchemes"]


def test_every_route_model_is_in_components() -> None:
    """Every `$ref` in `paths` resolves under `components.schemas`.

    A dangling reference would break `openapi-typescript`'s code
    generation in the desktop repo with a confusing error."""
    spec = json.loads(_SPEC.read_text())
    schemas = spec["components"]["schemas"]

    def _collect_refs(obj: Any, out: set[str]) -> None:
        if isinstance(obj, dict):
            if "$ref" in obj:
                out.add(obj["$ref"])
            for v in obj.values():
                _collect_refs(v, out)
        elif isinstance(obj, list):
            for v in obj:
                _collect_refs(v, out)

    refs: set[str] = set()
    _collect_refs(spec["paths"], refs)
    for ref in refs:
        assert ref.startswith("#/components/schemas/"), ref
        name = ref.removeprefix("#/components/schemas/")
        assert name in schemas, f"unresolved $ref in paths: {ref!r}"
