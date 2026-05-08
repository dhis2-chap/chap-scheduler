"""HTTP client for the chap REST API.

Constructed with primitives (``base_url``, ``auth``, optional
``route_prefix``) so this package stays free of any opinions about
where the credentials come from. Two common shapes:

- **Via the DHIS2 chap-route proxy** (used by chap-scheduler):
  pass ``route_prefix="/api/routes/chap/run"`` and DHIS2 basic auth.
- **Direct against chap**: pass ``route_prefix=""`` (the default) and
  whatever auth chap's deployment expects.

Connection pooling: a single :class:`httpx.Client` is held for the
lifetime of the :class:`ChapClient` instance, so polling loops reuse
the underlying TCP connection. Use as a context manager so the pool is
closed cleanly.

Retries: idempotent methods (GET / HEAD) retry on transient transport
errors and 5xx responses, with exponential backoff + jitter, capped at
``max_attempts`` (default 3). POST is never retried -- chap's
``submit_prediction`` is not idempotent and a retry could create
duplicate predictions. Disable retries for tests by passing
``max_attempts=1``.
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

from chap_client.errors import ChapHttpError
from chap_client.models import (
    ChapConfiguredModelCreate,
    ChapConfiguredModelDB,
    ChapConfiguredModelWithDataSource,
    ChapJobDescription,
    ChapJobResponse,
    ChapMakePredictionRequest,
    ChapModelSpec,
    ChapPredictionEntry,
    ChapSystemInfo,
)

# httpx-compatible auth shapes the client accepts.
ChapAuth = httpx.Auth | tuple[str, str]

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


def _is_retryable(exc: BaseException) -> bool:
    """Decide whether ``exc`` warrants a retry of the same request."""
    if isinstance(exc, _RETRYABLE_HTTPX_EXCEPTIONS):
        return True
    if isinstance(exc, ChapHttpError) and exc.status >= 500:
        return True
    return False


class ChapClient:
    """Calls chap REST endpoints.

    The methods that map onto specific chap endpoints return parsed
    Pydantic models; the lower-level :meth:`get` / :meth:`post` are
    exposed for cases we haven't typed yet.

    Use as a context manager so the underlying connection pool is
    closed:

    .. code-block:: python

        with ChapClient(base_url=url, auth=(user, password), route_prefix="/api/routes/chap/run") as client:
            client.system_info()
    """

    def __init__(
        self,
        base_url: str,
        auth: ChapAuth | None = None,
        *,
        route_prefix: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 3,
        retry_min_wait: float = 0.5,
        retry_max_wait: float = 8.0,
    ) -> None:
        """Create a chap HTTP client.

        Args:
            base_url: Origin of the chap-serving host.
            auth: httpx-compatible auth (an ``httpx.Auth`` or a
                ``(username, password)`` tuple for basic auth).
            route_prefix: Path prefix prepended before the chap endpoint
                paths -- empty for direct chap, or
                ``"/api/routes/chap/run"`` when reaching chap through
                DHIS2's proxy routes.
            timeout: Per-request timeout in seconds.
            transport: Optional httpx transport, primarily for tests
                (``httpx.MockTransport`` etc.). ``None`` uses the default.
            max_attempts: Total attempts (including the first) for
                retryable requests. Set to ``1`` to disable retries.
            retry_min_wait: Lower bound of the exponential-backoff wait,
                in seconds.
            retry_max_wait: Upper bound of the exponential-backoff wait,
                in seconds.
        """
        self._base_url = base_url
        self._auth = auth
        self._route_prefix = route_prefix
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
        return self._base_url

    def _url(self, path: str) -> str:
        return self._base_url.rstrip("/") + self._route_prefix + path

    # -- low-level HTTP -----------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send an HTTP request to ``path`` (relative to ``route_prefix``).

        Idempotent methods (GET / HEAD) retry on transient transport
        errors and 5xx responses; POST is never retried.
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
            auth=self._auth,
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

    def list_models(self) -> list[ChapModelSpec]:
        """List the model registry (``GET /v1/crud/models``)."""
        raw = self.get("/v1/crud/models")
        return [ChapModelSpec.model_validate(item) for item in raw]

    def list_configured_models(self) -> list[ChapModelSpec]:
        """List configured models (``GET /v1/crud/configured-models``).

        Note: chap returns the same ``ModelSpecRead`` shape here as for
        ``/v1/crud/models`` -- the API doesn't expose a tighter type for
        configured-models specifically.
        """
        raw = self.get("/v1/crud/configured-models")
        return [ChapModelSpec.model_validate(item) for item in raw]

    def create_configured_model(self, spec: ChapConfiguredModelCreate) -> ChapConfiguredModelDB:
        """Create a configured model (``POST /v1/crud/configured-models``).

        Returns chap's stored row (``ConfiguredModelDB`` upstream),
        which exposes ``modelTemplateId`` and the chosen option values
        rather than the merged read view.
        """
        body = spec.model_dump(by_alias=True, mode="json")
        return ChapConfiguredModelDB.model_validate(self.post("/v1/crud/configured-models", json=body))

    def configured_model_with_data_source(self, id: int) -> ChapConfiguredModelWithDataSource:
        """Fetch a single configured-model-with-data-source by id.

        Note: chap returns a richer ``…ReadWithPredictions`` shape on
        this endpoint (it embeds the prediction list); we only model the
        common fields and silently ignore the rest via
        ``extra="ignore"``. Add ``predictions`` to
        :class:`ChapConfiguredModelWithDataSource` if/when callers need it.
        """
        return ChapConfiguredModelWithDataSource.model_validate(
            self.get(f"/v1/crud/configured-models-with-data-source/{id}")
        )

    def create_configured_model_with_data_source_from_backtest(
        self,
        backtest_id: int,
    ) -> ChapConfiguredModelWithDataSource:
        """Create a configured-model-with-data-source row from a backtest.

        chap derives the configured-model body from the referenced
        backtest -- there is no request body. Marked **Experimental**
        upstream; the response shape may change without notice.
        """
        return ChapConfiguredModelWithDataSource.model_validate(
            self.post(f"/v1/crud/configured-models-with-data-source/from-backtest/{backtest_id}")
        )

    def submit_prediction(self, request: ChapMakePredictionRequest) -> ChapJobResponse:
        body = request.model_dump(by_alias=True, mode="json")
        return ChapJobResponse.model_validate(self.post("/v1/analytics/make-prediction-with-data-source", json=body))

    def job_status(self, job_id: str) -> str:
        """Poll a single chap job; returns the bare status string.

        ``GET /v1/jobs/{id}`` returns a quoted-JSON string -- e.g. the
        bytes ``"SUCCESS"`` (length 9, including the quotes) -- which
        httpx parses back to a Python ``str``. We strip whitespace
        defensively in case chap ever surrounds the value with padding.
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

        ``GET /v1/jobs/{id}`` returns only the status string, so to read
        the ``result`` field (which holds the prediction or backtest id
        once the job has succeeded) we list ``GET /v1/jobs`` and filter
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

        Uses ``GET /v1/analytics/prediction-entry/{id}?quantiles=...`` --
        the same endpoint the chap-frontend uses. Note: chap requires at
        least one quantile, otherwise it returns 422.
        """
        if not quantiles:
            raise ValueError("quantiles must contain at least one value")
        raw = self.get(
            f"/v1/analytics/prediction-entry/{prediction_id}",
            params={"quantiles": quantiles},
        )
        return [ChapPredictionEntry.model_validate(item) for item in raw]
