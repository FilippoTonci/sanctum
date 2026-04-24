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
        patch("sanctum.api.server.create_server") as mock_create,
    ):
        mock_engine.return_value = object()
        result = runner.invoke(
            cli,
            ["serve", "--port", "9100", "--token-path", str(token_path)],
        )

    assert result.exit_code == 0, result.output
    assert token_path.exists()
    assert "http://127.0.0.1:9100" in result.output
    mock_create.assert_called_once()
    _, kwargs = mock_create.call_args
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9100
    # server.run() is the accept loop; confirm we reach it.
    mock_create.return_value.run.assert_called_once()


def test_serve_emits_ready_signal_on_stdout(runner: CliRunner, tmp_path: Path):
    """The Electron sidecar lifecycle parses this line; format is load-bearing.

    Must be: `SANCTUM_READY host=<host> port=<port> token_path=<path>` on
    one line, emitted before waitress starts serving."""
    token_path = tmp_path / "api-token"
    with (
        patch("sanctum.cli.commands._create_engine"),
        patch("sanctum.api.server.create_server"),
    ):
        result = runner.invoke(
            cli,
            ["serve", "--port", "9100", "--token-path", str(token_path)],
        )

    assert result.exit_code == 0, result.output
    ready_lines = [line for line in result.output.splitlines() if line.startswith("SANCTUM_READY")]
    assert len(ready_lines) == 1, f"expected exactly one SANCTUM_READY line; got: {ready_lines}"
    line = ready_lines[0]
    assert "host=127.0.0.1" in line
    assert "port=9100" in line
    assert f"token_path={token_path}" in line


def test_serve_with_port_zero_resolves_to_real_port(runner: CliRunner, tmp_path: Path):
    """--port 0 must be resolved to a concrete port before the app is built
    (otherwise the Host/Origin allowlist would reject all real traffic)."""
    token_path = tmp_path / "api-token"
    with (
        patch("sanctum.cli.commands._create_engine"),
        patch("sanctum.api.server.create_server") as mock_create,
    ):
        result = runner.invoke(
            cli,
            ["serve", "--port", "0", "--token-path", str(token_path)],
        )

    assert result.exit_code == 0, result.output
    _, kwargs = mock_create.call_args
    assert kwargs["port"] != 0, "CLI must resolve port=0 to a real port before binding"
    assert kwargs["port"] > 1024, "OS-allocated ports are unprivileged"
    # And the ready signal reports the resolved port, not "0".
    ready_line = next(
        line for line in result.output.splitlines() if line.startswith("SANCTUM_READY")
    )
    assert f"port={kwargs['port']}" in ready_line


def test_serve_reads_token_from_stdin(runner: CliRunner):
    """`--token-stdin` must consume one line from stdin as the bearer
    token and never create/touch `~/.sanctum/api-token`."""
    with (
        patch("sanctum.cli.commands._create_engine"),
        patch("sanctum.api.server.create_server"),
        patch("sanctum.api.app.create_app") as mock_create_app,
    ):
        result = runner.invoke(
            cli,
            ["serve", "--port", "9200", "--token-stdin"],
            input="supplied-via-stdin\n",
        )

    assert result.exit_code == 0, result.output
    # The token the app was built with is what stdin fed in.
    _, kwargs = mock_create_app.call_args
    assert kwargs["token"] == "supplied-via-stdin"
    # Ready line uses the stdin marker, not a path.
    ready = next(line for line in result.output.splitlines() if line.startswith("SANCTUM_READY"))
    assert "token_source=stdin" in ready
    assert "token_path=" not in ready


def test_serve_token_stdin_rejects_empty_input(runner: CliRunner):
    """An empty stdin under `--token-stdin` is a hard error, not a
    silent token of `""`."""
    with (
        patch("sanctum.cli.commands._create_engine"),
        patch("sanctum.api.server.create_server"),
    ):
        result = runner.invoke(
            cli,
            ["serve", "--token-stdin"],
            input="",
        )

    assert result.exit_code != 0
    assert "empty" in result.output.lower() or "empty" in str(result.exception).lower()


def test_serve_token_stdin_and_token_path_are_mutually_exclusive(runner: CliRunner, tmp_path: Path):
    """Specifying both is ambiguous; refuse early."""
    with (
        patch("sanctum.cli.commands._create_engine"),
        patch("sanctum.api.server.create_server"),
    ):
        result = runner.invoke(
            cli,
            [
                "serve",
                "--token-stdin",
                "--token-path",
                str(tmp_path / "tok"),
            ],
            input="whatever\n",
        )

    assert result.exit_code != 0
    combined = (result.output + (str(result.exception) if result.exception else "")).lower()
    assert "mutually exclusive" in combined


def test_serve_reuses_existing_token(runner: CliRunner, tmp_path: Path):
    """Re-running `sanctum serve` should not rotate the token."""
    from sanctum.api.auth import write_token

    token_path = tmp_path / "api-token"
    # Use write_token so the on-disk perms start at 0600 — ensure_token's
    # loose-perms check would otherwise reject a plain write_text seed.
    write_token("preexisting-token", token_path)

    with (
        patch("sanctum.cli.commands._create_engine"),
        patch("sanctum.api.server.create_server"),
    ):
        result = runner.invoke(
            cli,
            ["serve", "--token-path", str(token_path)],
        )

    assert result.exit_code == 0, result.output
    assert token_path.read_text().strip() == "preexisting-token"
