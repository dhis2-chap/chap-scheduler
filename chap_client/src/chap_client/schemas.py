"""Pydantic models for chap requests and responses.

Field names match chap's camelCase JSON via ``alias=`` while the Python
attributes stay snake_case. ``extra="ignore"`` so unknown fields chap may
add later don't break us — we only model what we actively use.

When sending payloads back to chap (e.g. ``ChapRunPredictionSetupRequest``,
``ChapMakeEvaluationRequest``), dump with ``model_dump(by_alias=True)``
so the JSON keys match what chap expects.
"""

from datetime import datetime
from typing import Any, Literal

from geojson_pydantic import Feature, FeatureCollection
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Used for chap response models: snake_case attributes, camelCase aliases.
# `extra="ignore"` so chap can grow new response fields without breaking parsing.
_ALLOW_ALIAS = ConfigDict(extra="ignore", populate_by_name=True)

# Used for request models that include fields starting with ``model_``
# (Pydantic reserves that namespace by default, which would shadow ``modelId``).
_ALLOW_ALIAS_MODEL_NS = ConfigDict(
    extra="ignore",
    populate_by_name=True,
    protected_namespaces=(),
)

# Used for *mutating* request bodies. `extra="forbid"` rejects typo'd fields
# at validation time instead of letting them silently fall through to chap's
# defaults -- chap's own extra="ignore" stance means a mistyped `nPriods` is
# accepted on the wire and produces a job with the default value, with no
# error to the caller. See CHAP_CORE_ISSUES.md finding #16.
_FORBID_ALIAS_MODEL_NS = ConfigDict(
    extra="forbid",
    populate_by_name=True,
    protected_namespaces=(),
)


# --- system info -----------------------------------------------------------


class ChapSystemInfo(BaseModel):
    """Payload returned by ``GET <chap>/system/info``.

    The ``revision`` field that chap occasionally returns is intentionally
    omitted — it is almost never set in practice.
    """

    model_config = ConfigDict(extra="ignore")

    chap_core_version: str = Field(description="chap-core release version.")
    python_version: str = Field(description="Python interpreter version on the chap host.")
    server_date: datetime = Field(description="Server clock at the time of the request.")
    server_time_zone_id: str = Field(description="IANA timezone of the chap host.")


# --- configured models -----------------------------------------------------


class ChapDataSource(BaseModel):
    """A covariate ↔ DHIS2 data-element mapping inside a configured model."""

    model_config = _ALLOW_ALIAS

    covariate: str
    data_element_id: str = Field(alias="dataElementId")


class ChapModelTemplate(BaseModel):
    """The chap model template a configured model is based on.

    Only the fields we actively use or surface in logs are modelled — the
    rest (URLs, archived flags, hpoSearchSpace, etc.) are left to ``extra``.

    ``id`` is optional because the *embedded* form (under
    ``ChapConfiguredModel.modelTemplate``) sometimes omits it; the
    standalone form returned by ``GET /v1/crud/model-templates`` always
    has it. Callers that need a guaranteed id should look it up via
    `list_model_templates()`.
    """

    model_config = _ALLOW_ALIAS

    id: int | None = None
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


class ChapQuantileTarget(BaseModel):
    """One quantile -> DHIS2 data-element mapping on a prediction setup.

    Pushed by the scheduler when forecasts are written back to DHIS2:
    e.g. ``{quantile: "median", data_element_id: "DE_MED"}``. Not consumed
    by chap-core itself.
    """

    model_config = _ALLOW_ALIAS

    quantile: str
    data_element_id: str = Field(alias="dataElementId")


class ChapPredictionSetup(BaseModel):
    """One row from ``GET .../v1/crud/prediction-setups``.

    Carries everything we need to construct a DHIS2 analytics query: the
    DHIS2 data elements (per covariate), the org units, and the period
    range (``start_period`` -> present, in ``period_type`` granularity).
    Plus a snapshot of the parent backtest + the cron/quantile-target
    fields used by the scheduler (not consumed by chap-core).

    Replaces ``ChapConfiguredModelWithDataSource`` after chap-core PR #354.
    """

    model_config = _ALLOW_ALIAS

    id: int
    name: str
    backtest_id: int = Field(alias="backtestId")
    configured_model: ChapConfiguredModel = Field(alias="configuredModel")
    start_period: str = Field(alias="startPeriod")
    org_units: list[str] = Field(alias="orgUnits")
    covariate_sources: list[ChapDataSource] = Field(alias="covariateSources")
    period_type: str = Field(alias="periodType")
    schedule_cron_expression: str | None = Field(default=None, alias="scheduleCronExpression")
    schedule_enabled: bool = Field(default=False, alias="scheduleEnabled")
    quantile_targets: list[ChapQuantileTarget] = Field(default_factory=list, alias="quantileTargets")


# --- run-prediction request envelope ---------------------------------------
# Shared building blocks for the run-prediction-setup request and any
# evaluation/backtest submission body.


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


class ChapRunPredictionSetupRequest(BaseModel):
    """Body for ``POST /v1/crud/prediction-setups/{id}/run``.

    The setup id rides in the URL path (not the body). chap-core derives
    the configured model from the setup; the body only carries the input
    data + geojson + horizon.

    ``type`` is accepted but server-normalized to ``"prediction"``; we
    still send the field for parity with the existing flow's request
    construction. Extra fields are **forbidden** so legacy keys
    (``dataSources``, ``dataToBeFetched``, ``configuredModelWithDataSourceId``)
    fail loud rather than getting silently dropped. See `CHAP_CORE_ISSUES.md`
    finding #16.
    """

    model_config = _FORBID_ALIAS_MODEL_NS

    name: str = Field(min_length=1)
    geojson: FeatureCollection[Feature[Any, dict[str, Any]]]
    provided_data: list[ChapObservation] = Field(alias="providedData")
    n_periods: int = Field(alias="nPeriods", default=3, gt=0)
    type: Literal["forecasting", "backtesting"] | None = "forecasting"


# --- job + prediction result -----------------------------------------------


class ChapJobResponse(BaseModel):
    """Sync envelope returned by any chap endpoint that queues a job.

    Same shape across ``POST /v1/crud/prediction-setups/{id}/run``,
    ``POST /v1/analytics/create-backtest``, and the other job-queuing
    routes -- just the celery task id, the rest of the response lands
    later under ``GET /v1/jobs/{id}``.
    """

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


# --- model registry / configured-model CRUD -------------------------------


class ChapFeature(BaseModel):
    """A named feature reference -- a model's target or one of its covariates.

    chap's ``ModelSpecRead`` carries each feature as a small object
    (``{displayName, description, name}``) rather than a bare string.
    The OpenAPI schema currently types these as ``string`` -- the wire
    truth is the object form, so trust the wire.
    """

    model_config = _ALLOW_ALIAS

    name: str
    display_name: str | None = Field(default=None, alias="displayName")
    description: str | None = None


class ChapModelSpec(BaseModel):
    """Read shape returned by ``/v1/crud/models`` and ``/v1/crud/configured-models``.

    chap's own naming is somewhat overloaded (the same ``ModelSpecRead``
    schema describes both lists). We model the fields callers reach for
    today; chap may add or change others over time -- ``extra="ignore"``
    keeps unknown fields from breaking parsing.
    """

    model_config = _ALLOW_ALIAS

    id: int
    name: str
    target: ChapFeature
    covariates: list[ChapFeature] = Field(default_factory=list)
    display_name: str | None = Field(default=None, alias="displayName")
    description: str | None = None
    supported_period_type: str | None = Field(default=None, alias="supportedPeriodType")
    archived: bool = False
    uses_chapkit: bool = Field(default=False, alias="usesChapkit")
    user_option_values: dict[str, Any] = Field(default_factory=dict, alias="userOptionValues")
    additional_continuous_covariates: list[str] = Field(default_factory=list, alias="additionalContinuousCovariates")


class ChapConfiguredModelCreate(BaseModel):
    """Request body for ``POST /v1/crud/configured-models``.

    `chap_client.endpoints.models.ModelsEndpoints.create_configured_model`
    preflights ``model_template_id`` against `list_model_templates`
    by default (``validate=True``) so a wrong-id-space mistake
    surfaces synchronously instead of as chap's leaky 500 with an
    AssertionError in the body. See `CHAP_CORE_ISSUES.md` finding #3.

    Extra fields are forbidden; ``user_option_values`` defaults to
    ``{}`` so chap doesn't crash with the "None is not of type
    'object'" error documented as `CHAP_CORE_ISSUES.md` finding #10.
    """

    model_config = _FORBID_ALIAS_MODEL_NS

    name: str = Field(min_length=1)
    model_template_id: int = Field(alias="modelTemplateId", gt=0)
    user_option_values: dict[str, Any] = Field(default_factory=dict, alias="userOptionValues")
    additional_continuous_covariates: list[str] = Field(default_factory=list, alias="additionalContinuousCovariates")


class ChapConfiguredModelDB(BaseModel):
    """Response shape from ``POST /v1/crud/configured-models``.

    Smaller than `ChapModelSpec` -- this is the row chap stored,
    not the merged read view. ``modelTemplateId`` is exposed here.
    """

    model_config = _ALLOW_ALIAS_MODEL_NS

    id: int
    name: str
    model_template_id: int = Field(alias="modelTemplateId")
    archived: bool = False
    uses_chapkit: bool = Field(default=False, alias="usesChapkit")
    user_option_values: dict[str, Any] = Field(default_factory=dict, alias="userOptionValues")
    additional_continuous_covariates: list[str] = Field(default_factory=list, alias="additionalContinuousCovariates")


# --- datasets --------------------------------------------------------------


class ChapDataset(BaseModel):
    """A dataset chap stores: org units + period range + data sources.

    Datasets are tagged with a ``type`` (``evaluation`` for
    evaluations / backtests, ``prediction`` for predictions) so
    callers picking a dataset to evaluate against know which ones are
    eligible.
    """

    model_config = _ALLOW_ALIAS

    id: int
    name: str
    type: str
    period_type: str = Field(alias="periodType")
    first_period: str = Field(alias="firstPeriod")
    last_period: str = Field(alias="lastPeriod")
    org_units: list[str] = Field(default_factory=list, alias="orgUnits")
    covariates: list[str] = Field(default_factory=list)
    data_sources: list[ChapDataSource] = Field(default_factory=list, alias="dataSources")
    created: datetime | None = None


# --- evaluations ----------------------------------------------------------
#
# chap-core's REST URLs use "backtest" (e.g. /v1/crud/backtests,
# /v1/analytics/create-backtest); the chap UI surfaces the same
# concept as "Evaluation". chap_client's public types use the UI
# terminology so callers reading the chap UI see the same words in
# their code. The wire URLs are unchanged.


class ChapMakeEvaluationRequest(BaseModel):
    """Body for ``POST /v1/analytics/create-backtest`` (UI: "Create Evaluation").

    Note: ``model_id`` is the configured-model **name** (a string),
    not the integer id from ``/v1/crud/configured-models``. chap's
    OpenAPI types it as ``string`` -- confusing but consistent with
    what the API actually accepts. chap_client validates this string
    against the live configured-model list in
    `EvaluationsEndpoints.create_evaluation` (preflight, can be
    disabled with ``validate=False``); see `CHAP_CORE_ISSUES.md`
    finding #5.

    Extra fields are **forbidden** so a mistyped key (``nPriods``)
    errors at validation rather than silently falling through to
    chap's default; numeric fields are bounded to ``> 0``. See
    `CHAP_CORE_ISSUES.md` findings #14-#16.
    """

    model_config = _FORBID_ALIAS_MODEL_NS

    name: str = Field(min_length=1)
    model_id: str = Field(alias="modelId", min_length=1)
    dataset_id: int = Field(alias="datasetId", gt=0)
    n_periods: int | None = Field(default=None, alias="nPeriods", gt=0)
    n_splits: int | None = Field(default=None, alias="nSplits", gt=0)
    stride: int | None = Field(default=None, gt=0)


class ChapEvaluationRead(BaseModel):
    """Read shape for ``GET /v1/crud/backtests`` / ``/{id}/info`` (UI: "Evaluation").

    Once an evaluation finishes, ``aggregate_metrics`` carries the
    summary metrics chap computed (CRPS, MAE, RMSE, coverage, etc.)
    -- this is the "evaluation result" most callers want.
    """

    model_config = _ALLOW_ALIAS_MODEL_NS

    id: int
    name: str | None = None
    dataset_id: int = Field(alias="datasetId")
    model_id: str = Field(alias="modelId")
    model_template_version: str | None = Field(default=None, alias="modelTemplateVersion")
    org_units: list[str] = Field(default_factory=list, alias="orgUnits")
    split_periods: list[str] = Field(default_factory=list, alias="splitPeriods")
    aggregate_metrics: dict[str, float] = Field(default_factory=dict, alias="aggregateMetrics")
    created: datetime | None = None


class ChapEvaluationEntry(BaseModel):
    """One predicted value from ``GET /v1/analytics/evaluation-entry``.

    Looks like `ChapPredictionEntry` plus a ``split_period`` --
    evaluations run multiple splits per dataset, and each entry knows
    which split produced it.
    """

    model_config = _ALLOW_ALIAS

    org_unit: str = Field(alias="orgUnit")
    period: str
    quantile: float
    value: float
    split_period: str = Field(alias="splitPeriod")


# --- structured errors -----------------------------------------------------


class ChapRejection(BaseModel):
    """One rejected ``(org_unit, feature_name)`` cell in a chap 400 response."""

    model_config = _ALLOW_ALIAS

    reason: str
    org_unit: str = Field(alias="orgUnit")
    feature_name: str = Field(alias="featureName")
    time_periods: list[str] = Field(alias="timePeriods", default_factory=list)


class ChapMissingValuesDetail(BaseModel):
    """Structured ``detail`` body chap returns when input validation fails.

    Today chap returns this shape inside an HTTP 400 body (FastAPI's
    ``{"detail": {...}}`` envelope, which `from_error_body()` peels off).
    Upstream chap has a pending PR to switch this to a 200 response
    with a similar shape; once that lands we'll add a parallel parser
    for the success body and treat partially-rejected predictions as
    ``status="succeeded"`` with a ``rejection_detail`` set.

    Example payload (the inner ``detail`` dict, after the FastAPI envelope
    is peeled off):

    ```json
    {
      "message": "All regions rejected due to missing values",
      "imported_count": 0,
      "rejected": [
        {"reason": "...", "orgUnit": "...", "featureName": "rainfall", "timePeriods": ["202510", "202511"]}
      ]
    }
    ```
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
