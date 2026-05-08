"""System / health endpoints."""

from chap_client._client_base import ChapClientBase
from chap_client.schemas import ChapSystemInfo


class SystemEndpoints(ChapClientBase):
    """Methods for ``/system/*``."""

    def system_info(self) -> ChapSystemInfo:
        """Fetch system metadata (``GET /system/info``).

        Returns chap-core version, server time, and timezone -- useful
        as a connectivity probe and for surfacing in run reports.
        """
        return ChapSystemInfo.model_validate(self.get("/system/info"))
