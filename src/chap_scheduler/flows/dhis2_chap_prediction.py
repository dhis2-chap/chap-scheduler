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

import logging
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, ParamSpec, TypeVar

from dhis2_client.resources.analytics import next_period_id, period_key
from geojson_pydantic import Feature, FeatureCollection
from prefect import flow, task
from prefect.artifacts import create_markdown_artifact
from prefect.exceptions import MissingContextError
from prefect.logging import get_run_logger

from chap_client import (
    ChapConfiguredModelWithDataSource,
    ChapHttpError,
    ChapJobResponse,
    ChapMakePredictionRequest,
    ChapMissingValuesDetail,
    ChapObservation,
    ChapPredictionEntry,
    ChapSystemInfo,
)
from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.config import get_settings
from chap_scheduler.dhis2_models import (
    Dhis2AnalyticsResponse,
    Dhis2OrgUnit,
    Dhis2OrgUnitsResponse,
    Dhis2SystemInfo,
)
from chap_scheduler.report import ModelRunEntry, RunReport, render_report

# Hard ceiling on the number of periods _enumerate_periods will walk before
# refusing to truncate. Roughly: 10 years of monthly data, ~2.3 years weekly,
# 120 years yearly. Picked to keep accidental misconfigurations cheap while
# still covering realistic monthly horizons. Bumping it is a deliberate choice.
_PERIOD_ENUMERATION_CAP = 120
_TRANSIENT_JOB_STATUSES = frozenset({"PENDING", "RUNNING", "STARTED", "QUEUED", "PROCESSING"})
_DEFAULT_N_PERIODS_BY_PERIOD_TYPE: dict[str, int] = {"month": 3, "week": 12, "year": 1}
# Match the chap-frontend's STANDARD_QUANTILES (apps/modeling-app/.../usePredictionEntries.ts).
_DEFAULT_QUANTILES: list[float] = [0.1, 0.25, 0.5, 0.75, 0.9]
# Per-run knobs we keep internal so the Prefect quick-run UI stays minimal.
# (Operator-level knobs like the polling timeout live in
# `chap_scheduler.config.Settings`, env-driven not flow-parameter-driven.)
_DATASET_TYPE: Literal["forecasting", "backtesting"] = "forecasting"
# DHIS2 relative-period window used to probe the latest period that has data
# for every covariate. These are the longest *valid* relative periods DHIS2
# accepts per granularity -- DHIS2 has no LAST_24_MONTHS / LAST_104_WEEKS.
_PROBE_WINDOW_BY_PERIOD_TYPE: dict[str, str] = {
    "month": "LAST_12_MONTHS",
    "week": "LAST_52_WEEKS",
    "year": "LAST_5_YEARS",
}


class _StepFailure(Exception):
    """Raised inside ``_run_one_model`` to label which step failed."""

    def __init__(self, step: str) -> None:
        super().__init__(step)
        self.step = step


def _logger() -> Any:
    """Return Prefect's run logger when in a flow/task context, else a stdlib fallback.

    Lets us call the same ``logger.info(...)`` API from both production paths
    (which always run inside a Prefect context) and unit tests that exercise
    helpers like ``build_prediction_request`` directly via the function rather
    than the ``@task`` wrapper.
    """
    try:
        return get_run_logger()
    except MissingContextError:
        return logging.getLogger("chap_scheduler.flows")


# --- DHIS2 system info ------------------------------------------------------


@task(name="Fetch DHIS2 system info")
def fetch_dhis2_system_info(credentials: Dhis2Credentials) -> Dhis2SystemInfo:
    """Fetch ``GET /api/system/info`` so the report shows which DHIS2 we hit.

    Useful diagnostic if anything downstream fails -- you can tell at a
    glance which DHIS2 version was on the other end.
    """
    log = _logger()
    raw = credentials.get_client().get("/api/system/info")
    info = Dhis2SystemInfo.model_validate(raw)
    log.info("DHIS2 is up at %s (version %s)", credentials.base_url, info.version)
    if info.system_name:
        log.info("  system name : %s", info.system_name)
    return info


# --- chap interactions ------------------------------------------------------


@task(name="Verify chap is reachable")
def check_chap_core(credentials: Dhis2Credentials) -> ChapSystemInfo:
    """Verify the chap route is available on the DHIS2 instance.

    The DHIS2 admin sets up chap as a custom DHIS2 route, so chap is reached
    via the DHIS2 base URL with DHIS2 auth -- we never know or hold a separate
    chap URL. Hits ``GET <dhis2_base_url>/api/routes/chap/run/system/info``.
    """
    log = _logger()
    with credentials.chap_client() as client:
        info = client.system_info()
    log.info("chap is up on %s (chap-core v%s)", credentials.base_url, info.chap_core_version)
    log.info("  chap-core version : %s", info.chap_core_version)
    log.info("  python version    : %s", info.python_version)
    log.info("  server time zone  : %s", info.server_time_zone_id)
    log.info("  server date       : %s", info.server_date.isoformat())
    return info


@task(name="Fetch chap configured models")
def fetch_configured_models(
    credentials: Dhis2Credentials,
) -> list[ChapConfiguredModelWithDataSource]:
    """Pull all configured models with their data-source mappings from chap."""
    log = _logger()
    with credentials.chap_client() as client:
        models = client.list_configured_models_with_data_source()
    log.info("chap has %d configured model(s):", len(models))
    for m in models:
        tmpl = m.configured_model.model_template
        covariates = list(tmpl.required_covariates) + list(m.configured_model.additional_continuous_covariates)
        log.info("  - '%s' (id=%d) using template '%s'", m.name, m.id, tmpl.display_name)
        log.info("      target            : %s", tmpl.target)
        log.info("      period type       : %s", m.period_type)
        log.info("      start period      : %s", m.start_period)
        log.info("      covariates        : %s", ", ".join(covariates) or "(none)")
        log.info("      org units         : %d", len(m.org_units))
        log.info("      data sources (dx) : %d", len(m.data_sources))
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


def _period_covering(d: date, period_type: str) -> str:
    """Return the DHIS2 period ID for the period containing date ``d``.

    Used when an explicit ``end_date`` is supplied to mean
    "we have data through this date" -- the period covering that date is
    treated as included regardless of whether it is technically complete.
    """
    if period_type == "month":
        return f"{d.year}{d.month:02d}"
    if period_type == "year":
        return str(d.year)
    if period_type == "week":
        iso = d.isocalendar()
        return f"{iso.year}W{iso.week:02d}"
    raise ValueError(f"unsupported period_type: {period_type!r}")


def _resolve_end_period(period_type: str, end_date: date | None, today: date | None = None) -> str:
    """Pick the inclusive end period: explicit ``end_date`` wins, else last-completed."""
    if end_date is not None:
        return _period_covering(end_date, period_type)
    return _last_completed_period(period_type, today)


def _enumerate_periods(
    start: str,
    period_type: str,
    end_period: str | None = None,
    end_date: date | None = None,
    today: date | None = None,
) -> list[str]:
    """Walk forward from ``start`` to the inclusive end period.

    Resolution priority for the end:
    ``end_period`` (explicit string, used by the freshness probe) >
    ``end_date`` (user-supplied calendar date) >
    last-completed period for ``today``.
    """
    if end_period is not None:
        end = end_period
    else:
        end = _resolve_end_period(period_type, end_date, today)
    # Defensive: forward-walk from start would otherwise silently run to the
    # 120-period cap and emit a bogus range. Callers that intend a configured
    # start should validate against the resolved end before calling.
    if period_key(start) > period_key(end):
        raise ValueError(f"start period {start!r} is after end period {end!r}; cannot enumerate")
    periods = [start]
    while periods[-1] != end and len(periods) < _PERIOD_ENUMERATION_CAP:
        periods.append(next_period_id(periods[-1]))
    if periods[-1] != end:
        # We hit the cap without reaching end. Returning a truncated list would
        # cause the caller to name and submit the run as if it covered the full
        # start-end range, when in fact a chunk of the tail is missing.
        raise ValueError(
            f"period range from {start!r} to {end!r} exceeds the "
            f"{_PERIOD_ENUMERATION_CAP}-period cap (stopped at {periods[-1]!r}); "
            f"refusing to silently truncate. Tighten start_period or end_date, or "
            f"raise _PERIOD_ENUMERATION_CAP if a longer range is genuinely needed."
        )
    return periods


@task(
    name="Fetch DHIS2 analytics",
    task_run_name="Fetch DHIS2 analytics for {model.name} ({model.configured_model.name})",
)
def fetch_dhis2_for_model(
    credentials: Dhis2Credentials,
    model: ChapConfiguredModelWithDataSource,
    periods: list[str],
) -> Dhis2AnalyticsResponse:
    """Fetch DHIS2 analytics for ``model`` over the given (already-validated) period list."""
    client = credentials.get_client()

    log = _logger()
    dx_uids = [ds.data_element_id for ds in model.data_sources]
    dimension = [
        f"dx:{';'.join(dx_uids)}",
        f"pe:{';'.join(periods)}",
        f"ou:{';'.join(model.org_units)}",
    ]
    log.info(
        "Fetching analytics for '%s': %d dx x %d pe (%s..%s) x %d ou",
        model.name,
        len(dx_uids),
        len(periods),
        periods[0],
        periods[-1],
        len(model.org_units),
    )
    raw = client.get_analytics_data(dimension=dimension)
    response = Dhis2AnalyticsResponse.model_validate(raw)
    log.info(
        "  -> %d rows; columns: %s",
        len(response.rows),
        [h.name for h in response.headers],
    )
    return response


type _Feature = Feature[Any, dict[str, Any]]
type _FeatureCollection = FeatureCollection[_Feature]


@task(
    name="Probe DHIS2 for latest covariate periods",
    task_run_name="Probe DHIS2 latest periods for {model.name} ({model.configured_model.name})",
)
def probe_latest_covariate_periods(
    credentials: Dhis2Credentials,
    model: ChapConfiguredModelWithDataSource,
) -> dict[str, str]:
    """Find the latest period reported for each data element on this DHIS2.

    Hits the analytics API once with a recent relative window
    (``LAST_24_MONTHS`` for monthly models, etc.). For each row in the
    response, tracks the latest period seen for that data element. Returns
    a mapping ``{dataElementId: latestPeriodId}`` -- the caller takes the
    min across covariates to pick a safe end period.
    """
    log = _logger()
    client = credentials.get_client()
    dx = ";".join(ds.data_element_id for ds in model.data_sources)
    ou = ";".join(model.org_units)
    window = _PROBE_WINDOW_BY_PERIOD_TYPE.get(model.period_type, "LAST_24_MONTHS")
    log.info("Probing DHIS2 covariate freshness over %s", window)
    raw = client.get_analytics_data(dimension=[f"dx:{dx}", f"pe:{window}", f"ou:{ou}"])
    response = Dhis2AnalyticsResponse.model_validate(raw)
    latest: dict[str, str] = {}
    for row in response.rows:
        if len(row) < 4:
            continue
        de, period = row[0], row[1]
        if de not in latest or period_key(period) > period_key(latest[de]):
            latest[de] = period
    by_covariate = {ds.covariate: latest.get(ds.data_element_id, "(no data)") for ds in model.data_sources}
    log.info("  latest period per covariate: %s", by_covariate)
    return latest


def _safe_end_period(
    latest_per_de: dict[str, str],
    expected_data_element_ids: Iterable[str],
) -> str | None:
    """Return the min latest period across covariates, IFF *every* expected DE has data.

    A partial probe result is just as bad as an empty one: chap will reject the
    submission because at least one covariate has no values for the chosen
    period range. Returning ``None`` here lets the caller convert that into a
    diagnostic per-model failure rather than silently picking a period that
    only some covariates can support.
    """
    expected = set(expected_data_element_ids)
    if not expected or not expected.issubset(latest_per_de.keys()):
        return None
    return min((latest_per_de[de] for de in expected), key=period_key)


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
    raw = client.get(
        "/api/organisationUnits",
        params={
            "filter": f"id:in:[{','.join(model.org_units)}]",
            "fields": "id,geometry,parent[id],level,displayName,code",
            "paging": "false",
        },
    )
    response = Dhis2OrgUnitsResponse.model_validate(raw)
    _logger().info("Fetched %d org-unit features for '%s'", len(response.organisation_units), model.name)
    features = [_build_feature(ou) for ou in response.organisation_units if ou.geometry]
    return FeatureCollection[_Feature](type="FeatureCollection", features=features)


def _build_feature(org_unit: Dhis2OrgUnit) -> _Feature:
    properties: dict[str, Any] = {
        "id": org_unit.id,
        "level": org_unit.level,
        "displayName": org_unit.display_name,
    }
    if org_unit.code:
        properties["code"] = org_unit.code
    if org_unit.parent and org_unit.parent.id:
        properties["parent"] = org_unit.parent.id
        # `parentGraph` is set to the same value as `parent` to match the
        # chap-frontend's exact behaviour (see
        # apps/modeling-app/.../ModelExecutionForm/utils/orgUnitGeoJson.ts in
        # dhis2-chap/chap-frontend). chap doesn't currently use the
        # slash-delimited ancestor form here.
        properties["parentGraph"] = org_unit.parent.id
    return Feature[Any, dict[str, Any]](
        type="Feature",
        id=org_unit.id,
        geometry=org_unit.geometry,
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
    analytics: Dhis2AnalyticsResponse,
    geojson: _FeatureCollection,
    n_periods: int,
    dataset_type: Literal["forecasting", "backtesting"],
    name: str,
) -> ChapMakePredictionRequest:
    """Pure transform: analytics rows + chap config -> ``ChapMakePredictionRequest``.

    Maps each analytics row's ``dataElementId`` (column 0) to a covariate
    name via ``model.data_sources``. Rows whose dx isn't in ``dataSources``
    or whose value isn't numeric are dropped, with a count logged.
    """
    log = _logger()
    covariate_by_de = {ds.data_element_id: ds.covariate for ds in model.data_sources}
    rows = analytics.rows
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
        log.info(
            "  built %d observations (%d dropped: unknown dx, %d dropped: non-numeric value)",
            len(observations),
            dropped_unknown_dx,
            dropped_bad_value,
        )
    else:
        log.info("  built %d observations", len(observations))
    return ChapMakePredictionRequest(
        name=name,
        geojson=geojson,
        providedData=observations,
        dataSources=list(model.data_sources),
        dataToBeFetched=[],
        configuredModelWithDataSourceId=model.id,
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
    with credentials.chap_client() as client:
        job = client.submit_prediction(request)
    _logger().info("Submitted prediction; job id = %s", job.id)
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
    """Poll ``GET /v1/jobs/{id}`` until the chap job reaches a terminal state.

    Uses ``time.sleep`` between polls. This is fine because Prefect runs sync
    tasks like this one in a worker thread, so the sleep blocks that thread
    only -- not the engine's event loop. If we ever switch ``@task`` calls to
    async execution, convert this to ``async def`` + ``await asyncio.sleep``
    (and the flow body will need ``await`` at the call site).
    """
    del model_label
    log = _logger()
    deadline = time.monotonic() + timeout_seconds
    last: str | None = None
    with credentials.chap_client() as client:
        while True:
            status = client.job_status(job_id)
            if status != last:
                log.info("  job %s: status=%s", job_id, status)
                last = status
            if status.upper() not in _TRANSIENT_JOB_STATUSES:
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"chap job {job_id} did not finish within {timeout_seconds}s (last status: {status!r})"
                )
            time.sleep(poll_interval_seconds)


@task(
    name="Fetch prediction result",
    task_run_name="Fetch prediction result ({model_label})",
)
def fetch_prediction_result(
    credentials: Dhis2Credentials,
    job_id: str,
    model_label: str,
    quantiles: list[float] | None = None,
) -> tuple[int, list[ChapPredictionEntry]]:
    """Resolve the prediction id from the job and fetch its values.

    Two-step lookup because chap's single-job status endpoint only returns
    a string status: list ``GET /v1/jobs`` to find this job's full
    description (and its ``result``, which is the prediction id), then
    fetch values via ``/v1/analytics/prediction-entry/{id}?quantiles=...``.
    """
    del model_label
    with credentials.chap_client() as client:
        desc = client.job_description(job_id)
        if desc is None or desc.result is None:
            raise RuntimeError(f"could not resolve prediction id for job {job_id}")
        try:
            prediction_id = int(desc.result)
        except ValueError as exc:
            raise RuntimeError(f"job {job_id} result {desc.result!r} is not an int prediction id") from exc
        entries = client.prediction_entries(prediction_id, quantiles=quantiles or _DEFAULT_QUANTILES)
    _logger().info("Fetched %d prediction entries (prediction id=%d)", len(entries), prediction_id)
    return prediction_id, entries


# --- per-model orchestration ------------------------------------------------

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _step(name: str, fn: Callable[_P, _R], *args: _P.args, **kwargs: _P.kwargs) -> _R:
    """Call ``fn(*args, **kwargs)``; on failure raise `_StepFailure`.

    Preserves ``fn``'s return type via ``ParamSpec`` + ``TypeVar`` so call
    sites in `_run_one_model()` keep their static types instead of
    collapsing to ``Any``.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        raise _StepFailure(name) from exc


def _model_label(model: ChapConfiguredModelWithDataSource) -> str:
    return f"{model.name} ({model.configured_model.name})"


def _default_prediction_name(
    model: ChapConfiguredModelWithDataSource,
    *,
    end_period: str | None = None,
    end_date: date | None = None,
) -> str:
    """Human-readable name for a prediction submitted by this flow.

    Renders as e.g. ``"test (chapkit-ewars-model) 202301-202412"`` -- pairs
    the configured-model name with its template (so an operator scanning
    chap's predictions table can tell what produced each row) and the input
    period range (so two runs over different windows are distinguishable).
    """
    start = model.start_period
    end = end_period if end_period is not None else _resolve_end_period(model.period_type, end_date)
    return f"{_model_label(model)} {start}-{end}"


def _default_n_periods_for(model: ChapConfiguredModelWithDataSource) -> int:
    """Per-model forecast horizon, matching the chap-frontend's defaults."""
    return _DEFAULT_N_PERIODS_BY_PERIOD_TYPE.get(model.period_type, 3)


def _resolve_end_period_for_run(
    credentials: Dhis2Credentials,
    model: ChapConfiguredModelWithDataSource,
    end_date: date | None,
) -> str:
    """Decide the inclusive end period for this run.

    User-supplied ``end_date`` (if set) wins -- the user is asserting "I have
    data through here, trust me", and it bypasses the probe entirely.

    Otherwise we probe the DHIS2 analytics API for the latest period reported
    per data element. We require **complete coverage**: every covariate the
    configured model needs must have at least one value in the probe window.
    A partial result -- e.g. population is up-to-date but rainfall has no
    values yet -- raises a `_StepFailure` naming the missing
    covariates, since chap would reject the submission anyway and a clear
    diagnostic in the run report is more useful than a silent fallback to
    the last completed calendar period.
    """
    log = _logger()
    if end_date is not None:
        chosen = _period_covering(end_date, model.period_type)
        log.info("Using user-supplied end period %s (end_date=%s)", chosen, end_date.isoformat())
        return chosen

    latest = _step("probe_latest_covariate_periods", probe_latest_covariate_periods, credentials, model)
    expected_ids = {ds.data_element_id for ds in model.data_sources}
    missing_ids = expected_ids - latest.keys()
    if missing_ids:
        missing_covariates = sorted(ds.covariate for ds in model.data_sources if ds.data_element_id in missing_ids)
        window = _PROBE_WINDOW_BY_PERIOD_TYPE.get(model.period_type, "LAST_12_MONTHS")
        raise _StepFailure("probe_latest_covariate_periods") from RuntimeError(
            f"DHIS2 returned no data within {window} for covariate(s): "
            f"{', '.join(missing_covariates)}. Import the missing data, or "
            f"trigger the flow with an explicit end_date to override."
        )

    probed = _safe_end_period(latest, expected_ids)
    if probed is None:
        # The missing-IDs branch above guarantees this can't happen, but
        # `assert` would be stripped under `python -O`. Belt-and-braces.
        raise _StepFailure("probe_latest_covariate_periods") from RuntimeError(
            "internal: probe coverage check passed but no end period was selected"
        )
    log.info("Using probed end period %s (min latest across covariates)", probed)
    return probed


def _run_one_model(
    credentials: Dhis2Credentials,
    model: ChapConfiguredModelWithDataSource,
    entry: ModelRunEntry,
    end_date: date | None,
    *,
    prediction_timeout_seconds: int,
) -> None:
    label = _model_label(model)

    end_period = _resolve_end_period_for_run(credentials, model, end_date)

    # Validate the configured start..end range BEFORE we hit DHIS2:
    #   1. start must be at-or-before the selected end (otherwise nothing to fetch).
    #   2. the range must fit within _PERIOD_ENUMERATION_CAP (otherwise we'd
    #      ship a truncated query that silently lies about coverage).
    # Both failure modes are configuration issues, so label the failure as
    # validate_period_range rather than letting it bleed into the fetch step.
    if period_key(model.start_period) > period_key(end_period):
        source = "user-supplied end_date" if end_date is not None else "probed end period"
        raise _StepFailure("validate_period_range") from RuntimeError(
            f"configured start_period {model.start_period!r} is after the {source} "
            f"{end_period!r}; nothing to fetch. Check the chap configured-model "
            f"definition or trigger with an end_date at or after start_period."
        )
    try:
        periods = _enumerate_periods(model.start_period, model.period_type, end_period=end_period)
    except ValueError as exc:
        # The start>end branch is already covered above, so this only fires
        # for the cap-exceeded case.
        raise _StepFailure("validate_period_range") from exc

    entry.org_units_covered = len(model.org_units)
    entry.periods_covered = len(periods)

    analytics = _step("fetch_dhis2_for_model", fetch_dhis2_for_model, credentials, model, periods)
    entry.analytics_rows = len(analytics.rows)

    geojson = _step("fetch_org_units_geojson", fetch_org_units_geojson, credentials, model)

    request_name = _default_prediction_name(model, end_period=end_period)
    request = _step(
        "build_prediction_request",
        build_prediction_request,
        model,
        analytics,
        geojson,
        _default_n_periods_for(model),
        _DATASET_TYPE,
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
        prediction_timeout_seconds,
    )
    if status.upper() != "SUCCESS":
        raise _StepFailure("wait_for_prediction") from RuntimeError(f"job ended with status={status!r}")

    prediction_id, entries = _step(
        "fetch_prediction_result",
        fetch_prediction_result,
        credentials,
        job.id,
        label,
    )
    entry.prediction_id = prediction_id
    entry.prediction_values = len(entries)
    entry.predicted_periods = sorted({e.period for e in entries})
    _logger().info(
        "Prediction returned %d values for '%s' across %d periods",
        len(entries),
        model.name,
        len(entry.predicted_periods),
    )


def _populate_entry_from_step_failure(entry: ModelRunEntry, exc: _StepFailure) -> None:
    """Map a `_StepFailure` onto a `ModelRunEntry`'s failure fields.

    Captures which step failed, formats the underlying cause for the report,
    and -- when the cause is a `ChapHttpError` -- attempts to parse
    chap's structured "missing values" detail into ``entry.rejection_detail``
    so the markdown artifact renders the per-covariate summary.

    Extracted from the flow body so the routing logic is reachable from
    unit tests without spinning up a Prefect run.
    """
    entry.step_failed = exc.step
    cause = exc.__cause__
    entry.error = f"{type(cause).__name__}: {cause}" if cause else exc.step
    if isinstance(cause, ChapHttpError):
        # TODO: chap PR is switching this from 400 to 200; once merged we'll
        # also need to look for the same shape in the submit_prediction
        # success body.
        entry.rejection_detail = ChapMissingValuesDetail.from_error_body(cause.detail)


# --- run-report artifact ----------------------------------------------------


def _emit_run_report(report: RunReport) -> None:
    markdown = render_report(report)
    create_markdown_artifact(
        markdown=markdown,
        key="dhis2-chap-prediction-report",
        description="chap-scheduler run summary",
    )


# --- flow -------------------------------------------------------------------


@flow(name="dhis2-chap-prediction")
def dhis2_chap_prediction(
    credentials: Dhis2Credentials,
    end_date: date | None = None,
) -> RunReport:
    """Run a chap prediction for every configured model on the DHIS2 instance.

    A failure in one model is logged in the run report but does not abort
    the others. The report is emitted as a markdown artifact at the end of
    every run, including when chap-core itself was unreachable.

    Args:
        credentials: The DHIS2 credentials block. The chap route lives on
            the DHIS2 instance itself (set up by the DHIS2 admin), so this
            is the only endpoint identity the flow needs.
        end_date: Inclusive cut-off date for the analytics range. The period
            covering this date is included regardless of whether it is
            technically complete -- treat it as "we have data through here".
            Each configured model converts this date to its own period
            granularity (month / week / year). When omitted (default), each
            model uses the period before the one covering today.

    Returns:
        The accumulated `RunReport`.

    Note:
        Other knobs (forecast horizon, dataset type, job timeout) are kept
        internal -- ``n_periods`` is derived per-model from its period type
        (month -> 3, week -> 12, year -> 1), ``dataset_type`` is always
        ``"forecasting"``, and the per-job timeout is governed by
        ``CHAP_SCHEDULER_PREDICTION_TIMEOUT_SECONDS`` (default 1 hour;
        see `Settings`).
    """
    settings = get_settings()
    log = _logger()
    report = RunReport(
        dhis2_url=credentials.base_url,
        started_at=datetime.now(timezone.utc),
    )
    try:
        try:
            report.dhis2 = fetch_dhis2_system_info(credentials)
        except Exception as exc:
            report.dhis2_error = f"{type(exc).__name__}: {exc}"
            log.error("DHIS2 not reachable: %s", report.dhis2_error)
            return report

        try:
            report.chap = check_chap_core(credentials)
        except Exception as exc:
            report.chap_error = f"{type(exc).__name__}: {exc}"
            log.error("chap-core not reachable: %s", report.chap_error)
            return report

        try:
            models = fetch_configured_models(credentials)
        except Exception as exc:
            report.models_error = f"{type(exc).__name__}: {exc}"
            log.error("could not list configured models: %s", report.models_error)
            return report

        for model in models:
            entry = ModelRunEntry(
                name=model.name,
                template_name=model.configured_model.name,
            )
            try:
                _run_one_model(
                    credentials,
                    model,
                    entry,
                    end_date,
                    prediction_timeout_seconds=settings.prediction_timeout_seconds,
                )
                entry.status = "succeeded"
            except _StepFailure as exc:
                _populate_entry_from_step_failure(entry, exc)
                log.warning("[skip] %s: failed at %s -- %s", _model_label(model), exc.step, entry.error)
            report.entries.append(entry)
        return report
    finally:
        _emit_run_report(report)


def _register_blocks_on_startup() -> None:
    """Register the block types this flow depends on with the chap-scheduler API.

    Done from the worker (here) rather than the API lifespan, so the call
    goes over real HTTP (PREFECT_API_URL points at the already-serving
    chap-scheduler container) instead of triggering Prefect's ephemeral
    mode and spawning a second in-process Prefect server.
    """
    Dhis2Credentials.register_type_and_schema()


if __name__ == "__main__":
    _register_blocks_on_startup()
    dhis2_chap_prediction.serve(name="dhis2-chap-prediction")
