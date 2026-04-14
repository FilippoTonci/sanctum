from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from sanctum.config.settings import settings
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import SanctumError
from sanctum.documents import adapter_for

console = Console()


def _create_engine() -> SanctumEngine:
    """Composition root: wire concrete adapters into the engine."""
    from sanctum.analyzer.adapter import PresidioAnalyzer
    from sanctum.anonymizer.adapter import PresidioAnonymizer

    analyzer = PresidioAnalyzer(
        default_score_threshold=settings.analyzer.default_score_threshold,
        default_language=settings.analyzer.default_language,
    )
    anonymizer = PresidioAnonymizer(
        default_operator=settings.anonymizer.default_operator,
    )
    return SanctumEngine(analyzer=analyzer, anonymizer=anonymizer)


@click.group()
def cli() -> None:
    """Sanctum - Local-first PII anonymization for professionals."""


@cli.command()
@click.argument("text")
@click.option(
    "--language",
    "-l",
    default=settings.analyzer.default_language,
    show_default=True,
    help="Language of the input text.",
)
@click.option(
    "--threshold",
    "-t",
    default=settings.analyzer.default_score_threshold,
    show_default=True,
    type=float,
    help="Minimum confidence score for detections.",
)
@click.option(
    "--entities",
    "-e",
    default=None,
    help="Comma-separated list of entity types to detect.",
)
def analyze(text: str, language: str, threshold: float, entities: str | None) -> None:
    """Detect PII entities in TEXT."""
    try:
        engine = _create_engine()
        entity_list = [e.strip() for e in entities.split(",")] if entities else None
        detections = engine.analyze(
            text,
            language=language,
            score_threshold=threshold,
            entities=entity_list,
        )

        table = Table(title="PII Detections")
        table.add_column("Entity Type", style="cyan")
        table.add_column("Text", style="magenta")
        table.add_column("Score", justify="right", style="green")
        table.add_column("Start", justify="right")
        table.add_column("End", justify="right")
        table.add_column("Recognizer")

        for d in detections:
            table.add_row(
                d.entity_type,
                d.text_span,
                f"{d.score:.2f}",
                str(d.start),
                str(d.end),
                d.recognizer_name,
            )

        console.print(table)
        console.print(f"\nFound {len(detections)} entities in text")
    except SanctumError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e


@cli.command()
@click.argument("text")
@click.option(
    "--operator",
    "-o",
    default=settings.anonymizer.default_operator,
    show_default=True,
    help="Anonymization operator to apply.",
)
@click.option(
    "--language",
    "-l",
    default=settings.analyzer.default_language,
    show_default=True,
    help="Language of the input text.",
)
@click.option(
    "--threshold",
    "-t",
    default=settings.analyzer.default_score_threshold,
    show_default=True,
    type=float,
    help="Minimum confidence score for detections.",
)
def anonymize(text: str, operator: str, language: str, threshold: float) -> None:
    """Detect and anonymize PII in TEXT."""
    try:
        engine = _create_engine()
        result = engine.process(
            text,
            language=language,
            score_threshold=threshold,
        )

        console.print(f"\n[bold]Anonymized text:[/bold]\n{result.anonymized_text}\n")

        table = Table(title="Anonymization Details")
        table.add_column("Entity Type", style="cyan")
        table.add_column("Original Text", style="magenta")
        table.add_column("Operator", style="green")

        for d in result.detections:
            applied_op = result.operators_applied.get(d.entity_type, operator)
            table.add_row(d.entity_type, d.text_span, applied_op)

        console.print(table)
    except SanctumError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e


@cli.command("process-file")
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("output_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--language",
    "-l",
    default=settings.analyzer.default_language,
    show_default=True,
    help="Language of the document text.",
)
@click.option(
    "--threshold",
    "-t",
    default=settings.analyzer.default_score_threshold,
    show_default=True,
    type=float,
    help="Minimum confidence score for detections.",
)
@click.option(
    "--entities",
    "-e",
    default=None,
    help="Comma-separated list of entity types to detect.",
)
def process_file(
    input_path: Path,
    output_path: Path,
    language: str,
    threshold: float,
    entities: str | None,
) -> None:
    """Anonymize a structured office document (.docx/.xlsx/.pdf/.pptx)."""
    try:
        reader, writer = adapter_for(input_path)
        engine = _create_engine()
        entity_list = [e.strip() for e in entities.split(",")] if entities else None
        results = engine.process_document(
            reader,
            writer,
            input_path,
            output_path,
            language=language,
            score_threshold=threshold,
            entities=entity_list,
        )

        total = sum(len(r.detections) for r in results)
        console.print(
            f"[green]Wrote anonymized document to {output_path}[/green] "
            f"({len(results)} segments changed, {total} entities replaced)."
        )
    except SanctumError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e


@cli.command()
def config() -> None:
    """Show current Sanctum configuration."""
    table = Table(title="Sanctum Configuration")
    table.add_column("Section", style="cyan")
    table.add_column("Setting", style="magenta")
    table.add_column("Value", style="green")

    # NLP settings
    table.add_row("nlp", "spacy_model", settings.nlp.spacy_model)

    # Analyzer settings
    threshold = str(settings.analyzer.default_score_threshold)
    table.add_row("analyzer", "default_score_threshold", threshold)
    table.add_row("analyzer", "default_language", settings.analyzer.default_language)

    # Anonymizer settings
    table.add_row("anonymizer", "default_operator", settings.anonymizer.default_operator)

    # Security / mapping-store settings
    table.add_row("security", "session_only", str(settings.security.session_only))
    table.add_row("security", "store_path", str(settings.security.store_path))
    table.add_row("security", "kdf_memory_cost_kib", str(settings.security.kdf_memory_cost))

    console.print(table)
