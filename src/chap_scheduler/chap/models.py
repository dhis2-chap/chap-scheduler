"""Pydantic models for chap responses returned via DHIS2."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
