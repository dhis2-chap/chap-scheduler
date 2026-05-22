"""Unit tests for ``ChapClient`` using ``httpx.MockTransport``.

Constructed with primitives (``base_url``, ``auth`` tuple, optional
``route_prefix``) -- the chap_client package itself has no opinion on
where the credentials come from; the chap-scheduler-specific
DHIS2-via-block factory is exercised separately by the scheduler's own
tests.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from geojson_pydantic import Feature, FeatureCollection

from chap_client import (
    ChapClient,
    ChapConfiguredModelCreate,
    ChapHttpError,
    ChapMakeEvaluationRequest,
    ChapObservation,
    ChapRunPredictionSetupRequest,
)

_TEST_BASE_URL = "http://chap.test"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> ChapClient:
    return ChapClient(base_url=_TEST_BASE_URL, transport=httpx.MockTransport(handler))


def _retrying_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_attempts: int = 3,
) -> ChapClient:
    """Test helper: ChapClient with retries enabled but ~zero backoff."""
    return ChapClient(
        base_url=_TEST_BASE_URL,
        transport=httpx.MockTransport(handler),
        max_attempts=max_attempts,
        retry_min_wait=0.0,
        retry_max_wait=0.001,
    )


# --- low-level request / error handling -------------------------------------


def test_request_forwards_basic_auth_when_tuple_supplied() -> None:
    seen: dict[str, str | None] = {"auth": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    client = ChapClient(
        base_url=_TEST_BASE_URL,
        auth=("alice", "hunter2"),
        transport=httpx.MockTransport(handler),
    )
    client.get("/anything")
    # Basic alice:hunter2 -> "Basic YWxpY2U6aHVudGVyMg=="
    assert seen["auth"] is not None
    assert seen["auth"].startswith("Basic ")


def test_request_no_auth_header_when_auth_is_none() -> None:
    seen: dict[str, str | None] = {"auth": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    _client(handler).get("/anything")
    assert seen["auth"] is None


def test_route_prefix_is_prepended_to_paths() -> None:
    seen: dict[str, str] = {"path": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json="OK")

    ChapClient(
        base_url=_TEST_BASE_URL,
        route_prefix="/api/routes/chap/run",
        transport=httpx.MockTransport(handler),
    ).get("/v1/jobs/abc")
    assert seen["path"] == "/api/routes/chap/run/v1/jobs/abc"


def test_request_raises_chaphttperror_with_structured_json_detail() -> None:
    payload = {"detail": {"message": "All regions rejected", "imported_count": 0, "rejected": []}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=payload)

    client = _client(handler)
    with pytest.raises(ChapHttpError) as excinfo:
        client.post("/v1/analytics/make-prediction", json={"x": 1})
    err = excinfo.value
    assert err.status == 400
    assert err.method == "POST"
    assert err.path == "/v1/analytics/make-prediction"
    assert err.detail == payload


def test_request_raises_chaphttperror_with_text_body_when_not_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"<html>boom</html>", headers={"content-type": "text/html"})

    client = _client(handler)
    with pytest.raises(ChapHttpError) as excinfo:
        client.get("/anything")
    assert excinfo.value.status == 500
    assert excinfo.value.detail == "<html>boom</html>"


def test_request_returns_none_for_empty_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    assert _client(handler).get("/anything") is None


def test_request_returns_string_when_response_is_quoted_json_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'"SUCCESS"', headers={"content-type": "application/json"})

    assert _client(handler).get("/v1/jobs/abc") == "SUCCESS"


# --- typed endpoint: system_info -------------------------------------------


def test_system_info_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/system/info"
        return httpx.Response(
            200,
            json={
                "chap_core_version": "2.0.0.dev1",
                "python_version": "3.13.12",
                "server_date": "2026-05-07T11:38:23+00:00",
                "server_time_zone_id": "Etc/UTC",
            },
        )

    info = _client(handler).system_info()
    assert info.chap_core_version == "2.0.0.dev1"
    assert info.server_time_zone_id == "Etc/UTC"


# --- typed endpoint: prediction_setups -------------------------------------


def _prediction_setup_payload(id: int = 1, name: str = "test") -> dict[str, Any]:
    """Reusable fixture matching chap's `PredictionSetupRead` shape."""
    return {
        "id": id,
        "name": name,
        "backtestId": 7,
        "configuredModel": {
            "id": 12,
            "name": "chapkit-ewars-model",
            "additionalContinuousCovariates": ["rainfall"],
            "modelTemplate": {
                "name": "chapkit-ewars-model",
                "displayName": "CHAP-EWARS",
                "target": "disease_cases",
                "supportedPeriodType": "month",
                "requiredCovariates": ["population"],
            },
        },
        "startPeriod": "202301",
        "orgUnits": ["OU1"],
        "covariateSources": [{"covariate": "population", "dataElementId": "POP1"}],
        "periodType": "month",
        "scheduleEnabled": False,
        "quantileTargets": [],
    }


def test_prediction_setups_parses_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/crud/prediction-setups"
        return httpx.Response(200, json=[_prediction_setup_payload()])

    setups = _client(handler).list_prediction_setups()
    assert len(setups) == 1
    assert setups[0].name == "test"
    assert setups[0].configured_model.model_template.target == "disease_cases"
    assert setups[0].backtest_id == 7


def test_prediction_setup_fetches_by_id() -> None:
    """GET /v1/crud/prediction-setups/{id}."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/crud/prediction-setups/42"
        # chap returns the WithPredictions shape here; extra fields are ignored.
        payload = _prediction_setup_payload(id=42, name="rwanda-malaria")
        payload["created"] = "2026-05-08T10:00:00+00:00"
        payload["predictions"] = []
        return httpx.Response(200, json=payload)

    setup = _client(handler).get_prediction_setup(42)
    assert setup.id == 42
    assert setup.name == "rwanda-malaria"


def test_prediction_setup_propagates_404() -> None:
    """Unknown id -> ChapHttpError with status 404; not retried (4xx)."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404, json={"detail": "not found"})

    with pytest.raises(ChapHttpError) as excinfo:
        _retrying_client(handler).get_prediction_setup(999)
    assert excinfo.value.status == 404
    assert attempts == 1


def _feature(name: str) -> dict[str, str]:
    return {"name": name, "displayName": name.capitalize(), "description": f"{name} feature"}


def _model_spec_payload(id: int, name: str, target: str = "disease_cases") -> dict[str, Any]:
    """Reusable fixture matching chap's `ModelSpecRead` wire shape.

    Note: chap returns `target` and `covariates` as nested objects
    (``{name, displayName, description}``), not bare strings -- the
    OpenAPI spec is wrong about this, see chap_client/CHAP_SPEC_DRIFT.md.
    """
    return {
        "id": id,
        "name": name,
        "target": _feature(target),
        "covariates": [_feature("population"), _feature("rainfall")],
        "displayName": f"display-{name}",
        "supportedPeriodType": "month",
        "archived": False,
        "usesChapkit": True,
        "userOptionValues": {},
        "additionalContinuousCovariates": [],
    }


def test_list_models_parses_modelspecread_array() -> None:
    """``GET /v1/crud/models`` -> list[ChapModelSpec]."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/crud/models"
        return httpx.Response(
            200,
            json=[
                _model_spec_payload(id=1, name="chapkit-ewars-model"),
                _model_spec_payload(id=2, name="chapkit-rwanda-bym-model"),
            ],
        )

    models = _client(handler).list_models()
    assert len(models) == 2
    assert models[0].name == "chapkit-ewars-model"
    assert [c.name for c in models[0].covariates] == ["population", "rainfall"]
    assert models[0].target.name == "disease_cases"
    assert models[1].id == 2


def test_list_configured_models_parses_modelspecread_array() -> None:
    """``GET /v1/crud/configured-models`` returns the same shape as list_models."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/crud/configured-models"
        return httpx.Response(200, json=[_model_spec_payload(id=42, name="my-config")])

    configured = _client(handler).list_configured_models()
    assert len(configured) == 1
    assert configured[0].id == 42
    assert configured[0].name == "my-config"


def test_create_configured_model_posts_camelcase_body_and_parses_db_response() -> None:
    """``POST /v1/crud/configured-models`` -- camelCase wire body, ConfiguredModelDB response."""
    received_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/crud/configured-models"
        nonlocal received_body
        received_body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": 99,
                "name": "my-new-config",
                "modelTemplateId": 7,
                "archived": False,
                "usesChapkit": True,
                "userOptionValues": {"alpha": 0.5},
                "additionalContinuousCovariates": ["rainfall"],
            },
        )

    spec = ChapConfiguredModelCreate(
        name="my-new-config",
        modelTemplateId=7,
        userOptionValues={"alpha": 0.5},
        additionalContinuousCovariates=["rainfall"],
    )
    # validate=False -- this test exercises the POST body shape, not the
    # preflight (which gets its own tests below).
    created = _client(handler).create_configured_model(spec, validate=False)

    # Wire body uses camelCase keys (chap's API).
    assert received_body["name"] == "my-new-config"
    assert received_body["modelTemplateId"] == 7
    assert received_body["userOptionValues"] == {"alpha": 0.5}
    assert received_body["additionalContinuousCovariates"] == ["rainfall"]
    # Response parses into ChapConfiguredModelDB.
    assert created.id == 99
    assert created.model_template_id == 7
    assert created.user_option_values == {"alpha": 0.5}


def test_create_configured_model_does_not_retry_on_5xx() -> None:
    """POST is non-idempotent; transient 5xx aborts rather than risk a duplicate."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"detail": "unavailable"})

    spec = ChapConfiguredModelCreate(name="x", modelTemplateId=1)
    with pytest.raises(ChapHttpError) as excinfo:
        _retrying_client(handler).create_configured_model(spec, validate=False)
    assert excinfo.value.status == 503
    assert attempts == 1


# --- typed endpoint: run_prediction_setup ----------------------------------


def _build_prediction_request() -> ChapRunPredictionSetupRequest:
    return ChapRunPredictionSetupRequest(
        name="run-1",
        geojson=FeatureCollection[Feature[Any, dict[str, Any]]](type="FeatureCollection", features=[]),
        providedData=[ChapObservation(featureName="population", orgUnit="OU1", period="202301", value=1000.0)],
        nPeriods=3,
        type="forecasting",
    )


def test_run_prediction_setup_sends_camelcase_body_and_parses_job_response() -> None:
    received_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/crud/prediction-setups/5/run"
        nonlocal received_body
        received_body = json.loads(request.content)
        return httpx.Response(200, json={"id": "job-uuid-123"})

    client = _client(handler)
    job = client.run_prediction_setup(5, _build_prediction_request())
    assert job.id == "job-uuid-123"
    # camelCase keys -- the chap API expects these names.
    assert "providedData" in received_body
    assert "nPeriods" in received_body
    # Setup id rides in the URL path; not in the body.
    assert "configuredModelWithDataSourceId" not in received_body
    assert "dataSources" not in received_body
    assert "dataToBeFetched" not in received_body


# --- typed endpoint: job_status / job_description ---------------------------


def test_job_status_returns_status_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/jobs/abc-123"
        return httpx.Response(200, content=b'"SUCCESS"', headers={"content-type": "application/json"})

    assert _client(handler).job_status("abc-123") == "SUCCESS"


def test_job_description_finds_matching_job_in_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/jobs"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "other",
                    "type": "make_prediction",
                    "name": "x",
                    "status": "PENDING",
                    "start_time": None,
                    "end_time": None,
                    "result": None,
                },
                {
                    "id": "abc-123",
                    "type": "make_prediction",
                    "name": "test",
                    "status": "SUCCESS",
                    "start_time": "2026-05-07T10:00:00",
                    "end_time": "2026-05-07T10:01:00",
                    "result": "42",
                },
            ],
        )

    desc = _client(handler).job_description("abc-123")
    assert desc is not None
    assert desc.id == "abc-123"
    assert desc.status == "SUCCESS"
    assert desc.result == "42"


def test_job_description_returns_none_when_not_in_listing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    assert _client(handler).job_description("missing") is None


def test_list_jobs_parses_array() -> None:
    """``GET /v1/jobs`` -> list[ChapJobDescription]."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/jobs"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc-123",
                    "type": "make_prediction",
                    "name": "first",
                    "status": "SUCCESS",
                    "start_time": "2026-05-07T10:00:00",
                    "end_time": "2026-05-07T10:01:00",
                    "result": "42",
                },
                {
                    "id": "def-456",
                    "type": "create_backtest",
                    "name": "second",
                    "status": "PENDING",
                    "start_time": None,
                    "end_time": None,
                    "result": None,
                },
            ],
        )

    jobs = _client(handler).list_jobs()
    assert [j.id for j in jobs] == ["abc-123", "def-456"]
    assert jobs[0].status == "SUCCESS"
    assert jobs[0].result == "42"
    assert jobs[1].status == "PENDING"
    assert jobs[1].result is None


# --- typed endpoint: prediction_entries ------------------------------------


def test_prediction_entries_passes_quantiles_as_repeated_query_params() -> None:
    seen_query: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = {k: request.url.params.get_list(k) for k in request.url.params.keys()}
        assert request.url.path == "/v1/analytics/prediction-entry/42"
        return httpx.Response(
            200,
            json=[
                {"orgUnit": "OU1", "period": "202501", "quantile": 0.5, "value": 7.0},
                {"orgUnit": "OU1", "period": "202502", "quantile": 0.5, "value": 8.0},
            ],
        )

    entries = _client(handler).prediction_entries(42, quantiles=[0.1, 0.5, 0.9])
    assert len(entries) == 2
    assert entries[0].value == 7.0
    assert seen_query["quantiles"] == ["0.1", "0.5", "0.9"]


def test_prediction_entries_rejects_empty_quantile_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - shouldn't be called
        raise AssertionError("HTTP should not be called when quantiles is empty")

    with pytest.raises(ValueError, match="quantiles must contain at least one"):
        _client(handler).prediction_entries(42, quantiles=[])


# --- connection pooling + context manager ----------------------------------


def test_http_client_is_reused_across_calls() -> None:
    """Multiple GETs reuse the same underlying httpx.Client instance."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json="OK")

    client = _client(handler)
    client.get("/v1/jobs/1")
    client.get("/v1/jobs/2")
    assert calls == 2
    first = client._http()
    second = client._http()
    assert first is second


def test_close_disposes_underlying_client() -> None:
    """close() drops the cached httpx.Client; next call reopens one."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="OK")

    client = _client(handler)
    client.get("/v1/jobs/1")
    pre = client._client
    assert pre is not None

    client.close()
    assert client._client is None

    client.get("/v1/jobs/2")
    post = client._client
    assert post is not None and post is not pre


def test_context_manager_closes_on_exit() -> None:
    """Using ChapClient as a `with` block disposes the connection pool."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="OK")

    with _client(handler) as client:
        client.get("/v1/jobs/1")
        assert client._client is not None
    assert client._client is None


# --- retry behaviour --------------------------------------------------------


def test_get_retries_on_5xx_then_succeeds() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"detail": "temporarily unavailable"})
        return httpx.Response(200, json="OK")

    body = _retrying_client(handler).get("/v1/jobs/abc")
    assert body == "OK"
    assert attempts == 2


def test_get_retry_exhausted_raises_last_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"detail": "still down"})

    with pytest.raises(ChapHttpError) as excinfo:
        _retrying_client(handler, max_attempts=3).get("/v1/jobs/abc")
    assert excinfo.value.status == 503
    assert attempts == 3


def test_get_retries_on_httpx_connect_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json="OK")

    body = _retrying_client(handler).get("/v1/jobs/abc")
    assert body == "OK"
    assert attempts == 2


def test_get_does_not_retry_on_4xx() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404, json={"detail": "not found"})

    with pytest.raises(ChapHttpError) as excinfo:
        _retrying_client(handler).get("/v1/jobs/missing")
    assert excinfo.value.status == 404
    assert attempts == 1


def test_post_does_not_retry_on_5xx() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"detail": "unavailable"})

    with pytest.raises(ChapHttpError) as excinfo:
        _retrying_client(handler).post("/v1/crud/prediction-setups/7/run", json={})
    assert excinfo.value.status == 503
    assert attempts == 1


def test_max_attempts_one_disables_retries_for_get() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"detail": "unavailable"})

    with pytest.raises(ChapHttpError):
        _retrying_client(handler, max_attempts=1).get("/v1/jobs/abc")
    assert attempts == 1


# --- typed endpoint: datasets ---------------------------------------------


def _dataset_payload(id: int = 1, name: str = "test", type_: str = "evaluation") -> dict[str, Any]:
    """Reusable fixture matching chap's `DataSetRead` wire shape."""
    return {
        "id": id,
        "name": name,
        "type": type_,
        "periodType": "month",
        "firstPeriod": "202301",
        "lastPeriod": "202412",
        "orgUnits": ["OU1", "OU2"],
        "covariates": ["population", "rainfall"],
        "dataSources": [{"covariate": "population", "dataElementId": "POP1"}],
        "created": "2026-05-07T18:07:06.534502",
    }


def test_list_datasets_parses_array() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/crud/datasets"
        return httpx.Response(200, json=[_dataset_payload(1, "evals"), _dataset_payload(2, "preds", "prediction")])

    datasets = _client(handler).list_datasets()
    assert [(d.id, d.name, d.type) for d in datasets] == [(1, "evals", "evaluation"), (2, "preds", "prediction")]
    assert datasets[0].first_period == "202301"
    assert datasets[0].last_period == "202412"


def test_get_dataset_fetches_by_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/crud/datasets/42"
        return httpx.Response(200, json=_dataset_payload(id=42, name="my-dataset"))

    dataset = _client(handler).get_dataset(42)
    assert dataset.id == 42
    assert dataset.name == "my-dataset"


# --- typed endpoint: evaluations -----------------------------------------


def _backtest_payload(id: int = 1, name: str = "test", model_id: str = "chapkit-ewars-model") -> dict[str, Any]:
    """Reusable fixture matching chap's `BacktestRead` wire shape (subset)."""
    return {
        "id": id,
        "name": name,
        "modelId": model_id,
        "datasetId": 1,
        "modelTemplateVersion": "1.0.0",
        "orgUnits": ["OU1", "OU2"],
        "splitPeriods": ["202401", "202402"],
        "aggregateMetrics": {"crps": 24.12, "mae": 32.71, "rmse": 62.84},
        "created": "2026-05-08T12:15:39.302333",
    }


def test_list_evaluations_parses_array_with_aggregate_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/crud/backtests"
        return httpx.Response(200, json=[_backtest_payload(id=1, name="bt1"), _backtest_payload(id=2, name="bt2")])

    backtests = _client(handler).list_evaluations()
    assert [(b.id, b.name) for b in backtests] == [(1, "bt1"), (2, "bt2")]
    assert backtests[0].aggregate_metrics["crps"] == 24.12
    assert backtests[0].split_periods == ["202401", "202402"]


def test_get_evaluation_uses_info_path() -> None:
    """``GET /v1/crud/backtests/{id}/info`` -- not the heavier ``/full``."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/crud/backtests/7/info"
        return httpx.Response(200, json=_backtest_payload(id=7, name="seven"))

    bt = _client(handler).get_evaluation(7)
    assert bt.id == 7
    assert bt.name == "seven"
    assert bt.model_id == "chapkit-ewars-model"


def test_delete_evaluation_issues_DELETE_and_returns_none() -> None:
    seen: dict[str, str] = {"method": "", "path": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(204)

    result = _client(handler).delete_evaluation(99)
    assert result is None
    assert seen == {"method": "DELETE", "path": "/v1/crud/backtests/99"}


def test_create_evaluation_posts_camelcase_body() -> None:
    received: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/analytics/create-backtest"
        nonlocal received
        received = json.loads(request.content)
        return httpx.Response(200, json={"id": "bt-job-uuid"})

    req = ChapMakeEvaluationRequest(
        name="smoke-test",
        modelId="chapkit-ewars-model",
        datasetId=1,
        nPeriods=3,
        nSplits=10,
        stride=1,
    )
    # validate=False -- preflight has its own tests below.
    job = _client(handler).create_evaluation(req, validate=False)
    assert job.id == "bt-job-uuid"
    assert received == {
        "name": "smoke-test",
        "modelId": "chapkit-ewars-model",
        "datasetId": 1,
        "nPeriods": 3,
        "nSplits": 10,
        "stride": 1,
    }


def test_create_evaluation_excludes_unset_optional_params() -> None:
    """Optional fields left as None must be omitted from the wire body
    (chap may have its own defaults; sending None overrides)."""
    received: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received
        received = json.loads(request.content)
        return httpx.Response(200, json={"id": "bt-job-uuid"})

    req = ChapMakeEvaluationRequest(name="minimal", modelId="m", datasetId=1)
    _client(handler).create_evaluation(req, validate=False)
    assert "nPeriods" not in received
    assert "nSplits" not in received
    assert "stride" not in received


def test_create_evaluation_does_not_retry_on_5xx() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"detail": "unavailable"})

    req = ChapMakeEvaluationRequest(name="x", modelId="m", datasetId=1)
    with pytest.raises(ChapHttpError) as excinfo:
        _retrying_client(handler).create_evaluation(req, validate=False)
    assert excinfo.value.status == 503
    assert attempts == 1


# --- typed endpoint: evaluation_entries -----------------------------------


def test_evaluation_entries_passes_quantiles_and_backtest_id_as_query_params() -> None:
    seen_query: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = {k: request.url.params.get_list(k) for k in request.url.params.keys()}
        assert request.url.path == "/v1/analytics/evaluation-entry"
        return httpx.Response(
            200,
            json=[
                {"orgUnit": "OU1", "period": "202407", "quantile": 0.5, "value": 7.0, "splitPeriod": "202401"},
                {"orgUnit": "OU1", "period": "202408", "quantile": 0.5, "value": 8.0, "splitPeriod": "202401"},
            ],
        )

    entries = _client(handler).evaluation_entries(99, quantiles=[0.1, 0.5, 0.9])
    assert len(entries) == 2
    assert entries[0].split_period == "202401"
    assert entries[0].value == 7.0
    assert seen_query == {"backtestId": ["99"], "quantiles": ["0.1", "0.5", "0.9"]}


def test_evaluation_entries_forwards_optional_split_period_and_org_units() -> None:
    seen_query: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = {k: request.url.params.get_list(k) for k in request.url.params.keys()}
        return httpx.Response(200, json=[])

    _client(handler).evaluation_entries(
        99,
        quantiles=[0.5],
        split_period="202401",
        org_units=["OU1", "OU2"],
    )
    assert seen_query["splitPeriod"] == ["202401"]
    assert seen_query["orgUnits"] == ["OU1", "OU2"]


def test_evaluation_entries_rejects_empty_quantile_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("HTTP should not be called when quantiles is empty")

    with pytest.raises(ValueError, match="quantiles must contain at least one"):
        _client(handler).evaluation_entries(99, quantiles=[])


# --- defensive validation: schema constraints ------------------------------


def test_evaluation_request_rejects_empty_name() -> None:
    """Pydantic min_length=1 -- mitigates CHAP_SPEC_DRIFT.md finding #14."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="at least 1 character"):
        ChapMakeEvaluationRequest(name="", modelId="m", datasetId=1)


def test_evaluation_request_rejects_negative_n_periods() -> None:
    """Pydantic gt=0 -- mitigates CHAP_SPEC_DRIFT.md finding #15."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="greater than 0"):
        ChapMakeEvaluationRequest(name="x", modelId="m", datasetId=1, nPeriods=-5)


def test_evaluation_request_rejects_negative_n_splits_and_stride() -> None:
    """gt=0 also applies to nSplits and stride."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="greater than 0"):
        ChapMakeEvaluationRequest(name="x", modelId="m", datasetId=1, nSplits=0)
    with pytest.raises(pydantic.ValidationError, match="greater than 0"):
        ChapMakeEvaluationRequest(name="x", modelId="m", datasetId=1, stride=-1)


def test_evaluation_request_rejects_extra_fields() -> None:
    """extra='forbid' -- mitigates CHAP_SPEC_DRIFT.md finding #16."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="extra"):
        ChapMakeEvaluationRequest.model_validate(
            {"name": "x", "modelId": "m", "datasetId": 1, "weirdExtraField": "ignored?"}
        )


def test_prediction_request_rejects_empty_name() -> None:
    """ChapRunPredictionSetupRequest enforces min_length=1 on name."""
    import pydantic
    from geojson_pydantic import Feature, FeatureCollection

    fc: FeatureCollection[Feature[Any, dict[str, Any]]] = FeatureCollection(type="FeatureCollection", features=[])
    with pytest.raises(pydantic.ValidationError, match="at least 1 character"):
        ChapRunPredictionSetupRequest(
            name="",
            geojson=fc,
            providedData=[],
        )


def test_prediction_request_rejects_legacy_extra_fields() -> None:
    """``dataSources`` / ``configuredModelWithDataSourceId`` are gone in the new shape.

    chap-core PR #354 returns 422 on those keys; the client should refuse
    just as loudly so callers can't accidentally send the old body.
    """
    import pydantic
    from geojson_pydantic import Feature, FeatureCollection

    fc: FeatureCollection[Feature[Any, dict[str, Any]]] = FeatureCollection(type="FeatureCollection", features=[])
    with pytest.raises(pydantic.ValidationError, match="extra"):
        ChapRunPredictionSetupRequest.model_validate(
            {
                "name": "x",
                "geojson": fc.model_dump(),
                "providedData": [],
                "dataSources": [],
                "configuredModelWithDataSourceId": 1,
            }
        )


def test_configured_model_create_rejects_empty_name() -> None:
    """ChapConfiguredModelCreate enforces min_length=1 on name."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="at least 1 character"):
        ChapConfiguredModelCreate(name="", modelTemplateId=1)


def test_configured_model_create_rejects_zero_template_id() -> None:
    """gt=0 on modelTemplateId."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="greater than 0"):
        ChapConfiguredModelCreate(name="x", modelTemplateId=0)


# --- list_model_templates --------------------------------------------------


def test_list_model_templates_parses_array() -> None:
    """``GET /v1/crud/model-templates`` -> list[ChapModelTemplate] with id."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/crud/model-templates"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "chap_ewars_monthly",
                    "displayName": "Monthly CHAP-EWARS model",
                    "target": "disease_cases",
                    "supportedPeriodType": "month",
                    "requiredCovariates": ["rainfall", "mean_temperature", "population"],
                },
                {
                    "id": 2,
                    "name": "chap_ewars_weekly",
                    "displayName": "Weekly CHAP-EWARS model",
                    "target": "disease_cases",
                    "supportedPeriodType": "week",
                    "requiredCovariates": [],
                },
            ],
        )

    templates = _client(handler).list_model_templates()
    assert [t.id for t in templates] == [1, 2]
    assert templates[0].name == "chap_ewars_monthly"
    assert templates[0].display_name == "Monthly CHAP-EWARS model"


# --- preflight: create_configured_model ------------------------------------


def test_create_configured_model_preflight_rejects_unknown_template_id() -> None:
    """validate=True -> ValueError if modelTemplateId not in /v1/crud/model-templates."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/crud/model-templates":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "name": "t1",
                        "displayName": "T1",
                        "target": "disease_cases",
                        "supportedPeriodType": "month",
                        "requiredCovariates": [],
                    }
                ],
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    spec = ChapConfiguredModelCreate(name="x", modelTemplateId=99)
    with pytest.raises(ValueError, match="modelTemplateId=99 does not match"):
        _client(handler).create_configured_model(spec)


def test_create_configured_model_preflight_passes_when_template_id_matches() -> None:
    """validate=True with a matching template id -> POST proceeds normally."""
    seen_post = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_post
        if request.url.path == "/v1/crud/model-templates":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "name": "t1",
                        "displayName": "T1",
                        "target": "disease_cases",
                        "supportedPeriodType": "month",
                        "requiredCovariates": [],
                    }
                ],
            )
        if request.method == "POST" and request.url.path == "/v1/crud/configured-models":
            seen_post = True
            return httpx.Response(
                200,
                json={
                    "id": 5,
                    "name": "t1:my-name",
                    "modelTemplateId": 1,
                    "archived": False,
                    "usesChapkit": False,
                    "userOptionValues": {},
                    "additionalContinuousCovariates": [],
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    spec = ChapConfiguredModelCreate(name="my-name", modelTemplateId=1)
    created = _client(handler).create_configured_model(spec)
    assert seen_post
    assert created.id == 5


# --- preflight: create_evaluation ------------------------------------------


def test_create_evaluation_preflight_rejects_unknown_model_id() -> None:
    """validate=True -> ValueError if modelId not in /v1/crud/configured-models."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/crud/configured-models":
            return httpx.Response(200, json=[_model_spec_payload(id=1, name="known-name")])
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    req = ChapMakeEvaluationRequest(name="x", modelId="not-real", datasetId=1)
    with pytest.raises(ValueError, match="modelId='not-real' does not match"):
        _client(handler).create_evaluation(req)


def test_create_evaluation_preflight_rejects_unknown_dataset_id() -> None:
    """validate=True -> ValueError if datasetId returns 404 from get_dataset."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/crud/configured-models":
            return httpx.Response(200, json=[_model_spec_payload(id=1, name="known-name")])
        if request.url.path == "/v1/crud/datasets/99999":
            return httpx.Response(404, json={"detail": "Dataset not found"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    req = ChapMakeEvaluationRequest(name="x", modelId="known-name", datasetId=99999)
    with pytest.raises(ValueError, match="datasetId=99999 does not exist"):
        _client(handler).create_evaluation(req)


def test_create_evaluation_preflight_passes_when_both_resolve() -> None:
    """validate=True with valid modelId + datasetId -> POST proceeds."""
    seen_post = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_post
        if request.url.path == "/v1/crud/configured-models":
            return httpx.Response(200, json=[_model_spec_payload(id=1, name="known-name")])
        if request.url.path == "/v1/crud/datasets/1":
            return httpx.Response(200, json=_dataset_payload(id=1))
        if request.method == "POST" and request.url.path == "/v1/analytics/create-backtest":
            seen_post = True
            return httpx.Response(200, json={"id": "job-uuid"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    req = ChapMakeEvaluationRequest(name="x", modelId="known-name", datasetId=1)
    job = _client(handler).create_evaluation(req)
    assert seen_post
    assert job.id == "job-uuid"


def test_create_evaluation_skips_preflight_when_validate_false() -> None:
    """validate=False -> only the POST happens, no GETs for preflight."""
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"id": "job-uuid"})

    req = ChapMakeEvaluationRequest(name="x", modelId="any", datasetId=999)
    _client(handler).create_evaluation(req, validate=False)
    assert seen_paths == ["POST /v1/analytics/create-backtest"]


# --- wait_for_job ---------------------------------------------------------


def _wait_handler(
    *,
    members: list[str] | None = None,
    statuses: list[str] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Build an httpx handler that mocks /v1/jobs (members) + /v1/jobs/{id} (statuses)."""
    member_list: list[str] = members if members is not None else ["job-123"]
    status_iter = iter(statuses or [])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": jid,
                        "type": "make_prediction",
                        "name": "x",
                        "status": "PENDING",
                        "start_time": None,
                        "end_time": None,
                        "result": None,
                    }
                    for jid in member_list
                ],
            )
        if request.url.path.startswith("/v1/jobs/"):
            try:
                next_status = next(status_iter)
            except StopIteration:  # pragma: no cover -- test bug if hit
                raise AssertionError("test pulled more statuses than provided")
            return httpx.Response(200, json=next_status)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    return handler


def test_wait_for_job_returns_terminal_status_on_first_poll() -> None:
    """job_description finds the id, first job_status is terminal -> return immediately."""
    handler = _wait_handler(members=["job-123"], statuses=["SUCCESS"])
    result = _client(handler).wait_for_job("job-123", timeout=10.0, poll_interval=0.0)
    assert result == "SUCCESS"


def test_wait_for_job_polls_until_terminal() -> None:
    """Transient on first two polls, terminal on third."""
    handler = _wait_handler(members=["job-123"], statuses=["PENDING", "RUNNING", "SUCCESS"])
    result = _client(handler).wait_for_job("job-123", timeout=10.0, poll_interval=0.0)
    assert result == "SUCCESS"


def test_wait_for_job_returns_failure_status_without_retrying() -> None:
    """Non-transient terminal status (e.g. FAILED) is returned, not retried."""
    handler = _wait_handler(members=["job-123"], statuses=["FAILED"])
    assert _client(handler).wait_for_job("job-123", timeout=10.0, poll_interval=0.0) == "FAILED"


def test_wait_for_job_recognises_transient_status_case_insensitively() -> None:
    """Lowercase 'running' counts as transient -> keeps polling."""
    handler = _wait_handler(members=["job-123"], statuses=["running", "SUCCESS"])
    assert _client(handler).wait_for_job("job-123", timeout=10.0, poll_interval=0.0) == "SUCCESS"


def test_wait_for_job_raises_timeout_when_status_never_terminal() -> None:
    """Status keeps returning RUNNING; loop must give up at the deadline."""
    handler = _wait_handler(members=["job-123"], statuses=["RUNNING", "RUNNING", "RUNNING"])
    with pytest.raises(TimeoutError, match="did not finish within"):
        _client(handler).wait_for_job("job-123", timeout=0.0, poll_interval=0.0)


def test_wait_for_job_raises_value_error_for_unknown_id() -> None:
    """Mitigates CHAP_SPEC_DRIFT.md finding #7: unknown ids return 200 'PENDING'.

    Without the membership check the wait would hit the timeout. With it,
    the helper refuses synchronously with a useful error.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json=[])  # no jobs -> id not present
        raise AssertionError(f"job_status should not be polled for an unknown id: {request.url.path}")

    with pytest.raises(ValueError, match="unknown job id"):
        _client(handler).wait_for_job("not-a-real-job", timeout=10.0, poll_interval=0.0)


def test_wait_for_job_invokes_on_status_callback_on_changes_only() -> None:
    """on_status fires once per *change* in status, not every poll."""
    seen: list[str] = []

    handler = _wait_handler(
        members=["job-123"],
        statuses=["PENDING", "PENDING", "RUNNING", "SUCCESS"],
    )
    _client(handler).wait_for_job("job-123", timeout=10.0, poll_interval=0.0, on_status=seen.append)
    assert seen == ["PENDING", "RUNNING", "SUCCESS"]
