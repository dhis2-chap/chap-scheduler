"""Tests for ``chap_client.errors``."""

from chap_client import ChapHttpError as ChapHttpErrorTopLevel
from chap_client.errors import ChapHttpError


def test_chaphttperror_captures_request_shape_and_detail() -> None:
    err = ChapHttpError(
        method="POST",
        path="/v1/analytics/make-prediction-with-data-source",
        status=400,
        detail={"detail": {"message": "missing values", "rejected": []}},
    )
    assert err.method == "POST"
    assert err.path == "/v1/analytics/make-prediction-with-data-source"
    assert err.status == 400
    assert err.detail == {"detail": {"message": "missing values", "rejected": []}}


def test_chaphttperror_str_includes_method_path_status_and_detail() -> None:
    err = ChapHttpError(method="GET", path="/v1/jobs/abc", status=503, detail="upstream temporarily unavailable")
    s = str(err)
    assert "GET" in s
    assert "/v1/jobs/abc" in s
    assert "503" in s
    assert "upstream temporarily unavailable" in s


def test_chaphttperror_is_exposed_at_package_top_level() -> None:
    """``from chap_client import ChapHttpError`` works (package re-export)."""
    assert ChapHttpErrorTopLevel is ChapHttpError
