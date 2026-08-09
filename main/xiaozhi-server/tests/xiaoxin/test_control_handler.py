import asyncio
import importlib
import json
import sys
import types
from datetime import datetime, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict

from core.api.xiaoxin_control_handler import XiaoxinControlHandler
from core.xiaoxin.companion import (
    CompanionMind,
    CompanionObserveResult,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.store import CompanionStore
from core.xiaoxin.compliance import (
    AgeBand,
    ComplianceConfig,
    CompliancePolicyService,
    ComplianceStore,
    GlobalCompanionMode,
)
from core.xiaoxin.activation_store import XiaoxinActivationStore
from core.xiaoxin.control_types import (
    XiaoxinDeliveryState,
    XiaoxinFailureReason,
    build_xiaoxin_event_payload,
    parse_control_event_request,
)
from core.xiaoxin.delivery_store import XiaoxinDeliveryStore
from core.xiaoxin.dispatcher import DispatcherStoppedError
from core.xiaoxin.doorbell_credentials import DoorbellCredentialStore
from core.xiaoxin.identity.auth import XiaoxinAuthService
from core.xiaoxin.identity.resolver import XiaoxinIdentityResolver
from core.xiaoxin.identity.store import XiaoxinIdentityStore
from core.xiaoxin.notification_history_store import XiaoxinNotificationHistoryStore
from core.xiaoxin.overview.models import DailyWeather, IpCityLocation
from core.xiaoxin.overview.providers import (
    LocationValidationError,
    ProviderDataError,
)
from core.xiaoxin.overview.service import (
    DisabledOverviewSyncService,
    OverviewSyncService,
)
from core.xiaoxin.overview.store import XiaoxinOverviewStore
from core.xiaoxin.registry import XiaoxinDeviceRegistry


class FakeDispatcher:
    def __init__(self, store):
        self.store = store
        self.submitted = []

    async def submit(self, request):
        from core.xiaoxin.control_types import build_xiaoxin_event_payload

        payload = build_xiaoxin_event_payload("pending", request)
        record = self.store.create(request, payload)
        self.submitted.append(request)
        return record


class FakeDoorbellClient:
    def __init__(self):
        self.published = []
        self.should_publish = True
        self.state = "ok"

    def publish_wake(self, device_id, tenant_id=None):
        self.published.append(device_id)
        return self.should_publish

    def diagnostic_state(self):
        return self.state


class FakeRuntime:
    def __init__(self):
        self.registry = XiaoxinDeviceRegistry()
        self.store = XiaoxinDeliveryStore()
        self.dispatcher = FakeDispatcher(self.store)
        self.doorbell_client = FakeDoorbellClient()


class AutoConfiguredAuthService(XiaoxinAuthService):
    def __init__(
        self,
        identity_store,
        compliance_service,
        *,
        auto_configure_sessions=True,
    ):
        super().__init__(identity_store)
        self._compliance_service = compliance_service
        self._auto_configure_sessions = auto_configure_sessions

    def _configure_adult(self, user_id):
        status = self._compliance_service.status_for_user(user_id)
        if status.age_band is AgeBand.UNKNOWN:
            self._compliance_service.declare_age_band(
                user_id,
                AgeBand.AGE_18_PLUS,
            )
            self._compliance_service.accept_current_agreements(user_id)
            self._compliance_service.update_settings(
                user_id,
                proactive_enabled=True,
                memory_enabled=True,
            )

    def register(self, *args, **kwargs):
        user, token = super().register(*args, **kwargs)
        self._configure_adult(user.id)
        return user, token

    def create_session_for_user(self, user_id):
        if self._auto_configure_sessions:
            self._configure_adult(user_id)
        return super().create_session_for_user(user_id)


class AuthRuntime(FakeRuntime):
    def __init__(self, db_path, *, auto_configure_compliance=True):
        super().__init__()
        self.identity_store = XiaoxinIdentityStore(db_path)
        self.activation_store = XiaoxinActivationStore(db_path.with_name("xiaoxin_activation.db"))
        self.identity_resolver = XiaoxinIdentityResolver(self.identity_store)
        self.compliance_service = CompliancePolicyService(
            ComplianceStore(db_path),
            ComplianceConfig(companion_service_mode=GlobalCompanionMode.ENABLED),
        )
        self.auth_service = AutoConfiguredAuthService(
            self.identity_store,
            self.compliance_service,
            auto_configure_sessions=auto_configure_compliance,
        )


class SpyOverviewProjection:
    def __init__(self):
        self.calls = []

    def build_curriculum_overview(self, user_id, date_text, *, include_started=True):
        self.calls.append(("curriculum", user_id, date_text, None, include_started))
        return {"source": "projection", "date": date_text}

    def build_student_overview(
        self, user_id, date_text, *, device_id=None, include_started=True
    ):
        self.calls.append(("student", user_id, date_text, device_id, include_started))
        return {
            "source": "projection",
            "date": date_text,
            "todaySummary": {"latestNotificationState": "暂无通知"},
            "latestNotification": None,
        }


class RecordingOverviewService(SpyOverviewProjection):
    def __init__(self):
        super().__init__()
        self.refresh_calls = []
        self.clear_calls = []
        self.observe_calls = []
        self.refresh_error = None
        self.clear_error = None
        self.refresh_probe = None
        self.clear_probe = None
        self.device_refresh_calls = []
        self.device_refresh_error = None
        self.device_refresh_result = {
            "revision": 7,
            "publish_state": "pending",
        }

    async def observe_device_ip(self, device_id, public_ip, reason):
        self.observe_calls.append((device_id, public_ip, reason))
        return {"refreshed": True}

    async def refresh_user_devices(self, user_id, reason):
        self.refresh_calls.append((user_id, reason))
        if self.refresh_probe is not None:
            self.refresh_probe(user_id, reason)
        if self.refresh_error is not None:
            raise self.refresh_error
        return []

    async def clear_unbound_device(self, device_id, reason):
        self.clear_calls.append((device_id, reason))
        if self.clear_probe is not None:
            self.clear_probe(device_id, reason)
        if self.clear_error is not None:
            raise self.clear_error
        return None

    async def refresh_device(self, device_id, reason):
        self.device_refresh_calls.append((device_id, reason))
        if self.device_refresh_error is not None:
            raise self.device_refresh_error
        return dict(self.device_refresh_result)


class RecordingObservationIngress:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def observe_user_event(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class RecordingLogger:
    def __init__(self):
        self.records = []
        self.warnings = []
        self.fields = {}

    def bind(self, **fields):
        self.fields = fields
        return self

    def exception(self, message, *args):
        self.records.append((self.fields, message, args))

    def warning(self, message, *args):
        self.warnings.append((dict(self.fields), message, args))


class BatteryRegistry:
    def list_devices(self):
        return [
            {
                "device_id": "device-mp-zero",
                "state": "connected",
                "battery_level": 0,
                "battery_percent": 0,
                "firmware_version": "0.1.0",
                "last_seen_at": "2026-07-05T10:00:00+08:00",
            }
        ]


class OverviewConnection:
    def __init__(self):
        self.sent = []

    async def send_xiaoxin_event(self, payload):
        self.sent.append(payload)


class TextChatConnection:
    def __init__(self):
        self.submitted_texts = []
        self.submitted_speakers = []
        self.submitted_simulated_times = []
        self.submitted_await_tts_terminal = []
        self.submitted_evaluation_ids = []
        self.companion_subject_context = types.SimpleNamespace(pet_id="pet-a")
        self.error = None

    async def submit_control_text_chat(
        self,
        text,
        *,
        speaker=None,
        simulated_as_of=None,
        await_tts_terminal=False,
        evaluation_run_id=None,
        evaluation_case_id=None,
    ):
        if self.error is not None:
            raise self.error
        self.submitted_texts.append(text)
        self.submitted_speakers.append(speaker)
        self.submitted_simulated_times.append(simulated_as_of)
        self.submitted_await_tts_terminal.append(await_tts_terminal)
        self.submitted_evaluation_ids.append(
            (evaluation_run_id, evaluation_case_id)
        )
        return types.SimpleNamespace(
            event_id="event-eval-1",
            sentence_id="sentence-eval-1",
            submitted_at="2026-10-28T16:40:58+08:00",
            assistant_text="收到。",
            tts_outcome="done" if await_tts_terminal else "not_waited",
            tts_reason=None,
        )


class StaticWeatherProvider:
    def __init__(self):
        self.calls = []
        self.validation_calls = []
        self.validation_error = None
        self.daily_error = None

    async def validate_city(self, province, city):
        self.validation_calls.append((province, city))
        if self.validation_error is not None:
            raise self.validation_error

    async def daily(self, province, city, date_text):
        self.calls.append((province, city, date_text))
        if self.daily_error is not None:
            raise self.daily_error
        return DailyWeather(
            province=province,
            city=city,
            date=date_text,
            weather_code=1,
            weather_text="Cloudy",
            temperature_min_c=20,
            temperature_max_c=28,
            fetched_at=f"{date_text}T08:00:00+00:00",
            timezone_id="Asia/Shanghai",
        )


class BlockingValidationWeatherProvider(StaticWeatherProvider):
    def __init__(self):
        super().__init__()
        self.validation_started = asyncio.Event()
        self.validation_release = asyncio.Event()

    async def validate_city(self, province, city):
        self.validation_calls.append((province, city))
        self.validation_started.set()
        await self.validation_release.wait()


class BlockingForecastWeatherProvider(StaticWeatherProvider):
    def __init__(self):
        super().__init__()
        self.forecast_started = asyncio.Event()
        self.forecast_release = asyncio.Event()

    async def daily(self, province, city, date_text):
        self.calls.append((province, city, date_text))
        self.forecast_started.set()
        await self.forecast_release.wait()
        return await super().daily(province, city, date_text)


class SignalingIpLocationProvider:
    def __init__(self, *, province="Jiangsu", city="Nanjing"):
        self.province = province
        self.city = city
        self.located = asyncio.Event()

    async def locate(self, _public_ip):
        self.located.set()
        return IpCityLocation(
            province=self.province,
            city=self.city,
            country_code="CN",
            located_at="2026-07-11T01:00:00+00:00",
        )


def _request(method, path, *, remote="127.0.0.1", **kwargs):
    request = make_mocked_request(method, path, **kwargs)
    request._transport_peername = (remote, 12345)
    return request


def _json_body(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_overview_handler_wrappers_delegate_to_injected_projection_service(tmp_path):
    runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
    projection = SpyOverviewProjection()
    runtime.overview_service = projection
    handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)

    curriculum = handler._curriculum_overview("user-1", "2026-07-10")
    overview = handler._student_overview(
        "user-1", "2026-07-10", device_id="device-1"
    )

    assert curriculum == {"source": "projection", "date": "2026-07-10"}
    assert overview["source"] == "projection"
    assert projection.calls == [
        ("curriculum", "user-1", "2026-07-10", None, False),
        ("student", "user-1", "2026-07-10", "device-1", False),
    ]


def test_overview_handler_installs_projection_only_compatibility_service(tmp_path):
    runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")

    handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)

    assert runtime.overview_service is handler.overview_service


def test_miniprogram_notification_history_separates_companion_initiatives(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")

        system_request = parse_control_event_request(
            {
                "device_id": "device-a",
                "event": "notification",
                "title": "课程提醒",
                "body": "十分钟后上课。",
                "tag": "course:demo",
            }
        )
        companion_request = parse_control_event_request(
            {
                "device_id": "device-a",
                "event": "notification",
                "title": "小芯陪伴",
                "body": "我刚刚想到你了。",
                "tag": "companion:decision-1",
            }
        )
        runtime.store.create(
            system_request,
            build_xiaoxin_event_payload("pending", system_request),
        )
        runtime.store.create(
            companion_request,
            build_xiaoxin_event_payload("pending", companion_request),
        )

        notification_response = await handler.handle_miniprogram_notification_history(
            _miniprogram_request(
                "GET",
                "/api/miniprogram/notifications/history",
                token,
            )
        )
        companion_response = await handler.handle_miniprogram_companion_history(
            _miniprogram_request(
                "GET",
                "/api/miniprogram/companion/history",
                token,
            )
        )
        return (
            notification_response.status,
            json.loads(notification_response.text),
            companion_response.status,
            json.loads(companion_response.text),
        )

    notification_status, notifications, companion_status, companion_history = asyncio.run(
        scenario()
    )

    assert notification_status == 200
    assert [item["title"] for item in notifications["notifications"]] == ["课程提醒"]
    assert companion_status == 200
    assert [item["title"] for item in companion_history["messages"]] == ["小芯陪伴"]
    assert companion_history["messages"][0]["type"] == "companion_initiative"
    assert companion_history["messages"][0]["source"] == "companion"


async def _miniprogram_session(
    handler,
    openid="wx-openid-1",
    nickname="小杭",
    account_role="student",
):
    request = _request(
        "POST",
        "/api/miniprogram/session",
        headers={"Content-Type": "application/json"},
    )
    request._read_bytes = _json_body(
        {
            "openid": openid,
            "nickname": nickname,
            "accountRole": account_role,
        }
    )
    response = await handler.handle_miniprogram_session(request)
    body = json.loads(response.text)
    return response.status, body


def _miniprogram_request(method, path, token, payload=None, **kwargs):
    request = _request(
        method,
        path,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        **kwargs,
    )
    if payload is not None:
        request._read_bytes = _json_body(payload)
    return request


def _location_heartbeat_request(
    credential,
    *,
    device_id=None,
    password=None,
    remote="8.8.8.8",
    payload=None,
    extra_headers=None,
):
    headers = {
        "Device-Id": device_id or credential.device_id,
        "Device-Username": credential.username,
        "Authorization": f"Bearer {password or credential.password}",
        "Content-Type": "application/json",
    }
    headers.update(extra_headers or {})
    request = _request(
        "POST",
        "/api/xiaoxin/device/location-heartbeat",
        remote=remote,
        headers=headers,
    )
    request._read_bytes = _json_body(payload or {})
    return request


def test_location_heartbeat_authenticates_opaque_device_credential(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.doorbell_credential_store = DoorbellCredentialStore(
            tmp_path / "doorbell.db"
        )
        runtime.overview_service = RecordingOverviewService()
        credential = runtime.doorbell_credential_store.get_or_create(
            "credential-namespace",
            "device-1",
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)

        response = await handler.handle_location_heartbeat(
            _location_heartbeat_request(
                credential,
                payload={"ip": "1.1.1.1", "tenant": "attacker-tenant"},
            )
        )
        return response.status, json.loads(response.text), runtime.overview_service

    status, body, overview_service = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True, "observed": True}
    assert overview_service.observe_calls == [
        ("device-1", "8.8.8.8", "location_heartbeat")
    ]
    serialized = json.dumps(body)
    assert "1.1.1.1" not in serialized
    assert "credential-namespace" not in serialized


def test_location_heartbeat_rejects_credential_for_another_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.doorbell_credential_store = DoorbellCredentialStore(
            tmp_path / "doorbell.db"
        )
        runtime.overview_service = RecordingOverviewService()
        credential = runtime.doorbell_credential_store.get_or_create(
            "credential-namespace",
            "device-1",
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)

        response = await handler.handle_location_heartbeat(
            _location_heartbeat_request(credential, device_id="device-2")
        )
        return response.status, json.loads(response.text), runtime.overview_service

    status, body, overview_service = asyncio.run(scenario())

    assert status == 401
    assert body == {"success": False, "message": "invalid device credential"}
    assert overview_service.observe_calls == []
    serialized = json.dumps(body)
    assert "password" not in serialized
    assert "openid" not in serialized


def test_location_heartbeat_rejects_wrong_password_and_missing_headers(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.doorbell_credential_store = DoorbellCredentialStore(
            tmp_path / "doorbell.db"
        )
        runtime.overview_service = RecordingOverviewService()
        credential = runtime.doorbell_credential_store.get_or_create(
            "credential-namespace",
            "device-1",
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)

        wrong = await handler.handle_location_heartbeat(
            _location_heartbeat_request(credential, password="wrong-password")
        )
        missing = await handler.handle_location_heartbeat(
            _request(
                "POST",
                "/api/xiaoxin/device/location-heartbeat",
                remote="8.8.8.8",
                headers={"Device-Id": "device-1"},
            )
        )
        return wrong, missing, runtime.overview_service

    wrong, missing, overview_service = asyncio.run(scenario())

    assert wrong.status == 401
    assert missing.status == 401
    assert json.loads(wrong.text)["message"] == "invalid device credential"
    assert json.loads(missing.text)["message"] == "invalid device credential"
    assert overview_service.observe_calls == []


def test_location_heartbeat_invalid_proxy_cidr_logs_fixed_diagnostic(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.doorbell_credential_store = DoorbellCredentialStore(
            tmp_path / "doorbell.db"
        )
        runtime.overview_service = RecordingOverviewService()
        credential = runtime.doorbell_credential_store.get_or_create(
            "credential-namespace",
            "device-1",
        )
        config = {
            "xiaoxin_control": {
                "overview_mqtt": {
                    "trusted_proxy_cidrs": [
                        "10.0.0.0/8",
                        "invalid-secret-cidr",
                    ],
                }
            }
        }
        handler = XiaoxinControlHandler(config, runtime)
        logger = RecordingLogger()
        handler.logger = logger

        response = await handler.handle_location_heartbeat(
            _location_heartbeat_request(
                credential,
                remote="10.0.0.5",
                extra_headers={"X-Forwarded-For": "1.1.1.1"},
            )
        )
        return response, runtime.overview_service.observe_calls, logger.warnings

    response, calls, warnings = asyncio.run(scenario())

    assert response.status == 200
    assert calls == []
    assert warnings == [
        (
            {"tag": "xiaoxin.network"},
            "invalid trusted proxy CIDR ignored",
            (),
        )
    ]
    assert "invalid-secret-cidr" not in repr(warnings)


def test_location_heartbeat_ip_filter_accepts_only_real_global_unicast(
    tmp_path,
):
    runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
    handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)

    for address in ("198.18.0.1", "192.0.0.1", "2001:db8::1"):
        assert handler._observed_public_ip(
            _request("POST", "/heartbeat", remote=address)
        ) is None
    assert handler._observed_public_ip(
        _request("POST", "/heartbeat", remote="8.8.8.8")
    ) == "8.8.8.8"
    assert handler._observed_public_ip(
        _request("POST", "/heartbeat", remote="203.0.113.10")
    ) is None


def test_location_heartbeat_rejects_duplicate_forwarded_ip_headers(tmp_path):
    runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
    config = {
        "xiaoxin_control": {
            "overview_mqtt": {"trusted_proxy_cidrs": ["10.0.0.0/8"]}
        }
    }
    handler = XiaoxinControlHandler(config, runtime)

    for header_name in ("X-Forwarded-For", "X-Real-IP"):
        headers = CIMultiDict()
        headers.add(header_name, "1.1.1.1")
        headers.add(header_name, "8.8.8.8")
        request = _request(
            "POST",
            "/heartbeat",
            remote="10.0.0.5",
            headers=headers,
        )
        assert handler._observed_public_ip(request) is None


async def _weather_location_scenario(tmp_path, *, openid="weather-user"):
    runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
    overview_store = XiaoxinOverviewStore(tmp_path / "overview.db")
    weather_provider = StaticWeatherProvider()
    runtime.overview_store = overview_store
    runtime.overview_service = OverviewSyncService(
        identity_store=runtime.identity_store,
        overview_store=overview_store,
        weather_provider=weather_provider,
    )
    handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
    _status, session = await _miniprogram_session(handler, openid=openid)
    user = runtime.auth_service.user_for_token(session["token"])
    return runtime, handler, session, user, weather_provider


def test_weather_location_get_requires_session_and_bound_device(tmp_path):
    async def scenario():
        runtime, handler, session, _user, _provider = await _weather_location_scenario(
            tmp_path
        )
        unauthorized = await handler.handle_miniprogram_weather_location(
            _request("GET", "/api/miniprogram/weather-location")
        )
        unbound = await handler.handle_miniprogram_weather_location(
            _miniprogram_request(
                "GET",
                "/api/miniprogram/weather-location",
                session["token"],
            )
        )
        return unauthorized, unbound

    unauthorized, unbound = asyncio.run(scenario())

    assert unauthorized.status == 401
    assert unbound.status == 404
    assert json.loads(unbound.text) == {
        "success": False,
        "message": "no bound device",
    }


def test_weather_location_patch_disabled_returns_gate_without_store_or_provider(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.overview_store = None
        runtime.overview_weather_provider = None
        runtime.overview_service = DisabledOverviewSyncService(
            identity_store=runtime.identity_store,
            registry=runtime.registry,
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler, openid="disabled-weather")
        user = runtime.auth_service.user_for_token(session["token"])
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        response = await handler.handle_miniprogram_weather_location_update(
            _miniprogram_request(
                "PATCH", "/api/miniprogram/weather-location", session["token"],
                {"mode": "manual", "province": "Zhejiang", "city": "Hangzhou"},
            )
        )
        return response.status, json.loads(response.text)

    assert asyncio.run(scenario()) == (
        503,
        {"success": False, "message": "overview_mqtt_disabled"},
    )


def test_weather_location_manual_patch_validates_and_refreshes_owned_device(tmp_path):
    async def scenario():
        runtime, handler, session, user, provider = await _weather_location_scenario(
            tmp_path
        )
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")

        missing_city = await handler.handle_miniprogram_weather_location_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/weather-location",
                session["token"],
                {"mode": "manual", "province": "Zhejiang", "city": ""},
            )
        )
        updated = await handler.handle_miniprogram_weather_location_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/weather-location",
                session["token"],
                {
                    "mode": "manual",
                    "province": "Zhejiang",
                    "city": "Hangzhou",
                },
            )
        )
        return (
            missing_city,
            updated,
            runtime.overview_store.get_location("device-1"),
            runtime.overview_store.get_snapshot("device-1"),
            provider.validation_calls,
            provider.calls,
        )

    (
        missing_city,
        updated,
        location,
        snapshot,
        validation_calls,
        provider_calls,
    ) = asyncio.run(scenario())

    assert missing_city.status == 400
    assert json.loads(missing_city.text)["field"] == "city"
    assert updated.status == 200
    body = json.loads(updated.text)
    assert body["weatherLocation"]["mode"] == "manual"
    assert body["weatherLocation"]["province"] == "Zhejiang"
    assert body["weatherLocation"]["city"] == "Hangzhou"
    assert body["weatherLocation"]["weatherSummary"]
    assert body["weatherLocation"]["weatherFetchedAt"] == (
        f'{body["weatherLocation"]["weatherDate"]}T08:00:00+00:00'
    )
    assert body["weatherLocation"]["weatherProvider"] == "open_meteo"
    assert body["weatherLocation"]["syncRevision"] == snapshot.revision
    assert location["mode"] == "manual"
    assert validation_calls == [("Zhejiang", "Hangzhou")]
    assert provider_calls and provider_calls[0][:2] == ("Zhejiang", "Hangzhou")


def test_weather_location_manual_rejects_fictional_city_before_persist_or_refresh(
    tmp_path,
):
    async def scenario():
        runtime, handler, session, user, provider = await _weather_location_scenario(
            tmp_path
        )
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        provider.validation_error = LocationValidationError(
            "raw provider body: fictional province/city"
        )

        response = await handler.handle_miniprogram_weather_location_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/weather-location",
                session["token"],
                {
                    "mode": "manual",
                    "province": "FictionalProvince",
                    "city": "FictionalCity",
                },
            )
        )
        return response, runtime, provider

    response, runtime, provider = asyncio.run(scenario())

    assert response.status == 400
    assert json.loads(response.text) == {
        "success": False,
        "message": "invalid weather location",
        "field": "city",
    }
    assert "raw provider body" not in response.text
    assert runtime.overview_store.get_location("device-1") is None
    assert runtime.overview_store.get_snapshot("device-1") is None
    assert provider.calls == []


def test_weather_location_manual_malformed_geocode_is_retryable_not_user_error(
    tmp_path,
):
    async def scenario():
        runtime, handler, session, user, provider = await _weather_location_scenario(
            tmp_path
        )
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        provider.validation_error = ProviderDataError(
            "raw malformed geocoding schema and provider body"
        )

        response = await handler.handle_miniprogram_weather_location_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/weather-location",
                session["token"],
                {
                    "mode": "manual",
                    "province": "Zhejiang",
                    "city": "Hangzhou",
                },
            )
        )
        return response, runtime, provider

    response, runtime, provider = asyncio.run(scenario())

    assert response.status == 503
    assert json.loads(response.text) == {
        "success": False,
        "message": "weather location validation unavailable",
        "retryable": True,
    }
    assert "raw malformed" not in response.text
    assert runtime.overview_store.get_location("device-1") is None
    assert runtime.overview_store.get_snapshot("device-1") is None
    assert provider.calls == []


def test_weather_location_manual_geocode_timeout_is_retryable_and_not_persisted(
    tmp_path,
):
    async def scenario():
        runtime, handler, session, user, provider = await _weather_location_scenario(
            tmp_path
        )
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        provider.validation_error = asyncio.TimeoutError("raw timeout detail")

        response = await handler.handle_miniprogram_weather_location_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/weather-location",
                session["token"],
                {
                    "mode": "manual",
                    "province": "Zhejiang",
                    "city": "Hangzhou",
                },
            )
        )
        return response, runtime, provider

    response, runtime, provider = asyncio.run(scenario())

    assert response.status == 503
    assert json.loads(response.text) == {
        "success": False,
        "message": "weather location validation unavailable",
        "retryable": True,
    }
    assert "raw timeout detail" not in response.text
    assert runtime.overview_store.get_location("device-1") is None
    assert runtime.overview_store.get_snapshot("device-1") is None
    assert provider.calls == []


def test_weather_location_manual_accepts_valid_city_when_forecast_is_transiently_down(
    tmp_path,
):
    async def scenario():
        runtime, handler, session, user, provider = await _weather_location_scenario(
            tmp_path
        )
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        provider.daily_error = asyncio.TimeoutError("forecast temporarily down")

        response = await handler.handle_miniprogram_weather_location_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/weather-location",
                session["token"],
                {
                    "mode": "manual",
                    "province": "Zhejiang",
                    "city": "Hangzhou",
                },
            )
        )
        return response, runtime, provider

    response, runtime, provider = asyncio.run(scenario())

    assert response.status == 200
    assert runtime.overview_store.get_location("device-1")["city"] == "Hangzhou"
    assert runtime.overview_store.get_snapshot("device-1") is not None
    assert provider.validation_calls == [("Zhejiang", "Hangzhou")]
    assert provider.calls and provider.calls[0][:2] == ("Zhejiang", "Hangzhou")


def test_weather_location_manual_rechecks_owner_after_geocode_before_write(tmp_path):
    async def scenario():
        runtime, handler, session_a, user_a, _provider = await _weather_location_scenario(
            tmp_path,
            openid="weather-owner-a",
        )
        _status_b, session_b = await _miniprogram_session(
            handler,
            openid="weather-owner-b",
        )
        user_b = runtime.auth_service.user_for_token(session_b["token"])
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user_a.id, "Desk")
        provider = BlockingValidationWeatherProvider()
        runtime.overview_service.weather_provider = provider

        patch_task = asyncio.create_task(
            handler.handle_miniprogram_weather_location_update(
                _miniprogram_request(
                    "PATCH",
                    "/api/miniprogram/weather-location",
                    session_a["token"],
                    {
                        "mode": "manual",
                        "province": "Zhejiang",
                        "city": "Hangzhou",
                    },
                )
            )
        )
        await provider.validation_started.wait()
        runtime.identity_store.unbind_device("device-1", user_a.id)
        runtime.identity_store.bind_device("device-1", user_b.id, "Desk B")
        provider.validation_release.set()
        response = await patch_task
        return response, runtime, provider

    response, runtime, provider = asyncio.run(scenario())

    assert response.status == 404
    assert json.loads(response.text) == {
        "success": False,
        "message": "device not found",
    }
    assert runtime.overview_store.get_location("device-1") is None
    assert runtime.overview_store.get_snapshot("device-1") is None
    assert provider.calls == []


def test_weather_location_manual_rolls_back_if_owner_changes_during_refresh(tmp_path):
    async def scenario():
        runtime, handler, session_a, user_a, _provider = await _weather_location_scenario(
            tmp_path,
            openid="weather-refresh-a",
        )
        _status_b, session_b = await _miniprogram_session(
            handler,
            openid="weather-refresh-b",
        )
        user_b = runtime.auth_service.user_for_token(session_b["token"])
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user_a.id, "Desk")
        provider = BlockingForecastWeatherProvider()
        runtime.overview_service.weather_provider = provider

        patch_task = asyncio.create_task(
            handler.handle_miniprogram_weather_location_update(
                _miniprogram_request(
                    "PATCH",
                    "/api/miniprogram/weather-location",
                    session_a["token"],
                    {
                        "mode": "manual",
                        "province": "Zhejiang",
                        "city": "Hangzhou",
                    },
                )
            )
        )
        await provider.forecast_started.wait()
        runtime.identity_store.unbind_device("device-1", user_a.id)
        runtime.identity_store.bind_device("device-1", user_b.id, "Desk B")
        provider.forecast_release.set()
        response = await patch_task
        return response, runtime

    response, runtime = asyncio.run(scenario())

    assert response.status == 404
    assert json.loads(response.text)["message"] == "device not found"
    assert runtime.overview_store.get_location("device-1") is None
    assert runtime.overview_store.get_snapshot("device-1") is None


def test_weather_location_automatic_rolls_back_if_owner_changes_during_refresh(
    tmp_path,
):
    async def scenario():
        runtime, handler, session_a, user_a, _provider = await _weather_location_scenario(
            tmp_path,
            openid="weather-auto-a",
        )
        _status_b, session_b = await _miniprogram_session(
            handler,
            openid="weather-auto-b",
        )
        user_b = runtime.auth_service.user_for_token(session_b["token"])
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user_a.id, "Desk")
        runtime.overview_store.set_automatic_location(
            "device-1",
            "old-ip-hmac",
            IpCityLocation(
                province="Zhejiang",
                city="Hangzhou",
                country_code="CN",
                located_at="2026-07-10T08:00:00+00:00",
            ),
        )
        runtime.overview_store.set_manual_location("device-1", "Shanghai", "Shanghai")
        previous = runtime.overview_store.get_location("device-1")
        provider = BlockingForecastWeatherProvider()
        runtime.overview_service.weather_provider = provider

        patch_task = asyncio.create_task(
            handler.handle_miniprogram_weather_location_update(
                _miniprogram_request(
                    "PATCH",
                    "/api/miniprogram/weather-location",
                    session_a["token"],
                    {"mode": "automatic"},
                )
            )
        )
        await provider.forecast_started.wait()
        runtime.identity_store.unbind_device("device-1", user_a.id)
        runtime.identity_store.bind_device("device-1", user_b.id, "Desk B")
        provider.forecast_release.set()
        response = await patch_task
        return response, runtime, previous

    response, runtime, previous = asyncio.run(scenario())

    assert response.status == 404
    assert json.loads(response.text)["message"] == "device not found"
    restored = runtime.overview_store.get_location("device-1")
    assert restored["mode"] == previous["mode"] == "manual"
    assert (restored["province"], restored["city"]) == (
        previous["province"],
        previous["city"],
    )
    assert runtime.overview_store.get_snapshot("device-1") is None


def test_manual_rollback_does_not_overwrite_concurrent_automatic_candidate(tmp_path):
    async def scenario():
        runtime, handler, session_a, user_a, _provider = await _weather_location_scenario(
            tmp_path,
            openid="weather-cas-a",
        )
        _status_b, session_b = await _miniprogram_session(
            handler,
            openid="weather-cas-b",
        )
        user_b = runtime.auth_service.user_for_token(session_b["token"])
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user_a.id, "Desk")
        runtime.overview_store.set_manual_location("device-1", "Shanghai", "Shanghai")
        weather_provider = BlockingForecastWeatherProvider()
        ip_provider = SignalingIpLocationProvider()
        runtime.overview_service.weather_provider = weather_provider
        runtime.overview_service.ip_location_provider = ip_provider
        runtime.overview_service._ip_hmac_key = b"test-ip-hmac-key"

        patch_task = asyncio.create_task(
            handler.handle_miniprogram_weather_location_update(
                _miniprogram_request(
                    "PATCH",
                    "/api/miniprogram/weather-location",
                    session_a["token"],
                    {
                        "mode": "manual",
                        "province": "Zhejiang",
                        "city": "Hangzhou",
                    },
                )
            )
        )
        await weather_provider.forecast_started.wait()
        observe_task = asyncio.create_task(
            runtime.overview_service.observe_device_ip(
                "device-1",
                "8.8.8.8",
                "location_heartbeat",
            )
        )
        await asyncio.sleep(0)
        assert ip_provider.located.is_set() is False
        runtime.identity_store.unbind_device("device-1", user_a.id)
        runtime.identity_store.bind_device("device-1", user_b.id, "Desk B")
        weather_provider.forecast_release.set()
        patch_response, observe_result = await asyncio.gather(
            patch_task,
            observe_task,
        )
        return patch_response, observe_result, runtime, user_b

    patch_response, observe_result, runtime, user_b = asyncio.run(scenario())

    assert patch_response.status == 404
    location = runtime.overview_store.get_location("device-1")
    assert location["mode"] == "manual"
    assert location["province"] == "Shanghai"
    assert location["city"] == "Shanghai"
    assert location["automatic_province"] == "Jiangsu"
    assert location["automatic_city"] == "Nanjing"
    assert observe_result["refreshed"] is True
    snapshot = runtime.overview_store.get_snapshot("device-1")
    assert snapshot is not None
    assert snapshot.owner_user_id == user_b.id


def test_weather_location_patch_cannot_target_another_students_device(tmp_path):
    async def scenario():
        runtime, handler, session_a, user_a, _provider = await _weather_location_scenario(
            tmp_path,
            openid="weather-user-a",
        )
        _status_b, session_b = await _miniprogram_session(
            handler,
            openid="weather-user-b",
        )
        user_b = runtime.auth_service.user_for_token(session_b["token"])
        runtime.identity_store.upsert_seen_device("device-a")
        runtime.identity_store.bind_device("device-a", user_a.id, "A")
        runtime.identity_store.upsert_seen_device("device-b")
        runtime.identity_store.bind_device("device-b", user_b.id, "B")

        response = await handler.handle_miniprogram_weather_location_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/weather-location",
                session_a["token"],
                {
                    "deviceId": "device-b",
                    "mode": "manual",
                    "province": "Shanghai",
                    "city": "Shanghai",
                },
            )
        )
        return response, runtime.overview_store.get_location("device-b")

    response, other_location = asyncio.run(scenario())

    assert response.status == 404
    assert json.loads(response.text)["message"] == "device not found"
    assert other_location is None


def test_weather_location_automatic_restores_latest_candidate_without_leaks(tmp_path):
    async def scenario():
        runtime, handler, session, user, _provider = await _weather_location_scenario(
            tmp_path
        )
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        runtime.overview_store.set_automatic_location(
            "device-1",
            "raw-ip-hmac-must-not-leak",
            IpCityLocation(
                province="Zhejiang",
                city="Hangzhou",
                country_code="CN",
                located_at="2026-07-10T08:00:00+00:00",
            ),
        )
        runtime.overview_store.set_manual_location("device-1", "Shanghai", "Shanghai")

        updated = await handler.handle_miniprogram_weather_location_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/weather-location",
                session["token"],
                {"mode": "automatic"},
            )
        )
        fetched = await handler.handle_miniprogram_weather_location(
            _miniprogram_request(
                "GET",
                "/api/miniprogram/weather-location",
                session["token"],
            )
        )
        return updated, fetched, runtime.overview_store.get_location("device-1")

    updated, fetched, location = asyncio.run(scenario())

    assert updated.status == 200
    assert fetched.status == 200
    updated_body = json.loads(updated.text)
    fetched_body = json.loads(fetched.text)
    assert updated_body["weatherLocation"]["mode"] == "automatic"
    assert updated_body["weatherLocation"]["city"] == "Hangzhou"
    assert fetched_body["weatherLocation"]["city"] == "Hangzhou"
    assert location["mode"] == "automatic"
    serialized = json.dumps(fetched_body)
    assert "raw-ip-hmac-must-not-leak" not in serialized
    assert "public_ip" not in serialized
    assert "openid" not in serialized


def test_semester_overview_refresh_runs_only_after_successful_write(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        overview_service = RecordingOverviewService()
        runtime.overview_service = overview_service
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)
        user = runtime.auth_service.user_for_token(session["token"])

        get_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/semester",
            session["token"],
        )
        get_response = await handler.handle_miniprogram_semester(get_request)
        curriculum_response = await handler.handle_miniprogram_curriculum_overview(
            _miniprogram_request(
                "GET",
                "/api/miniprogram/curriculum/overview?date=2026-07-10",
                session["token"],
            )
        )
        overview_response = await handler.handle_miniprogram_overview(
            _miniprogram_request(
                "GET",
                "/api/miniprogram/overview?date=2026-07-10",
                session["token"],
            )
        )

        invalid_request = _miniprogram_request(
            "PATCH",
            "/api/miniprogram/semester",
            session["token"],
            {"label": "2026 spring", "startDate": "bad", "totalWeeks": 18},
        )
        invalid_response = await handler.handle_miniprogram_semester_update(
            invalid_request
        )

        update_request = _miniprogram_request(
            "PATCH",
            "/api/miniprogram/semester",
            session["token"],
            {
                "label": "2026 spring",
                "startDate": "2026-03-02",
                "totalWeeks": 18,
            },
        )
        update_response = await handler.handle_miniprogram_semester_update(
            update_request
        )
        return (
            user.id,
            get_response.status,
            curriculum_response.status,
            overview_response.status,
            invalid_response.status,
            update_response.status,
            overview_service.refresh_calls,
            overview_service.clear_calls,
        )

    (
        user_id,
        get_status,
        curriculum_status,
        overview_status,
        invalid_status,
        update_status,
        refresh_calls,
        clear_calls,
    ) = asyncio.run(scenario())

    assert get_status == 200
    assert curriculum_status == 200
    assert overview_status == 200
    assert invalid_status == 400
    assert update_status == 200
    assert refresh_calls == [(user_id, "semester_updated")]
    assert clear_calls == []


def test_miniprogram_course_reminder_settings_default_and_update(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)
        token = session["token"]

        default_response = await handler.handle_miniprogram_course_reminder_settings(
            _miniprogram_request(
                "GET", "/api/miniprogram/course-reminder-settings", token
            )
        )
        invalid_response = (
            await handler.handle_miniprogram_course_reminder_settings_update(
                _miniprogram_request(
                    "PATCH",
                    "/api/miniprogram/course-reminder-settings",
                    token,
                    {"remindBeforeMin": 121},
                )
            )
        )
        fractional_response = (
            await handler.handle_miniprogram_course_reminder_settings_update(
                _miniprogram_request(
                    "PATCH",
                    "/api/miniprogram/course-reminder-settings",
                    token,
                    {"remindBeforeMin": 15.5},
                )
            )
        )
        updated_response = (
            await handler.handle_miniprogram_course_reminder_settings_update(
                _miniprogram_request(
                    "PATCH",
                    "/api/miniprogram/course-reminder-settings",
                    token,
                    {"remindBeforeMin": 30},
                )
            )
        )
        fetched_response = await handler.handle_miniprogram_course_reminder_settings(
            _miniprogram_request(
                "GET", "/api/miniprogram/course-reminder-settings", token
            )
        )
        return (
            default_response,
            invalid_response,
            fractional_response,
            updated_response,
            fetched_response,
        )

    (
        default_response,
        invalid_response,
        fractional_response,
        updated_response,
        fetched_response,
    ) = asyncio.run(scenario())

    assert default_response.status == 200
    assert json.loads(default_response.text)["courseReminderSettings"] == {
        "remindBeforeMin": 15
    }
    assert invalid_response.status == 400
    assert fractional_response.status == 400
    assert updated_response.status == 200
    assert json.loads(updated_response.text)["courseReminderSettings"] == {
        "remindBeforeMin": 30
    }
    assert json.loads(fetched_response.text)["courseReminderSettings"] == {
        "remindBeforeMin": 30
    }


def test_course_overview_refresh_runs_only_after_successful_writes(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        overview_service = RecordingOverviewService()
        observation_ingress = RecordingObservationIngress()
        runtime.overview_service = overview_service
        runtime.observation_ingress = observation_ingress
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)
        user = runtime.auth_service.user_for_token(session["token"])
        token = session["token"]
        course_payload = {
            "title": "Linear Algebra",
            "classroom": "Room 201",
            "teacher": "Professor Liu",
            "weekday": 2,
            "startSection": 3,
            "endSection": 4,
            "weekRange": "1-18",
            "startsAt": "10:10",
            "endsAt": "11:45",
            "notes": "",
        }

        list_response = await handler.handle_miniprogram_courses(
            _miniprogram_request("GET", "/api/miniprogram/courses", token)
        )
        create_response = await handler.handle_miniprogram_course_create(
            _miniprogram_request(
                "POST", "/api/miniprogram/courses", token, course_payload
            )
        )
        course_id = json.loads(create_response.text)["course"]["id"]
        get_response = await handler.handle_miniprogram_course(
            _miniprogram_request(
                "GET",
                f"/api/miniprogram/courses/{course_id}",
                token,
                match_info={"course_id": course_id},
            )
        )
        update_response = await handler.handle_miniprogram_course_update(
            _miniprogram_request(
                "PATCH",
                f"/api/miniprogram/courses/{course_id}",
                token,
                {**course_payload, "classroom": "Room 202"},
                match_info={"course_id": course_id},
            )
        )
        missing_update_response = await handler.handle_miniprogram_course_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/courses/missing",
                token,
                course_payload,
                match_info={"course_id": "missing"},
            )
        )
        missing_delete_response = await handler.handle_miniprogram_course_delete(
            _miniprogram_request(
                "DELETE",
                "/api/miniprogram/courses/missing",
                token,
                match_info={"course_id": "missing"},
            )
        )
        delete_response = await handler.handle_miniprogram_course_delete(
            _miniprogram_request(
                "DELETE",
                f"/api/miniprogram/courses/{course_id}",
                token,
                match_info={"course_id": course_id},
            )
        )
        return (
            user.id,
            list_response.status,
            create_response.status,
            get_response.status,
            update_response.status,
            missing_update_response.status,
            json.loads(missing_delete_response.text)["deleted"],
            json.loads(delete_response.text)["deleted"],
            overview_service.refresh_calls,
            observation_ingress.calls,
        )

    (
        user_id,
        list_status,
        create_status,
        get_status,
        update_status,
        missing_update_status,
        missing_deleted,
        deleted,
        refresh_calls,
        observation_calls,
    ) = asyncio.run(scenario())

    assert list_status == 200
    assert create_status == 200
    assert get_status == 200
    assert update_status == 200
    assert missing_update_status == 404
    assert missing_deleted is False
    assert deleted is True
    assert refresh_calls == [
        (user_id, "course_created"),
        (user_id, "course_updated"),
        (user_id, "course_deleted"),
    ]
    assert [call["kind"] for call in observation_calls] == [
        "course_created",
        "course_updated",
        "course_deleted",
    ]
    assert all(
        call["source_kind"] == "miniprogram_course"
        for call in observation_calls
    )


def test_miniprogram_explicit_companion_observation_is_normalized_and_ingested(
    tmp_path,
):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        ingress = RecordingObservationIngress(
            CompanionObserveResult(
                observation_id="observation-1",
                status="recorded",
                evidence_ids=("evidence-1",),
            )
        )
        runtime.observation_ingress = ingress
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)
        response = await handler.handle_miniprogram_companion_observation(
            _miniprogram_request(
                "POST",
                "/api/miniprogram/companion/observations",
                session["token"],
                {
                    "idempotencyKey": "goal-set:goal-1:v1",
                    "kind": "goal_set",
                    "payload": {
                        "goalId": "goal-1",
                        "title": "通过英语六级",
                        "targetAt": "2026-12-12T09:00:00+08:00",
                    },
                },
            )
        )
        return response, ingress.calls

    response, calls = asyncio.run(scenario())

    assert response.status == 200
    assert json.loads(response.text)["observation"] == {
        "observation_id": "observation-1",
        "status": "recorded",
        "evidence_ids": ["evidence-1"],
    }
    assert len(calls) == 1
    assert calls[0]["kind"] == "goal_set"
    assert calls[0]["source_ref"] == "goal-1"
    assert calls[0]["payload"] == {
        "goal_id": "goal-1",
        "title": "通过英语六级",
        "status": "active",
        "target_at": "2026-12-12T09:00:00+08:00",
    }


def test_todo_overview_refresh_covers_create_update_complete_and_delete(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        overview_service = RecordingOverviewService()
        observation_ingress = RecordingObservationIngress()
        runtime.overview_service = overview_service
        runtime.observation_ingress = observation_ingress
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)
        user = runtime.auth_service.user_for_token(session["token"])
        token = session["token"]

        list_response = await handler.handle_miniprogram_todos(
            _miniprogram_request("GET", "/api/miniprogram/todos", token)
        )
        invalid_response = await handler.handle_miniprogram_todo_create(
            _miniprogram_request(
                "POST",
                "/api/miniprogram/todos",
                token,
                {"title": "", "dueAt": "2026-07-10T18:00:00+08:00"},
            )
        )
        create_response = await handler.handle_miniprogram_todo_create(
            _miniprogram_request(
                "POST",
                "/api/miniprogram/todos",
                token,
                {
                    "title": "Submit report",
                    "dueAt": "2026-07-10T18:00:00+08:00",
                    "notes": "Draft ready",
                },
            )
        )
        todo_id = json.loads(create_response.text)["todo"]["id"]
        update_response = await handler.handle_miniprogram_todo_update(
            _miniprogram_request(
                "PATCH",
                f"/api/miniprogram/todos/{todo_id}",
                token,
                {"title": "Submit final report"},
                match_info={"todo_id": todo_id},
            )
        )
        complete_response = await handler.handle_miniprogram_todo_update(
            _miniprogram_request(
                "PATCH",
                f"/api/miniprogram/todos/{todo_id}",
                token,
                {"status": "done"},
                match_info={"todo_id": todo_id},
            )
        )
        missing_update_response = await handler.handle_miniprogram_todo_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/todos/missing",
                token,
                {"status": "done"},
                match_info={"todo_id": "missing"},
            )
        )
        missing_delete_response = await handler.handle_miniprogram_todo_delete(
            _miniprogram_request(
                "DELETE",
                "/api/miniprogram/todos/missing",
                token,
                match_info={"todo_id": "missing"},
            )
        )
        delete_response = await handler.handle_miniprogram_todo_delete(
            _miniprogram_request(
                "DELETE",
                f"/api/miniprogram/todos/{todo_id}",
                token,
                match_info={"todo_id": todo_id},
            )
        )
        return (
            user.id,
            list_response.status,
            invalid_response.status,
            create_response.status,
            update_response.status,
            complete_response.status,
            missing_update_response.status,
            json.loads(missing_delete_response.text)["deleted"],
            json.loads(delete_response.text)["deleted"],
            overview_service.refresh_calls,
            observation_ingress.calls,
        )

    (
        user_id,
        list_status,
        invalid_status,
        create_status,
        update_status,
        complete_status,
        missing_update_status,
        missing_deleted,
        deleted,
        refresh_calls,
        observation_calls,
    ) = asyncio.run(scenario())

    assert list_status == 200
    assert invalid_status == 400
    assert create_status == 200
    assert update_status == 200
    assert complete_status == 200
    assert missing_update_status == 404
    assert missing_deleted is False
    assert deleted is True
    assert refresh_calls == [
        (user_id, "todo_created"),
        (user_id, "todo_updated"),
        (user_id, "todo_updated"),
        (user_id, "todo_deleted"),
    ]
    assert [call["kind"] for call in observation_calls] == [
        "todo_created",
        "todo_updated",
        "todo_completed",
        "todo_deleted",
    ]
    assert observation_calls[2]["payload"]["completion_source"] == (
        "explicit_user_action"
    )


def test_activation_bind_overview_refresh_runs_after_identity_commit(tmp_path):
    async def scenario(case_name, use_cookie, already_bound):
        runtime = AuthRuntime(tmp_path / f"{case_name}.db")
        overview_service = RecordingOverviewService()
        runtime.overview_service = overview_service
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        if use_cookie:
            user, token = runtime.auth_service.register(
                f"{case_name}-user", "secret-pass", case_name
            )
        else:
            _status, session_body = await _miniprogram_session(
                handler, openid=f"{case_name}-openid"
            )
            token = session_body["token"]
            user = runtime.auth_service.user_for_token(token)

        device_id = f"device-{case_name}"
        runtime.identity_store.upsert_seen_device(device_id, case_name)
        if already_bound:
            runtime.identity_store.bind_device(device_id, user.id, case_name)
        activation = runtime.activation_store.create_or_refresh_activation(device_id)
        observations = []

        def observe_refresh(user_id, reason):
            device = runtime.identity_store.get_device_by_device_id(device_id)
            consumed = runtime.activation_store.get_activation_by_code(
                activation.code
            ).consumed_at
            observations.append(
                (user_id, reason, device.owner_user_id, device.bind_status, bool(consumed))
            )

        overview_service.refresh_probe = observe_refresh
        if use_cookie:
            request = _request(
                "POST",
                "/api/xiaoxin/devices/activation-bind",
                headers={"Cookie": f"xiaoxin_session={token}"},
            )
            request._read_bytes = _json_body(
                {"code": activation.code, "display_name": case_name}
            )
        else:
            request = _miniprogram_request(
                "POST",
                "/api/xiaoxin/devices/activation-bind",
                token,
                {"code": activation.code, "display_name": case_name},
            )
        response = await handler.handle_activation_bind_device(request)

        failed_request = _miniprogram_request(
            "POST",
            "/api/xiaoxin/devices/activation-bind",
            token,
            {"code": "999999"},
        )
        failed_response = await handler.handle_activation_bind_device(failed_request)
        return (
            user.id,
            response.status,
            failed_response.status,
            overview_service.refresh_calls,
            observations,
        )

    for case_name, use_cookie, already_bound in (
        ("miniprogram-bind", False, False),
        ("console-rebind", True, True),
    ):
        user_id, status, failed_status, refresh_calls, observations = asyncio.run(
            scenario(case_name, use_cookie, already_bound)
        )
        assert status == 200
        assert failed_status == 404
        assert refresh_calls == [(user_id, "device_bound")]
        assert observations == [
            (user_id, "device_bound", user_id, "bound", True)
        ]


def test_unbind_overview_clear_runs_after_identity_commit(tmp_path):
    async def scenario(case_name, use_cookie):
        runtime = AuthRuntime(tmp_path / f"{case_name}.db")
        overview_service = RecordingOverviewService()
        runtime.overview_service = overview_service
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        if use_cookie:
            user, token = runtime.auth_service.register(
                f"{case_name}-user", "secret-pass", case_name
            )
        else:
            _status, session_body = await _miniprogram_session(
                handler, openid=f"{case_name}-openid"
            )
            token = session_body["token"]
            user = runtime.auth_service.user_for_token(token)

        device_id = f"device-{case_name}"
        runtime.identity_store.upsert_seen_device(device_id, case_name)
        runtime.identity_store.bind_device(device_id, user.id, case_name)
        observations = []

        def observe_clear(cleared_device_id, reason):
            device = runtime.identity_store.get_device_by_device_id(cleared_device_id)
            observations.append(
                (
                    cleared_device_id,
                    reason,
                    device.owner_user_id,
                    device.bind_status,
                )
            )

        overview_service.clear_probe = observe_clear
        if use_cookie:
            failed_request = _request(
                "POST",
                "/api/xiaoxin/devices/missing/unbind",
                headers={"Cookie": f"xiaoxin_session={token}"},
                match_info={"device_id": "missing"},
            )
            failed_response = await handler.handle_unbind_device(failed_request)
            request = _request(
                "POST",
                f"/api/xiaoxin/devices/{device_id}/unbind",
                headers={"Cookie": f"xiaoxin_session={token}"},
                match_info={"device_id": device_id},
            )
            response = await handler.handle_unbind_device(request)
        else:
            get_response = await handler.handle_miniprogram_device(
                _miniprogram_request("GET", "/api/miniprogram/device", token)
            )
            assert get_response.status == 200
            failed_response = await handler.handle_miniprogram_device_unbind(
                _miniprogram_request(
                    "POST",
                    "/api/miniprogram/device/unbind",
                    token,
                    {"device_id": "missing"},
                )
            )
            response = await handler.handle_miniprogram_device_unbind(
                _miniprogram_request(
                    "POST",
                    "/api/miniprogram/device/unbind",
                    token,
                    {"device_id": device_id},
                )
            )
        return (
            failed_response.status,
            response.status,
            overview_service.refresh_calls,
            overview_service.clear_calls,
            observations,
        )

    for case_name, use_cookie, failed_status in (
        ("miniprogram-unbind", False, 200),
        ("console-unbind", True, 403),
    ):
        (
            actual_failed_status,
            status,
            refresh_calls,
            clear_calls,
            observations,
        ) = asyncio.run(scenario(case_name, use_cookie))
        device_id = f"device-{case_name}"
        assert actual_failed_status == failed_status
        assert status == 200
        assert refresh_calls == []
        assert clear_calls == [(device_id, "device_unbound")]
        assert observations == [(device_id, "device_unbound", None, "seen")]


def test_overview_refresh_failure_is_logged_without_failing_committed_write(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        overview_service = RecordingOverviewService()
        overview_service.refresh_error = RuntimeError("mqtt unavailable")
        runtime.overview_service = overview_service
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        logger = RecordingLogger()
        handler.logger = logger
        _status, session = await _miniprogram_session(handler)
        user = runtime.auth_service.user_for_token(session["token"])
        response = await handler.handle_miniprogram_course_create(
            _miniprogram_request(
                "POST",
                "/api/miniprogram/courses",
                session["token"],
                {
                    "title": "Linear Algebra",
                    "weekday": 2,
                    "startSection": 3,
                    "endSection": 4,
                },
            )
        )
        return (
            user.id,
            response.status,
            runtime.identity_store.list_student_courses(user.id),
            overview_service.refresh_calls,
            logger.records,
        )

    user_id, status, courses, refresh_calls, records = asyncio.run(scenario())

    assert status == 200
    assert len(courses) == 1
    assert refresh_calls == [(user_id, "course_created")]
    assert records == [
        (
            {"tag": "xiaoxin.overview"},
            "overview refresh failed reason=course_created",
            (),
        )
    ]


def test_overview_clear_failure_is_logged_without_failing_committed_unbind(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        overview_service = RecordingOverviewService()
        overview_service.clear_error = RuntimeError("mqtt unavailable")
        runtime.overview_service = overview_service
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        logger = RecordingLogger()
        handler.logger = logger
        _status, session = await _miniprogram_session(handler)
        user = runtime.auth_service.user_for_token(session["token"])
        device_id = "device-clear-error"
        runtime.identity_store.upsert_seen_device(device_id, "Clear Error")
        runtime.identity_store.bind_device(device_id, user.id, "Clear Error")
        response = await handler.handle_miniprogram_device_unbind(
            _miniprogram_request(
                "POST",
                "/api/miniprogram/device/unbind",
                session["token"],
                {"device_id": device_id},
            )
        )
        device = runtime.identity_store.get_device_by_device_id(device_id)
        return response.status, device, overview_service.clear_calls, logger.records

    status, device, clear_calls, records = asyncio.run(scenario())

    assert status == 200
    assert device.owner_user_id is None
    assert device.bind_status == "seen"
    assert clear_calls == [("device-clear-error", "device_unbound")]
    assert records == [
        (
            {"tag": "xiaoxin.overview"},
            "overview clear failed reason=device_unbound",
            (),
        )
    ]


def test_overview_triggers_do_not_run_when_identity_writes_raise(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        overview_service = RecordingOverviewService()
        runtime.overview_service = overview_service
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)
        user = runtime.auth_service.user_for_token(session["token"])
        token = session["token"]
        device_id = "device-write-error"
        runtime.identity_store.upsert_seen_device(device_id, "Write Error")
        runtime.identity_store.bind_device(device_id, user.id, "Write Error")

        def fail_write(*args, **kwargs):
            raise RuntimeError("identity write failed")

        runtime.identity_store.create_student_course = fail_write
        try:
            await handler.handle_miniprogram_course_create(
                _miniprogram_request(
                    "POST",
                    "/api/miniprogram/courses",
                    token,
                    {
                        "title": "Linear Algebra",
                        "weekday": 2,
                        "startSection": 3,
                        "endSection": 4,
                    },
                )
            )
        except RuntimeError as exc:
            assert str(exc) == "identity write failed"
        else:
            raise AssertionError("identity write exception did not propagate")

        runtime.identity_store.unbind_device = fail_write
        try:
            await handler.handle_miniprogram_device_unbind(
                _miniprogram_request(
                    "POST",
                    "/api/miniprogram/device/unbind",
                    token,
                    {"device_id": device_id},
                )
            )
        except RuntimeError as exc:
            assert str(exc) == "identity write failed"
        else:
            raise AssertionError("identity write exception did not propagate")
        return overview_service.refresh_calls, overview_service.clear_calls

    refresh_calls, clear_calls = asyncio.run(scenario())

    assert refresh_calls == []
    assert clear_calls == []


async def _bind_miniprogram_device_by_activation(
    handler, runtime, token, device_id, display_name="我的小芯"
):
    runtime.identity_store.upsert_seen_device(device_id, display_name)
    session = runtime.activation_store.create_or_refresh_activation(device_id)
    request = _miniprogram_request(
        "POST",
        "/api/xiaoxin/devices/activation-bind",
        token,
        {"code": session.code, "display_name": display_name},
    )
    return await handler.handle_activation_bind_device(request)


def _control_event_body(device_id: str) -> bytes:
    return json.dumps(
        {
            "device_id": device_id,
            "event": "notification",
            "title": "Reminder",
            "body": "Remember the task.",
        }
    ).encode("utf-8")


async def _post_control_event(handler, token: str, device_id: str):
    request = _request(
        "POST",
        "/api/xiaoxin/events",
        headers={
            "Content-Type": "application/json",
            "Cookie": f"xiaoxin_session={token}",
        },
        payload=None,
    )
    request._read_bytes = _control_event_body(device_id)
    return await handler.handle_create_event(request)


def test_devices_endpoint_returns_auth_unavailable_without_auth_service():
    async def scenario():
        runtime = FakeRuntime()
        runtime.registry.update_doorbell_status("aa", "online")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        request = _request("GET", "/api/xiaoxin/devices")
        response = await handler.handle_devices(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 404
    assert body == {"success": False, "message": "auth unavailable"}


def test_control_devices_endpoint_lists_all_admin_targets(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        owner, _owner_token = runtime.auth_service.register("owner", "secret-pass", "Owner")
        _admin, admin_token = runtime.auth_service.register(
            "admin", "secret-pass", "Admin", role="admin"
        )
        runtime.identity_store.upsert_seen_device("seen-device", "Seen Device")
        runtime.identity_store.upsert_seen_device("bound-device", "Bound Device")
        runtime.identity_store.bind_device("bound-device", owner.id, "Bound Device")
        runtime.registry.update_doorbell_status("wakeable-device", "online")

        request = _request(
            "GET",
            "/api/xiaoxin/admin/devices",
            headers={"Cookie": f"xiaoxin_session={admin_token}"},
        )
        response = await handler.handle_admin_devices(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    devices = {device["device_id"]: device for device in body["devices"]}
    assert devices["seen-device"]["bind_status"] == "seen"
    assert devices["bound-device"]["bind_status"] == "bound"
    assert devices["wakeable-device"]["state"] == "wakeable"
    assert devices["wakeable-device"]["doorbell_state"] == "online"


def test_control_devices_endpoint_exposes_safe_overview_diagnostics_without_refresh(
    tmp_path,
):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        overview_store = XiaoxinOverviewStore(
            tmp_path / "overview.db",
            clock=lambda: datetime(2026, 7, 11, 1, 0, tzinfo=timezone.utc),
        )
        overview_service = RecordingOverviewService()
        runtime.overview_store = overview_store
        runtime.overview_service = overview_service
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        owner, _owner_token = runtime.auth_service.register(
            "owner", "secret-pass", "Owner"
        )
        _admin, admin_token = runtime.auth_service.register(
            "admin", "secret-pass", "Admin", role="admin"
        )
        runtime.identity_store.upsert_seen_device("bound-device", "Bound Device")
        runtime.identity_store.bind_device("bound-device", owner.id, "Bound Device")
        overview_store.set_manual_location("bound-device", "浙江", "杭州")
        overview_store.put_daily_weather(
            DailyWeather(
                province="浙江",
                city="杭州",
                date="2026-07-11",
                weather_code=1,
                weather_text="晴",
                temperature_min_c=25,
                temperature_max_c=34,
                fetched_at="2026-07-11T00:30:00+00:00",
                timezone_id="Asia/Shanghai",
            ),
            "open-meteo",
        )
        snapshot, _changed = overview_store.upsert_snapshot(
            "bound-device",
            owner.id,
            {
                "bound": True,
                "weather": {"city": "杭州", "date": "2026-07-11"},
                "payload": "raw-secret-payload",
                "openid": "wx-secret",
                "public_ip": "8.8.8.8",
                "public_ip_hmac": "secret-hmac",
                "credential": "secret-password",
                "provider_body": {"api_key": "secret-provider-body"},
            },
            "2026-07-11T01:00:00+00:00",
        )
        overview_store.mark_publish_attempt(
            "bound-device",
            snapshot.revision,
            "2026-07-11T01:10:00+00:00",
            "broker password=secret-password",
        )

        request = _request(
            "GET",
            "/api/xiaoxin/devices",
            headers={"Cookie": f"xiaoxin_session={admin_token}"},
        )
        response = await handler.handle_devices(request)
        return response.status, json.loads(response.text), overview_service, owner.id

    status, body, overview_service, owner_id = asyncio.run(scenario())

    assert status == 200
    overview = next(
        device["overview"]
        for device in body["devices"]
        if device["device_id"] == "bound-device"
    )
    assert overview == {
        "revision": 1,
        "publish_state": "pending",
        "published_at": None,
        "last_error": "overview_sync_failed",
        "attempts": 1,
        "weather": {
            "mode": "manual",
            "city": "杭州",
            "date": "2026-07-11",
            "cache_status": "cached",
        },
    }
    serialized = json.dumps(overview, ensure_ascii=False)
    for secret in (
        "raw-secret-payload",
        "wx-secret",
        "8.8.8.8",
        "secret-hmac",
        "secret-password",
        "secret-provider-body",
        owner_id,
    ):
        assert secret not in serialized
    assert overview_service.device_refresh_calls == []


def test_control_devices_survives_malformed_overview_payload_json(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        overview_store = XiaoxinOverviewStore(
            tmp_path / "overview.db",
            clock=lambda: datetime(2026, 7, 11, 1, 0, tzinfo=timezone.utc),
        )
        overview_service = RecordingOverviewService()
        runtime.overview_store = overview_store
        runtime.overview_service = overview_service
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        owner, _owner_token = runtime.auth_service.register(
            "owner", "secret-pass", "Owner"
        )
        _admin, admin_token = runtime.auth_service.register(
            "admin", "secret-pass", "Admin", role="admin"
        )
        for device_id, display_name, city in (
            ("bad-device", "Bad Device", "杭州"),
            ("good-device", "Good Device", "宁波"),
        ):
            runtime.identity_store.upsert_seen_device(device_id, display_name)
            runtime.identity_store.bind_device(device_id, owner.id, display_name)
            overview_store.set_manual_location(device_id, "浙江", city)
        overview_store.put_daily_weather(
            DailyWeather(
                province="浙江",
                city="宁波",
                date="2026-07-11",
                weather_code=1,
                weather_text="晴",
                temperature_min_c=25,
                temperature_max_c=34,
                fetched_at="2026-07-11T00:30:00+00:00",
                timezone_id="Asia/Shanghai",
            ),
            "open-meteo",
        )
        for device_id, city in (
            ("bad-device", "杭州"),
            ("good-device", "宁波"),
        ):
            overview_store.upsert_snapshot(
                device_id,
                owner.id,
                {
                    "bound": True,
                    "weather": {"city": city, "date": "2026-07-11"},
                },
                "2026-07-11T01:00:00+00:00",
            )
        with overview_store._connect() as conn:
            conn.execute(
                """
                UPDATE device_overview_snapshots
                SET payload_json = ?, last_error = ?
                WHERE device_id = ?
                """,
                (
                    '{"weather":{"date":"raw-payload-secret"',
                    "broker password=raw-error-secret",
                    "bad-device",
                ),
            )

        request = _request(
            "GET",
            "/api/xiaoxin/devices",
            headers={"Cookie": f"xiaoxin_session={admin_token}"},
        )
        response = await handler.handle_devices(request)
        return response.status, json.loads(response.text), overview_service

    status, body, overview_service = asyncio.run(scenario())

    assert status == 200
    devices = {device["device_id"]: device for device in body["devices"]}
    assert devices["bad-device"]["overview"] == {
        "revision": 1,
        "publish_state": "pending",
        "published_at": None,
        "last_error": "overview_sync_failed",
        "attempts": 0,
        "weather": {
            "mode": "manual",
            "city": "杭州",
            "date": "",
            "cache_status": "unknown",
        },
    }
    assert devices["good-device"]["overview"]["weather"] == {
        "mode": "manual",
        "city": "宁波",
        "date": "2026-07-11",
        "cache_status": "cached",
    }
    serialized = json.dumps(body, ensure_ascii=False)
    assert "raw-payload-secret" not in serialized
    assert "raw-error-secret" not in serialized
    assert overview_service.device_refresh_calls == []


def test_manual_overview_mqtt_sync_queues_without_live_websocket(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        overview_service = RecordingOverviewService()
        runtime.overview_service = overview_service
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _admin, admin_token = runtime.auth_service.register(
            "admin", "secret-pass", "Admin", role="admin"
        )
        runtime.identity_store.upsert_seen_device("offline-device", "Offline Device")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/offline-device/overview-mqtt-sync",
            headers={"Cookie": f"xiaoxin_session={admin_token}"},
            match_info={"device_id": "offline-device"},
        )
        response = await handler.handle_sync_device_overview_mqtt(request)
        return response.status, json.loads(response.text), overview_service

    status, body, overview_service = asyncio.run(scenario())

    assert status == 200
    assert body == {
        "success": True,
        "device_id": "offline-device",
        "revision": 7,
        "publish_state": "pending",
    }
    assert overview_service.device_refresh_calls == [
        ("offline-device", "manual_resync")
    ]


def test_manual_overview_mqtt_sync_sanitizes_service_failure(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        overview_service = RecordingOverviewService()
        overview_service.device_refresh_error = RuntimeError(
            "broker password=super-secret provider-body={token:secret}"
        )
        runtime.overview_service = overview_service
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _admin, admin_token = runtime.auth_service.register(
            "admin", "secret-pass", "Admin", role="admin"
        )
        runtime.identity_store.upsert_seen_device("offline-device", "Offline Device")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/offline-device/overview-mqtt-sync",
            headers={"Cookie": f"xiaoxin_session={admin_token}"},
            match_info={"device_id": "offline-device"},
        )
        response = await handler.handle_sync_device_overview_mqtt(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 502
    assert body == {"success": False, "message": "overview mqtt sync failed"}
    assert "super-secret" not in json.dumps(body)


def test_manual_overview_mqtt_sync_returns_stable_disabled_gate(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.overview_store = None
        runtime.overview_service = DisabledOverviewSyncService(
            identity_store=runtime.identity_store,
            registry=runtime.registry,
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _admin, token = runtime.auth_service.register(
            "admin-disabled", "secret-pass", "Admin", role="admin"
        )
        runtime.identity_store.upsert_seen_device("offline-device", "Offline Device")
        response = await handler.handle_sync_device_overview_mqtt(_request(
            "POST", "/api/xiaoxin/devices/offline-device/overview-mqtt-sync",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"device_id": "offline-device"},
        ))
        return response.status, json.loads(response.text)

    assert asyncio.run(scenario()) == (
        503,
        {"success": False, "message": "overview_mqtt_disabled"},
    )


def test_miniprogram_session_creates_student_profile_from_openid(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        status, body = await _miniprogram_session(handler)
        return status, body

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["success"] is True
    assert body["token"]
    assert body["profile"]["openid"] == "wx-openid-1"
    assert body["profile"]["nickname"] == "小杭"
    assert body["profile"]["student_no"] == ""


def test_miniprogram_minor_compliance_and_guardian_confirmation_story(tmp_path):
    async def scenario():
        db_path = tmp_path / "xiaoxin_control.db"
        runtime = AuthRuntime(db_path, auto_configure_compliance=False)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)

        student_status, student_session = await _miniprogram_session(
            handler,
            openid="wx-student-minor",
            nickname="小同学",
        )
        conflict_status, conflict_body = await _miniprogram_session(
            handler,
            openid="wx-student-minor",
            nickname="错误监护账号",
            account_role="guardian",
        )
        with runtime.identity_store._connect() as conn:
            orphan_guardian = conn.execute(
                "SELECT 1 FROM users WHERE username = ?",
                ("mp-guardian:wx-student-minor",),
            ).fetchone()

        age_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/compliance/age-band",
            student_session["token"],
            {"ageBand": "AGE_14_17"},
        )
        age_response = await handler.handle_miniprogram_compliance_age_band(
            age_request
        )
        agreement_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/compliance/agreements",
            student_session["token"],
            {"accepted": True},
        )
        agreement_response = (
            await handler.handle_miniprogram_compliance_agreements(
                agreement_request
            )
        )

        invitation_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/guardian/invitations",
            student_session["token"],
        )
        invitation_response = (
            await handler.handle_miniprogram_guardian_invitation_create(
                invitation_request
            )
        )
        invitation = json.loads(invitation_response.text)["invitation"]

        guardian_status, guardian_session = await _miniprogram_session(
            handler,
            openid="wx-guardian-1",
            nickname="监护人",
            account_role="guardian",
        )
        guardian_user_id = guardian_session["user"]["id"]
        guardian_profile = runtime.identity_store.get_student_profile_for_user(
            guardian_user_id
        )
        guardian_pet = runtime.identity_store.get_personal_pet_for_user(
            guardian_user_id
        )

        with runtime.compliance_service.store._connect() as conn:
            conn.execute(
                "UPDATE guardian_bindings SET expires_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", invitation["bindingId"]),
            )
        expired_request = _miniprogram_request(
            "GET",
            f"/api/miniprogram/guardian/invitations/{invitation['token']}",
            guardian_session["token"],
            match_info={"token": invitation["token"]},
        )
        expired_response = (
            await handler.handle_miniprogram_guardian_invitation(expired_request)
        )

        replacement_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/guardian/invitations",
            student_session["token"],
        )
        replacement_response = (
            await handler.handle_miniprogram_guardian_invitation_create(
                replacement_request
            )
        )
        replacement = json.loads(replacement_response.text)["invitation"]
        accept_request = _miniprogram_request(
            "POST",
            f"/api/miniprogram/guardian/invitations/{replacement['token']}/accept",
            guardian_session["token"],
            {"accepted": True},
            match_info={"token": replacement["token"]},
        )
        accept_response = (
            await handler.handle_miniprogram_guardian_invitation_accept(
                accept_request
            )
        )
        replay_request = _miniprogram_request(
            "POST",
            f"/api/miniprogram/guardian/invitations/{replacement['token']}/accept",
            guardian_session["token"],
            {"accepted": True},
            match_info={"token": replacement["token"]},
        )
        replay_response = (
            await handler.handle_miniprogram_guardian_invitation_accept(
                replay_request
            )
        )

        status_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/compliance/status",
            student_session["token"],
        )
        final_status_response = (
            await handler.handle_miniprogram_compliance_status(status_request)
        )
        locked_age_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/compliance/age-band",
            student_session["token"],
            {"ageBand": "AGE_18_PLUS"},
        )
        locked_age_response = (
            await handler.handle_miniprogram_compliance_age_band(
                locked_age_request
            )
        )
        runtime.compliance_service.config = ComplianceConfig(
            companion_service_mode=GlobalCompanionMode.ENABLED,
            current_service_agreement_version="service-2026-08-v2",
        )
        renewed_agreement_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/compliance/agreements",
            student_session["token"],
            {"accepted": True},
        )
        renewed_agreement_response = (
            await handler.handle_miniprogram_compliance_agreements(
                renewed_agreement_request
            )
        )
        return {
            "student_status": student_status,
            "conflict_status": conflict_status,
            "conflict_body": conflict_body,
            "orphan_guardian": orphan_guardian,
            "age": (age_response.status, json.loads(age_response.text)),
            "agreement": (
                agreement_response.status,
                json.loads(agreement_response.text),
            ),
            "invitation_status": invitation_response.status,
            "guardian_status": guardian_status,
            "guardian_profile": guardian_profile,
            "guardian_pet": guardian_pet,
            "expired": (
                expired_response.status,
                json.loads(expired_response.text),
            ),
            "replacement_status": replacement_response.status,
            "accepted": (
                accept_response.status,
                json.loads(accept_response.text),
            ),
            "replay": (
                replay_response.status,
                json.loads(replay_response.text),
            ),
            "final": (
                final_status_response.status,
                json.loads(final_status_response.text),
            ),
            "locked_age": (
                locked_age_response.status,
                json.loads(locked_age_response.text),
            ),
            "renewed_agreement": (
                renewed_agreement_response.status,
                json.loads(renewed_agreement_response.text),
            ),
        }

    result = asyncio.run(scenario())

    assert result["student_status"] == 200
    assert result["conflict_status"] == 409
    assert result["conflict_body"]["code"] == "account_role_conflict"
    assert result["orphan_guardian"] is None
    assert result["age"][0] == 200
    assert result["age"][1]["compliance"]["companionMode"] == "tool_only"
    assert result["agreement"][0] == 200
    assert result["agreement"][1]["compliance"]["requiredActions"] == [
        "confirm_guardian"
    ]
    assert result["invitation_status"] == 200
    assert result["guardian_status"] == 200
    assert result["guardian_profile"] is None
    assert result["guardian_pet"] is None
    assert result["expired"][0] == 409
    assert result["expired"][1]["code"] == "guardian_invitation_expired"
    assert result["replacement_status"] == 200
    assert result["accepted"][0] == 200
    assert result["accepted"][1]["compliance"]["companionMode"] == (
        "minor_companion"
    )
    assert result["replay"][0] == 409
    assert result["replay"][1]["code"] == "guardian_invitation_unavailable"
    assert result["final"][0] == 200
    assert result["final"][1]["compliance"]["guardianConfirmed"] is True
    assert result["final"][1]["compliance"]["requiredActions"] == []
    assert result["locked_age"][0] == 409
    assert result["locked_age"][1]["code"] == "age_band_locked"
    assert result["renewed_agreement"][0] == 200
    renewed_compliance = result["renewed_agreement"][1]["compliance"]
    assert renewed_compliance["companionMode"] == "tool_only"
    assert renewed_compliance["guardianRequired"] is True
    assert renewed_compliance["requiredActions"] == ["confirm_guardian"]


def test_miniprogram_session_creates_student_profile_from_wechat_code(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler(
            {
                "xiaoxin_control": {
                    "miniprogram_code_openid_map": {
                        "wx-code-1": "wx-openid-from-code"
                    }
                }
            },
            runtime,
        )
        request = _request(
            "POST",
            "/api/miniprogram/session",
            headers={"Content-Type": "application/json"},
        )
        request._read_bytes = _json_body({"code": "wx-code-1", "nickname": "小杭"})
        response = await handler.handle_miniprogram_session(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["success"] is True
    assert body["token"]
    assert body["profile"]["openid"] == "wx-openid-from-code"
    assert body["profile"]["nickname"] == "小杭"


def test_miniprogram_session_rejects_wechat_code_without_exchange_config(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        request = _request(
            "POST",
            "/api/miniprogram/session",
            headers={"Content-Type": "application/json"},
        )
        request._read_bytes = _json_body({"code": "wx-code-1", "nickname": "小杭"})
        response = await handler.handle_miniprogram_session(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 400
    assert body == {
        "success": False,
        "message": "wechat code exchange unavailable",
        "field": "code",
    }


def test_miniprogram_wechat_credentials_can_come_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XIAOXIN_MINIPROGRAM_APPID", "wx-env-appid")
    monkeypatch.setenv("XIAOXIN_MINIPROGRAM_SECRET", "env-secret")
    runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
    handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)

    assert handler._miniprogram_wechat_credentials() == ("wx-env-appid", "env-secret")


def test_miniprogram_profile_can_be_updated_with_session_token(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)
        request = _miniprogram_request(
            "PATCH",
            "/api/miniprogram/profile",
            session["token"],
            {
                "student_no": "20240001",
                "college": "信息工程学院",
                "major": "计算机科学与技术",
                "class_name": "计科2401",
                "grade": "2024级",
                "nickname": "小芯同学",
            },
        )
        update_response = await handler.handle_miniprogram_profile_update(request)
        get_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/profile",
            session["token"],
        )
        get_response = await handler.handle_miniprogram_profile(get_request)
        return (
            update_response.status,
            json.loads(update_response.text),
            get_response.status,
            json.loads(get_response.text),
        )

    update_status, update_body, get_status, get_body = asyncio.run(scenario())

    assert update_status == 200
    assert update_body["profile"]["student_no"] == "20240001"
    assert get_status == 200
    assert get_body["profile"]["college"] == "信息工程学院"
    assert get_body["profile"]["class_name"] == "计科2401"


def test_miniprogram_profile_update_persists_authoritative_academic_revision(
    tmp_path,
):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(
            handler, openid="wx-academic-revision", nickname="Liu"
        )
        user = runtime.identity_store.get_user_by_username(
            "mp:wx-academic-revision"
        )
        assert user is not None
        runtime.identity_store.upsert_seen_device("device-academic")
        runtime.identity_store.bind_device(
            "device-academic", user.id, "Academic device"
        )
        speaker = runtime.identity_store.get_or_create_speaker_profile(
            user.id,
            "device-academic",
            "speaker-academic",
            "Liu",
        )
        subject = runtime.identity_store.get_or_create_memory_subject(
            user.id,
            "device-academic",
            speaker.id,
            "user_speaker",
            "Liu",
        )
        profile = runtime.identity_store.update_student_profile(
            user.id, {"grade": "大一"}
        )
        assert profile is not None
        pet = runtime.identity_store.get_personal_pet_for_user(user.id)
        assert pet is not None
        companion_store = CompanionStore(tmp_path / "xiaoxin_companion.db")
        companion_mind = CompanionMind(
            store=companion_store,
            token_secret=b"control-academic-revision",
        )
        runtime.companion_mind = companion_mind
        context = CompanionSubjectContext(
            owner_user_id=user.id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
            speaker_identity="confirmed",
            academic_stage="freshman",
            persistence_allowed=True,
        )
        prepared = runtime.companion_mind.prepare_turn(
            CompanionTurnRequest(
                turn_id="turn-control-academic-initial",
                subject=context,
                request_digest="digest-control-academic-initial",
                surface="voice",
                occurred_at="2026-09-01T08:00:00+08:00",
            )
        )
        runtime.companion_mind.commit_turn(
            prepared,
            CompanionTurnOutcome(
                visible_response="收到。",
                assistant_action="reply",
                delivery_status="delivered",
            ),
        )

        request = _miniprogram_request(
            "PATCH",
            "/api/miniprogram/profile",
            session["token"],
            {
                "grade": "大三",
                "academicStatus": "active",
                "transitionKind": "skip_advance",
                "effectiveAt": "2027-09-01T08:00:00+08:00",
            },
        )
        response = await handler.handle_miniprogram_profile_update(request)
        state = companion_store.get_academic_state(
            owner_user_id=user.id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
        )
        transitions = companion_store.list_academic_transitions(
            owner_user_id=user.id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
        )
        nickname_response = await handler.handle_miniprogram_profile_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/profile",
                session["token"],
                {"nickname": "Liu updated"},
            )
        )
        transitions_after_nickname = companion_store.list_academic_transitions(
            owner_user_id=user.id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
        )

        class FailingMind:
            def apply_control(self, _command):
                raise RuntimeError("companion unavailable")

        senior_payload = {
            "grade": "大四",
            "academicStatus": "active",
            "transitionKind": "advance",
            "effectiveAt": "2028-09-01T08:00:00+08:00",
        }
        runtime.companion_mind = FailingMind()
        failed_response = await handler.handle_miniprogram_profile_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/profile",
                session["token"],
                senior_payload,
            )
        )
        runtime.companion_mind = companion_mind
        retry_response = await handler.handle_miniprogram_profile_update(
            _miniprogram_request(
                "PATCH",
                "/api/miniprogram/profile",
                session["token"],
                senior_payload,
            )
        )
        retried_state = companion_store.get_academic_state(
            owner_user_id=user.id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
        )
        return (
            response.status,
            json.loads(response.text),
            state,
            transitions,
            json.loads(nickname_response.text),
            transitions_after_nickname,
            failed_response.status,
            retry_response.status,
            retried_state,
        )

    (
        status,
        body,
        state,
        transitions,
        nickname_body,
        transitions_after_nickname,
        failed_status,
        retry_status,
        retried_state,
    ) = asyncio.run(scenario())

    assert status == 200
    assert body["profile"]["academic_status"] == "active"
    assert body["profile"]["revision"] == 3
    assert state is not None
    assert state["academic_stage"] == "junior"
    assert state["source_revision"] == 3
    assert transitions[-1]["transition_kind"] == "skip_advance"
    assert transitions[-1]["effective_at"] == "2027-09-01T08:00:00+08:00"
    assert nickname_body["profile"]["revision"] == 3
    assert len(transitions_after_nickname) == len(transitions)
    assert failed_status == 503
    assert retry_status == 200
    assert retried_state is not None
    assert retried_state["academic_stage"] == "senior"
    assert retried_state["source_revision"] == 4


def test_miniprogram_can_activation_bind_and_unbind_owned_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        runtime.registry.register_connection("device-mp-1", object(), "websocket")
        _status, session = await _miniprogram_session(handler)

        bind_response = await _bind_miniprogram_device_by_activation(
            handler,
            runtime,
            session["token"],
            "device-mp-1",
        )

        get_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/device",
            session["token"],
        )
        get_response = await handler.handle_miniprogram_device(get_request)

        unbind_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/device/unbind",
            session["token"],
            {"device_id": "device-mp-1"},
        )
        unbind_response = await handler.handle_miniprogram_device_unbind(unbind_request)

        after_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/device",
            session["token"],
        )
        after_response = await handler.handle_miniprogram_device(after_request)
        return (
            bind_response.status,
            json.loads(bind_response.text),
            get_response.status,
            json.loads(get_response.text),
            unbind_response.status,
            json.loads(unbind_response.text),
            after_response.status,
            json.loads(after_response.text),
        )

    (
        bind_status,
        bind_body,
        get_status,
        get_body,
        unbind_status,
        unbind_body,
        after_status,
        after_body,
    ) = asyncio.run(scenario())

    assert bind_status == 200
    assert bind_body["device"]["device_id"] == "device-mp-1"
    assert bind_body["device"]["owner_user_id"] is not None
    assert get_status == 200
    assert get_body["device"]["state"] == "connected"
    assert get_body["device"]["batteryLevel"] is None
    assert get_body["device"]["batteryPercent"] is None
    assert unbind_status == 200
    assert unbind_body["success"] is True
    assert after_status == 200
    assert after_body["device"]["bound"] is False


def test_miniprogram_device_bind_requires_activation_code(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        runtime.identity_store.upsert_seen_device("prebound-device", "Prebound")
        _status, session = await _miniprogram_session(handler)

        bind_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/device/bind",
            session["token"],
            {"device_id": "prebound-device", "display_name": "Mine"},
        )
        bind_response = await handler.handle_miniprogram_device_bind(bind_request)
        device = runtime.identity_store.get_device_by_device_id("prebound-device")
        return bind_response.status, json.loads(bind_response.text), device

    status, body, device = asyncio.run(scenario())

    assert status == 403
    assert body == {"success": False, "message": "activation_required"}
    assert device is not None
    assert device.owner_user_id is None


def test_miniprogram_device_bind_rejects_unsafe_device_id(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)

        bind_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/device/bind",
            session["token"],
            {"device_id": "tenant/escape", "display_name": "Mine"},
        )
        response = await handler.handle_miniprogram_device_bind(bind_request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 400
    assert body == {"success": False, "message": "invalid device_id"}


def test_miniprogram_device_payload_exposes_registry_battery_and_firmware(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.registry.update_device_telemetry(
            "device-mp-zero",
            battery_level=0,
            battery_percent=0,
            firmware_version="0.1.2",
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        runtime.identity_store.upsert_seen_device("device-mp-zero", "桌面小芯")
        _status, session = await _miniprogram_session(handler)

        await _bind_miniprogram_device_by_activation(
            handler,
            runtime,
            session["token"],
            "device-mp-zero",
        )

        get_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/device",
            session["token"],
        )
        get_response = await handler.handle_miniprogram_device(get_request)
        return get_response.status, json.loads(get_response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["device"]["batteryLevel"] == 0
    assert body["device"]["batteryPercent"] == 0
    assert body["device"]["firmwareVersion"] == "0.1.2"


def test_miniprogram_diagnostics_requires_session(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        request = _request("GET", "/api/miniprogram/diagnostics")
        response = await handler.handle_miniprogram_diagnostics(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 401
    assert body == {"success": False, "message": "login required"}


def test_miniprogram_diagnostics_reports_core_endpoint_checks(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.registry = BatteryRegistry()
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        runtime.identity_store.upsert_seen_device("device-mp-zero", "Desk XiaoXin")
        _status, session = await _miniprogram_session(handler, openid="wx-diagnostics")

        await _bind_miniprogram_device_by_activation(
            handler,
            runtime,
            session["token"],
            "device-mp-zero",
            "Desk XiaoXin",
        )

        course_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/courses",
            session["token"],
            {
                "title": "Diagnostics Course",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-16",
                "startsAt": "08:00",
            },
        )
        await handler.handle_miniprogram_course_create(course_request)

        request = _miniprogram_request(
            "GET",
            "/api/miniprogram/diagnostics?date=2025-09-01",
            session["token"],
        )
        response = await handler.handle_miniprogram_diagnostics(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["success"] is True
    diagnostics = body["diagnostics"]
    checks = {check["name"]: check for check in diagnostics["checks"]}
    assert diagnostics["overallStatus"] == "ok"
    assert diagnostics["summary"] == {"ok": 6, "warning": 0, "error": 0}
    assert set(checks) == {
        "session",
        "profile",
        "device",
        "semester",
        "courses",
        "curriculumOverview",
    }
    assert checks["session"]["status"] == "ok"
    assert checks["session"]["details"]["openid"] == "wx-diagnostics"
    assert checks["profile"]["details"]["profileExists"] is True
    assert checks["device"]["details"]["bound"] is True
    assert checks["semester"]["details"]["startDate"] == "2025-09-01"
    assert checks["courses"]["details"]["count"] == 1
    assert checks["curriculumOverview"]["details"]["todayCourseCount"] == 1


def test_miniprogram_diagnostics_warns_when_device_is_unbound(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)
        request = _miniprogram_request(
            "GET",
            "/api/miniprogram/diagnostics?date=2025-09-01",
            session["token"],
        )
        response = await handler.handle_miniprogram_diagnostics(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    diagnostics = body["diagnostics"]
    checks = {check["name"]: check for check in diagnostics["checks"]}
    assert diagnostics["overallStatus"] == "warning"
    assert diagnostics["summary"]["warning"] == 1
    assert diagnostics["summary"]["error"] == 0
    assert checks["device"]["status"] == "warning"
    assert checks["device"]["message"] == "no bound device"


def test_miniprogram_diagnostics_reports_curriculum_business_error(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)
        request = _miniprogram_request(
            "GET",
            "/api/miniprogram/diagnostics?date=not-a-date",
            session["token"],
        )
        response = await handler.handle_miniprogram_diagnostics(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    diagnostics = body["diagnostics"]
    checks = {check["name"]: check for check in diagnostics["checks"]}
    assert diagnostics["overallStatus"] == "error"
    assert checks["curriculumOverview"]["status"] == "error"
    assert checks["curriculumOverview"]["message"] == "time data 'not-a-date' does not match format '%Y-%m-%d'"


def test_miniprogram_curriculum_persists_semester_courses_and_overview(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)

        semester_request = _miniprogram_request(
            "PATCH",
            "/api/miniprogram/semester",
            session["token"],
            {
                "label": "2026 春季学期",
                "startDate": "2026-03-02",
                "totalWeeks": 18,
            },
        )
        semester_response = await handler.handle_miniprogram_semester_update(
            semester_request
        )

        create_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/courses",
            session["token"],
            {
                "title": "高等数学",
                "classroom": "三教 204",
                "teacher": "王老师",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "第1-16周",
            },
        )
        create_response = await handler.handle_miniprogram_course_create(
            create_request
        )
        created = json.loads(create_response.text)["course"]

        list_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/courses",
            session["token"],
        )
        list_response = await handler.handle_miniprogram_courses(list_request)

        get_request = _miniprogram_request(
            "GET",
            f"/api/miniprogram/courses/{created['id']}",
            session["token"],
            match_info={"course_id": created["id"]},
        )
        get_response = await handler.handle_miniprogram_course(get_request)

        update_request = _miniprogram_request(
            "PATCH",
            f"/api/miniprogram/courses/{created['id']}",
            session["token"],
            {
                "title": "线性代数",
                "classroom": "教七-1",
                "teacher": "李老师",
                "weekday": 2,
                "startSection": 3,
                "endSection": 4,
                "weekRange": "第2-18周",
            },
            match_info={"course_id": created["id"]},
        )
        update_response = await handler.handle_miniprogram_course_update(
            update_request
        )

        overview_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/curriculum/overview?date=2026-03-10",
            session["token"],
        )
        overview_response = await handler.handle_miniprogram_curriculum_overview(
            overview_request
        )

        delete_request = _miniprogram_request(
            "DELETE",
            f"/api/miniprogram/courses/{created['id']}",
            session["token"],
            match_info={"course_id": created["id"]},
        )
        delete_response = await handler.handle_miniprogram_course_delete(
            delete_request
        )

        after_response = await handler.handle_miniprogram_courses(list_request)
        return {
            "semester": json.loads(semester_response.text),
            "created": created,
            "list": json.loads(list_response.text),
            "get": json.loads(get_response.text),
            "updated": json.loads(update_response.text),
            "overview": json.loads(overview_response.text),
            "deleted": json.loads(delete_response.text),
            "after": json.loads(after_response.text),
        }

    result = asyncio.run(scenario())

    assert result["semester"]["semester"]["startDate"] == "2026-03-02"
    assert result["semester"]["semester"]["totalWeeks"] == 18
    assert result["created"]["title"] == "高等数学"
    assert result["list"]["courses"][0]["id"] == result["created"]["id"]
    assert result["get"]["course"]["classroom"] == "三教 204"
    assert result["updated"]["course"]["title"] == "线性代数"
    assert result["updated"]["course"]["weekday"] == 2
    assert result["overview"]["overview"]["currentWeek"] == 2
    assert result["overview"]["overview"]["todayCourses"][0]["title"] == "线性代数"
    assert result["overview"]["overview"]["nextCourse"]["classroom"] == "教七-1"
    assert result["deleted"]["deleted"] is True
    assert result["after"]["courses"] == []


def test_miniprogram_overview_uses_personal_pet_lifecycle_for_companion_age_and_curriculum(
    tmp_path,
):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.registry = BatteryRegistry()
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        runtime.identity_store.upsert_seen_device("device-mp-zero", "桌面小芯")
        _status, session = await _miniprogram_session(handler)
        owner_user, _owner_profile = runtime.identity_store.get_or_create_student_by_openid(
            "wx-openid-1"
        )

        await _bind_miniprogram_device_by_activation(
            handler,
            runtime,
            session["token"],
            "device-mp-zero",
        )
        with runtime.identity_store._connect() as conn:
            conn.execute(
                "UPDATE devices SET bound_at = ? WHERE device_id = ?",
                ("2026-03-10T09:30:00+08:00", "device-mp-zero"),
            )
            conn.execute(
                """
                UPDATE personal_pets
                SET status = 'active',
                    companion_started_at = ?,
                    started_at_source = 'first_device_bind',
                    updated_at = ?
                WHERE owner_user_id = ?
                """,
                (
                    "2026-03-08T09:30:00+08:00",
                    "2026-03-08T09:30:00+08:00",
                    owner_user.id,
                ),
            )
        with runtime.activation_store._connect() as conn:
            consumed_session = conn.execute(
                """
                SELECT id
                FROM device_activation_codes
                WHERE device_id = ?
                  AND consumed_at IS NOT NULL
                ORDER BY consumed_at DESC, created_at DESC
                LIMIT 1
                """,
                ("device-mp-zero",),
            ).fetchone()
            assert consumed_session is not None
            conn.execute(
                "UPDATE device_activation_codes SET consumed_at = ? WHERE id = ?",
                ("2026-03-10T09:30:00+08:00", consumed_session["id"]),
            )

        semester_request = _miniprogram_request(
            "PATCH",
            "/api/miniprogram/semester",
            session["token"],
            {
                "label": "2026 春季学期",
                "startDate": "2026-03-02",
                "totalWeeks": 18,
            },
        )
        await handler.handle_miniprogram_semester_update(semester_request)

        course_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/courses",
            session["token"],
            {
                "title": "线性代数",
                "classroom": "教七-1",
                "teacher": "李老师",
                "weekday": 2,
                "startSection": 3,
                "endSection": 4,
                "weekRange": "第1-18周",
                "startsAt": "10:10",
                "endsAt": "11:45",
            },
        )
        await handler.handle_miniprogram_course_create(course_request)

        todo_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/todos",
            session["token"],
            {
                "title": "submit report",
                "dueAt": "2026-03-10T20:30:00+08:00",
                "notes": "",
            },
        )
        await handler.handle_miniprogram_todo_create(todo_request)
        notification_response = await _post_control_event(
            handler,
            session["token"],
            "device-mp-zero",
        )
        notification_id = json.loads(notification_response.text)["delivery_id"]
        runtime.store.transition(notification_id, XiaoxinDeliveryState.DONE)

        overview_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/overview?date=2026-03-10",
            session["token"],
        )
        overview_response = await handler.handle_miniprogram_overview(overview_request)
        return overview_response.status, json.loads(overview_response.text), notification_id

    status, body, notification_id = asyncio.run(scenario())

    assert status == 200
    overview = body["overview"]
    assert overview["device"]["bound"] is True
    assert overview["device"]["batteryLevel"] == 0
    assert overview["device"]["batteryPercent"] == 0
    assert overview["device"]["firmwareVersion"] == "0.1.0"
    assert overview["course"]["title"] == "线性代数 10:10"
    assert overview["course"]["detail"] == "教七-1 · 第3-4节"
    assert overview["weather"]["configured"] is False
    assert overview["todo"]["count"] == 1
    assert overview["petStatus"]["petId"].startswith("pet_")
    assert overview["petStatus"]["lifecycleStatus"] == "active"
    assert overview["petStatus"]["companionStartedAt"] == "2026-03-08T09:30:00+08:00"
    assert overview["petStatus"]["companionDays"] == 3
    assert overview["petStatus"]["companionYear"] == 1
    assert overview["todaySummary"]["courseCount"] == 1
    assert overview["todaySummary"]["reminderCount"] == 1
    assert overview["todaySummary"]["latestNotificationState"] == "最新通知已播报"
    assert overview["latestNotification"]["deliveryId"] == notification_id
    assert overview["latestNotification"]["title"] == "Reminder"
    assert overview["latestNotification"]["status"] == "announced"
    assert "高等数学" not in json.dumps(overview, ensure_ascii=False)


def test_miniprogram_curriculum_overview_reports_course_conflicts(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)

        semester_request = _miniprogram_request(
            "PATCH",
            "/api/miniprogram/semester",
            session["token"],
            {
                "label": "2026 spring",
                "startDate": "2026-03-02",
                "totalWeeks": 18,
            },
        )
        await handler.handle_miniprogram_semester_update(semester_request)

        for payload in (
            {
                "title": "Linear Algebra",
                "classroom": "Room 201",
                "weekday": 2,
                "startSection": 3,
                "endSection": 4,
                "weekRange": "1-18",
                "startsAt": "10:10",
            },
            {
                "title": "Physics",
                "classroom": "Room 202",
                "weekday": 2,
                "startSection": 4,
                "endSection": 5,
                "weekRange": "1—18",
                "startsAt": "10:55",
            },
            {
                "title": "English",
                "classroom": "Room 203",
                "weekday": 2,
                "startSection": 6,
                "endSection": 7,
                "weekRange": "1-18",
                "startsAt": "14:00",
            },
            {
                "title": "Wrong Weekday",
                "classroom": "Room 204",
                "weekday": 3,
                "startSection": 3,
                "endSection": 4,
                "weekRange": "1-18",
                "startsAt": "10:10",
            },
            {
                "title": "Wrong Week",
                "classroom": "Room 205",
                "weekday": 2,
                "startSection": 3,
                "endSection": 4,
                "weekRange": "3-18",
                "startsAt": "10:10",
            },
        ):
            create_request = _miniprogram_request(
                "POST",
                "/api/miniprogram/courses",
                session["token"],
                payload,
            )
            await handler.handle_miniprogram_course_create(create_request)

        overview_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/curriculum/overview?date=2026-03-10",
            session["token"],
        )
        overview_response = await handler.handle_miniprogram_curriculum_overview(
            overview_request
        )
        return json.loads(overview_response.text)["overview"]

    overview = asyncio.run(scenario())

    courses = {course["title"]: course for course in overview["todayCourses"]}
    assert overview["conflictCount"] == 1
    assert "Wrong Weekday" not in courses
    assert "Wrong Week" not in courses
    assert courses["Linear Algebra"]["conflictCount"] == 1
    assert courses["Physics"]["conflictCount"] == 1
    assert courses["English"]["conflictCount"] == 0
    assert courses["Physics"]["id"] in courses["Linear Algebra"]["conflictCourseIds"]
    assert courses["Linear Algebra"]["id"] in courses["Physics"]["conflictCourseIds"]
    assert courses["English"]["conflictCourseIds"] == []


def test_miniprogram_todos_crud_and_overview_summary(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)

        create_early = _miniprogram_request(
            "POST",
            "/api/miniprogram/todos",
            session["token"],
            {
                "title": "带学生证",
                "dueAt": "2026-03-10T08:00:00+08:00",
                "notes": "进实验楼",
            },
        )
        early_response = await handler.handle_miniprogram_todo_create(create_early)
        early = json.loads(early_response.text)["todo"]
        assert early["source"] == "miniprogram"
        assert early["sourceDeviceId"] == ""

        create_late = _miniprogram_request(
            "POST",
            "/api/miniprogram/todos",
            session["token"],
            {
                "title": "交实验报告",
                "dueAt": "2026-03-10T20:00:00+08:00",
            },
        )
        late_response = await handler.handle_miniprogram_todo_create(create_late)
        late = json.loads(late_response.text)["todo"]

        update_request = _miniprogram_request(
            "PATCH",
            f"/api/miniprogram/todos/{late['id']}",
            session["token"],
            {"status": "done"},
            match_info={"todo_id": late["id"]},
        )
        update_response = await handler.handle_miniprogram_todo_update(update_request)

        list_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/todos",
            session["token"],
        )
        list_response = await handler.handle_miniprogram_todos(list_request)

        overview_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/overview?date=2026-03-10",
            session["token"],
        )
        overview_response = await handler.handle_miniprogram_overview(overview_request)

        delete_request = _miniprogram_request(
            "DELETE",
            f"/api/miniprogram/todos/{early['id']}",
            session["token"],
            match_info={"todo_id": early["id"]},
        )
        delete_response = await handler.handle_miniprogram_todo_delete(delete_request)
        after_response = await handler.handle_miniprogram_todos(list_request)

        return {
            "early_status": early_response.status,
            "early": early,
            "updated": json.loads(update_response.text),
            "list": json.loads(list_response.text),
            "overview": json.loads(overview_response.text),
            "deleted": json.loads(delete_response.text),
            "after": json.loads(after_response.text),
        }

    result = asyncio.run(scenario())

    assert result["early_status"] == 200
    assert result["early"]["title"] == "带学生证"
    assert result["early"]["dueAt"] == "2026-03-10T08:00:00+08:00"
    assert result["early"]["status"] == "pending"
    assert result["updated"]["todo"]["status"] == "done"
    assert [todo["title"] for todo in result["list"]["todos"]] == [
        "带学生证",
        "交实验报告",
    ]
    todo_card = result["overview"]["overview"]["todo"]
    assert todo_card["configured"] is True
    assert todo_card["count"] == 1
    assert todo_card["detail"] == "08:00 带学生证"
    assert todo_card["nextTodo"]["id"] == result["early"]["id"]
    assert result["deleted"]["deleted"] is True
    assert [todo["id"] for todo in result["after"]["todos"]] == [
        result["updated"]["todo"]["id"]
    ]


def test_miniprogram_todo_create_rejects_non_object_json_and_invalid_due_at(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)

        non_object = _miniprogram_request(
            "POST",
            "/api/miniprogram/todos",
            session["token"],
        )
        non_object._read_bytes = b"[]"
        non_object_response = await handler.handle_miniprogram_todo_create(non_object)

        invalid_due_at = _miniprogram_request(
            "POST",
            "/api/miniprogram/todos",
            session["token"],
            {
                "title": "格式错误",
                "dueAt": "tomorrow",
            },
        )
        invalid_due_at_response = await handler.handle_miniprogram_todo_create(
            invalid_due_at
        )

        return (
            non_object_response.status,
            json.loads(non_object_response.text),
            invalid_due_at_response.status,
            json.loads(invalid_due_at_response.text),
        )

    non_object_status, non_object_body, invalid_status, invalid_body = asyncio.run(
        scenario()
    )

    assert non_object_status == 400
    assert non_object_body["message"] == "json object required"
    assert invalid_status == 400
    assert invalid_body["message"].startswith("dueAt must use")


def test_control_console_can_sync_real_overview_to_owned_connected_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")
        conn = OverviewConnection()
        runtime.registry.register_connection("device-a", conn, "websocket")

        semester_request = _request(
            "PATCH",
            "/api/miniprogram/semester",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        semester_request._read_bytes = _json_body(
            {"startDate": "2026-03-02", "totalWeeks": 18}
        )
        await handler.handle_miniprogram_semester_update(semester_request)

        course_request = _request(
            "POST",
            "/api/miniprogram/courses",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        course_request._read_bytes = _json_body(
            {
                "title": "线性代数",
                "classroom": "教七-1",
                "weekday": 2,
                "startSection": 3,
                "endSection": 4,
                "weekRange": "第1-18周",
                "startsAt": "10:10",
            }
        )
        await handler.handle_miniprogram_course_create(course_request)

        todo_request = _request(
            "POST",
            "/api/miniprogram/todos",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        todo_request._read_bytes = _json_body(
            {
                "title": "带学生证",
                "dueAt": "2026-03-10T08:00:00+08:00",
            }
        )
        await handler.handle_miniprogram_todo_create(todo_request)

        request = _request(
            "POST",
            "/api/xiaoxin/devices/device-a/overview-sync?date=2026-03-10",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"device_id": "device-a"},
        )
        response = await handler.handle_sync_device_overview(request)
        return response.status, json.loads(response.text), conn.sent

    status, body, sent = asyncio.run(scenario())

    assert status == 200
    assert body["success"] is True
    assert body["device_id"] == "device-a"
    assert len(sent) == 1
    assert sent[0]["type"] == "xiaoxin_overview_update"
    assert sent[0]["course"]["title"] == "线性代数 10:10"
    assert sent[0]["todo"]["detail"] == "08:00 带学生证"
    assert sent[0]["overview"]["device"]["deviceId"] == "device-a"


def test_miniprogram_courses_are_isolated_by_student_account(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status_a, session_a = await _miniprogram_session(handler, openid="openid-a")
        _status_b, session_b = await _miniprogram_session(handler, openid="openid-b")

        create_request = _miniprogram_request(
            "POST",
            "/api/miniprogram/courses",
            session_a["token"],
            {
                "title": "大学英语",
                "classroom": "一教 101",
                "teacher": "陈老师",
                "weekday": 3,
                "startSection": 5,
                "endSection": 6,
                "weekRange": "第1-16周",
            },
        )
        await handler.handle_miniprogram_course_create(create_request)

        list_request_b = _miniprogram_request(
            "GET",
            "/api/miniprogram/courses",
            session_b["token"],
        )
        response_b = await handler.handle_miniprogram_courses(list_request_b)
        return response_b.status, json.loads(response_b.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["courses"] == []


def test_post_event_returns_created_delivery_for_owned_bound_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")
        request = _request(
            "POST",
            "/api/xiaoxin/events",
            headers={"Content-Type": "application/json"},
            payload=None,
        )
        request._read_bytes = json.dumps(
            {
                "device_id": "aa",
                "event": "notification",
                "title": "提醒",
                "body": "内容",
            }
        ).encode("utf-8")
        response = await _post_control_event(handler, token, "device-a")
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["delivery_id"].startswith("del_")
    assert body["state"] == "created"


def test_post_event_reports_stopped_dispatcher_as_service_unavailable(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")

        async def reject_submit(request):
            raise DispatcherStoppedError("internal shutdown detail")

        runtime.dispatcher.submit = reject_submit
        response = await _post_control_event(handler, token, "device-a")
        return response.status, json.loads(response.text), runtime.store.list_recent()

    status, body, records = asyncio.run(scenario())
    assert status == 503
    assert body == {
        "success": False,
        "message": "notification dispatcher is stopped",
    }
    assert records == []


def test_post_event_allows_admin_to_dispatch_other_users_bound_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _alice, alice_token = runtime.auth_service.register(
            "alice", "secret-pass", "Alice", role="admin"
        )
        bob, _bob_token = runtime.auth_service.register("bob", "secret-pass", "Bob")
        runtime.identity_store.upsert_seen_device("device-b", "Device B")
        runtime.identity_store.bind_device("device-b", bob.id, "Device B")

        response = await _post_control_event(handler, alice_token, "device-b")
        return response.status, json.loads(response.text), runtime.dispatcher.submitted

    status, body, submitted = asyncio.run(scenario())

    assert status == 200
    assert body["delivery_id"].startswith("del_")
    assert body["state"] == "created"
    assert len(submitted) == 1
    assert submitted[0].device_id == "device-b"


def test_post_event_allows_admin_to_dispatch_seen_unbound_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _alice, alice_token = runtime.auth_service.register(
            "alice", "secret-pass", "Alice", role="admin"
        )
        runtime.identity_store.upsert_seen_device("device-unbound", "Unbound Device")

        response = await _post_control_event(handler, alice_token, "device-unbound")
        return response.status, json.loads(response.text), runtime.dispatcher.submitted

    status, body, submitted = asyncio.run(scenario())

    assert status == 200
    assert body["delivery_id"].startswith("del_")
    assert body["state"] == "created"
    assert len(submitted) == 1
    assert submitted[0].device_id == "device-unbound"


def test_demo_data_endpoint_returns_defaults_for_authenticated_user(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"demo_data_path": str(tmp_path / "demo-data.json")}},
            runtime,
        )
        _user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        request = _request(
            "GET",
            "/api/xiaoxin/demo-data",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        response = await handler.handle_demo_data(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["overview"]["weather"]["summary"] == "多云 26°C"
    assert body["overview"]["course"]["title"] == "高等数学 10:10"
    assert body["overview"]["todo"]["count"] == 2
    assert body["notifications"][0]["id"] == "course-reminder-demo"
    assert body["notifications"][0]["event"] == "course_reminder"
    assert body["notifications"][0]["body"] == "高等数学 10:10 3教204"
    assert "分钟后" not in body["notifications"][0]["speak_text"]
    assert [notification["id"] for notification in body["notifications"]] == [
        "course-reminder-demo",
        "todo-reminder-demo",
        "network-demo",
        "battery-demo",
        "system-update-demo",
    ]


def test_put_demo_data_persists_overview_and_notifications(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        demo_path = tmp_path / "demo-data.json"
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"demo_data_path": str(demo_path)}},
            runtime,
        )
        _user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        request = _request(
            "PUT",
            "/api/xiaoxin/demo-data",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
        )
        request._read_bytes = json.dumps(
            {
                "overview": {
                    "weather": {
                        "configured": True,
                        "available": True,
                        "summary": "晴 28°C",
                        "detail": "湿度 50%",
                    },
                    "course": {
                        "configured": True,
                        "available_today": True,
                        "title": "线性代数 14:00",
                        "detail": "2教101",
                    },
                    "todo": {
                        "configured": True,
                        "count": 3,
                        "detail": "实验报告",
                    },
                },
                "notifications": [
                    {
                        "id": "todo-demo",
                        "event": "todo_reminder",
                        "title": "待办提醒",
                        "body": "提交实验报告",
                        "tag": "待办",
                        "priority": 2,
                        "ttl_ms": 0,
                        "speak": False,
                        "speak_text": "",
                        "todo_title": "实验报告",
                        "due_at": "2026-07-05T18:00:00+08:00",
                    }
                ],
            }
        ).encode("utf-8")
        put_response = await handler.handle_save_demo_data(request)

        get_request = _request(
            "GET",
            "/api/xiaoxin/demo-data",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        get_response = await handler.handle_demo_data(get_request)
        return (
            put_response.status,
            json.loads(put_response.text),
            get_response.status,
            json.loads(get_response.text),
            json.loads(demo_path.read_text(encoding="utf-8")),
        )

    put_status, put_body, get_status, get_body, stored = asyncio.run(scenario())

    assert put_status == 200
    assert put_body["success"] is True
    assert get_status == 200
    assert get_body["overview"]["weather"]["summary"] == "晴 28°C"
    assert get_body["notifications"][0]["id"] == "todo-demo"
    assert stored["notifications"][0]["event"] == "todo_reminder"


def test_send_demo_overview_pushes_demo_data_to_owned_connected_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        demo_path = tmp_path / "demo-data.json"
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"demo_data_path": str(demo_path)}},
            runtime,
        )
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")
        conn = OverviewConnection()
        runtime.registry.register_connection("device-a", conn, "websocket")

        save_request = _request(
            "PUT",
            "/api/xiaoxin/demo-data",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
        )
        save_request._read_bytes = json.dumps(
            {
                "overview": {
                    "weather": {
                        "configured": True,
                        "available": True,
                        "summary": "晴 28°C",
                        "detail": "湿度 50%",
                    },
                    "course": {
                        "configured": True,
                        "available_today": True,
                        "title": "线性代数 14:00",
                        "detail": "2教 201",
                    },
                    "todo": {
                        "configured": True,
                        "count": 3,
                        "detail": "实验报告",
                    },
                },
                "notifications": [
                    {
                        "id": "network-demo",
                        "event": "notification",
                        "title": "校园网状态",
                        "body": "宿舍区 Wi-Fi 已恢复",
                        "tag": "网络",
                        "priority": 2,
                        "ttl_ms": 0,
                        "speak": False,
                        "speak_text": "",
                    }
                ],
            }
        ).encode("utf-8")
        await handler.handle_save_demo_data(save_request)

        request = _request(
            "POST",
            "/api/xiaoxin/demo-data/overview/send",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
        )
        request._read_bytes = json.dumps({"device_id": "device-a"}).encode("utf-8")
        response = await handler.handle_send_demo_overview(request)
        return response.status, json.loads(response.text), conn.sent

    status, body, sent = asyncio.run(scenario())

    assert status == 200
    assert body["success"] is True
    assert len(sent) == 1
    assert sent[0]["type"] == "xiaoxin_overview_update"
    assert sent[0]["weather"]["summary"] == "晴 28°C"
    assert sent[0]["course"]["title"] == "线性代数 14:00"
    assert sent[0]["todo"]["count"] == 3
    assert sent[0]["notifications"][0]["id"] == "network-demo"
    assert sent[0]["notifications"][0]["tag"] == "网络"
    assert sent[0]["overview"]["source"] == "demo"


def test_send_demo_notification_dispatches_to_owned_bound_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"demo_data_path": str(tmp_path / "demo-data.json")}},
            runtime,
        )
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")

        request = _request(
            "POST",
            "/api/xiaoxin/demo-data/notifications/course-reminder-demo/send",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
            match_info={"notification_id": "course-reminder-demo"},
        )
        request._read_bytes = json.dumps({"device_id": "device-a"}).encode("utf-8")
        response = await handler.handle_send_demo_notification(request)
        return response.status, json.loads(response.text), runtime.dispatcher.submitted

    status, body, submitted = asyncio.run(scenario())

    assert status == 200
    assert body["delivery_id"].startswith("del_")
    assert body["state"] == "created"
    assert len(submitted) == 1
    assert submitted[0].device_id == "device-a"
    assert submitted[0].event.value == "course_reminder"
    assert submitted[0].title == "上课提醒"


def test_send_demo_notification_reports_stopped_dispatcher(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"demo_data_path": str(tmp_path / "demo-data.json")}},
            runtime,
        )
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")

        async def reject_submit(request):
            raise DispatcherStoppedError("internal shutdown detail")

        runtime.dispatcher.submit = reject_submit
        request = _request(
            "POST",
            "/api/xiaoxin/demo-data/notifications/course-reminder-demo/send",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
            match_info={"notification_id": "course-reminder-demo"},
        )
        request._read_bytes = json.dumps({"device_id": "device-a"}).encode("utf-8")
        response = await handler.handle_send_demo_notification(request)
        return response.status, json.loads(response.text), runtime.store.list_recent()

    status, body, records = asyncio.run(scenario())
    assert status == 503
    assert body == {
        "success": False,
        "message": "notification dispatcher is stopped",
    }
    assert records == []


def test_post_event_does_not_mislabel_unrelated_runtime_error(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")

        async def reject_submit(request):
            raise RuntimeError("unrelated internal failure")

        runtime.dispatcher.submit = reject_submit
        with pytest.raises(RuntimeError, match="unrelated internal failure"):
            await _post_control_event(handler, token, "device-a")

    asyncio.run(scenario())


def test_send_demo_notification_rejects_unknown_notification(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"demo_data_path": str(tmp_path / "demo-data.json")}},
            runtime,
        )
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")
        request = _request(
            "POST",
            "/api/xiaoxin/demo-data/notifications/missing/send",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
            match_info={"notification_id": "missing"},
        )
        request._read_bytes = json.dumps({"device_id": "device-a"}).encode("utf-8")
        response = await handler.handle_send_demo_notification(request)
        return response.status, json.loads(response.text), runtime.dispatcher.submitted

    status, body, submitted = asyncio.run(scenario())

    assert status == 404
    assert body == {"success": False, "message": "notification not found"}
    assert submitted == []


def test_logged_in_user_can_bind_device_by_activation_code(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-1", "Device 1")
        session = runtime.activation_store.create_or_refresh_activation("device-1")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/activation-bind",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        request._read_bytes = json.dumps(
            {"code": session.code, "display_name": "桌面小新"}
        ).encode("utf-8")
        response = await handler.handle_activation_bind_device(request)
        return response.status, json.loads(response.text), runtime, user, session

    status, body, runtime, user, session = asyncio.run(scenario())

    assert status == 200
    assert body["success"] is True
    assert body["device"]["device_id"] == "device-1"
    assert body["device"]["owner_user_id"] == user.id
    assert runtime.activation_store.get_activation_by_code(session.code).consumed_at


def test_activation_bind_rejects_unknown_code(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/activation-bind",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        request._read_bytes = json.dumps({"code": "123456"}).encode("utf-8")
        response = await handler.handle_activation_bind_device(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 404
    assert body == {"success": False, "message": "activation code not found"}


def test_activation_bind_rejects_expired_code(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        session = runtime.activation_store.create_or_refresh_activation(
            "device-1", ttl_seconds=-1
        )
        request = _request(
            "POST",
            "/api/xiaoxin/devices/activation-bind",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        request._read_bytes = json.dumps({"code": session.code}).encode("utf-8")
        response = await handler.handle_activation_bind_device(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 410
    assert body == {"success": False, "message": "activation code expired"}


def test_activation_bind_rejects_consumed_code(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-1", "Device 1")
        session = runtime.activation_store.create_or_refresh_activation("device-1")

        first_request = _request(
            "POST",
            "/api/xiaoxin/devices/activation-bind",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        first_request._read_bytes = json.dumps({"code": session.code}).encode("utf-8")
        first_response = await handler.handle_activation_bind_device(first_request)

        second_request = _request(
            "POST",
            "/api/xiaoxin/devices/activation-bind",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        second_request._read_bytes = json.dumps({"code": session.code}).encode("utf-8")
        second_response = await handler.handle_activation_bind_device(second_request)
        return (
            first_response.status,
            json.loads(first_response.text),
            second_response.status,
            json.loads(second_response.text),
        )

    first_status, first_body, second_status, second_body = asyncio.run(scenario())

    assert first_status == 200
    assert first_body["success"] is True
    assert second_status == 404
    assert second_body == {"success": False, "message": "activation code not found"}


def test_activation_bind_rejects_code_already_bound_to_another_user(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        alice, _alice_token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        bob, bob_token = runtime.auth_service.register("bob", "secret-pass", "Bob")
        runtime.identity_store.upsert_seen_device("device-1", "Device 1")
        runtime.identity_store.bind_device("device-1", alice.id, "Alice Device")
        session = runtime.activation_store.create_or_refresh_activation("device-1")

        request = _request(
            "POST",
            "/api/xiaoxin/devices/activation-bind",
            headers={"Cookie": f"xiaoxin_session={bob_token}"},
        )
        request._read_bytes = json.dumps({"code": session.code}).encode("utf-8")
        response = await handler.handle_activation_bind_device(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 409
    assert body == {
        "success": False,
        "message": "device is already bound to another user",
    }


def test_activation_bind_rejects_empty_or_malformed_code_with_field(tmp_path):
    async def scenario(case_name, code_value):
        runtime = AuthRuntime(tmp_path / f"{case_name}.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/activation-bind",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        request._read_bytes = json.dumps({"code": code_value}).encode("utf-8")
        response = await handler.handle_activation_bind_device(request)
        return response.status, json.loads(response.text)

    cases = [("empty", ""), ("malformed", "12ab")]
    for case_name, code_value in cases:
        status, body = asyncio.run(scenario(case_name, code_value))
        assert status == 400
        assert body == {"success": False, "message": "code required", "field": "code"}


def test_configured_secret_does_not_gate_api_without_session(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"secret": "secret-1"}},
            runtime,
        )
        request = _request("GET", "/api/xiaoxin/devices")
        response = await handler.handle_devices(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 401
    assert body == {"success": False, "message": "login required"}


def test_console_allows_local_secret_bootstrap_without_header():
    async def scenario():
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"secret": "secret-1"}},
            FakeRuntime(),
        )
        request = _request("GET", "/xiaoxin/control/")
        response = await handler.handle_console(request)
        return response.status, response.text

    status, html = asyncio.run(scenario())

    assert status == 200
    assert "/api/xiaoxin/devices" in html
    assert "/api/xiaoxin/demo-data/overview/send" in html
    assert "syncDemoOverview" in html


def test_console_allows_public_navigation_to_auth_shell_when_secret_unset(tmp_path):
    async def scenario():
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {}},
            AuthRuntime(tmp_path / "xiaoxin_control.db"),
        )
        request = _request("GET", "/xiaoxin/control/", remote="121.43.33.0")
        response = await handler.handle_console(request)
        return response.status, response.content_type, response.text

    status, content_type, html = asyncio.run(scenario())

    assert status == 200
    assert content_type == "text/html"
    assert "/api/xiaoxin/auth/login" in html
    assert "/api/xiaoxin/auth/register" in html


def test_console_html_disables_browser_cache():
    async def scenario():
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, FakeRuntime())
        request = _request("GET", "/xiaoxin/control/")
        response = await handler.handle_console(request)
        return response.headers

    headers = asyncio.run(scenario())

    assert headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert headers["Pragma"] == "no-cache"
    assert headers["Expires"] == "0"


def test_console_allows_public_navigation_even_when_secret_is_configured(tmp_path):
    async def scenario():
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"secret": "secret-1"}},
            AuthRuntime(tmp_path / "xiaoxin_control.db"),
        )
        request = _request("GET", "/xiaoxin/control/", remote="10.0.0.9")
        response = await handler.handle_console(request)
        return response.status, response.content_type, response.text

    status, content_type, html = asyncio.run(scenario())

    assert status == 200
    assert content_type == "text/html"
    assert "/api/xiaoxin/auth/login" in html
    assert "/api/xiaoxin/auth/register" in html


def test_configured_secret_does_not_bypass_missing_auth_service():
    async def scenario():
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"secret": "secret-1"}},
            FakeRuntime(),
        )
        request = _request("GET", "/api/xiaoxin/devices")
        response = await handler.handle_devices(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 404
    assert body == {"success": False, "message": "auth unavailable"}


def test_static_console_contains_product_name_api_paths_without_secret_support():
    async def scenario():
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, FakeRuntime())
        request = _request("GET", "/xiaoxin/control/")
        response = await handler.handle_console(request)
        return response.text

    html = asyncio.run(scenario())

    assert "小芯调控控制台" in html
    assert "/api/xiaoxin/devices" in html
    assert "/api/xiaoxin/events" in html
    assert "/api/xiaoxin/auth/me" in html
    assert "/api/xiaoxin/auth/login" in html
    assert "/api/xiaoxin/auth/register" in html
    assert "/api/xiaoxin/legacy-memory" not in html
    assert "/api/xiaoxin/demo-data" in html
    assert "syncDemoOverview" in html
    assert "sendDemoNotification" not in html
    assert "/api/xiaoxin/devices/activation-bind" not in html
    assert "activationBindDevice" not in html
    assert "activationBindForm" not in html
    assert "绑定码" not in html
    assert "验证码绑定" not in html
    assert "/api/xiaoxin/devices/wake-by-id" in html
    assert "wakeByIdForm" in html
    assert "wakeDeviceById" in html
    assert "/api/xiaoxin/devices/manual-bind" not in html
    assert "wakeDevice" in html
    assert "旧版记忆" not in html
    assert "说话人" in html
    assert "记忆主体" in html
    assert "主体记忆" in html
    assert "V2 安全投影" in html
    assert "X-Xiaoxin-Control-Secret" not in html
    assert "secretInput" not in html
    assert "secretSaveBtn" not in html
    assert "localStorage.getItem(secretKey)" not in html
    assert "payload.evidence" in html
    assert "item.source_summary" in html
    assert "legacyMemoryLabel(item.filename)" not in html
    forbidden_visible_text = [
        "<h2>Speakers</h2>",
        "<h2>Memory Subjects</h2>",
        "<h2>Subject Memory</h2>",
        "<h2>Legacy Memory</h2>",
        "Select a memory subject",
        "No memory subjects yet",
        "No speakers yet",
        ">Profile<",
        ">Companion<",
        ">Episodes<",
        ">Growth Arcs<",
        "Clear Subject Memory",
        "Forget query",
        "topic or fact to remove",
        "Forget Matching Memory",
        "Speaker updated",
        "Speaker archived",
        "Select a merge target",
        "Memory subject merged",
        "No stored summaries",
        ">Save<",
        ">Archive<",
        ">Memory<",
        "Select target",
        ">Merge<",
        "Legacy memory list failed",
        "No legacy memory files found",
        "manual review only",
        "TTL",
    ]
    for text in forbidden_visible_text:
        assert text not in html


def test_non_localhost_api_fails_closed_without_auth_service():
    async def scenario():
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, FakeRuntime())
        request = _request("GET", "/api/xiaoxin/devices", remote="10.0.0.9")
        return await handler.handle_devices(request)

    response = asyncio.run(scenario())

    assert response.status == 404
    assert json.loads(response.text) == {"success": False, "message": "auth unavailable"}


def test_non_localhost_console_still_serves_auth_shell_without_auth_service():
    async def scenario():
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, FakeRuntime())
        request = _request("GET", "/xiaoxin/control/", remote="10.0.0.9")
        response = await handler.handle_console(request)
        return response.status, response.text

    status, html = asyncio.run(scenario())

    assert status == 200
    assert "/api/xiaoxin/auth/login" in html
    assert "/api/xiaoxin/auth/register" in html


def test_request_without_remote_api_fails_closed_without_auth_service():
    async def scenario():
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, FakeRuntime())
        request = make_mocked_request("GET", "/api/xiaoxin/devices")
        response = await handler.handle_devices(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 404
    assert body == {"success": False, "message": "auth unavailable"}


def test_miniprogram_notification_history_requires_session(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        request = _request("GET", "/api/miniprogram/notifications/history")
        response = await handler.handle_miniprogram_notification_history(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 401
    assert body == {"success": False, "message": "login required"}


def test_miniprogram_notification_history_lists_only_current_users_device_records(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        alice, alice_token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        bob, bob_token = runtime.auth_service.register("bob", "secret-pass", "Bob")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.upsert_seen_device("device-b", "Device B")
        runtime.identity_store.bind_device("device-a", alice.id, "Device A")
        runtime.identity_store.bind_device("device-b", bob.id, "Device B")

        alice_response = await _post_control_event(handler, alice_token, "device-a")
        alice_delivery_id = json.loads(alice_response.text)["delivery_id"]
        runtime.store.transition(alice_delivery_id, XiaoxinDeliveryState.DONE)

        bob_response = await _post_control_event(handler, bob_token, "device-b")
        bob_delivery_id = json.loads(bob_response.text)["delivery_id"]
        runtime.store.transition(
            bob_delivery_id,
            XiaoxinDeliveryState.FAILED,
            XiaoxinFailureReason.ACK_TIMEOUT,
        )

        request = _miniprogram_request(
            "GET",
            "/api/miniprogram/notifications/history",
            alice_token,
        )
        response = await handler.handle_miniprogram_notification_history(request)
        return response.status, json.loads(response.text), alice_delivery_id, bob_delivery_id

    status, body, alice_delivery_id, bob_delivery_id = asyncio.run(scenario())

    assert status == 200
    assert body["success"] is True
    assert [item["deliveryId"] for item in body["notifications"]] == [alice_delivery_id]
    notification = body["notifications"][0]
    assert notification["id"] == alice_delivery_id
    assert notification["deviceId"] == "device-a"
    assert notification["type"] == "notification"
    assert notification["title"] == "Reminder"
    assert notification["body"] == "Remember the task."
    assert notification["status"] == "announced"
    assert notification["deliveryState"] == "done"
    assert notification["reason"] == ""
    assert notification["source"] == "hardware_delivery"
    assert notification["occurredAt"] == notification["createdAt"]
    assert bob_delivery_id not in json.dumps(body)


def test_miniprogram_notification_history_marks_offline_course_as_missed(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")

        request = _request(
            "POST",
            "/api/xiaoxin/events",
            headers={
                "Content-Type": "application/json",
                "Cookie": f"xiaoxin_session={token}",
            },
            payload=None,
        )
        request._read_bytes = _json_body(
            {
                "device_id": "device-a",
                "event": "course_reminder",
                "title": "Course reminder",
                "body": "Linear Algebra starts at 10:10",
                "course_name": "Linear Algebra",
                "classroom": "Room 201",
                "starts_at": "10:10",
            }
        )
        create_response = await handler.handle_create_event(request)
        delivery_id = json.loads(create_response.text)["delivery_id"]
        runtime.store.transition(
            delivery_id,
            XiaoxinDeliveryState.FAILED,
            XiaoxinFailureReason.DEVICE_OFFLINE,
        )

        history_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/notifications/history",
            token,
        )
        history_response = await handler.handle_miniprogram_notification_history(
            history_request
        )
        return history_response.status, json.loads(history_response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    notification = body["notifications"][0]
    assert notification["type"] == "course_reminder"
    assert notification["status"] == "missed"
    assert notification["deliveryState"] == "failed"
    assert notification["reason"] == "device_offline"
    assert notification["source"] == "hardware_delivery"


def test_miniprogram_notification_history_marks_offline_todo_as_pending_redelivery(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")

        request = _request(
            "POST",
            "/api/xiaoxin/events",
            headers={
                "Content-Type": "application/json",
                "Cookie": f"xiaoxin_session={token}",
            },
            payload=None,
        )
        request._read_bytes = _json_body(
            {
                "device_id": "device-a",
                "event": "todo_reminder",
                "title": "Todo reminder",
                "body": "Submit lab report",
                "todo_title": "Submit lab report",
                "due_at": "2026-03-10T20:00:00+08:00",
            }
        )
        create_response = await handler.handle_create_event(request)
        delivery_id = json.loads(create_response.text)["delivery_id"]
        runtime.store.transition(
            delivery_id,
            XiaoxinDeliveryState.FAILED,
            XiaoxinFailureReason.DEVICE_OFFLINE,
        )

        history_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/notifications/history",
            token,
        )
        history_response = await handler.handle_miniprogram_notification_history(
            history_request
        )
        return history_response.status, json.loads(history_response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    notification = body["notifications"][0]
    assert notification["type"] == "todo_reminder"
    assert notification["status"] == "pending_redelivery"
    assert notification["deliveryState"] == "failed"
    assert notification["reason"] == "device_offline"


def test_miniprogram_notification_history_maps_pending_and_failed_states(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")

        pending_response = await _post_control_event(handler, token, "device-a")
        pending_delivery_id = json.loads(pending_response.text)["delivery_id"]

        failed_response = await _post_control_event(handler, token, "device-a")
        failed_delivery_id = json.loads(failed_response.text)["delivery_id"]
        runtime.store.transition(
            failed_delivery_id,
            XiaoxinDeliveryState.FAILED,
            XiaoxinFailureReason.ACK_TIMEOUT,
        )

        history_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/notifications/history",
            token,
        )
        history_response = await handler.handle_miniprogram_notification_history(
            history_request
        )
        return (
            json.loads(history_response.text),
            pending_delivery_id,
            failed_delivery_id,
        )

    body, pending_delivery_id, failed_delivery_id = asyncio.run(scenario())

    notifications = {
        item["deliveryId"]: item
        for item in body["notifications"]
    }
    assert notifications[pending_delivery_id]["status"] == "pending"
    assert notifications[pending_delivery_id]["deliveryState"] == "created"
    assert notifications[failed_delivery_id]["status"] == "failed"
    assert notifications[failed_delivery_id]["deliveryState"] == "failed"
    assert notifications[failed_delivery_id]["reason"] == "ack_timeout"


def test_miniprogram_notification_history_reads_persisted_records_after_runtime_restart(tmp_path):
    async def scenario():
        db_path = tmp_path / "xiaoxin_control.db"
        history_path = tmp_path / "xiaoxin_notification_history.db"
        runtime = AuthRuntime(db_path)
        runtime.notification_history_store = XiaoxinNotificationHistoryStore(history_path)
        runtime.store = XiaoxinDeliveryStore(
            history_sink=runtime.notification_history_store
        )
        runtime.dispatcher.store = runtime.store
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")

        create_response = await _post_control_event(handler, token, "device-a")
        delivery_id = json.loads(create_response.text)["delivery_id"]
        runtime.store.transition(delivery_id, XiaoxinDeliveryState.DONE)

        restarted = AuthRuntime(db_path)
        restarted.notification_history_store = XiaoxinNotificationHistoryStore(history_path)
        restarted.store = XiaoxinDeliveryStore(
            history_sink=restarted.notification_history_store
        )
        restarted.dispatcher.store = restarted.store
        restarted_handler = XiaoxinControlHandler({"xiaoxin_control": {}}, restarted)
        history_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/notifications/history",
            token,
        )
        history_response = await restarted_handler.handle_miniprogram_notification_history(
            history_request
        )
        return history_response.status, json.loads(history_response.text), delivery_id

    status, body, delivery_id = asyncio.run(scenario())

    assert status == 200
    assert [item["deliveryId"] for item in body["notifications"]] == [delivery_id]
    assert body["notifications"][0]["status"] == "announced"


def test_miniprogram_notification_history_falls_back_when_persisted_store_is_none(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.notification_history_store = None
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")

        create_response = await _post_control_event(handler, token, "device-a")
        delivery_id = json.loads(create_response.text)["delivery_id"]
        runtime.store.transition(delivery_id, XiaoxinDeliveryState.DONE)

        history_request = _miniprogram_request(
            "GET",
            "/api/miniprogram/notifications/history",
            token,
        )
        history_response = await handler.handle_miniprogram_notification_history(
            history_request
        )
        return history_response.status, json.loads(history_response.text), delivery_id

    status, body, delivery_id = asyncio.run(scenario())

    assert status == 200
    assert [item["deliveryId"] for item in body["notifications"]] == [delivery_id]


def test_deliveries_endpoints_return_recent_and_detail_records_for_owner(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.bind_device("device-a", user.id, "Device A")
        create_request = _request(
            "POST",
            "/api/xiaoxin/events",
            headers={"Content-Type": "application/json"},
            payload=None,
        )
        create_request._read_bytes = json.dumps(
            {
                "device_id": "aa",
                "event": "notification",
                "title": "提醒",
                "body": "内容",
            }
        ).encode("utf-8")
        create_response = await _post_control_event(handler, token, "device-a")
        delivery_id = json.loads(create_response.text)["delivery_id"]

        list_request = _request(
            "GET",
            "/api/xiaoxin/deliveries",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        detail_request = _request(
            "GET",
            f"/api/xiaoxin/deliveries/{delivery_id}",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"delivery_id": delivery_id},
        )
        missing_request = _request(
            "GET",
            "/api/xiaoxin/deliveries/del_missing",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"delivery_id": "del_missing"},
        )

        list_response = await handler.handle_deliveries(list_request)
        detail_response = await handler.handle_delivery_detail(detail_request)
        missing_response = await handler.handle_delivery_detail(missing_request)
        return (
            json.loads(list_response.text),
            json.loads(detail_response.text),
            missing_response.status,
            json.loads(missing_response.text),
        )

    list_body, detail_body, missing_status, missing_body = asyncio.run(scenario())

    assert list_body["deliveries"][0]["delivery_id"] == detail_body["delivery_id"]
    assert detail_body["timeline"][0]["state"] == "created"
    assert missing_status == 404
    assert missing_body == {"success": False, "message": "delivery not found"}


def test_deliveries_list_only_includes_current_users_devices(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        alice, alice_token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        bob, bob_token = runtime.auth_service.register("bob", "secret-pass", "Bob")
        runtime.identity_store.upsert_seen_device("device-a", "Device A")
        runtime.identity_store.upsert_seen_device("device-b", "Device B")
        runtime.identity_store.bind_device("device-a", alice.id, "Device A")
        runtime.identity_store.bind_device("device-b", bob.id, "Device B")
        await _post_control_event(handler, alice_token, "device-a")
        await _post_control_event(handler, bob_token, "device-b")

        request = _request(
            "GET",
            "/api/xiaoxin/deliveries",
            headers={"Cookie": f"xiaoxin_session={alice_token}"},
        )
        response = await handler.handle_deliveries(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert [record["device_id"] for record in body["deliveries"]] == ["device-a"]


def test_delivery_detail_returns_404_for_other_users_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _alice, alice_token = runtime.auth_service.register(
            "alice", "secret-pass", "Alice", role="admin"
        )
        bob, bob_token = runtime.auth_service.register("bob", "secret-pass", "Bob")
        runtime.identity_store.upsert_seen_device("device-b", "Device B")
        runtime.identity_store.bind_device("device-b", bob.id, "Device B")
        create_response = await _post_control_event(handler, bob_token, "device-b")
        delivery_id = json.loads(create_response.text)["delivery_id"]

        request = _request(
            "GET",
            f"/api/xiaoxin/deliveries/{delivery_id}",
            headers={"Cookie": f"xiaoxin_session={alice_token}"},
            match_info={"delivery_id": delivery_id},
        )
        response = await handler.handle_delivery_detail(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 404
    assert body == {"success": False, "message": "delivery not found"}


def test_options_request_does_not_include_control_secret_header():
    async def scenario():
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, FakeRuntime())
        request = _request("OPTIONS", "/api/xiaoxin/events")
        response = await handler.handle_options(request)
        return response.headers

    headers = asyncio.run(scenario())

    assert "X-Xiaoxin-Control-Secret" not in headers["Access-Control-Allow-Headers"]
    assert headers["Access-Control-Allow-Methods"] == "GET, POST, PUT, PATCH, DELETE, OPTIONS"


def test_register_login_me_and_logout_flow(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)

        register = _request(
            "POST",
            "/api/xiaoxin/auth/register",
            headers={"Content-Type": "application/json"},
        )
        register._read_bytes = json.dumps(
            {"username": "liu", "password": "secret-pass", "display_name": "刘昊江"}
        ).encode("utf-8")
        register_response = await handler.handle_register(register)
        cookie = register_response.cookies["xiaoxin_session"].value

        me = _request(
            "GET",
            "/api/xiaoxin/auth/me",
            headers={"Cookie": f"xiaoxin_session={cookie}"},
        )
        me_response = await handler.handle_me(me)

        logout = _request(
            "POST",
            "/api/xiaoxin/auth/logout",
            headers={"Cookie": f"xiaoxin_session={cookie}"},
        )
        logout_response = await handler.handle_logout(logout)

        me_after = _request(
            "GET",
            "/api/xiaoxin/auth/me",
            headers={"Cookie": f"xiaoxin_session={cookie}"},
        )
        me_after_response = await handler.handle_me(me_after)
        return (
            register_response.status,
            json.loads(me_response.text),
            logout_response.status,
            me_after_response.status,
        )

    register_status, me_body, logout_status, me_after_status = asyncio.run(scenario())

    assert register_status == 200
    assert me_body["user"]["username"] == "liu"
    assert logout_status == 200
    assert me_after_status == 401


def test_devices_requires_session_when_auth_runtime_exists(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        request = _request("GET", "/api/xiaoxin/devices")
        return await handler.handle_devices(request)

    response = asyncio.run(scenario())

    assert response.status == 401


def test_logged_in_user_cannot_bind_seen_device_without_activation(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "刘昊江")
        runtime.identity_store.upsert_seen_device("device-1")

        bind_request = _request(
            "POST",
            "/api/xiaoxin/devices/device-1/bind",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"device_id": "device-1"},
        )
        bind_response = await handler.handle_bind_device(bind_request)

        device = runtime.identity_store.get_device_by_device_id("device-1")
        return user, bind_response.status, json.loads(bind_response.text), device

    user, status, body, device = asyncio.run(scenario())

    assert user.username == "liu"
    assert status == 403
    assert body == {"success": False, "message": "activation_required"}
    assert device is not None
    assert device.owner_user_id is None


def test_logged_in_user_bind_rejects_unsafe_path_device_id(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        bind_request = _request(
            "POST",
            "/api/xiaoxin/devices/device%2Fescape/bind",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"device_id": "device/escape"},
        )
        response = await handler.handle_bind_device(bind_request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 400
    assert body == {"success": False, "message": "invalid device_id"}


def test_logged_in_user_can_unbind_owned_control_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-1", "Desk")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")

        unbind_request = _request(
            "POST",
            "/api/xiaoxin/devices/device-1/unbind",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"device_id": "device-1"},
        )
        response = await handler.handle_unbind_device(unbind_request)
        device = runtime.identity_store.get_device_by_device_id("device-1")
        return response.status, json.loads(response.text), device

    status, body, device = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True, "device_id": "device-1"}
    assert device is not None
    assert device.owner_user_id is None
    assert device.bind_status == "seen"


def test_logged_in_user_cannot_unbind_another_users_control_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        alice, _alice_token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        _bob, bob_token = runtime.auth_service.register("bob", "secret-pass", "Bob")
        runtime.identity_store.upsert_seen_device("device-1", "Desk")
        runtime.identity_store.bind_device("device-1", alice.id, "Desk")

        unbind_request = _request(
            "POST",
            "/api/xiaoxin/devices/device-1/unbind",
            headers={"Cookie": f"xiaoxin_session={bob_token}"},
            match_info={"device_id": "device-1"},
        )
        response = await handler.handle_unbind_device(unbind_request)
        device = runtime.identity_store.get_device_by_device_id("device-1")
        return response.status, json.loads(response.text), device, alice.id

    status, body, device, alice_id = asyncio.run(scenario())

    assert status == 403
    assert body == {"success": False, "message": "device_not_bound"}
    assert device is not None
    assert device.owner_user_id == alice_id
    assert device.bind_status == "bound"


def test_logged_in_user_cannot_manually_bind_unseen_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        bind_request = _request(
            "POST",
            "/api/xiaoxin/devices/manual-bind",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
        )
        bind_request._read_bytes = json.dumps(
            {"device_id": "manual-device-1", "display_name": "桌面小芯"}
        ).encode("utf-8")
        bind_response = await handler.handle_manual_bind_device(bind_request)

        device = runtime.identity_store.get_device_by_device_id("manual-device-1")
        return user.id, bind_response.status, json.loads(bind_response.text), device

    user_id, status, bind_body, device = asyncio.run(scenario())

    assert user_id
    assert status == 403
    assert bind_body == {"success": False, "message": "activation_required"}
    assert device is None


def test_manual_bind_rejects_empty_device_id(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/manual-bind",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
        )
        request._read_bytes = json.dumps({"device_id": "   "}).encode("utf-8")
        response = await handler.handle_manual_bind_device(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 400
    assert body == {"success": False, "message": "device_id required", "field": "device_id"}


def test_manual_bind_rejects_unsafe_device_id(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/manual-bind",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
        )
        request._read_bytes = json.dumps({"device_id": "device/escape"}).encode("utf-8")
        response = await handler.handle_manual_bind_device(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 400
    assert body == {"success": False, "message": "invalid device_id"}


def test_logged_in_user_can_wake_owned_bound_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Device 1")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/device-1/wake",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"device_id": "device-1"},
        )
        response = await handler.handle_wake_device(request)
        return response.status, json.loads(response.text), runtime.doorbell_client.published

    status, body, published = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True, "device_id": "device-1"}
    assert published == ["device-1"]


def test_logged_in_console_can_wake_offline_device_by_id(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/wake-by-id",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
        )
        request._read_bytes = json.dumps({"device_id": "device-offline-1"}).encode(
            "utf-8"
        )
        response = await handler.handle_wake_device_by_id(request)
        device = runtime.identity_store.get_device_by_device_id("device-offline-1")
        return response.status, json.loads(response.text), runtime.doorbell_client.published, device

    status, body, published, device = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True, "device_id": "device-offline-1"}
    assert published == ["device-offline-1"]
    assert device is None


def test_wake_by_id_rejects_unsafe_device_id_without_publish(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/wake-by-id",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
        )
        request._read_bytes = json.dumps({"device_id": "tenant/escape"}).encode(
            "utf-8"
        )
        response = await handler.handle_wake_device_by_id(request)
        return response.status, json.loads(response.text), runtime.doorbell_client.published

    status, body, published = asyncio.run(scenario())

    assert status == 400
    assert body == {"success": False, "message": "invalid device_id"}
    assert published == []


def test_wake_device_allows_control_admin_to_wake_other_users_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _alice, alice_token = runtime.auth_service.register(
            "alice", "secret-pass", "Alice", role="admin"
        )
        bob, _bob_token = runtime.auth_service.register("bob", "secret-pass", "Bob")
        runtime.identity_store.upsert_seen_device("device-b")
        runtime.identity_store.bind_device("device-b", bob.id, "Device B")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/device-b/wake",
            headers={"Cookie": f"xiaoxin_session={alice_token}"},
            match_info={"device_id": "device-b"},
        )
        response = await handler.handle_wake_device(request)
        return response.status, json.loads(response.text), runtime.doorbell_client.published

    status, body, published = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True, "device_id": "device-b"}
    assert published == ["device-b"]


def test_wake_device_reports_publish_failure(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.doorbell_client.should_publish = False
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Device 1")
        request = _request(
            "POST",
            "/api/xiaoxin/devices/device-1/wake",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"device_id": "device-1"},
        )
        response = await handler.handle_wake_device(request)
        return response.status, json.loads(response.text), runtime.doorbell_client.published

    status, body, published = asyncio.run(scenario())

    assert status == 400
    assert body == {"success": False, "message": "wake_publish_failed"}
    assert published == ["device-1"]


async def _post_text_chat(handler, token: str, device_id: str, payload: dict):
    request = _request(
        "POST",
        f"/api/xiaoxin/devices/{device_id}/text-chat",
        headers={
            "Content-Type": "application/json",
            "Cookie": f"xiaoxin_session={token}",
        },
        match_info={"device_id": device_id},
    )
    request._read_bytes = _json_body(payload)
    return await handler.handle_device_text_chat(request)


def test_public_entry_compliance_gate_blocks_companion_but_keeps_tools(tmp_path):
    async def scenario():
        runtime = AuthRuntime(
            tmp_path / "xiaoxin_control.db",
            auto_configure_compliance=False,
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        session_status, session = await _miniprogram_session(
            handler,
            openid="wx-compliance-gate",
            nickname="Gate User",
        )
        token = session["token"]
        user_id = session["user"]["id"]

        bind_denied = await _bind_miniprogram_device_by_activation(
            handler,
            runtime,
            token,
            "device-gated-bind",
        )

        runtime.identity_store.upsert_seen_device("device-gated-chat", "Gate device")
        runtime.identity_store.bind_device(
            "device-gated-chat",
            user_id,
            "Gate device",
        )
        conn = TextChatConnection()
        runtime.registry.register_connection(
            "device-gated-chat",
            conn,
            "websocket",
        )

        chat_denied = await _post_text_chat(
            handler,
            token,
            "device-gated-chat",
            {"text": "我今天很难过，陪我聊聊"},
        )
        tool_allowed = await _post_text_chat(
            handler,
            token,
            "device-gated-chat",
            {"text": "今天天气怎么样"},
        )

        runtime.compliance_service.declare_age_band(
            user_id,
            AgeBand.AGE_18_PLUS,
        )
        runtime.compliance_service.accept_current_agreements(user_id)
        companion_allowed = await _post_text_chat(
            handler,
            token,
            "device-gated-chat",
            {"text": "我今天很难过，陪我聊聊"},
        )
        return {
            "session_status": session_status,
            "bind_denied": bind_denied,
            "chat_denied": chat_denied,
            "tool_allowed": tool_allowed,
            "companion_allowed": companion_allowed,
            "submitted_texts": conn.submitted_texts,
        }

    result = asyncio.run(scenario())

    assert result["session_status"] == 200
    bind_body = json.loads(result["bind_denied"].text)
    assert result["bind_denied"].status == 403
    assert bind_body["code"] == "COMPLIANCE_GATE_DENIED"
    assert bind_body["capability"] == "DEVICE_BIND"
    assert bind_body["requiredActions"] == [
        "declare_age_band",
        "accept_agreements",
    ]

    chat_body = json.loads(result["chat_denied"].text)
    assert result["chat_denied"].status == 403
    assert chat_body["code"] == "COMPLIANCE_GATE_DENIED"
    assert chat_body["capability"] == "COMPANION_CHAT"
    assert chat_body["mode"] == "tool_only"

    assert result["tool_allowed"].status == 200
    assert result["companion_allowed"].status == 200
    assert result["submitted_texts"] == [
        "今天天气怎么样",
        "我今天很难过，陪我聊聊",
    ]


def test_text_chat_endpoint_submits_text_to_owned_connected_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")
        conn = TextChatConnection()
        runtime.registry.register_connection("device-a", conn, "websocket")

        response = await _post_text_chat(
            handler,
            token,
            "device-a",
            {"text": "  浣犺兘鍚埌鎴戝悧锛? "},
        )
        return response.status, json.loads(response.text), conn.submitted_texts

    status, body, submitted_texts = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True, "message": "submitted"}
    assert submitted_texts == ["浣犺兘鍚埌鎴戝悧锛?"]


def test_text_chat_simulated_time_requires_enabled_admin_and_is_forwarded(
    tmp_path, monkeypatch
):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, user_token = runtime.auth_service.register(
            "alice", "secret-pass", "Alice"
        )
        _, admin_token = runtime.auth_service.register(
            "admin", "secret-pass", "Admin", role="admin"
        )
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")
        conn = TextChatConnection()
        runtime.registry.register_connection("device-a", conn, "websocket")
        work_calls = []

        async def run_due_memory_work(*, now, pet_id, limit):
            work_calls.append((now, pet_id, limit))
            return types.SimpleNamespace(
                claimed=2, succeeded=2, retried=0, failed=0
            )

        runtime.companion_mind = types.SimpleNamespace(
            run_due_memory_work=run_due_memory_work
        )
        payload = {
            "text": "remember me",
            "simulated_as_of": "2026-10-28T16:40:58+08:00",
            "evaluation_run_id": "run-20261028",
            "case_id": "H02",
            "await_tts_terminal": True,
        }

        denied = await _post_text_chat(
            handler, user_token, "device-a", payload
        )
        monkeypatch.setenv("XIAOXIN_ALLOW_SIMULATED_TIME", "1")
        monkeypatch.setenv("XIAOXIN_EVALUATION_MODE", "1")
        accepted = await _post_text_chat(
            handler, admin_token, "device-a", payload
        )
        return denied, accepted, conn, work_calls

    denied, accepted, conn, work_calls = asyncio.run(scenario())

    assert denied.status == 403
    assert json.loads(denied.text)["message"] == "evaluation mode not allowed"
    assert accepted.status == 200
    accepted_body = json.loads(accepted.text)
    assert accepted_body["simulated_as_of"] == "2026-10-28T16:40:58+08:00"
    assert accepted_body["evaluation_run_id"] == "run-20261028"
    assert accepted_body["case_id"] == "H02"
    assert accepted_body["event_id"] == "event-eval-1"
    assert accepted_body["sentence_id"] == "sentence-eval-1"
    assert accepted_body["assistant_text"] == "收到。"
    assert accepted_body["tts_outcome"] == "done"
    assert accepted_body["accelerated_work"] == {
        "claimed": 2,
        "succeeded": 2,
        "retried": 0,
        "failed": 0,
    }
    assert conn.submitted_simulated_times == [
        datetime.fromisoformat("2026-10-28T16:40:58+08:00")
    ]
    assert conn.submitted_await_tts_terminal == [True]
    assert conn.submitted_evaluation_ids == [("run-20261028", "H02")]
    assert work_calls == [("2026-10-28T16:40:58+08:00", "pet-a", 20)]


def test_text_chat_endpoint_can_use_owned_confirmed_speaker_profile(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")
        profile = runtime.identity_store.get_or_create_speaker_profile(
            user.id,
            "device-a",
            "provider-alice",
            "Alice",
        )
        conn = TextChatConnection()
        runtime.registry.register_connection("device-a", conn, "websocket")

        response = await _post_text_chat(
            handler,
            token,
            "device-a",
            {"text": "remember this", "speaker_profile_id": profile.id},
        )
        return response.status, json.loads(response.text), conn

    status, body, conn = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True, "message": "submitted"}
    assert conn.submitted_texts == ["remember this"]
    assert conn.submitted_speakers == ["voiceprint:provider-alice"]


def test_text_chat_endpoint_rejects_speaker_profile_from_another_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        for device_id in ("device-a", "device-b"):
            runtime.identity_store.upsert_seen_device(device_id, device_id)
            runtime.identity_store.bind_device(device_id, user.id, device_id)
        profile = runtime.identity_store.get_or_create_speaker_profile(
            user.id,
            "device-b",
            "provider-alice",
            "Alice",
        )
        conn = TextChatConnection()
        runtime.registry.register_connection("device-a", conn, "websocket")

        response = await _post_text_chat(
            handler,
            token,
            "device-a",
            {"text": "remember this", "speaker_profile_id": profile.id},
        )
        return response.status, json.loads(response.text), conn

    status, body, conn = asyncio.run(scenario())

    assert status == 403
    assert body == {
        "success": False,
        "message": "speaker profile not allowed",
        "field": "speaker_profile_id",
    }
    assert conn.submitted_texts == []


def test_text_chat_endpoint_requires_session(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        request = _request(
            "POST",
            "/api/xiaoxin/devices/device-a/text-chat",
            headers={"Content-Type": "application/json"},
            match_info={"device_id": "device-a"},
        )
        request._read_bytes = _json_body({"text": "hello"})
        response = await handler.handle_device_text_chat(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 401
    assert body == {"success": False, "message": "login required"}


def test_text_chat_endpoint_allows_control_admin_target_before_connection_check(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _alice, alice_token = runtime.auth_service.register(
            "alice", "secret-pass", "Alice", role="admin"
        )
        bob, _bob_token = runtime.auth_service.register("bob", "secret-pass", "Bob")
        runtime.identity_store.upsert_seen_device("device-b", "Bob device")
        runtime.identity_store.bind_device("device-b", bob.id, "Bob device")

        response = await _post_text_chat(
            handler,
            alice_token,
            "device-b",
            {"text": "hello"},
        )
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 409
    assert body == {"success": False, "message": "device not connected"}


def test_text_chat_endpoint_rejects_empty_text(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")

        response = await _post_text_chat(
            handler,
            token,
            "device-a",
            {"text": "   "},
        )
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 400
    assert body == {"success": False, "message": "text required", "field": "text"}


def test_text_chat_endpoint_rejects_text_over_500_characters(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")

        response = await _post_text_chat(
            handler,
            token,
            "device-a",
            {"text": "x" * 501},
        )
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 400
    assert body == {"success": False, "message": "text too long", "field": "text"}


def test_text_chat_endpoint_rejects_non_object_json_body(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")

        results = []
        for payload in ([], None, "hello"):
            response = await _post_text_chat(handler, token, "device-a", payload)
            results.append((response.status, json.loads(response.text)))
        return results

    results = asyncio.run(scenario())

    assert results == [
        (400, {"success": False, "message": "invalid json object", "field": "body"}),
        (400, {"success": False, "message": "invalid json object", "field": "body"}),
        (400, {"success": False, "message": "invalid json object", "field": "body"}),
    ]


def test_text_chat_endpoint_rejects_non_string_text(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")

        response = await _post_text_chat(
            handler,
            token,
            "device-a",
            {"text": ["hello"]},
        )
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 400
    assert body == {"success": False, "message": "text must be string", "field": "text"}


def test_text_chat_endpoint_rejects_disconnected_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")

        response = await _post_text_chat(
            handler,
            token,
            "device-a",
            {"text": "hello"},
        )
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 409
    assert body == {"success": False, "message": "device not connected"}


def test_text_chat_endpoint_maps_busy_error_to_conflict(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")
        conn = TextChatConnection()
        conn.error = RuntimeError("text chat busy")
        runtime.registry.register_connection("device-a", conn, "websocket")

        response = await _post_text_chat(handler, token, "device-a", {"text": "hello"})
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 409
    assert body == {"success": False, "message": "text chat busy"}


def test_text_chat_endpoint_logs_unexpected_failures(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        runtime.identity_store.upsert_seen_device("device-a", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-a", user.id, "Desk XiaoXin")
        conn = TextChatConnection()
        conn.error = RuntimeError("boom")
        runtime.registry.register_connection("device-a", conn, "websocket")
        records = []

        class FakeLogger:
            def bind(self, **kwargs):
                return self

            def exception(self, message, *args):
                records.append((message, args))

        handler.logger = FakeLogger()

        response = await _post_text_chat(handler, token, "device-a", {"text": "hello"})
        return response.status, json.loads(response.text), records

    status, body, records = asyncio.run(scenario())

    assert status == 500
    assert body == {"success": False, "message": "text chat failed"}
    assert records == [("text chat failed device_id=%s", ("device-a",))]


def test_student_cannot_wake_unbound_prebound_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.identity_store.upsert_seen_device("device-1", tenant_id="hzcu-iee")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _status, session = await _miniprogram_session(handler)
        request = _miniprogram_request(
            "POST",
            "/api/xiaoxin/devices/device-1/wake",
            session["token"],
            match_info={"device_id": "device-1"},
        )
        response = await handler.handle_wake_device(request)
        return response.status, json.loads(response.text), runtime

    status, body, runtime = asyncio.run(scenario())

    assert status == 403
    assert body == {"success": False, "message": "device_not_bound"}
    assert runtime.doorbell_client.published == []


def test_student_wake_rejects_tenant_mismatch(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"tenant": {"id": "hzcu-iee"}}},
            runtime,
        )
        _status, session = await _miniprogram_session(handler)
        user_id = session["user"]["id"]
        runtime.identity_store.upsert_seen_device("device-1", tenant_id="other-tenant")
        runtime.identity_store.bind_device(
            "device-1",
            user_id,
            "Desk",
            tenant_id="other-tenant",
        )
        request = _miniprogram_request(
            "POST",
            "/api/xiaoxin/devices/device-1/wake",
            session["token"],
            match_info={"device_id": "device-1"},
        )
        response = await handler.handle_wake_device(request)
        return response.status, json.loads(response.text), runtime

    status, body, runtime = asyncio.run(scenario())

    assert status == 403
    assert body == {"success": False, "message": "tenant_mismatch"}
    assert runtime.doorbell_client.published == []


def test_legacy_bind_endpoint_does_not_assign_first_owner_or_takeover(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        alice, alice_token = runtime.auth_service.register("alice", "secret-pass", "Alice")
        bob, bob_token = runtime.auth_service.register("bob", "secret-pass", "Bob")
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", bob.id, "Bob Device")

        first_bind_request = _request(
            "POST",
            "/api/xiaoxin/devices/device-1/bind",
            headers={"Cookie": f"xiaoxin_session={bob_token}"},
            match_info={"device_id": "device-1"},
        )
        first_bind_response = await handler.handle_bind_device(first_bind_request)

        takeover_request = _request(
            "POST",
            "/api/xiaoxin/devices/device-1/bind",
            headers={"Cookie": f"xiaoxin_session={alice_token}"},
            match_info={"device_id": "device-1"},
        )
        takeover_response = await handler.handle_bind_device(takeover_request)

        device = runtime.identity_store.get_device_by_device_id("device-1")
        return (
            alice.id,
            bob.id,
            first_bind_response.status,
            json.loads(first_bind_response.text),
            takeover_response.status,
            json.loads(takeover_response.text),
            device,
        )

    alice_id, bob_id, first_status, first_body, takeover_status, takeover_body, device = asyncio.run(
        scenario()
    )

    assert alice_id != bob_id
    assert first_status == 403
    assert first_body == {"success": False, "message": "activation_required"}
    assert takeover_status == 403
    assert takeover_body == {"success": False, "message": "activation_required"}
    assert device is not None
    assert device.owner_user_id == bob_id


def test_login_returns_cookie_for_existing_user(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.auth_service.register("liu", "secret-pass", "刘昊江")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        login = _request(
            "POST",
            "/api/xiaoxin/auth/login",
            headers={"Content-Type": "application/json"},
        )
        login._read_bytes = json.dumps(
            {"username": "liu", "password": "secret-pass"}
        ).encode("utf-8")
        response = await handler.handle_login(login)
        return (
            response.status,
            json.loads(response.text),
            response.cookies["xiaoxin_session"].value,
        )

    status, body, cookie = asyncio.run(scenario())

    assert status == 200
    assert body["user"]["username"] == "liu"
    assert cookie


def test_auth_endpoints_ignore_configured_secret(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        runtime.auth_service.register("liu", "secret-pass", "Liu")
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"secret": "secret-1"}},
            runtime,
        )

        register = _request(
            "POST",
            "/api/xiaoxin/auth/register",
            headers={"Content-Type": "application/json"},
        )
        register._read_bytes = json.dumps(
            {"username": "newliu", "password": "secret-pass", "display_name": "New Liu"}
        ).encode("utf-8")
        register_response = await handler.handle_register(register)

        login = _request(
            "POST",
            "/api/xiaoxin/auth/login",
            headers={"Content-Type": "application/json"},
        )
        login._read_bytes = json.dumps(
            {"username": "liu", "password": "secret-pass"}
        ).encode("utf-8")
        login_response = await handler.handle_login(login)

        cookie = register_response.cookies["xiaoxin_session"].value

        me = _request(
            "GET",
            "/api/xiaoxin/auth/me",
            headers={"Cookie": f"xiaoxin_session={cookie}"},
        )
        me_response = await handler.handle_me(me)

        logout = _request(
            "POST",
            "/api/xiaoxin/auth/logout",
            headers={"Cookie": f"xiaoxin_session={cookie}"},
        )
        logout_response = await handler.handle_logout(logout)

        return (
            register_response.status,
            json.loads(register_response.text),
            login_response.status,
            json.loads(login_response.text),
            me_response.status,
            json.loads(me_response.text),
            logout_response.status,
            json.loads(logout_response.text),
        )

    (
        register_status,
        register_body,
        login_status,
        login_body,
        me_status,
        me_body,
        logout_status,
        logout_body,
    ) = asyncio.run(scenario())

    assert register_status == 200
    assert register_body["user"]["username"] == "newliu"
    assert login_status == 200
    assert login_body["user"]["username"] == "liu"
    assert me_status == 200
    assert me_body["user"]["username"] == "newliu"
    assert logout_status == 200
    assert logout_body == {"success": True}


def test_control_secret_header_is_ignored_when_sent(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler(
            {"xiaoxin_control": {"secret": "secret-1"}},
            runtime,
        )

        register = _request(
            "POST",
            "/api/xiaoxin/auth/register",
            headers={
                "Content-Type": "application/json",
                "X-Xiaoxin-Control-Secret": "secret-1",
            },
        )
        register._read_bytes = json.dumps(
            {"username": "liu", "password": "secret-pass", "display_name": "Liu"}
        ).encode("utf-8")
        register_response = await handler.handle_register(register)
        cookie = register_response.cookies["xiaoxin_session"].value

        me = _request(
            "GET",
            "/api/xiaoxin/auth/me",
            headers={
                "Cookie": f"xiaoxin_session={cookie}",
                "X-Xiaoxin-Control-Secret": "secret-1",
            },
        )
        me_response = await handler.handle_me(me)

        logout = _request(
            "POST",
            "/api/xiaoxin/auth/logout",
            headers={
                "Cookie": f"xiaoxin_session={cookie}",
                "X-Xiaoxin-Control-Secret": "secret-1",
            },
        )
        logout_response = await handler.handle_logout(logout)

        return (
            register_response.status,
            json.loads(register_response.text),
            me_response.status,
            json.loads(me_response.text),
            logout_response.status,
            json.loads(logout_response.text),
        )

    register_status, register_body, me_status, me_body, logout_status, logout_body = asyncio.run(
        scenario()
    )

    assert register_status == 200
    assert register_body["user"]["username"] == "liu"
    assert me_status == 200
    assert me_body["user"]["username"] == "liu"
    assert logout_status == 200
    assert logout_body == {"success": True}


def test_speakers_endpoint_returns_current_user_speakers(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "刘昊江")
        runtime.identity_store.upsert_seen_device("device-1", "桌面小新")
        runtime.identity_store.bind_device("device-1", user.id, "桌面小新")
        runtime.identity_resolver.resolve_turn_subject("device-1", "刘昊江", "session-1")

        request = _request(
            "GET",
            "/api/xiaoxin/speakers",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        response = await handler.handle_speakers(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["speakers"][0]["display_name"] == "刘昊江"


def test_update_speaker_changes_current_user_display_name(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "刘昊江")
        runtime.identity_store.upsert_seen_device("device-1", "桌面小新")
        runtime.identity_store.bind_device("device-1", user.id, "桌面小新")
        speaker = runtime.identity_store.get_or_create_speaker_profile(
            user.id,
            "device-1",
            "speaker-1",
            "原名",
        )

        request = _request(
            "PATCH",
            f"/api/xiaoxin/speakers/{speaker.id}",
            headers={"Cookie": f"xiaoxin_session={token}", "Content-Type": "application/json"},
            match_info={"speaker_id": speaker.id},
        )
        request._read_bytes = json.dumps({"display_name": "  新名字  "}).encode("utf-8")
        response = await handler.handle_update_speaker(request)
        speakers = runtime.identity_store.list_speakers_for_user(user.id)
        return response.status, json.loads(response.text), speakers

    status, body, speakers = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True}
    assert speakers[0].display_name == "新名字"


def test_archive_speaker_marks_current_user_speaker_archived(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "刘昊江")
        runtime.identity_store.upsert_seen_device("device-1", "桌面小新")
        runtime.identity_store.bind_device("device-1", user.id, "桌面小新")
        speaker = runtime.identity_store.get_or_create_speaker_profile(
            user.id,
            "device-1",
            "speaker-1",
            "原名",
        )

        request = _request(
            "POST",
            f"/api/xiaoxin/speakers/{speaker.id}/archive",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"speaker_id": speaker.id},
        )
        response = await handler.handle_archive_speaker(request)
        speakers = runtime.identity_store.list_speakers_for_user(user.id)
        return response.status, json.loads(response.text), speakers

    status, body, speakers = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True}
    assert speakers[0].status == "archived"


def test_memory_subjects_endpoint_returns_current_user_subjects(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "刘昊江")
        runtime.identity_store.upsert_seen_device("device-1", "桌面小新")
        runtime.identity_store.bind_device("device-1", user.id, "桌面小新")
        runtime.identity_resolver.resolve_turn_subject("device-1", None, "session-1")

        request = _request(
            "GET",
            "/api/xiaoxin/memory-subjects",
            headers={"Cookie": f"xiaoxin_session={token}"},
        )
        response = await handler.handle_memory_subjects(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["memory_subjects"][0]["kind"] == "device_unknown"


def test_merge_memory_subject_creates_alias_for_current_user_subjects(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = runtime.auth_service.register("liu", "secret-pass", "刘昊江")
        runtime.identity_store.upsert_seen_device("device-1", "桌面小新")
        runtime.identity_store.bind_device("device-1", user.id, "桌面小新")
        runtime.identity_store.upsert_seen_device("device-2", "宿舍小新")
        runtime.identity_store.bind_device("device-2", user.id, "宿舍小新")
        source = runtime.identity_store.get_or_create_memory_subject(
            user.id,
            "device-1",
            None,
            "device_unknown",
            "未知说话人",
        )
        target = runtime.identity_store.get_or_create_memory_subject(
            user.id,
            "device-2",
            None,
            "device_unknown",
            "未知说话人（宿舍设备）",
        )

        request = _request(
            "POST",
            f"/api/xiaoxin/memory-subjects/{source.id}/merge",
            headers={"Cookie": f"xiaoxin_session={token}", "Content-Type": "application/json"},
            match_info={"subject_id": source.id},
        )
        request._read_bytes = json.dumps({"to_subject_id": target.id}).encode("utf-8")
        response = await handler.handle_merge_memory_subject(request)
        resolved = runtime.identity_store.resolve_subject_alias(source.id)
        return response.status, json.loads(response.text), resolved

    status, body, resolved = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True}
    assert resolved is not None


def test_user_cannot_merge_other_users_subject(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user_a, token_a = runtime.auth_service.register("a-user", "secret-pass", "A")
        user_b, _ = runtime.auth_service.register("b-user", "secret-pass", "B")
        runtime.identity_store.upsert_seen_device("device-b", "B 设备")
        runtime.identity_store.bind_device("device-b", user_b.id, "B 设备")
        subject_b = runtime.identity_store.get_or_create_memory_subject(
            user_b.id,
            "device-b",
            None,
            "device_unknown",
            "未知说话人",
        )

        request = _request(
            "POST",
            f"/api/xiaoxin/memory-subjects/{subject_b.id}/merge",
            headers={"Cookie": f"xiaoxin_session={token_a}", "Content-Type": "application/json"},
            match_info={"subject_id": subject_b.id},
        )
        request._read_bytes = json.dumps({"to_subject_id": subject_b.id}).encode("utf-8")
        response = await handler.handle_merge_memory_subject(request)
        return user_a.id, response.status

    user_a_id, status = asyncio.run(scenario())

    assert user_a_id.startswith("usr_")
    assert status == 404


def test_memory_subject_summary_requires_owner(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user_a, token_a = runtime.auth_service.register("a-user", "secret-pass", "A")
        user_b, _ = runtime.auth_service.register("b-user", "secret-pass", "B")
        runtime.identity_store.upsert_seen_device("device-b", "B device")
        runtime.identity_store.bind_device("device-b", user_b.id, "B device")
        subject_b = runtime.identity_store.get_or_create_memory_subject(
            user_b.id,
            "device-b",
            None,
            "device_unknown",
            "未知说话人",
        )

        request = _request(
            "GET",
            f"/api/xiaoxin/memory-subjects/{subject_b.id}/memory",
            headers={"Cookie": f"xiaoxin_session={token_a}"},
            match_info={"subject_id": subject_b.id},
        )
        response = await handler.handle_memory_subject_detail(request)
        return response.status

    assert asyncio.run(scenario()) == 404


def test_memory_subject_handlers_return_auth_unavailable_without_auth_service(tmp_path):
    async def scenario():
        runtime = FakeRuntime()
        runtime.identity_store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user = runtime.identity_store.create_user("a-user", "secret-pass", "A")
        runtime.identity_store.upsert_seen_device("device-a", "A device")
        runtime.identity_store.bind_device("device-a", user.id, "A device")
        subject = runtime.identity_store.get_or_create_memory_subject(
            user.id,
            "device-a",
            None,
            "device_unknown",
            "unknown speaker",
        )

        cases = [
            ("GET", handler.handle_memory_subject_detail),
            ("POST", handler.handle_companion_memory_control),
        ]
        results = []
        for method, func in cases:
            request = _request(
                method,
                f"/api/xiaoxin/memory-subjects/{subject.id}/memory",
                match_info={"subject_id": subject.id},
            )
            response = await func(request)
            results.append((response.status, json.loads(response.text)))
        return results

    results = asyncio.run(scenario())

    for status, body in results:
        assert status == 404
        assert body == {"success": False, "message": "auth unavailable"}


def test_memory_subject_detail_uses_companion_projection(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, _ = runtime.identity_store.get_or_create_student_by_openid(
            "wx-v2-detail", "Liu"
        )
        runtime.identity_store.update_student_profile(user.id, {"grade": "大二"})
        token = runtime.auth_service.create_session_for_user(user.id)
        runtime.identity_store.upsert_seen_device("device-1", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-1", user.id, "Desk XiaoXin")
        speaker = runtime.identity_store.get_or_create_speaker_profile(
            user.id,
            "device-1",
            "speaker-1",
            "Liu",
        )
        subject = runtime.identity_store.get_or_create_memory_subject(
            user.id,
            "device-1",
            speaker.id,
            "user_speaker",
            "Liu",
        )
        from core.xiaoxin.companion import CompanionMind
        from core.xiaoxin.companion.store import CompanionStore

        runtime.companion_mind = CompanionMind(
            store=CompanionStore(tmp_path / "xiaoxin_companion.db")
        )
        request = _request(
            "GET",
            f"/api/xiaoxin/memory-subjects/{subject.id}/memory",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"subject_id": subject.id},
        )
        response = await handler.handle_memory_subject_detail(request)
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["surface"] == "operator"
    assert body["xiaoxin_age"] == 2
    assert "profile_count" not in body
    assert "episode_count" not in body


def test_memory_subject_detail_works_for_migrated_console_owner_without_student_profile(
    tmp_path,
):
    async def scenario():
        db_path = tmp_path / "xiaoxin_control.db"
        runtime = AuthRuntime(db_path)
        user, token = runtime.auth_service.register(
            "legacy-console", "secret-pass", "Legacy Console"
        )
        runtime.identity_store.upsert_seen_device("device-1", "Desk XiaoXin")
        runtime.identity_store.bind_device("device-1", user.id, "Desk XiaoXin")
        subject = runtime.identity_store.get_or_create_memory_subject(
            user.id,
            "device-1",
            None,
            "device_unknown",
            "未知说话人",
        )
        with runtime.identity_store._connect() as conn:
            conn.execute(
                "DELETE FROM personal_pets WHERE owner_user_id = ?", (user.id,)
            )

        runtime.identity_store = XiaoxinIdentityStore(db_path)
        runtime.auth_service = XiaoxinAuthService(runtime.identity_store)
        runtime.identity_resolver = XiaoxinIdentityResolver(runtime.identity_store)
        from core.xiaoxin.companion import CompanionMind
        from core.xiaoxin.companion.store import CompanionStore

        runtime.companion_mind = CompanionMind(
            store=CompanionStore(tmp_path / "xiaoxin_companion.db")
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        request = _request(
            "GET",
            f"/api/xiaoxin/memory-subjects/{subject.id}/memory",
            headers={"Cookie": f"xiaoxin_session={token}"},
            match_info={"subject_id": subject.id},
        )

        response = await handler.handle_memory_subject_detail(request)
        pet = runtime.identity_store.get_personal_pet_for_user(user.id)
        return response.status, json.loads(response.text), pet

    status, body, pet = asyncio.run(scenario())

    assert status == 200
    assert body["surface"] == "operator"
    assert body["payload"]["policy"]["memory_reference_budget"] == 0
    assert pet is not None
    assert pet.id.startswith("pet_")


def test_http_server_build_app_mounts_control_routes_when_runtime_present(monkeypatch):
    ota_module = types.ModuleType("core.api.ota_handler")
    vision_module = types.ModuleType("core.api.vision_handler")

    class _FakeOtaHandler:
        def __init__(self, config, xiaoxin_runtime=None):
            self.config = config
            self.constructor_runtime = xiaoxin_runtime
            self.xiaoxin_runtime = xiaoxin_runtime

        async def handle_get(self, request):
            return web.Response()

        async def handle_post(self, request):
            return web.Response()

        async def handle_activate(self, request):
            return web.Response(text="ok")

        async def handle_options(self, request):
            return web.Response()

        async def handle_download(self, request):
            return web.Response()

    class _FakeVisionHandler:
        def __init__(self, config):
            self.config = config

        async def handle_get(self, request):
            return web.Response()

        async def handle_post(self, request):
            return web.Response()

        async def handle_options(self, request):
            return web.Response()

    ota_module.OTAHandler = _FakeOtaHandler
    vision_module.VisionHandler = _FakeVisionHandler
    # The HTTP server may already be imported by OTA tests in this pytest
    # process. Patch the actual constructor bindings instead of manipulating
    # sys.modules, which can leave a stale package attribute after teardown.
    http_server = importlib.import_module("core.http_server")
    monkeypatch.setattr(http_server, "OTAHandler", _FakeOtaHandler)
    monkeypatch.setattr(http_server, "VisionHandler", _FakeVisionHandler)
    SimpleHttpServer = http_server.SimpleHttpServer

    config = {
        "server": {"http_port": 8003},
        "xiaoxin_control": {},
    }
    server = SimpleHttpServer(config, xiaoxin_runtime=FakeRuntime())

    app = server.build_app()

    routes = {
        (route.method, route.resource.canonical)
        for route in app.router.routes()
    }

    assert ("GET", "/xiaoxin/control/") in routes
    assert ("GET", "/api/xiaoxin/devices") in routes
    assert ("GET", "/api/xiaoxin/admin/devices") in routes
    assert ("GET", "/api/xiaoxin/admin/speakers") in routes
    assert ("GET", "/api/xiaoxin/admin/memory-subjects") in routes
    assert ("GET", "/api/xiaoxin/admin/memory-subjects/{subject_id}") in routes
    assert ("POST", "/api/xiaoxin/admin/memory-subjects/{subject_id}/control") in routes
    assert ("POST", "/api/xiaoxin/admin/memory-subjects/{subject_id}/merge") in routes
    assert ("GET", "/api/xiaoxin/admin/audits") in routes
    assert ("POST", "/xiaoxin/ota/activate") in routes
    assert server.ota_handler.constructor_runtime is server.xiaoxin_runtime
    assert ("POST", "/api/xiaoxin/devices/activation-bind") in routes
    assert ("POST", "/api/xiaoxin/devices/manual-bind") in routes
    assert ("POST", "/api/xiaoxin/devices/wake-by-id") in routes
    assert ("POST", "/api/xiaoxin/devices/{device_id}/bind") in routes
    assert ("POST", "/api/xiaoxin/devices/{device_id}/unbind") in routes
    assert ("POST", "/api/xiaoxin/devices/{device_id}/wake") in routes
    assert ("POST", "/api/xiaoxin/devices/{device_id}/text-chat") in routes
    assert ("POST", "/api/xiaoxin/devices/{device_id}/overview-mqtt-sync") in routes
    assert ("POST", "/api/xiaoxin/events") in routes
    assert ("GET", "/api/miniprogram/course-reminder-settings") in routes
    assert ("PATCH", "/api/miniprogram/course-reminder-settings") in routes
    assert ("GET", "/api/miniprogram/todos") in routes
    assert ("POST", "/api/miniprogram/todos") in routes
    assert ("PATCH", "/api/miniprogram/todos/{todo_id}") in routes
    assert ("DELETE", "/api/miniprogram/todos/{todo_id}") in routes
    assert ("GET", "/api/miniprogram/diagnostics") in routes
    assert ("GET", "/api/miniprogram/notifications/history") in routes
    assert ("GET", "/api/xiaoxin/demo-data") in routes
    assert ("PUT", "/api/xiaoxin/demo-data") in routes
    assert ("POST", "/api/xiaoxin/demo-data/overview/send") in routes
    assert ("POST", "/api/xiaoxin/demo-data/notifications/{notification_id}/send") in routes
    assert ("GET", "/api/xiaoxin/speakers") in routes
    assert ("GET", "/api/xiaoxin/legacy-memory") not in routes
    assert ("GET", "/api/xiaoxin/memory-subjects/{subject_id}/memory") in routes
    assert ("DELETE", "/api/xiaoxin/memory-subjects/{subject_id}/memory") not in routes
    assert ("POST", "/api/xiaoxin/memory-subjects/{subject_id}/forget") not in routes
    assert (
        "POST",
        "/api/xiaoxin/memory-subjects/{subject_id}/memory/control",
    ) in routes
    assert ("POST", "/api/xiaoxin/memory-subjects/{subject_id}/merge") in routes
