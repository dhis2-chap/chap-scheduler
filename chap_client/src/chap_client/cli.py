"""Typer CLI mirroring the `ChapClient` methods 1:1.

Read-only by default: most commands list / fetch resources and pretty-
print the JSON response. The mutating commands (`evaluations create`,
`configured-models create`, ...) are spelled out explicitly so a typo
on the read path can't accidentally write.

Auth + base-url come from CLI options or the matching `CHAP_CLIENT_*`
env vars:

    chap-client --base-url http://localhost:8000 system info

    CHAP_CLIENT_BASE_URL=http://localhost:8000 chap-client system info

For chap reached through DHIS2's proxy, set
`--route-prefix /api/routes/chap/run` plus DHIS2 basic auth
(`--user admin --password district`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Annotated, Any

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from chap_client import (
    ChapClient,
    ChapConfiguredModelCreate,
    ChapMakeEvaluationRequest,
    __version__,
)

# stderr=False so JSON written to stdout stays pipe-friendly. `is_terminal`
# is False when stdout is captured (CliRunner, ` | jq`, redirect-to-file),
# which keeps machine-readable output unchanged for those callers.
_console = Console()
_err_console = Console(stderr=True)

app = typer.Typer(name="chap-client", help="HTTP CLI for the chap REST API.", no_args_is_help=True)
datasets_app = typer.Typer(help="List / fetch chap datasets.", no_args_is_help=True)
models_app = typer.Typer(help="Model registry + configured models.", no_args_is_help=True)
cmwds_app = typer.Typer(help="Configured-models-with-data-source CRUD.", no_args_is_help=True)
evaluations_app = typer.Typer(help="Run + list + fetch evaluations (chap UI: 'Evaluations').", no_args_is_help=True)
jobs_app = typer.Typer(help="Inspect chap job state.", no_args_is_help=True)
predictions_app = typer.Typer(help="Pull prediction values.", no_args_is_help=True)
app.add_typer(datasets_app, name="datasets")
app.add_typer(models_app, name="models")
app.add_typer(cmwds_app, name="cmwds", help="Alias for configured-models-with-data-source.")
app.add_typer(evaluations_app, name="evaluations")
app.add_typer(jobs_app, name="jobs")
app.add_typer(predictions_app, name="predictions")


# Default constructor params; overridable on every invocation.
class _ClientOptions(BaseModel):
    base_url: str
    user: str | None = None
    password: str | None = None
    route_prefix: str = ""
    max_attempts: int = 3


def _build_client(opts: _ClientOptions) -> ChapClient:
    if not opts.base_url:
        if _err_console.is_terminal:
            _err_console.print("[bold red]error:[/] --base-url (or CHAP_CLIENT_BASE_URL) is required")
        else:
            typer.echo("error: --base-url (or CHAP_CLIENT_BASE_URL) is required", err=True)
        raise typer.Exit(code=2)
    auth: tuple[str, str] | None = None
    if opts.user is not None and opts.password is not None:
        auth = (opts.user, opts.password)
    return ChapClient(
        base_url=opts.base_url,
        auth=auth,
        route_prefix=opts.route_prefix,
        max_attempts=opts.max_attempts,
    )


def _to_json_text(data: Any) -> str:
    """Dump `data` as a JSON string with chap's wire shape (camelCase keys)."""
    if isinstance(data, BaseModel):
        return data.model_dump_json(by_alias=True, indent=2)
    if isinstance(data, list) and data and isinstance(data[0], BaseModel):
        return json.dumps([m.model_dump(by_alias=True, mode="json") for m in data], indent=2)
    return json.dumps(data, indent=2, default=str)


def _print_json(data: Any) -> None:
    """Print JSON to stdout.

    Syntax-highlights when stdout is a terminal; emits raw JSON when piped
    or redirected so `| jq` and similar consumers see byte-for-byte the
    same shape they did before rich was wired up.
    """
    text = _to_json_text(data)
    if _console.is_terminal:
        _console.print_json(text)
    else:
        typer.echo(text)


# Map chap job statuses onto rich color names so the output reads at a glance.
_JOB_STATUS_COLORS: dict[str, str] = {
    "SUCCESS": "green",
    "SUCCEEDED": "green",
    "FAILED": "red",
    "FAILURE": "red",
    "ERROR": "red",
    "CANCELLED": "yellow",
    "CANCELED": "yellow",
    "PENDING": "yellow",
    "RUNNING": "yellow",
    "STARTED": "yellow",
    "QUEUED": "yellow",
    "PROCESSING": "yellow",
}


def _print_status(status: str) -> None:
    """Print a chap job status, color-coded when stdout is a terminal."""
    if _console.is_terminal:
        color = _JOB_STATUS_COLORS.get(status.upper(), "white")
        _console.print(f"[bold {color}]{status}[/]")
    else:
        typer.echo(status)


# --- list-as-table rendering ------------------------------------------------


# A column spec: (header, value_fn, optional kwargs for Table.add_column).
_Column = tuple[str, Callable[[Any], str]] | tuple[str, Callable[[Any], str], dict[str, Any]]


def _print_list(rows: Sequence[BaseModel], *, title: str, columns: list[_Column]) -> None:
    """Print a homogeneous list as a rich Table on a TTY, plain JSON when piped.

    Each column spec is ``(header, value_fn)`` or ``(header, value_fn, kwargs)``
    where ``kwargs`` is forwarded verbatim to ``Table.add_column`` so callers
    can set ``justify="right"`` for numeric columns, ``style="cyan"``, etc.
    """
    if not _console.is_terminal:
        _print_json(list(rows))
        return

    table = Table(title=title, header_style="bold", title_justify="left", show_lines=False)
    for column in columns:
        header, _, *rest = column
        kwargs: dict[str, Any] = rest[0] if rest else {}
        table.add_column(header, **kwargs)
    for row in rows:
        table.add_row(*[fmt(row) for _, fmt, *_extra in columns])
    if not rows:
        _console.print(f"[dim]{title}: (no rows)[/]")
        return
    _console.print(table)


def _short(text: str | None, n: int = 40) -> str:
    """Crop text to ``n`` chars + ellipsis."""
    if text is None:
        return ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _fmt_metric(value: float | None, fmt: str = ".3f") -> str:
    return "" if value is None else format(value, fmt)


# --- top-level options -----------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"chap-client {__version__}")
        raise typer.Exit()


_BaseUrl = Annotated[
    str,
    typer.Option(
        "--base-url",
        envvar="CHAP_CLIENT_BASE_URL",
        help="Origin of the chap-serving host (e.g. http://localhost:8000 or your DHIS2 base URL).",
    ),
]
_User = Annotated[
    str | None,
    typer.Option("--user", envvar="CHAP_CLIENT_USER", help="Basic-auth username; omit if chap is unauthenticated."),
]
_Password = Annotated[
    str | None,
    typer.Option("--password", envvar="CHAP_CLIENT_PASSWORD", help="Basic-auth password."),
]
_RoutePrefix = Annotated[
    str,
    typer.Option(
        "--route-prefix",
        envvar="CHAP_CLIENT_ROUTE_PREFIX",
        help="Path prefix; use '/api/routes/chap/run' when reaching chap via DHIS2.",
    ),
]
_MaxAttempts = Annotated[
    int,
    typer.Option(
        "--max-attempts",
        envvar="CHAP_CLIENT_MAX_ATTEMPTS",
        help="Total attempts for retryable (GET / HEAD) requests.",
    ),
]


@app.callback()
def root(
    ctx: typer.Context,
    base_url: _BaseUrl = "",
    user: _User = None,
    password: _Password = None,
    route_prefix: _RoutePrefix = "",
    max_attempts: _MaxAttempts = 3,
    version: Annotated[
        bool,
        typer.Option("--version", "-v", callback=_version_callback, is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    """chap-client CLI."""
    del version  # handled by the callback
    ctx.obj = _ClientOptions(
        base_url=base_url,
        user=user,
        password=password,
        route_prefix=route_prefix,
        max_attempts=max_attempts,
    )


# --- system ----------------------------------------------------------------


@app.command()
def info(ctx: typer.Context) -> None:
    """Print chap system info (chap-core version, server time, timezone)."""
    with _build_client(ctx.obj) as client:
        _print_json(client.system_info())


# --- datasets --------------------------------------------------------------


@datasets_app.command("list")
def datasets_list(ctx: typer.Context) -> None:
    """List all datasets."""
    with _build_client(ctx.obj) as client:
        rows = client.list_datasets()
    _print_list(
        rows,
        title="Datasets",
        columns=[
            ("ID", lambda d: str(d.id), {"justify": "right", "style": "cyan"}),
            ("Name", lambda d: _short(d.name, 40)),
            ("Type", lambda d: d.type),
            ("Period", lambda d: f"{d.first_period}–{d.last_period}"),
            ("Org units", lambda d: str(len(d.org_units)), {"justify": "right"}),
            ("Covariates", lambda d: ", ".join(d.covariates)),
        ],
    )


@datasets_app.command("get")
def datasets_get(ctx: typer.Context, id: int) -> None:
    """Fetch a single dataset by id."""
    with _build_client(ctx.obj) as client:
        _print_json(client.get_dataset(id))


# --- models ----------------------------------------------------------------


def _model_spec_columns() -> list[_Column]:
    return [
        ("ID", lambda m: str(m.id), {"justify": "right", "style": "cyan"}),
        ("Name", lambda m: _short(m.name, 50)),
        ("Target", lambda m: m.target.name),
        ("Covariates", lambda m: ", ".join(c.name for c in m.covariates)),
        ("Period type", lambda m: m.supported_period_type or ""),
    ]


@models_app.command("list")
def models_list(ctx: typer.Context) -> None:
    """List the model registry (`/v1/crud/models`)."""
    with _build_client(ctx.obj) as client:
        rows = client.list_models()
    _print_list(rows, title="Models", columns=_model_spec_columns())


@models_app.command("list-configured")
def models_list_configured(ctx: typer.Context) -> None:
    """List configured models (`/v1/crud/configured-models`)."""
    with _build_client(ctx.obj) as client:
        rows = client.list_configured_models()
    _print_list(rows, title="Configured models", columns=_model_spec_columns())


@models_app.command("create-configured")
def models_create_configured(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Configured-model name (chap may rewrite it).")],
    template_id: Annotated[int, typer.Option("--template-id", help="Model-template id from /v1/crud/model-templates.")],
) -> None:
    """Create a configured model."""
    spec = ChapConfiguredModelCreate(name=name, modelTemplateId=template_id)
    with _build_client(ctx.obj) as client:
        _print_json(client.create_configured_model(spec))


# --- configured-models-with-data-source ------------------------------------


@cmwds_app.command("list")
def cmwds_list(ctx: typer.Context) -> None:
    """List configured-models-with-data-source rows."""
    with _build_client(ctx.obj) as client:
        rows = client.list_configured_models_with_data_source()
    _print_list(
        rows,
        title="Configured models with data source",
        columns=[
            ("ID", lambda m: str(m.id), {"justify": "right", "style": "cyan"}),
            ("Name", lambda m: _short(m.name, 40)),
            ("Configured model", lambda m: m.configured_model.name),
            ("Period type", lambda m: m.period_type),
            ("Start", lambda m: m.start_period),
            ("Org units", lambda m: str(len(m.org_units)), {"justify": "right"}),
        ],
    )


@cmwds_app.command("get")
def cmwds_get(ctx: typer.Context, id: int) -> None:
    """Fetch a single configured-model-with-data-source by id."""
    with _build_client(ctx.obj) as client:
        _print_json(client.get_configured_model_with_data_source(id))


@cmwds_app.command("from-evaluation")
def cmwds_from_evaluation(ctx: typer.Context, evaluation_id: int) -> None:
    """Create a configured-model-with-data-source from an existing evaluation."""
    with _build_client(ctx.obj) as client:
        _print_json(client.create_configured_model_with_data_source_from_backtest(evaluation_id))


# --- evaluations -----------------------------------------------------------


@evaluations_app.command("list")
def evaluations_list(ctx: typer.Context) -> None:
    """List all evaluations (each entry carries `aggregate_metrics` once finished)."""
    with _build_client(ctx.obj) as client:
        rows = client.list_evaluations()
    _print_list(
        rows,
        title="Evaluations",
        columns=[
            ("ID", lambda e: str(e.id), {"justify": "right", "style": "cyan"}),
            ("Name", lambda e: _short(e.name, 35)),
            ("Model", lambda e: e.model_id),
            ("Dataset", lambda e: str(e.dataset_id), {"justify": "right"}),
            ("CRPS", lambda e: _fmt_metric(e.aggregate_metrics.get("crps")), {"justify": "right"}),
            ("MAE", lambda e: _fmt_metric(e.aggregate_metrics.get("mae")), {"justify": "right"}),
            ("RMSE", lambda e: _fmt_metric(e.aggregate_metrics.get("rmse")), {"justify": "right"}),
            ("Splits", lambda e: str(len(e.split_periods)), {"justify": "right"}),
        ],
    )


@evaluations_app.command("get")
def evaluations_get(ctx: typer.Context, id: int) -> None:
    """Fetch a single evaluation by id."""
    with _build_client(ctx.obj) as client:
        _print_json(client.get_evaluation(id))


@evaluations_app.command("delete")
def evaluations_delete(ctx: typer.Context, id: int) -> None:
    """Delete an evaluation by id."""
    with _build_client(ctx.obj) as client:
        client.delete_evaluation(id)
    typer.echo(f"deleted evaluation {id}")


@evaluations_app.command("create")
def evaluations_create(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Run label.")],
    model_id: Annotated[
        str,
        typer.Option(
            "--model-id",
            help="Configured-model NAME (string), e.g. 'chapkit-ewars-model'. NOT the integer id.",
        ),
    ],
    dataset_id: Annotated[int, typer.Option("--dataset-id", help="Numeric dataset id from `datasets list`.")],
    n_periods: Annotated[int | None, typer.Option("--n-periods")] = None,
    n_splits: Annotated[int | None, typer.Option("--n-splits")] = None,
    stride: Annotated[int | None, typer.Option("--stride")] = None,
) -> None:
    """Submit an evaluation job. Returns the chap job id; poll with `jobs status`."""
    req = ChapMakeEvaluationRequest(
        name=name,
        modelId=model_id,
        datasetId=dataset_id,
        nPeriods=n_periods,
        nSplits=n_splits,
        stride=stride,
    )
    with _build_client(ctx.obj) as client:
        _print_json(client.create_evaluation(req))


@evaluations_app.command("entries")
def evaluations_entries(
    ctx: typer.Context,
    id: int,
    quantile: Annotated[
        list[float],
        typer.Option("--quantile", "-q", help="Quantile to fetch; pass multiple flags for several values."),
    ],
    split_period: Annotated[str | None, typer.Option("--split-period")] = None,
    org_unit: Annotated[
        list[str] | None,
        typer.Option("--org-unit", help="Limit to these org units; pass multiple times."),
    ] = None,
) -> None:
    """Pull per-row evaluation values for a finished evaluation."""
    with _build_client(ctx.obj) as client:
        entries = client.evaluation_entries(
            id, quantiles=quantile, split_period=split_period, org_units=list(org_unit) if org_unit else None
        )
    _print_json(entries)


# --- jobs ------------------------------------------------------------------


@jobs_app.command("status")
def jobs_status(ctx: typer.Context, id: str) -> None:
    """Print the bare status string for a single job."""
    with _build_client(ctx.obj) as client:
        status = client.job_status(id)
    _print_status(status)


@jobs_app.command("description")
def jobs_description(ctx: typer.Context, id: str) -> None:
    """Print the full description (incl. `result`) for a single job, or 'null'."""
    with _build_client(ctx.obj) as client:
        desc = client.job_description(id)
    _print_json(desc) if desc else typer.echo("null")


# --- predictions -----------------------------------------------------------


@predictions_app.command("entries")
def predictions_entries(
    ctx: typer.Context,
    prediction_id: int,
    quantile: Annotated[list[float], typer.Option("--quantile", "-q")],
) -> None:
    """Pull per-row predicted values for a stored prediction."""
    with _build_client(ctx.obj) as client:
        entries = client.prediction_entries(prediction_id, quantiles=quantile)
    _print_json(entries)


if __name__ == "__main__":
    app()
