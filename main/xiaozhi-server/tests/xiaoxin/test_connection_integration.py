import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from queue import Queue
from types import ModuleType, SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import config.config_loader as config_loader


if "opuslib_next" not in sys.modules:
    fake_opuslib = ModuleType("opuslib_next")
    fake_opuslib.APPLICATION_AUDIO = "audio"
    fake_opuslib.constants = SimpleNamespace(
        APPLICATION_AUDIO="audio",
        SIGNAL_VOICE="voice",
    )
    fake_opuslib.OpusError = RuntimeError

    class _FakeEncoder:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, data, frame_size):
            return data

    class _FakeDecoder:
        def __init__(self, *args, **kwargs):
            pass

        def decode(self, data, frame_size):
            return data

    fake_opuslib.Encoder = _FakeEncoder
    fake_opuslib.Decoder = _FakeDecoder
    sys.modules["opuslib_next"] = fake_opuslib


from core.connection import ConnectionHandler
from core.handle.sendAudioHandle import send_tts_message
from core.providers.tts.dto.dto import ContentType, SentenceType
from core.xiaoxin.companion import (
    CompanionSubjectContext,
    CompanionTurnRequest,
    CompanionUnavailableError,
)
from core.xiaoxin.types import XiaoxinConfig


class FakeTTS:
    def __init__(self):
        self.stored = []
        self.tts_text_queue = Queue()

    def store_tts_text(self, sentence_id, text):
        self.stored.append((sentence_id, text))


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


class FakeDialogue:
    def __init__(self):
        self.messages = []

    def put(self, message):
        self.messages.append(message)

    def get_llm_dialogue(self):
        return [
            {"role": message.role, "content": message.content}
            for message in self.messages
            if getattr(message, "content", None)
        ]


class FakeRuntime:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def handle_turn(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class RaisingRuntime:
    def handle_turn(self, **kwargs):
        raise RuntimeError("xiaoxin adapter failed")


class RaisingResolver:
    def resolve_turn_subject(self, device_id, speaker, session_id):
        raise RuntimeError("resolver offline")


class ConfirmedResolver:
    def resolve_turn_subject(self, device_id, speaker, session_id):
        return SimpleNamespace(
            memory_subject_id="ms_subject_1",
            owner_user_id="user-1",
            subject_kind="user_speaker",
        )


class UnknownResolver:
    def resolve_turn_subject(self, device_id, speaker, session_id):
        return SimpleNamespace(
            memory_subject_id="ms_unknown_1",
            owner_user_id="user-1",
            subject_kind="device_unknown",
        )


class CompanionIdentityStore:
    def get_personal_pet_for_user(self, owner_user_id):
        assert owner_user_id == "user-1"
        return SimpleNamespace(id="pet-1", owner_user_id="user-1")

    def get_student_profile_for_user(self, owner_user_id):
        assert owner_user_id == "user-1"
        return {
            "college": "信息与电气工程学院",
            "major": "电子信息工程",
            "class_name": "2501",
            "grade": "大二",
        }


class PrivateProfileMustNotBeRead:
    def __init__(self):
        self.calls = []

    def get_personal_pet_for_user(self, owner_user_id):
        self.calls.append(("pet", owner_user_id))
        return SimpleNamespace(id="pet-1", owner_user_id=owner_user_id)

    def get_student_profile_for_user(self, owner_user_id):
        self.calls.append(("profile", owner_user_id))
        return {"grade": "大二"}


class FakeMemory:
    async def query_memory(self, query):
        return f"memory:{query}"


def make_conn(result):
    cfg = {
        "xiaoxin_runtime": {"enabled": True},
        "exit_commands": [],
        "selected_module": {"Memory": "nomem", "Intent": "nointent"},
        "Memory": {"nomem": {"type": "nomem"}},
        "Intent": {"nointent": {"type": "nointent"}},
    }
    conn = ConnectionHandler(cfg, None, None, None, None, None)
    conn.device_id = "device_1"
    conn.session_id = "session_1"
    conn.current_speaker = None
    conn.tts = FakeTTS()
    conn.dialogue = FakeDialogue()
    conn.xiaoxin_runtime = FakeRuntime(result)
    conn.client_hello_event.set()
    return conn


def test_try_xiaoxin_turn_stores_handled_reply():
    result = SimpleNamespace(
        handled=True,
        reply="我在呢。",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)

    handled = conn._try_xiaoxin_turn("你好", "sentence_1")

    assert handled is True
    assert conn.tts.stored == [("sentence_1", "我在呢。")]
    assert conn.dialogue.messages[-1].role == "assistant"
    assert conn.dialogue.messages[-1].content == "我在呢。"


def test_chat_normalizes_xiaoxin_asr_name_before_dialogue_and_runtime():
    result = SimpleNamespace(
        handled=True,
        reply="\u6211\u662f\u5c0f\u82af\u3002",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)

    handled = conn.chat("\u4f60\u53eb\u5c0f\u65b0\u5417\uff1f")

    assert handled is True
    assert conn.dialogue.messages[0].content == "\u4f60\u53eb\u5c0f\u82af\u5417\uff1f"
    assert conn.xiaoxin_runtime.calls[0]["user_text"] == "\u4f60\u53eb\u5c0f\u82af\u5417\uff1f"


def test_weather_turn_is_not_intercepted_by_overview_snapshot():
    result = SimpleNamespace(
        handled=True,
        reply="天气请求交给工具链处理。",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    conn.xiaoxin_control_runtime = SimpleNamespace(
        overview_store=SimpleNamespace(
            get_snapshot=lambda device_id: SimpleNamespace(
                payload={
                    "weather": {
                        "configured": True,
                        "available": True,
                        "province": "浙江",
                        "city": "杭州",
                        "date": today,
                        "summary": "杭州 · 中雨",
                        "detail": "今日 24～29℃",
                        "fetched_at": f"{today}T07:30:00+08:00",
                    }
                }
            )
        )
    )

    handled = conn._try_xiaoxin_turn("杭州今天天气怎么样", "sentence-weather")

    assert handled is False
    assert conn.xiaoxin_runtime.calls == []
    assert conn.tts.stored == []


def test_try_xiaoxin_turn_bypasses_existing_tool_flow():
    result = SimpleNamespace(handled=False, reply=None, bypass_reason="existing_tool")
    conn = make_conn(result)

    handled = conn._try_xiaoxin_turn("拜拜", "sentence_1")

    assert handled is False
    assert conn.tts.stored == []


def test_try_xiaoxin_turn_stores_system_error_when_runtime_fails(caplog):
    conn = make_conn(SimpleNamespace(handled=False, reply=None))
    conn.config["system_error_response"] = "小芯现在有点忙，我们稍后再试吧。"
    conn.xiaoxin_runtime = RaisingRuntime()

    with caplog.at_level("ERROR"):
        handled = conn._try_xiaoxin_turn("你好", "sentence_1")

    assert handled is True
    assert conn.tts.stored == [("sentence_1", "小芯现在有点忙，我们稍后再试吧。")]
    assert conn.dialogue.messages[-1].role == "assistant"
    assert conn.dialogue.messages[-1].content == "小芯现在有点忙，我们稍后再试吧。"
    assert "Xiaoxin runtime failed" in caplog.text


def test_chat_closes_tts_turn_with_system_error_when_runtime_fails():
    conn = make_conn(SimpleNamespace(handled=False, reply=None))
    conn.config["system_error_response"] = "小芯现在有点忙，我们稍后再试吧。"
    conn.xiaoxin_runtime = RaisingRuntime()

    handled = conn.chat("你好")

    assert handled is True
    first_action = conn.tts.tts_text_queue.get_nowait()
    reply_text = conn.tts.tts_text_queue.get_nowait()
    last_action = conn.tts.tts_text_queue.get_nowait()
    assert first_action.sentence_type == SentenceType.FIRST
    assert reply_text.sentence_type == SentenceType.MIDDLE
    assert reply_text.content_type == ContentType.TEXT
    assert reply_text.content_detail == "小芯现在有点忙，我们稍后再试吧。"
    assert last_action.sentence_type == SentenceType.LAST
    assert conn.tts.stored == [(conn.sentence_id, "小芯现在有点忙，我们稍后再试吧。")]
    assert conn.dialogue.messages[-1].content == "小芯现在有点忙，我们稍后再试吧。"


def test_init_xiaoxin_runtime_reuses_control_runtime_companion_mind(monkeypatch):
    project_dir = Path("D:/runtime-root")
    captured = {}
    companion_mind = object()

    class FakeConstructedRuntime:
        def __init__(self, cfg, companion_mind=None):
            captured["cfg"] = cfg
            captured["companion_mind"] = companion_mind

    runtime_module = ModuleType("core.xiaoxin.runtime")
    runtime_module.XiaoxinRuntime = FakeConstructedRuntime

    monkeypatch.setattr(config_loader, "get_project_dir", lambda: project_dir)
    monkeypatch.setitem(sys.modules, "core.xiaoxin.runtime", runtime_module)

    conn = ConnectionHandler(
        {
            "xiaoxin_runtime": {
                "enabled": True,
                "knowledge_dir": "knowledge-base",
                "companion_db_path": "companion-base/xiaoxin_companion.db",
                "max_tokens": 1024,
            },
            "exit_commands": [],
        },
        None,
        None,
        None,
        None,
        None,
        server=SimpleNamespace(
            xiaoxin_runtime=SimpleNamespace(companion_mind=companion_mind)
        ),
    )

    conn._init_xiaoxin_runtime()

    assert isinstance(conn.xiaoxin_runtime, FakeConstructedRuntime)
    assert captured["companion_mind"] is companion_mind
    assert isinstance(captured["cfg"], XiaoxinConfig)
    assert captured["cfg"] == XiaoxinConfig(
        enabled=True,
        knowledge_dir=project_dir / "knowledge-base",
        companion_db_path=project_dir / "companion-base" / "xiaoxin_companion.db",
        max_tokens=1024,
    )


def test_concurrent_connections_share_process_companion_mind(monkeypatch):
    injected_minds = []
    companion_mind = object()

    class FakeConstructedRuntime:
        def __init__(self, cfg, companion_mind=None):
            injected_minds.append(companion_mind)

    runtime_module = ModuleType("core.xiaoxin.runtime")
    runtime_module.XiaoxinRuntime = FakeConstructedRuntime
    monkeypatch.setitem(sys.modules, "core.xiaoxin.runtime", runtime_module)

    server = SimpleNamespace(
        xiaoxin_runtime=SimpleNamespace(companion_mind=companion_mind)
    )
    connections = [
        ConnectionHandler(
            {
                "xiaoxin_runtime": {"enabled": True},
                "exit_commands": [],
            },
            None,
            None,
            None,
            None,
            None,
            server=server,
        )
        for _ in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(ConnectionHandler._init_xiaoxin_runtime, connections))

    assert injected_minds == [companion_mind, companion_mind]


def test_init_xiaoxin_runtime_without_control_runtime_disables_private_persistence(
    monkeypatch,
):
    captured = {}

    class FakeConstructedRuntime:
        def __init__(self, cfg, companion_mind=None):
            captured["companion_mind"] = companion_mind

    runtime_module = ModuleType("core.xiaoxin.runtime")
    runtime_module.XiaoxinRuntime = FakeConstructedRuntime
    monkeypatch.setitem(sys.modules, "core.xiaoxin.runtime", runtime_module)

    conn = ConnectionHandler(
        {
            "xiaoxin_runtime": {"enabled": True},
            "exit_commands": [],
        },
        None,
        None,
        None,
        None,
        None,
    )

    conn._init_xiaoxin_runtime()

    mind = captured["companion_mind"]
    anonymous = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-unknown",
            subject=CompanionSubjectContext(
                owner_user_id="anonymous-owner",
                pet_id="anonymous-pet",
                memory_subject_id="anonymous-subject",
                speaker_identity="unknown",
                academic_stage="unknown",
                persistence_allowed=False,
            ),
            request_digest="digest",
            surface="voice",
            occurred_at="2026-07-20T12:00:00+08:00",
        )
    )
    assert anonymous.persistence_allowed is False

    with pytest.raises(CompanionUnavailableError):
        mind.prepare_turn(
            CompanionTurnRequest(
                turn_id="turn-confirmed",
                subject=CompanionSubjectContext(
                    owner_user_id="owner-1",
                    pet_id="pet-1",
                    memory_subject_id="subject-1",
                    speaker_identity="confirmed",
                    academic_stage="freshman",
                    persistence_allowed=True,
                ),
                request_digest="digest",
                surface="voice",
                occurred_at="2026-07-20T12:00:00+08:00",
            )
        )


def test_init_xiaoxin_runtime_leaves_none_when_disabled():
    conn = ConnectionHandler(
        {
            "xiaoxin_runtime": {"enabled": False},
            "exit_commands": [],
        },
        None,
        None,
        None,
        None,
        None,
    )
    conn.xiaoxin_runtime = object()

    conn._init_xiaoxin_runtime()

    assert conn.xiaoxin_runtime is None


def test_chat_queues_last_action_when_xiaoxin_handles_top_level_turn():
    result = SimpleNamespace(
        handled=True,
        reply="收到啦。",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)

    handled = conn.chat("你好")

    assert handled is True
    first_action = conn.tts.tts_text_queue.get_nowait()
    reply_text = conn.tts.tts_text_queue.get_nowait()
    last_action = conn.tts.tts_text_queue.get_nowait()
    assert first_action.sentence_type == SentenceType.FIRST
    assert first_action.content_type == ContentType.ACTION
    assert reply_text.sentence_type == SentenceType.MIDDLE
    assert reply_text.content_type == ContentType.TEXT
    assert reply_text.content_detail == "收到啦。"
    assert last_action.sentence_type == SentenceType.LAST
    assert last_action.content_type == ContentType.ACTION


def test_chat_does_not_pass_current_user_turn_twice_to_xiaoxin():
    result = SimpleNamespace(
        handled=True,
        reply="\u6536\u5230\u4e86",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)

    handled = conn.chat("\u4f60\u597d")

    assert handled is True
    assert conn.xiaoxin_runtime.calls[0]["history"] == []


def test_chat_passes_device_time_snapshot_to_xiaoxin_runtime():
    result = SimpleNamespace(
        handled=True,
        reply="\u6536\u5230\u4e86",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)
    conn.device_time_snapshot = {
        "wall_time_ms": 1783129240000,
        "sync_status": "synced",
        "timezone": "Asia/Shanghai",
        "source": "sntp",
        "received_at_ms": 1783129245250,
    }

    handled = conn.chat("\u4f60\u597d")

    assert handled is True
    assert conn.xiaoxin_runtime.calls[0]["device_time_snapshot"] == conn.device_time_snapshot


def test_chat_continues_old_flow_without_memory_for_existing_tool():
    class RecordingMemory:
        def __init__(self):
            self.calls = []

        async def query_memory(self, query):
            self.calls.append(query)
            return f"memory:{query}"

    result = SimpleNamespace(handled=False, reply=None, bypass_reason="existing_tool")
    conn = make_conn(result)
    memory = RecordingMemory()
    conn.memory = memory
    conn.loop = object()

    assert conn.chat("拜拜") is None

    assert conn.xiaoxin_runtime.calls == []
    assert memory.calls == []
    assert conn.dialogue.messages[0].role == "user"
    assert conn.dialogue.messages[0].content == "拜拜"
    first_action = conn.tts.tts_text_queue.get_nowait()
    assert first_action.sentence_type == SentenceType.FIRST
    assert first_action.content_type == ContentType.ACTION


def test_control_console_tts_sends_start_before_queueing_audio_work():
    conn = make_conn(SimpleNamespace(handled=False))
    conn.websocket = FakeWebSocket()

    result = asyncio.run(
        conn.speak_from_control_console(
            "小芯提醒你，记得喝水。", "del-1", "sentence-fixed"
        )
    )

    sent_messages = [json.loads(item) for item in conn.websocket.sent]
    assert sent_messages == [
        {
            "type": "tts",
            "state": "start",
            "session_id": conn.session_id,
            "sentence_id": "sentence-fixed",
        }
    ]
    assert result is None
    assert conn.client_is_speaking is True
    assert conn.xiaoxin_control_tts_deliveries == {"sentence-fixed": "del-1"}
    first_action = conn.tts.tts_text_queue.get_nowait()
    assert first_action.sentence_id == "sentence-fixed"
    assert first_action.sentence_type == SentenceType.FIRST
    

def test_try_xiaoxin_turn_falls_back_to_anonymous_companion_when_resolver_fails(caplog):
    result = SimpleNamespace(
        handled=True,
        reply="收到了",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)
    conn.device_id = "device id/1"
    conn.current_speaker = "speaker-a"
    conn.xiaoxin_control_runtime = SimpleNamespace(identity_resolver=RaisingResolver())

    with caplog.at_level("ERROR"):
        handled = conn._try_xiaoxin_turn("你好", "sentence_1")

    assert handled is True
    assert conn.xiaoxin_runtime.calls[0]["companion_subject_context"] is None
    assert "memory_scope" not in conn.xiaoxin_runtime.calls[0]
    assert "disable_memory_persistence" not in conn.xiaoxin_runtime.calls[0]
    assert conn.dialogue.messages[-1].content == "收到了"
    assert "Xiaoxin identity resolution failed" in caplog.text


def test_try_xiaoxin_turn_passes_subject_context_and_stable_turn_id():
    result = SimpleNamespace(
        handled=True,
        reply="收到了",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)
    conn.xiaoxin_control_runtime = SimpleNamespace(
        identity_resolver=ConfirmedResolver(),
        identity_store=CompanionIdentityStore(),
    )

    handled = conn._try_xiaoxin_turn("你好", "sentence-stable-1")

    call = conn.xiaoxin_runtime.calls[0]
    assert handled is True
    assert call["turn_id"] == "sentence-stable-1"
    assert "memory_scope" not in call
    assert "memory_subject_context" not in call
    assert call["companion_subject_context"] is conn.companion_subject_context
    assert call["trusted_student_profile"] == {
        "college": "信息与电气工程学院",
        "major": "电子信息工程",
        "class_name": "2501",
        "grade": "大二",
    }
    assert conn.companion_subject_context.owner_user_id == "user-1"
    assert conn.companion_subject_context.pet_id == "pet-1"
    assert conn.companion_subject_context.memory_subject_id == "ms_subject_1"
    assert conn.companion_subject_context.academic_stage == "sophomore"
    assert conn.companion_subject_context.speaker_identity == "confirmed"
    assert conn.companion_subject_context.persistence_allowed is True


def test_unknown_speaker_does_not_read_owner_pet_or_student_profile():
    result = SimpleNamespace(
        handled=True,
        reply="收到了",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)
    conn.current_speaker = "未知说话人"
    identity_store = PrivateProfileMustNotBeRead()
    conn.xiaoxin_control_runtime = SimpleNamespace(
        identity_resolver=UnknownResolver(),
        identity_store=identity_store,
    )

    handled = conn._try_xiaoxin_turn("你好", "sentence-unknown-1")

    call = conn.xiaoxin_runtime.calls[0]
    assert handled is True
    assert "memory_subject_context" not in call
    assert call["companion_subject_context"] is None
    assert call["trusted_student_profile"] is None
    assert identity_store.calls == []


def test_submit_control_text_chat_clears_audio_state_and_calls_chat(monkeypatch):
    result = SimpleNamespace(
        handled=True,
        reply="鏀跺埌",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)
    conn.client_have_voice = True
    conn.client_voice_stop = True
    conn.client_abort = True
    conn.client_audio_buffer.extend(b"old-buffer")
    conn.client_voice_window.append(True)
    conn.last_is_voice = True
    conn.vad_last_voice_time = 123.456
    conn.asr_audio.append(b"old-audio")
    calls = []

    def fake_chat(text, *, sentence_id=None):
        assert conn.client_have_voice is False
        assert conn.client_voice_stop is False
        assert conn.client_audio_buffer == bytearray()
        assert list(conn.client_voice_window) == []
        assert conn.last_is_voice is False
        assert conn.vad_last_voice_time == 0.0
        assert conn.asr_audio == []
        assert conn.client_abort is False
        calls.append(text)
        return True

    monkeypatch.setattr(conn, "chat", fake_chat)

    asyncio.run(conn.submit_control_text_chat("  浣犺兘鍚埌鎴戝悧锛? "))

    assert calls == ["浣犺兘鍚埌鎴戝悧锛?"]
    assert conn.client_have_voice is False
    assert conn.client_voice_stop is False
    assert conn.asr_audio == []
    assert conn.client_abort is False


def test_submit_control_text_chat_restores_time_provider_after_simulated_turn(
    monkeypatch,
):
    conn = make_conn(SimpleNamespace(handled=False))
    original_time_provider = lambda: datetime(2026, 7, 30, 16, 40)
    simulated_as_of = datetime.fromisoformat("2026-10-28T16:40:58+08:00")
    conn.xiaoxin_runtime.time_provider = original_time_provider
    observed = []

    def fake_chat(text, *, sentence_id=None):
        observed.append(conn.xiaoxin_runtime.time_provider())

    monkeypatch.setattr(conn, "chat", fake_chat)

    asyncio.run(
        conn.submit_control_text_chat(
            "D90 recall", simulated_as_of=simulated_as_of
        )
    )

    assert observed == [simulated_as_of]
    assert conn.xiaoxin_runtime.time_provider is original_time_provider


def test_submit_control_text_chat_rejects_empty_text():
    conn = make_conn(SimpleNamespace(handled=False))

    with pytest.raises(ValueError, match="text is empty"):
        asyncio.run(conn.submit_control_text_chat("   "))


def test_submit_control_text_chat_waits_for_hello_and_mcp_ready_before_chat(
    monkeypatch,
):
    conn = make_conn(SimpleNamespace(handled=False))
    features = {
        "mcp": True,
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn.features = None
    conn.client_hello_event.clear()
    events = []

    async def wait_until_mcp_ready(*, timeout_seconds):
        events.append(("mcp", timeout_seconds))

    async def wait_until_ready(*, timeout_seconds):
        events.append(("idle", timeout_seconds))

    async def quiesce():
        events.append(("quiesce", None))

    async def start_reliable_tts(sentence_id, *, delivery_id=None):
        events.append(("ready", sentence_id))

    def fake_chat(text, *, sentence_id=None):
        events.append(("chat", sentence_id))

    monkeypatch.setattr(conn, "_wait_until_tts_ready", wait_until_ready)
    monkeypatch.setattr(conn, "_quiesce_audio_for_reliable_tts", quiesce)
    monkeypatch.setattr(conn, "_start_reliable_tts", start_reliable_tts)
    monkeypatch.setattr(conn, "chat", fake_chat)

    async def scenario():
        submission = asyncio.create_task(
            conn.submit_control_text_chat("remember my test code")
        )
        await asyncio.sleep(0)
        assert events == []
        conn.features = features
        conn.mcp_client = SimpleNamespace(wait_until_ready=wait_until_mcp_ready)
        conn.client_hello_event.set()
        await submission

    asyncio.run(scenario())

    sentence_id = conn.control_text_chat_sentence_id
    assert events == [
        ("mcp", 8),
        ("idle", 5),
        ("quiesce", None),
        ("ready", sentence_id),
        ("chat", sentence_id),
    ]


def test_submit_control_text_chat_starts_done_timeout_only_after_stop(monkeypatch):
    conn = make_conn(SimpleNamespace(handled=False))
    conn.features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn.config["tts_done_ack_timeout_ms"] = 20
    sender_futures = []

    async def start_reliable_tts(sentence_id, *, delivery_id=None):
        conn.begin_tts_ack_wait("ready", sentence_id)
        assert conn.resolve_tts_ack("ready", sentence_id) is True
        assert conn.mark_tts_streaming(sentence_id) is True

    async def wait_for_audio_completion(conn_arg):
        await asyncio.sleep(0.03)

    async def send_message(payload):
        message = json.loads(payload)
        if message.get("state") == "stop":
            conn.loop.call_later(
                0.002,
                conn.resolve_tts_ack,
                "done",
                message["sentence_id"],
            )

    def fake_chat(text, *, sentence_id=None):
        conn.sentence_id = sentence_id
        sender_futures.append(
            asyncio.run_coroutine_threadsafe(
                send_tts_message(conn, "stop", sentence_id=sentence_id),
                conn.loop,
            )
        )

    conn.websocket = SimpleNamespace(send=send_message)
    monkeypatch.setattr(
        conn,
        "_wait_until_tts_ready",
        lambda **kwargs: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        conn,
        "_quiesce_audio_for_reliable_tts",
        lambda: asyncio.sleep(0),
    )
    monkeypatch.setattr(conn, "_start_reliable_tts", start_reliable_tts)
    monkeypatch.setattr(conn, "chat", fake_chat)
    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        wait_for_audio_completion,
    )

    async def scenario():
        conn.loop = asyncio.get_running_loop()
        result = await conn.submit_control_text_chat(
            "请给我一段足够长的回复",
            await_tts_terminal=True,
        )
        await asyncio.wrap_future(sender_futures[0])
        return result

    result = asyncio.run(scenario())

    assert result.tts_outcome == "done"
    assert result.tts_reason is None


def test_submit_control_text_chat_rejects_text_over_500_chars():
    conn = make_conn(SimpleNamespace(handled=False))

    with pytest.raises(ValueError, match="text is too long"):
        asyncio.run(conn.submit_control_text_chat("a" * 501))


def test_submit_control_text_chat_rejects_concurrent_submission_before_interleaving(monkeypatch):
    conn = make_conn(SimpleNamespace(handled=False))
    gate = asyncio.Event()
    release = asyncio.Event()
    calls = []

    def fake_chat(text, *, sentence_id=None):
        calls.append(text)
        conn.loop.call_soon_threadsafe(gate.set)
        future = asyncio.run_coroutine_threadsafe(release.wait(), conn.loop)
        future.result(timeout=1)
        return True

    monkeypatch.setattr(conn, "chat", fake_chat)

    async def scenario():
        conn.loop = asyncio.get_running_loop()
        first = asyncio.create_task(conn.submit_control_text_chat("first"))
        await gate.wait()
        with pytest.raises(RuntimeError, match="text chat busy"):
            await conn.submit_control_text_chat("second")
        release.set()
        await first

    asyncio.run(scenario())

    assert calls == ["first"]
