"""Tests for the pure-logic tasks in the dhis2-chap-prediction flow.

We unwrap the @task decorator via ``.fn`` so the tests don't need a Prefect
server in the loop.
"""

from chap_scheduler.flows.dhis2_chap_prediction import dhis2_chap_prediction, predict_next_period, summarize


def _row(period: str, value: str) -> list[str]:
    return ["fbfJHSPpUQD", period, "ImspTQPwCqd", value]


def test_summarize_empty() -> None:
    out = summarize.fn([])
    assert out == {"count": 0, "sum": 0.0, "mean": 0.0, "min": 0.0, "max": 0.0}


def test_summarize_basic() -> None:
    rows = [_row("202601", "10"), _row("202602", "20"), _row("202603", "30")]
    out = summarize.fn(rows)
    assert out["count"] == 3
    assert out["sum"] == 60.0
    assert out["mean"] == 20.0
    assert out["min"] == 10.0
    assert out["max"] == 30.0


def test_predict_next_period_uses_trailing_window() -> None:
    rows = [
        _row("202601", "100"),
        _row("202602", "200"),
        _row("202603", "10"),
        _row("202604", "20"),
        _row("202605", "30"),
    ]
    # Mean of the last 3 sorted ascending → (10 + 20 + 30) / 3 == 20
    assert predict_next_period.fn(rows, window=3) == 20.0


def test_predict_next_period_empty() -> None:
    assert predict_next_period.fn([], window=3) == 0.0


def test_credentials_parameter_renders_block_dropdown_in_ui() -> None:
    # Prefect uses block_type_slug in the parameter schema to render
    # a saved-instance picker in the UI; credentials is required (no default).
    schema = dhis2_chap_prediction.parameters.model_dump()
    assert "credentials" in schema["required"]
    creds_def = schema["definitions"]["Dhis2Credentials"]
    assert creds_def["block_type_slug"] == "chap-dhis2-credentials"


def test_predict_next_period_unsorted_input() -> None:
    # Out-of-order rows must be sorted by period before windowing.
    rows = [
        _row("202605", "30"),
        _row("202601", "100"),
        _row("202604", "20"),
        _row("202602", "200"),
        _row("202603", "10"),
    ]
    assert predict_next_period.fn(rows, window=2) == 25.0
