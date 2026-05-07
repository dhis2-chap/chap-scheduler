"""DHIS2 -> chap prediction flow.

Step-by-step:

1. Verify chap is reachable on the chosen DHIS2 instance.
2. Pull the chap configured-models (target / covariates / org units / period
   range) from chap.
3. For each configured model, build the DHIS2 analytics query implied by
   that configuration and fetch the data.

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

from datetime import date
from typing import Any

from dhis2_client.resources.analytics import next_period_id
from prefect import flow, task

from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.chap import (
    ChapConfiguredModelWithDataSource,
    ChapSystemInfo,
)

_PERIOD_ENUMERATION_CAP = 120


@task
def check_chap_core(credentials: Dhis2Credentials) -> ChapSystemInfo:
    """Verify the chap route is available on the DHIS2 instance.

    The DHIS2 admin sets up chap as a custom DHIS2 route, so chap is reached
    via the DHIS2 base URL with DHIS2 auth -- we never know or hold a separate
    chap URL. Hits ``GET <dhis2_base_url>/api/routes/chap/run/system/info``.
    """
    client = credentials.get_client()
    raw = client.get("/api/routes/chap/run/system/info")
    info = ChapSystemInfo.model_validate(raw)
    print(f"chap is up on {credentials.base_url} (chap-core v{info.chap_core_version})")
    print(f"  chap-core version : {info.chap_core_version}")
    print(f"  python version    : {info.python_version}")
    print(f"  server time zone  : {info.server_time_zone_id}")
    print(f"  server date       : {info.server_date.isoformat()}")
    return info


@task
def fetch_configured_models(
    credentials: Dhis2Credentials,
) -> list[ChapConfiguredModelWithDataSource]:
    """Pull all configured models with their data-source mappings from chap."""
    client = credentials.get_client()
    raw = client.get("/api/routes/chap/run/v1/crud/configured-models-with-data-source")
    models = [ChapConfiguredModelWithDataSource.model_validate(item) for item in raw]
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


def _current_period(period_type: str, today: date | None = None) -> str:
    """Return the DHIS2 period ID covering ``today`` for ``period_type``."""
    today = today or date.today()
    if period_type == "month":
        return f"{today.year}{today.month:02d}"
    if period_type == "year":
        return str(today.year)
    if period_type == "week":
        iso = today.isocalendar()
        return f"{iso.year}W{iso.week:02d}"
    raise ValueError(f"unsupported period_type: {period_type!r}")


def _enumerate_periods(start: str, period_type: str, today: date | None = None) -> list[str]:
    """Walk forward from ``start`` until we reach the period covering today."""
    end = _current_period(period_type, today)
    periods = [start]
    while periods[-1] != end and len(periods) < _PERIOD_ENUMERATION_CAP:
        periods.append(next_period_id(periods[-1]))
    return periods


@task
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
    for row in rows[:5]:
        print(f"     {row}")
    if len(rows) > 5:
        print(f"     ... ({len(rows) - 5} more)")
    return data


@flow(name="dhis2-chap-prediction", log_prints=True)
def dhis2_chap_prediction(credentials: Dhis2Credentials) -> ChapSystemInfo:
    """Verify chap, list its configured models, and fetch DHIS2 data per model.

    Args:
        credentials: The DHIS2 credentials block. The chap route lives on
            the DHIS2 instance itself (set up by the DHIS2 admin), so this
            is the only endpoint identity the flow needs.

    Returns:
        The chap system info payload.
    """
    info = check_chap_core(credentials)
    models = fetch_configured_models(credentials)
    for model in models:
        fetch_dhis2_for_model(credentials, model)
    return info


if __name__ == "__main__":
    dhis2_chap_prediction.serve(name="dhis2-chap-prediction")
