import asyncio
import json

from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict

from core.api.ota_handler import OTAHandler
from core.xiaoxin.activation_store import XiaoxinActivationStore
from core.xiaoxin.doorbell_credentials import DoorbellCredentialStore
from core.xiaoxin.identity.auth import XiaoxinAuthService
from core.xiaoxin.identity.store import XiaoxinIdentityStore
from core.xiaoxin.overview.models import IpCityLocation
from core.xiaoxin.overview.service import OverviewSyncService
from core.xiaoxin.overview.store import XiaoxinOverviewStore


class Runtime:
    def __init__(self, tmp_path):
        self.identity_store = XiaoxinIdentityStore(tmp_path / "identity.db")
        self.auth_service = XiaoxinAuthService(self.identity_store)
        self.activation_store = XiaoxinActivationStore(tmp_path / "activation.db")


class RecordingBootRuntime(Runtime):
    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.doorbell_credential_store = DoorbellCredentialStore(
            tmp_path / "doorbell.db"
        )
        self.boot_calls = []

    def note_device_boot(self, device_id, *, boot_event_id, boot_reason):
        self.boot_calls.append(
            {
                "device_id": device_id,
                "boot_event_id": boot_event_id,
                "boot_reason": boot_reason,
                "device_seen": (
                    self.identity_store.get_device_by_device_id(device_id) is not None
                ),
            }
        )


class RecordingOverviewService:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def observe_device_ip(self, device_id, public_ip, reason):
        self.calls.append((device_id, public_ip, reason))
        if self.error is not None:
            raise self.error
        return {"refreshed": True}


class StaticIpLocationProvider:
    def __init__(self):
        self.calls = []

    async def locate(self, public_ip):
        self.calls.append(public_ip)
        return IpCityLocation(
            province="Zhejiang",
            city="Hangzhou",
            country_code="CN",
            located_at="2026-07-10T08:00:00+00:00",
        )


class RecordingLogger:
    def __init__(self):
        self.fields = {}
        self.warnings = []
        self.records = []

    def bind(self, **fields):
        self.fields = fields
        return self

    def warning(self, message, *args):
        self.warnings.append((dict(self.fields), message, args))
        self.records.append(("warning", dict(self.fields), message, args))

    def debug(self, message, *args):
        self.records.append(("debug", dict(self.fields), message, args))

    def info(self, message, *args):
        self.records.append(("info", dict(self.fields), message, args))

    def error(self, message, *args):
        self.records.append(("error", dict(self.fields), message, args))

    def exception(self, message, *args):
        self.records.append(("exception", dict(self.fields), message, args))


def _config():
    return {
        "server": {
            "auth": {"enabled": False},
            "auth_key": "secret",
            "port": 8000,
            "http_port": 8003,
            "websocket": "ws://example/xiaoxin/v1/",
            "timezone_offset": 8,
        },
        "firmware_cache_ttl": 30,
    }


def _ota_request(
    device_id="device-1",
    *,
    client_id="client-1",
    remote="127.0.0.1",
    headers=None,
):
    request_headers = CIMultiDict({"Device-Id": device_id})
    if client_id is not None:
        request_headers.add("Client-Id", client_id)
    if headers:
        request_headers.extend(headers)
    request = make_mocked_request(
        "POST",
        "/xiaozhi/ota/",
        headers=request_headers,
    )
    request._transport_peername = (remote, 12345)
    request._read_bytes = json.dumps(
        {"application": {"version": "1.0.0"}, "board": {"type": "default"}}
    ).encode("utf-8")
    return request


def _activation_request(device_id="device-1"):
    request = make_mocked_request(
        "POST",
        "/xiaozhi/ota/activate",
        headers={"Device-Id": device_id},
    )
    request._read_bytes = b"{}"
    return request


def _device_credential_headers(runtime, tmp_path, device_id="device-1"):
    runtime.doorbell_credential_store = DoorbellCredentialStore(
        tmp_path / "doorbell.db"
    )
    credential = runtime.doorbell_credential_store.get_or_create(
        "credential-namespace",
        device_id,
    )
    return credential, {
        "Device-Username": credential.username,
        "Authorization": f"Bearer {credential.password}",
    }


def test_unbound_device_ota_response_contains_activation(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        handler = OTAHandler(_config(), runtime)
        response = await handler.handle_post(_ota_request())
        return response.status, json.loads(response.text), runtime

    status, body, runtime = asyncio.run(scenario())
    session = runtime.activation_store.get_latest_activation_by_device_id("device-1")

    assert status == 200
    assert session is not None
    assert body["activation"]["code"].isdigit()
    assert len(body["activation"]["code"]) == 6
    assert body["activation"]["code"] == session.code
    assert body["activation"]["message"] == session.message
    assert body["activation"]["challenge"] == session.challenge
    assert body["activation"]["timeout_ms"] == 600000
    assert runtime.identity_store.get_device_by_device_id("device-1") is not None


def test_bound_device_ota_response_omits_activation(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        user, _ = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        handler = OTAHandler(_config(), runtime)
        response = await handler.handle_post(_ota_request())
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())

    assert status == 200
    assert "activation" not in body


def test_ota_check_does_not_record_boot_checkin(tmp_path):
    async def scenario():
        runtime = RecordingBootRuntime(tmp_path)
        handler = OTAHandler(_config(), runtime)
        response = await handler.handle_post(_ota_request())
        return response.status, runtime

    status, runtime = asyncio.run(scenario())

    assert status == 200
    assert runtime.boot_calls == []


def test_activate_returns_202_until_bound_then_200(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        session = runtime.activation_store.create_or_refresh_activation("device-1")
        handler = OTAHandler(_config(), runtime)
        waiting_response = await handler.handle_activate(_activation_request())
        user, _ = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        done_response = await handler.handle_activate(_activation_request())
        return waiting_response.status, done_response.status, session

    waiting_status, done_status, session = asyncio.run(scenario())

    assert session.code
    assert waiting_status == 202
    assert done_status == 200


def test_activate_returns_404_without_live_activation_session(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        handler = OTAHandler(_config(), runtime)
        response = await handler.handle_activate(_activation_request())
        return response.status

    assert asyncio.run(scenario()) == 404


def test_activate_returns_410_for_expired_session(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.activation_store.create_or_refresh_activation("device-1", ttl_seconds=-1)
        handler = OTAHandler(_config(), runtime)
        response = await handler.handle_activate(_activation_request())
        return response.status

    assert asyncio.run(scenario()) == 410


def test_ota_response_contains_enabled_doorbell_mqtt_config(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        runtime.doorbell_credential_store = DoorbellCredentialStore(
            tmp_path / "doorbell.db"
        )
        config = _config()
        config["xiaoxin_control"] = {
            "tenant": {"id": "hzcu-iee", "display_name": "信息与电气工程学院"},
            "doorbell_mqtt": {"endpoint": "mqtt.example:1883"},
        }
        handler = OTAHandler(config, runtime)
        response = await handler.handle_post(_ota_request("device-1"))
        return response.status, json.loads(response.text), runtime

    status, body, runtime = asyncio.run(scenario())
    stored = runtime.doorbell_credential_store.get("hzcu-iee", "device-1")

    assert status == 200
    assert body["doorbell_mqtt"]["enabled"] is True
    assert "tenant_id" not in body["doorbell_mqtt"]
    assert body["doorbell_mqtt"]["endpoint"] == "mqtt.example:1883"
    assert body["doorbell_mqtt"]["client_id"] == "hzcu-iee:device-1"
    assert body["doorbell_mqtt"]["username"] == "hzcu-iee:device-1"
    assert body["doorbell_mqtt"]["password"] == stored.password
    assert body["doorbell_mqtt"]["status_topic"] == "device/device-1/status"
    assert (
        body["doorbell_mqtt"]["notification_topic"]
        == "device/device-1/notification"
    )
    assert body["doorbell_mqtt"]["overview_topic"] == "device/device-1/overview"
    assert body["doorbell_mqtt"]["keepalive_seconds"] == 240
    assert body["doorbell_mqtt"]["qos"] == 1
    assert stored is not None
    assert (
        runtime.identity_store.get_device_by_device_id("device-1").tenant_id
        == "hzcu-iee"
    )


def test_ota_rejects_unsafe_device_id_before_doorbell_credentials(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        runtime.doorbell_credential_store = DoorbellCredentialStore(
            tmp_path / "doorbell.db"
        )
        config = _config()
        config["xiaoxin_control"] = {
            "tenant": {"id": "hzcu-iee", "display_name": "信息与电气工程学院"},
            "doorbell_mqtt": {"endpoint": "mqtt.example:1883"},
        }
        handler = OTAHandler(config, runtime)
        response = await handler.handle_post(_ota_request("device/+/1"))
        return response.status, json.loads(response.text), runtime

    status, body, runtime = asyncio.run(scenario())

    assert status == 400
    assert body == {"success": False, "message": "invalid device_id"}
    assert runtime.doorbell_credential_store.list_active() == []


def test_ota_response_contains_disabled_doorbell_mqtt_when_endpoint_missing(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        runtime.doorbell_credential_store = DoorbellCredentialStore(
            tmp_path / "doorbell.db"
        )
        config = _config()
        config["xiaoxin_control"] = {}
        handler = OTAHandler(config, runtime)
        response = await handler.handle_post(_ota_request("device-1"))
        return response.status, json.loads(response.text), runtime

    status, body, runtime = asyncio.run(scenario())

    assert status == 200
    assert body["doorbell_mqtt"] == {
        "version": 1,
        "enabled": False,
        "reason": "doorbell_mqtt_not_configured",
    }
    assert runtime.doorbell_credential_store.get("hzcu-iee", "device-1") is None


def test_ota_observes_public_request_ip(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        runtime.overview_service = RecordingOverviewService()
        _credential, headers = _device_credential_headers(runtime, tmp_path)
        handler = OTAHandler(_config(), runtime)

        response = await handler.handle_post(
            _ota_request(remote="8.8.8.8", headers=headers)
        )

        return response.status, runtime.overview_service.calls

    status, calls = asyncio.run(scenario())

    assert status == 200
    assert calls == [("device-1", "8.8.8.8", "ota")]


def test_ota_logs_do_not_expose_device_credentials_or_complete_headers(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        credential, credential_headers = _device_credential_headers(
            runtime,
            tmp_path,
        )
        runtime.overview_service = RecordingOverviewService()
        handler = OTAHandler(_config(), runtime)
        logger = RecordingLogger()
        handler.logger = logger

        response = await handler.handle_post(
            _ota_request(
                remote="8.8.8.8",
                headers={
                    **credential_headers,
                    "X-Trace-Token": "trace-not-secret",
                },
            )
        )
        return response.status, credential, logger.records

    status, credential, records = asyncio.run(scenario())

    assert status == 200
    serialized = repr(records)
    assert credential.password not in serialized
    assert credential.username not in serialized
    assert f"Bearer {credential.password}" not in serialized
    assert "Authorization" not in serialized
    assert "Device-Username" not in serialized
    assert "X-Trace-Token" not in serialized


def test_ota_does_not_observe_private_request_ip(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        runtime.overview_service = RecordingOverviewService()
        _credential, headers = _device_credential_headers(runtime, tmp_path)
        handler = OTAHandler(_config(), runtime)

        response = await handler.handle_post(
            _ota_request(remote="192.168.1.20", headers=headers)
        )

        return response.status, runtime.overview_service.calls

    status, calls = asyncio.run(scenario())

    assert status == 200
    assert calls == []


def test_ota_observe_device_ip_rejects_non_global_special_use_addresses(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        runtime.overview_service = RecordingOverviewService()
        _credential, headers = _device_credential_headers(runtime, tmp_path)
        handler = OTAHandler(_config(), runtime)

        for address in ("198.18.0.1", "192.0.0.1", "2001:db8::1"):
            response = await handler.handle_post(
                _ota_request(remote=address, headers=headers)
            )
            assert response.status == 200
        return runtime.overview_service.calls

    assert asyncio.run(scenario()) == []


def test_ota_observe_device_ip_accepts_real_global_unicast(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        runtime.overview_service = RecordingOverviewService()
        _credential, headers = _device_credential_headers(runtime, tmp_path)
        handler = OTAHandler(_config(), runtime)

        for address in ("8.8.8.8", "2606:4700:4700::1111"):
            response = await handler.handle_post(
                _ota_request(remote=address, headers=headers)
            )
            assert response.status == 200
        return runtime.overview_service.calls

    assert asyncio.run(scenario()) == [
        ("device-1", "8.8.8.8", "ota"),
        ("device-1", "2606:4700:4700::1111", "ota"),
    ]


def test_ota_uses_forwarded_client_only_from_trusted_proxy_chain(tmp_path):
    async def scenario():
        trusted_runtime = Runtime(tmp_path / "trusted")
        trusted_runtime.overview_service = RecordingOverviewService()
        _credential, trusted_headers = _device_credential_headers(
            trusted_runtime,
            tmp_path / "trusted",
        )
        config = _config()
        config["xiaoxin_control"] = {
            "overview_mqtt": {
                "trusted_proxy_cidrs": ["10.0.0.0/8"],
            }
        }
        trusted_handler = OTAHandler(config, trusted_runtime)
        trusted_response = await trusted_handler.handle_post(
            _ota_request(
                remote="10.0.0.5",
                headers={
                    **trusted_headers,
                    "X-Forwarded-For": "1.1.1.1, 10.0.0.4",
                },
            )
        )

        untrusted_runtime = Runtime(tmp_path / "untrusted")
        untrusted_runtime.overview_service = RecordingOverviewService()
        _credential, untrusted_headers = _device_credential_headers(
            untrusted_runtime,
            tmp_path / "untrusted",
        )
        untrusted_handler = OTAHandler(config, untrusted_runtime)
        untrusted_response = await untrusted_handler.handle_post(
            _ota_request(
                remote="8.8.4.4",
                headers={
                    **untrusted_headers,
                    "X-Forwarded-For": "198.51.100.99",
                },
            )
        )
        return (
            trusted_response.status,
            trusted_runtime.overview_service.calls,
            untrusted_response.status,
            untrusted_runtime.overview_service.calls,
        )

    trusted_status, trusted_calls, untrusted_status, untrusted_calls = asyncio.run(
        scenario()
    )

    assert trusted_status == 200
    assert trusted_calls == [("device-1", "1.1.1.1", "ota")]
    assert untrusted_status == 200
    assert untrusted_calls == [("device-1", "8.8.4.4", "ota")]


def test_ota_rejects_duplicate_forwarded_ip_headers_from_trusted_proxy(tmp_path):
    async def scenario(header_name):
        runtime = Runtime(tmp_path / header_name)
        runtime.overview_service = RecordingOverviewService()
        credential, _headers = _device_credential_headers(
            runtime,
            tmp_path / header_name,
        )
        config = _config()
        config["xiaoxin_control"] = {
            "overview_mqtt": {"trusted_proxy_cidrs": ["10.0.0.0/8"]}
        }
        handler = OTAHandler(config, runtime)
        headers = [
            ("Device-Username", credential.username),
            ("Authorization", f"Bearer {credential.password}"),
            (header_name, "1.1.1.1"),
            (header_name, "8.8.8.8"),
        ]

        response = await handler.handle_post(
            _ota_request(remote="10.0.0.5", headers=headers)
        )
        return response.status, runtime.overview_service.calls

    for header_name in ("X-Forwarded-For", "X-Real-IP"):
        status, calls = asyncio.run(scenario(header_name))
        assert status == 200
        assert calls == []


def test_ota_ip_observation_failure_does_not_break_ota(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        runtime.overview_service = RecordingOverviewService(RuntimeError("provider down"))
        _credential, headers = _device_credential_headers(runtime, tmp_path)
        handler = OTAHandler(_config(), runtime)

        response = await handler.handle_post(
            _ota_request(remote="8.8.8.8", headers=headers)
        )

        return response.status, json.loads(response.text), runtime.overview_service.calls

    status, body, calls = asyncio.run(scenario())

    assert status == 200
    assert body["firmware"]["version"] == "1.0.0"
    assert calls == [("device-1", "8.8.8.8", "ota")]


def test_ota_observation_does_not_overwrite_manual_weather_location(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        user, _ = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        overview_store = XiaoxinOverviewStore(tmp_path / "overview.db")
        overview_store.set_manual_location("device-1", "Shanghai", "Shanghai")
        provider = StaticIpLocationProvider()
        runtime.overview_service = OverviewSyncService(
            identity_store=runtime.identity_store,
            overview_store=overview_store,
            ip_location_provider=provider,
            ip_hmac_key=b"test-hmac-key",
        )
        _credential, headers = _device_credential_headers(runtime, tmp_path)
        handler = OTAHandler(_config(), runtime)

        response = await handler.handle_post(
            _ota_request(remote="8.8.8.8", headers=headers)
        )

        return response.status, overview_store.get_location("device-1"), provider.calls

    status, location, provider_calls = asyncio.run(scenario())

    assert status == 200
    assert provider_calls == ["8.8.8.8"]
    assert location["mode"] == "manual"
    assert location["province"] == "Shanghai"
    assert location["city"] == "Shanghai"
    assert location["automatic_province"] == "Zhejiang"
    assert location["automatic_city"] == "Hangzhou"


def test_ota_skips_observation_without_device_credential(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        runtime.doorbell_credential_store = DoorbellCredentialStore(
            tmp_path / "doorbell.db"
        )
        runtime.overview_service = RecordingOverviewService()
        handler = OTAHandler(_config(), runtime)

        response = await handler.handle_post(_ota_request(remote="8.8.8.8"))

        return response.status, json.loads(response.text), runtime.overview_service.calls

    status, body, calls = asyncio.run(scenario())

    assert status == 200
    assert body["firmware"]["version"] == "1.0.0"
    assert calls == []


def test_ota_skips_observation_for_wrong_device_credential(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        credential, _headers = _device_credential_headers(runtime, tmp_path)
        runtime.overview_service = RecordingOverviewService()
        handler = OTAHandler(_config(), runtime)

        response = await handler.handle_post(
            _ota_request(
                remote="8.8.8.8",
                headers={
                    "Device-Username": credential.username,
                    "Authorization": "Bearer wrong-password",
                },
            )
        )

        return response.status, runtime.overview_service.calls

    status, calls = asyncio.run(scenario())

    assert status == 200
    assert calls == []


def test_ota_failed_request_does_not_observe_before_client_id_validation(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        _credential, headers = _device_credential_headers(runtime, tmp_path)
        runtime.overview_service = RecordingOverviewService()
        handler = OTAHandler(_config(), runtime)
        request = _ota_request(
            client_id=None,
            remote="8.8.8.8",
            headers=headers,
        )

        response = await handler.handle_post(request)

        return json.loads(response.text), runtime.overview_service.calls

    body, calls = asyncio.run(scenario())

    assert body == {"success": False, "message": "request error."}
    assert calls == []


def test_ota_blank_client_id_is_invalid_and_does_not_observe(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        _credential, headers = _device_credential_headers(runtime, tmp_path)
        runtime.overview_service = RecordingOverviewService()
        handler = OTAHandler(_config(), runtime)

        response = await handler.handle_post(
            _ota_request(
                client_id="   ",
                remote="8.8.8.8",
                headers=headers,
            )
        )
        return json.loads(response.text), runtime.overview_service.calls

    body, calls = asyncio.run(scenario())

    assert body == {"success": False, "message": "request error."}
    assert calls == []


def test_ota_invalid_trusted_proxy_cidr_logs_fixed_diagnostic_and_fails_closed(
    tmp_path,
):
    async def scenario():
        runtime = Runtime(tmp_path)
        _credential, credential_headers = _device_credential_headers(
            runtime,
            tmp_path,
        )
        runtime.overview_service = RecordingOverviewService()
        config = _config()
        config["xiaoxin_control"] = {
            "overview_mqtt": {
                "trusted_proxy_cidrs": ["10.0.0.0/8", "invalid-secret-cidr"],
            }
        }
        handler = OTAHandler(config, runtime)
        logger = RecordingLogger()
        handler.logger = logger

        response = await handler.handle_post(
            _ota_request(
                remote="10.0.0.5",
                headers={
                    **credential_headers,
                    "X-Forwarded-For": "1.1.1.1",
                },
            )
        )
        return response.status, runtime.overview_service.calls, logger.warnings

    status, calls, warnings = asyncio.run(scenario())

    assert status == 200
    assert calls == []
    assert warnings == [
        (
            {"tag": "xiaoxin.network"},
            "invalid trusted proxy CIDR ignored",
            (),
        )
    ]
    serialized = repr(warnings)
    assert "invalid-secret-cidr" not in serialized
    assert "X-Forwarded-For" not in serialized
    assert "1.1.1.1" not in serialized
