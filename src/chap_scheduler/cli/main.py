"""chap-scheduler CLI entry point."""

from __future__ import annotations

import typer
import uvicorn

from chap_scheduler import __version__
from chap_scheduler.config import get_settings

_TOP_HELP = """\
chap-scheduler — FastAPI service embedding Prefect.

Common commands:
  chap-scheduler serve              Run the FastAPI server.
  chap-scheduler info               Print resolved configuration.
  chap-scheduler --version          Show version and exit.
"""

app = typer.Typer(
    name="chap-scheduler",
    help=_TOP_HELP,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"chap-scheduler {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    _version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """chap-scheduler CLI."""


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind host (overrides settings)."),
    port: int | None = typer.Option(None, help="Bind port (overrides settings)."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (development)."),
    log_level: str | None = typer.Option(None, help="Uvicorn log level."),
) -> None:
    """Run the FastAPI server."""
    settings = get_settings()
    uvicorn.run(
        "chap_scheduler.api.app:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=reload or settings.reload,
        log_level=log_level or settings.log_level,
    )


@app.command()
def info() -> None:
    """Print resolved configuration."""
    settings = get_settings()
    typer.echo(f"chap-scheduler {__version__}")
    typer.echo(f"  host:                {settings.host}")
    typer.echo(f"  port:                {settings.port}")
    typer.echo(f"  log_level:           {settings.log_level}")
    typer.echo(f"  reload:              {settings.reload}")
    typer.echo(f"  embed_prefect:       {settings.embed_prefect}")
    typer.echo(f"  prefect_mount_path:  {settings.prefect_mount_path}")


def main() -> None:
    """Module-level entry point."""
    app()


if __name__ == "__main__":
    main()
