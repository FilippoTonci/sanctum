"""Determinism checks for the generated office fixtures.

These catch regressions where a new python-docx/openpyxl/python-pptx/reportlab
release starts emitting something time-based we don't normalize yet. Running
the generator twice must produce identical bytes; the committed MANIFEST
must match what the generator currently produces.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import shutil

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "scripts" / "generate_office_fixtures.py"
FIXTURES = ROOT / "tests" / "fixtures" / "office"
MANIFEST = FIXTURES / "MANIFEST.sha256.json"


def _load_generator():
    spec = importlib.util.spec_from_file_location("_gen", GENERATOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_committed_fixtures_match_manifest():
    """Every fixture on disk must match the sha in the committed manifest."""
    manifest = json.loads(MANIFEST.read_text())
    for rel, expected in manifest.items():
        actual = _sha256(ROOT / rel)
        assert actual == expected, f"{rel}: sha drifted from manifest"


def test_generator_is_deterministic(tmp_path, monkeypatch):
    """Running the generator twice into a fresh directory yields identical bytes.

    We call the builder functions directly rather than ``main()`` so the
    manifest-writing step (which needs ``relative_to(ROOT)``) doesn't get
    in the way of a tmp-path OUT.
    """
    gen = _load_generator()
    out_dir = tmp_path / "office"
    monkeypatch.setattr(gen, "OUT", out_dir)

    def _run() -> dict[str, str]:
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)
        for build in gen.BUILDERS:
            build()
        return {
            p.name: _sha256(p)
            for p in sorted(out_dir.iterdir())
            if p.suffix != ".json"
        }

    first = _run()
    second = _run()
    assert first == second, f"non-deterministic generation: {first} vs {second}"


@pytest.mark.parametrize(
    "rel",
    [
        "tests/fixtures/office/nda_contract.docx",
        "tests/fixtures/office/invoice.xlsx",
        "tests/fixtures/office/engagement_letter.pdf",
        "tests/fixtures/office/internal_memo.pptx",
    ],
)
def test_fixture_has_provenance_json(rel):
    """Every office fixture has a sibling .json describing provenance + PII inventory."""
    json_path = (ROOT / rel).with_suffix(".json")
    assert json_path.is_file()
    data = json.loads(json_path.read_text())
    assert data["provenance"] == "synthetic"
    assert data["source_text"].startswith("tests/fixtures/")
    assert isinstance(data["pii_inventory"], list)
    assert data["pii_inventory"], "PII inventory should not be empty"


def test_fixtures_stay_small():
    """Fixture binaries are a committed artifact; keep them under 100 KB each."""
    manifest = json.loads(MANIFEST.read_text())
    limit = 100 * 1024
    for rel in manifest:
        size = (ROOT / rel).stat().st_size
        assert size < limit, f"{rel} is {size} bytes (>{limit})"
