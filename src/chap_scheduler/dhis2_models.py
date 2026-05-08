"""DHIS2 native API response models used by the prediction flow.

The chap-side request / response shapes live in `chap_client.schemas`;
these models are the DHIS2 side -- the shapes returned by DHIS2's own
``/api/system/info``, ``/api/analytics``, and ``/api/organisationUnits``
endpoints.
"""

from datetime import datetime
from typing import Any

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
