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
from pydantic import BaseModel, ConfigDict, Field

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
    """Body for ``POST /v1/analytics/make-prediction`` (mirrors the FE)."""

    model_config = _ALLOW_ALIAS_MODEL_NS

    name: str
    geojson: FeatureCollection[Feature[Any, dict[str, Any]]]
    provided_data: list[ChapObservation] = Field(alias="providedData")
    data_sources: list[ChapDataSource] = Field(alias="dataSources")
    data_to_be_fetched: list[ChapFetchRequest] = Field(alias="dataToBeFetched", default_factory=list)
    model_id: str = Field(alias="modelId")
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


class ChapPredictionValue(BaseModel):
    """One predicted value from ``GET /v1/jobs/{id}/prediction_result``."""

    model_config = _ALLOW_ALIAS

    org_unit: str = Field(alias="orgUnit")
    data_element: str = Field(alias="dataElement")
    period: str
    value: float


class ChapPredictionResult(BaseModel):
    """The full ``FullPredictionResponse`` payload."""

    model_config = _ALLOW_ALIAS

    disease_id: str = Field(alias="diseaseId")
    data_values: list[ChapPredictionValue] = Field(alias="dataValues")


# --- run-report (artifact summary) ------------------------------------------


class ModelRunEntry(BaseModel):
    """Per-configured-model outcome inside a flow run."""

    name: str
    template_name: str
    status: Literal["succeeded", "failed"] = "failed"
    step_failed: str | None = None
    error: str | None = None
    job_id: str | None = None
    analytics_rows: int | None = None
    org_units_covered: int | None = None
    periods_covered: int | None = None
    prediction_values: int | None = None


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
