"""Pydantic models for chap responses returned via DHIS2.

Field names match chap's camelCase JSON via ``alias=`` while the Python
attributes stay snake_case. ``extra="ignore"`` so unknown fields chap may
add later don't break us — we only model what we actively use.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

_ALLOW_ALIAS = ConfigDict(extra="ignore", populate_by_name=True)


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
