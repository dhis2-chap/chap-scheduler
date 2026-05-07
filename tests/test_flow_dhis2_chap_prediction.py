"""Tests for pure-logic helpers in the dhis2-chap-prediction flow."""

from datetime import date

from chap_scheduler.flows.dhis2_chap_prediction import (
    _enumerate_periods,
    _last_completed_period,
    dhis2_chap_prediction,
)


def test_last_completed_period_monthly_within_year() -> None:
    assert _last_completed_period("month", date(2026, 5, 7)) == "202604"


def test_last_completed_period_monthly_across_year_boundary() -> None:
    assert _last_completed_period("month", date(2026, 1, 15)) == "202512"


def test_last_completed_period_yearly() -> None:
    assert _last_completed_period("year", date(2026, 5, 7)) == "2025"


def test_last_completed_period_weekly() -> None:
    # 2026-05-07 is Thu, ISO week 19 of 2026 → previous full week is 18.
    assert _last_completed_period("week", date(2026, 5, 7)) == "2026W18"


def test_enumerate_periods_walks_to_last_completed() -> None:
    # Stops at the last completed period, never reaching the in-progress 202605.
    periods = _enumerate_periods("202601", "month", today=date(2026, 5, 7))
    assert periods == ["202601", "202602", "202603", "202604"]


def test_enumerate_periods_when_start_already_completed() -> None:
    assert _enumerate_periods("202604", "month", today=date(2026, 5, 7)) == ["202604"]


def test_credentials_parameter_renders_block_dropdown_in_ui() -> None:
    # Prefect uses block_type_slug in the parameter schema to render
    # a saved-instance picker in the UI; credentials is required (no default).
    schema = dhis2_chap_prediction.parameters.model_dump()
    assert "credentials" in schema["required"]
    creds_def = schema["definitions"]["Dhis2Credentials"]
    assert creds_def["block_type_slug"] == "chap-dhis2-credentials"
