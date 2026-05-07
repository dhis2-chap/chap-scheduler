"""DHIS2 → CHAP prediction flow.

Pulls an analytics series from DHIS2 and produces a naive next-period
prediction. Stand-in until real CHAP model inference is wired up — for now
we just demonstrate the data path end-to-end.

The flow accepts a ``Dhis2Credentials`` block as a required parameter, so
each run picks the DHIS2 instance to talk to. Register one or more block
instances ahead of time (UI: ``/prefect/blocks/catalog`` → "DHIS2 Credentials
(chap-scheduler)" → New) and pick one from the dropdown when triggering.

Run as a worker against the embedded Prefect server:

    python -m chap_scheduler.flows.dhis2_chap_prediction
"""

# NOTE: don't add `from __future__ import annotations` here — Prefect builds
# a Pydantic model from this flow's signature for parameter validation, and
# stringified annotations turn the Dhis2Credentials block reference into an
# unresolvable forward ref ("class is not fully defined") at run time.

from statistics import mean

from prefect import flow, task

from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.chap import ChapSystemInfo

AnalyticsRow = list[str]


@task
def check_chap_core(credentials: Dhis2Credentials) -> ChapSystemInfo:
    """Verify the chap route is available on the DHIS2 instance.

    The DHIS2 admin sets up chap as a custom DHIS2 route, so chap is reached
    via the DHIS2 base URL with DHIS2 auth — we never know or hold a separate
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
def fetch_analytics(
    credentials: Dhis2Credentials,
    data_element: str,
    periods: str,
    org_unit: str,
) -> list[AnalyticsRow]:
    """Pull analytics rows for ``dx:<data_element>`` × ``pe:<periods>`` × ``ou:<org_unit>``."""
    client = credentials.get_client()
    data = client.get_analytics_data(
        dimension=[f"dx:{data_element}", f"pe:{periods}", f"ou:{org_unit}"],
    )
    rows: list[AnalyticsRow] = data.get("rows", [])
    return rows


@task
def summarize(rows: list[AnalyticsRow]) -> dict[str, float]:
    """Compute count / sum / mean / min / max across the value column."""
    values = [float(r[3]) for r in rows]
    if not values:
        return {"count": 0, "sum": 0.0, "mean": 0.0, "min": 0.0, "max": 0.0}
    return {
        "count": float(len(values)),
        "sum": sum(values),
        "mean": mean(values),
        "min": min(values),
        "max": max(values),
    }


@task
def predict_next_period(rows: list[AnalyticsRow], window: int = 3) -> float:
    """Naive prediction: mean of the last ``window`` periods (period column sorted ascending)."""
    rows_sorted = sorted(rows, key=lambda r: r[1])
    values = [float(r[3]) for r in rows_sorted[-window:]]
    return mean(values) if values else 0.0


@flow(name="dhis2-chap-prediction", log_prints=True)
def dhis2_chap_prediction(
    credentials: Dhis2Credentials,
    # --- Disabled while we focus on chap integration. ----------------------
    # Analytic parameters (dx / pe / ou) will be supplied by chap itself
    # (via DHIS2's chap route) once that integration lands; flip these back
    # on then.
    # data_element: str = "fbfJHSPpUQD",
    # periods: str = "LAST_12_MONTHS",
    # org_unit: str = "USER_ORGUNIT",
    # window: int = 3,
) -> ChapSystemInfo:
    """Run a chap prediction against the chosen DHIS2 instance.

    Currently a stub — only verifies that the DHIS2 instance has the chap
    route set up. The DHIS2 ingestion + naive-prediction pipeline is parked
    below until chap is wired up to supply the analytic parameters.

    Args:
        credentials: The DHIS2 credentials block. The chap route lives on
            the DHIS2 instance itself (set up by the DHIS2 admin), so this
            is the only endpoint identity the flow needs.

    Returns:
        The chap system info payload.
    """
    return check_chap_core(credentials)

    # --- DHIS2 ingestion + prediction (disabled, see signature note). ------
    # rows = fetch_analytics(credentials, data_element, periods, org_unit)
    # summary = summarize(rows)
    # prediction = predict_next_period(rows, window=window)
    # print(f"Using DHIS2 instance: {credentials.base_url} (user={credentials.username})")
    # print(f"Returned {int(summary['count'])} rows for dx={data_element} pe={periods} ou={org_unit}")
    # print(f"Summary: {summary}")
    # print(f"Prediction (mean of last {window} periods): {prediction:.2f}")


if __name__ == "__main__":
    dhis2_chap_prediction.serve(name="dhis2-chap-prediction")
