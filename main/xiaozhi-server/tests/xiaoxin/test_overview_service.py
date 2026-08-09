import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone

from core.xiaoxin.identity import store as identity_store_module
from core.xiaoxin.companion import CompanionProjection
from core.xiaoxin.identity.store import XiaoxinIdentityStore
from core.xiaoxin.overview import service as overview_service_module
from core.xiaoxin.overview.models import DailyWeather, IpCityLocation
from core.xiaoxin.overview.service import OverviewSyncService
from core.xiaoxin.overview.store import XiaoxinOverviewStore


DATE_TEXT = "2026-07-10"


class FakeWeatherProvider:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    async def daily(self, province: str, city: str, date_text: str) -> DailyWeather:
        self.calls.append((province, city, date_text))
        if self.error is not None:
            raise self.error
        return DailyWeather(
            province=province,
            city=city,
            date=date_text,
            weather_code=2,
            weather_text="多云",
            temperature_min_c=26,
            temperature_max_c=35,
            fetched_at="2026-07-10T06:05:00+00:00",
            timezone_id="Asia/Shanghai",
            country_code="CN",
        )


class BlockingFirstWeatherProvider(FakeWeatherProvider):
    def __init__(self):
        super().__init__()
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def daily(self, province: str, city: str, date_text: str) -> DailyWeather:
        call_number = len(self.calls) + 1
        self.calls.append((province, city, date_text))
        if call_number == 1:
            self.first_started.set()
            await self.release_first.wait()
        return DailyWeather(
            province=province,
            city=city,
            date=date_text,
            weather_code=2,
            weather_text="多云",
            temperature_min_c=26,
            temperature_max_c=35,
            fetched_at="2026-07-10T06:05:00+00:00",
            timezone_id="Asia/Shanghai",
            country_code="CN",
        )


class FakeLocationProvider:
    def __init__(self, location=None):
        self.location = location
        self.calls: list[str] = []

    async def locate(self, public_ip: str):
        self.calls.append(public_ip)
        return self.location


class BlockingLocationProvider(FakeLocationProvider):
    def __init__(self, location):
        super().__init__(location)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def locate(self, public_ip: str):
        self.calls.append(public_ip)
        self.started.set()
        await self.release.wait()
        return self.location


class FakePublisher:
    def __init__(self, results: list[int | None] | None = None):
        self.results = list(results or [])
        self.published: list[tuple[str, dict[str, object]]] = []
        self.ack_listeners = []
        self.publish_session_generation = 0

    def publish_overview(self, device_id: str, payload: dict[str, object]):
        self.published.append((device_id, json.loads(json.dumps(payload))))
        if self.results:
            return self.results.pop(0)
        return len(self.published)

    def add_publish_ack_listener(self, listener):
        self.ack_listeners.append(listener)

    def ack(self, mid: int) -> None:
        for listener in tuple(self.ack_listeners):
            listener(mid, self.publish_session_generation)


class RaisingPublisher(FakePublisher):
    def publish_overview(self, device_id: str, payload: dict[str, object]):
        self.published.append((device_id, json.loads(json.dumps(payload))))
        raise RuntimeError("broker unavailable")


class EarlyAckPublisher(FakePublisher):
    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode

    def publish_overview(self, device_id: str, payload: dict[str, object]):
        self.published.append((device_id, json.loads(json.dumps(payload))))
        mid = 90 + len(self.published)

        def acknowledge():
            for listener in tuple(self.ack_listeners):
                listener(mid, self.publish_session_generation)

        if self.mode == "sync":
            acknowledge()
        else:
            thread = threading.Thread(target=acknowledge)
            thread.start()
            thread.join()
        return mid


class ReusedMidWithStaleAckPublisher(FakePublisher):
    def publish_overview(self, device_id: str, payload: dict[str, object]):
        self.published.append((device_id, json.loads(json.dumps(payload))))
        mid = 77
        if len(self.published) == 2:
            for listener in tuple(self.ack_listeners):
                listener(mid, self.publish_session_generation)
        return mid


class EmptyRegistry:
    def list_devices(self):
        return []


class StaticRegistry:
    def __init__(self, devices):
        self.devices = devices

    def list_devices(self):
        return self.devices


def _clock():
    current = [datetime(2026, 7, 10, 7, 30, tzinfo=timezone.utc)]
    return current, lambda: current[0]


def _seed_bound_student(identity_store: XiaoxinIdentityStore, device_id="device-1"):
    user = identity_store.create_user("student-a", "hash", "Student A")
    identity_store.upsert_seen_device(device_id)
    identity_store.bind_device(device_id, user.id, "Desk Xiaoxin")
    identity_store.update_student_semester(
        user.id,
        {"label": "2026 秋季学期", "startDate": "2026-07-06", "totalWeeks": 18},
    )
    identity_store.create_student_course(
        user.id,
        {
            "title": "体育",
            "classroom": "体育馆",
            "teacher": "李老师",
            "weekday": 5,
            "startSection": 7,
            "endSection": 8,
            "weekRange": "第1-18周",
            "startsAt": "15:25",
            "endsAt": "17:00",
            "notes": "",
        },
    )
    identity_store.create_student_todo(
        user.id,
        {"title": "提交实验报告", "dueAt": "2026-07-10T20:30:00+08:00"},
    )
    return user


def _rebind_to_second_student(identity_store: XiaoxinIdentityStore, owner_a):
    assert identity_store.unbind_device("device-1", owner_a.id) is True
    owner_b = identity_store.create_user("student-b", "hash", "Student B")
    identity_store.bind_device("device-1", owner_b.id, "Desk Xiaoxin")
    identity_store.update_student_semester(
        owner_b.id,
        {"startDate": "2026-07-06", "totalWeeks": 18},
    )
    identity_store.create_student_course(
        owner_b.id,
        {
            "title": "B 的课程",
            "classroom": "B 教室",
            "weekday": 5,
            "startSection": 1,
            "endSection": 2,
            "weekRange": "第1-18周",
            "startsAt": "08:00",
        },
    )
    return owner_b


def _seed_stale_owner_snapshot(store: XiaoxinOverviewStore, owner_user_id: str):
    return store.upsert_snapshot(
        "device-1",
        owner_user_id,
        {
            "bound": True,
            "weather": {
                "configured": False,
                "available": False,
                "province": "",
                "city": "",
                "date": DATE_TEXT,
                "summary": "A 的旧天气",
                "detail": "",
                "fetched_at": "",
            },
            "course": {
                "configured": True,
                "available_today": True,
                "title": "A 的旧课程",
                "detail": "A 教室",
            },
            "todo": {
                "configured": True,
                "count": 1,
                "detail": "A 的旧待办",
            },
        },
        "2026-07-10T07:30:00+00:00",
    )[0]


def _service(
    tmp_path,
    *,
    weather_provider=None,
    publisher=None,
    registry=None,
    companion_mind=None,
):
    current, clock = _clock()
    identity_store = XiaoxinIdentityStore(tmp_path / "identity.db")
    overview_store = XiaoxinOverviewStore(tmp_path / "overview.db", clock=clock)
    publisher = publisher or FakePublisher()
    service = OverviewSyncService(
        identity_store=identity_store,
        overview_store=overview_store,
        weather_provider=weather_provider or FakeWeatherProvider(),
        publisher=publisher,
        registry=registry or EmptyRegistry(),
        clock=clock,
        companion_mind=companion_mind,
    )
    return service, identity_store, overview_store, publisher, current


class FakeCompanionMind:
    def __init__(self, projection):
        self.projection = projection
        self.requests = []

    def project(self, request):
        self.requests.append(request)
        return self.projection


def test_voice_weather_reuses_device_city_cache_when_province_is_omitted(
    tmp_path,
):
    weather_provider = FakeWeatherProvider(
        error=AssertionError("cached weather must not call provider")
    )
    service, _identity_store, overview_store, _publisher, _current = _service(
        tmp_path,
        weather_provider=weather_provider,
    )
    overview_store.set_manual_location("device-1", "浙江", "杭州")
    cached = DailyWeather(
        province="浙江",
        city="杭州",
        date=DATE_TEXT,
        weather_code=61,
        weather_text="小雨",
        temperature_min_c=26,
        temperature_max_c=34,
        fetched_at="2026-07-10T06:05:00+00:00",
        timezone_id="Asia/Shanghai",
        country_code="CN",
    )
    overview_store.put_daily_weather(cached, "open_meteo")

    result = asyncio.run(
        service.query_daily_weather(
            "",
            "杭州",
            DATE_TEXT,
            device_id="device-1",
        )
    )

    assert result == cached
    assert weather_provider.calls == []


def test_voice_weather_reuses_device_cache_with_city_suffix(tmp_path):
    weather_provider = FakeWeatherProvider(
        error=AssertionError("cached weather must not call provider")
    )
    service, _identity_store, overview_store, _publisher, _current = _service(
        tmp_path,
        weather_provider=weather_provider,
    )
    overview_store.set_manual_location("device-1", "浙江", "杭州")
    cached = DailyWeather(
        province="浙江",
        city="杭州",
        date=DATE_TEXT,
        weather_code=61,
        weather_text="小雨",
        temperature_min_c=26,
        temperature_max_c=34,
        fetched_at="2026-07-10T06:05:00+00:00",
        timezone_id="Asia/Shanghai",
        country_code="CN",
    )
    overview_store.put_daily_weather(cached, "open_meteo")

    result = asyncio.run(
        service.query_daily_weather(
            "",
            "杭州市",
            DATE_TEXT,
            device_id="device-1",
        )
    )

    assert result == cached
    assert weather_provider.calls == []


def test_voice_weather_cache_miss_fetches_once_and_fills_cache(tmp_path):
    weather_provider = FakeWeatherProvider()
    service, _identity_store, overview_store, _publisher, _current = _service(
        tmp_path,
        weather_provider=weather_provider,
    )

    first = asyncio.run(
        service.query_daily_weather("浙江", "杭州", DATE_TEXT)
    )
    second = asyncio.run(
        service.query_daily_weather("浙江", "杭州", DATE_TEXT)
    )

    assert first == second
    assert first.weather_text == "多云"
    assert weather_provider.calls == [("浙江", "杭州", DATE_TEXT)]
    assert overview_store.get_daily_weather(
        "浙江",
        "杭州",
        DATE_TEXT,
        "open_meteo",
    ) == first


def test_voice_weather_expired_cache_fetches_provider(tmp_path):
    weather_provider = FakeWeatherProvider()
    service, _identity_store, overview_store, _publisher, current = _service(
        tmp_path,
        weather_provider=weather_provider,
    )
    overview_store.put_daily_weather(
        DailyWeather(
            province="浙江",
            city="杭州",
            date=DATE_TEXT,
            weather_code=61,
            weather_text="小雨",
            temperature_min_c=26,
            temperature_max_c=34,
            fetched_at="2026-07-10T06:05:00+00:00",
            timezone_id="Asia/Shanghai",
            country_code="CN",
        ),
        "open_meteo",
    )
    current[0] = datetime(2026, 7, 10, 16, 0, tzinfo=timezone.utc)

    result = asyncio.run(
        service.query_daily_weather("浙江", "杭州", DATE_TEXT)
    )

    assert result.weather_text == "多云"
    assert weather_provider.calls == [("浙江", "杭州", DATE_TEXT)]


def test_concurrent_voice_weather_cache_misses_share_one_provider_call(tmp_path):
    async def scenario():
        weather_provider = BlockingFirstWeatherProvider()
        service, _identity_store, _store, _publisher, _current = _service(
            tmp_path,
            weather_provider=weather_provider,
        )
        first = asyncio.create_task(
            service.query_daily_weather("浙江", "杭州", DATE_TEXT)
        )
        await weather_provider.first_started.wait()
        second = asyncio.create_task(
            service.query_daily_weather("浙江", "杭州", DATE_TEXT)
        )
        await asyncio.sleep(0)
        weather_provider.release_first.set()
        results = await asyncio.gather(first, second)
        return results, weather_provider.calls

    results, calls = asyncio.run(scenario())

    assert results[0] == results[1]
    assert calls == [("浙江", "杭州", DATE_TEXT)]


def test_overview_refresh_and_voice_query_share_weather_fill(tmp_path):
    async def scenario():
        weather_provider = BlockingFirstWeatherProvider()
        service, identity_store, overview_store, _publisher, _current = _service(
            tmp_path,
            weather_provider=weather_provider,
        )
        _seed_bound_student(identity_store)
        overview_store.set_manual_location("device-1", "浙江", "杭州")
        refresh = asyncio.create_task(
            service.refresh_device("device-1", "weather_test", DATE_TEXT)
        )
        await weather_provider.first_started.wait()
        voice = asyncio.create_task(
            service.query_daily_weather("浙江", "杭州", DATE_TEXT)
        )
        await asyncio.sleep(0)
        weather_provider.release_first.set()
        refresh_result, voice_result = await asyncio.gather(refresh, voice)
        return refresh_result, voice_result, weather_provider.calls

    refresh_result, voice_result, calls = asyncio.run(scenario())

    assert refresh_result["payload"]["weather"]["available"] is True
    assert voice_result.weather_text == "多云"
    assert calls == [("浙江", "杭州", DATE_TEXT)]


def test_voice_weather_provider_failure_does_not_fill_cache(tmp_path):
    weather_provider = FakeWeatherProvider(error=RuntimeError("provider down"))
    service, _identity_store, overview_store, _publisher, _current = _service(
        tmp_path,
        weather_provider=weather_provider,
    )

    try:
        asyncio.run(
            service.query_daily_weather("浙江", "杭州", DATE_TEXT)
        )
    except RuntimeError as exc:
        assert str(exc) == "provider down"
    else:
        raise AssertionError("provider failure must propagate")

    assert overview_store.get_daily_weather(
        "浙江",
        "杭州",
        DATE_TEXT,
        "open_meteo",
    ) is None


def test_voice_weather_cache_write_failure_propagates(tmp_path):
    weather_provider = FakeWeatherProvider()
    service, _identity_store, overview_store, _publisher, _current = _service(
        tmp_path,
        weather_provider=weather_provider,
    )

    def fail_write(_weather, _provider):
        raise RuntimeError("cache write failed")

    overview_store.put_daily_weather = fail_write

    try:
        asyncio.run(
            service.query_daily_weather("浙江", "杭州", DATE_TEXT)
        )
    except RuntimeError as exc:
        assert str(exc) == "cache write failed"
    else:
        raise AssertionError("cache write failure must propagate")

    assert weather_provider.calls == [("浙江", "杭州", DATE_TEXT)]


def test_voice_weather_does_not_reuse_device_cache_for_another_city(tmp_path):
    weather_provider = FakeWeatherProvider()
    service, _identity_store, overview_store, _publisher, _current = _service(
        tmp_path,
        weather_provider=weather_provider,
    )
    overview_store.set_manual_location("device-1", "浙江", "杭州")
    overview_store.put_daily_weather(
        DailyWeather(
            province="浙江",
            city="杭州",
            date=DATE_TEXT,
            weather_code=61,
            weather_text="小雨",
            temperature_min_c=26,
            temperature_max_c=34,
            fetched_at="2026-07-10T06:05:00+00:00",
            timezone_id="Asia/Shanghai",
            country_code="CN",
        ),
        "open_meteo",
    )

    result = asyncio.run(
        service.query_daily_weather(
            "",
            "北京",
            DATE_TEXT,
            device_id="device-1",
        )
    )

    assert result.city == "北京"
    assert weather_provider.calls == [("", "北京", DATE_TEXT)]


def test_refresh_device_builds_bound_weather_course_todo_payload(tmp_path):
    service, identity_store, overview_store, publisher, _current = _service(tmp_path)
    user = _seed_bound_student(identity_store)
    overview_store.set_manual_location("device-1", "浙江", "杭州")

    result = asyncio.run(
        service.refresh_device("device-1", "course_created", DATE_TEXT)
    )

    payload = result["payload"]
    assert payload["type"] == "xiaoxin_overview_update"
    assert payload["version"] == 1
    assert payload["device_id"] == "device-1"
    assert payload["bound"] is True
    assert payload["weather"]["summary"] == "杭州 · 多云"
    assert payload["weather"]["detail"] == "今日 26～35℃"
    assert payload["course"]["title"] == "体育 15:25"
    assert payload["todo"]["count"] == 1
    assert len(publisher.published) == 1
    encoded = json.dumps(payload, ensure_ascii=False)
    assert user.id not in encoded
    assert "openid" not in encoded
    assert "user_id" not in encoded
    assert "student_no" not in encoded


def test_refresh_without_companion_mind_keeps_legacy_overview_payload(tmp_path):
    service, identity_store, _store, _publisher, _current = _service(tmp_path)
    _seed_bound_student(identity_store)

    payload = asyncio.run(
        service.refresh_device("device-1", "course_created", DATE_TEXT)
    )["payload"]

    assert "companion" not in payload


def test_overview_projects_one_growth_moment_to_miniprogram_and_hardware(
    tmp_path,
    monkeypatch,
):
    growth_moment = {
        "moment_id": "growth-1",
        "from_stage": "freshman",
        "to_stage": "sophomore",
        "xiaoxin_age": 2,
        "safe_summary": "我们一起走进大二了。",
        "occurred_at": "2026-07-10T07:00:00+00:00",
        "evidence_id": "private-evidence",
        "chapters": ["private-chapter"],
    }
    mind = FakeCompanionMind(
        CompanionProjection(
            surface="miniprogram",
            xiaoxin_age=2,
            relationship_stage="familiar",
            payload={"growth_moment": growth_moment},
        )
    )
    monkeypatch.setattr(
        identity_store_module,
        "utc_now_iso",
        lambda: "2026-07-01T08:00:00+08:00",
    )
    service, identity_store, _store, _publisher, _current = _service(
        tmp_path,
        companion_mind=mind,
    )
    user, _profile = identity_store.get_or_create_student_by_openid(
        "wx-growth-openid",
        "Student A",
    )
    identity_store.upsert_seen_device("device-1")
    identity_store.bind_device("device-1", user.id, "Desk Xiaoxin")
    identity_store.update_student_profile(user.id, {"grade": "sophomore"})
    subject = identity_store.get_or_create_memory_subject(
        user.id,
        "device-1",
        None,
        "user_speaker",
        "Student A",
    )

    overview = service.build_student_overview(user.id, DATE_TEXT)
    payload = asyncio.run(
        service.refresh_device("device-1", "profile_updated", DATE_TEXT)
    )["payload"]

    assert overview["petStatus"]["academicStage"] == "sophomore"
    assert overview["petStatus"]["xiaoxinAge"] == 2
    assert overview["petStatus"]["relationshipStage"] == "familiar"
    assert overview["petStatus"]["growthMoment"] == {
        "momentId": "growth-1",
        "fromStage": "freshman",
        "toStage": "sophomore",
        "xiaoxinAge": 2,
        "safeSummary": "我们一起走进大二了。",
        "occurredAt": "2026-07-10T07:00:00+00:00",
    }
    assert payload["companion"] == {
        "xiaoxin_age": 2,
        "academic_stage": "sophomore",
        "growth_moment_id": "growth-1",
        "growth_summary": "我们一起走进大二了。",
        "expression": "growth",
    }
    assert set(payload["companion"]) == {
        "xiaoxin_age",
        "academic_stage",
        "growth_moment_id",
        "growth_summary",
        "expression",
    }
    assert mind.requests[-1].subject.memory_subject_id == subject.id


def test_overview_skips_companion_projection_when_subject_is_ambiguous(
    tmp_path,
    monkeypatch,
):
    mind = FakeCompanionMind(
        CompanionProjection(
            surface="miniprogram",
            xiaoxin_age=2,
            relationship_stage="familiar",
        )
    )
    monkeypatch.setattr(
        identity_store_module,
        "utc_now_iso",
        lambda: "2026-07-01T08:00:00+08:00",
    )
    service, identity_store, _store, _publisher, _current = _service(
        tmp_path,
        companion_mind=mind,
    )
    user = _seed_bound_student(identity_store)
    identity_store.get_or_create_memory_subject(
        user.id, "device-1", None, "user_speaker", "Student A"
    )
    identity_store.upsert_seen_device("device-2")
    identity_store.bind_device("device-2", user.id, "Second Xiaoxin")
    identity_store.get_or_create_memory_subject(
        user.id, "device-2", None, "user_speaker", "Student A duplicate"
    )

    overview = service.build_student_overview(user.id, DATE_TEXT)
    payload = asyncio.run(
        service.refresh_device("device-1", "profile_updated", DATE_TEXT)
    )["payload"]

    assert "xiaoxinAge" not in overview["petStatus"]
    assert "companion" not in payload
    assert mind.requests == []


def test_hardware_growth_summary_is_truncated_on_a_utf8_boundary(tmp_path):
    service, _identity_store, _store, _publisher, _current = _service(tmp_path)

    companion = service._wire_companion_card(
        {
            "academicStage": "sophomore",
            "xiaoxinAge": 2,
            "growthMoment": {
                "momentId": "growth-1",
                "safeSummary": "成" * 30,
            },
        }
    )

    assert companion is not None
    assert companion["growth_summary"] == "成" * 21
    assert len(companion["growth_summary"].encode("utf-8")) <= 63


def test_hardware_unknown_academic_stage_keeps_null_age(tmp_path):
    service, _identity_store, _store, _publisher, _current = _service(tmp_path)

    companion = service._wire_companion_card(
        {
            "academicStage": "unknown",
            "xiaoxinAge": None,
        }
    )

    assert companion == {
        "xiaoxin_age": None,
        "academic_stage": "unknown",
        "growth_moment_id": "",
        "growth_summary": "",
        "expression": "idle",
    }


def test_unbound_device_does_not_reset_personal_pet_companion_age(
    tmp_path,
    monkeypatch,
):
    service, identity_store, _overview_store, _publisher, _current = _service(
        tmp_path
    )
    user, _profile = identity_store.get_or_create_student_by_openid(
        "wx-openid-1",
        "小杭",
    )
    identity_store.upsert_seen_device("device-1")
    monkeypatch.setattr(
        identity_store_module,
        "utc_now_iso",
        lambda: "2026-09-01T08:00:00+08:00",
    )
    identity_store.bind_device("device-1", user.id, "桌面小芯")

    assert identity_store.unbind_device("device-1", user.id) is True
    overview = service.build_student_overview(user.id, "2026-09-03")

    assert overview["device"]["bound"] is False
    assert overview["petStatus"]["companionDays"] == 3
    assert overview["petStatus"]["companionYear"] == 1
    assert overview["petStatus"]["companionStartedAt"] == "2026-09-01T08:00:00+08:00"


def test_invalid_personal_pet_start_does_not_break_overview(tmp_path, caplog):
    service, identity_store, _overview_store, _publisher, _current = _service(
        tmp_path
    )
    user, _profile = identity_store.get_or_create_student_by_openid(
        "wx-openid-1",
        "小林",
    )
    identity_store.upsert_seen_device("device-1")
    identity_store.bind_device("device-1", user.id, "桌面小芯")
    with identity_store._connect() as conn:
        conn.execute(
            """
            UPDATE personal_pets
            SET companion_started_at = ?
            WHERE owner_user_id = ?
            """,
            ("not-a-timestamp", user.id),
        )

    with caplog.at_level("WARNING"):
        overview = service.build_student_overview(user.id, "2026-09-03")

    assert overview["petStatus"]["lifecycleStatus"] == "active"
    assert overview["petStatus"]["companionStartedAt"] == "not-a-timestamp"
    assert overview["petStatus"]["companionDays"] == 0
    assert overview["petStatus"]["companionYear"] == 0
    assert overview["petStatus"]["anniversaryDate"] is None
    assert "invalid personal pet lifecycle" in caplog.text


def test_refresh_device_without_location_uses_canonical_weather_empty_state(tmp_path):
    service, identity_store, _store, _publisher, _current = _service(tmp_path)
    _seed_bound_student(identity_store)

    payload = asyncio.run(
        service.refresh_device("device-1", "course_created", DATE_TEXT)
    )["payload"]

    assert payload["weather"] == {
        "configured": False,
        "available": False,
        "province": "",
        "city": "",
        "date": DATE_TEXT,
        "summary": "天气位置未知",
        "detail": "可在小程序中设置城市",
        "fetched_at": "",
    }
    assert payload["course"]["available_today"] is True
    assert payload["todo"]["count"] == 1


def test_weather_failure_does_not_block_course_or_todo_projection(tmp_path):
    service, identity_store, overview_store, _publisher, _current = _service(
        tmp_path,
        weather_provider=FakeWeatherProvider(error=RuntimeError("provider secret")),
    )
    _seed_bound_student(identity_store)
    overview_store.set_manual_location("device-1", "浙江", "杭州")

    payload = asyncio.run(
        service.refresh_device("device-1", "todo_created", DATE_TEXT)
    )["payload"]

    assert payload["weather"]["configured"] is True
    assert payload["weather"]["available"] is False
    assert payload["weather"]["summary"] == "杭州 · 天气暂不可用"
    assert "provider secret" not in json.dumps(payload, ensure_ascii=False)
    assert payload["course"]["title"] == "体育 15:25"
    assert payload["todo"]["detail"] == "20:30 提交实验报告"


def test_crud_refresh_respects_exhausted_weather_retry_state(tmp_path):
    weather_provider = FakeWeatherProvider()
    service, identity_store, overview_store, _publisher, _current = _service(
        tmp_path,
        weather_provider=weather_provider,
    )
    _seed_bound_student(identity_store)
    overview_store.set_manual_location("device-1", "Zhejiang", "Hangzhou")
    overview_store.record_weather_failure(
        "Zhejiang",
        "Hangzhou",
        DATE_TEXT,
        "open_meteo",
        "provider unavailable",
        attempts=4,
        next_attempt_at=None,
    )

    result = asyncio.run(
        service.refresh_device("device-1", "course_created", DATE_TEXT)
    )

    assert weather_provider.calls == []
    assert result["payload"]["weather"]["configured"] is True
    assert result["payload"]["weather"]["available"] is False


def test_same_business_content_preserves_wire_payload_and_does_not_publish_again(tmp_path):
    service, identity_store, _store, publisher, current = _service(tmp_path)
    _seed_bound_student(identity_store)

    first = asyncio.run(service.refresh_device("device-1", "first", DATE_TEXT))
    current[0] += timedelta(minutes=10)
    second = asyncio.run(service.refresh_device("device-1", "manual", DATE_TEXT))

    assert first["changed"] is True
    assert second["changed"] is False
    assert second["publish_attempted"] is False
    assert second["payload"] == first["payload"]
    assert len(publisher.published) == 1


def test_clear_unbound_device_publishes_higher_revision_without_old_owner_data(tmp_path):
    service, identity_store, _store, publisher, _current = _service(tmp_path)
    user = _seed_bound_student(identity_store)
    first = asyncio.run(service.refresh_device("device-1", "bound", DATE_TEXT))
    assert identity_store.unbind_device("device-1", user.id) is True

    cleared = asyncio.run(service.clear_unbound_device("device-1", "device_unbound"))

    payload = cleared["payload"]
    assert payload["revision"] > first["payload"]["revision"]
    assert payload["bound"] is False
    assert payload["weather"]["summary"] == "设备未绑定"
    assert payload["course"]["title"] == "设备未绑定"
    assert payload["todo"]["count"] == 0
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "体育" not in encoded
    assert "提交实验报告" not in encoded
    assert len(publisher.published) == 2


def test_device_projection_is_isolated_to_its_current_owner(tmp_path):
    service, identity_store, _store, _publisher, _current = _service(tmp_path)
    owner = _seed_bound_student(identity_store)
    other = identity_store.create_user("student-b", "hash", "Student B")
    identity_store.update_student_semester(
        other.id,
        {"startDate": "2026-07-06", "totalWeeks": 18},
    )
    identity_store.create_student_course(
        other.id,
        {
            "title": "机密课程",
            "classroom": "别处",
            "weekday": 5,
            "startSection": 1,
            "endSection": 2,
            "weekRange": "第1-18周",
            "startsAt": "08:00",
        },
    )
    identity_store.create_student_todo(
        other.id,
        {"title": "他人的待办", "dueAt": "2026-07-10T09:00:00+08:00"},
    )

    result = asyncio.run(service.refresh_device("device-1", "manual", DATE_TEXT))
    encoded = json.dumps(result["payload"], ensure_ascii=False)

    assert owner.id not in encoded
    assert other.id not in encoded
    assert "机密课程" not in encoded
    assert "他人的待办" not in encoded
    assert result["payload"]["course"]["title"] == "体育 15:25"


def test_stale_owner_projection_cannot_overwrite_new_owner_snapshot(tmp_path):
    async def scenario():
        provider = BlockingFirstWeatherProvider()
        service, identity_store, store, _publisher, _current = _service(
            tmp_path, weather_provider=provider
        )
        owner_a = _seed_bound_student(identity_store)
        course_a = identity_store.list_student_courses(owner_a.id)[0]
        identity_store.update_student_course(
            owner_a.id, course_a["id"], {"title": "A 的机密课程"}
        )
        store.set_manual_location("device-1", "浙江", "杭州")

        refresh_a = asyncio.create_task(
            service.refresh_device("device-1", "owner_a", DATE_TEXT)
        )
        await provider.first_started.wait()

        assert identity_store.unbind_device("device-1", owner_a.id) is True
        owner_b = identity_store.create_user("student-b", "hash", "Student B")
        identity_store.bind_device("device-1", owner_b.id, "Desk Xiaoxin")
        identity_store.update_student_semester(
            owner_b.id,
            {"startDate": "2026-07-06", "totalWeeks": 18},
        )
        identity_store.create_student_course(
            owner_b.id,
            {
                "title": "B 的课程",
                "classroom": "B 教室",
                "weekday": 5,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "第1-18周",
                "startsAt": "08:00",
            },
        )
        refresh_b = asyncio.create_task(
            service.refresh_device("device-1", "owner_b", DATE_TEXT)
        )
        await asyncio.sleep(0)
        provider.release_first.set()

        result_a, result_b = await asyncio.gather(refresh_a, refresh_b)
        pending = store.list_pending_snapshots("2026-07-11T00:00:00+00:00")
        return result_a, result_b, pending

    result_a, result_b, pending = asyncio.run(scenario())

    assert result_a["discarded"] is True
    assert result_b["discarded"] is False
    assert len(pending) == 1
    encoded = json.dumps(pending[0].payload, ensure_ascii=False)
    assert "B 的课程" in encoded
    assert "A 的机密课程" not in encoded


def test_wire_payload_is_bounded_and_ui_text_is_truncated(tmp_path):
    service, identity_store, overview_store, _publisher, _current = _service(tmp_path)
    user = _seed_bound_student(identity_store)
    courses = identity_store.list_student_courses(user.id)
    identity_store.update_student_course(
        user.id,
        courses[0]["id"],
        {"title": "超长课程" * 100, "classroom": "超长教室" * 100},
    )
    todos = identity_store.list_student_todos(user.id)
    identity_store.update_student_todo(
        user.id,
        todos[0]["id"],
        {"title": "超长待办" * 200},
    )
    overview_store.set_manual_location(
        "device-1", "超长省份" * 100, "超长城市" * 100
    )

    payload = asyncio.run(
        service.refresh_device("device-1", "long_text", DATE_TEXT)
    )["payload"]

    assert len(payload["weather"]["summary"]) <= 48
    assert len(payload["course"]["title"]) <= 48
    assert len(payload["course"]["detail"]) <= 64
    assert len(payload["todo"]["detail"]) <= 64
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) <= 2048


def test_build_student_overview_reuses_cards_without_snapshot_or_mqtt_side_effects(tmp_path):
    service, identity_store, store, publisher, _current = _service(tmp_path)
    user = _seed_bound_student(identity_store)

    overview = service.build_student_overview(user.id, DATE_TEXT, device_id="device-1")

    assert overview["course"]["title"] == "体育 15:25"
    assert overview["todo"]["detail"] == "20:30 提交实验报告"
    assert overview["latestNotification"] is None
    assert publisher.published == []
    assert store.list_pending_snapshots("2026-07-11T00:00:00+00:00") == []


def test_build_student_overview_prioritizes_offline_device_state(tmp_path):
    service, identity_store, _store, _publisher, _current = _service(tmp_path)
    user = _seed_bound_student(identity_store)

    overview = service.build_student_overview(user.id, DATE_TEXT, device_id="device-1")

    assert overview["device"]["state"] == "offline"
    assert overview["todaySummary"]["courseCount"] == 1
    assert overview["todaySummary"]["reminderCount"] == 1
    assert overview["petStatus"]["todayState"] == "设备已离线"
    assert overview["petStatus"]["recentSummary"] == (
        "暂时无法同步课程和提醒，请检查设备网络或电源。"
    )


def test_build_student_overview_reports_ready_only_for_connected_device(tmp_path):
    registry = StaticRegistry(
        [{"device_id": "device-1", "state": "connected"}]
    )
    service, identity_store, _store, _publisher, _current = _service(
        tmp_path, registry=registry
    )
    user = _seed_bound_student(identity_store)

    overview = service.build_student_overview(user.id, DATE_TEXT, device_id="device-1")

    assert overview["device"]["state"] == "connected"
    assert overview["petStatus"]["todayState"] == "已准备好"
    assert overview["petStatus"]["recentSummary"] == "今天有 1 门课程、1 个提醒。"


def test_build_student_overview_reports_wakeable_device_as_standby(tmp_path):
    registry = StaticRegistry(
        [{"device_id": "device-1", "state": "wakeable"}]
    )
    service, identity_store, _store, _publisher, _current = _service(
        tmp_path, registry=registry
    )
    user = _seed_bound_student(identity_store)

    overview = service.build_student_overview(user.id, DATE_TEXT, device_id="device-1")

    assert overview["device"]["state"] == "wakeable"
    assert overview["petStatus"]["todayState"] == "待机中"
    assert overview["petStatus"]["recentSummary"] == (
        "设备已联网，有任务时会自动唤醒。"
    )


def test_curriculum_overview_skips_started_courses_and_includes_reminder_lead_time(
    tmp_path,
):
    service, identity_store, _store, _publisher, _current = _service(tmp_path)
    user = _seed_bound_student(identity_store)
    identity_store.update_student_course_reminder_settings(
        user.id, {"remindBeforeMin": 30}
    )
    identity_store.create_student_course(
        user.id,
        {
            "title": "Evening Seminar",
            "classroom": "Room 204",
            "weekday": 5,
            "startSection": 9,
            "endSection": 10,
            "weekRange": "1-18",
            "startsAt": "16:00",
        },
    )

    overview = service.build_curriculum_overview(
        user.id, DATE_TEXT, include_started=False
    )

    assert overview["nextCourse"]["title"] == "Evening Seminar"
    assert overview["nextCourse"]["remindBeforeMin"] == 30


def test_puback_marks_only_the_exact_device_revision_published(tmp_path):
    publisher = FakePublisher(results=[41, 42])
    service, identity_store, store, _publisher, _current = _service(
        tmp_path, publisher=publisher
    )
    _seed_bound_student(identity_store, "device-1")
    second = identity_store.create_user("student-b", "hash", "Student B")
    identity_store.upsert_seen_device("device-2")
    identity_store.bind_device("device-2", second.id, "Second")

    first_result = asyncio.run(service.refresh_device("device-1", "refresh", DATE_TEXT))
    second_result = asyncio.run(service.refresh_device("device-2", "refresh", DATE_TEXT))
    publisher.ack(42)

    pending = store.list_pending_snapshots("2026-07-11T00:00:00+00:00")
    assert [(item.device_id, item.revision) for item in pending] == [
        ("device-1", first_result["revision"])
    ]
    assert second_result["revision"] == 1


def test_puback_before_publish_return_is_reconciled_without_stale_pending(tmp_path):
    for mode in ("sync", "thread"):
        publisher = EarlyAckPublisher(mode)
        service, identity_store, store, _publisher, _current = _service(
            tmp_path / mode, publisher=publisher
        )
        _seed_bound_student(identity_store)

        result = asyncio.run(
            service.refresh_device("device-1", "refresh", DATE_TEXT)
        )

        assert result["publish_accepted"] is True
        assert store.list_pending_snapshots("2026-07-11T00:00:00+00:00") == []


def test_successful_publish_waits_for_ack_deadline_before_duplicate_delivery(tmp_path):
    publisher = FakePublisher(results=[51, 52])
    service, identity_store, _store, _publisher, current = _service(
        tmp_path, publisher=publisher
    )
    _seed_bound_student(identity_store)

    asyncio.run(service.refresh_device("device-1", "refresh", DATE_TEXT))
    ack_timeout = overview_service_module.PUBLISH_ACK_TIMEOUT_SECONDS
    current[0] += timedelta(seconds=ack_timeout - 1)
    before_deadline = asyncio.run(service.drain_pending())
    current[0] += timedelta(seconds=1)
    at_deadline = asyncio.run(service.drain_pending())

    assert before_deadline == 0
    assert len(publisher.published) == 2
    assert at_deadline == 1


def test_stale_ack_for_reused_mid_does_not_publish_new_attempt(tmp_path):
    publisher = ReusedMidWithStaleAckPublisher()
    service, identity_store, store, _publisher, current = _service(
        tmp_path, publisher=publisher
    )
    _seed_bound_student(identity_store)
    ack_timeout = overview_service_module.PUBLISH_ACK_TIMEOUT_SECONDS

    asyncio.run(service.refresh_device("device-1", "refresh", DATE_TEXT))
    current[0] += timedelta(seconds=ack_timeout)
    assert asyncio.run(service.drain_pending()) == 1

    pending = store.list_pending_snapshots("2026-07-11T00:00:00+00:00")
    assert len(pending) == 1
    assert pending[0].next_attempt_at == (
        current[0] + timedelta(seconds=ack_timeout)
    ).isoformat()


def test_queued_old_session_ack_cannot_publish_reused_mid_in_new_session(tmp_path):
    publisher = FakePublisher(results=[1, 1])
    service, identity_store, store, _publisher, _current = _service(
        tmp_path,
        publisher=publisher,
    )
    user = _seed_bound_student(identity_store)
    service.begin_publish_session(1)
    first = asyncio.run(
        service.refresh_device("device-1", "first", DATE_TEXT)
    )
    queued_old_ack = lambda: service.handle_publish_ack(1, 1)

    service.reset_publish_session()
    service.begin_publish_session(2)
    identity_store.create_student_todo(
        user.id,
        {
            "title": "New task",
            "dueAt": "2026-07-10T21:00:00+08:00",
        },
    )
    second = asyncio.run(
        service.refresh_device("device-1", "todo_created", DATE_TEXT)
    )

    queued_old_ack()
    after_old_ack = store.get_snapshot("device-1")
    service.handle_publish_ack(1, 2)
    after_new_ack = store.get_snapshot("device-1")

    assert second["revision"] > first["revision"]
    assert after_old_ack.revision == second["revision"]
    assert after_old_ack.publish_state == "pending"
    assert after_new_ack.revision == second["revision"]
    assert after_new_ack.publish_state == "published"


def test_publish_refusal_uses_required_backoff_sequence(tmp_path):
    publisher = FakePublisher(results=[None] * 6)
    service, identity_store, store, _publisher, current = _service(
        tmp_path, publisher=publisher
    )
    _seed_bound_student(identity_store)

    asyncio.run(service.refresh_device("device-1", "refresh", DATE_TEXT))
    observed = []
    for expected_delay in [1, 2, 5, 15, 30]:
        snapshot = store.list_pending_snapshots("2026-07-11T00:00:00+00:00")[0]
        next_attempt = datetime.fromisoformat(snapshot.next_attempt_at)
        observed.append(int((next_attempt - current[0]).total_seconds()))
        current[0] = next_attempt
        if expected_delay != 30:
            assert asyncio.run(service.drain_pending()) == 1

    assert observed == [1, 2, 5, 15, 30]


def test_publish_exception_is_converted_to_pending_backoff(tmp_path):
    publisher = RaisingPublisher()
    service, identity_store, store, _publisher, current = _service(
        tmp_path, publisher=publisher
    )
    _seed_bound_student(identity_store)

    result = asyncio.run(
        service.refresh_device("device-1", "course_created", DATE_TEXT)
    )

    pending = store.list_pending_snapshots("2026-07-11T00:00:00+00:00")
    assert result["publish_accepted"] is False
    assert len(pending) == 1
    assert pending[0].publish_attempts == 1
    assert datetime.fromisoformat(pending[0].next_attempt_at) == current[0] + timedelta(
        seconds=1
    )


def test_new_content_overwrites_older_pending_revision(tmp_path):
    publisher = FakePublisher(results=[None, None])
    service, identity_store, store, _publisher, _current = _service(
        tmp_path, publisher=publisher
    )
    user = _seed_bound_student(identity_store)
    old = asyncio.run(service.refresh_device("device-1", "old", DATE_TEXT))
    todo = identity_store.list_student_todos(user.id)[0]
    identity_store.update_student_todo(
        user.id, todo["id"], {"title": "新内容"}
    )

    new = asyncio.run(service.refresh_device("device-1", "new", DATE_TEXT))

    pending = store.list_pending_snapshots("2026-07-11T00:00:00+00:00")
    assert len(pending) == 1
    assert pending[0].revision == old["revision"] + 1 == new["revision"]
    assert "新内容" in json.dumps(pending[0].payload, ensure_ascii=False)
    assert pending[0].publish_attempts == 1


def test_drain_pending_publishes_due_snapshots(tmp_path):
    publisher = FakePublisher(results=[77])
    service, identity_store, store, _publisher, _current = _service(
        tmp_path, publisher=publisher
    )
    user = _seed_bound_student(identity_store)
    overview = service.build_student_overview(user.id, DATE_TEXT, device_id="device-1")
    snapshot, changed = store.upsert_snapshot(
        "device-1",
        user.id,
        {
            "bound": True,
            "weather": overview["weather"],
            "course": overview["course"],
            "todo": overview["todo"],
        },
        "2026-07-10T07:30:00+00:00",
    )
    assert changed is True

    assert asyncio.run(service.drain_pending()) == 1
    assert publisher.published[0][1]["revision"] == snapshot.revision


def test_drain_waits_for_device_refresh_and_never_publishes_stale_owner(tmp_path):
    async def scenario():
        provider = BlockingFirstWeatherProvider()
        publisher = FakePublisher()
        service, identity_store, store, _publisher, _current = _service(
            tmp_path,
            weather_provider=provider,
            publisher=publisher,
        )
        owner_a = _seed_bound_student(identity_store)
        _seed_stale_owner_snapshot(store, owner_a.id)
        _rebind_to_second_student(identity_store, owner_a)
        store.set_manual_location("device-1", "浙江", "杭州")

        refresh_b = asyncio.create_task(
            service.refresh_device("device-1", "owner_b", DATE_TEXT)
        )
        await provider.first_started.wait()
        drain = asyncio.create_task(service.drain_pending())
        await asyncio.sleep(0)
        provider.release_first.set()
        await asyncio.gather(refresh_b, drain)
        return publisher.published

    published = asyncio.run(scenario())
    encoded = json.dumps(published, ensure_ascii=False)

    assert "B 的课程" in encoded
    assert "A 的旧课程" not in encoded
    assert "A 的旧待办" not in encoded


def test_restart_drain_coalesces_stale_owner_snapshot_to_current_owner(tmp_path):
    publisher = FakePublisher()
    service, identity_store, store, _publisher, _current = _service(
        tmp_path,
        publisher=publisher,
    )
    owner_a = _seed_bound_student(identity_store)
    stale = _seed_stale_owner_snapshot(store, owner_a.id)
    owner_b = _rebind_to_second_student(identity_store, owner_a)

    assert asyncio.run(service.drain_pending()) == 1

    assert len(publisher.published) == 1
    payload = publisher.published[0][1]
    assert payload["revision"] > stale.revision
    assert "B 的课程" in json.dumps(payload, ensure_ascii=False)
    assert "A 的旧课程" not in json.dumps(payload, ensure_ascii=False)
    pending = store.list_pending_snapshots("2026-07-11T00:00:00+00:00")
    assert len(pending) == 1
    assert pending[0].owner_user_id == owner_b.id
    assert pending[0].revision == payload["revision"]


def test_observe_device_ip_requires_configured_hmac_key(tmp_path):
    current, clock = _clock()
    identity_store = XiaoxinIdentityStore(tmp_path / "identity.db")
    overview_store = XiaoxinOverviewStore(tmp_path / "overview.db", clock=clock)
    _seed_bound_student(identity_store)
    location_provider = FakeLocationProvider(
        IpCityLocation(
            province="浙江",
            city="杭州",
            country_code="CN",
            located_at="2026-07-10T07:30:00+00:00",
        )
    )
    service = OverviewSyncService(
        identity_store=identity_store,
        overview_store=overview_store,
        ip_location_provider=location_provider,
        clock=clock,
    )

    result = asyncio.run(
        service.observe_device_ip("device-1", "8.8.8.8", "heartbeat")
    )

    assert result["error_code"] == "overview_ip_hmac_unconfigured"
    assert result["refreshed"] is False
    assert location_provider.calls == []
    assert overview_store.get_location("device-1") is None


def test_observe_same_ip_uses_hmac_cache_without_provider_call(tmp_path):
    current, clock = _clock()
    identity_store = XiaoxinIdentityStore(tmp_path / "identity.db")
    overview_store = XiaoxinOverviewStore(tmp_path / "overview.db", clock=clock)
    _seed_bound_student(identity_store)
    provider = FakeLocationProvider(IpCityLocation(
        province="Zhejiang", city="Hangzhou", country_code="CN",
        located_at="2026-07-10T07:30:00+00:00",
    ))
    service = OverviewSyncService(
        identity_store=identity_store, overview_store=overview_store,
        weather_provider=FakeWeatherProvider(), ip_location_provider=provider,
        clock=clock, ip_hmac_key=b"test-key",
    )

    asyncio.run(service.observe_device_ip("device-1", "8.8.8.8", "first"))
    provider.calls.clear()
    result = asyncio.run(
        service.observe_device_ip("device-1", "8.8.8.8", "same")
    )

    assert provider.calls == []
    assert result["location_changed"] is False
    assert result["refreshed"] is False


def test_concurrent_same_ip_observation_calls_provider_once(tmp_path):
    async def scenario():
        current, clock = _clock()
        identity_store = XiaoxinIdentityStore(tmp_path / "identity.db")
        overview_store = XiaoxinOverviewStore(tmp_path / "overview.db", clock=clock)
        _seed_bound_student(identity_store)
        provider = BlockingLocationProvider(IpCityLocation(
            province="Zhejiang", city="Hangzhou", country_code="CN",
            located_at="2026-07-10T07:30:00+00:00",
        ))
        service = OverviewSyncService(
            identity_store=identity_store, overview_store=overview_store,
            weather_provider=FakeWeatherProvider(), ip_location_provider=provider,
            clock=clock, ip_hmac_key=b"test-key",
        )
        first = asyncio.create_task(
            service.observe_device_ip("device-1", "8.8.8.8", "first")
        )
        await provider.started.wait()
        second = asyncio.create_task(
            service.observe_device_ip("device-1", "8.8.8.8", "second")
        )
        provider.release.set()
        await asyncio.gather(first, second)
        return provider.calls

    assert asyncio.run(scenario()) == ["8.8.8.8"]


def test_first_weather_failure_persists_600_second_retry_and_coalesces_city(tmp_path):
    async def scenario():
        provider = FakeWeatherProvider(error=RuntimeError("provider secret"))
        service, identity_store, store, _publisher, current = _service(
            tmp_path, weather_provider=provider
        )
        first_user = _seed_bound_student(identity_store, "device-1")
        second_user = identity_store.create_user("student-b", "hash", "Student B")
        identity_store.upsert_seen_device("device-2")
        identity_store.bind_device("device-2", second_user.id, "Desk 2")
        store.set_manual_location("device-1", "Zhejiang", "Hangzhou")
        store.set_manual_location("device-2", "Zhejiang", "Hangzhou")
        await asyncio.gather(
            service.refresh_device("device-1", "course_created", DATE_TEXT),
            service.refresh_device("device-2", "todo_created", DATE_TEXT),
        )
        retry = store.get_weather_retry_state(
            "Zhejiang", "Hangzhou", DATE_TEXT, "open_meteo"
        )
        return provider.calls, retry, current[0]

    calls, retry, now = asyncio.run(scenario())

    assert len(calls) == 1
    assert retry["fetch_attempts"] == 1
    assert retry["next_attempt_at"] == (now + timedelta(seconds=600)).isoformat()
    assert retry["last_error"] == "overview_weather_fetch_failed"
