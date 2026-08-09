from datetime import datetime, timezone

from core.xiaoxin.local_time import local_date_text, local_datetime


def test_local_time_uses_shanghai_calendar_day_for_utc_input():
    utc_value = datetime(2026, 7, 12, 16, 5, tzinfo=timezone.utc)

    local_value = local_datetime(utc_value)

    assert local_value.isoformat() == "2026-07-13T00:05:00+08:00"
    assert local_date_text(utc_value) == "2026-07-13"
