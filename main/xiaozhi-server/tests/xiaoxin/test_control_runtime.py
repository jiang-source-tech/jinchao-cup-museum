import asyncio
import logging
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.xiaoxin.control_runtime import (
    _repair_todo_reminder_outcomes_from_history,
    create_xiaoxin_control_runtime,
)
from core.xiaoxin.control_types import (
    XiaoxinControlEventRequest,
    XiaoxinFailureReason,
    XiaoxinDeliveryState,
    XiaoxinEvent,
)
from core.xiaoxin.companion import (
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.doorbell_client import (
    DoorbellMqttSettings,
    XiaoxinDoorbellClient,
)
from core.xiaoxin.overview.models import DailyWeather


def _install_module(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    for attr_name, attr_value in attrs.items():
        setattr(module, attr_name, attr_value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _overview_runtime_config(tmp_path, *, enabled=True):
    return {
        "server": {},
        "xiaoxin_control": {
            "identity_db": str(tmp_path / "identity.db"),
            "notification_history_db": str(tmp_path / "history.db"),
            "activation_db": str(tmp_path / "activation.db"),
            "doorbell_credentials_db": str(tmp_path / "doorbell.db"),
            "overview_mqtt": {
                "enabled": enabled,
                "db": str(tmp_path / "overview.db"),
                "ip_hmac_secret": "",
                "retry_tick_seconds": 0.01,
                "daily_refresh_hour": 0,
                "daily_refresh_minute": 5,
            },
        },
    }


def test_runtime_exposes_overview_service_and_store(tmp_path):
    runtime = create_xiaoxin_control_runtime(_overview_runtime_config(tmp_path))

    assert runtime.overview_store.db_path == tmp_path / "overview.db"
    assert runtime.overview_service.overview_store is runtime.overview_store
    assert runtime.overview_service.identity_store is runtime.identity_store
    assert runtime.overview_service.registry is runtime.registry


def test_runtime_selects_amap_weather_provider_with_environment_overrides(
    tmp_path, monkeypatch
):
    captured = {}

    class FakeAmapWeatherProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "core.xiaoxin.control_runtime.AmapWeatherProvider",
        FakeAmapWeatherProvider,
    )
    monkeypatch.setenv("XIAOXIN_AMAP_API_KEY", "environment-key")
    monkeypatch.setenv("XIAOXIN_AMAP_API_HOST", "weather.example.test")
    config = _overview_runtime_config(tmp_path)
    config["xiaoxin_control"]["overview_mqtt"].update(
        {
            "weather_provider": "amap",
            "amap_api_key": "yaml-key",
            "amap_api_host": "restapi.amap.com",
            "amap_city_adcodes": {"浙江/杭州": "330100"},
        }
    )

    runtime = create_xiaoxin_control_runtime(config)

    assert isinstance(runtime.overview_weather_provider, FakeAmapWeatherProvider)
    assert runtime.overview_service.weather_provider_name == "amap"
    assert captured == {
        "api_key": "environment-key",
        "api_host": "weather.example.test",
        "city_adcodes": {"浙江/杭州": "330100"},
    }


def test_disabled_overview_runtime_does_not_touch_db_or_construct_providers(
    tmp_path, monkeypatch
):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("blocked", encoding="utf-8")
    config = _overview_runtime_config(tmp_path, enabled=False)
    config["xiaoxin_control"]["overview_mqtt"]["db"] = str(
        blocked_parent / "overview.db"
    )

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("disabled overview constructed an external provider")

    monkeypatch.setattr(
        "core.xiaoxin.control_runtime.OpenMeteoWeatherProvider", fail_provider
    )
    monkeypatch.setattr(
        "core.xiaoxin.control_runtime.PconlineIpLocationProvider", fail_provider
    )

    runtime = create_xiaoxin_control_runtime(config)
    user = runtime.identity_store.create_user("student", "hash", "Student")
    projection = runtime.overview_service.build_student_overview(
        user.id, "2026-07-10"
    )
    disabled_results = asyncio.run(
        runtime.overview_service.refresh_device("device-1", "manual_resync")
    )

    assert runtime.overview_enabled is False
    assert runtime.overview_store is None
    assert projection["date"] == "2026-07-10"
    assert disabled_results["error_code"] == "overview_mqtt_disabled"
    assert not (blocked_parent / "overview.db").exists()


def test_empty_overview_hmac_secret_is_not_replaced_with_a_hardcoded_key(tmp_path):
    runtime = create_xiaoxin_control_runtime(_overview_runtime_config(tmp_path))

    result = asyncio.run(
        runtime.overview_service.observe_device_ip(
            "device-1",
            "8.8.8.8",
            "test",
        )
    )

    assert result["error_code"] == "overview_ip_hmac_unconfigured"


def test_overview_hmac_environment_variable_overrides_yaml(tmp_path, monkeypatch):
    config = _overview_runtime_config(tmp_path)
    config["xiaoxin_control"]["overview_mqtt"]["ip_hmac_secret"] = "yaml-secret"
    monkeypatch.setenv("XIAOXIN_OVERVIEW_IP_HMAC_SECRET", "environment-secret")

    runtime = create_xiaoxin_control_runtime(config)

    assert runtime.overview_service._ip_hmac_key == b"environment-secret"


def test_empty_overview_hmac_environment_variable_falls_back_to_yaml(
    tmp_path, monkeypatch
):
    config = _overview_runtime_config(tmp_path)
    config["xiaoxin_control"]["overview_mqtt"]["ip_hmac_secret"] = "yaml-secret"
    monkeypatch.setenv("XIAOXIN_OVERVIEW_IP_HMAC_SECRET", "")

    runtime = create_xiaoxin_control_runtime(config)

    assert runtime.overview_service._ip_hmac_key == b"yaml-secret"


def test_runtime_start_registers_overview_listeners_once_and_stop_cancels_loop(
    tmp_path,
):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        tick_started = asyncio.Event()
        tick_cancelled = asyncio.Event()

        class FakeDoorbellClient:
            def __init__(self):
                self.connect_listeners = []
                self.ack_listeners = []
                self.start_calls = 0
                self.stop_calls = 0
                self.publish_session_generation = 1

            def add_connect_listener(self, listener):
                self.connect_listeners.append(listener)

            def add_publish_ack_listener(self, listener):
                self.ack_listeners.append(listener)

            def start(self, loop):
                self.start_calls += 1

            def stop(self):
                self.stop_calls += 1

            def publish_overview(self, device_id, payload):
                return None

        class FakeOverviewService:
            publisher = None

            def begin_publish_session(self, generation):
                return None

            async def drain_pending(self):
                tick_started.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    tick_cancelled.set()
                    raise

            def handle_publish_ack(self, mid, generation):
                return None

            def reset_publish_session(self):
                return None

        fake_client = FakeDoorbellClient()
        runtime.doorbell_client = fake_client
        runtime.overview_service = FakeOverviewService()

        await runtime.start()
        first_task = runtime._overview_task
        await runtime.start()
        await asyncio.wait_for(tick_started.wait(), timeout=0.5)
        await runtime.stop()

        return runtime, fake_client, first_task, tick_cancelled.is_set()

    runtime, fake_client, first_task, tick_cancelled = asyncio.run(scenario())

    assert len(fake_client.connect_listeners) == 1
    assert len(fake_client.ack_listeners) == 1
    assert fake_client.start_calls == 2
    assert first_task is not None
    assert runtime._overview_task is None
    assert tick_cancelled is True
    assert fake_client.stop_calls == 1


def test_runtime_resets_publish_session_only_after_old_client_stops(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        lifecycle = []

        class FakeDoorbellClient:
            publish_session_generation = 1

            def add_connect_listener(self, listener):
                return None

            def add_publish_ack_listener(self, listener):
                return None

            def start(self, loop):
                lifecycle.append("start")

            def stop(self):
                lifecycle.append("stop")

            def publish_overview(self, device_id, payload):
                return 1

        class FakeOverviewService:
            publisher = None

            def begin_publish_session(self, generation):
                return None

            async def drain_pending(self):
                return 0

            def handle_publish_ack(self, mid, generation):
                return None

            def reset_publish_session(self):
                lifecycle.append("reset")

        runtime.doorbell_client = FakeDoorbellClient()
        runtime.overview_service = FakeOverviewService()
        await runtime.start()
        await runtime.stop()
        return lifecycle

    assert asyncio.run(scenario())[-2:] == ["stop", "reset"]


def test_new_mqtt_session_can_reuse_old_inflight_mid(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        runtime.overview_retry_tick_seconds = 60
        runtime.overview_clock = lambda: datetime(
            2026,
            7,
            10,
            0,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        )

        class ReusingMidDoorbellClient:
            def __init__(self):
                self.ack_listeners = []
                self.publish_session_generation = 0

            def add_connect_listener(self, listener):
                return None

            def add_publish_ack_listener(self, listener):
                self.ack_listeners.append(listener)

            def start(self, loop):
                self.publish_session_generation += 1

            def stop(self):
                return None

            def publish_overview(self, device_id, payload):
                return 1

            def ack(self, mid):
                for listener in tuple(self.ack_listeners):
                    listener(mid, self.publish_session_generation)

        client = ReusingMidDoorbellClient()
        runtime.doorbell_client = client
        user = runtime.identity_store.create_user("student", "hash", "Student")
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        runtime.identity_store.update_student_semester(
            user.id,
            {
                "startDate": "2026-07-06",
                "totalWeeks": 18,
            },
        )

        await runtime.start()
        first = await runtime.overview_service.refresh_device(
            "device-1",
            "first",
            "2026-07-10",
        )
        await runtime.stop()

        await runtime.start()
        runtime.identity_store.create_student_todo(
            user.id,
            {
                "title": "New task",
                "dueAt": "2026-07-10T20:00:00+08:00",
            },
        )
        second = await runtime.overview_service.refresh_device(
            "device-1",
            "todo_created",
            "2026-07-10",
        )
        client.ack(1)
        snapshot = runtime.overview_store.get_snapshot("device-1")
        await runtime.stop()
        return first, second, snapshot

    first, second, snapshot = asyncio.run(scenario())

    assert second["revision"] > first["revision"]
    assert snapshot.revision == second["revision"]
    assert snapshot.publish_state == "published"


def test_first_overview_publish_after_startup_connect_failure_keeps_ack_mapping(
    tmp_path,
):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        runtime.overview_retry_tick_seconds = 60
        runtime.overview_clock = lambda: datetime(
            2026,
            7,
            10,
            0,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        )

        class RefusingPahoClient:
            def connect(self, host, port, keepalive):
                raise ConnectionRefusedError("startup refused")

        class RecoveringPahoClient:
            def __init__(self):
                self.on_connect = None
                self.on_message = None
                self.on_publish = None

            def connect(self, host, port, keepalive):
                return None

            def loop_start(self):
                self.on_connect(self, None, None, 0, None)

            def loop_stop(self):
                return None

            def disconnect(self):
                return None

            def subscribe(self, topic, qos=0):
                return None

            def publish(self, topic, payload, qos=0, retain=False):
                return SimpleNamespace(rc=0, mid=1)

        recovering = RecoveringPahoClient()
        paho_clients = iter((RefusingPahoClient(), recovering))
        runtime.doorbell_client = XiaoxinDoorbellClient(
            DoorbellMqttSettings(host="localhost", port=1883),
            runtime.registry,
            tenant=runtime.doorbell_client.tenant,
            client_factory=lambda: next(paho_clients),
        )
        user = runtime.identity_store.create_user("student", "hash", "Student")
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        runtime.identity_store.update_student_semester(
            user.id,
            {
                "startDate": "2026-07-06",
                "totalWeeks": 18,
            },
        )

        await runtime.start()
        generation_after_failed_start = (
            runtime.doorbell_client.publish_session_generation
        )
        result = await runtime.overview_service.refresh_device(
            "device-1",
            "first",
            "2026-07-10",
        )
        await asyncio.sleep(0)
        recovering.on_publish(recovering, None, 1, None, None)
        await asyncio.sleep(0)
        snapshot = runtime.overview_store.get_snapshot("device-1")
        await runtime.stop()
        return generation_after_failed_start, result, snapshot

    generation_after_failed_start, result, snapshot = asyncio.run(scenario())

    assert generation_after_failed_start is None
    assert result["publish_accepted"] is True
    assert snapshot.publish_state == "published"


def test_overview_connect_listener_wakes_single_loop_without_blocking_callback(
    tmp_path,
):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        second_drain = asyncio.Event()
        drain_calls = 0

        class FakeDoorbellClient:
            def __init__(self):
                self.connect_listener = None
                self.publish_session_generation = 1

            def add_connect_listener(self, listener):
                self.connect_listener = listener

            def add_publish_ack_listener(self, listener):
                return None

            def start(self, loop):
                return None

            def stop(self):
                return None

            def publish_overview(self, device_id, payload):
                return None

        class FakeOverviewService:
            publisher = None

            def begin_publish_session(self, generation):
                return None

            async def drain_pending(self):
                nonlocal drain_calls
                drain_calls += 1
                if drain_calls >= 2:
                    second_drain.set()
                return 0

            def handle_publish_ack(self, mid, generation):
                return None

            def reset_publish_session(self):
                return None

        fake_client = FakeDoorbellClient()
        runtime.doorbell_client = fake_client
        runtime.overview_service = FakeOverviewService()
        runtime.overview_retry_tick_seconds = 60

        await runtime.start()
        while drain_calls < 1:
            await asyncio.sleep(0)
        fake_client.connect_listener()
        await asyncio.wait_for(second_drain.wait(), timeout=0.5)
        task = runtime._overview_task
        await runtime.stop()
        return drain_calls, task

    drain_calls, task = asyncio.run(scenario())

    assert drain_calls == 2
    assert task is not None


def test_overview_tick_drains_pending_and_processes_due_weather_retry(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        now = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
        calls = []

        class FakeOverviewStore:
            def list_due_weather_retries(self, now_iso):
                calls.append(("due", now_iso))
                return [
                    {
                        "province": "Zhejiang",
                        "city": "Hangzhou",
                        "country_code": "CN",
                        "date": "2026-07-10",
                        "provider": "open_meteo",
                        "fetch_attempts": 1,
                    }
                ]

            def put_daily_weather(self, weather, provider):
                calls.append(("put", weather.city, provider))

        class FakeOverviewService:
            publisher = None

            async def drain_pending(self):
                calls.append(("drain",))
                return 0

            async def refresh_device(self, device_id, reason, date_text=None):
                calls.append(("refresh", device_id, reason, date_text))

        class FakeWeatherProvider:
            async def daily(self, province, city, date_text):
                calls.append(("weather", province, city, date_text))
                return DailyWeather(
                    province=province,
                    city=city,
                    country_code="CN",
                    date=date_text,
                    weather_code=1,
                    weather_text="Clear",
                    temperature_min_c=20,
                    temperature_max_c=30,
                    fetched_at=now.isoformat(),
                    timezone_id="Asia/Shanghai",
                )

        runtime.overview_store = FakeOverviewStore()
        runtime.overview_service = FakeOverviewService()
        runtime.overview_weather_provider = FakeWeatherProvider()
        runtime.identity_store.list_all_devices = lambda: [
            SimpleNamespace(
                device_id="device-1",
                owner_user_id="user-1",
                bind_status="bound",
            )
        ]
        runtime.overview_store.get_location = lambda device_id: {
            "province": "Zhejiang",
            "city": "Hangzhou",
            "country_code": "CN",
        }

        await runtime._run_overview_tick(now)
        return calls

    calls = asyncio.run(scenario())

    assert calls[0] == ("drain",)
    assert calls[1][0] == "due"
    assert ("weather", "Zhejiang", "Hangzhou", "2026-07-10") in calls
    assert (
        "refresh",
        "device-1",
        "weather_retry",
        "2026-07-10",
    ) in calls


def test_weather_retry_backoff_stops_after_third_failed_retry(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        now = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
        recorded = []

        class FakeOverviewStore:
            def record_weather_failure(
                self,
                province,
                city,
                date_text,
                provider,
                error,
                attempts,
                next_attempt_at,
                country_code="CN",
            ):
                recorded.append((attempts, next_attempt_at))

        class FailingWeatherProvider:
            async def daily(self, province, city, date_text):
                raise RuntimeError("provider unavailable")

        runtime.overview_store = FakeOverviewStore()
        runtime.overview_weather_provider = FailingWeatherProvider()
        for previous_attempts in range(4):
            await runtime._refresh_weather_entry(
                {
                    "province": "Zhejiang",
                    "city": "Hangzhou",
                    "country_code": "CN",
                    "date": "2026-07-10",
                    "provider": "open_meteo",
                    "fetch_attempts": previous_attempts,
                },
                now,
            )
        return recorded

    recorded = asyncio.run(scenario())

    assert recorded == [
        (1, (datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc) + timedelta(seconds=600)).isoformat()),
        (2, (datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc) + timedelta(seconds=1800)).isoformat()),
        (3, (datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc) + timedelta(seconds=7200)).isoformat()),
        (4, None),
    ]


def test_daily_overview_refresh_runs_once_after_configured_local_time(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        refreshes = []

        class FakeOverviewStore:
            def list_due_weather_retries(self, now_iso):
                return []

            def get_location(self, device_id):
                return None

        class FakeOverviewService:
            publisher = None

            async def drain_pending(self):
                return 0

            async def refresh_device(self, device_id, reason, date_text=None):
                refreshes.append((device_id, reason, date_text))

        runtime.overview_store = FakeOverviewStore()
        runtime.overview_service = FakeOverviewService()
        runtime.identity_store.list_all_devices = lambda: [
            SimpleNamespace(
                device_id="device-1",
                owner_user_id="user-1",
                bind_status="bound",
            )
        ]
        local_tz = timezone(timedelta(hours=8))

        await runtime._run_overview_tick(
            datetime(2026, 7, 10, 0, 4, 59, tzinfo=local_tz)
        )
        await runtime._run_overview_tick(
            datetime(2026, 7, 10, 0, 5, 0, tzinfo=local_tz)
        )
        await runtime._run_overview_tick(
            datetime(2026, 7, 10, 23, 0, 0, tzinfo=local_tz)
        )
        await runtime._run_overview_tick(
            datetime(2026, 7, 11, 0, 5, 0, tzinfo=local_tz)
        )
        return refreshes

    refreshes = asyncio.run(scenario())

    assert refreshes == [
        ("device-1", "weather_day_changed", "2026-07-10"),
        ("device-1", "weather_day_changed", "2026-07-11"),
    ]


def test_daily_overview_refresh_uses_shanghai_date_when_clock_is_utc(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        refreshes = []

        class FakeOverviewStore:
            def list_due_weather_retries(self, now_iso):
                return []

            def get_location(self, device_id):
                return None

        class FakeOverviewService:
            publisher = None

            async def drain_pending(self):
                return 0

            async def refresh_device(self, device_id, reason, date_text=None):
                refreshes.append((device_id, reason, date_text))

        runtime.overview_store = FakeOverviewStore()
        runtime.overview_service = FakeOverviewService()
        runtime.identity_store.list_all_devices = lambda: [
            SimpleNamespace(
                device_id="device-1",
                owner_user_id="user-1",
                bind_status="bound",
            )
        ]
        runtime._overview_last_daily_refresh_date = "2026-07-12"

        await runtime._run_overview_tick(
            datetime(2026, 7, 12, 16, 5, 0, tzinfo=timezone.utc)
        )
        return refreshes, runtime._overview_last_daily_refresh_date

    refreshes, refresh_marker = asyncio.run(scenario())

    assert refreshes == [
        ("device-1", "weather_day_changed", "2026-07-13")
    ]
    assert refresh_marker == "2026-07-13"


def test_failed_daily_overview_refresh_retries_on_the_next_same_day_tick(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        calls = 0

        class FakeOverviewStore:
            def list_due_weather_retries(self, now_iso):
                return []

        class FakeOverviewService:
            async def drain_pending(self):
                return 0

        async def daily_refresh(local_date, now_utc):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("daily refresh failed")

        runtime.overview_store = FakeOverviewStore()
        runtime.overview_service = FakeOverviewService()
        runtime._run_daily_overview_refresh = daily_refresh
        now = datetime(
            2026,
            7,
            10,
            0,
            5,
            tzinfo=timezone(timedelta(hours=8)),
        )

        await runtime._run_overview_tick(now)
        marker_after_failure = runtime._overview_last_daily_refresh_date
        await runtime._run_overview_tick(now + timedelta(seconds=1))
        return calls, marker_after_failure, runtime._overview_last_daily_refresh_date

    calls, marker_after_failure, final_marker = asyncio.run(scenario())

    assert calls == 2
    assert marker_after_failure is None
    assert final_marker == "2026-07-10"


def test_cancelled_daily_overview_refresh_does_not_commit_daily_marker(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )

        class FakeOverviewStore:
            def list_due_weather_retries(self, now_iso):
                return []

        class FakeOverviewService:
            async def drain_pending(self):
                return 0

        async def daily_refresh(local_date, now_utc):
            raise asyncio.CancelledError

        runtime.overview_store = FakeOverviewStore()
        runtime.overview_service = FakeOverviewService()
        runtime._run_daily_overview_refresh = daily_refresh
        now = datetime(
            2026,
            7,
            10,
            0,
            5,
            tzinfo=timezone(timedelta(hours=8)),
        )

        try:
            await runtime._run_overview_tick(now)
        except asyncio.CancelledError:
            pass
        return runtime._overview_last_daily_refresh_date

    assert asyncio.run(scenario()) is None


def test_daily_same_city_failure_fetches_once_and_refreshes_both_devices(tmp_path):
    async def scenario():
        now = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
        runtime = create_xiaoxin_control_runtime(
            _overview_runtime_config(tmp_path)
        )
        runtime.overview_store._clock = lambda: now
        provider_calls = []

        class FirstFailureThenSuccessProvider:
            async def daily(self, province, city, date_text):
                provider_calls.append((province, city, date_text))
                if len(provider_calls) == 1:
                    raise RuntimeError("provider unavailable")
                return DailyWeather(
                    province=province,
                    city=city,
                    country_code="CN",
                    date=date_text,
                    weather_code=1,
                    weather_text="Clear",
                    temperature_min_c=20,
                    temperature_max_c=30,
                    fetched_at="2026-07-10T01:00:00+00:00",
                    timezone_id="Asia/Shanghai",
                )

        provider = FirstFailureThenSuccessProvider()
        runtime.overview_weather_provider = provider
        runtime.overview_service.weather_provider = provider
        user_a = runtime.identity_store.create_user("student-a", "hash", "A")
        user_b = runtime.identity_store.create_user("student-b", "hash", "B")
        for device_id, user in (("device-a", user_a), ("device-b", user_b)):
            runtime.identity_store.upsert_seen_device(device_id)
            runtime.identity_store.bind_device(device_id, user.id, device_id)
            runtime.identity_store.update_student_semester(
                user.id,
                {
                    "startDate": "2026-07-06",
                    "totalWeeks": 18,
                },
            )
            runtime.overview_store.set_manual_location(
                device_id,
                "Zhejiang",
                "Hangzhou",
            )

        await runtime._run_daily_overview_refresh("2026-07-10", now)
        retry = runtime.overview_store.get_weather_retry_state(
            "Zhejiang",
            "Hangzhou",
            "2026-07-10",
            "open_meteo",
        )
        snapshots = [
            runtime.overview_store.get_snapshot(device_id)
            for device_id in ("device-a", "device-b")
        ]
        await runtime._run_overview_tick(now + timedelta(seconds=600))
        retry_after_success = runtime.overview_store.get_weather_retry_state(
            "Zhejiang",
            "Hangzhou",
            "2026-07-10",
            "open_meteo",
        )
        refreshed_snapshots = [
            runtime.overview_store.get_snapshot(device_id)
            for device_id in ("device-a", "device-b")
        ]
        return (
            provider_calls,
            retry,
            snapshots,
            retry_after_success,
            refreshed_snapshots,
        )

    (
        provider_calls,
        retry,
        snapshots,
        retry_after_success,
        refreshed_snapshots,
    ) = asyncio.run(scenario())

    assert provider_calls == [
        ("Zhejiang", "Hangzhou", "2026-07-10"),
        ("Zhejiang", "Hangzhou", "2026-07-10"),
    ]
    assert retry["fetch_attempts"] == 1
    assert retry["next_attempt_at"] == "2026-07-10T01:10:00+00:00"
    assert all(snapshot is not None for snapshot in snapshots)
    assert all(
        snapshot.payload["weather"]["configured"] is True
        and snapshot.payload["weather"]["available"] is False
        for snapshot in snapshots
    )
    assert retry_after_success is None
    assert all(
        snapshot.payload["weather"]["available"] is True
        for snapshot in refreshed_snapshots
    )


def test_websocket_server_preserves_injected_xiaoxin_runtime(monkeypatch):
    _install_module(
        monkeypatch,
        "core.connection",
        ConnectionHandler=object,
    )
    _install_module(
        monkeypatch,
        "config.config_loader",
        load_config=lambda: {},
    )
    _install_module(
        monkeypatch,
        "core.auth",
        AuthManager=type(
            "AuthManager",
            (),
            {"__init__": lambda self, *args, **kwargs: None},
        ),
        AuthenticationError=Exception,
    )
    _install_module(
        monkeypatch,
        "core.utils.modules_initialize",
        initialize_modules=lambda *args, **kwargs: {},
    )
    _install_module(
        monkeypatch,
        "core.utils.util",
        check_vad_update=lambda *args, **kwargs: False,
        check_asr_update=lambda *args, **kwargs: False,
    )

    import core.websocket_server as websocket_server

    runtime = object()

    monkeypatch.setattr(
        websocket_server, "initialize_modules", lambda *args, **kwargs: {}
    )

    server = websocket_server.WebSocketServer(
        {
            "selected_module": "",
            "server": {"auth_key": "test-auth-key"},
        },
        xiaoxin_runtime=runtime,
    )

    assert server.xiaoxin_runtime is runtime


def test_control_runtime_uses_configured_timeouts_retry_schedule_and_history_limit():
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {"mqtt_gateway": ""},
            "xiaoxin_control": {
                "delivery_history_limit": 7,
                "wake_timeout_seconds": 3,
                "ack_timeout_seconds": 4,
                "todo_reminder_replay_window_minutes": 45,
            },
            "tts_delivery_retry_delays_ms": [100, 250, 900],
        }
    )

    assert runtime.store.limit == 7
    assert runtime.dispatcher.wake_timeout_seconds == 3
    assert runtime.dispatcher.ack_timeout_seconds == 4
    assert runtime.dispatcher.retry_delays_seconds == (0.1, 0.25, 0.9)
    assert runtime.todo_reminder_scheduler.replay_window == timedelta(minutes=45)


def test_control_runtime_stops_dispatcher_before_doorbell_client():
    async def scenario():
        runtime = create_xiaoxin_control_runtime({"server": {}, "xiaoxin_control": {}})
        calls = []

        class FakeDispatcher:
            async def stop(self):
                calls.append("dispatcher")

        class FakeDoorbell:
            def stop(self):
                calls.append("doorbell")

        runtime.dispatcher = FakeDispatcher()
        runtime.doorbell_client = FakeDoorbell()
        await runtime.stop()
        return calls

    assert asyncio.run(scenario()) == ["dispatcher", "doorbell"]


def test_control_runtime_wires_notification_history_store(tmp_path):
    history_db = tmp_path / "history.db"
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {"mqtt_gateway": ""},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "identity.db"),
                "notification_history_db": str(history_db),
                "notification_history_limit": 17,
            },
        }
    )

    assert runtime.notification_history_store.db_path == history_db
    assert runtime.notification_history_store.limit == 17
    assert runtime.store.history_sink is runtime.notification_history_store


def test_control_runtime_repairs_only_history_confirmed_done_todos():
    class FakeIdentityStore:
        def __init__(self):
            self.repaired = None

        def list_pending_student_todo_delivery_ids(self):
            return {"del-done", "del-failed", "del-missing"}

        def repair_todo_reminder_outcomes(self, delivery_ids):
            self.repaired = set(delivery_ids)
            return len(self.repaired)

    class FakeHistoryStore:
        def get_delivery_states(self, delivery_ids):
            assert set(delivery_ids) == {"del-done", "del-failed", "del-missing"}
            return {"del-done": "done", "del-failed": "failed"}

    identity_store = FakeIdentityStore()
    repaired_count = _repair_todo_reminder_outcomes_from_history(
        identity_store,
        FakeHistoryStore(),
    )

    assert repaired_count == 1
    assert identity_store.repaired == {"del-done"}


def test_control_runtime_exposes_doorbell_credential_store_with_configured_db_path(
    tmp_path,
):
    doorbell_credentials_db = tmp_path / "doorbell_credentials.db"
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
                "doorbell_credentials_db": str(doorbell_credentials_db),
            },
        }
    )

    assert runtime.doorbell_credential_store.db_path == doorbell_credentials_db


def test_control_runtime_exposes_todo_reminder_scheduler(tmp_path):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
        }
    )

    assert runtime.todo_reminder_scheduler.identity_store is runtime.identity_store
    assert runtime.todo_reminder_scheduler.dispatcher is runtime.dispatcher


def test_control_runtime_exposes_course_reminder_scheduler(tmp_path):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
        }
    )

    assert runtime.course_reminder_scheduler.identity_store is runtime.identity_store
    assert runtime.course_reminder_scheduler.dispatcher is runtime.dispatcher


def test_control_runtime_start_does_not_fail_when_mqtt_endpoint_is_absent():
    async def scenario():
        runtime = create_xiaoxin_control_runtime({"server": {}, "xiaoxin_control": {}})
        await runtime.start()
        await runtime.stop()

    asyncio.run(scenario())


def test_control_runtime_does_not_start_todo_reminder_loop_by_default():
    async def scenario():
        runtime = create_xiaoxin_control_runtime({"server": {}, "xiaoxin_control": {}})
        calls = []

        class FakeScheduler:
            async def dispatch_due_todos(self, now):
                calls.append(now)

        runtime.todo_reminder_scheduler = FakeScheduler()

        await runtime.start()
        await asyncio.sleep(0.02)
        await runtime.stop()

        return calls

    assert asyncio.run(scenario()) == []


def test_control_runtime_does_not_start_todo_reminder_loop_when_control_disabled():
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            {
                "server": {},
                "xiaoxin_control": {
                    "enabled": False,
                    "todo_reminder_scheduler_enabled": True,
                    "todo_reminder_tick_seconds": 0.01,
                },
            }
        )
        calls = []

        class FakeScheduler:
            async def dispatch_due_todos(self, now):
                calls.append(now)

        runtime.todo_reminder_scheduler = FakeScheduler()

        await runtime.start()
        await asyncio.sleep(0.02)
        await runtime.stop()

        return calls

    assert asyncio.run(scenario()) == []


def test_control_runtime_starts_configured_course_reminder_loop(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            {
                "server": {},
                "xiaoxin_control": {
                    "identity_db": str(tmp_path / "xiaoxin_control.db"),
                    "todo_reminder_scheduler_enabled": False,
                    "course_reminder_scheduler_enabled": True,
                    "reminder_tick_seconds": 0.01,
                },
            }
        )
        second_tick = asyncio.Event()
        todo_calls = []
        course_calls = []

        class FakeTodoScheduler:
            async def dispatch_due_todos(self, now):
                todo_calls.append(now)
                return []

        class FakeCourseScheduler:
            async def dispatch_due_courses(self, now):
                course_calls.append(now)
                if len(course_calls) >= 2:
                    second_tick.set()
                return []

        runtime.todo_reminder_scheduler = FakeTodoScheduler()
        runtime.course_reminder_scheduler = FakeCourseScheduler()

        await runtime.start()
        await asyncio.wait_for(second_tick.wait(), timeout=0.5)
        await runtime.stop()

        return todo_calls, course_calls

    todo_calls, course_calls = asyncio.run(scenario())

    assert todo_calls == []
    assert len(course_calls) >= 2
    assert all(call.tzinfo is not None for call in course_calls)


def test_control_runtime_starts_configured_todo_reminder_loop(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            {
                "server": {},
                "xiaoxin_control": {
                    "identity_db": str(tmp_path / "xiaoxin_control.db"),
                    "todo_reminder_scheduler_enabled": True,
                    "todo_reminder_tick_seconds": 0.01,
                },
            }
        )
        second_tick = asyncio.Event()
        calls = []

        class FakeScheduler:
            async def dispatch_due_todos(self, now):
                calls.append(now)
                if len(calls) >= 2:
                    second_tick.set()
                return []

        runtime.todo_reminder_scheduler = FakeScheduler()

        await runtime.start()
        await asyncio.wait_for(second_tick.wait(), timeout=0.5)
        await runtime.stop()

        return calls

    calls = asyncio.run(scenario())

    assert len(calls) >= 2
    assert all(call.tzinfo is not None for call in calls)


def test_control_runtime_stop_cancels_running_todo_reminder_tick(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            {
                "server": {},
                "xiaoxin_control": {
                    "identity_db": str(tmp_path / "xiaoxin_control.db"),
                    "todo_reminder_scheduler_enabled": True,
                    "todo_reminder_tick_seconds": 60,
                },
            }
        )
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class FakeScheduler:
            async def dispatch_due_todos(self, now):
                started.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

        runtime.todo_reminder_scheduler = FakeScheduler()

        await runtime.start()
        await asyncio.wait_for(started.wait(), timeout=0.5)
        await runtime.stop()

        return cancelled.is_set()

    assert asyncio.run(scenario()) is True


def test_control_runtime_todo_reminder_loop_logs_tick_errors_and_continues(
    tmp_path, caplog
):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            {
                "server": {},
                "xiaoxin_control": {
                    "identity_db": str(tmp_path / "xiaoxin_control.db"),
                    "todo_reminder_scheduler_enabled": True,
                    "todo_reminder_tick_seconds": 0.01,
                },
            }
        )
        second_tick = asyncio.Event()
        calls = []

        class FakeScheduler:
            async def dispatch_due_todos(self, now):
                calls.append(now)
                if len(calls) == 1:
                    raise RuntimeError("boom")
                second_tick.set()
                return []

        runtime.todo_reminder_scheduler = FakeScheduler()

        await runtime.start()
        await asyncio.wait_for(second_tick.wait(), timeout=0.5)
        await runtime.stop()

        return len(calls)

    caplog.set_level(logging.ERROR)

    assert asyncio.run(scenario()) >= 2
    assert "todo reminder scheduler tick failed" in caplog.text


def test_control_runtime_runs_companion_work_in_background(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            {
                "server": {},
                "xiaoxin_control": {
                    "identity_db": str(tmp_path / "xiaoxin_control.db"),
                },
                "xiaoxin_runtime": {
                    "companion_worker_enabled": False,
                    "companion_worker_tick_seconds": 0.01,
                },
            }
        )
        calls = []

        class FakeMind:
            def run_due_work(self, *, now, limit):
                calls.append((now, limit))

        runtime.companion_mind = FakeMind()
        runtime.companion_worker_enabled = True

        await runtime.start()
        async def wait_for_second_tick():
            while len(calls) < 2:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait_for_second_tick(), timeout=0.5)
        await runtime.stop()

        return calls, runtime._companion_task

    calls, task = asyncio.run(scenario())

    assert len(calls) >= 2
    assert all(datetime.fromisoformat(now).tzinfo is not None for now, _ in calls)
    assert all(limit == 20 for _, limit in calls)
    assert task is None


def test_control_runtime_builds_and_runs_production_companion_worker(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "xiaoxin_companion.db"

    class FakeProvider:
        def complete_chat(
            self, messages, max_tokens=None, temperature=None, response_format=None
        ):
            return (
                '{"schema_version":"companion-reflection-proposal-v1",'
                '"safe_summary":"无长期变化。","evidence_ids":[],'
                '"adjustments":[],"proposed_user_facts":[],'
                '"chapter_statements":[]}'
            )

    monkeypatch.setattr(
        "core.utils.llm.create_instance",
        lambda provider_type, provider_config: FakeProvider(),
    )
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "selected_module": {"LLM": "FakeLLM"},
            "LLM": {"FakeLLM": {"type": "FakeLLM"}},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
            "xiaoxin_runtime": {
                "companion_db_path": str(database_path),
                "companion_worker_enabled": True,
                "companion_worker_tick_seconds": 0.01,
            },
        }
    )
    assert runtime.companion_mind is not None
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = runtime.companion_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-production-worker",
            subject=subject,
            request_digest="digest-production-worker",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    runtime.companion_mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "assistant_action",
                    "ownership_scope": "relationship",
                    "content": {"reply_mode": "free_chat"},
                    "source_summary": "本轮成功生成回复。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "short_term",
                    "prompt_eligible": False,
                },
            ),
        ),
    )

    async def scenario():
        await runtime.start()

        async def wait_for_success():
            while True:
                with sqlite3.connect(database_path) as connection:
                    status = connection.execute(
                        "SELECT status FROM consolidation_jobs"
                    ).fetchone()[0]
                if status == "succeeded":
                    return
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait_for_success(), timeout=0.5)
        await runtime.stop()

    asyncio.run(scenario())


def test_control_runtime_can_isolate_companion_worker_llm(tmp_path, monkeypatch):
    created = []

    class FakeProvider:
        pass

    def create_provider(provider_type, provider_config):
        created.append((provider_type, provider_config))
        return FakeProvider()

    monkeypatch.setattr("core.utils.llm.create_instance", create_provider)

    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "selected_module": {"LLM": "ForegroundLLM"},
            "LLM": {
                "ForegroundLLM": {"type": "foreground", "api_key": "front"},
                "WorkerLLM": {"type": "worker", "api_key": "background"},
            },
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
            "xiaoxin_runtime": {
                "companion_db_path": str(tmp_path / "xiaoxin_companion.db"),
                "companion_worker_enabled": True,
                "companion_worker_llm": "WorkerLLM",
            },
        }
    )

    assert runtime.companion_worker_enabled is True
    assert created == [("worker", {"type": "worker", "api_key": "background"})]


def test_control_runtime_builds_companion_mind_for_api_when_worker_is_disabled(
    tmp_path,
):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
            "xiaoxin_runtime": {
                "companion_db_path": str(tmp_path / "xiaoxin_companion.db"),
                "companion_worker_enabled": False,
            },
        }
    )

    assert runtime.companion_mind is not None
    assert runtime.observation_ingress is not None
    assert runtime.companion_worker_enabled is False


def test_control_runtime_records_delivery_and_tts_as_distinct_observations(tmp_path):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
            "xiaoxin_runtime": {
                "companion_db_path": str(tmp_path / "xiaoxin_companion.db"),
            },
        }
    )
    user = runtime.identity_store.create_user("liu", "hash", "Liu")
    runtime.identity_store.upsert_seen_device("device-1")
    runtime.identity_store.bind_device("device-1", user.id)
    calls = []
    runtime.observation_ingress = SimpleNamespace(
        observe_user_event=lambda **kwargs: calls.append(kwargs)
    )
    todo = runtime.identity_store.create_student_todo(
        user.id,
        {
            "title": "完成作业",
            "dueAt": "2026-07-20T20:00:00+08:00",
        },
    )
    record = runtime.store.create(
        XiaoxinControlEventRequest(
            device_id="device-1",
            event=XiaoxinEvent.TODO_REMINDER,
            title="提醒事项",
            body="完成作业",
            tag=f"todo:{todo['id']}",
            speak=True,
        ),
        {"type": "xiaoxin_event"},
    )
    runtime.identity_store.claim_student_todo_for_reminder(
        user.id,
        todo["id"],
        "2026-07-20T20:00:00+08:00",
    )
    runtime.identity_store.mark_student_todo_reminded(
        user.id,
        todo["id"],
        record.delivery_id,
        "2026-07-20T20:00:00+08:00",
    )
    record.state = XiaoxinDeliveryState.DONE
    record.tts_state = "done"

    runtime.observe_todo_reminder_tts_done(record.delivery_id, "sentence-1")

    assert [call["kind"] for call in calls] == [
        "reminder_delivered",
        "reminder_tts_completed",
    ]
    assert all(call["payload"]["todo_id"] == todo["id"] for call in calls)
    assert all("completion_source" not in call["payload"] for call in calls)
    stored_todo = runtime.identity_store.get_student_todo(user.id, todo["id"])
    assert stored_todo["status"] == "pending"
    assert stored_todo["reminder_status"] == "tts_completed"


def test_control_runtime_records_terminal_todo_reminder_delivery_failure(tmp_path):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            {
                "server": {},
                "xiaoxin_control": {
                    "identity_db": str(tmp_path / "xiaoxin_control.db"),
                    "todo_reminder_scheduler_enabled": True,
                    "todo_reminder_tick_seconds": 0.01,
                },
                "xiaoxin_runtime": {
                    "companion_db_path": str(tmp_path / "xiaoxin_companion.db"),
                },
            }
        )
        user = runtime.identity_store.create_user("liu", "hash", "Liu")
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id)
        todo = runtime.identity_store.create_student_todo(
            user.id,
            {
                "title": "完成作业",
                "dueAt": "2026-07-20T20:00:00+08:00",
            },
        )
        record = runtime.store.create(
            XiaoxinControlEventRequest(
                device_id="device-1",
                event=XiaoxinEvent.TODO_REMINDER,
                title="提醒事项",
                body="完成作业",
                tag=f"todo:{todo['id']}",
                speak=True,
            ),
            {"type": "xiaoxin_event"},
        )
        runtime.store.transition(
            record.delivery_id,
            XiaoxinDeliveryState.FAILED,
            XiaoxinFailureReason.EXPIRED,
        )
        observed = asyncio.Event()
        calls = []

        def observe_user_event(**kwargs):
            calls.append(kwargs)
            observed.set()

        runtime.observation_ingress = SimpleNamespace(
            observe_user_event=observe_user_event
        )

        class FakeScheduler:
            emitted = False

            async def dispatch_due_todos(self, now):
                if self.emitted:
                    return []
                self.emitted = True
                return [
                    {"todo_id": todo["id"], "delivery_id": record.delivery_id}
                ]

        async def fake_wait_for_delivery(delivery_id):
            assert delivery_id == record.delivery_id

        runtime.todo_reminder_scheduler = FakeScheduler()
        runtime.dispatcher.wait_for_delivery_task = fake_wait_for_delivery

        await runtime.start()
        await asyncio.wait_for(observed.wait(), timeout=0.5)
        await runtime.stop()
        return calls

    calls = asyncio.run(scenario())

    assert len(calls) == 1
    assert calls[0]["kind"] == "reminder_delivery_failed"
    assert calls[0]["source_ref"] == calls[0]["payload"]["delivery_id"]
    assert calls[0]["payload"]["delivery_status"] == "failed"
    assert calls[0]["payload"]["failure_reason"] == "expired"
    assert "completion_source" not in calls[0]["payload"]


def test_control_runtime_starts_without_companion_worker_when_llm_init_fails(
    tmp_path, monkeypatch, caplog
):
    def fail_provider_creation(provider_type, provider_config):
        raise RuntimeError("provider secret must not reach logs")

    monkeypatch.setattr(
        "core.utils.llm.create_instance",
        fail_provider_creation,
    )
    caplog.set_level(logging.ERROR)

    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "selected_module": {"LLM": "BrokenLLM"},
            "LLM": {"BrokenLLM": {"type": "BrokenLLM", "api_key": "secret"}},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
            "xiaoxin_runtime": {
                "companion_db_path": str(tmp_path / "xiaoxin_companion.db"),
                "companion_worker_enabled": True,
                "companion_initiative_scheduler_enabled": True,
                "companion_initiative_delivery_enabled": True,
            },
        }
    )

    assert runtime.companion_mind is not None
    assert runtime.companion_worker_enabled is False
    assert runtime.companion_mind._initiative_scheduler is None

    async def scenario():
        await runtime.start()
        assert runtime._companion_task is None
        await runtime.stop()

    asyncio.run(scenario())
    assert "companion worker initialization failed" in caplog.text.lower()
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text


def test_control_runtime_wires_initiative_dry_run_without_reflection_model(tmp_path):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
            "xiaoxin_runtime": {
                "companion_db_path": str(tmp_path / "xiaoxin_companion.db"),
                "companion_worker_enabled": False,
                "companion_initiative_scheduler_enabled": True,
                "companion_initiative_delivery_enabled": False,
            },
        }
    )

    assert runtime.companion_worker_enabled is True
    assert runtime.companion_mind._worker is None
    assert runtime.companion_mind._initiative_scheduler is not None

    async def scenario():
        await runtime.start()
        assert runtime._companion_task is not None
        await runtime.stop()

    asyncio.run(scenario())


def test_control_runtime_rejects_delivery_without_model_worker(tmp_path):
    with pytest.raises(ValueError, match="requires the model worker"):
        create_xiaoxin_control_runtime(
            {
                "server": {},
                "xiaoxin_control": {
                    "identity_db": str(tmp_path / "xiaoxin_control.db"),
                },
                "xiaoxin_runtime": {
                    "companion_db_path": str(tmp_path / "xiaoxin_companion.db"),
                    "companion_worker_enabled": False,
                    "companion_initiative_scheduler_enabled": True,
                    "companion_initiative_delivery_enabled": True,
                },
            }
        )


def test_control_runtime_companion_work_logs_tick_errors_and_continues(
    tmp_path, caplog
):
    async def scenario():
        runtime = create_xiaoxin_control_runtime(
            {
                "server": {},
                "xiaoxin_control": {
                    "identity_db": str(tmp_path / "xiaoxin_control.db"),
                },
                "xiaoxin_runtime": {
                    "companion_worker_enabled": False,
                    "companion_worker_tick_seconds": 0.01,
                },
            }
        )
        calls = []

        class FakeMind:
            async def run_due_work(self, *, now, limit):
                calls.append((now, limit))
                if len(calls) == 1:
                    raise RuntimeError("boom")

        runtime.companion_mind = FakeMind()
        runtime.companion_worker_enabled = True

        await runtime.start()
        async def wait_for_second_tick():
            while len(calls) < 2:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait_for_second_tick(), timeout=0.5)
        await runtime.stop()

        return len(calls)

    caplog.set_level(logging.ERROR)

    assert asyncio.run(scenario()) >= 2
    assert "companion worker tick failed" in caplog.text


def test_control_runtime_dispatches_only_eligible_companion_projection(tmp_path):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
        }
    )
    connection = object()
    runtime.registry.register_connection("device-1", connection, "websocket")
    requests = []
    claims = []
    deliveries = []

    class FakeMind:
        def project(self, request):
            requests.append(request)
            if request.initiative_decision_id is not None:
                claims.append(
                    (request.subject, request.initiative_decision_id, request.now)
                )
                return SimpleNamespace(
                    payload={
                        "eligible": True,
                        "decision_id": request.initiative_decision_id,
                        "reason_code": "evidence_backed_followup",
                        "evidence_ids": ("evidence-1",),
                        "content_brief": "Store 重新验证后的有依据陪伴。",
                        "hardware_expression": {"intensity": "low"},
                    }
                )
            return SimpleNamespace(
                payload={
                    "eligible": True,
                    "decision_id": "decision-1",
                    "content_brief": "不得直接信任的投影文案。",
                }
            )

    async def fake_submit(device_id, payload):
        deliveries.append((device_id, payload))
        return SimpleNamespace(delivery_id="delivery-1")

    runtime.companion_mind = FakeMind()
    runtime.dispatcher.submit_companion_initiative = fake_submit
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    projection, delivery = asyncio.run(
        runtime.dispatch_companion_initiative(
            device_id="device-1",
            subject=subject,
            now="2026-07-18T12:00:00+08:00",
        )
    )

    assert projection.payload["eligible"] is True
    assert delivery.delivery_id == "delivery-1"
    assert requests[0].device_available is True
    assert requests[0].higher_priority_pending is False
    assert claims == [(subject, "decision-1", "2026-07-18T12:00:00+08:00")]
    assert deliveries == [
        (
            "device-1",
            {
                "eligible": True,
                "decision_id": "decision-1",
                "reason_code": "evidence_backed_followup",
                "evidence_ids": ("evidence-1",),
                "content_brief": "Store 重新验证后的有依据陪伴。",
                "hardware_expression": {"intensity": "low"},
            },
        )
    ]


def test_control_runtime_records_eventual_initiative_delivery_failure(tmp_path):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
        }
    )
    runtime.registry.register_connection("device-1", object(), "websocket")
    controls = []

    class FakeMind:
        def project(self, request):
            if request.initiative_decision_id is not None:
                return SimpleNamespace(
                    payload={
                        "eligible": True,
                        "decision_id": request.initiative_decision_id,
                        "reason_code": "evidence_backed_followup",
                        "evidence_ids": ("evidence-1",),
                        "content_brief": "有依据的低频陪伴。",
                        "hardware_expression": {"intensity": "low"},
                    }
                )
            return SimpleNamespace(
                payload={"eligible": True, "decision_id": "decision-1"}
            )

        def apply_control(self, command):
            controls.append(command)

    async def fake_submit(device_id, payload):
        return SimpleNamespace(delivery_id="delivery-1")

    async def fake_wait(delivery_id):
        return None

    runtime.companion_mind = FakeMind()
    runtime.companion_clock = lambda: datetime.fromisoformat(
        "2026-07-18T12:05:00+08:00"
    )
    runtime.dispatcher.submit_companion_initiative = fake_submit
    runtime.dispatcher.wait_for_delivery_task = fake_wait
    runtime.store.create = lambda request, payload: None
    runtime.store.get = lambda delivery_id: SimpleNamespace(
        state=XiaoxinDeliveryState.FAILED
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    async def scenario():
        await runtime.dispatch_companion_initiative(
            device_id="device-1",
            subject=subject,
            now="2026-07-18T12:00:00+08:00",
        )
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert len(controls) == 1
    assert controls[0].action == "record_initiative_feedback"
    assert controls[0].payload == {
        "decision_id": "decision-1",
        "outcome": "delivery_failed",
        "now": "2026-07-18T12:05:00+08:00",
        "idempotency_key": "initiative-delivery-failed:decision-1:delivery-1",
    }


def test_control_runtime_records_initiative_submit_failure(tmp_path):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
        }
    )
    runtime.registry.register_connection("device-1", object(), "websocket")
    controls = []

    class FakeMind:
        def project(self, request):
            if request.initiative_decision_id is not None:
                return SimpleNamespace(
                    payload={
                        "eligible": True,
                        "decision_id": request.initiative_decision_id,
                        "reason_code": "evidence_backed_followup",
                        "evidence_ids": ("evidence-1",),
                        "content_brief": "有依据的低频陪伴。",
                        "hardware_expression": {"intensity": "low"},
                    }
                )
            return SimpleNamespace(
                payload={"eligible": True, "decision_id": "decision-1"}
            )

        def apply_control(self, command):
            controls.append(command)

    async def failing_submit(device_id, payload):
        raise RuntimeError("dispatcher stopped")

    runtime.companion_mind = FakeMind()
    runtime.companion_clock = lambda: datetime.fromisoformat(
        "2026-07-18T12:05:00+08:00"
    )
    runtime.dispatcher.submit_companion_initiative = failing_submit
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    async def scenario():
        try:
            await runtime.dispatch_companion_initiative(
                device_id="device-1",
                subject=subject,
                now="2026-07-18T12:00:00+08:00",
            )
        except RuntimeError as exc:
            assert str(exc) == "dispatcher stopped"
        else:
            raise AssertionError("submit failure must propagate")

    asyncio.run(scenario())

    assert len(controls) == 1
    assert controls[0].payload == {
        "decision_id": "decision-1",
        "outcome": "delivery_failed",
        "now": "2026-07-18T12:05:00+08:00",
        "idempotency_key": "initiative-delivery-failed:decision-1:submit",
    }


def test_control_runtime_records_initiative_failure_when_dispatcher_stops(tmp_path):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
        }
    )
    send_started = asyncio.Event()
    controls = []

    class BlockingConnection:
        async def send_xiaoxin_event(self, payload):
            send_started.set()
            await asyncio.Future()

    class FakeMind:
        def project(self, request):
            if request.initiative_decision_id is not None:
                return SimpleNamespace(
                    payload={
                        "eligible": True,
                        "decision_id": "decision-stop",
                        "reason_code": "evidence_backed_followup",
                        "evidence_ids": ("evidence-1",),
                        "content_brief": "有依据的陪伴。",
                        "hardware_expression": {},
                    }
                )
            return SimpleNamespace(
                payload={
                    "eligible": True,
                    "decision_id": "decision-stop",
                    "reason_code": "evidence_backed_followup",
                    "evidence_ids": ("evidence-1",),
                    "content_brief": "有依据的陪伴。",
                    "hardware_expression": {},
                }
            )

        def apply_control(self, command):
            controls.append(command)
            return SimpleNamespace(status="applied")

    runtime.registry.register_connection(
        "device-1", BlockingConnection(), "websocket"
    )
    runtime.companion_mind = FakeMind()
    runtime.companion_clock = lambda: datetime.fromisoformat(
        "2026-07-18T12:05:00+08:00"
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    async def scenario():
        _, delivery = await runtime.dispatch_companion_initiative(
            device_id="device-1",
            subject=subject,
            now="2026-07-18T12:00:00+08:00",
        )
        await send_started.wait()
        await runtime.stop()
        return delivery.delivery_id

    delivery_id = asyncio.run(scenario())

    assert len(controls) == 1
    assert controls[0].payload == {
        "decision_id": "decision-stop",
        "outcome": "delivery_failed",
        "now": "2026-07-18T12:05:00+08:00",
        "idempotency_key": (
            f"initiative-delivery-failed:decision-stop:{delivery_id}"
        ),
    }


def test_control_runtime_creates_identity_services(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
        }
    )

    assert runtime.identity_store is not None
    assert runtime.auth_service is not None
    assert runtime.compliance_service is not None
    assert runtime.compliance_service.config.companion_service_mode.value == "tool_only"
    assert Path(tmp_path / "xiaoxin_control.db").exists()


def test_runtime_exposes_activation_store(tmp_path):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {"mqtt_gateway": ""},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "identity.db"),
                "activation_db": str(tmp_path / "activation.db"),
            },
        }
    )

    session = runtime.activation_store.create_or_refresh_activation("device-1")

    assert session.device_id == "device-1"


def test_control_runtime_note_device_seen_records_identity_device(tmp_path):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
        }
    )

    runtime.note_device_seen("device-1")

    device = runtime.identity_store.get_device_by_device_id("device-1")

    assert device is not None
    assert device.device_id == "device-1"
    assert device.owner_user_id is None


def test_control_runtime_note_device_seen_swallows_store_failure(
    tmp_path, caplog, monkeypatch
):
    runtime = create_xiaoxin_control_runtime(
        {
            "server": {},
            "xiaoxin_control": {
                "identity_db": str(tmp_path / "xiaoxin_control.db"),
            },
        }
    )

    def fail_upsert_seen_device(device_id, display_name=None):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        runtime.identity_store, "upsert_seen_device", fail_upsert_seen_device
    )
    caplog.set_level(logging.ERROR)

    runtime.note_device_seen("device-1")

    assert any("device-1" in record.message for record in caplog.records)
    assert any(record.exc_info is not None for record in caplog.records)


def test_app_main_wires_one_shared_runtime_into_both_servers(monkeypatch):
    opuslib_next_stub = types.ModuleType("opuslib_next")
    opuslib_next_stub.APPLICATION_AUDIO = object()
    opuslib_next_stub.Encoder = object
    opuslib_next_stub.Decoder = object
    monkeypatch.setitem(sys.modules, "opuslib_next", opuslib_next_stub)
    _install_module(
        monkeypatch,
        "config.logger",
        setup_logging=lambda: types.SimpleNamespace(
            bind=lambda **kwargs: types.SimpleNamespace(
                info=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None
            )
        ),
    )
    _install_module(
        monkeypatch,
        "config.settings",
        load_config=lambda: {"server": {"auth_key": "test-auth-key"}},
    )
    _install_module(
        monkeypatch,
        "core.http_server",
        SimpleHttpServer=object,
    )
    _install_module(
        monkeypatch,
        "core.utils.gc_manager",
        get_gc_manager=lambda interval_seconds=300: object(),
    )
    _install_module(
        monkeypatch,
        "core.utils.util",
        check_ffmpeg_installed=lambda: None,
        get_local_ip=lambda: "127.0.0.1",
        validate_mcp_endpoint=lambda value: False,
    )
    _install_module(
        monkeypatch,
        "core.websocket_server",
        WebSocketServer=object,
    )
    _install_module(
        monkeypatch,
        "core.xiaoxin.control_runtime",
        create_xiaoxin_control_runtime=lambda config: object(),
    )

    import app

    runtime_calls = []
    ws_runtime_args = []
    http_runtime_args = []
    ws_started = asyncio.Event()
    http_started = asyncio.Event()
    shutdown_requested = asyncio.Event()

    class FakeRuntime:
        async def start(self):
            runtime_calls.append("start")

        async def stop(self):
            runtime_calls.append("stop")

    runtime = FakeRuntime()

    class FakeGcManager:
        async def start(self):
            runtime_calls.append("gc-start")

        async def stop(self):
            runtime_calls.append("gc-stop")

    class FakeWebSocketServer:
        def __init__(self, config, xiaoxin_runtime=None):
            ws_runtime_args.append(xiaoxin_runtime)
            self.xiaoxin_runtime = xiaoxin_runtime

        async def start(self):
            ws_started.set()
            await shutdown_requested.wait()

    class FakeHttpServer:
        def __init__(self, config, xiaoxin_runtime=None):
            http_runtime_args.append(xiaoxin_runtime)
            self.xiaoxin_runtime = xiaoxin_runtime

        async def start(self):
            http_started.set()
            await shutdown_requested.wait()

    async def fake_monitor_stdin():
        await shutdown_requested.wait()

    async def fake_wait_for_exit():
        await ws_started.wait()
        await http_started.wait()
        raise asyncio.CancelledError

    monkeypatch.setattr(
        app, "load_config", lambda: {"server": {"auth_key": "test-auth-key"}}
    )
    monkeypatch.setattr(app, "check_ffmpeg_installed", lambda: None)
    monkeypatch.setattr(
        app, "get_gc_manager", lambda interval_seconds=300: FakeGcManager()
    )
    monkeypatch.setattr(app, "create_xiaoxin_control_runtime", lambda config: runtime)
    monkeypatch.setattr(app, "WebSocketServer", FakeWebSocketServer)
    monkeypatch.setattr(app, "SimpleHttpServer", FakeHttpServer)
    monkeypatch.setattr(app, "monitor_stdin", fake_monitor_stdin)
    monkeypatch.setattr(app, "wait_for_exit", fake_wait_for_exit)
    monkeypatch.setattr(app, "get_local_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(app, "validate_mcp_endpoint", lambda value: False)

    async def scenario():
        await app.main()

    asyncio.run(scenario())

    assert len(ws_runtime_args) == 1
    assert len(http_runtime_args) == 1
    assert ws_runtime_args[0] is http_runtime_args[0]
    assert isinstance(ws_runtime_args[0], FakeRuntime)
    assert ws_runtime_args[0] is runtime
    assert runtime_calls == ["gc-start", "start", "gc-stop", "stop"]
def test_first_weather_failure_survives_restart_and_due_tick_succeeds(tmp_path):
    async def scenario():
        config = _overview_runtime_config(tmp_path)
        first = create_xiaoxin_control_runtime(config)
        fixed = datetime(2026, 7, 10, 7, 30, tzinfo=timezone.utc)
        first.overview_service._clock = lambda: fixed
        first.overview_store._clock = lambda: fixed
        user = first.identity_store.create_user("student", "hash", "Student")
        first.identity_store.upsert_seen_device("device-1")
        first.identity_store.bind_device("device-1", user.id, "Desk")
        first.overview_store.set_manual_location(
            "device-1", "Zhejiang", "Hangzhou"
        )

        class FailingProvider:
            async def daily(self, province, city, date_text):
                raise RuntimeError("provider secret")

        first.overview_service.weather_provider = FailingProvider()
        await first.overview_service.refresh_device(
            "device-1", "course_created", "2026-07-10"
        )
        before = first.overview_store.get_weather_retry_state(
            "Zhejiang", "Hangzhou", "2026-07-10", "open_meteo"
        )

        restarted = create_xiaoxin_control_runtime(config)
        due = fixed + timedelta(seconds=600)
        restarted.overview_store._clock = lambda: due
        restarted.overview_service._clock = lambda: due

        class RecoveredProvider:
            async def daily(self, province, city, date_text):
                return DailyWeather(
                    province=province,
                    city=city,
                    country_code="CN",
                    date=date_text,
                    weather_code=1,
                    weather_text="Clear",
                    temperature_min_c=20,
                    temperature_max_c=30,
                    fetched_at="2026-07-10T07:40:00+00:00",
                    timezone_id="Asia/Shanghai",
                )

        restarted.overview_weather_provider = RecoveredProvider()
        await restarted._run_overview_tick(due)
        weather = restarted.overview_store.get_daily_weather(
            "Zhejiang", "Hangzhou", "2026-07-10", "open_meteo"
        )
        after = restarted.overview_store.get_weather_retry_state(
            "Zhejiang", "Hangzhou", "2026-07-10", "open_meteo"
        )
        return before, weather, after

    before, weather, after = asyncio.run(scenario())

    assert before["fetch_attempts"] == 1
    assert before["next_attempt_at"] == "2026-07-10T07:40:00+00:00"
    assert weather.weather_text == "Clear"
    assert after is None
