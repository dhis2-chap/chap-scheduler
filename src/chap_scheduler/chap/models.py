"""Pydantic models for chap requests / responses exchanged via DHIS2.

Field names match chap's camelCase JSON via ``alias=`` while the Python
attributes stay snake_case. ``extra="ignore"`` so unknown fields chap may
add later don't break us — we only model what we actively use.

When sending payloads back to chap (the ``ChapMakePredictionRequest``),
dump with ``model_dump(by_alias=True)`` so the JSON keys match what chap
expects.
"""

from datetime import datetime
from typing import Any, Literal

from geojson_pydantic import Feature, FeatureCollection
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Used for chap response models: snake_case attributes, camelCase aliases.
_ALLOW_ALIAS = ConfigDict(extra="ignore", populate_by_name=True)

# Used for request models that include fields starting with ``model_``
# (Pydantic reserves that namespace by default, which would shadow ``modelId``).
_ALLOW_ALIAS_MODEL_NS = ConfigDict(
    extra="ignore",
    populate_by_name=True,
    protected_namespaces=(),
)


class ChapSystemInfo(BaseModel):
    """Payload returned by ``GET /api/routes/chap/run/system/info``.

    The ``revision`` field that chap occasionally returns is intentionally
    omitted — it is almost never set in practice.
    """

    model_config = ConfigDict(extra="ignore")

    chap_core_version: str = Field(description="chap-core release version.")
    python_version: str = Field(description="Python interpreter version on the chap host.")
    server_date: datetime = Field(description="Server clock at the time of the request.")
    server_time_zone_id: str = Field(description="IANA timezone of the chap host.")


class Dhis2SystemInfo(BaseModel):
    """Subset of ``GET /api/system/info`` that we care about for the run report.

    Only the fields useful for diagnostics are modelled; the rest (and DHIS2
    has many) are ignored via ``extra="ignore"``.
    """

    model_config = _ALLOW_ALIAS

    version: str
    revision: str | None = None
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


class ChapDataSource(BaseModel):
    """A covariate ↔ DHIS2 data-element mapping inside a configured model."""

    model_config = _ALLOW_ALIAS

    covariate: str
    data_element_id: str = Field(alias="dataElementId")


class ChapModelTemplate(BaseModel):
    """The chap model template a configured model is based on.

    Only the fields we actively use or surface in logs are modelled — the
    rest (URLs, archived flags, hpoSearchSpace, etc.) are left to ``extra``.
    """

    model_config = _ALLOW_ALIAS

    name: str
    display_name: str = Field(alias="displayName")
    target: str
    required_covariates: list[str] = Field(alias="requiredCovariates", default_factory=list)
    supported_period_type: str = Field(alias="supportedPeriodType")


class ChapConfiguredModel(BaseModel):
    """The ``configuredModel`` block embedded in each configured-model row."""

    model_config = _ALLOW_ALIAS

    id: int
    name: str
    additional_continuous_covariates: list[str] = Field(alias="additionalContinuousCovariates", default_factory=list)
    model_template: ChapModelTemplate = Field(alias="modelTemplate")


class ChapConfiguredModelWithDataSource(BaseModel):
    """One row from ``GET .../v1/crud/configured-models-with-data-source``.

    Carries everything we need to construct a DHIS2 analytics query: the
    DHIS2 data elements (per covariate), the org units, and the period
    range (``start_period`` → present, in ``period_type`` granularity).
    """

    model_config = _ALLOW_ALIAS

    id: int
    name: str
    configured_model: ChapConfiguredModel = Field(alias="configuredModel")
    start_period: str = Field(alias="startPeriod")
    org_units: list[str] = Field(alias="orgUnits")
    data_sources: list[ChapDataSource] = Field(alias="dataSources")
    period_type: str = Field(alias="periodType")


# --- chap make-prediction request envelope ----------------------------------


class ChapObservation(BaseModel):
    """One value-per-(period, org_unit, covariate) cell, sent to chap as input."""

    model_config = _ALLOW_ALIAS

    feature_name: str = Field(alias="featureName")
    org_unit: str = Field(alias="orgUnit")
    period: str
    value: float


class ChapFetchRequest(BaseModel):
    """Tells chap to fetch a covariate from an external data source itself."""

    model_config = _ALLOW_ALIAS

    feature_name: str = Field(alias="featureName")
    data_source_name: str = Field(alias="dataSourceName")


class ChapMakePredictionRequest(BaseModel):
    """Body for ``POST /v1/analytics/make-prediction-with-data-source``.

    We use the ``-with-data-source`` variant (vs the stable ``make-prediction``)
    because it carries ``configuredModelWithDataSourceId``, which chap stores
    on the resulting prediction so the UI can link it back to the configured
    model that produced it.
    """

    model_config = _ALLOW_ALIAS_MODEL_NS

    name: str
    geojson: FeatureCollection[Feature[Any, dict[str, Any]]]
    provided_data: list[ChapObservation] = Field(alias="providedData")
    data_sources: list[ChapDataSource] = Field(alias="dataSources")
    data_to_be_fetched: list[ChapFetchRequest] = Field(alias="dataToBeFetched", default_factory=list)
    configured_model_with_data_source_id: int = Field(alias="configuredModelWithDataSourceId")
    n_periods: int = Field(alias="nPeriods", default=3)
    type: Literal["forecasting", "backtesting"] = "forecasting"


# --- chap job + prediction result -------------------------------------------


class ChapJobResponse(BaseModel):
    """Sync response from ``POST /v1/analytics/make-prediction`` -- just the id."""

    id: str


class ChapJobDescription(BaseModel):
    """Row from ``GET /v1/jobs`` / shape we'd build polling job status."""

    model_config = _ALLOW_ALIAS

    id: str
    type: str
    name: str
    status: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    result: str | None = None


class ChapPredictionEntry(BaseModel):
    """One predicted value from ``GET /v1/analytics/prediction-entry/{id}?quantiles=...``."""

    model_config = _ALLOW_ALIAS

    org_unit: str = Field(alias="orgUnit")
    period: str
    quantile: float
    value: float


# --- chap structured errors ------------------------------------------------


class ChapRejection(BaseModel):
    """One rejected ``(org_unit, feature_name)`` cell in a chap 400 response."""

    model_config = _ALLOW_ALIAS

    reason: str
    org_unit: str = Field(alias="orgUnit")
    feature_name: str = Field(alias="featureName")
    time_periods: list[str] = Field(alias="timePeriods", default_factory=list)


class ChapMissingValuesDetail(BaseModel):
    """The structured ``detail`` body chap returns when input validation fails.

    Today chap returns this shape inside an HTTP 400 body (FastAPI's
    ``{"detail": {...}}`` envelope, which :meth:`from_error_body` peels off).

    .. note::

        Upstream chap has a pending PR to switch this to a 200 response with
        a similar (but possibly differently-enveloped) shape -- so partial
        rejections become "success with warnings" rather than failures. When
        that lands we'll add a parallel parser for the success body and
        treat partially-rejected predictions as ``status="succeeded"`` with
        a ``rejection_detail`` set.

    Example payload:

    .. code-block:: json

        {
          "message": "All regions rejected due to missing values",
          "imported_count": 0,
          "rejected": [
            {"reason": "...", "orgUnit": "...", "featureName": "rainfall",
             "timePeriods": ["202510", "202511"]}
          ]
        }
    """

    model_config = _ALLOW_ALIAS

    message: str
    imported_count: int = 0
    rejected: list[ChapRejection] = Field(default_factory=list)

    @classmethod
    def from_error_body(cls, body: Any) -> "ChapMissingValuesDetail | None":
        """Try to parse a chap error body. Returns ``None`` if shape doesn't match."""
        if not isinstance(body, dict):
            return None
        inner = body.get("detail")
        if not isinstance(inner, dict):
            return None
        try:
            return cls.model_validate(inner)
        except ValidationError:
            # Shape mismatch -- not a missing-values rejection. Bare Exception
            # would mask programmer errors inside model_validate itself.
            return None


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
