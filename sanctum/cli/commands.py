from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table
from sanctum.config.settings import settings
from sanctum.core.engine import SanctumEngine
from sanctum.core.exceptions import SanctumError
from sanctum.core.models import OperatorPolicy
from sanctum.documents import adapter_for
from sanctum.security import (
    EncryptedFileMappingStore,
    InMemoryMappingStore,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sanctum.core.protocols import MappingStore

console = Console()


@contextmanager
def _mapping_store(store_path: Path | None, passphrase: str | None) -> Iterator[MappingStore]:
    """Yield a ready mapping store, persisting on exit when file-backed.

    `store_path=None` → `InMemoryMappingStore` (no lifecycle). Otherwise
    `EncryptedFileMappingStore` is unlocked with the given passphrase;
    the store re-encrypts and writes on context exit so any new mappings
    created during the call are persisted.
    """
    if store_path is None:
        yield InMemoryMappingStore()
        return
    if not passphrase:
        raise click.UsageError(
            "--passphrase is required when --store is supplied "
            "(or use the SANCTUM_PASSPHRASE env var)."
        )
    encrypted = EncryptedFileMappingStore(store_path)
    encrypted.unlock(passphrase)
    try:
        yield encrypted
    finally:
        encrypted.lock()


def _pseudonymize_policies(store: MappingStore, language: str) -> dict[str, OperatorPolicy]:
    return {
        "DEFAULT": OperatorPolicy(
            operator_name="pseudonymize",
            params={"store": store, "language": language},
        )
    }


def _create_engine() -> SanctumEngine:
    """Composition root: wire concrete adapters into the engine."""
    from sanctum.analyzer.adapter import PresidioAnalyzer
    from sanctum.analyzer.nlp_config import create_nlp_engine
    from sanctum.anonymizer.adapter import PresidioAnonymizer

    # Build the NLP engine explicitly so (a) we keep ORGANIZATION on
    # (Presidio's default config drops it as too noisy) and (b) we never
    # fall through to the default loader, which can call
    # `spacy.cli.download()` and break the air-gap.
    nlp_engine = create_nlp_engine(model_name=settings.nlp.spacy_model)
    analyzer = PresidioAnalyzer(
        nlp_engine=nlp_engine,
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
@click.option(
    "--store",
    "store_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to a persistent encrypted mapping file (used with --operator pseudonymize).",
)
@click.option(
    "--passphrase",
    envvar="SANCTUM_PASSPHRASE",
    default=None,
    help="Passphrase for the mapping store. Prefer the SANCTUM_PASSPHRASE env var.",
)
def anonymize(
    text: str,
    operator: str,
    language: str,
    threshold: float,
    store_path: Path | None,
    passphrase: str | None,
) -> None:
    """Detect and anonymize PII in TEXT."""
    try:
        engine = _create_engine()
        if operator == "pseudonymize":
            with _mapping_store(store_path, passphrase) as store:
                result = engine.process(
                    text,
                    language=language,
                    score_threshold=threshold,
                    operator_policies=_pseudonymize_policies(store, language),
                )
        else:
            # Wire `--operator` through as a DEFAULT policy so the choice
            # actually reaches the engine. Without this, the CLI silently
            # fell back to the configured default operator and the flag
            # was a no-op for anything other than pseudonymize.
            policies = {"DEFAULT": OperatorPolicy(operator_name=operator)}
            result = engine.process(
                text,
                language=language,
                score_threshold=threshold,
                operator_policies=policies,
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
@click.option(
    "--operator",
    "-o",
    default=None,
    help="Anonymization operator; use 'pseudonymize' for consistent reversible surrogates.",
)
@click.option(
    "--store",
    "store_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to a persistent encrypted mapping file (used with --operator pseudonymize).",
)
@click.option(
    "--passphrase",
    envvar="SANCTUM_PASSPHRASE",
    default=None,
    help="Passphrase for the mapping store. Prefer the SANCTUM_PASSPHRASE env var.",
)
def process_file(
    input_path: Path,
    output_path: Path,
    language: str,
    threshold: float,
    entities: str | None,
    operator: str | None,
    store_path: Path | None,
    passphrase: str | None,
) -> None:
    """Anonymize a structured office document (.docx/.xlsx/.pdf/.pptx)."""
    try:
        reader, writer = adapter_for(input_path)
        engine = _create_engine()
        entity_list = [e.strip() for e in entities.split(",")] if entities else None

        if operator == "pseudonymize":
            with _mapping_store(store_path, passphrase) as store:
                results = engine.process_document(
                    reader,
                    writer,
                    input_path,
                    output_path,
                    language=language,
                    score_threshold=threshold,
                    entities=entity_list,
                    operator_policies=_pseudonymize_policies(store, language),
                )
        else:
            # Mirror the `anonymize` command: when `--operator` is set,
            # wrap it in a DEFAULT policy so the engine actually applies
            # it. `None` preserves the engine's configured default.
            policies = {"DEFAULT": OperatorPolicy(operator_name=operator)} if operator else None
            results = engine.process_document(
                reader,
                writer,
                input_path,
                output_path,
                language=language,
                score_threshold=threshold,
                entities=entity_list,
                operator_policies=policies,
            )

        total = sum(len(r.detections) for r in results)
        console.print(
            f"[green]Wrote anonymized document to {output_path}[/green] "
            f"({len(results)} segments changed, {total} entities replaced)."
        )
    except SanctumError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e


@cli.group()
def mapping() -> None:
    """Manage the encrypted mapping store used by the pseudonymize operator."""


@mapping.command("reverse")
@click.argument("pseudonym")
@click.argument("entity_type")
@click.option(
    "--store",
    "store_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to an existing encrypted mapping file.",
)
@click.option(
    "--passphrase",
    envvar="SANCTUM_PASSPHRASE",
    prompt=True,
    hide_input=True,
    help="Passphrase for the mapping store.",
)
def mapping_reverse(pseudonym: str, entity_type: str, store_path: Path, passphrase: str) -> None:
    """Look up the original that maps to PSEUDONYM for ENTITY_TYPE."""
    try:
        store = EncryptedFileMappingStore(store_path)
        store.unlock(passphrase)
        try:
            original = store.reverse(pseudonym, entity_type)
        finally:
            store.lock()
        if original is None:
            console.print(f"[yellow]No mapping found for {pseudonym} ({entity_type}).[/yellow]")
            raise SystemExit(2)
        console.print(original)
    except SanctumError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e


@mapping.command("rotate")
@click.option(
    "--store",
    "store_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--old-passphrase", prompt=True, hide_input=True)
@click.option("--new-passphrase", prompt=True, hide_input=True, confirmation_prompt=True)
def mapping_rotate(store_path: Path, old_passphrase: str, new_passphrase: str) -> None:
    """Re-encrypt the mapping file under a new passphrase (fresh salt)."""
    try:
        store = EncryptedFileMappingStore(store_path)
        store.rotate_passphrase(old_passphrase, new_passphrase)
        store.lock()
        console.print(f"[green]Rotated passphrase on {store_path}.[/green]")
    except SanctumError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e


@cli.command()
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Loopback address to bind. Non-loopback hosts are refused at startup.",
)
@click.option(
    "--port",
    default=8765,
    show_default=True,
    type=int,
    help="TCP port to listen on.",
)
@click.option(
    "--token-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Override the default ~/.sanctum/api-token location.",
)
@click.option(
    "--threads",
    default=4,
    show_default=True,
    type=int,
    help="Waitress worker-thread count.",
)
def serve(host: str, port: int, token_path: Path | None, threads: int) -> None:
    """Start the localhost API server."""
    import signal

    from sanctum.api.app import create_app
    from sanctum.api.auth import DEFAULT_TOKEN_PATH, ensure_token
    from sanctum.api.server import assert_loopback, run

    assert_loopback(host)

    path = token_path or DEFAULT_TOKEN_PATH
    token = ensure_token(path)
    engine = _create_engine()
    app = create_app(token=token, host=host, port=port, engine=engine)

    console.print(f"[green]Sanctum API listening on[/green] http://{host}:{port}")
    console.print(f"[dim]Bearer token stored at {path} (0600)[/dim]")
    console.print(
        "[dim]Clients: "
        f'curl -H "Authorization: Bearer $(cat {path})" http://{host}:{port}/health[/dim]'
    )

    # Route SIGTERM through the default interrupt handler so waitress's
    # blocking serve loop exits cleanly on `systemctl stop` / `docker stop`,
    # not just on Ctrl-C. Without this, the process gets killed mid-request
    # and any unlocked mapping store is left with its flock held.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        run(app, host=host, port=port, threads=threads)
    except KeyboardInterrupt:
        console.print("[dim]Sanctum API shutting down...[/dim]")
    finally:
        # Best-effort flush of the encrypted mapping store on the way out.
        # Any failure here is logged and swallowed — the process is exiting
        # and there is nothing more we can do about a crashed write.
        store = app.config.get("SANCTUM_MAPPING_STORE")
        if store is not None and getattr(store, "is_unlocked", False):
            try:
                store.lock()
                console.print("[dim]Mapping store locked on shutdown.[/dim]")
            except Exception as exc:
                console.print(f"[yellow]Failed to lock mapping store: {exc}[/yellow]")


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
