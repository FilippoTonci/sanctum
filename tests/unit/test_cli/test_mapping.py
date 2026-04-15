"""Unit tests for `sanctum mapping` + pseudonymize flag wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from sanctum.cli.commands import cli
from sanctum.core.models import AnonymizationResult, DetectionResult, OperatorPolicy
from sanctum.security.mapping_store import EncryptedFileMappingStore


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _anon_result(text: str, anonymized: str) -> AnonymizationResult:
    return AnonymizationResult(
        original_text=text,
        anonymized_text=anonymized,
        detections=[
            DetectionResult(
                entity_type="PERSON", start=0, end=len("Alice"), score=0.9, text_span="Alice"
            )
        ],
        operators_applied={"PERSON": "pseudonymize"},
    )


def test_anonymize_pseudonymize_session_only_passes_policies(runner: CliRunner):
    with patch("sanctum.cli.commands._create_engine") as mock_engine:
        mock_engine.return_value.process.return_value = _anon_result("Alice", "Dana")
        result = runner.invoke(cli, ["anonymize", "Alice went home.", "--operator", "pseudonymize"])

    assert result.exit_code == 0, result.output
    call = mock_engine.return_value.process.call_args
    policies = call.kwargs["operator_policies"]
    assert "DEFAULT" in policies
    policy: OperatorPolicy = policies["DEFAULT"]
    assert policy.operator_name == "pseudonymize"
    assert "store" in policy.params


def test_anonymize_pseudonymize_with_store_requires_passphrase(runner: CliRunner, tmp_path: Path):
    store = tmp_path / "store.sanctum"
    with patch("sanctum.cli.commands._create_engine"):
        result = runner.invoke(
            cli,
            [
                "anonymize",
                "Alice",
                "--operator",
                "pseudonymize",
                "--store",
                str(store),
            ],
        )
    assert result.exit_code != 0
    assert "passphrase" in result.output.lower()


def test_mapping_reverse_roundtrips(runner: CliRunner, tmp_path: Path):
    path = tmp_path / "store.sanctum"
    s = EncryptedFileMappingStore(path, kdf_time_cost=1, kdf_memory_cost=8, kdf_parallelism=1)
    s.unlock("pw")
    surrogate = s.get_or_create("Alice Smith", "PERSON", lambda: "Dana Doe")
    s.lock()

    result = runner.invoke(
        cli,
        [
            "mapping",
            "reverse",
            surrogate,
            "PERSON",
            "--store",
            str(path),
            "--passphrase",
            "pw",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Alice Smith" in result.output


def test_mapping_reverse_unknown_pseudonym_exits_two(runner: CliRunner, tmp_path: Path):
    path = tmp_path / "store.sanctum"
    s = EncryptedFileMappingStore(path, kdf_time_cost=1, kdf_memory_cost=8, kdf_parallelism=1)
    s.unlock("pw")
    s.lock()  # empty store, persisted

    result = runner.invoke(
        cli,
        [
            "mapping",
            "reverse",
            "does-not-exist",
            "PERSON",
            "--store",
            str(path),
            "--passphrase",
            "pw",
        ],
    )
    assert result.exit_code == 2
