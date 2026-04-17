"""Unit tests for `sanctum serve` — covers the CLI wiring without booting waitress."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from sanctum.cli.commands import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_serve_help_lists_expected_options(runner: CliRunner):
    result = runner.invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    for opt in ("--host", "--port", "--token-path", "--threads"):
        assert opt in result.output


def test_serve_refuses_non_loopback_host(runner: CliRunner, tmp_path: Path):
    """The serve command must fail loudly if asked to bind a public IP."""
    with patch("sanctum.cli.commands._create_engine"):
        result = runner.invoke(
            cli,
            ["serve", "--host", "0.0.0.0", "--token-path", str(tmp_path / "tok")],
        )
    assert result.exit_code != 0
    assert result.exception is not None
    assert "non-loopback" in str(result.exception)


def test_serve_writes_token_and_calls_run(runner: CliRunner, tmp_path: Path):
    """Happy path: generates token, builds app, hands off to the runner."""
    token_path = tmp_path / "api-token"
    with (
        patch("sanctum.cli.commands._create_engine") as mock_engine,
        patch("sanctum.api.server.waitress_serve") as mock_waitress,
    ):
        mock_engine.return_value = object()
        result = runner.invoke(
            cli,
            ["serve", "--port", "9100", "--token-path", str(token_path)],
        )

    assert result.exit_code == 0, result.output
    assert token_path.exists()
    assert "http://127.0.0.1:9100" in result.output
    mock_waitress.assert_called_once()
    _, kwargs = mock_waitress.call_args
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9100


def test_serve_reuses_existing_token(runner: CliRunner, tmp_path: Path):
    """Re-running `sanctum serve` should not rotate the token."""
    from sanctum.api.auth import write_token

    token_path = tmp_path / "api-token"
    # Use write_token so the on-disk perms start at 0600 — ensure_token's
    # loose-perms check would otherwise reject a plain write_text seed.
    write_token("preexisting-token", token_path)

    with (
        patch("sanctum.cli.commands._create_engine"),
        patch("sanctum.api.server.waitress_serve"),
    ):
        result = runner.invoke(
            cli,
            ["serve", "--token-path", str(token_path)],
        )

    assert result.exit_code == 0, result.output
    assert token_path.read_text().strip() == "preexisting-token"
