"""Tests for pure-logic helpers in the dhis2-chap-prediction flow."""

from datetime import date

from chap_scheduler.flows.dhis2_chap_prediction import (
    _current_period,
    _enumerate_periods,
    dhis2_chap_prediction,
)


def test_current_period_monthly() -> None:
    assert _current_period("month", date(2026, 5, 7)) == "202605"


def test_current_period_yearly() -> None:
    assert _current_period("year", date(2026, 5, 7)) == "2026"


def test_enumerate_periods_walks_to_today() -> None:
    periods = _enumerate_periods("202601", "month", today=date(2026, 5, 7))
    assert periods == ["202601", "202602", "202603", "202604", "202605"]


def test_enumerate_periods_handles_start_equals_today() -> None:
    assert _enumerate_periods("202605", "month", today=date(2026, 5, 7)) == ["202605"]


def test_credentials_parameter_renders_block_dropdown_in_ui() -> None:
    # Prefect uses block_type_slug in the parameter schema to render
    # a saved-instance picker in the UI; credentials is required (no default).
    schema = dhis2_chap_prediction.parameters.model_dump()
    assert "credentials" in schema["required"]
    creds_def = schema["definitions"]["Dhis2Credentials"]
    assert creds_def["block_type_slug"] == "chap-dhis2-credentials"
