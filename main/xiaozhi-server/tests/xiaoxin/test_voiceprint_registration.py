import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import make_mocked_request

from core.api.xiaoxin_control_handler import XiaoxinControlHandler
from core.utils.voiceprint_provider import VoiceprintProvider
from core.xiaoxin.activation_store import XiaoxinActivationStore
from core.xiaoxin.identity.auth import XiaoxinAuthService
from core.xiaoxin.identity.models import SPEAKER_ARCHIVED, SPEAKER_CONFIRMED
from core.xiaoxin.identity.store import XiaoxinIdentityStore
from core.xiaoxin.voiceprint_registration import (
    VoiceprintRegistrationError,
    VoiceprintRegistrar,
    voiceprint_speaker_id,
)


class Runtime:
    def __init__(self, tmp_path):
        self.identity_store = XiaoxinIdentityStore(tmp_path / "control.db")
        self.activation_store = XiaoxinActivationStore(tmp_path / "activation.db")
        self.auth_service = XiaoxinAuthService(self.identity_store)


def _request(method, path, token, *, content_type="application/json"):
    request = make_mocked_request(
        method,
        path,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
    )
    request._transport_peername = ("127.0.0.1", 12345)
    return request


async def _session(handler, openid="voiceprint-openid"):
    request = make_mocked_request(
        "POST",
        "/api/miniprogram/session",
        headers={"Content-Type": "application/json"},
    )
    request._read_bytes = json.dumps({"openid": openid}).encode()
    response = await handler.handle_miniprogram_session(request)
    return json.loads(response.text)["token"]


def test_voiceprint_speaker_id_is_stable_and_does_not_expose_owner_ids():
    first = voiceprint_speaker_id("user-1", "pet-1")
    second = voiceprint_speaker_id("user-1", "pet-1")

    assert first == second
    assert first.startswith("xiaoxin_")
    assert "user-1" not in first
    assert "pet-1" not in first
    assert first != voiceprint_speaker_id("user-2", "pet-1")


def test_registrar_requires_self_hosted_health_url_with_key():
    assert not VoiceprintRegistrar({}).configured
    assert not VoiceprintRegistrar({"url": "http://127.0.0.1:8005/voiceprint/health"}).configured
    assert VoiceprintRegistrar(
        {"url": "http://voiceprint.internal:8005/voiceprint/health?key=test"}
    ).configured


def test_unconfigured_registration_fails_closed():
    async def scenario():
        try:
            await VoiceprintRegistrar({}).register(
                speaker_id="xiaoxin_test",
                audio=b"RIFF",
            )
        except RuntimeError as exc:
            return str(exc)
        raise AssertionError("registration should fail when provider is not configured")

    assert asyncio.run(scenario()) == "voiceprint service is not configured"


def test_registrar_health_uses_provider_health_contract():
    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def json(self, content_type=None):
            return {"status": "healthy", "total_voiceprints": 1}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def get(self, url):
            assert url == "http://voiceprint.test:8005/voiceprint/health?key=secret"
            return Response()

    async def scenario():
        registrar = VoiceprintRegistrar(
            {"url": "http://voiceprint.test:8005/voiceprint/health?key=secret"}
        )
        with patch(
            "core.xiaoxin.voiceprint_registration.ClientSession",
            return_value=Session(),
        ):
            return await registrar.check_health()

    assert asyncio.run(scenario()) is True


def test_registrar_accepts_voiceprint_api_success_response():
    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def json(self, content_type=None):
            return {"success": True, "msg": "已登记: xiaoxin_test"}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, **kwargs):
            assert url == "http://voiceprint.test:8005/voiceprint/register"
            assert kwargs["headers"] == {"Authorization": "Bearer secret"}
            return Response()

    async def scenario():
        registrar = VoiceprintRegistrar(
            {"url": "http://voiceprint.test:8005/voiceprint/health?key=secret"}
        )
        with patch(
            "core.xiaoxin.voiceprint_registration.ClientSession",
            return_value=Session(),
        ):
            return await registrar.register(
                speaker_id="xiaoxin_test",
                audio=b"RIFF\x00\x00\x00\x00WAVE",
            )

    result = asyncio.run(scenario())
    assert result.speaker_id == "xiaoxin_test"
    assert result.provider_response["success"] is True


def test_registrar_checks_provider_success_flag_even_for_http_200():
    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def json(self, content_type=None):
            return {"success": False, "msg": "embedding failed"}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, **kwargs):
            assert url == "http://voiceprint.test:8005/voiceprint/register"
            assert kwargs["headers"] == {"Authorization": "Bearer secret"}
            return Response()

    async def scenario():
        registrar = VoiceprintRegistrar(
            {"url": "http://voiceprint.test:8005/voiceprint/health?key=secret"}
        )
        with patch(
            "core.xiaoxin.voiceprint_registration.ClientSession",
            return_value=Session(),
        ):
            await registrar.register(
                speaker_id="xiaoxin_test",
                audio=b"RIFF\x00\x00\x00\x00WAVE",
            )

    try:
        asyncio.run(scenario())
    except VoiceprintRegistrationError as exc:
        assert str(exc) == "voiceprint provider rejected the audio"
    else:
        raise AssertionError("HTTP 200 with success=false must fail closed")


def test_provider_default_accepts_match_above_upstream_threshold():
    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def json(self):
            return {"speaker_id": "xiaoxin_owner", "score": 0.294}

    class Session:
        def __init__(self, **kwargs):
            self.timeout = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, **kwargs):
            assert url == "http://voiceprint.test:8005/voiceprint/identify"
            assert kwargs["headers"] == {
                "Authorization": "Bearer secret",
                "Accept": "application/json",
            }
            return Response()

    config = {
        "url": "http://voiceprint.test:8005/voiceprint/health?key=secret",
        "speakers": [],
    }
    with patch.object(VoiceprintProvider, "_check_server_health", return_value=True):
        provider = VoiceprintProvider(
            config,
            speaker_resolver=lambda _device_id: [("xiaoxin_owner", "主人")],
        )

    with patch("core.utils.voiceprint_provider.aiohttp.ClientSession", Session):
        result = asyncio.run(
            provider.identify_speaker(
                b"RIFF\x00\x00\x00\x00WAVE",
                "session-1",
                device_id="device-1",
            )
        )

    assert result == "voiceprint:xiaoxin_owner"


def test_dynamic_voiceprint_candidates_do_not_fall_back_to_static_speakers():
    config = {
        "url": "http://voiceprint.test:8005/voiceprint/health?key=secret",
        "speakers": ["test1,测试用户,legacy static speaker"],
    }
    with patch.object(VoiceprintProvider, "_check_server_health", return_value=True):
        provider = VoiceprintProvider(config, speaker_resolver=lambda _device_id: [])

    result = asyncio.run(
        provider.identify_speaker(b"audio", "session-1", device_id="device-1")
    )

    assert result == "未知说话人"


def test_dynamic_voiceprint_candidate_errors_fail_closed():
    def broken_resolver(_device_id):
        raise RuntimeError("identity database unavailable")

    config = {
        "url": "http://voiceprint.test:8005/voiceprint/health?key=secret",
        "speakers": ["test1,测试用户,legacy static speaker"],
    }
    with patch.object(VoiceprintProvider, "_check_server_health", return_value=True):
        provider = VoiceprintProvider(config, speaker_resolver=broken_resolver)

    result = asyncio.run(
        provider.identify_speaker(b"audio", "session-1", device_id="device-1")
    )

    assert result == "未知说话人"


def test_miniprogram_voiceprint_status_requires_binding(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        handler = XiaoxinControlHandler({"voiceprint": {}}, runtime)
        token = await _session(handler)
        response = await handler.handle_miniprogram_voiceprint(
            _request("GET", "/api/miniprogram/voiceprint", token)
        )
        return response.status, json.loads(response.text)

    status, body = asyncio.run(scenario())
    assert status == 200
    assert body["voiceprint"] == {
        "configured": False,
        "available": False,
        "bound": False,
        "enrolled": False,
        "status": "device_required",
    }


def test_miniprogram_accepts_valid_wav_with_platform_content_type(tmp_path):
    audio = b"RIFF\x00\x00\x00\x00WAVE-owner-audio"

    class Field:
        name = "audio"
        headers = {"Content-Type": "audio/wave"}

        def __init__(self):
            self._chunks = [audio]

        async def read_chunk(self, size=0):
            return self._chunks.pop(0) if self._chunks else b""

        async def release(self):
            return None

    class Reader:
        def __init__(self):
            self._fields = [Field()]

        async def next(self):
            return self._fields.pop(0) if self._fields else None

    async def scenario():
        handler = XiaoxinControlHandler({"voiceprint": {}}, Runtime(tmp_path))
        request = _request(
            "POST",
            "/api/miniprogram/voiceprint",
            "test-token",
            content_type="multipart/form-data; boundary=test",
        )
        request.multipart = AsyncMock(return_value=Reader())
        return await handler._read_miniprogram_voiceprint_audio(request)

    assert asyncio.run(scenario()) == (audio, "voiceprint.wav", "audio/wav")


def test_miniprogram_rejects_non_wav_content_despite_platform_content_type(tmp_path):
    class Field:
        name = "audio"
        headers = {"Content-Type": "audio/wave"}

        def __init__(self):
            self._chunks = [b"not-a-wave-file"]

        async def read_chunk(self, size=0):
            return self._chunks.pop(0) if self._chunks else b""

        async def release(self):
            return None

    class Reader:
        def __init__(self):
            self._fields = [Field()]

        async def next(self):
            return self._fields.pop(0) if self._fields else None

    async def scenario():
        handler = XiaoxinControlHandler({"voiceprint": {}}, Runtime(tmp_path))
        request = _request(
            "POST",
            "/api/miniprogram/voiceprint",
            "test-token",
            content_type="multipart/form-data; boundary=test",
        )
        request.multipart = AsyncMock(return_value=Reader())
        await handler._read_miniprogram_voiceprint_audio(request)

    with pytest.raises(ValueError, match="voiceprint audio must be a valid WAV file"):
        asyncio.run(scenario())


def test_miniprogram_voiceprint_registration_is_bound_to_logged_in_user(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        handler = XiaoxinControlHandler(
            {"voiceprint": {"url": "http://voiceprint.test/voiceprint/health?key=test"}},
            runtime,
        )
        registrar = type("Registrar", (), {"configured": True})()
        registrar.register = AsyncMock()
        handler.voiceprint_registrar = registrar
        token = await _session(handler)
        user = runtime.auth_service.user_for_token(token)
        device_id = "device-voiceprint"
        runtime.identity_store.upsert_seen_device(device_id)
        runtime.identity_store.bind_device(device_id, user.id, "我的小芯")

        class Field:
            name = "audio"
            filename = "owner.wav"
            headers = {"Content-Type": "audio/wav"}

            def __init__(self):
                self._chunks = [b"RIFF\x00\x00\x00\x00WAVE-owner-audio"]

            async def read_chunk(self, size=0):
                return self._chunks.pop(0) if self._chunks else b""

            async def release(self):
                return None

        class Reader:
            def __init__(self):
                self._fields = [Field()]

            async def next(self):
                return self._fields.pop(0) if self._fields else None

        request = _request(
            "POST",
            "/api/miniprogram/voiceprint",
            token,
            content_type="multipart/form-data; boundary=test",
        )
        request.multipart = AsyncMock(return_value=Reader())
        pet = runtime.identity_store.get_personal_pet_for_user(user.id)
        speaker_id = voiceprint_speaker_id(user.id, pet.id)
        archived = runtime.identity_store.get_or_create_speaker_profile(
            owner_user_id=user.id,
            device_id=device_id,
            speaker_key=speaker_id,
            display_name="主人",
        )
        assert runtime.identity_store.archive_speaker(archived.id, user.id)
        response = await handler.handle_miniprogram_voiceprint_register(request)
        profiles = runtime.identity_store.list_speakers_for_device(user.id, device_id)
        return response.status, json.loads(response.text), registrar, user, profiles

    status, body, registrar, user, profiles = asyncio.run(scenario())
    assert status == 200
    assert body["voiceprint"]["enrolled"] is True
    registrar.register.assert_awaited_once()
    assert registrar.register.await_args.kwargs["speaker_id"].startswith("xiaoxin_")
    assert registrar.register.await_args.kwargs["audio"] == b"RIFF\x00\x00\x00\x00WAVE-owner-audio"
    assert registrar.register.await_args.kwargs["filename"] == "voiceprint.wav"
    assert len(profiles) == 1
    assert profiles[0].status == SPEAKER_CONFIRMED


def test_unconfigured_provider_never_reports_active_status(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        handler = XiaoxinControlHandler({"voiceprint": {}}, runtime)
        token = await _session(handler)
        user = runtime.auth_service.user_for_token(token)
        device_id = "device-unconfigured-voiceprint"
        runtime.identity_store.upsert_seen_device(device_id)
        runtime.identity_store.bind_device(device_id, user.id, "我的小芯")
        pet = runtime.identity_store.get_personal_pet_for_user(user.id)
        runtime.identity_store.get_or_create_speaker_profile(
            owner_user_id=user.id,
            device_id=device_id,
            speaker_key=voiceprint_speaker_id(user.id, pet.id),
            display_name="主人",
        )
        response = await handler.handle_miniprogram_voiceprint(
            _request("GET", "/api/miniprogram/voiceprint", token)
        )
        return json.loads(response.text)["voiceprint"]

    voiceprint = asyncio.run(scenario())
    assert voiceprint["configured"] is False
    assert voiceprint["available"] is False
    assert voiceprint["enrolled"] is True
    assert voiceprint["status"] == "unconfigured"


def test_configured_but_unhealthy_provider_is_not_available(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        handler = XiaoxinControlHandler(
            {"voiceprint": {"url": "http://voiceprint.test/voiceprint/health?key=test"}},
            runtime,
        )
        token = await _session(handler)
        user = runtime.auth_service.user_for_token(token)
        device_id = "device-unhealthy-voiceprint"
        runtime.identity_store.upsert_seen_device(device_id)
        runtime.identity_store.bind_device(device_id, user.id, "我的小芯")
        with patch.object(
            handler.voiceprint_registrar,
            "check_health",
            AsyncMock(return_value=False),
        ):
            response = await handler.handle_miniprogram_voiceprint(
                _request("GET", "/api/miniprogram/voiceprint", token)
            )
        return json.loads(response.text)["voiceprint"]

    voiceprint = asyncio.run(scenario())
    assert voiceprint["configured"] is True
    assert voiceprint["available"] is False
    assert voiceprint["status"] == "unavailable"


def test_archived_speaker_is_not_reactivated_by_normal_resolution(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "identity.db")
    user = store.create_user("archive-protection", "hash", "测试用户")
    store.upsert_seen_device("device-1")
    store.bind_device("device-1", user.id, "我的小芯")
    profile = store.get_or_create_speaker_profile(
        owner_user_id=user.id,
        device_id="device-1",
        speaker_key="xiaoxin_owner",
        display_name="主人",
    )
    assert store.archive_speaker(profile.id, user.id)

    resolved_again = store.get_or_create_speaker_profile(
        owner_user_id=user.id,
        device_id="device-1",
        speaker_key="xiaoxin_owner",
        display_name="主人",
    )

    assert resolved_again.status == SPEAKER_ARCHIVED
