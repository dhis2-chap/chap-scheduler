"""Retry policy used by `chap_client.base.ChapClientBase`.

Idempotent methods (GET / HEAD) retry on transient transport errors
and 5xx responses; POST is never retried because chap's mutating
endpoints (``run_prediction_setup``, ``create_evaluation``, ...) are
not idempotent and a retry on a connection blip would risk a duplicate.
"""

import httpx

from chap_client.errors import ChapHttpError

# Methods we'll retry. POST is non-idempotent for chap's mutating endpoints,
# so it stays a single-shot.
RETRYABLE_METHODS = frozenset({"GET", "HEAD"})

# httpx exception classes that indicate a transient transport-layer failure --
# the kind a quick retry typically resolves. ConnectError is the most common
# (connection refused / DNS hiccup); ReadError fires on connection drops
# mid-read; RemoteProtocolError on malformed framing.
RETRYABLE_HTTPX_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def is_retryable(exc: BaseException) -> bool:
    """Decide whether ``exc`` warrants a retry of the same request."""
    if isinstance(exc, RETRYABLE_HTTPX_EXCEPTIONS):
        return True
    if isinstance(exc, ChapHttpError) and exc.status >= 500:
        return True
    return False
