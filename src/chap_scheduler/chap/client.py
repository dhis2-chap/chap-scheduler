"""HTTP client for the chap routes exposed by DHIS2.

We don't use ``dhis2-client`` for chap calls because chap is *not* part of
DHIS2's native API -- it's a separate service that DHIS2 proxies via custom
routes (``/api/routes/chap/run/*``). dhis2-client masks chap's response
bodies (we'd see ``DHIS2HTTPError: UNKNOWN`` instead of chap's actual error
messages); plain ``httpx`` lets us surface the response verbatim.

Native DHIS2 endpoints (analytics, organisationUnits, ...) still go through
``dhis2-client``.

Connection pooling: a single :class:`httpx.Client` is held for the
lifetime of the :class:`ChapClient` instance, so polling loops (the chap
job-status loop in particular) reuse the underlying TCP connection
instead of opening a new one per call. Use it as a context manager
(``with ChapClient(...) as client:``) so the pool is closed cleanly.
For one-shot calls, ``ChapClient(...).system_info()`` still works -- the
client is closed when the instance is garbage-collected, just with an
httpx ``ResourceWarning`` if the GC is delayed.

Retries: idempotent methods (GET / HEAD) retry on transient transport
errors and 5xx responses, with exponential backoff + jitter, capped at
``max_attempts`` (default 3). POST is never retried -- chap's
``submit_prediction`` is not idempotent and a retry could create duplicate
predictions. Disable retries for tests by passing ``max_attempts=1``.
"""

from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.chap.models import (
    ChapConfiguredModelWithDataSource,
    ChapJobDescription,
    ChapJobResponse,
    ChapMakePredictionRequest,
    ChapPredictionEntry,
    ChapSystemInfo,
)

_CHAP_ROUTE_PREFIX = "/api/routes/chap/run"
_DEFAULT_TIMEOUT = 60.0

# Methods we'll retry. POST is non-idempotent for chap's submit_prediction
# (a retry on a connection error would create a second prediction job), so
# it stays a single-shot.
_RETRYABLE_METHODS = frozenset({"GET", "HEAD"})

# httpx exception classes that indicate a transient transport-layer failure --
# the kind a quick retry typically resolves. ConnectError is the most common
# (connection refused / DNS hiccup); ReadError fires on connection drops
# mid-read; RemoteProtocolError on malformed framing.
_RETRYABLE_HTTPX_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


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


def _is_retryable(exc: BaseException) -> bool:
    """Decide whether ``exc`` warrants a retry of the same request."""
    if isinstance(exc, _RETRYABLE_HTTPX_EXCEPTIONS):
        return True
    if isinstance(exc, ChapHttpError) and exc.status >= 500:
        return True
    return False


class ChapClient:
    """Calls chap endpoints living under DHIS2's ``/api/routes/chap/run/*``.

    Holds a single :class:`httpx.Client` for connection pooling. The methods
    that map onto specific chap endpoints return parsed Pydantic models;
    the lower-level :meth:`get` / :meth:`post` are exposed for cases we
    haven't typed yet (e.g. job logs).

    Use as a context manager so the underlying connection pool is closed:

    .. code-block:: python

        with ChapClient(credentials) as client:
            client.system_info()
            client.configured_models()
    """

    def __init__(
        self,
        credentials: Dhis2Credentials,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 3,
        retry_min_wait: float = 0.5,
        retry_max_wait: float = 8.0,
    ) -> None:
        """Create a chap HTTP client.

        Args:
            credentials: DHIS2 credentials block (base URL + auth) for the
                instance hosting the chap routes.
            timeout: Per-request timeout in seconds.
            transport: Optional httpx transport, primarily for tests
                (``httpx.MockTransport`` etc.). ``None`` uses the default.
            max_attempts: Total attempts (including the first) for retryable
                requests. Set to ``1`` to disable retries (the default in
                tests). Production default is ``3``.
            retry_min_wait: Lower bound of the exponential-backoff wait, in
                seconds. The first retry waits at least this long.
            retry_max_wait: Upper bound of the exponential-backoff wait, in
                seconds. Caps the wait between attempts.
        """
        self._credentials = credentials
        self._timeout = timeout
        self._transport = transport
        self._max_attempts = max(1, max_attempts)
        self._retry_min_wait = retry_min_wait
        self._retry_max_wait = retry_max_wait
        self._client: httpx.Client | None = None

    # -- lifecycle ----------------------------------------------------------

    def _http(self) -> httpx.Client:
        """Return the lazy-initialised, instance-scoped httpx.Client."""
        if self._client is None:
            self._client = httpx.Client(transport=self._transport, timeout=self._timeout)
        return self._client

    def close(self) -> None:
        """Close the underlying httpx connection pool, if open."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "ChapClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

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
        """Send an HTTP request to ``path`` (relative to the chap route prefix).

        Idempotent methods (GET / HEAD) retry on transient transport errors
        and 5xx responses; POST is never retried (see module docstring).
        """
        if method.upper() in _RETRYABLE_METHODS and self._max_attempts > 1:
            retryer = Retrying(
                retry=retry_if_exception(_is_retryable),
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential_jitter(
                    initial=self._retry_min_wait,
                    max=self._retry_max_wait,
                ),
                reraise=True,
            )
            return retryer(self._do_request, method, path, json=json, params=params)
        return self._do_request(method, path, json=json, params=params)

    def _do_request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = self._http().request(
            method,
            self._url(path),
            auth=self._auth(),
            json=json,
            params=params,
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
        return ChapJobResponse.model_validate(self.post("/v1/analytics/make-prediction-with-data-source", json=body))

    def job_status(self, job_id: str) -> str:
        """Poll a single chap job; returns the bare status string.

        ``GET /v1/jobs/{id}`` returns a quoted-JSON string -- e.g. the bytes
        ``"SUCCESS"`` (length 9, including the quotes) -- which httpx parses
        back to a Python ``str``. We strip whitespace defensively in case
        chap ever surrounds the value with padding.
        """
        body = self.get(f"/v1/jobs/{job_id}")
        if not isinstance(body, str):
            raise ChapHttpError(
                method="GET",
                path=f"/v1/jobs/{job_id}",
                status=200,
                detail=f"expected string job-status response, got {type(body).__name__}: {body!r}",
            )
        return body.strip()

    def job_description(self, job_id: str) -> ChapJobDescription | None:
        """Find a single job's full description (incl. ``result``) by id.

        ``GET /v1/jobs/{id}`` returns only the status string, so to read the
        ``result`` field (which holds the prediction or backtest id once
        the job has succeeded) we list ``GET /v1/jobs`` and filter
        client-side. Cheap as long as the job table stays small.
        """
        for entry in self.get("/v1/jobs"):
            if entry.get("id") == job_id:
                return ChapJobDescription.model_validate(entry)
        return None

    def prediction_entries(
        self,
        prediction_id: int,
        quantiles: list[float],
    ) -> list[ChapPredictionEntry]:
        """Fetch the actual predicted values for a prediction at the given quantiles.

        Uses ``GET /v1/analytics/prediction-entry/{id}?quantiles=...`` -- the
        same endpoint the chap-frontend uses. Note: chap requires at least
        one quantile, otherwise it returns 422.
        """
        if not quantiles:
            raise ValueError("quantiles must contain at least one value")
        raw = self.get(
            f"/v1/analytics/prediction-entry/{prediction_id}",
            params={"quantiles": quantiles},
        )
        return [ChapPredictionEntry.model_validate(item) for item in raw]
