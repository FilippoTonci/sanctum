"""Unit tests for ``sanctum commit-review`` CLI command.

WS1 only wires the subcommand — the engine's ``commit_review`` raises
NotImplementedError. These tests lock in the arg parsing, the mapping-
store lifecycle, and the "not yet supported" exit path so they stay
stable when WS6 fills in real semantics.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from sanctum.cli.commands import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_commit_review_requires_store(runner: CliRunner, tmp_path: Path) -> None:
    src = tmp_path / "review.docx"
    src.write_bytes(b"x")
    out = tmp_path / "final.docx"
    result = runner.invoke(cli, ["commit-review", str(src), str(out)])
    assert result.exit_code != 0
    assert "--store" in result.output or "store" in result.output.lower()


def test_commit_review_forwards_to_engine(runner: CliRunner, tmp_path: Path) -> None:
    """Even though WS1's engine raises NotImplementedError, the CLI
    must call it with the right args — that's the contract for WS6
    to complete."""
    src = tmp_path / "review.docx"
    src.write_bytes(b"x")
    out = tmp_path / "final.docx"
    store = tmp_path / "map.sanctum"

    fake_reader, fake_writer = Mock(), Mock()
    mock_store = Mock()
    with (
        patch("sanctum.cli.commands.adapter_for", return_value=(fake_reader, fake_writer)),
        patch("sanctum.cli.commands._create_engine") as mock_engine,
        patch(
            "sanctum.cli.commands._mapping_store",
            return_value=_DummyCtx(mock_store),
        ),
    ):
        # The engine stub for WS1 raises NotImplementedError; simulate that.
        mock_engine.return_value.commit_review.side_effect = NotImplementedError(
            "commit_review reconciliation lands in Phase 1.5 WS6"
        )
        result = runner.invoke(
            cli,
            [
                "commit-review",
                str(src),
                str(out),
                "--store",
                str(store),
                "--passphrase",
                "pw",
            ],
        )

    assert result.exit_code == 2, result.output
    assert "Not yet supported" in result.output
    # Confirm the engine was actually called with the right positional args
    # so WS6 only has to remove the NotImplementedError, not rewire.
    mock_engine.return_value.commit_review.assert_called_once()
    args, _ = mock_engine.return_value.commit_review.call_args
    assert args[0] is fake_reader
    assert args[1] is fake_writer
    assert args[2] == src
    assert args[3] == out
    assert args[4] is mock_store


class _DummyCtx:
    """Minimal context-manager wrapper so _mapping_store can be patched."""

    def __init__(self, value: object) -> None:
        self._value = value

    def __enter__(self) -> object:
        return self._value

    def __exit__(self, *exc: object) -> None:
        return None
