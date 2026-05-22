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

import functools
import json
from collections.abc import Callable, Sequence
from typing import Annotated, Any

import httpx
import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from chap_client import (
    ChapClient,
    ChapConfiguredModelCreate,
    ChapHttpError,
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
prediction_setups_app = typer.Typer(
    help="List + fetch prediction setups (read-only; create / run via chap REST API).",
    no_args_is_help=True,
)
evaluations_app = typer.Typer(help="Run + list + fetch evaluations (chap UI: 'Evaluations').", no_args_is_help=True)
jobs_app = typer.Typer(help="Inspect chap job state.", no_args_is_help=True)
predictions_app = typer.Typer(help="Pull prediction values.", no_args_is_help=True)
app.add_typer(datasets_app, name="datasets")
app.add_typer(models_app, name="models")
app.add_typer(
    prediction_setups_app,
    name="prediction-setups",
    help="List + fetch prediction setups (read-only).",
)
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
        _print_error_line("--base-url (or CHAP_CLIENT_BASE_URL) is required")
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


# --- friendly error handling -----------------------------------------------


# httpx exceptions that mean "could not reach chap" -- typically the server
# is down, the URL is wrong, or the network path is broken. Treated
# uniformly as connection errors so the user sees a single short line
# rather than a 30-frame httpx traceback.
_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def _print_error_line(message: str, *, hint: str | None = None) -> None:
    """Print a single-line error to stderr, styled if the terminal supports it."""
    if _err_console.is_terminal:
        _err_console.print(f"[bold red]error:[/] {message}")
        if hint:
            _err_console.print(f"  [dim]hint:[/] {hint}")
    else:
        typer.echo(f"error: {message}", err=True)
        if hint:
            typer.echo(f"  hint: {hint}", err=True)


def _friendly(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a Typer command body so transport / chap-side errors print cleanly.

    Catches httpx connection errors (server down, wrong URL, dropped
    socket) and `ChapHttpError` (chap returned a non-2xx) and converts
    them to a one-line stderr message + ``typer.Exit(code=1)`` instead
    of letting the httpx traceback bubble up to the user.

    Bypasses for `typer.Exit` / `KeyboardInterrupt` -- those are how
    we signal exit / interrupt and shouldn't be caught here.
    Re-raises anything else unchanged so unexpected bugs still
    surface with a real traceback.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, KeyboardInterrupt):
            raise
        except _CONNECTION_ERRORS as exc:
            _print_error_line(
                f"could not reach chap: {type(exc).__name__}: {exc}",
                hint="check that chap-core is running and --base-url is correct",
            )
            raise typer.Exit(code=1) from None
        except ChapHttpError as exc:
            _print_error_line(
                f"chap {exc.method} {exc.path} -> HTTP {exc.status}",
                hint=str(exc.detail)[:200] if exc.detail else None,
            )
            raise typer.Exit(code=1) from None

    return wrapper


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
        _console.print(_styled_status(status))
    else:
        typer.echo(status)


def _styled_status(status: str) -> str:
    """Wrap a status string in a bold-coloured rich markup tag."""
    color = _JOB_STATUS_COLORS.get(status.upper(), "white")
    return f"[bold {color}]{status}[/]"


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


def _render_record_value(v: Any) -> str:
    """Format a record-table cell. Scalars verbatim; collections summarized."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (str, int, float)):
        return str(v)
    if isinstance(v, list):
        if not v:
            return "[]"
        # Short list of scalars -> comma-separated; otherwise count.
        if len(v) <= 8 and all(isinstance(x, (str, int, float, bool)) for x in v):
            return ", ".join(str(x) for x in v)
        return f"<{len(v)} items>"
    if isinstance(v, dict):
        if not v:
            return "{}"
        # Short dict of scalars (e.g. aggregate_metrics) -> rendered inline.
        if all(isinstance(x, (str, int, float, bool, type(None))) for x in v.values()):
            return ", ".join(f"{k}={_render_record_value(val)}" for k, val in v.items())
        return f"<{len(v)} fields>"
    return str(v)


def _print_record(model: BaseModel, *, title: str) -> None:
    """Render a pydantic record as a 2-column key/value table on a TTY.

    Scalars render verbatim. Short lists / dicts of scalars render inline;
    deeper nested values render as ``<N items>`` / ``<N fields>`` / type
    summaries -- pipe through ``| cat`` to see the full JSON shape.
    """
    if not _console.is_terminal:
        _print_json(model)
        return
    table = Table(title=title, title_justify="left", show_header=False, pad_edge=False)
    table.add_column("Field", style="cyan", justify="right")
    table.add_column("Value")
    payload = model.model_dump(by_alias=True, mode="json")
    for k, v in payload.items():
        table.add_row(k, _render_record_value(v))
    _console.print(table)


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
@_friendly
def info(ctx: typer.Context) -> None:
    """Print chap system info (chap-core version, server time, timezone)."""
    with _build_client(ctx.obj) as client:
        _print_record(client.system_info(), title="chap-core")


# --- datasets --------------------------------------------------------------


@datasets_app.command("list")
@_friendly
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
@_friendly
def datasets_get(ctx: typer.Context, id: int) -> None:
    """Fetch a single dataset by id."""
    with _build_client(ctx.obj) as client:
        _print_record(client.get_dataset(id), title=f"Dataset {id}")


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
@_friendly
def models_list(ctx: typer.Context) -> None:
    """List the model registry (`/v1/crud/models`)."""
    with _build_client(ctx.obj) as client:
        rows = client.list_models()
    _print_list(rows, title="Models", columns=_model_spec_columns())


@models_app.command("list-configured")
@_friendly
def models_list_configured(ctx: typer.Context) -> None:
    """List configured models (`/v1/crud/configured-models`)."""
    with _build_client(ctx.obj) as client:
        rows = client.list_configured_models()
    _print_list(rows, title="Configured models", columns=_model_spec_columns())


@models_app.command("create-configured")
@_friendly
def models_create_configured(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Configured-model name (chap may rewrite it).")],
    template_id: Annotated[int, typer.Option("--template-id", help="Model-template id from /v1/crud/model-templates.")],
) -> None:
    """Create a configured model."""
    spec = ChapConfiguredModelCreate(name=name, modelTemplateId=template_id)
    with _build_client(ctx.obj) as client:
        _print_record(client.create_configured_model(spec), title="Configured model")


# --- prediction-setups ------------------------------------------------------


@prediction_setups_app.command("list")
@_friendly
def prediction_setups_list(ctx: typer.Context) -> None:
    """List prediction setups."""
    with _build_client(ctx.obj) as client:
        rows = client.list_prediction_setups()
    _print_list(
        rows,
        title="Prediction setups",
        columns=[
            ("ID", lambda m: str(m.id), {"justify": "right", "style": "cyan"}),
            ("Name", lambda m: _short(m.name, 40)),
            ("Configured model", lambda m: m.configured_model.name),
            ("Period type", lambda m: m.period_type),
            ("Start", lambda m: m.start_period),
            ("Org units", lambda m: str(len(m.org_units)), {"justify": "right"}),
            ("Backtest", lambda m: str(m.backtest_id), {"justify": "right"}),
        ],
    )


@prediction_setups_app.command("get")
@_friendly
def prediction_setups_get(ctx: typer.Context, id: int) -> None:
    """Fetch a single prediction setup by id."""
    with _build_client(ctx.obj) as client:
        _print_record(
            client.get_prediction_setup(id),
            title=f"Prediction setup {id}",
        )


# --- evaluations -----------------------------------------------------------


@evaluations_app.command("list")
@_friendly
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
@_friendly
def evaluations_get(ctx: typer.Context, id: int) -> None:
    """Fetch a single evaluation by id."""
    with _build_client(ctx.obj) as client:
        _print_record(client.get_evaluation(id), title=f"Evaluation {id}")


@evaluations_app.command("delete")
@_friendly
def evaluations_delete(ctx: typer.Context, id: int) -> None:
    """Delete an evaluation by id."""
    with _build_client(ctx.obj) as client:
        client.delete_evaluation(id)
    typer.echo(f"deleted evaluation {id}")


@evaluations_app.command("create")
@_friendly
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
@_friendly
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


@jobs_app.command("list")
@_friendly
def jobs_list(ctx: typer.Context) -> None:
    """List every job chap currently has on record."""
    with _build_client(ctx.obj) as client:
        rows = client.list_jobs()
    _print_list(
        rows,
        title="Jobs",
        columns=[
            ("ID", lambda j: j.id, {"style": "cyan"}),
            ("Type", lambda j: j.type),
            ("Name", lambda j: _short(j.name, 40)),
            ("Status", lambda j: _styled_status(j.status)),
            ("Result", lambda j: j.result or ""),
            ("Started", lambda j: j.start_time.isoformat(timespec="seconds") if j.start_time else ""),
            ("Ended", lambda j: j.end_time.isoformat(timespec="seconds") if j.end_time else ""),
        ],
    )


@jobs_app.command("status")
@_friendly
def jobs_status(ctx: typer.Context, id: str) -> None:
    """Print the bare status string for a single job."""
    with _build_client(ctx.obj) as client:
        status = client.job_status(id)
    _print_status(status)


@jobs_app.command("description")
@_friendly
def jobs_description(ctx: typer.Context, id: str) -> None:
    """Print the full description (incl. `result`) for a single job, or 'null'."""
    with _build_client(ctx.obj) as client:
        desc = client.job_description(id)
    _print_json(desc) if desc else typer.echo("null")


# --- predictions -----------------------------------------------------------


@predictions_app.command("entries")
@_friendly
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
