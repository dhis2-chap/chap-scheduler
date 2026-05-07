"""Unit tests for ``ChapClient`` using ``httpx.MockTransport``.

These exercise the typed endpoint methods (``system_info``,
``configured_models``, ``submit_prediction``, ``job_status``,
``job_description``, ``prediction_entries``) plus the raw HTTP layer
(``request`` / ``ChapHttpError``) without standing up a real server.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from geojson_pydantic import Feature, FeatureCollection
from pydantic import SecretStr

from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.chap import (
    ChapClient,
    ChapHttpError,
    ChapMakePredictionRequest,
    ChapObservation,
)


def _credentials() -> Dhis2Credentials:
    return Dhis2Credentials(
        base_url="http://test.example",
        username="alice",
        password=SecretStr("hunter2"),
    )


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> ChapClient:
    return ChapClient(_credentials(), transport=httpx.MockTransport(handler))


# --- low-level request / error handling -------------------------------------


def test_request_sends_basic_auth() -> None:
    seen: dict[str, str | None] = {"auth": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    _client(handler).get("/anything")
    # Basic alice:hunter2 -> "Basic YWxpY2U6aHVudGVyMg=="
    assert seen["auth"] is not None
    assert seen["auth"].startswith("Basic ")


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
    # /v1/jobs/{id} returns a bare quoted-JSON string ("SUCCESS") -- httpx
    # parses that to a Python str. Verifies job_status's str(...) is OK.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'"SUCCESS"', headers={"content-type": "application/json"})

    assert _client(handler).get("/v1/jobs/abc") == "SUCCESS"


# --- typed endpoint: system_info -------------------------------------------


def test_system_info_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/routes/chap/run/system/info"
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


# --- typed endpoint: configured_models -------------------------------------


def test_configured_models_parses_list() -> None:
    payload = [
        {
            "id": 1,
            "name": "test",
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
            "dataSources": [{"covariate": "population", "dataElementId": "POP1"}],
            "periodType": "month",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/routes/chap/run/v1/crud/configured-models-with-data-source"
        return httpx.Response(200, json=payload)

    models = _client(handler).configured_models()
    assert len(models) == 1
    assert models[0].name == "test"
    assert models[0].configured_model.model_template.target == "disease_cases"


# --- typed endpoint: submit_prediction -------------------------------------


def _build_prediction_request() -> ChapMakePredictionRequest:
    return ChapMakePredictionRequest(
        name="run-1",
        geojson=FeatureCollection[Feature[Any, dict[str, Any]]](type="FeatureCollection", features=[]),
        providedData=[ChapObservation(featureName="population", orgUnit="OU1", period="202301", value=1000.0)],
        dataSources=[],
        dataToBeFetched=[],
        configuredModelWithDataSourceId=1,
        nPeriods=3,
        type="forecasting",
    )


def test_submit_prediction_sends_camelcase_body_and_parses_job_response() -> None:
    received_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/routes/chap/run/v1/analytics/make-prediction-with-data-source"
        nonlocal received_body
        received_body = json.loads(request.content)
        return httpx.Response(200, json={"id": "job-uuid-123"})

    client = _client(handler)
    job = client.submit_prediction(_build_prediction_request())
    assert job.id == "job-uuid-123"
    # camelCase keys -- the chap API expects these names.
    assert "providedData" in received_body
    assert "configuredModelWithDataSourceId" in received_body
    assert "nPeriods" in received_body


# --- typed endpoint: job_status / job_description ---------------------------


def test_job_status_returns_status_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/routes/chap/run/v1/jobs/abc-123"
        return httpx.Response(200, content=b'"SUCCESS"', headers={"content-type": "application/json"})

    assert _client(handler).job_status("abc-123") == "SUCCESS"


def test_job_description_finds_matching_job_in_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/routes/chap/run/v1/jobs"
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


# --- typed endpoint: prediction_entries ------------------------------------


def test_prediction_entries_passes_quantiles_as_repeated_query_params() -> None:
    seen_query: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # httpx represents repeated keys as a list under request.url.params
        nonlocal seen_query
        seen_query = {k: request.url.params.get_list(k) for k in request.url.params.keys()}
        assert request.url.path == "/api/routes/chap/run/v1/analytics/prediction-entry/42"
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
