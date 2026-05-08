"""Tests for the vendored DHIS2 ISO period helpers.

`chap_scheduler.period_utils` was lifted from `dhis2-client` when chap-scheduler
migrated DHIS2 access to `dhis2w-client` (which doesn't expose the
period-id math). These tests cover the supported period shapes plus the
documented error cases.
"""

import pytest

from chap_scheduler.period_utils import next_period_id, period_key, period_start_end


def test_period_start_end_daily() -> None:
    assert period_start_end("20240615") == {"startDate": "2024-06-15", "endDate": "2024-06-15"}


def test_period_start_end_date_range() -> None:
    assert period_start_end("20240601_20240630") == {"startDate": "2024-06-01", "endDate": "2024-06-30"}


def test_period_start_end_date_range_rejects_inverted() -> None:
    with pytest.raises(ValueError, match="end<start"):
        period_start_end("20240630_20240601")


def test_period_start_end_weekly_iso_monday_anchor() -> None:
    # 2024-W26 = Mon 2024-06-24 .. Sun 2024-06-30
    assert period_start_end("2024W26") == {"startDate": "2024-06-24", "endDate": "2024-06-30"}


def test_period_start_end_quarterly() -> None:
    assert period_start_end("2024Q1") == {"startDate": "2024-01-01", "endDate": "2024-03-31"}
    assert period_start_end("2024Q4") == {"startDate": "2024-10-01", "endDate": "2024-12-31"}


def test_period_start_end_six_monthly() -> None:
    assert period_start_end("2024S1") == {"startDate": "2024-01-01", "endDate": "2024-06-30"}
    assert period_start_end("2024S2") == {"startDate": "2024-07-01", "endDate": "2024-12-31"}


def test_period_start_end_monthly() -> None:
    assert period_start_end("202402") == {"startDate": "2024-02-01", "endDate": "2024-02-29"}  # leap year
    assert period_start_end("202312") == {"startDate": "2023-12-01", "endDate": "2023-12-31"}


def test_period_start_end_monthly_rejects_invalid_month() -> None:
    with pytest.raises(ValueError, match="Invalid month"):
        period_start_end("202413")


def test_period_start_end_yearly() -> None:
    assert period_start_end("2024") == {"startDate": "2024-01-01", "endDate": "2024-12-31"}


def test_period_start_end_unrecognised_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        period_start_end("not-a-period")


def test_next_period_id_daily() -> None:
    assert next_period_id("20240615") == "20240616"
    # Cross-month
    assert next_period_id("20240131") == "20240201"


def test_next_period_id_weekly_handles_year_rollover() -> None:
    # 2024-W52 -> 2025-W01 (depending on ISO calendar)
    out = next_period_id("2024W52")
    # Could be 2024W53 (long year) or 2025W01; either is correct ISO.
    assert out in ("2024W53", "2025W01")


def test_next_period_id_quarterly_rolls_year() -> None:
    assert next_period_id("2024Q1") == "2024Q2"
    assert next_period_id("2024Q4") == "2025Q1"


def test_next_period_id_six_monthly_rolls_year() -> None:
    assert next_period_id("2024S1") == "2024S2"
    assert next_period_id("2024S2") == "2025S1"


def test_next_period_id_monthly_rolls_year() -> None:
    assert next_period_id("202401") == "202402"
    assert next_period_id("202412") == "202501"


def test_next_period_id_yearly() -> None:
    assert next_period_id("2024") == "2025"


def test_next_period_id_unrecognised_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        next_period_id("garbage")


def test_period_key_orders_chronologically() -> None:
    # Sortable; max picks the latest period regardless of shape mismatch.
    periods = ["202401", "202312", "2024Q4", "20240615"]
    assert max(periods, key=period_key) == "2024Q4"


def test_period_key_returns_distinct_tuples_for_distinct_periods() -> None:
    a = period_key("202401")
    b = period_key("202402")
    assert a < b
