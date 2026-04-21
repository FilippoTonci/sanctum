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


def test_process_file_forwards_review_flag(runner: CliRunner, tmp_path: Path) -> None:
    """--review must propagate to engine.process_document(review=True)."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    out = tmp_path / "out.docx"

    fake_reader, fake_writer = Mock(), Mock()
    with (
        patch("sanctum.cli.commands.adapter_for", return_value=(fake_reader, fake_writer)),
        patch("sanctum.cli.commands._create_engine") as mock_engine,
    ):
        mock_engine.return_value.process_document.return_value = []
        result = runner.invoke(cli, ["process-file", str(src), str(out), "--review"])

    assert result.exit_code == 0, result.output
    call = mock_engine.return_value.process_document.call_args
    assert call.kwargs["review"] is True


def test_process_file_default_is_no_review(runner: CliRunner, tmp_path: Path) -> None:
    """WS1 keeps default review=False to avoid breaking adapters that
    don't yet implement emit_review; WS6 flips this."""
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

    call = mock_engine.return_value.process_document.call_args
    assert call.kwargs["review"] is False


def test_process_file_review_not_yet_supported_exits_2(runner: CliRunner, tmp_path: Path) -> None:
    """When engine raises NotImplementedError (WS1 adapters don't support
    review), CLI should exit 2 with a user-facing 'not yet supported'
    message rather than a traceback."""
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    out = tmp_path / "out.docx"

    fake_reader, fake_writer = Mock(), Mock()
    with (
        patch("sanctum.cli.commands.adapter_for", return_value=(fake_reader, fake_writer)),
        patch("sanctum.cli.commands._create_engine") as mock_engine,
    ):
        mock_engine.return_value.process_document.side_effect = NotImplementedError(
            "adapter does not yet implement emit_review"
        )
        result = runner.invoke(cli, ["process-file", str(src), str(out), "--review"])

    assert result.exit_code == 2, result.output
    assert "Not yet supported" in result.output
