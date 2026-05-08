"""Smoke tests for `chap-client` CLI.

Each command is exercised with `ChapClient` patched at the module level
so we can assert on stdout without standing up a real chap server.
"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from chap_client import (
    ChapDataset,
    ChapEvaluationEntry,
    ChapEvaluationRead,
    ChapJobDescription,
    ChapJobResponse,
    ChapModelSpec,
    ChapPredictionEntry,
    ChapSystemInfo,
)
from chap_client.cli import app

runner = CliRunner()


def _system_info() -> ChapSystemInfo:
    return ChapSystemInfo(
        chap_core_version="2.0.0.dev1",
        python_version="3.13.12",
        server_date="2026-05-08T10:00:00+00:00",  # type: ignore[arg-type]
        server_time_zone_id="Etc/UTC",
    )


def _dataset(id: int = 1, name: str = "test") -> ChapDataset:
    return ChapDataset.model_validate(
        {
            "id": id,
            "name": name,
            "type": "evaluation",
            "periodType": "month",
            "firstPeriod": "202301",
            "lastPeriod": "202412",
            "orgUnits": ["OU1"],
            "covariates": ["population"],
            "dataSources": [{"covariate": "population", "dataElementId": "POP1"}],
        }
    )


def _model_spec(id: int = 1, name: str = "m") -> ChapModelSpec:
    return ChapModelSpec.model_validate(
        {
            "id": id,
            "name": name,
            "target": {"name": "disease_cases"},
            "covariates": [{"name": "population"}],
        }
    )


def _evaluation(id: int = 1, name: str = "ev") -> ChapEvaluationRead:
    return ChapEvaluationRead.model_validate(
        {
            "id": id,
            "name": name,
            "datasetId": 1,
            "modelId": "chapkit-ewars-model",
            "aggregateMetrics": {"crps": 24.12},
            "splitPeriods": ["202401"],
        }
    )


@pytest.fixture
def mock_client() -> Any:
    """Patches `ChapClient` inside the CLI module to a configurable MagicMock."""
    with patch("chap_client.cli.ChapClient") as cls:
        cls.return_value.__enter__.return_value = cls.return_value
        cls.return_value.__exit__.return_value = None
        yield cls.return_value


# --- top-level -------------------------------------------------------------


def test_version_prints_and_exits() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "chap-client" in result.stdout


def test_missing_base_url_errors_with_clear_message() -> None:
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 2
    # CliRunner mixes stderr into stdout when mix_stderr is the default.
    combined = result.output
    assert "base-url" in combined or "BASE_URL" in combined.upper()


# --- system ----------------------------------------------------------------


def test_info_prints_system_info_json(mock_client: MagicMock) -> None:
    mock_client.system_info.return_value = _system_info()
    result = runner.invoke(app, ["--base-url", "http://localhost:8000", "info"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["chap_core_version"] == "2.0.0.dev1"


def test_info_picks_up_base_url_from_env_var(mock_client: MagicMock) -> None:
    mock_client.system_info.return_value = _system_info()
    result = runner.invoke(app, ["info"], env={"CHAP_CLIENT_BASE_URL": "http://localhost:8000"})
    assert result.exit_code == 0
    assert "chap_core_version" in result.stdout


# --- datasets --------------------------------------------------------------


def test_datasets_list_prints_array(mock_client: MagicMock) -> None:
    mock_client.list_datasets.return_value = [_dataset(1, "a"), _dataset(2, "b")]
    result = runner.invoke(app, ["--base-url", "http://localhost:8000", "datasets", "list"])
    assert result.exit_code == 0
    items = json.loads(result.stdout)
    assert [d["id"] for d in items] == [1, 2]


def test_datasets_get_prints_single(mock_client: MagicMock) -> None:
    mock_client.get_dataset.return_value = _dataset(42, "my-ds")
    result = runner.invoke(app, ["--base-url", "http://localhost:8000", "datasets", "get", "42"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["id"] == 42
    mock_client.get_dataset.assert_called_once_with(42)


# --- models ----------------------------------------------------------------


def test_models_list_prints_array(mock_client: MagicMock) -> None:
    mock_client.list_models.return_value = [_model_spec(1, "m1"), _model_spec(2, "m2")]
    result = runner.invoke(app, ["--base-url", "http://localhost:8000", "models", "list"])
    assert result.exit_code == 0
    items = json.loads(result.stdout)
    assert [m["name"] for m in items] == ["m1", "m2"]


# --- evaluations -----------------------------------------------------------


def test_evaluations_list_prints_aggregate_metrics(mock_client: MagicMock) -> None:
    mock_client.list_evaluations.return_value = [_evaluation(1, "e1")]
    result = runner.invoke(app, ["--base-url", "http://localhost:8000", "evaluations", "list"])
    assert result.exit_code == 0
    items = json.loads(result.stdout)
    assert items[0]["aggregateMetrics"]["crps"] == 24.12


def test_evaluations_create_passes_camelcase_payload(mock_client: MagicMock) -> None:
    mock_client.create_evaluation.return_value = ChapJobResponse(id="job-123")
    result = runner.invoke(
        app,
        [
            "--base-url",
            "http://localhost:8000",
            "evaluations",
            "create",
            "--name",
            "smoke",
            "--model-id",
            "chapkit-ewars-model",
            "--dataset-id",
            "1",
        ],
    )
    assert result.exit_code == 0
    spec = mock_client.create_evaluation.call_args.args[0]
    # Wire body uses camelCase, but the pydantic model stores snake_case.
    assert spec.name == "smoke"
    assert spec.model_id == "chapkit-ewars-model"
    assert spec.dataset_id == 1


def test_evaluations_entries_forwards_quantiles_and_filters(mock_client: MagicMock) -> None:
    mock_client.evaluation_entries.return_value = [
        ChapEvaluationEntry.model_validate(
            {"orgUnit": "OU1", "period": "202407", "quantile": 0.5, "value": 7.0, "splitPeriod": "202401"}
        )
    ]
    result = runner.invoke(
        app,
        [
            "--base-url",
            "http://localhost:8000",
            "evaluations",
            "entries",
            "99",
            "--quantile",
            "0.1",
            "--quantile",
            "0.5",
            "--split-period",
            "202401",
            "--org-unit",
            "OU1",
        ],
    )
    assert result.exit_code == 0
    args, kwargs = mock_client.evaluation_entries.call_args
    assert args == (99,)
    assert kwargs == {"quantiles": [0.1, 0.5], "split_period": "202401", "org_units": ["OU1"]}


def test_evaluations_delete_prints_confirmation(mock_client: MagicMock) -> None:
    result = runner.invoke(app, ["--base-url", "http://localhost:8000", "evaluations", "delete", "7"])
    assert result.exit_code == 0
    assert "deleted evaluation 7" in result.stdout
    mock_client.delete_evaluation.assert_called_once_with(7)


# --- jobs ------------------------------------------------------------------


def test_jobs_status_prints_bare_string(mock_client: MagicMock) -> None:
    mock_client.job_status.return_value = "SUCCESS"
    result = runner.invoke(app, ["--base-url", "http://localhost:8000", "jobs", "status", "abc-123"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "SUCCESS"


def test_jobs_description_prints_null_when_missing(mock_client: MagicMock) -> None:
    mock_client.job_description.return_value = None
    result = runner.invoke(app, ["--base-url", "http://localhost:8000", "jobs", "description", "missing"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "null"


def test_jobs_description_prints_json_when_found(mock_client: MagicMock) -> None:
    mock_client.job_description.return_value = ChapJobDescription(
        id="abc-123", type="make_prediction", name="x", status="SUCCESS", result="42"
    )
    result = runner.invoke(app, ["--base-url", "http://localhost:8000", "jobs", "description", "abc-123"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["id"] == "abc-123"
    assert payload["result"] == "42"


# --- predictions -----------------------------------------------------------


def test_predictions_entries_forwards_quantiles(mock_client: MagicMock) -> None:
    mock_client.prediction_entries.return_value = [
        ChapPredictionEntry.model_validate({"orgUnit": "OU1", "period": "202501", "quantile": 0.5, "value": 7.0})
    ]
    result = runner.invoke(
        app,
        [
            "--base-url",
            "http://localhost:8000",
            "predictions",
            "entries",
            "42",
            "--quantile",
            "0.5",
        ],
    )
    assert result.exit_code == 0
    args, kwargs = mock_client.prediction_entries.call_args
    assert args == (42,)
    assert kwargs == {"quantiles": [0.5]}


# --- top-level option pass-through ----------------------------------------


def test_route_prefix_and_auth_are_forwarded_to_chapclient() -> None:
    """Ensure CLI options reach the ChapClient constructor, not just the
    command body."""
    with patch("chap_client.cli.ChapClient") as cls:
        cls.return_value.__enter__.return_value = cls.return_value
        cls.return_value.__exit__.return_value = None
        cls.return_value.system_info.return_value = _system_info()
        runner.invoke(
            app,
            [
                "--base-url",
                "http://dhis.example",
                "--user",
                "admin",
                "--password",
                "district",
                "--route-prefix",
                "/api/routes/chap/run",
                "--max-attempts",
                "1",
                "info",
            ],
        )
        kwargs = cls.call_args.kwargs
        assert kwargs == {
            "base_url": "http://dhis.example",
            "auth": ("admin", "district"),
            "route_prefix": "/api/routes/chap/run",
            "max_attempts": 1,
        }
