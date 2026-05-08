"""DHIS2 native models + run-report shapes used by the scheduler.

The chap-side models (request / response envelopes for the chap API)
live in `chap_client.schemas` and are re-exported by
`chap_scheduler.chap` for backwards compatibility with existing
import paths.
"""

from datetime import datetime
from typing import Any, Literal

from chap_client.schemas import ChapMissingValuesDetail, ChapSystemInfo
from pydantic import BaseModel, ConfigDict, Field

_ALLOW_ALIAS = ConfigDict(extra="ignore", populate_by_name=True)


class Dhis2SystemInfo(BaseModel):
    """Subset of ``GET /api/system/info`` that we care about for the run report.

    Only the fields useful for diagnostics are modelled; the rest (and DHIS2
    has many) are ignored via ``extra="ignore"``.
    """

    model_config = _ALLOW_ALIAS

    version: str
    build_time: datetime | None = Field(default=None, alias="buildTime")
    system_name: str | None = Field(default=None, alias="systemName")
    server_date: datetime | None = Field(default=None, alias="serverDate")
    instance_base_url: str | None = Field(default=None, alias="contextPath")


# --- DHIS2 native API responses --------------------------------------------


class Dhis2AnalyticsHeader(BaseModel):
    """One column descriptor from ``GET /api/analytics``."""

    model_config = ConfigDict(extra="ignore")

    name: str
    column: str | None = None
    value_type: str | None = Field(default=None, alias="valueType")
    type: str | None = None


class Dhis2AnalyticsResponse(BaseModel):
    """Subset of the DHIS2 analytics response shape.

    DHIS2 returns each cell as a string in ``rows`` regardless of the data
    element's value type; numeric coercion happens at the call site (see
    ``build_prediction_request``).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    headers: list[Dhis2AnalyticsHeader] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class Dhis2OrgUnitParentRef(BaseModel):
    """The ``parent`` field from ``/api/organisationUnits?fields=...,parent[id]``."""

    model_config = ConfigDict(extra="ignore")

    id: str


class Dhis2OrgUnit(BaseModel):
    """One organisation unit as returned by ``/api/organisationUnits?fields=...``.

    Geometry is left as a raw dict because chap accepts the GeoJSON geometry
    object verbatim and we only round-trip it -- no need to re-validate
    Polygon vs MultiPolygon vs anything else.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    display_name: str | None = Field(default=None, alias="displayName")
    code: str | None = None
    level: int | None = None
    parent: Dhis2OrgUnitParentRef | None = None
    geometry: dict[str, Any] | None = None


class Dhis2OrgUnitsResponse(BaseModel):
    """Wrapper for ``/api/organisationUnits``: the rows live under ``organisationUnits``."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    organisation_units: list[Dhis2OrgUnit] = Field(default_factory=list, alias="organisationUnits")


# --- run-report (artifact summary) ------------------------------------------


class ModelRunEntry(BaseModel):
    """Per-configured-model outcome inside a flow run."""

    name: str
    template_name: str
    status: Literal["succeeded", "failed"] = "failed"
    step_failed: str | None = None
    error: str | None = None
    rejection_detail: ChapMissingValuesDetail | None = None
    job_id: str | None = None
    prediction_id: int | None = None
    analytics_rows: int | None = None
    org_units_covered: int | None = None
    periods_covered: int | None = None
    prediction_values: int | None = None
    predicted_periods: list[str] | None = None


class RunReport(BaseModel):
    """End-of-run summary, rendered as a markdown artifact."""

    dhis2_url: str
    started_at: datetime
    dhis2: Dhis2SystemInfo | None = None
    dhis2_error: str | None = None
    chap: ChapSystemInfo | None = None
    chap_error: str | None = None
    models_error: str | None = None
    entries: list[ModelRunEntry] = Field(default_factory=list)
