"""Allow ``python -m sanctum.cli`` (used by tests that must not depend on PATH)."""

from sanctum.cli.commands import cli

if __name__ == "__main__":
    cli()
