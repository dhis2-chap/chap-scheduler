"""Tests for pure-logic helpers in the dhis2-chap-prediction flow."""

from datetime import date
from typing import Any

from geojson_pydantic import Feature, FeatureCollection

from chap_scheduler.chap import (
    ChapConfiguredModel,
    ChapConfiguredModelWithDataSource,
    ChapDataSource,
    ChapModelTemplate,
)
from chap_scheduler.flows.dhis2_chap_prediction import (
    _build_feature,
    _default_n_periods_for,
    _default_prediction_name,
    _enumerate_periods,
    _last_completed_period,
    _period_covering,
    _resolve_end_period,
    _safe_end_period,
    build_prediction_request,
    dhis2_chap_prediction,
)


def _row(dx: str, period: str, value: str) -> list[str]:
    return [dx, period, "OU1", value]


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
        orgUnits=["OU1", "OU2"],
        dataSources=[
            ChapDataSource(covariate="population", dataElementId="POP1"),
            ChapDataSource(covariate="rainfall", dataElementId="RAIN1"),
            ChapDataSource(covariate="disease_cases", dataElementId="DISEASE1"),
        ],
        periodType="month",
    )


# --- period helpers --------------------------------------------------------


def test_last_completed_period_monthly_within_year() -> None:
    assert _last_completed_period("month", date(2026, 5, 7)) == "202604"


def test_last_completed_period_monthly_across_year_boundary() -> None:
    assert _last_completed_period("month", date(2026, 1, 15)) == "202512"


def test_last_completed_period_yearly() -> None:
    assert _last_completed_period("year", date(2026, 5, 7)) == "2025"


def test_last_completed_period_weekly() -> None:
    assert _last_completed_period("week", date(2026, 5, 7)) == "2026W18"


def test_enumerate_periods_walks_to_last_completed() -> None:
    periods = _enumerate_periods("202601", "month", today=date(2026, 5, 7))
    assert periods == ["202601", "202602", "202603", "202604"]


def test_enumerate_periods_when_start_already_completed() -> None:
    assert _enumerate_periods("202604", "month", today=date(2026, 5, 7)) == ["202604"]


# --- end-date override -----------------------------------------------------


def test_period_covering_monthly() -> None:
    assert _period_covering(date(2024, 12, 31), "month") == "202412"
    assert _period_covering(date(2024, 12, 1), "month") == "202412"


def test_period_covering_yearly_and_weekly() -> None:
    assert _period_covering(date(2024, 12, 31), "year") == "2024"
    # 2024-12-31 is Tue, ISO week 1 of 2025
    assert _period_covering(date(2024, 12, 31), "week") == "2025W01"


def test_resolve_end_period_prefers_explicit_end_date() -> None:
    # With end_date set, today is irrelevant.
    assert _resolve_end_period("month", end_date=date(2024, 12, 31), today=date(2026, 5, 7)) == "202412"


def test_resolve_end_period_falls_back_to_last_completed_when_not_set() -> None:
    assert _resolve_end_period("month", end_date=None, today=date(2026, 5, 7)) == "202604"


def test_enumerate_periods_with_end_date_includes_period_covering_it() -> None:
    periods = _enumerate_periods("202410", "month", end_date=date(2024, 12, 31))
    assert periods == ["202410", "202411", "202412"]


def test_enumerate_periods_explicit_end_period_overrides_end_date() -> None:
    # end_period (string) wins over end_date when both are provided.
    periods = _enumerate_periods("202410", "month", end_period="202411", end_date=date(2024, 12, 31))
    assert periods == ["202410", "202411"]


# --- safe-end-period from probe -------------------------------------------


def test_safe_end_period_returns_min_when_all_expected_covered() -> None:
    latest = {"POP1": "202602", "RAIN1": "202601", "DISEASE1": "202604"}
    expected = ["POP1", "RAIN1", "DISEASE1"]
    assert _safe_end_period(latest, expected) == "202601"


def test_safe_end_period_returns_none_when_a_covariate_is_missing() -> None:
    # RAIN1 has no probe data -- partial coverage must NOT pick "the min of what
    # we got"; that would silently submit incomplete input to chap.
    latest = {"POP1": "202602", "DISEASE1": "202604"}
    expected = ["POP1", "RAIN1", "DISEASE1"]
    assert _safe_end_period(latest, expected) is None


def test_safe_end_period_returns_none_when_probe_empty() -> None:
    assert _safe_end_period({}, ["POP1"]) is None


def test_safe_end_period_returns_none_when_no_expected_ids() -> None:
    # Defensive: nothing to compare against -> no safe choice.
    assert _safe_end_period({"POP1": "202602"}, []) is None


def test_safe_end_period_ignores_extra_data_elements_in_probe() -> None:
    # Probe returned an extra DE we don't care about; that's fine, we still
    # take the min over the expected subset only.
    latest = {"POP1": "202602", "RAIN1": "202601", "EXTRA": "202412"}
    expected = ["POP1", "RAIN1"]
    assert _safe_end_period(latest, expected) == "202601"


# --- n_periods default (per-model) ------------------------------------------


def test_default_n_periods_for_month() -> None:
    assert _default_n_periods_for(_model_fixture()) == 3


def test_default_n_periods_for_unknown_period_type_falls_back_to_three() -> None:
    model = _model_fixture()
    model.period_type = "fortnight"
    assert _default_n_periods_for(model) == 3


# --- prediction-name helper -------------------------------------------------


def test_default_prediction_name_includes_explicit_end_date_range() -> None:
    name = _default_prediction_name(_model_fixture(), end_date=date(2024, 12, 31))
    assert name == "test (chapkit-ewars-model) 202301-202412"


def test_default_prediction_name_uses_end_period_when_supplied_directly() -> None:
    # Probe-driven end period takes priority over end_date.
    name = _default_prediction_name(_model_fixture(), end_period="202410", end_date=date(2024, 12, 31))
    assert name == "test (chapkit-ewars-model) 202301-202410"


# --- build_prediction_request -----------------------------------------------


def test_build_prediction_request_maps_dx_to_covariate_via_data_sources() -> None:
    model = _model_fixture()
    analytics = {
        "headers": [],
        "rows": [
            _row("POP1", "202301", "1000"),
            _row("RAIN1", "202301", "120.5"),
            _row("DISEASE1", "202301", "42"),
        ],
    }
    geojson: FeatureCollection[Feature[Any, dict[str, Any]]] = FeatureCollection(type="FeatureCollection", features=[])
    req = build_prediction_request(model, analytics, geojson, n_periods=3, dataset_type="forecasting", name="run-1")
    assert req.configured_model_with_data_source_id == 1
    assert req.n_periods == 3
    assert req.type == "forecasting"
    assert req.data_to_be_fetched == []
    feature_names = sorted(o.feature_name for o in req.provided_data)
    assert feature_names == ["disease_cases", "population", "rainfall"]
    by_covariate = {o.feature_name: o.value for o in req.provided_data}
    assert by_covariate["population"] == 1000.0
    assert by_covariate["rainfall"] == 120.5


def test_build_prediction_request_drops_unknown_dx_and_bad_values() -> None:
    model = _model_fixture()
    analytics = {
        "rows": [
            _row("POP1", "202301", "1000"),
            _row("UNKNOWN", "202301", "1"),  # dropped: dx not in dataSources
            _row("RAIN1", "202301", ""),  # dropped: empty value
            _row("RAIN1", "202301", "not a number"),  # dropped: non-numeric
        ],
    }
    geojson: FeatureCollection[Feature[Any, dict[str, Any]]] = FeatureCollection(type="FeatureCollection", features=[])
    req = build_prediction_request(model, analytics, geojson, n_periods=3, dataset_type="forecasting", name="run-1")
    assert len(req.provided_data) == 1
    assert req.provided_data[0].feature_name == "population"


def test_build_prediction_request_serialises_with_camelcase_aliases() -> None:
    model = _model_fixture()
    analytics = {"rows": [_row("POP1", "202301", "1")]}
    geojson: FeatureCollection[Feature[Any, dict[str, Any]]] = FeatureCollection(type="FeatureCollection", features=[])
    req = build_prediction_request(model, analytics, geojson, n_periods=3, dataset_type="forecasting", name="run-1")
    body = req.model_dump(by_alias=True, mode="json")
    # The chap API expects camelCase keys.
    assert "providedData" in body
    assert "dataSources" in body
    assert "dataToBeFetched" in body
    assert "configuredModelWithDataSourceId" in body
    assert "nPeriods" in body
    # And the nested observation must be camelCase too.
    obs = body["providedData"][0]
    assert "featureName" in obs
    assert "orgUnit" in obs


# --- geojson feature builder ------------------------------------------------


def test_build_feature_includes_parent_and_code() -> None:
    feature = _build_feature(
        {
            "id": "OU1",
            "displayName": "Region 1",
            "level": 2,
            "code": "R1",
            "parent": {"id": "PARENT"},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        }
    )
    assert feature.id == "OU1"
    # geometry stays as the raw dict because the Feature is typed Feature[Any, ...]
    assert feature.geometry == {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    props = feature.properties or {}
    assert props["id"] == "OU1"
    assert props["code"] == "R1"
    assert props["parent"] == "PARENT"
    assert props["parentGraph"] == "PARENT"


def test_build_feature_omits_optional_properties_when_missing() -> None:
    feature = _build_feature(
        {
            "id": "OU1",
            "level": 1,
            "displayName": "Country",
            "geometry": {"type": "Polygon", "coordinates": []},
        }
    )
    props = feature.properties or {}
    assert "code" not in props
    assert "parent" not in props
    assert "parentGraph" not in props


# --- flow signature ---------------------------------------------------------


def test_credentials_parameter_renders_block_dropdown_in_ui() -> None:
    schema = dhis2_chap_prediction.parameters.model_dump()
    assert "credentials" in schema["required"]
    creds_def = schema["definitions"]["Dhis2Credentials"]
    assert creds_def["block_type_slug"] == "chap-dhis2-credentials"
