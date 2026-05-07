"""HTTP client for the chap routes exposed by DHIS2.

We don't use ``dhis2-client`` for chap calls because chap is *not* part of
DHIS2's native API -- it's a separate service that DHIS2 proxies via custom
routes (``/api/routes/chap/run/*``). dhis2-client masks chap's response
bodies (we'd see ``DHIS2HTTPError: UNKNOWN`` instead of chap's actual error
messages); plain ``httpx`` lets us surface the response verbatim.

Native DHIS2 endpoints (analytics, organisationUnits, ...) still go through
``dhis2-client``.
"""

from typing import Any

import httpx

from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.chap.models import (
    ChapConfiguredModelWithDataSource,
    ChapJobResponse,
    ChapMakePredictionRequest,
    ChapPredictionResult,
    ChapSystemInfo,
)

_CHAP_ROUTE_PREFIX = "/api/routes/chap/run"
_DEFAULT_TIMEOUT = 60.0


class ChapHttpError(Exception):
    """Raised when a chap call returns a non-2xx response.

    Carries the chap response body (parsed JSON when possible, otherwise
    the raw text) so the caller can surface it in logs / the run report.
    """

    def __init__(self, method: str, path: str, status: int, detail: Any) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail
        super().__init__(f"chap {method} {path} -> HTTP {status}: {detail}")


class ChapClient:
    """Calls chap endpoints living under DHIS2's ``/api/routes/chap/run/*``.

    Cheap to construct -- holds only the credentials block and a timeout.
    The methods that map onto specific chap endpoints return parsed Pydantic
    models; the lower-level :meth:`get` / :meth:`post` are exposed for cases
    we haven't typed yet (e.g. job logs).
    """

    def __init__(self, credentials: Dhis2Credentials, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._credentials = credentials
        self._timeout = timeout

    # -- url / auth helpers -------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._credentials.base_url

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + _CHAP_ROUTE_PREFIX + path

    def _auth(self) -> tuple[str, str]:
        return self._credentials.username, self._credentials.password.get_secret_value()

    # -- low-level HTTP -----------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send an HTTP request to ``path`` (relative to the chap route prefix)."""
        response = httpx.request(
            method,
            self._url(path),
            auth=self._auth(),
            json=json,
            params=params,
            timeout=self._timeout,
        )
        if not response.is_success:
            try:
                detail: Any = response.json()
            except ValueError:
                detail = response.text
            raise ChapHttpError(method=method, path=path, status=response.status_code, detail=detail)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, *, json: Any = None) -> Any:
        return self.request("POST", path, json=json)

    # -- typed endpoint methods --------------------------------------------

    def system_info(self) -> ChapSystemInfo:
        return ChapSystemInfo.model_validate(self.get("/system/info"))

    def configured_models(self) -> list[ChapConfiguredModelWithDataSource]:
        raw = self.get("/v1/crud/configured-models-with-data-source")
        return [ChapConfiguredModelWithDataSource.model_validate(item) for item in raw]

    def submit_prediction(self, request: ChapMakePredictionRequest) -> ChapJobResponse:
        body = request.model_dump(by_alias=True, mode="json")
        return ChapJobResponse.model_validate(self.post("/v1/analytics/make-prediction", json=body))

    def job_status(self, job_id: str) -> str:
        """Poll a single chap job; returns the bare status string."""
        return str(self.get(f"/v1/jobs/{job_id}")).strip()

    def prediction_result(self, job_id: str) -> ChapPredictionResult:
        return ChapPredictionResult.model_validate(self.get(f"/v1/jobs/{job_id}/prediction_result"))
