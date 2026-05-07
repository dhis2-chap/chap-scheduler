"""chap-scheduler CLI entry point."""

from __future__ import annotations

import os

import typer
import uvicorn

from chap_scheduler import __version__
from chap_scheduler.config import get_settings

_TOP_HELP = """\
chap-scheduler — FastAPI service embedding Prefect.

Common commands:
  chap-scheduler serve              Run the FastAPI server.
  chap-scheduler register-blocks    Register block types with a running API.
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


@app.command(name="register-blocks")
def register_blocks(
    api_url: str | None = typer.Option(
        None,
        "--api-url",
        help=(
            "PREFECT_API_URL to register against. Defaults to the local "
            "embedded server using the configured host/port/mount path."
        ),
    ),
) -> None:
    """Register chap-scheduler block types with a *running* chap-scheduler API.

    Use this when you run `chap-scheduler serve` standalone (no worker
    container). The worker entrypoint registers the same block types
    automatically; this command is the equivalent for setups without a worker.

    Goes over real HTTP, so the chap-scheduler server must be up and listening
    before you run this -- otherwise Prefect's client falls back to ephemeral
    mode and spawns a second in-process Prefect server (which is exactly what
    we removed from the API lifespan).
    """
    settings = get_settings()
    # 127.0.0.1 explicitly: settings.host is the *bind* address (commonly
    # 0.0.0.0 in container deployments), which is unreliable as a target URL.
    # Pass --api-url for anything other than the locally-listening server.
    target = api_url or f"http://127.0.0.1:{settings.port}{settings.prefect_mount_path}/api"
    os.environ["PREFECT_API_URL"] = target
    # Lazy imports so `prefect`'s setup_logging() doesn't fire on every CLI invocation.
    from chap_scheduler.blocks.dhis2 import Dhis2Credentials

    typer.echo(f"Registering block types against {target}")
    Dhis2Credentials.register_type_and_schema()
    typer.echo("  Dhis2Credentials -> registered")


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
