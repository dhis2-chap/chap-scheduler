"""DHIS2 -> chap prediction flow.

Step-by-step (per configured model, isolated -- a failure in one model
does not abort the others):

1. Verify chap is reachable on the chosen DHIS2 instance.
2. Pull the configured-models-with-data-source list from chap.
3. For each model:
   a. Fetch the DHIS2 analytics rows it implies.
   b. Fetch the org-unit polygons (geojson).
   c. Build a ``ChapMakePredictionRequest`` and POST it to chap.
   d. Poll the chap job until it reaches a terminal status.
   e. Pull the prediction result and log a summary.
4. Emit a markdown ``RunReport`` artifact regardless of outcome -- even
   when chap-core itself was unreachable.

The flow accepts a ``Dhis2Credentials`` block as a required parameter, so
each run picks the DHIS2 instance to talk to. Register one or more block
instances ahead of time (UI: ``/prefect/blocks/catalog`` -> "DHIS2 Credentials
(chap-scheduler)" -> New) and pick one from the dropdown when triggering.

Run as a worker against the embedded Prefect server:

    python -m chap_scheduler.flows.dhis2_chap_prediction
"""

# NOTE: don't add `from __future__ import annotations` here -- Prefect builds
# a Pydantic model from this flow's signature for parameter validation, and
# stringified annotations turn the Dhis2Credentials block reference into an
# unresolvable forward ref ("class is not fully defined") at run time.

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from dhis2_client.resources.analytics import next_period_id
from geojson_pydantic import Feature, FeatureCollection
from prefect import flow, task
from prefect.artifacts import create_markdown_artifact

from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.chap import (
    ChapClient,
    ChapConfiguredModelWithDataSource,
    ChapJobResponse,
    ChapMakePredictionRequest,
    ChapObservation,
    ChapPredictionResult,
    ChapSystemInfo,
    Dhis2SystemInfo,
    ModelRunEntry,
    RunReport,
    render_report,
)

_PERIOD_ENUMERATION_CAP = 120
_TRANSIENT_JOB_STATUSES = frozenset({"PENDING", "RUNNING", "STARTED", "QUEUED", "PROCESSING"})
_DEFAULT_N_PERIODS_BY_PERIOD_TYPE: dict[str, int] = {"month": 3, "week": 12, "year": 1}


class _StepFailure(Exception):
    """Raised inside ``_run_one_model`` to label which step failed."""

    def __init__(self, step: str) -> None:
        super().__init__(step)
        self.step = step


# --- DHIS2 system info ------------------------------------------------------


@task(name="Fetch DHIS2 system info")
def fetch_dhis2_system_info(credentials: Dhis2Credentials) -> Dhis2SystemInfo:
    """Fetch ``GET /api/system/info`` so the report shows which DHIS2 we hit.

    Useful diagnostic if anything downstream fails -- you can tell at a
    glance which DHIS2 version was on the other end.
    """
    raw = credentials.get_client().get("/api/system/info")
    info = Dhis2SystemInfo.model_validate(raw)
    print(f"DHIS2 is up at {credentials.base_url} (version {info.version})")
    if info.system_name:
        print(f"  system name : {info.system_name}")
    if info.revision:
        print(f"  revision    : {info.revision}")
    return info


# --- chap interactions ------------------------------------------------------


@task(name="Verify chap is reachable")
def check_chap_core(credentials: Dhis2Credentials) -> ChapSystemInfo:
    """Verify the chap route is available on the DHIS2 instance.

    The DHIS2 admin sets up chap as a custom DHIS2 route, so chap is reached
    via the DHIS2 base URL with DHIS2 auth -- we never know or hold a separate
    chap URL. Hits ``GET <dhis2_base_url>/api/routes/chap/run/system/info``.
    """
    info = ChapClient(credentials).system_info()
    print(f"chap is up on {credentials.base_url} (chap-core v{info.chap_core_version})")
    print(f"  chap-core version : {info.chap_core_version}")
    print(f"  python version    : {info.python_version}")
    print(f"  server time zone  : {info.server_time_zone_id}")
    print(f"  server date       : {info.server_date.isoformat()}")
    return info


@task(name="Fetch chap configured models")
def fetch_configured_models(
    credentials: Dhis2Credentials,
) -> list[ChapConfiguredModelWithDataSource]:
    """Pull all configured models with their data-source mappings from chap."""
    models = ChapClient(credentials).configured_models()
    print(f"chap has {len(models)} configured model(s):")
    for m in models:
        tmpl = m.configured_model.model_template
        covariates = list(tmpl.required_covariates) + list(m.configured_model.additional_continuous_covariates)
        print(f"  - '{m.name}' (id={m.id}) using template '{tmpl.display_name}'")
        print(f"      target            : {tmpl.target}")
        print(f"      period type       : {m.period_type}")
        print(f"      start period      : {m.start_period}")
        print(f"      covariates        : {', '.join(covariates) or '(none)'}")
        print(f"      org units         : {len(m.org_units)}")
        print(f"      data sources (dx) : {len(m.data_sources)}")
    return models


# --- DHIS2 fetches ----------------------------------------------------------


def _last_completed_period(period_type: str, today: date | None = None) -> str:
    """Return the DHIS2 period ID for the last *completed* period before ``today``.

    The period covering today itself is in progress and therefore excluded --
    we want only periods whose data window has fully closed.
    """
    today = today or date.today()
    if period_type == "month":
        prev = today.replace(day=1) - timedelta(days=1)
        return f"{prev.year}{prev.month:02d}"
    if period_type == "year":
        return str(today.year - 1)
    if period_type == "week":
        iso = (today - timedelta(days=7)).isocalendar()
        return f"{iso.year}W{iso.week:02d}"
    raise ValueError(f"unsupported period_type: {period_type!r}")


def _enumerate_periods(start: str, period_type: str, today: date | None = None) -> list[str]:
    """Walk forward from ``start`` until we reach the last completed period."""
    end = _last_completed_period(period_type, today)
    periods = [start]
    while periods[-1] != end and len(periods) < _PERIOD_ENUMERATION_CAP:
        periods.append(next_period_id(periods[-1]))
    return periods


@task(
    name="Fetch DHIS2 analytics",
    task_run_name="Fetch DHIS2 analytics for {model.name} ({model.configured_model.name})",
)
def fetch_dhis2_for_model(
    credentials: Dhis2Credentials,
    model: ChapConfiguredModelWithDataSource,
) -> dict[str, Any]:
    """Build the DHIS2 analytics query for ``model`` and fetch the data."""
    client = credentials.get_client()

    dx_uids = [ds.data_element_id for ds in model.data_sources]
    periods = _enumerate_periods(model.start_period, model.period_type)
    dimension = [
        f"dx:{';'.join(dx_uids)}",
        f"pe:{';'.join(periods)}",
        f"ou:{';'.join(model.org_units)}",
    ]
    print(
        f"Fetching analytics for '{model.name}': "
        f"{len(dx_uids)} dx x {len(periods)} pe ({periods[0]}..{periods[-1]}) x {len(model.org_units)} ou"
    )
    data: dict[str, Any] = client.get_analytics_data(dimension=dimension)
    rows: list[list[Any]] = data.get("rows", [])
    headers: list[dict[str, Any]] = data.get("headers", [])
    print(f"  -> {len(rows)} rows; columns: {[h.get('name') for h in headers]}")
    return data


type _Feature = Feature[Any, dict[str, Any]]
type _FeatureCollection = FeatureCollection[_Feature]


@task(
    name="Fetch org-unit geojson",
    task_run_name="Fetch org-unit geojson for {model.name} ({model.configured_model.name})",
)
def fetch_org_units_geojson(
    credentials: Dhis2Credentials,
    model: ChapConfiguredModelWithDataSource,
) -> _FeatureCollection:
    """Fetch org-unit polygons + assemble the GeoJSON ``FeatureCollection``.

    Mirrors what the chap-frontend does: regular ``/api/organisationUnits``
    with explicit ``fields=`` rather than the ``.geojson`` extension, so we
    can populate the ``properties`` block (level/displayName/parent) the way
    chap expects.
    """
    if not model.org_units:
        return FeatureCollection[_Feature](type="FeatureCollection", features=[])
    client = credentials.get_client()
    payload = client.get(
        "/api/organisationUnits",
        params={
            "filter": f"id:in:[{','.join(model.org_units)}]",
            "fields": "id,geometry,parent[id],level,displayName,code",
            "paging": "false",
        },
    )
    org_units = payload.get("organisationUnits", [])
    print(f"Fetched {len(org_units)} org-unit features for '{model.name}'")
    features = [_build_feature(ou) for ou in org_units if ou.get("geometry")]
    return FeatureCollection[_Feature](type="FeatureCollection", features=features)


def _build_feature(org_unit: dict[str, Any]) -> _Feature:
    properties: dict[str, Any] = {
        "id": org_unit["id"],
        "level": org_unit.get("level"),
        "displayName": org_unit.get("displayName"),
    }
    if org_unit.get("code"):
        properties["code"] = org_unit["code"]
    parent = org_unit.get("parent") or {}
    parent_id = parent.get("id")
    if parent_id:
        properties["parent"] = parent_id
        properties["parentGraph"] = parent_id
    return Feature[Any, dict[str, Any]](
        type="Feature",
        id=org_unit["id"],
        geometry=org_unit["geometry"],
        properties=properties,
    )


# --- Build prediction request ----------------------------------------------


def _row_value_to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_prediction_request(
    model: ChapConfiguredModelWithDataSource,
    analytics: dict[str, Any],
    geojson: _FeatureCollection,
    n_periods: int,
    dataset_type: Literal["forecasting", "backtesting"],
    name: str,
) -> ChapMakePredictionRequest:
    """Pure transform: analytics rows + chap config -> ``ChapMakePredictionRequest``.

    Maps each analytics row's ``dataElementId`` (column 0) to a covariate
    name via ``model.data_sources``. Rows whose dx isn't in ``dataSources``
    or whose value isn't numeric are dropped, with a count printed.
    """
    covariate_by_de = {ds.data_element_id: ds.covariate for ds in model.data_sources}
    rows: list[list[Any]] = analytics.get("rows", [])
    observations: list[ChapObservation] = []
    dropped_unknown_dx = 0
    dropped_bad_value = 0
    for row in rows:
        if len(row) < 4:
            dropped_bad_value += 1
            continue
        dx, pe, ou, raw_value = row[0], row[1], row[2], row[3]
        covariate = covariate_by_de.get(dx)
        if covariate is None:
            dropped_unknown_dx += 1
            continue
        value = _row_value_to_float(raw_value)
        if value is None:
            dropped_bad_value += 1
            continue
        observations.append(ChapObservation(featureName=covariate, orgUnit=ou, period=pe, value=value))
    if dropped_unknown_dx or dropped_bad_value:
        print(
            f"  built {len(observations)} observations "
            f"({dropped_unknown_dx} dropped: unknown dx, {dropped_bad_value} dropped: non-numeric value)"
        )
    else:
        print(f"  built {len(observations)} observations")
    return ChapMakePredictionRequest(
        name=name,
        geojson=geojson,
        providedData=observations,
        dataSources=list(model.data_sources),
        dataToBeFetched=[],
        modelId=model.configured_model.model_template.name,
        nPeriods=n_periods,
        type=dataset_type,
    )


@task(
    name="Submit prediction",
    task_run_name="Submit prediction for {model_label}",
)
def submit_prediction(
    credentials: Dhis2Credentials,
    request: ChapMakePredictionRequest,
    model_label: str,
) -> ChapJobResponse:
    """POST the prediction request and return the job id.

    The ``model_label`` parameter only feeds into the task-run display name
    so each loop iteration is distinguishable in the Prefect UI.
    """
    del model_label  # display-only
    job = ChapClient(credentials).submit_prediction(request)
    print(f"Submitted prediction; job id = {job.id}")
    return job


@task(
    name="Wait for prediction job",
    task_run_name="Wait for prediction job ({model_label})",
)
def wait_for_prediction(
    credentials: Dhis2Credentials,
    job_id: str,
    model_label: str,
    timeout_seconds: int = 600,
    poll_interval_seconds: float = 5.0,
) -> str:
    """Poll ``GET /v1/jobs/{id}`` until the chap job reaches a terminal state."""
    del model_label
    client = ChapClient(credentials)
    deadline = time.monotonic() + timeout_seconds
    last: str | None = None
    while True:
        status = client.job_status(job_id)
        if status != last:
            print(f"  job {job_id}: status={status}")
            last = status
        if status.upper() not in _TRANSIENT_JOB_STATUSES:
            return status
        if time.monotonic() >= deadline:
            raise TimeoutError(f"chap job {job_id} did not finish within {timeout_seconds}s (last status: {status!r})")
        time.sleep(poll_interval_seconds)


@task(
    name="Fetch prediction result",
    task_run_name="Fetch prediction result ({model_label})",
)
def fetch_prediction_result(
    credentials: Dhis2Credentials,
    job_id: str,
    model_label: str,
) -> ChapPredictionResult:
    """Fetch ``GET /v1/jobs/{id}/prediction_result`` and parse it."""
    del model_label
    return ChapClient(credentials).prediction_result(job_id)


# --- per-model orchestration ------------------------------------------------


def _step(name: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Call ``fn(*args, **kwargs)``; on failure raise :class:`_StepFailure`."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        raise _StepFailure(name) from exc


def _model_label(model: ChapConfiguredModelWithDataSource) -> str:
    return f"{model.name} ({model.configured_model.name})"


def _run_one_model(
    credentials: Dhis2Credentials,
    model: ChapConfiguredModelWithDataSource,
    entry: ModelRunEntry,
    n_periods: int,
    dataset_type: Literal["forecasting", "backtesting"],
    timeout_seconds: int,
) -> None:
    label = _model_label(model)
    analytics = _step("fetch_dhis2_for_model", fetch_dhis2_for_model, credentials, model)
    rows = analytics.get("rows", [])
    entry.analytics_rows = len(rows)
    entry.org_units_covered = len(model.org_units)
    entry.periods_covered = len(_enumerate_periods(model.start_period, model.period_type))

    geojson = _step("fetch_org_units_geojson", fetch_org_units_geojson, credentials, model)

    request_name = f"chap-scheduler-{model.name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    request = _step(
        "build_prediction_request",
        build_prediction_request,
        model,
        analytics,
        geojson,
        n_periods,
        dataset_type,
        request_name,
    )

    job = _step("submit_prediction", submit_prediction, credentials, request, label)
    entry.job_id = job.id

    status = _step(
        "wait_for_prediction",
        wait_for_prediction,
        credentials,
        job.id,
        label,
        timeout_seconds,
    )
    if status.upper() != "SUCCESS":
        raise _StepFailure("wait_for_prediction") from RuntimeError(f"job ended with status={status!r}")

    result = _step(
        "fetch_prediction_result",
        fetch_prediction_result,
        credentials,
        job.id,
        label,
    )
    entry.prediction_values = len(result.data_values)
    print(f"Prediction returned {len(result.data_values)} values for '{model.name}'")


# --- run-report artifact ----------------------------------------------------


def _emit_run_report(report: RunReport) -> None:
    markdown = render_report(report)
    create_markdown_artifact(
        markdown=markdown,
        key="dhis2-chap-prediction-report",
        description="chap-scheduler run summary",
    )


# --- flow -------------------------------------------------------------------


def _resolve_n_periods(models: list[ChapConfiguredModelWithDataSource], n_periods: int | None) -> int:
    """Pick a default ``n_periods`` from the first model's period type if not given."""
    if n_periods is not None:
        return n_periods
    if models:
        period_type = models[0].period_type
        return _DEFAULT_N_PERIODS_BY_PERIOD_TYPE.get(period_type, 3)
    return 3


@flow(name="dhis2-chap-prediction", log_prints=True)
def dhis2_chap_prediction(
    credentials: Dhis2Credentials,
    n_periods: int | None = None,
    dataset_type: Literal["forecasting", "backtesting"] = "forecasting",
    prediction_timeout_seconds: int = 600,
) -> RunReport:
    """Run a chap prediction for every configured model on the DHIS2 instance.

    A failure in one model is logged in the run report but does not abort
    the others. The report is emitted as a markdown artifact at the end of
    every run, including when chap-core itself was unreachable.

    Args:
        credentials: The DHIS2 credentials block. The chap route lives on
            the DHIS2 instance itself (set up by the DHIS2 admin), so this
            is the only endpoint identity the flow needs.
        n_periods: Forecast horizon. ``None`` means "pick a sensible default
            from the model's period type" (month -> 3, week -> 12, year -> 1).
        dataset_type: ``"forecasting"`` (default) or ``"backtesting"``.
        prediction_timeout_seconds: Max time to wait for each chap job.

    Returns:
        The accumulated :class:`~chap_scheduler.chap.models.RunReport`.
    """
    report = RunReport(
        dhis2_url=credentials.base_url,
        started_at=datetime.now(timezone.utc),
    )
    try:
        try:
            report.dhis2 = fetch_dhis2_system_info(credentials)
        except Exception as exc:
            report.dhis2_error = f"{type(exc).__name__}: {exc}"
            print(f"DHIS2 not reachable: {report.dhis2_error}")
            return report

        try:
            report.chap = check_chap_core(credentials)
        except Exception as exc:
            report.chap_error = f"{type(exc).__name__}: {exc}"
            print(f"chap-core not reachable: {report.chap_error}")
            return report

        try:
            models = fetch_configured_models(credentials)
        except Exception as exc:
            report.models_error = f"{type(exc).__name__}: {exc}"
            print(f"could not list configured models: {report.models_error}")
            return report

        n = _resolve_n_periods(models, n_periods)
        for model in models:
            entry = ModelRunEntry(
                name=model.name,
                template_name=model.configured_model.name,
            )
            try:
                _run_one_model(credentials, model, entry, n, dataset_type, prediction_timeout_seconds)
                entry.status = "succeeded"
            except _StepFailure as exc:
                entry.step_failed = exc.step
                cause = exc.__cause__
                entry.error = f"{type(cause).__name__}: {cause}" if cause else exc.step
                print(f"[skip] {_model_label(model)}: failed at {exc.step} -- {entry.error}")
            report.entries.append(entry)
        return report
    finally:
        _emit_run_report(report)


if __name__ == "__main__":
    dhis2_chap_prediction.serve(name="dhis2-chap-prediction")
