from datetime import timezone

from chap_scheduler.chap import ChapSystemInfo


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
