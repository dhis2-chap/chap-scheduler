"""Exceptions raised by the chap HTTP client."""

from typing import Any


class ChapHttpError(Exception):
    """Raised when a chap call returns a non-2xx response.

    Carries the chap response body (parsed JSON when possible, otherwise
    the raw text) so the caller can surface it in logs / the run report.
    """

    def __init__(self, method: str, path: str, status: int, detail: Any) -> None:
        """Capture the request shape and the chap response body."""
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail
        super().__init__(f"chap {method} {path} -> HTTP {status}: {detail}")
