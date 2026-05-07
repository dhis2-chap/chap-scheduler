from datetime import datetime, timezone

from chap_scheduler.chap import (
    ChapMissingValuesDetail,
    ChapSystemInfo,
    Dhis2SystemInfo,
    ModelRunEntry,
    RunReport,
    render_report,
)


def _started() -> datetime:
    return datetime(2026, 5, 7, 11, 38, 23, tzinfo=timezone.utc)


def _finished() -> datetime:
    return datetime(2026, 5, 7, 11, 38, 37, tzinfo=timezone.utc)


def _ok_dhis2() -> Dhis2SystemInfo:
    return Dhis2SystemInfo.model_validate(
        {
            "version": "2.44-SNAPSHOT",
            "systemName": "DHIS 2 Demo - Sierra Leone",
        }
    )


def _ok_chap() -> ChapSystemInfo:
    return ChapSystemInfo.model_validate(
        {
            "chap_core_version": "2.0.0.dev1",
            "python_version": "3.13.12",
            "server_date": "2026-05-07T11:38:23+00:00",
            "server_time_zone_id": "Etc/UTC",
        }
    )


def test_renders_chap_unreachable_short_circuit() -> None:
    report = RunReport(
        dhis2_url="http://dhis.example.org",
        started_at=_started(),
        dhis2=_ok_dhis2(),
        chap_error="ConnectError: Connection refused",
    )
    md = render_report(report, finished_at=_finished())
    assert "## DHIS2" in md
    assert "DHIS2 2.44-SNAPSHOT" in md
    assert "## chap-core" in md
    assert "NOT REACHABLE" in md
    assert "ConnectError" in md
    # Per-model section should be absent
    assert "Configured models" not in md


def test_renders_dhis2_unreachable_short_circuits_before_chap() -> None:
    report = RunReport(
        dhis2_url="http://dhis.example.org",
        started_at=_started(),
        dhis2_error="ConnectError: dhis2 is down",
    )
    md = render_report(report, finished_at=_finished())
    assert "## DHIS2" in md
    assert "NOT REACHABLE" in md
    assert "ConnectError: dhis2 is down" in md
    # chap section is skipped because DHIS2 is the gateway
    assert "## chap-core" not in md


def test_renders_mixed_success_and_failure() -> None:
    report = RunReport(
        dhis2_url="http://dhis.example.org",
        started_at=_started(),
        dhis2=_ok_dhis2(),
        chap=_ok_chap(),
        entries=[
            ModelRunEntry(
                name="test",
                template_name="chapkit-ewars-model",
                status="succeeded",
                job_id="abc-123",
                analytics_rows=2356,
                org_units_covered=18,
                periods_covered=40,
                prediction_values=540,
            ),
            ModelRunEntry(
                name="rwanda",
                template_name="chapkit-rwanda-bym-model",
                step_failed="submit_prediction",
                error="ApiError: 422 Validation",
            ),
        ],
    )
    md = render_report(report, finished_at=_finished())
    assert "REACHABLE" in md
    assert "2.0.0.dev1" in md
    assert "1 succeeded, 1 failed" in md
    assert "SUCCEEDED" in md
    assert "FAILED at `submit_prediction`" in md
    assert "ApiError: 422 Validation" in md
    assert "abc-123" in md
    assert "Org units: 18" in md
    assert "Analytics rows fetched: 2,356" in md


def test_renders_models_listing_failure() -> None:
    report = RunReport(
        dhis2_url="http://dhis.example.org",
        started_at=_started(),
        dhis2=_ok_dhis2(),
        chap=_ok_chap(),
        models_error="HTTPStatusError: 500",
    )
    md = render_report(report, finished_at=_finished())
    assert "Could not list configured models" in md
    assert "HTTPStatusError: 500" in md


def test_renders_rejection_detail_per_covariate_summary() -> None:
    detail = ChapMissingValuesDetail.model_validate(
        {
            "message": "All regions rejected due to missing values",
            "imported_count": 0,
            "rejected": [
                {
                    "reason": "Missing value for some/all time periods",
                    "orgUnit": "OU1",
                    "featureName": "rainfall",
                    "timePeriods": ["202510", "202511", "202512"],
                },
                {
                    "reason": "Missing value for some/all time periods",
                    "orgUnit": "OU2",
                    "featureName": "rainfall",
                    "timePeriods": ["202510", "202511", "202512"],
                },
                {
                    "reason": "Missing value for some/all time periods",
                    "orgUnit": "OU1",
                    "featureName": "mean_temperature",
                    "timePeriods": ["202512"],
                },
            ],
        }
    )
    report = RunReport(
        dhis2_url="http://dhis.example.org",
        started_at=_started(),
        dhis2=_ok_dhis2(),
        chap=_ok_chap(),
        entries=[
            ModelRunEntry(
                name="test",
                template_name="chapkit-ewars-model",
                step_failed="submit_prediction",
                error="ChapHttpError: ...",
                rejection_detail=detail,
            )
        ],
    )
    md = render_report(report, finished_at=_finished())
    # Per-covariate summary instead of the giant raw blob
    assert "All regions rejected due to missing values" in md
    assert "imported 0" in md
    assert "`rainfall`: 2 org units" in md
    assert "`mean_temperature`: 1 org units" in md
    assert "202510, 202511, 202512" in md
    # Original raw error blob should NOT be in the rendered output
    assert "ChapHttpError: ..." not in md


def test_renders_no_configured_models() -> None:
    report = RunReport(
        dhis2_url="http://dhis.example.org",
        started_at=_started(),
        dhis2=_ok_dhis2(),
        chap=_ok_chap(),
        entries=[],
    )
    md = render_report(report, finished_at=_finished())
    assert "0 succeeded, 0 failed" in md
    assert "No configured models" in md
