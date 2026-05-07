"""Unit tests for ``wait_for_prediction``'s polling loop and ``_run_one_model``'s
per-step error routing.

These exercise the parts of the flow that are most likely to silently regress:

- ``wait_for_prediction``: transient-status retry, terminal-status detection
  (case-insensitive), and the timeout path.
- ``_run_one_model`` -> ``_StepFailure``: each step's failure must surface
  with the right step label so the run report points at the right culprit.
- ``_populate_entry_from_step_failure``: the flow body's catch handler now
  lives in a helper, including the special-case parse of
  ``ChapHttpError.detail`` into ``entry.rejection_detail``.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.chap import (
    ChapConfiguredModel,
    ChapConfiguredModelWithDataSource,
    ChapDataSource,
    ChapHttpError,
    ChapModelTemplate,
    ModelRunEntry,
)
from chap_scheduler.flows.dhis2_chap_prediction import (
    _populate_entry_from_step_failure,
    _run_one_model,
    _StepFailure,
    wait_for_prediction,
)


def _credentials() -> Dhis2Credentials:
    return Dhis2Credentials(
        base_url="http://test.example",
        username="alice",
        password=SecretStr("hunter2"),
    )


def _model_fixture() -> ChapConfiguredModelWithDataSource:
    template = ChapModelTemplate(
        name="chapkit-ewars-model",
        displayName="CHAP-EWARS",
        target="disease_cases",
        requiredCovariates=["population"],
        supportedPeriodType="month",
    )
    cm = ChapConfiguredModel(
        id=12,
        name="chapkit-ewars-model",
        additionalContinuousCovariates=["rainfall"],
        modelTemplate=template,
    )
    return ChapConfiguredModelWithDataSource(
        id=1,
        name="test",
        configuredModel=cm,
        startPeriod="202301",
        orgUnits=["OU1"],
        dataSources=[ChapDataSource(covariate="population", dataElementId="POP1")],
        periodType="month",
    )


# --- #24: wait_for_prediction polling --------------------------------------


def test_wait_for_prediction_returns_immediately_on_terminal_status() -> None:
    """First poll returns SUCCESS -> task returns SUCCESS without sleeping."""
    with patch("chap_scheduler.flows.dhis2_chap_prediction.ChapClient") as mock_chap_client:
        mock_chap_client.return_value.job_status.return_value = "SUCCESS"
        result = wait_for_prediction.fn(
            _credentials(),
            "job-123",
            "label",
            timeout_seconds=10,
            poll_interval_seconds=0.0,
        )
    assert result == "SUCCESS"
    assert mock_chap_client.return_value.job_status.call_count == 1


def test_wait_for_prediction_polls_until_terminal() -> None:
    """Transient statuses on the first two polls, terminal on the third."""
    with patch("chap_scheduler.flows.dhis2_chap_prediction.ChapClient") as mock_chap_client:
        mock_chap_client.return_value.job_status.side_effect = ["PENDING", "RUNNING", "SUCCESS"]
        result = wait_for_prediction.fn(
            _credentials(),
            "job-123",
            "label",
            timeout_seconds=10,
            poll_interval_seconds=0.0,
        )
    assert result == "SUCCESS"
    assert mock_chap_client.return_value.job_status.call_count == 3


def test_wait_for_prediction_returns_failure_status_without_retrying() -> None:
    """Non-transient terminal status (e.g. FAILED) is returned, not retried."""
    with patch("chap_scheduler.flows.dhis2_chap_prediction.ChapClient") as mock_chap_client:
        mock_chap_client.return_value.job_status.return_value = "FAILED"
        result = wait_for_prediction.fn(
            _credentials(),
            "job-123",
            "label",
            timeout_seconds=10,
            poll_interval_seconds=0.0,
        )
    assert result == "FAILED"
    assert mock_chap_client.return_value.job_status.call_count == 1


def test_wait_for_prediction_recognises_transient_status_case_insensitively() -> None:
    """Lowercase 'running' counts as transient -> keeps polling."""
    with patch("chap_scheduler.flows.dhis2_chap_prediction.ChapClient") as mock_chap_client:
        mock_chap_client.return_value.job_status.side_effect = ["running", "SUCCESS"]
        result = wait_for_prediction.fn(
            _credentials(),
            "job-123",
            "label",
            timeout_seconds=10,
            poll_interval_seconds=0.0,
        )
    assert result == "SUCCESS"
    assert mock_chap_client.return_value.job_status.call_count == 2


def test_wait_for_prediction_raises_timeout_when_status_never_terminal() -> None:
    """Status keeps returning RUNNING; loop must give up at the deadline."""
    with patch("chap_scheduler.flows.dhis2_chap_prediction.ChapClient") as mock_chap_client:
        mock_chap_client.return_value.job_status.return_value = "RUNNING"
        with pytest.raises(TimeoutError, match="did not finish within"):
            wait_for_prediction.fn(
                _credentials(),
                "job-123",
                "label",
                timeout_seconds=0,
                poll_interval_seconds=0.0,
            )
    assert mock_chap_client.return_value.job_status.call_count >= 1


# --- #25: _populate_entry_from_step_failure --------------------------------


def test_populate_entry_records_step_label_and_cause_message() -> None:
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    cause = RuntimeError("boom")
    failure = _StepFailure("submit_prediction")
    failure.__cause__ = cause

    _populate_entry_from_step_failure(entry, failure)

    assert entry.step_failed == "submit_prediction"
    assert entry.error == "RuntimeError: boom"
    assert entry.rejection_detail is None


def test_populate_entry_handles_step_failure_without_cause() -> None:
    """When _StepFailure has no __cause__, error falls back to the step name."""
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    failure = _StepFailure("validate_period_range")

    _populate_entry_from_step_failure(entry, failure)

    assert entry.step_failed == "validate_period_range"
    assert entry.error == "validate_period_range"


def test_populate_entry_parses_chap_missing_values_detail_from_chaphttperror() -> None:
    """When the cause is a ChapHttpError carrying chap's structured 'missing values'
    body, populate ``entry.rejection_detail`` so the report renders the per-
    covariate summary."""
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    chap_error = ChapHttpError(
        method="POST",
        path="/v1/analytics/make-prediction-with-data-source",
        status=400,
        detail={
            "detail": {
                "message": "All regions rejected due to missing values",
                "imported_count": 0,
                "rejected": [
                    {
                        "reason": "Missing value for some/all time periods",
                        "orgUnit": "OU1",
                        "featureName": "rainfall",
                        "timePeriods": ["202510", "202511"],
                    },
                ],
            }
        },
    )
    failure = _StepFailure("submit_prediction")
    failure.__cause__ = chap_error

    _populate_entry_from_step_failure(entry, failure)

    assert entry.step_failed == "submit_prediction"
    assert entry.rejection_detail is not None
    assert entry.rejection_detail.message == "All regions rejected due to missing values"
    assert entry.rejection_detail.imported_count == 0
    assert len(entry.rejection_detail.rejected) == 1
    assert entry.rejection_detail.rejected[0].feature_name == "rainfall"


def test_populate_entry_leaves_rejection_detail_none_for_non_chap_errors() -> None:
    """Plain ChapHttpError that doesn't match the missing-values shape: rejection_detail stays None."""
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    chap_error = ChapHttpError(
        method="GET",
        path="/v1/jobs/abc/prediction_result",
        status=500,
        detail={"detail": "Internal server error"},  # not a structured rejection
    )
    failure = _StepFailure("fetch_prediction_result")
    failure.__cause__ = chap_error

    _populate_entry_from_step_failure(entry, failure)

    assert entry.rejection_detail is None
    assert "ChapHttpError" in (entry.error or "")


# --- #25: _run_one_model exception routing ---------------------------------


def _patch_step(name: str, side_effect: Any) -> Any:
    """Helper: patch a flow-module symbol with a Mock that raises the given exception."""
    return patch(f"chap_scheduler.flows.dhis2_chap_prediction.{name}", side_effect=side_effect)


def test_run_one_model_labels_resolve_end_period_failure() -> None:
    """Probe / end-period resolution failures are surfaced verbatim (already a _StepFailure)."""
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    inner_failure = _StepFailure("probe_latest_covariate_periods")
    inner_failure.__cause__ = RuntimeError("missing covariate")

    with _patch_step("_resolve_end_period_for_run", inner_failure):
        with pytest.raises(_StepFailure) as excinfo:
            _run_one_model(
                _credentials(),
                _model_fixture(),
                entry,
                end_date=None,
                prediction_timeout_seconds=600,
            )
    assert excinfo.value.step == "probe_latest_covariate_periods"


def test_run_one_model_labels_validate_period_range_for_start_after_end() -> None:
    """If the configured start_period is after the resolved end, fail with
    `validate_period_range` (not as a fetch failure)."""
    model = _model_fixture()
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")

    with patch(
        "chap_scheduler.flows.dhis2_chap_prediction._resolve_end_period_for_run",
        return_value="202012",  # Earlier than start_period 202301 -> validate_period_range
    ):
        with pytest.raises(_StepFailure) as excinfo:
            _run_one_model(
                _credentials(),
                model,
                entry,
                end_date=None,
                prediction_timeout_seconds=600,
            )
    assert excinfo.value.step == "validate_period_range"


def test_run_one_model_labels_fetch_dhis2_failure() -> None:
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._resolve_end_period_for_run",
            return_value="202412",
        ),
        _patch_step("fetch_dhis2_for_model", RuntimeError("dhis2 boom")),
    ):
        with pytest.raises(_StepFailure) as excinfo:
            _run_one_model(
                _credentials(),
                _model_fixture(),
                entry,
                end_date=None,
                prediction_timeout_seconds=600,
            )
    assert excinfo.value.step == "fetch_dhis2_for_model"
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_run_one_model_labels_submit_prediction_failure() -> None:
    """Mock the chain up to submit_prediction so the failure surfaces at the
    right step."""
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    analytics = MagicMock()
    analytics.rows = []  # entry.analytics_rows is set from len(...)

    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._resolve_end_period_for_run",
            return_value="202412",
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_for_model", return_value=analytics),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_org_units_geojson",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.build_prediction_request",
            return_value=MagicMock(),
        ),
        _patch_step("submit_prediction", RuntimeError("chap rejected")),
    ):
        with pytest.raises(_StepFailure) as excinfo:
            _run_one_model(
                _credentials(),
                _model_fixture(),
                entry,
                end_date=None,
                prediction_timeout_seconds=600,
            )
    assert excinfo.value.step == "submit_prediction"


def test_run_one_model_translates_non_success_terminal_status_to_step_failure() -> None:
    """If wait_for_prediction returns FAILED (terminal but not SUCCESS),
    `_run_one_model` raises _StepFailure('wait_for_prediction')."""
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    analytics = MagicMock()
    analytics.rows = []
    job_response = MagicMock()
    job_response.id = "job-456"

    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._resolve_end_period_for_run",
            return_value="202412",
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_for_model", return_value=analytics),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_org_units_geojson",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.build_prediction_request",
            return_value=MagicMock(),
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.submit_prediction", return_value=job_response),
        patch("chap_scheduler.flows.dhis2_chap_prediction.wait_for_prediction", return_value="FAILED"),
    ):
        with pytest.raises(_StepFailure) as excinfo:
            _run_one_model(
                _credentials(),
                _model_fixture(),
                entry,
                end_date=None,
                prediction_timeout_seconds=600,
            )
    assert excinfo.value.step == "wait_for_prediction"
    assert entry.job_id == "job-456"


def test_run_one_model_succeeds_end_to_end_with_all_steps_mocked() -> None:
    """Happy path: every step returns OK, _run_one_model returns None and
    populates the entry's prediction stats."""
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    analytics = MagicMock()
    analytics.rows = ["r1", "r2"]
    job_response = MagicMock()
    job_response.id = "job-456"
    pred_entry_a = MagicMock()
    pred_entry_a.period = "202501"
    pred_entry_b = MagicMock()
    pred_entry_b.period = "202502"

    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._resolve_end_period_for_run",
            return_value="202412",
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_for_model", return_value=analytics),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_org_units_geojson",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.build_prediction_request",
            return_value=MagicMock(),
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.submit_prediction", return_value=job_response),
        patch("chap_scheduler.flows.dhis2_chap_prediction.wait_for_prediction", return_value="SUCCESS"),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_prediction_result",
            return_value=(42, [pred_entry_a, pred_entry_b]),
        ),
    ):
        _run_one_model(
            _credentials(),
            _model_fixture(),
            entry,
            end_date=None,
            prediction_timeout_seconds=600,
        )
    assert entry.job_id == "job-456"
    assert entry.prediction_id == 42
    assert entry.prediction_values == 2
    assert entry.predicted_periods == ["202501", "202502"]
    assert entry.analytics_rows == 2
