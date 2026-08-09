from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from core.xiaoxin.overview import (
    DailyWeather,
    IpCityLocation,
    XiaoxinOverviewStore,
)


def _weather(
    *,
    province: str = "浙江",
    city: str = "杭州",
    date: str = "2026-07-10",
    weather_text: str = "多云",
    timezone_id: str = "Asia/Shanghai",
) -> DailyWeather:
    return DailyWeather(
        province=province,
        city=city,
        date=date,
        weather_code=3,
        weather_text=weather_text,
        temperature_min_c=26.0,
        temperature_max_c=35.0,
        fetched_at="2026-07-10T06:00:00+08:00",
        timezone_id=timezone_id,
    )


def _clock_at(value: str):
    fixed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return lambda: fixed


def test_snapshot_store_increments_revision_only_when_content_changes(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")

    first, first_changed = store.upsert_snapshot(
        "device-1",
        "user-1",
        {"bound": True, "course": {"title": "数学"}},
        "2026-07-10T08:00:00+08:00",
    )
    same, same_changed = store.upsert_snapshot(
        "device-1",
        "user-1",
        {"course": {"title": "数学"}, "bound": True},
        "2026-07-10T08:01:00+08:00",
    )
    changed, changed_flag = store.upsert_snapshot(
        "device-1",
        "user-1",
        {"bound": True, "course": {"title": "体育"}},
        "2026-07-10T08:02:00+08:00",
    )

    assert (first.revision, first_changed) == (1, True)
    assert (same.revision, same_changed) == (1, False)
    assert same.content_hash == first.content_hash
    assert (changed.revision, changed_flag) == (2, True)


def test_snapshot_persists_complete_wire_payload_without_regenerating_same_content(
    tmp_path,
):
    db_path = tmp_path / "overview.db"
    store = XiaoxinOverviewStore(db_path)
    first, first_changed = store.upsert_snapshot(
        "device-1",
        "user-1",
        {"bound": True},
        "2026-07-10T08:00:00+08:00",
    )
    same, same_changed = store.upsert_snapshot(
        "device-1",
        "user-1",
        {"bound": True},
        "2026-07-10T08:01:00+08:00",
    )

    assert first_changed is True
    assert same_changed is False
    assert first.payload == {
        "type": "xiaoxin_overview_update",
        "version": 1,
        "device_id": "device-1",
        "revision": 1,
        "generated_at": "2026-07-10T08:00:00+08:00",
        "bound": True,
    }
    assert same.payload == first.payload

    restarted = XiaoxinOverviewStore(db_path)
    assert restarted.list_pending_snapshots(
        "2026-07-10T08:02:00+08:00"
    ) == [first]


def test_manual_location_takes_precedence_over_later_automatic_location(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    automatic = IpCityLocation(
        province="浙江",
        city="杭州",
        country_code="CN",
        located_at="2026-07-10T07:00:00+08:00",
    )
    later_automatic = IpCityLocation(
        province="江苏",
        city="南京",
        country_code="CN",
        located_at="2026-07-10T09:00:00+08:00",
    )

    store.set_automatic_location("device-1", "ip-hmac-1", automatic)
    manual = store.set_manual_location("device-1", "上海", "上海")
    result = store.set_automatic_location("device-1", "ip-hmac-2", later_automatic)

    assert manual["mode"] == "manual"
    assert (result["province"], result["city"]) == ("上海", "上海")
    assert result["mode"] == "manual"
    assert store.get_location("device-1") == result


def test_switching_back_to_automatic_restores_latest_candidate_after_restart(
    tmp_path,
):
    db_path = tmp_path / "overview.db"
    store = XiaoxinOverviewStore(db_path)
    store.set_automatic_location(
        "device-1",
        "ip-hmac-1",
        IpCityLocation(
            province="浙江",
            city="杭州",
            country_code="CN",
            located_at="2026-07-10T07:00:00+08:00",
        ),
    )
    store.set_manual_location("device-1", "上海", "上海")
    manual = store.set_automatic_location(
        "device-1",
        "ip-hmac-2",
        IpCityLocation(
            province="大阪府",
            city="大阪市",
            country_code="JP",
            located_at="2026-07-10T09:00:00+08:00",
        ),
    )

    assert (manual["province"], manual["city"]) == ("上海", "上海")
    assert manual["automatic_public_ip_hmac"] == "ip-hmac-2"
    assert manual["automatic_country_code"] == "JP"

    restarted = XiaoxinOverviewStore(db_path)
    automatic = restarted.set_location_mode("device-1", "automatic")

    assert automatic is not None
    assert automatic["mode"] == "automatic"
    assert (
        automatic["province"],
        automatic["city"],
        automatic["country_code"],
        automatic["public_ip_hmac"],
    ) == ("大阪府", "大阪市", "JP", "ip-hmac-2")
    assert automatic["located_at"] == "2026-07-10T09:00:00+08:00"


def test_restore_location_replaces_temporary_manual_state_or_deletes_row(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    store.set_automatic_location(
        "device-1",
        "automatic-hmac",
        IpCityLocation(
            province="Zhejiang",
            city="Hangzhou",
            country_code="CN",
            located_at="2026-07-10T08:00:00+00:00",
        ),
    )
    previous = store.get_location("device-1")
    temporary = store.set_manual_location("device-1", "Shanghai", "Shanghai")

    assert store.restore_location(
        "device-1",
        previous,
        expected_revision=temporary["location_revision"],
    ) is True

    restored = store.get_location("device-1")
    assert (restored["mode"], restored["province"], restored["city"]) == (
        previous["mode"],
        previous["province"],
        previous["city"],
    )
    assert restored["location_revision"] == temporary["location_revision"] + 1

    assert store.restore_location(
        "device-1",
        None,
        expected_revision=restored["location_revision"],
    ) is True

    assert store.get_location("device-1") is None


def test_location_mutations_increment_monotonic_revision(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    automatic = store.set_automatic_location(
        "device-1",
        "ip-hmac-1",
        IpCityLocation(
            province="Zhejiang",
            city="Hangzhou",
            country_code="CN",
            located_at="2026-07-10T08:00:00+00:00",
        ),
    )
    manual = store.set_manual_location("device-1", "Shanghai", "Shanghai")
    candidate = store.set_automatic_location(
        "device-1",
        "ip-hmac-2",
        IpCityLocation(
            province="Jiangsu",
            city="Nanjing",
            country_code="CN",
            located_at="2026-07-11T08:00:00+00:00",
        ),
    )
    switched = store.set_location_mode("device-1", "automatic")

    assert [
        automatic["location_revision"],
        manual["location_revision"],
        candidate["location_revision"],
        switched["location_revision"],
    ] == [1, 2, 3, 4]


def test_restore_location_uses_revision_cas_and_preserves_newer_candidate(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    store.set_manual_location("device-1", "Shanghai", "Shanghai")
    previous = store.get_location("device-1")
    temporary = store.set_manual_location("device-1", "Zhejiang", "Hangzhou")
    newer = store.set_automatic_location(
        "device-1",
        "new-ip-hmac",
        IpCityLocation(
            province="Jiangsu",
            city="Nanjing",
            country_code="CN",
            located_at="2026-07-11T08:00:00+00:00",
        ),
    )

    assert store.restore_location(
        "device-1",
        previous,
        expected_revision=temporary["location_revision"],
    ) is False
    persisted = store.get_location("device-1")
    assert persisted["location_revision"] == newer["location_revision"]
    assert persisted["automatic_city"] == "Nanjing"


def test_concurrent_automatic_observation_cannot_overwrite_committed_manual_mode(
    tmp_path,
):
    db_path = tmp_path / "overview.db"
    seed = XiaoxinOverviewStore(db_path)
    seed.set_automatic_location(
        "device-1",
        "ip-hmac-1",
        IpCityLocation(
            province="浙江",
            city="杭州",
            country_code="CN",
            located_at="2026-07-10T07:00:00+08:00",
        ),
    )
    automatic_read = threading.Event()
    manual_write_started = threading.Event()
    manual_write_finished = threading.Event()
    allow_automatic_write = threading.Event()
    errors: list[BaseException] = []

    class PausedAutomaticConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if "SELECT mode FROM device_weather_locations" in sql:
                automatic_read.set()
                if not allow_automatic_write.wait(5):
                    raise TimeoutError("automatic write was not released")
            return cursor

    class SignaledManualConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "INSERT INTO device_weather_locations" in sql:
                manual_write_started.set()
            return super().execute(sql, parameters)

    def connect(factory):
        conn = sqlite3.connect(db_path, timeout=5.0, factory=factory)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    automatic_store = XiaoxinOverviewStore(db_path)
    manual_store = XiaoxinOverviewStore(db_path)
    automatic_store._connect = lambda: connect(PausedAutomaticConnection)
    manual_store._connect = lambda: connect(SignaledManualConnection)

    def observe_automatic():
        try:
            automatic_store.set_automatic_location(
                "device-1",
                "ip-hmac-2",
                IpCityLocation(
                    province="江苏",
                    city="南京",
                    country_code="CN",
                    located_at="2026-07-10T09:00:00+08:00",
                ),
            )
        except BaseException as exc:
            errors.append(exc)

    def choose_manual():
        try:
            manual_store.set_manual_location("device-1", "上海", "上海")
            manual_write_finished.set()
        except BaseException as exc:
            errors.append(exc)

    automatic_thread = threading.Thread(target=observe_automatic)
    manual_thread = threading.Thread(target=choose_manual)
    automatic_thread.start()
    assert automatic_read.wait(5)
    manual_thread.start()
    assert manual_write_started.wait(5)
    manual_write_finished.wait(1)
    allow_automatic_write.set()
    automatic_thread.join(5)
    manual_thread.join(5)

    assert not automatic_thread.is_alive()
    assert not manual_thread.is_alive()
    assert errors == []
    location = seed.get_location("device-1")
    assert location is not None
    assert location["mode"] == "manual"
    assert (location["province"], location["city"]) == ("上海", "上海")
    assert location["automatic_city"] == "南京"


def test_daily_weather_cache_is_keyed_by_city_date_and_provider(tmp_path):
    store = XiaoxinOverviewStore(
        tmp_path / "overview.db", clock=_clock_at("2026-07-10T00:00:00Z")
    )
    hangzhou_today = _weather()
    hangzhou_tomorrow = _weather(date="2026-07-11", weather_text="晴")
    ningbo_today = _weather(city="宁波", weather_text="小雨")
    alternate_provider = _weather(weather_text="阴")

    store.put_daily_weather(hangzhou_today, "open-meteo")
    store.put_daily_weather(hangzhou_tomorrow, "open-meteo")
    store.put_daily_weather(ningbo_today, "open-meteo")
    store.put_daily_weather(alternate_provider, "provider-b")

    assert (
        store.get_daily_weather("浙江", "杭州", "2026-07-10", "open-meteo")
        == hangzhou_today
    )
    assert (
        store.get_daily_weather("浙江", "杭州", "2026-07-11", "open-meteo")
        == hangzhou_tomorrow
    )
    assert (
        store.get_daily_weather("浙江", "宁波", "2026-07-10", "open-meteo")
        == ningbo_today
    )
    assert (
        store.get_daily_weather("浙江", "杭州", "2026-07-10", "provider-b")
        == alternate_provider
    )


def test_daily_weather_cache_does_not_collide_across_countries(tmp_path):
    store = XiaoxinOverviewStore(
        tmp_path / "overview.db", clock=_clock_at("2026-07-10T00:00:00Z")
    )
    cn_weather = DailyWeather(
        province="吉林",
        city="长春",
        date="2026-07-10",
        weather_code=1,
        weather_text="晴",
        temperature_min_c=20.0,
        temperature_max_c=30.0,
        fetched_at="2026-07-10T06:00:00+08:00",
        country_code="CN",
        timezone_id="Asia/Shanghai",
    )
    jp_weather = DailyWeather(
        province="吉林",
        city="长春",
        date="2026-07-10",
        weather_code=3,
        weather_text="多云",
        temperature_min_c=18.0,
        temperature_max_c=26.0,
        fetched_at="2026-07-10T07:00:00+09:00",
        country_code="JP",
        timezone_id="Asia/Tokyo",
    )

    store.put_daily_weather(cn_weather, "provider-a")
    store.put_daily_weather(jp_weather, "provider-a")

    assert store.get_daily_weather(
        "吉林",
        "长春",
        "2026-07-10",
        "provider-a",
        country_code="CN",
    ) == cn_weather
    assert store.get_daily_weather(
        "吉林",
        "长春",
        "2026-07-10",
        "provider-a",
        country_code="JP",
    ) == jp_weather


def test_weather_retry_preserves_explicit_country_code(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    store.record_weather_failure(
        "大阪府",
        "大阪市",
        "2026-07-10",
        "provider-a",
        "timeout",
        attempts=1,
        next_attempt_at="2026-07-10T00:30:00+00:00",
        country_code="JP",
    )

    due = store.list_due_weather_retries("2026-07-10T01:00:00+00:00")

    assert due[0]["country_code"] == "JP"


def test_daily_weather_expiration_uses_injected_clock(tmp_path):
    current = [datetime(2026, 7, 10, 15, 59, 59, tzinfo=timezone.utc)]
    store = XiaoxinOverviewStore(tmp_path / "overview.db", clock=lambda: current[0])
    weather = _weather()
    store.put_daily_weather(weather, "open-meteo")

    assert store.get_daily_weather(
        "浙江",
        "杭州",
        "2026-07-10",
        "open-meteo",
    ) == weather
    current[0] = datetime(2026, 7, 10, 16, 0, 0, tzinfo=timezone.utc)
    assert store.get_daily_weather(
        "浙江",
        "杭州",
        "2026-07-10",
        "open-meteo",
    ) is None


def test_hangzhou_expiry_uses_city_timezone_when_fetched_at_is_utc(tmp_path):
    current = [datetime(2026, 7, 10, 15, 59, 59, tzinfo=timezone.utc)]
    store = XiaoxinOverviewStore(tmp_path / "overview.db", clock=lambda: current[0])
    weather = DailyWeather(
        province="浙江",
        city="杭州",
        date="2026-07-10",
        weather_code=3,
        weather_text="多云",
        temperature_min_c=26.0,
        temperature_max_c=35.0,
        fetched_at="2026-07-10T06:00:00+00:00",
        timezone_id="Asia/Shanghai",
    )
    store.put_daily_weather(weather, "open-meteo")

    assert store.get_daily_weather(
        "浙江", "杭州", "2026-07-10", "open-meteo"
    ) == weather
    current[0] = datetime(2026, 7, 10, 16, 0, 0, tzinfo=timezone.utc)
    assert store.get_daily_weather(
        "浙江", "杭州", "2026-07-10", "open-meteo"
    ) is None


def test_weather_expiry_handles_dst_timezone_boundary(tmp_path):
    current = [datetime(2026, 11, 2, 4, 59, 59, tzinfo=timezone.utc)]
    store = XiaoxinOverviewStore(tmp_path / "overview.db", clock=lambda: current[0])
    weather = DailyWeather(
        province="New York",
        city="New York",
        date="2026-11-01",
        weather_code=3,
        weather_text="Cloudy",
        temperature_min_c=5.0,
        temperature_max_c=12.0,
        fetched_at="2026-11-01T12:00:00+00:00",
        country_code="US",
        timezone_id="America/New_York",
    )
    store.put_daily_weather(weather, "open-meteo")

    assert store.get_daily_weather(
        "New York",
        "New York",
        "2026-11-01",
        "open-meteo",
        country_code="US",
    ) == weather
    current[0] = datetime(2026, 11, 2, 5, 0, 0, tzinfo=timezone.utc)
    assert store.get_daily_weather(
        "New York",
        "New York",
        "2026-11-01",
        "open-meteo",
        country_code="US",
    ) is None


def test_weather_cache_uses_injected_clock_instead_of_process_wall_clock(tmp_path):
    store = XiaoxinOverviewStore(
        tmp_path / "overview.db",
        clock=lambda: datetime(2001, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    weather = DailyWeather(
        province="Test",
        city="Clock",
        date="2001-01-01",
        weather_code=1,
        weather_text="Clear",
        temperature_min_c=1.0,
        temperature_max_c=2.0,
        fetched_at="2001-01-01T00:00:00+00:00",
        timezone_id="UTC",
    )
    store.put_daily_weather(weather, "test")

    assert store.get_daily_weather(
        "Test", "Clock", "2001-01-01", "test"
    ) == weather


def test_daily_weather_requires_explicit_timezone_id():
    with pytest.raises(TypeError, match="timezone_id"):
        DailyWeather(
            province="浙江",
            city="杭州",
            date="2026-07-10",
            weather_code=3,
            weather_text="多云",
            temperature_min_c=26.0,
            temperature_max_c=35.0,
            fetched_at="2026-07-10T06:00:00+00:00",
        )


def test_daily_weather_rejects_timezone_less_fetched_at(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    weather = DailyWeather(
        province="浙江",
        city="杭州",
        date="2026-07-10",
        weather_code=3,
        weather_text="多云",
        temperature_min_c=26.0,
        temperature_max_c=35.0,
        fetched_at="2026-07-10T06:00:00",
        timezone_id="Asia/Shanghai",
    )

    with pytest.raises(ValueError, match="UTC offset"):
        store.put_daily_weather(weather, "open-meteo")


def test_weather_failure_retry_state_survives_restart(tmp_path):
    db_path = tmp_path / "overview.db"
    store = XiaoxinOverviewStore(db_path)
    store.record_weather_failure(
        "浙江",
        "杭州",
        "2026-07-10",
        "open-meteo",
        "provider timeout",
        attempts=2,
        next_attempt_at="2026-07-10T08:30:00+08:00",
    )

    restarted = XiaoxinOverviewStore(db_path)

    assert restarted.get_daily_weather(
        "浙江", "杭州", "2026-07-10", "open-meteo"
    ) is None
    assert restarted.list_due_weather_retries(
        "2026-07-10T08:29:59+08:00"
    ) == []
    assert restarted.list_due_weather_retries(
        "2026-07-10T08:30:00+08:00"
    ) == [
        {
            "country_code": "CN",
            "province": "浙江",
            "city": "杭州",
            "date": "2026-07-10",
            "provider": "open-meteo",
            "fetch_attempts": 2,
            "next_attempt_at": "2026-07-10T00:30:00+00:00",
            "last_error": "provider timeout",
        }
    ]


def test_weather_retry_state_reads_future_and_exhausted_failures(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    store.record_weather_failure(
        "Zhejiang",
        "Hangzhou",
        "2026-07-10",
        "open_meteo",
        "timeout",
        attempts=1,
        next_attempt_at="2026-07-10T01:10:00+00:00",
    )
    store.record_weather_failure(
        "Tokyo",
        "Tokyo",
        "2026-07-10",
        "open_meteo",
        "exhausted",
        attempts=4,
        next_attempt_at=None,
        country_code="JP",
    )

    assert store.get_weather_retry_state(
        "Zhejiang",
        "Hangzhou",
        "2026-07-10",
        "open_meteo",
    ) == {
        "country_code": "CN",
        "province": "Zhejiang",
        "city": "Hangzhou",
        "date": "2026-07-10",
        "provider": "open_meteo",
        "fetch_attempts": 1,
        "next_attempt_at": "2026-07-10T01:10:00+00:00",
        "last_error": "timeout",
    }
    assert store.get_weather_retry_state(
        "Tokyo",
        "Tokyo",
        "2026-07-10",
        "open_meteo",
        country_code="JP",
    )["next_attempt_at"] is None


def test_weather_retry_due_comparison_normalizes_mixed_offsets(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    store.record_weather_failure(
        "浙江",
        "杭州",
        "2026-07-10",
        "open-meteo",
        "provider timeout",
        attempts=1,
        next_attempt_at="2026-07-10T08:30:00+08:00",
    )

    due = store.list_due_weather_retries("2026-07-10T01:00:00+00:00")

    assert len(due) == 1
    assert due[0]["next_attempt_at"] == "2026-07-10T00:30:00+00:00"


def test_new_snapshot_overwrites_older_pending_snapshot(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    first, _ = store.upsert_snapshot(
        "device-1",
        "user-1",
        {"bound": True, "course": {"title": "数学"}},
        "2026-07-10T08:00:00+08:00",
    )
    store.mark_publish_attempt(
        "device-1",
        first.revision,
        "2026-07-10T08:00:05+08:00",
        "broker unavailable",
    )

    latest, _ = store.upsert_snapshot(
        "device-1",
        "user-1",
        {"bound": True, "course": {"title": "体育"}},
        "2026-07-10T08:01:00+08:00",
    )

    assert latest.revision == 2
    assert latest.publish_state == "pending"
    assert latest.publish_attempts == 0
    assert latest.next_attempt_at is None
    assert store.list_pending_snapshots("2026-07-10T08:01:00+08:00") == [latest]


def test_snapshot_pending_due_comparison_normalizes_mixed_offsets(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    snapshot, _ = store.upsert_snapshot(
        "device-1", "user-1", {"bound": True}, "2026-07-10T08:00:00+08:00"
    )
    store.mark_publish_attempt(
        "device-1",
        snapshot.revision,
        "2026-07-10T08:30:00+08:00",
        "broker unavailable",
    )

    due = store.list_pending_snapshots("2026-07-10T01:00:00+00:00")

    assert len(due) == 1
    assert due[0].next_attempt_at == "2026-07-10T00:30:00+00:00"


def test_stale_puback_cannot_publish_a_newer_revision(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    first, _ = store.upsert_snapshot(
        "device-1", "user-1", {"bound": False}, "2026-07-10T08:00:00+08:00"
    )
    latest, _ = store.upsert_snapshot(
        "device-1", "user-1", {"bound": True}, "2026-07-10T08:01:00+08:00"
    )

    assert store.mark_published(
        "device-1", first.revision, "2026-07-10T08:01:01+08:00"
    ) is False
    assert store.list_pending_snapshots("2026-07-10T08:02:00+08:00") == [latest]
    assert store.mark_published(
        "device-1", latest.revision, "2026-07-10T08:02:01+08:00"
    ) is True
    assert store.list_pending_snapshots("2026-07-10T08:03:00+08:00") == []


def test_location_weather_and_pending_snapshot_survive_restart(tmp_path):
    db_path = tmp_path / "overview.db"
    store = XiaoxinOverviewStore(
        db_path, clock=_clock_at("2026-07-10T00:00:00Z")
    )
    location = IpCityLocation(
        province="浙江",
        city="杭州",
        country_code="CN",
        located_at="2026-07-10T07:00:00+08:00",
    )
    store.set_automatic_location("device-1", "ip-hmac-1", location)
    weather = _weather()
    store.put_daily_weather(weather, "open-meteo")
    snapshot, _ = store.upsert_snapshot(
        "device-1",
        "user-1",
        {"bound": True, "weather": {"summary": "杭州 · 多云"}},
        "2026-07-10T08:00:00+08:00",
    )
    store.mark_publish_attempt(
        "device-1",
        snapshot.revision,
        "2026-07-10T08:05:00+08:00",
        "not connected",
    )

    restarted = XiaoxinOverviewStore(
        db_path, clock=_clock_at("2026-07-10T00:05:00Z")
    )

    assert restarted.get_location("device-1")["city"] == "杭州"
    assert (
        restarted.get_daily_weather(
            "浙江", "杭州", "2026-07-10", "open-meteo"
        )
        == weather
    )
    pending = restarted.list_pending_snapshots("2026-07-10T08:05:00+08:00")
    assert len(pending) == 1
    assert pending[0].revision == 1
    assert pending[0].publish_attempts == 1
    assert pending[0].payload["weather"]["summary"] == "杭州 · 多云"


def test_snapshot_diagnostics_returns_only_safe_metadata_and_weather_date(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    snapshot, _changed = store.upsert_snapshot(
        "device-1",
        "owner-secret",
        {
            "bound": True,
            "weather": {"date": "2026-07-11"},
            "payload": "raw-secret-payload",
            "credential": "mqtt-secret",
        },
        "2026-07-11T01:00:00+00:00",
    )
    store.mark_publish_attempt(
        "device-1",
        snapshot.revision,
        "2026-07-11T01:10:00+00:00",
        "overview_publish_failed",
    )

    assert store.get_snapshot_diagnostics("device-1") == {
        "revision": 1,
        "publish_state": "pending",
        "publish_attempts": 1,
        "last_error": "overview_publish_failed",
        "published_at": None,
        "weather_date": "2026-07-11",
    }
    assert store.get_snapshot_diagnostics("missing") is None

    with store._connect() as conn:
        conn.execute(
            """
            UPDATE device_overview_snapshots
            SET payload_json = ?
            WHERE device_id = ?
            """,
            ('{"weather":{"date":"raw-payload-secret"', "device-1"),
        )

    diagnostics = store.get_snapshot_diagnostics("device-1")
    assert diagnostics == {
        "revision": 1,
        "publish_state": "pending",
        "publish_attempts": 1,
        "last_error": "overview_publish_failed",
        "published_at": None,
        "weather_date": "",
    }
    assert "raw-payload-secret" not in repr(diagnostics)


def test_existing_location_table_migrates_location_revision(tmp_path):
    db_path = tmp_path / "overview.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE device_weather_locations (
                device_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                public_ip_hmac TEXT,
                province TEXT NOT NULL,
                city TEXT NOT NULL,
                country_code TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                located_at TEXT NOT NULL,
                automatic_public_ip_hmac TEXT,
                automatic_province TEXT,
                automatic_city TEXT,
                automatic_country_code TEXT,
                automatic_located_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO device_weather_locations (
                device_id, mode, public_ip_hmac, province, city,
                country_code, located_at, updated_at
            ) VALUES (?, 'manual', NULL, ?, ?, 'CN', ?, ?)
            """,
            (
                "device-1",
                "Zhejiang",
                "Hangzhou",
                "2026-07-10T08:00:00+00:00",
                "2026-07-10T08:00:00+00:00",
            ),
        )

    store = XiaoxinOverviewStore(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(device_weather_locations)"
            )
        }
    assert "location_revision" in columns
    assert store.get_location("device-1")["location_revision"] == 1


def test_earliest_overview_schema_migrates_to_head_and_remains_operational(tmp_path):
    db_path = tmp_path / "overview.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE device_weather_locations (
                device_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                public_ip_hmac TEXT,
                province TEXT NOT NULL,
                city TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                located_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE daily_city_weather (
                cache_key TEXT PRIMARY KEY,
                country_code TEXT NOT NULL,
                province TEXT NOT NULL,
                city TEXT NOT NULL,
                date TEXT NOT NULL,
                weather_code INTEGER,
                weather_text TEXT,
                temperature_min_c REAL,
                temperature_max_c REAL,
                fetched_at TEXT,
                expires_at TEXT,
                fetch_attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE device_overview_snapshots (
                device_id TEXT PRIMARY KEY,
                owner_user_id TEXT,
                revision INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                publish_state TEXT NOT NULL,
                publish_attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                last_error TEXT,
                generated_at TEXT NOT NULL,
                published_at TEXT,
                updated_at TEXT NOT NULL
            );
            INSERT INTO device_weather_locations VALUES (
                'device-old', 'automatic', 'old-hmac', 'Zhejiang', 'Hangzhou',
                NULL, NULL, '2026-07-10T00:00:00+00:00',
                '2026-07-10T00:00:00+00:00'
            );
            """
        )

    store = XiaoxinOverviewStore(
        db_path, clock=_clock_at("2026-07-10T01:00:00+00:00")
    )
    location = store.get_location("device-old")
    store.put_daily_weather(_weather(province="Zhejiang", city="Hangzhou"), "open_meteo")
    snapshot, _ = store.upsert_snapshot(
        "device-old", "user-1", {"bound": True}, "2026-07-10T01:00:00+00:00"
    )

    assert location["country_code"] == "CN"
    assert location["automatic_public_ip_hmac"] == "old-hmac"
    assert location["automatic_city"] == "Hangzhou"
    assert location["location_revision"] == 1
    assert store.get_daily_weather(
        "Zhejiang", "Hangzhou", "2026-07-10", "open_meteo"
    ).timezone_id == "Asia/Shanghai"
    assert store.get_snapshot("device-old").revision == snapshot.revision


def test_malformed_retry_and_snapshot_rows_are_quarantined_without_blocking(tmp_path):
    store = XiaoxinOverviewStore(
        tmp_path / "overview.db",
        clock=_clock_at("2026-07-10T01:00:00+00:00"),
    )
    store.record_weather_failure(
        "Zhejiang", "Hangzhou", "2026-07-10", "open_meteo",
        "provider_down", 1, "2026-07-10T00:00:00+00:00",
    )
    good_snapshot, _ = store.upsert_snapshot(
        "device-good", "user-1", {"bound": True}, "2026-07-10T00:01:00+00:00"
    )
    with store._connect() as conn:
        for index in range(5):
            conn.execute(
                """INSERT INTO daily_city_weather (
                cache_key, country_code, province, city, date, timezone_id,
                fetch_attempts, next_attempt_at, last_error, updated_at
            ) VALUES (?, 'CN', 'Bad', 'Bad', '2026-07-10', 'UTC',
                      1, '2026-07-09T00:00:00+00:00', 'raw-secret', ?)""",
                (f"not-json-{index}", f"2026-07-09T00:00:0{index}+00:00"),
            )
            device_id = f"device-bad-{index}"
            conn.execute(
                """INSERT INTO device_overview_snapshots (
                    device_id, owner_user_id, revision, content_hash, payload_json,
                    publish_state, publish_attempts, generated_at, updated_at
                ) VALUES (?, 'user-1', 1, 'bad', 'not-json', 'pending', 0,
                          '2026-07-09T00:00:00+00:00', ?)""",
                (device_id, f"2026-07-09T00:00:0{index}+00:00"),
            )

    due = store.list_due_weather_retries("2026-07-10T01:00:00+00:00", limit=1)
    pending = store.list_pending_snapshots("2026-07-10T01:00:00+00:00", limit=1)
    restarted = XiaoxinOverviewStore(tmp_path / "overview.db")

    assert [row["city"] for row in due] == ["Hangzhou"]
    assert [row.device_id for row in pending] == [good_snapshot.device_id]
    assert [row.device_id for row in restarted.list_pending_snapshots(
        "2026-07-10T01:00:00+00:00"
    )] == [good_snapshot.device_id]
    with restarted._connect() as conn:
        weather_bad = conn.execute(
            "SELECT quarantined, last_error FROM daily_city_weather WHERE cache_key='not-json-0'"
        ).fetchone()
        snapshot_bad = conn.execute(
            "SELECT quarantined, last_error FROM device_overview_snapshots WHERE device_id='device-bad-0'"
        ).fetchone()
    assert tuple(weather_bad) == (1, "overview_retry_row_malformed")
    assert tuple(snapshot_bad) == (1, "overview_payload_invalid")


def test_sqlite_schema_and_retry_indexes_match_persistence_contract(tmp_path):
    db_path = tmp_path / "overview.db"
    XiaoxinOverviewStore(db_path)
    with sqlite3.connect(db_path) as conn:
        location_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(device_weather_locations)")
        }
        weather_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(daily_city_weather)")
        }
        snapshot_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(device_overview_snapshots)")
        }
        weather_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(daily_city_weather)")
        }
        snapshot_indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(device_overview_snapshots)")
        }

    assert location_columns == {
        "device_id",
        "mode",
        "public_ip_hmac",
        "province",
        "city",
        "country_code",
        "latitude",
        "longitude",
        "located_at",
        "automatic_public_ip_hmac",
        "automatic_province",
        "automatic_city",
        "automatic_country_code",
        "automatic_located_at",
        "location_revision",
        "updated_at",
    }
    assert weather_columns == {
        "cache_key",
        "country_code",
        "province",
        "city",
        "date",
        "weather_code",
        "weather_text",
        "temperature_min_c",
        "temperature_max_c",
        "fetched_at",
        "timezone_id",
        "expires_at",
        "fetch_attempts",
        "next_attempt_at",
        "last_error",
        "quarantined",
        "updated_at",
    }
    assert snapshot_columns == {
        "device_id",
        "owner_user_id",
        "revision",
        "content_hash",
        "payload_json",
        "publish_state",
        "publish_attempts",
        "next_attempt_at",
        "last_error",
        "generated_at",
        "published_at",
        "quarantined",
        "updated_at",
    }
    assert "idx_daily_city_weather_retry" in weather_indexes
    assert "idx_device_overview_pending" in snapshot_indexes
