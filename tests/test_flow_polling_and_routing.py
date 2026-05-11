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

from chap_client import (
    ChapConfiguredModel,
    ChapConfiguredModelWithDataSource,
    ChapDataSource,
    ChapHttpError,
    ChapJobDescription,
    ChapModelTemplate,
)
from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.flows.dhis2_chap_prediction import (
    CalculatedEnd,
    FixedEnd,
    OffsetEnd,
    _populate_entry_from_step_failure,
    _resolve_end_period_for_run,
    _run_one_model,
    _StepFailure,
    dhis2_chap_prediction,
    fetch_prediction_result,
    wait_for_prediction,
)
from chap_scheduler.report import ModelRunEntry


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


# --- #24: wait_for_prediction delegation ----------------------------------
#
# The polling-loop logic itself moved to `chap_client.ChapClient.wait_for_job`
# in roadmap #55 (mitigates `CHAP_SPEC_DRIFT.md` finding #7); see
# `chap_client/tests/test_client.py::test_wait_for_job_*` for the
# transient/terminal/timeout/membership-check cases. The flow's
# `wait_for_prediction` is now a thin Prefect task wrapper that
# delegates to that helper. These tests verify the wiring.


def test_wait_for_prediction_returns_chap_clients_terminal_status() -> None:
    """The flow returns whatever `client.wait_for_job` returned."""
    with patch.object(Dhis2Credentials, "chap_client") as mock_chap_client:
        mock_chap_client.return_value.__enter__.return_value = mock_chap_client.return_value
        mock_chap_client.return_value.wait_for_job.return_value = "SUCCESS"
        result = wait_for_prediction.fn(
            _credentials(),
            "job-123",
            "label",
            timeout_seconds=10,
            poll_interval_seconds=0.0,
        )
    assert result == "SUCCESS"


def test_wait_for_prediction_forwards_timeout_and_poll_kwargs() -> None:
    """timeout_seconds / poll_interval_seconds reach `client.wait_for_job`."""
    with patch.object(Dhis2Credentials, "chap_client") as mock_chap_client:
        mock_chap_client.return_value.__enter__.return_value = mock_chap_client.return_value
        mock_chap_client.return_value.wait_for_job.return_value = "SUCCESS"
        wait_for_prediction.fn(
            _credentials(),
            "job-abc",
            "model-label",
            timeout_seconds=42,
            poll_interval_seconds=2.5,
        )
    call = mock_chap_client.return_value.wait_for_job.call_args
    assert call.args == ("job-abc",)
    assert call.kwargs["timeout"] == 42
    assert call.kwargs["poll_interval"] == 2.5
    # The flow injects an `on_status` callback so transitions are logged.
    assert callable(call.kwargs["on_status"])


def test_wait_for_prediction_propagates_value_error_for_unknown_job_id() -> None:
    """`client.wait_for_job` refuses unknown ids (drift #7); flow propagates."""
    with patch.object(Dhis2Credentials, "chap_client") as mock_chap_client:
        mock_chap_client.return_value.__enter__.return_value = mock_chap_client.return_value
        mock_chap_client.return_value.wait_for_job.side_effect = ValueError("unknown job id: 'bogus'")
        with pytest.raises(ValueError, match="unknown job id"):
            wait_for_prediction.fn(
                _credentials(),
                "bogus",
                "label",
                timeout_seconds=10,
                poll_interval_seconds=0.0,
            )


def test_wait_for_prediction_propagates_timeout_error() -> None:
    """`client.wait_for_job` raises TimeoutError on deadline; flow propagates."""
    with patch.object(Dhis2Credentials, "chap_client") as mock_chap_client:
        mock_chap_client.return_value.__enter__.return_value = mock_chap_client.return_value
        mock_chap_client.return_value.wait_for_job.side_effect = TimeoutError(
            "chap job job-123 did not finish within 0.0s (last status: 'RUNNING')"
        )
        with pytest.raises(TimeoutError, match="did not finish within"):
            wait_for_prediction.fn(
                _credentials(),
                "job-123",
                "label",
                timeout_seconds=0,
                poll_interval_seconds=0.0,
            )


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
                end=CalculatedEnd(),
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
                end=CalculatedEnd(),
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
                end=CalculatedEnd(),
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
                end=CalculatedEnd(),
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
                end=CalculatedEnd(),
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
            end=CalculatedEnd(),
            prediction_timeout_seconds=600,
        )
    assert entry.job_id == "job-456"
    assert entry.prediction_id == 42
    assert entry.prediction_values == 2
    assert entry.predicted_periods == ["202501", "202502"]
    assert entry.analytics_rows == 2


# --- #42: dhis2_chap_prediction flow body early-return paths ---------------


def _model_with_two_covariates() -> ChapConfiguredModelWithDataSource:
    """A model fixture with two distinct data sources -- so a probe that
    only returns one of them exercises the missing-covariate branch."""
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
        dataSources=[
            ChapDataSource(covariate="population", dataElementId="POP1"),
            ChapDataSource(covariate="rainfall", dataElementId="RAIN1"),
        ],
        periodType="month",
    )


def test_flow_returns_early_when_dhis2_system_info_fails() -> None:
    """DHIS2 unreachable -> report.dhis2_error set; chap path is not touched."""
    creds = _credentials()
    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_system_info",
            side_effect=ConnectionError("dhis2 down"),
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.check_chap_core") as check_chap,
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_configured_models") as fetch_models,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, CalculatedEnd())
    assert report.dhis2 is None
    assert "ConnectionError: dhis2 down" in (report.dhis2_error or "")
    assert report.chap is None
    assert report.chap_error is None  # never reached
    assert report.entries == []
    check_chap.assert_not_called()
    fetch_models.assert_not_called()


def test_flow_returns_early_when_chap_check_fails() -> None:
    """chap unreachable -> report.chap_error set; the configured-models
    fetch and per-model loop are skipped."""
    creds = _credentials()
    dhis2_info = MagicMock()
    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_system_info",
            return_value=dhis2_info,
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.check_chap_core",
            side_effect=RuntimeError("chap is down"),
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_configured_models") as fetch_models,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, CalculatedEnd())
    assert report.dhis2 is dhis2_info
    assert report.dhis2_error is None
    assert report.chap is None
    assert "RuntimeError: chap is down" in (report.chap_error or "")
    assert report.entries == []
    fetch_models.assert_not_called()


def test_flow_returns_early_when_fetch_configured_models_fails() -> None:
    """The configured-models listing failing -> report.models_error set;
    no per-model entries are produced."""
    creds = _credentials()
    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_system_info",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.check_chap_core",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_configured_models",
            side_effect=RuntimeError("500 internal"),
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, CalculatedEnd())
    assert report.dhis2 is not None
    assert report.chap is not None
    assert "RuntimeError: 500 internal" in (report.models_error or "")
    assert report.entries == []


def test_flow_emits_run_report_artifact_even_when_dhis2_unreachable() -> None:
    """The run-report artifact is in a `finally` so it runs even on the
    early-return paths."""
    creds = _credentials()
    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_system_info",
            side_effect=ConnectionError("dhis2 down"),
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact") as create_artifact,
    ):
        dhis2_chap_prediction.fn(creds, CalculatedEnd())
    create_artifact.assert_called_once()
    kwargs = create_artifact.call_args.kwargs
    assert kwargs.get("key") == "dhis2-chap-prediction-report"
    assert "NOT REACHABLE" in kwargs.get("markdown", "")


# --- #43: _resolve_end_period_for_run missing-covariate branch -------------


def test_resolve_end_period_raises_step_failure_for_missing_covariate() -> None:
    """Probe returns coverage for population but not rainfall ->
    _StepFailure('probe_latest_covariate_periods') with the missing
    covariate name in the cause's message."""
    model = _model_with_two_covariates()
    with patch(
        "chap_scheduler.flows.dhis2_chap_prediction.probe_latest_covariate_periods",
        return_value={"POP1": "202604"},  # RAIN1 absent
    ):
        with pytest.raises(_StepFailure) as excinfo:
            _resolve_end_period_for_run(_credentials(), model, CalculatedEnd())
    assert excinfo.value.step == "probe_latest_covariate_periods"
    cause = excinfo.value.__cause__
    assert isinstance(cause, RuntimeError)
    assert "rainfall" in str(cause)
    assert "LAST_12_MONTHS" in str(cause)


def test_resolve_end_period_uses_explicit_end_date_without_probing() -> None:
    """A FixedEnd short-circuits the probe entirely."""
    from datetime import date as _date

    model = _model_with_two_covariates()
    with patch(
        "chap_scheduler.flows.dhis2_chap_prediction.probe_latest_covariate_periods",
    ) as probe:
        out = _resolve_end_period_for_run(_credentials(), model, FixedEnd(date=_date(2026, 4, 30)))
    probe.assert_not_called()
    assert out == "202604"  # the period covering 2026-04-30 for monthly


# --- #44: fetch_prediction_result two-step lookup --------------------------


def test_fetch_prediction_result_raises_when_job_not_in_listing() -> None:
    """job_description returns None -> 'could not resolve prediction id'."""
    with patch.object(Dhis2Credentials, "chap_client") as mc:
        mc.return_value.__enter__.return_value = mc.return_value
        mc.return_value.job_description.return_value = None
        with pytest.raises(RuntimeError, match="could not resolve prediction id"):
            fetch_prediction_result.fn(_credentials(), "job-abc", "label")


def test_fetch_prediction_result_raises_when_job_has_no_result() -> None:
    """Job exists in listing but result is None (e.g. job still queued
    when somehow this code ran) -> 'could not resolve prediction id'."""
    desc = ChapJobDescription(
        id="job-abc",
        type="make_prediction",
        name="x",
        status="SUCCESS",
        result=None,
    )
    with patch.object(Dhis2Credentials, "chap_client") as mc:
        mc.return_value.__enter__.return_value = mc.return_value
        mc.return_value.job_description.return_value = desc
        with pytest.raises(RuntimeError, match="could not resolve prediction id"):
            fetch_prediction_result.fn(_credentials(), "job-abc", "label")


def test_fetch_prediction_result_raises_when_result_is_not_an_int() -> None:
    """job result is a string that's not parseable as int -> diagnostic
    'is not an int prediction id' chained from the underlying ValueError."""
    desc = ChapJobDescription(
        id="job-abc",
        type="make_prediction",
        name="x",
        status="SUCCESS",
        result="not-an-int",
    )
    with patch.object(Dhis2Credentials, "chap_client") as mc:
        mc.return_value.__enter__.return_value = mc.return_value
        mc.return_value.job_description.return_value = desc
        with pytest.raises(RuntimeError, match="is not an int prediction id") as excinfo:
            fetch_prediction_result.fn(_credentials(), "job-abc", "label")
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_fetch_prediction_result_returns_int_id_and_entries_on_success() -> None:
    """Happy path: job resolves to int prediction id, entries are returned."""
    desc = ChapJobDescription(
        id="job-abc",
        type="make_prediction",
        name="x",
        status="SUCCESS",
        result="42",
    )
    pred_entry = MagicMock()
    pred_entry.period = "202501"
    with patch.object(Dhis2Credentials, "chap_client") as mc:
        mc.return_value.__enter__.return_value = mc.return_value
        mc.return_value.job_description.return_value = desc
        mc.return_value.prediction_entries.return_value = [pred_entry]
        prediction_id, entries = fetch_prediction_result.fn(_credentials(), "job-abc", "label")
    assert prediction_id == 42
    assert entries == [pred_entry]
    # quantiles defaulted from _DEFAULT_QUANTILES.
    args, kwargs = mc.return_value.prediction_entries.call_args
    assert args[0] == 42
    assert kwargs.get("quantiles") == [0.1, 0.25, 0.5, 0.75, 0.9]


# --- end-spec resolver branches -------------------------------------------


def test_resolve_end_period_uses_offset_skips_probe() -> None:
    """When end is an OffsetEnd, the DHIS2 probe must not be called."""
    model = _model_with_two_covariates()
    with patch(
        "chap_scheduler.flows.dhis2_chap_prediction.probe_latest_covariate_periods",
    ) as probe:
        out = _resolve_end_period_for_run(_credentials(), model, OffsetEnd(offset=1))
    probe.assert_not_called()
    # Monthly + offset=1 should match _last_completed_period for the same model;
    # asserting the format here is enough -- the helper-level tests pin the math.
    assert isinstance(out, str)
    assert len(out) == 6  # YYYYMM


# --- flow-entry validation + CMWDS filter ----------------------------------


def test_offset_end_rejects_negative_at_construction() -> None:
    """OffsetEnd's pydantic validator rejects negative offsets at construction
    time, before the flow ever runs."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OffsetEnd(offset=-1)


def _two_models() -> list[ChapConfiguredModelWithDataSource]:
    """Two CMWDS rows with distinct ids, for filter-tests."""
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
        additionalContinuousCovariates=[],
        modelTemplate=template,
    )

    def _row(row_id: int, name: str) -> ChapConfiguredModelWithDataSource:
        return ChapConfiguredModelWithDataSource(
            id=row_id,
            name=name,
            configuredModel=cm,
            startPeriod="202301",
            orgUnits=["OU1"],
            dataSources=[ChapDataSource(covariate="population", dataElementId="POP1")],
            periodType="month",
        )

    return [_row(1, "alpha"), _row(2, "beta")]


def test_flow_runs_only_matching_cmwds_when_id_filter_set() -> None:
    """configured_model_id filters the CMWDS list to a single row before
    iteration; only that row's _run_one_model is invoked."""
    creds = _credentials()
    models = _two_models()
    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_system_info",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.check_chap_core",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_configured_models",
            return_value=models,
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._run_one_model",
        ) as run_one,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, CalculatedEnd(), 2)
    assert run_one.call_count == 1
    passed_model = run_one.call_args.args[1]
    assert passed_model.id == 2
    assert passed_model.name == "beta"
    assert len(report.entries) == 1
    assert report.entries[0].name == "beta"


def test_flow_records_failure_when_configured_model_id_not_in_list() -> None:
    """When configured_model_id matches nothing, the report records a clear
    diagnostic naming the bad id + available ids; no predictions run."""
    creds = _credentials()
    models = _two_models()
    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_system_info",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.check_chap_core",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_configured_models",
            return_value=models,
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._run_one_model",
        ) as run_one,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, CalculatedEnd(), 999)
    run_one.assert_not_called()
    assert report.entries == []
    assert report.models_error is not None
    assert "configured_model_id=999" in report.models_error
    assert "[1, 2]" in report.models_error


def test_flow_processes_all_cmwds_when_filter_is_none() -> None:
    """Default behaviour: the flow iterates every CMWDS row when
    configured_model_id is None."""
    creds = _credentials()
    models = _two_models()
    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_system_info",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.check_chap_core",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_configured_models",
            return_value=models,
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._run_one_model",
        ) as run_one,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, CalculatedEnd(), None)
    assert run_one.call_count == 2
    assert {e.name for e in report.entries} == {"alpha", "beta"}
