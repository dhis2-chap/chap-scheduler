"""Base class for :class:`chap_client.ChapClient` -- HTTP plumbing only.

Holds the lazily-initialised :class:`httpx.Client`, basic-auth /
route-prefix wiring, the retry policy, and the typed-error mapping.
The endpoint mixins under :mod:`chap_client._endpoints` inherit from
:class:`ChapClientBase` so each can call ``self.get`` / ``self.post``
without knowing anything about the HTTP layer.
"""

from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from chap_client._retries import RETRYABLE_METHODS, is_retryable
from chap_client.errors import ChapHttpError

# httpx-compatible auth shapes the client accepts.
ChapAuth = httpx.Auth | tuple[str, str]

_DEFAULT_TIMEOUT = 60.0


class ChapClientBase:
    """HTTP plumbing for :class:`chap_client.ChapClient`.

    Endpoint mixins inherit from this class so they can call
    ``self.get`` / ``self.post`` / ``self.request`` with the same
    signatures whether the client was built with primitives directly
    or wrapped via a higher-level factory.
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

    def __enter__(self) -> Self:
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
        if method.upper() in RETRYABLE_METHODS and self._max_attempts > 1:
            retryer = Retrying(
                retry=retry_if_exception(is_retryable),
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential_jitter(initial=self._retry_min_wait, max=self._retry_max_wait),
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
