from datetime import timezone

from chap_scheduler.chap import ChapConfiguredModelWithDataSource, ChapSystemInfo


def test_chap_system_info_parses_real_payload() -> None:
    info = ChapSystemInfo.model_validate(
        {
            "chap_core_version": "2.0.0.dev1",
            "python_version": "3.13.12",
            "server_date": "2026-05-07T10:39:41.206627+00:00",
            "server_time_zone_id": "Etc/UTC",
            "revision": "",  # not modeled — ignored by extra='ignore'
        }
    )
    assert info.chap_core_version == "2.0.0.dev1"
    assert info.python_version == "3.13.12"
    assert info.server_time_zone_id == "Etc/UTC"
    assert info.server_date.tzinfo is not None
    assert info.server_date.utcoffset() == timezone.utc.utcoffset(info.server_date)


def test_configured_model_with_data_source_parses_real_payload() -> None:
    payload = {
        "id": 1,
        "name": "test",
        "created": "2026-05-07T11:13:23.474245",
        "configuredModel": {
            "userOptionValues": {},
            "additionalContinuousCovariates": ["rainfall", "mean_temperature"],
            "name": "chapkit-ewars-model",
            "id": 12,
            "modelTemplate": {
                "name": "chapkit-ewars-model",
                "displayName": "CHAP-EWARS Model (chapkit)",
                "target": "disease_cases",
                "supportedPeriodType": "month",
                "requiredCovariates": ["population"],
                "id": 11,
            },
        },
        "startPeriod": "202301",
        "orgUnits": ["FRmrFTE63D0", "K27JzTKmBKh"],
        "dataSources": [
            {"covariate": "population", "dataElementId": "naAwC0qIH2N"},
            {"covariate": "disease_cases", "dataElementId": "A6UIC6raN1P"},
        ],
        "periodType": "month",
    }
    m = ChapConfiguredModelWithDataSource.model_validate(payload)
    assert m.id == 1
    assert m.name == "test"
    assert m.start_period == "202301"
    assert m.period_type == "month"
    assert [ds.covariate for ds in m.data_sources] == ["population", "disease_cases"]
    assert [ds.data_element_id for ds in m.data_sources] == ["naAwC0qIH2N", "A6UIC6raN1P"]
    assert m.configured_model.model_template.target == "disease_cases"
    assert m.configured_model.additional_continuous_covariates == ["rainfall", "mean_temperature"]
