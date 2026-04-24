"""Unit tests for ``sanctum process-file`` CLI command.

These stub out the engine + adapter plumbing so the test exercises only
the command wiring: arg parsing, dispatch to ``adapter_for``, and
return-code behaviour on error.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from sanctum.cli.commands import cli
from sanctum.core.exceptions import UnsupportedDocumentFormatError
from sanctum.core.models import (
    AnonymizationResult,
    DetectionResult,
    StructuredDocument,
    TextSegment,
)


def _fake_result() -> AnonymizationResult:
    return AnonymizationResult(
        original_text="Alice",
        anonymized_text="<PERSON>",
        detections=[
            DetectionResult(entity_type="PERSON", start=0, end=5, score=0.9, text_span="Alice")
        ],
        operators_applied={"PERSON": "replace"},
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_process_file_happy_path(runner: CliRunner, tmp_path: Path) -> None:
    src = tmp_path / "in.docx"
    src.write_bytes(b"fake")  # click just checks existence
    out = tmp_path / "out.docx"

    fake_reader = Mock()
    fake_writer = Mock()
    fake_doc = StructuredDocument(
        source_path=src, format="docx", segments=[TextSegment(id="body/p0/r0", text="x")]
    )
    fake_reader.read.return_value = fake_doc

    with (
        patch("sanctum.cli.commands.adapter_for", return_value=(fake_reader, fake_writer)),
        patch("sanctum.cli.commands._create_engine") as mock_engine,
    ):
        mock_engine.return_value.process_document.return_value = [_fake_result()]
        result = runner.invoke(cli, ["process-file", str(src), str(out)])

    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Wrote anonymized document" in output
    assert "1 segments changed" in output
    assert "1 entities replaced" in output


def test_process_file_unknown_extension_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    src = tmp_path / "in.odt"
    src.write_bytes(b"fake")
    out = tmp_path / "out.odt"

    def _raise(_path: Path) -> None:
        raise UnsupportedDocumentFormatError("No adapter for .odt")

    with patch("sanctum.cli.commands.adapter_for", side_effect=_raise):
        result = runner.invoke(cli, ["process-file", str(src), str(out)])

    assert result.exit_code == 1
    assert "Error" in result.output


def test_process_file_rejects_missing_input(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        cli,
        ["process-file", str(tmp_path / "nope.docx"), str(tmp_path / "out.docx")],
    )
    assert result.exit_code != 0
    assert "does not exist" in result.output.lower()


def test_process_file_forwards_entities_flag(runner: CliRunner, tmp_path: Path) -> None:
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    out = tmp_path / "out.docx"

    fake_reader, fake_writer = Mock(), Mock()
    with (
        patch("sanctum.cli.commands.adapter_for", return_value=(fake_reader, fake_writer)),
        patch("sanctum.cli.commands._create_engine") as mock_engine,
    ):
        mock_engine.return_value.process_document.return_value = []
        result = runner.invoke(
            cli,
            [
                "process-file",
                str(src),
                str(out),
                "--entities",
                "PERSON, EMAIL_ADDRESS",
                "--threshold",
                "0.7",
            ],
        )

    assert result.exit_code == 0, result.output
    call = mock_engine.return_value.process_document.call_args
    assert call.kwargs["entities"] == ["PERSON", "EMAIL_ADDRESS"]
    assert call.kwargs["score_threshold"] == 0.7


def test_process_file_default_goes_through_process_document(
    runner: CliRunner, tmp_path: Path
) -> None:
    """No --review → fire-and-forget path: process_document is called,
    create_review_session is not."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    out = tmp_path / "out.docx"

    fake_reader, fake_writer = Mock(), Mock()
    with (
        patch("sanctum.cli.commands.adapter_for", return_value=(fake_reader, fake_writer)),
        patch("sanctum.cli.commands._create_engine") as mock_engine,
    ):
        mock_engine.return_value.process_document.return_value = []
        runner.invoke(cli, ["process-file", str(src), str(out)])

    assert mock_engine.return_value.process_document.called
    assert not mock_engine.return_value.create_review_session.called


def test_process_file_review_creates_session_and_prints_id(
    runner: CliRunner, tmp_path: Path
) -> None:
    """--review → engine.create_review_session is called; no output_path is
    passed; the CLI prints the session id and a URL hint."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")

    fake_reader = Mock()
    fake_session = Mock()
    fake_session.id = "session-abc-123"

    with (
        patch("sanctum.cli.commands.adapter_for", return_value=(fake_reader, Mock())),
        patch("sanctum.cli.commands._create_engine") as mock_engine,
    ):
        mock_engine.return_value.create_review_session.return_value = fake_session
        result = runner.invoke(cli, ["process-file", str(src), "--review"])

    assert result.exit_code == 0, result.output
    assert "session-abc-123" in result.output
    # URL template points back at the loopback API — concrete port is the
    # user's `sanctum serve` choice, so we keep it as <port> in copy.
    assert "/review/session-abc-123" in result.output
    # Engine got the CLI's operator fallback (hips today).
    call = mock_engine.return_value.create_review_session.call_args
    assert call.kwargs["default_operator"] == "hips"
    assert not mock_engine.return_value.process_document.called


def test_process_file_review_respects_explicit_operator(runner: CliRunner, tmp_path: Path) -> None:
    """--operator overrides the configured default for the session."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")

    fake_session = Mock()
    fake_session.id = "s1"
    with (
        patch("sanctum.cli.commands.adapter_for", return_value=(Mock(), Mock())),
        patch("sanctum.cli.commands._create_engine") as mock_engine,
    ):
        mock_engine.return_value.create_review_session.return_value = fake_session
        result = runner.invoke(cli, ["process-file", str(src), "--review", "--operator", "replace"])

    assert result.exit_code == 0, result.output
    call = mock_engine.return_value.create_review_session.call_args
    assert call.kwargs["default_operator"] == "replace"


def test_process_file_review_rejects_output_path(runner: CliRunner, tmp_path: Path) -> None:
    """OUTPUT_PATH under --review is nonsensical (no file is produced until
    commit); the CLI should raise UsageError, not silently ignore it."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    out = tmp_path / "out.docx"

    result = runner.invoke(cli, ["process-file", str(src), str(out), "--review"])
    assert result.exit_code != 0
    assert "OUTPUT_PATH must be omitted" in result.output


def test_process_file_no_review_requires_output_path(runner: CliRunner, tmp_path: Path) -> None:
    """Fire-and-forget mode has nowhere to write without OUTPUT_PATH."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")

    result = runner.invoke(cli, ["process-file", str(src)])
    assert result.exit_code != 0
    assert "OUTPUT_PATH is required" in result.output
