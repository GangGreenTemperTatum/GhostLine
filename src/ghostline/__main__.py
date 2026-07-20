"""Entrypoint for the ``ghostline`` console script (see pyproject.toml [project.scripts])."""

from __future__ import annotations

from ghostline.cli import app


def main() -> None:
    """Run the Typer CLI."""
    app()


if __name__ == "__main__":
    main()
