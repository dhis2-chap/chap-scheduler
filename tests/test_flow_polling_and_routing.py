"""Unit tests for ``wait_for_prediction``'s polling loop and ``_run_one_setup``'s
per-step error routing.

These exercise the parts of the flow that are most likely to silently regress:

- ``wait_for_prediction``: transient-status retry, terminal-status detection
  (case-insensitive), and the timeout path.
- ``_run_one_setup`` -> ``_StepFailure``: each step's failure must surface
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
    ChapDataSource,
    ChapHttpError,
    ChapJobDescription,
    ChapModelTemplate,
    ChapPredictionSetup,
)
from chap_scheduler.blocks.dhis2 import Dhis2Credentials
from chap_scheduler.flows.dhis2_chap_prediction import (
    _populate_entry_from_step_failure,
    _resolve_end_period_for_run,
    _run_one_setup,
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


def _setup_fixture() -> ChapPredictionSetup:
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
    return ChapPredictionSetup(
        id=1,
        name="test",
        backtestId=7,
        configuredModel=cm,
        startPeriod="202301",
        orgUnits=["OU1"],
        covariateSources=[ChapDataSource(covariate="population", dataElementId="POP1")],
        periodType="month",
    )


# --- wait_for_prediction delegation ---------------------------------------
#
# The polling-loop logic itself lives in `chap_client.ChapClient.wait_for_job`
# (mitigates `CHAP_CORE_ISSUES.md` finding #7); see
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
            "setup-label",
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


# --- _populate_entry_from_step_failure --------------------------------


def test_populate_entry_records_step_label_and_cause_message() -> None:
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    cause = RuntimeError("boom")
    failure = _StepFailure("run_prediction_setup")
    failure.__cause__ = cause

    _populate_entry_from_step_failure(entry, failure)

    assert entry.step_failed == "run_prediction_setup"
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
        path="/v1/crud/prediction-setups/7/run",
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
    failure = _StepFailure("run_prediction_setup")
    failure.__cause__ = chap_error

    _populate_entry_from_step_failure(entry, failure)

    assert entry.step_failed == "run_prediction_setup"
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


# --- _run_one_setup exception routing --------------------------------------


def _patch_step(name: str, side_effect: Any) -> Any:
    """Helper: patch a flow-module symbol with a Mock that raises the given exception."""
    return patch(f"chap_scheduler.flows.dhis2_chap_prediction.{name}", side_effect=side_effect)


def test_run_one_setup_labels_resolve_end_period_failure() -> None:
    """Probe / end-period resolution failures are surfaced verbatim (already a _StepFailure)."""
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    inner_failure = _StepFailure("probe_latest_covariate_periods")
    inner_failure.__cause__ = RuntimeError("missing covariate")

    with _patch_step("_resolve_end_period_for_run", inner_failure):
        with pytest.raises(_StepFailure) as excinfo:
            _run_one_setup(
                _credentials(),
                _setup_fixture(),
                entry,
                end_mode="calculated",
                end_date=None,
                end_period_offset=None,
                prediction_timeout_seconds=600,
            )
    assert excinfo.value.step == "probe_latest_covariate_periods"


def test_run_one_setup_labels_validate_period_range_for_start_after_end() -> None:
    """If the configured start_period is after the resolved end, fail with
    `validate_period_range` (not as a fetch failure)."""
    setup = _setup_fixture()
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")

    with patch(
        "chap_scheduler.flows.dhis2_chap_prediction._resolve_end_period_for_run",
        return_value="202012",  # Earlier than start_period 202301 -> validate_period_range
    ):
        with pytest.raises(_StepFailure) as excinfo:
            _run_one_setup(
                _credentials(),
                setup,
                entry,
                end_mode="calculated",
                end_date=None,
                end_period_offset=None,
                prediction_timeout_seconds=600,
            )
    assert excinfo.value.step == "validate_period_range"


def test_run_one_setup_labels_fetch_dhis2_failure() -> None:
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._resolve_end_period_for_run",
            return_value="202412",
        ),
        _patch_step("fetch_dhis2_for_setup", RuntimeError("dhis2 boom")),
    ):
        with pytest.raises(_StepFailure) as excinfo:
            _run_one_setup(
                _credentials(),
                _setup_fixture(),
                entry,
                end_mode="calculated",
                end_date=None,
                end_period_offset=None,
                prediction_timeout_seconds=600,
            )
    assert excinfo.value.step == "fetch_dhis2_for_setup"
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_run_one_setup_labels_run_prediction_setup_failure() -> None:
    """Mock the chain up to run_prediction_setup so the failure surfaces at the
    right step."""
    entry = ModelRunEntry(name="test", template_name="chapkit-ewars-model")
    analytics = MagicMock()
    analytics.rows = []  # entry.analytics_rows is set from len(...)

    with (
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._resolve_end_period_for_run",
            return_value="202412",
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_for_setup", return_value=analytics),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_org_units_geojson",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.build_prediction_request",
            return_value=MagicMock(),
        ),
        _patch_step("run_prediction_setup", RuntimeError("chap rejected")),
    ):
        with pytest.raises(_StepFailure) as excinfo:
            _run_one_setup(
                _credentials(),
                _setup_fixture(),
                entry,
                end_mode="calculated",
                end_date=None,
                end_period_offset=None,
                prediction_timeout_seconds=600,
            )
    assert excinfo.value.step == "run_prediction_setup"


def test_run_one_setup_translates_non_success_terminal_status_to_step_failure() -> None:
    """If wait_for_prediction returns FAILED (terminal but not SUCCESS),
    `_run_one_setup` raises _StepFailure('wait_for_prediction')."""
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
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_for_setup", return_value=analytics),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_org_units_geojson",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.build_prediction_request",
            return_value=MagicMock(),
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.run_prediction_setup", return_value=job_response),
        patch("chap_scheduler.flows.dhis2_chap_prediction.wait_for_prediction", return_value="FAILED"),
    ):
        with pytest.raises(_StepFailure) as excinfo:
            _run_one_setup(
                _credentials(),
                _setup_fixture(),
                entry,
                end_mode="calculated",
                end_date=None,
                end_period_offset=None,
                prediction_timeout_seconds=600,
            )
    assert excinfo.value.step == "wait_for_prediction"
    assert entry.job_id == "job-456"


def test_run_one_setup_succeeds_end_to_end_with_all_steps_mocked() -> None:
    """Happy path: every step returns OK, _run_one_setup returns None and
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
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_for_setup", return_value=analytics),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_org_units_geojson",
            return_value=MagicMock(),
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.build_prediction_request",
            return_value=MagicMock(),
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.run_prediction_setup", return_value=job_response),
        patch("chap_scheduler.flows.dhis2_chap_prediction.wait_for_prediction", return_value="SUCCESS"),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_prediction_result",
            return_value=(42, [pred_entry_a, pred_entry_b]),
        ),
    ):
        _run_one_setup(
            _credentials(),
            _setup_fixture(),
            entry,
            end_mode="calculated",
            end_date=None,
            end_period_offset=None,
            prediction_timeout_seconds=600,
        )
    assert entry.job_id == "job-456"
    assert entry.prediction_id == 42
    assert entry.prediction_values == 2
    assert entry.predicted_periods == ["202501", "202502"]
    assert entry.analytics_rows == 2


# --- dhis2_chap_prediction flow body early-return paths --------------------


def _setup_with_two_covariates() -> ChapPredictionSetup:
    """A setup fixture with two distinct covariate sources -- so a probe that
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
    return ChapPredictionSetup(
        id=1,
        name="test",
        backtestId=7,
        configuredModel=cm,
        startPeriod="202301",
        orgUnits=["OU1"],
        covariateSources=[
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
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_prediction_setups") as fetch_setups,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds)
    assert report.dhis2 is None
    assert "ConnectionError: dhis2 down" in (report.dhis2_error or "")
    assert report.chap is None
    assert report.chap_error is None  # never reached
    assert report.entries == []
    check_chap.assert_not_called()
    fetch_setups.assert_not_called()


def test_flow_returns_early_when_chap_check_fails() -> None:
    """chap unreachable -> report.chap_error set; the prediction-setups
    fetch and per-setup loop are skipped."""
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
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_prediction_setups") as fetch_setups,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds)
    assert report.dhis2 is dhis2_info
    assert report.dhis2_error is None
    assert report.chap is None
    assert "RuntimeError: chap is down" in (report.chap_error or "")
    assert report.entries == []
    fetch_setups.assert_not_called()


def test_flow_returns_early_when_fetch_prediction_setups_fails() -> None:
    """The prediction-setups listing failing -> report.models_error set;
    no per-setup entries are produced."""
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
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_prediction_setups",
            side_effect=RuntimeError("500 internal"),
        ),
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds)
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
        dhis2_chap_prediction.fn(creds)
    create_artifact.assert_called_once()
    kwargs = create_artifact.call_args.kwargs
    assert kwargs.get("key") == "dhis2-chap-prediction-report"
    assert "NOT REACHABLE" in kwargs.get("markdown", "")


# --- _resolve_end_period_for_run missing-covariate branch ------------------


def test_resolve_end_period_raises_step_failure_for_missing_covariate() -> None:
    """Probe returns coverage for population but not rainfall ->
    _StepFailure('probe_latest_covariate_periods') with the missing
    covariate name in the cause's message."""
    setup = _setup_with_two_covariates()
    with patch(
        "chap_scheduler.flows.dhis2_chap_prediction.probe_latest_covariate_periods",
        return_value={"POP1": "202604"},  # RAIN1 absent
    ):
        with pytest.raises(_StepFailure) as excinfo:
            _resolve_end_period_for_run(_credentials(), setup, "calculated", None, None)
    assert excinfo.value.step == "probe_latest_covariate_periods"
    cause = excinfo.value.__cause__
    assert isinstance(cause, RuntimeError)
    assert "rainfall" in str(cause)
    assert "LAST_12_MONTHS" in str(cause)


def test_resolve_end_period_uses_explicit_end_date_without_probing() -> None:
    """end_mode='fixed' short-circuits the probe entirely."""
    from datetime import date as _date

    setup = _setup_with_two_covariates()
    with patch(
        "chap_scheduler.flows.dhis2_chap_prediction.probe_latest_covariate_periods",
    ) as probe:
        out = _resolve_end_period_for_run(_credentials(), setup, "fixed", _date(2026, 4, 30), None)
    probe.assert_not_called()
    assert out == "202604"  # the period covering 2026-04-30 for monthly


# --- fetch_prediction_result two-step lookup --------------------------


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


# --- end-mode resolver branches -------------------------------------------


def test_resolve_end_period_uses_offset_skips_probe() -> None:
    """When end_mode='offset', the DHIS2 probe must not be called."""
    setup = _setup_with_two_covariates()
    with patch(
        "chap_scheduler.flows.dhis2_chap_prediction.probe_latest_covariate_periods",
    ) as probe:
        out = _resolve_end_period_for_run(_credentials(), setup, "offset", None, 1)
    probe.assert_not_called()
    # Monthly + offset=1 should match _last_completed_period for the same setup;
    # asserting the format here is enough -- the helper-level tests pin the math.
    assert isinstance(out, str)
    assert len(out) == 6  # YYYYMM


def test_resolve_end_period_fixed_mode_requires_end_date() -> None:
    """Defensive: resolver rejects fixed mode without an end_date (the flow
    body's pre-check is the primary guard)."""
    setup = _setup_with_two_covariates()
    with pytest.raises(ValueError, match="end_date"):
        _resolve_end_period_for_run(_credentials(), setup, "fixed", None, None)


def test_resolve_end_period_offset_mode_requires_end_period_offset() -> None:
    """Defensive: resolver rejects offset mode without an end_period_offset."""
    setup = _setup_with_two_covariates()
    with pytest.raises(ValueError, match="end_period_offset"):
        _resolve_end_period_for_run(_credentials(), setup, "offset", None, None)


# --- flow-entry validation + prediction-setup filter -----------------------


def test_flow_rejects_fixed_mode_without_end_date() -> None:
    """If end_mode='fixed' but end_date is None, the flow raises at entry."""
    creds = _credentials()
    with (
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_system_info") as fetch_dhis2,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        with pytest.raises(ValueError, match="end_date"):
            dhis2_chap_prediction.fn(creds, "fixed", None, None, None)
    fetch_dhis2.assert_not_called()


def test_flow_rejects_offset_mode_without_end_period_offset() -> None:
    """If end_mode='offset' but end_period_offset is None, the flow raises."""
    creds = _credentials()
    with (
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_system_info") as fetch_dhis2,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        with pytest.raises(ValueError, match="end_period_offset"):
            dhis2_chap_prediction.fn(creds, "offset", None, None, None)
    fetch_dhis2.assert_not_called()


def test_flow_rejects_negative_end_period_offset() -> None:
    """end_period_offset < 0 implies a future end period and is rejected."""
    creds = _credentials()
    with (
        patch("chap_scheduler.flows.dhis2_chap_prediction.fetch_dhis2_system_info") as fetch_dhis2,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        with pytest.raises(ValueError, match=">= 0"):
            dhis2_chap_prediction.fn(creds, "offset", None, -1, None)
    fetch_dhis2.assert_not_called()


def _two_setups() -> list[ChapPredictionSetup]:
    """Two prediction-setup rows with distinct ids, for filter-tests."""
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

    def _row(row_id: int, name: str, schedule_enabled: bool = True) -> ChapPredictionSetup:
        return ChapPredictionSetup(
            id=row_id,
            name=name,
            backtestId=row_id + 100,
            configuredModel=cm,
            startPeriod="202301",
            orgUnits=["OU1"],
            covariateSources=[ChapDataSource(covariate="population", dataElementId="POP1")],
            periodType="month",
            scheduleEnabled=schedule_enabled,
        )

    return [_row(1, "alpha"), _row(2, "beta")]


def test_flow_runs_only_matching_setup_when_id_filter_set() -> None:
    """prediction_setup_id filters the setup list to a single row before
    iteration; only that row's _run_one_setup is invoked."""
    creds = _credentials()
    setups = _two_setups()
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
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_prediction_setups",
            return_value=setups,
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._run_one_setup",
        ) as run_one,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, "calculated", None, None, 2)
    assert run_one.call_count == 1
    passed_setup = run_one.call_args.args[1]
    assert passed_setup.id == 2
    assert passed_setup.name == "beta"
    assert len(report.entries) == 1
    assert report.entries[0].name == "beta"


def test_flow_records_failure_when_prediction_setup_id_not_in_list() -> None:
    """When prediction_setup_id matches nothing, the report records a clear
    diagnostic naming the bad id + available ids; no predictions run."""
    creds = _credentials()
    setups = _two_setups()
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
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_prediction_setups",
            return_value=setups,
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._run_one_setup",
        ) as run_one,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, "calculated", None, None, 999)
    run_one.assert_not_called()
    assert report.entries == []
    assert report.models_error is not None
    assert "prediction_setup_id=999" in report.models_error
    assert "[1, 2]" in report.models_error


def test_flow_processes_all_setups_when_filter_is_none() -> None:
    """Default behaviour: the flow iterates every prediction-setup row when
    prediction_setup_id is None."""
    creds = _credentials()
    setups = _two_setups()
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
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_prediction_setups",
            return_value=setups,
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._run_one_setup",
        ) as run_one,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, "calculated", None, None, None)
    assert run_one.call_count == 2
    assert {e.name for e in report.entries} == {"alpha", "beta"}


# --- schedule_enabled filter -----------------------------------------------


def _two_setups_one_disabled() -> list[ChapPredictionSetup]:
    """One enabled + one disabled setup, sharing the rest of the shape with `_two_setups()`."""
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

    def _row(row_id: int, name: str, *, enabled: bool) -> ChapPredictionSetup:
        return ChapPredictionSetup(
            id=row_id,
            name=name,
            backtestId=row_id + 100,
            configuredModel=cm,
            startPeriod="202301",
            orgUnits=["OU1"],
            covariateSources=[ChapDataSource(covariate="population", dataElementId="POP1")],
            periodType="month",
            scheduleEnabled=enabled,
        )

    return [_row(1, "alpha", enabled=True), _row(2, "beta-disabled", enabled=False)]


def test_flow_skips_disabled_setups_when_no_id_filter() -> None:
    """Without an explicit ``prediction_setup_id``, disabled setups are
    skipped (chap-core treats ``schedule_enabled`` as informational --
    chap-scheduler is the consumer that honors it)."""
    creds = _credentials()
    setups = _two_setups_one_disabled()
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
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_prediction_setups",
            return_value=setups,
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._run_one_setup",
        ) as run_one,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, "calculated", None, None, None)
    assert run_one.call_count == 1
    passed_setup = run_one.call_args.args[1]
    assert passed_setup.id == 1
    assert passed_setup.name == "alpha"
    assert [e.name for e in report.entries] == ["alpha"]


def test_flow_runs_disabled_setup_when_explicitly_selected_by_id() -> None:
    """An explicit ``prediction_setup_id`` overrides ``schedule_enabled``
    -- manual / debug runs against a disabled setup must still work."""
    creds = _credentials()
    setups = _two_setups_one_disabled()
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
            "chap_scheduler.flows.dhis2_chap_prediction.fetch_prediction_setups",
            return_value=setups,
        ),
        patch(
            "chap_scheduler.flows.dhis2_chap_prediction._run_one_setup",
        ) as run_one,
        patch("chap_scheduler.flows.dhis2_chap_prediction.create_markdown_artifact"),
    ):
        report = dhis2_chap_prediction.fn(creds, "calculated", None, None, 2)
    assert run_one.call_count == 1
    passed_setup = run_one.call_args.args[1]
    assert passed_setup.id == 2
    assert passed_setup.schedule_enabled is False
    assert [e.name for e in report.entries] == ["beta-disabled"]
