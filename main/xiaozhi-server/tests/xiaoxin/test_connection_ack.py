import json
import asyncio
import queue
import sys
import types
from types import SimpleNamespace

import pytest

if "opuslib_next" not in sys.modules:
    opuslib_next = types.ModuleType("opuslib_next")

    class _FakeEncoder:
        def __init__(self, *args, **kwargs):
            self.bitrate = None
            self.complexity = None
            self.signal = None

        def reset_state(self):
            return None

        def encode(self, frame_bytes, frame_size):
            return frame_bytes

    opuslib_next.Encoder = _FakeEncoder
    opuslib_next.constants = SimpleNamespace(
        APPLICATION_AUDIO=2049,
        SIGNAL_VOICE=3001,
    )
    sys.modules["opuslib_next"] = opuslib_next

from core.connection import ConnectionHandler
from core.handle.helloHandle import handleHelloMessage
from core.handle.sendAudioHandle import (
    _do_send_audio,
    sendAudioMessage,
    send_tts_message,
)
from core.handle.textHandler.xiaoxinAckMessageHandler import XiaoxinAckMessageHandler
from core.handle.textMessageType import TextMessageType
from core.providers.tts.dto.dto import SentenceType
from core.xiaoxin.tts_delivery import TtsAckResult, TtsAttemptError
from core.xiaoxin.registry import XiaoxinDeviceRegistry
from core.utils.audioRateController import AudioRateController


class FakeDispatcher:
    def __init__(self):
        self.acks = []
        self.done = []
        self.failed = []
        self.legacy_unverified = []

    async def handle_ack(self, device_id, ack, conn):
        self.acks.append((device_id, ack, conn))

    def mark_tts_done(self, delivery_id, sentence_id):
        self.done.append((delivery_id, sentence_id))

    def mark_tts_attempt_failed(self, delivery_id, sentence_id, reason):
        self.failed.append((delivery_id, sentence_id, reason))

    def mark_tts_legacy_unverified(self, delivery_id, sentence_id):
        self.legacy_unverified.append((delivery_id, sentence_id))


class FakeRegistry:
    def __init__(self):
        self.registered = []
        self.unregistered = []

    def register_connection(self, device_id, conn, transport):
        self.registered.append((device_id, conn, transport))

    def unregister_connection(self, device_id, conn):
        self.unregistered.append((device_id, conn))


class FakeRuntime:
    def __init__(self):
        self.dispatcher = FakeDispatcher()
        self.registry = FakeRegistry()
        self.todo_reminder_tts_done = []

    def observe_todo_reminder_tts_done(self, delivery_id, sentence_id):
        self.todo_reminder_tts_done.append((delivery_id, sentence_id))


class FakeServer:
    def __init__(self):
        self.xiaoxin_runtime = FakeRuntime()


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


def test_connection_records_todo_reminder_tts_outcome_after_dispatcher_completion():
    conn = ConnectionHandler.__new__(ConnectionHandler)
    conn.xiaoxin_control_runtime = FakeRuntime()
    conn.xiaoxin_control_tts_deliveries = {"sentence-1": "delivery-1"}

    conn.mark_xiaoxin_control_tts_done("sentence-1")

    assert conn.xiaoxin_control_runtime.dispatcher.done == [
        ("delivery-1", "sentence-1")
    ]
    assert conn.xiaoxin_control_runtime.todo_reminder_tts_done == [
        ("delivery-1", "sentence-1")
    ]


class FakeConn:
    def __init__(self):
        self.device_id = "aa"
        self.server = FakeServer()
        self.websocket = FakeWebSocket()
        self.session_id = "session-1"
        self.config = {"enable_stop_tts_notify": False}
        self.xiaoxin_control_tts_deliveries = {}
        self.tts = SimpleNamespace(tts_audio_first_sentence=False)
        self.logger = SimpleNamespace(
            bind=lambda **kwargs: SimpleNamespace(info=lambda *args, **kw: None)
        )
        self.calling = False
        self.close_after_chat = False
        self.client_is_speaking = True
        self.done_calls = []
        self.closed = False
        self.sentence_id = None

    def mark_xiaoxin_control_tts_done(self, sentence_id):
        self.done_calls.append(sentence_id)
        delivery_id = self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
        if delivery_id:
            self.server.xiaoxin_runtime.dispatcher.mark_tts_done(
                delivery_id, sentence_id
            )

    def mark_xiaoxin_control_tts_failed(self, sentence_id, reason):
        delivery_id = self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
        if delivery_id:
            self.server.xiaoxin_runtime.dispatcher.mark_tts_attempt_failed(
                delivery_id, sentence_id, reason
            )

    def mark_xiaoxin_control_tts_legacy_unverified(self, sentence_id):
        delivery_id = self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
        if delivery_id:
            self.server.xiaoxin_runtime.dispatcher.mark_tts_legacy_unverified(
                delivery_id, sentence_id
            )

    def clearSpeakStatus(self):
        self.client_is_speaking = False

    def supports_reliable_notification_tts(self):
        return False

    def begin_tts_ack_wait(self, state, sentence_id):
        return None

    async def close(self):
        self.closed = True


class EmptyAsyncIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class FakeConnectionWebSocket:
    def __init__(self, device_id="device-1", path="/ws"):
        self.request = SimpleNamespace(
            headers={"device-id": device_id},
            path=path,
        )
        self.remote_address = ("127.0.0.1", 9000)

    def __aiter__(self):
        return EmptyAsyncIterator()


class CapturingLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **kwargs):
        return self

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))


def _make_connection_handler():
    return ConnectionHandler(
        config={
            "exit_commands": [],
            "close_connection_no_voice_time": 120,
            "xiaozhi": {"audio_params": {"sample_rate": 24000}},
        },
        _vad=None,
        _asr=None,
        _llm=None,
        _memory=None,
        _intent=None,
        server=FakeServer(),
    )


def test_route_message_logs_audio_frame_arrival_and_queueing():
    async def scenario():
        conn = _make_connection_handler()
        logger = CapturingLogger()
        conn.logger = logger
        conn.bind_completed_event.set()
        conn.vad = object()
        conn.asr = object()

        await conn._route_message(b"opus-frame")

        assert conn.audio_frames_received == 1
        assert conn.audio_frames_queued == 1
        assert conn.asr_audio_queue.get_nowait() == b"opus-frame"
        assert any("audio frame received" in message for _, message in logger.messages)

    asyncio.run(scenario())


def test_route_message_logs_audio_frame_drop_when_asr_not_ready():
    async def scenario():
        conn = _make_connection_handler()
        logger = CapturingLogger()
        conn.logger = logger
        conn.bind_completed_event.set()
        conn.vad = object()
        conn.asr = None

        await conn._route_message(b"opus-frame")

        assert conn.audio_frames_received == 1
        assert conn.audio_frames_queued == 0
        assert conn.audio_frames_dropped_before_asr_ready == 1
        assert conn.asr_audio_queue.empty()
        assert any(
            "audio frame dropped before vad/asr ready" in message
            for _, message in logger.messages
        )

    asyncio.run(scenario())


def test_ack_handler_dispatches_ack_to_runtime():
    async def scenario():
        conn = FakeConn()
        handler = XiaoxinAckMessageHandler()
        await handler.handle(
            conn,
            {
                "type": "xiaoxin_ack",
                "delivery_id": "del_1",
                "state": "device_received",
                "reason": None,
            },
        )
        return conn.server.xiaoxin_runtime.dispatcher.acks

    acks = asyncio.run(scenario())

    assert acks[0][0] == "aa"
    assert acks[0][1]["delivery_id"] == "del_1"


def test_ack_handler_message_type_is_xiaoxin_ack():
    assert XiaoxinAckMessageHandler().message_type == TextMessageType.XIAOXIN_ACK


def test_hello_handler_stores_device_sntp_time_snapshot(monkeypatch):
    async def scenario():
        conn = SimpleNamespace(
            logger=SimpleNamespace(
                bind=lambda **kwargs: SimpleNamespace(
                    debug=lambda *args, **kw: None,
                    info=lambda *args, **kw: None,
                )
            ),
            audio_format=None,
            welcome_msg={"type": "hello", "audio_params": {"sample_rate": 24000}},
            websocket=FakeWebSocket(),
            features=None,
        )
        monkeypatch.setattr("core.handle.helloHandle.time.time", lambda: 1783129245.25)

        await handleHelloMessage(
            conn,
            {
                "type": "hello",
                "device_time": {
                    "wall_time_ms": 1783129240000,
                    "sync_status": "synced",
                    "timezone": "Asia/Shanghai",
                    "source": "sntp",
                },
            },
        )
        return conn

    conn = asyncio.run(scenario())

    assert conn.device_time_snapshot == {
        "wall_time_ms": 1783129240000,
        "sync_status": "synced",
        "timezone": "Asia/Shanghai",
        "source": "sntp",
        "received_at_ms": 1783129245250,
    }
    assert conn.websocket.sent


def test_hello_handler_keeps_server_playback_sample_rate(monkeypatch):
    async def scenario():
        conn = SimpleNamespace(
            logger=SimpleNamespace(
                bind=lambda **kwargs: SimpleNamespace(
                    debug=lambda *args, **kw: None,
                    info=lambda *args, **kw: None,
                )
            ),
            audio_format=None,
            welcome_msg={
                "type": "hello",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 24000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            },
            websocket=FakeWebSocket(),
            features=None,
        )

        await handleHelloMessage(
            conn,
            {
                "type": "hello",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            },
        )
        return conn

    conn = asyncio.run(scenario())
    response = json.loads(conn.websocket.sent[-1])

    assert response["audio_params"]["sample_rate"] == 24000
    assert conn.welcome_msg["audio_params"]["sample_rate"] == 24000
    assert conn.audio_format == "opus"
    assert conn.client_audio_params["sample_rate"] == 16000


def test_hello_handler_records_device_battery_and_firmware():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        conn = SimpleNamespace(
            device_id="device-telemetry",
            xiaoxin_control_runtime=SimpleNamespace(registry=registry),
            logger=SimpleNamespace(
                bind=lambda **kwargs: SimpleNamespace(
                    debug=lambda *args, **kw: None,
                    info=lambda *args, **kw: None,
                )
            ),
            audio_format=None,
            welcome_msg={"type": "hello", "audio_params": {"sample_rate": 24000}},
            websocket=FakeWebSocket(),
            features=None,
        )

        await handleHelloMessage(
            conn,
            {
                "type": "hello",
                "device_status": {
                    "battery_level": 4,
                    "battery_percent": 73,
                    "firmware_version": "0.1.2",
                },
            },
        )
        return registry.list_devices()[0]

    device = asyncio.run(scenario())

    assert device["battery_level"] == 4
    assert device["battery_percent"] == 73
    assert device["firmware_version"] == "0.1.2"


def test_hello_handler_requires_boot_metadata_for_boot_checkin():
    async def scenario():
        boot_calls = []
        registry = XiaoxinDeviceRegistry()

        def note_device_boot(device_id, **kwargs):
            boot_calls.append((device_id, kwargs))

        conn = SimpleNamespace(
            device_id="device-boot",
            xiaoxin_control_runtime=SimpleNamespace(
                registry=registry,
                note_device_boot=note_device_boot,
            ),
            logger=SimpleNamespace(
                bind=lambda **kwargs: SimpleNamespace(
                    debug=lambda *args, **kw: None,
                    info=lambda *args, **kw: None,
                )
            ),
            audio_format=None,
            welcome_msg={"type": "hello", "audio_params": {"sample_rate": 24000}},
            websocket=FakeWebSocket(),
            features=None,
        )

        await handleHelloMessage(
            conn,
            {"type": "hello", "device_status": {"firmware_version": "old"}},
        )
        await handleHelloMessage(
            conn,
            {
                "type": "hello",
                "device_status": {
                    "boot_id": "boot-1",
                    "reset_reason": "poweron",
                },
            },
        )
        return boot_calls

    assert asyncio.run(scenario()) == [
        (
            "device-boot",
            {"boot_id": "boot-1", "reset_reason": "poweron"},
        )
    ]


def test_tts_audio_waits_for_initial_prebuffer_before_sending(monkeypatch):
    async def scenario():
        conn = FakeConn()
        conn.sentence_id = "sentence-1"
        conn.conn_from_mqtt_gateway = False
        conn.client_abort = False

        for index in range(4):
            await sendAudioMessage(
                conn,
                SentenceType.MIDDLE,
                [bytes([index])],
                None,
                sentence_id="sentence-1",
            )
        assert conn.websocket.sent == []

        await sendAudioMessage(
            conn,
            SentenceType.MIDDLE,
            [b"four"],
            None,
            sentence_id="sentence-1",
        )
        pending = conn.audio_rate_controller.pending_send_task
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        return conn.websocket.sent

    sent = asyncio.run(scenario())
    assert sent[:5] == [bytes([0]), bytes([1]), bytes([2]), bytes([3]), b"four"]


def test_tts_stop_flushes_partial_initial_prebuffer(monkeypatch):
    async def fake_wait_for_audio_completion(conn):
        assert conn.websocket.sent[:2] == [b"first", b"second"]

    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        fake_wait_for_audio_completion,
    )

    async def scenario():
        conn = FakeConn()
        conn.sentence_id = "sentence-1"
        conn.conn_from_mqtt_gateway = False
        conn.client_abort = False

        await sendAudioMessage(
            conn,
            SentenceType.MIDDLE,
            [b"first"],
            None,
            sentence_id="sentence-1",
        )
        await sendAudioMessage(
            conn,
            SentenceType.MIDDLE,
            [b"second"],
            None,
            sentence_id="sentence-1",
        )
        await sendAudioMessage(
            conn,
            SentenceType.LAST,
            [],
            None,
            sentence_id="sentence-1",
        )
        pending = conn.audio_rate_controller.pending_send_task
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        return conn.websocket.sent

    sent = asyncio.run(scenario())
    assert sent[:2] == [b"first", b"second"]
    assert json.loads(sent[-1])["state"] == "stop"


def test_send_audio_message_passes_sentence_id_to_stop(monkeypatch):
    conn = FakeConn()
    conn.sentence_id = "sentence-1"
    stop_calls = []

    async def fake_send_audio(conn_arg, audios):
        return None

    async def fake_send_tts_message(conn_arg, state, text=None, sentence_id=None):
        stop_calls.append((state, text, sentence_id))

    monkeypatch.setattr("core.handle.sendAudioHandle.sendAudio", fake_send_audio)
    monkeypatch.setattr(
        "core.handle.sendAudioHandle.send_tts_message",
        fake_send_tts_message,
    )

    asyncio.run(
        sendAudioMessage(
            conn,
            SentenceType.LAST,
            [b"audio"],
            "done",
            sentence_id="sentence-1",
        )
    )

    assert stop_calls == [("stop", None, "sentence-1")]


def test_send_audio_message_maps_subtitle_update_without_restarting_tts(monkeypatch):
    conn = FakeConn()
    conn.sentence_id = "sentence-1"
    tts_calls = []

    async def fake_send_audio(conn_arg, audios):
        assert audios == []

    async def fake_send_tts_message(conn_arg, state, text=None, sentence_id=None):
        tts_calls.append((state, text, sentence_id))

    monkeypatch.setattr("core.handle.sendAudioHandle.sendAudio", fake_send_audio)
    monkeypatch.setattr(
        "core.handle.sendAudioHandle.send_tts_message",
        fake_send_tts_message,
    )

    asyncio.run(
        sendAudioMessage(
            conn,
            SentenceType.UPDATE,
            [],
            "今天杭州天气晴朗，未来七天适合出去玩。",
            sentence_id="sentence-1",
        )
    )

    assert tts_calls == [
        (
            "sentence_update",
            "今天杭州天气晴朗，未来七天适合出去玩。",
            "sentence-1",
        )
    ]


def test_send_audio_message_does_not_drop_first_subtitle_when_rate_controller_is_stale():
    async def scenario():
        conn = FakeConn()
        conn.sentence_id = "sentence-1"
        conn.conn_from_mqtt_gateway = False
        conn.client_abort = False
        conn.audio_rate_controller = AudioRateController()
        conn.audio_flow_control = {"sentence_id": "sentence-1"}

        stale_task = asyncio.create_task(asyncio.sleep(0))
        conn.audio_rate_controller.pending_send_task = stale_task
        await stale_task

        try:
            await sendAudioMessage(
                conn,
                SentenceType.FIRST,
                [b"audio"],
                "小芯提醒你，记得喝水，休息一下眼睛。",
                sentence_id="sentence-1",
            )
        finally:
            pending = conn.audio_rate_controller.pending_send_task
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        return conn.websocket.sent

    sent = asyncio.run(scenario())
    json_messages = [item for item in sent if isinstance(item, str)]
    assert json_messages
    subtitle = json.loads(json_messages[0])
    assert subtitle["type"] == "tts"
    assert subtitle["state"] == "sentence_start"
    assert subtitle["sentence_id"] == "sentence-1"
    assert subtitle["text"] == "小芯提醒你，记得喝水，休息一下眼睛。"


def test_send_tts_message_stop_marks_stale_legacy_delivery_unverified_once(
    monkeypatch,
):
    conn = FakeConn()
    conn.sentence_id = "sentence-2"
    conn.xiaoxin_control_tts_deliveries = {
        "sentence-1": "del-1",
        "sentence-2": "del-2",
    }

    async def fake_wait_for_audio_completion(conn_arg):
        return None

    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        fake_wait_for_audio_completion,
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-1"))

    assert conn.server.xiaoxin_runtime.dispatcher.done == []
    assert conn.server.xiaoxin_runtime.dispatcher.legacy_unverified == [
        ("del-1", "sentence-1")
    ]
    assert conn.done_calls == []


def test_send_tts_message_stop_marks_current_legacy_delivery_unverified_once(
    monkeypatch,
):
    conn = FakeConn()
    conn.sentence_id = "sentence-1"
    conn.xiaoxin_control_tts_deliveries = {"sentence-1": "del-1"}

    async def fake_wait_for_audio_completion(conn_arg):
        return None

    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        fake_wait_for_audio_completion,
    )

    asyncio.run(send_tts_message(conn, "stop"))

    assert conn.server.xiaoxin_runtime.dispatcher.done == []
    assert conn.server.xiaoxin_runtime.dispatcher.legacy_unverified == [
        ("del-1", "sentence-1")
    ]
    assert conn.done_calls == []


def test_send_tts_message_stop_does_not_run_global_side_effects_for_stale_control_sentence(
    monkeypatch,
):
    conn = FakeConn()
    conn.client_is_speaking = True
    conn.sentence_id = "sentence-2"
    conn.xiaoxin_control_tts_deliveries = {"sentence-1": "del-1"}
    stop_calls = []
    conn.audio_rate_controller = SimpleNamespace(
        stop_sending=lambda: stop_calls.append("stopped")
    )

    async def fake_wait_for_audio_completion(conn_arg):
        return None

    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        fake_wait_for_audio_completion,
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-1"))

    assert conn.server.xiaoxin_runtime.dispatcher.done == []
    assert conn.server.xiaoxin_runtime.dispatcher.legacy_unverified == [
        ("del-1", "sentence-1")
    ]
    assert conn.done_calls == []
    assert conn.client_is_speaking is True
    assert stop_calls == []
    assert len(conn.websocket.sent) == 1
    payload = json.loads(conn.websocket.sent[-1])
    assert payload["type"] == "tts"
    assert payload["state"] == "stop"
    assert payload["sentence_id"] == "sentence-1"


def test_send_tts_message_stop_waits_for_audio_completion_before_marking_stale_control_delivery(
    monkeypatch,
):
    conn = FakeConn()
    conn.sentence_id = "sentence-2"
    conn.xiaoxin_control_tts_deliveries = {"sentence-1": "del-1"}
    order = []

    def supports_reliable_notification_tts():
        return True

    def begin_tts_ack_wait(state, sentence_id):
        order.append(f"begin:{state}:{sentence_id}")

    async def wait_for_tts_ack(state, sentence_id, timeout_ms):
        assert (
            conn.websocket.sent
        ), "expected tts:stop to be sent before waiting for done ack"
        payload = json.loads(conn.websocket.sent[-1])
        assert payload["type"] == "tts"
        assert payload["state"] == "stop"
        assert payload["sentence_id"] == "sentence-1"
        order.append(f"wait:{state}:{sentence_id}:{timeout_ms}")
        return TtsAckResult("done", sentence_id)

    async def fake_wait_for_audio_completion(conn_arg):
        order.append("server-audio-complete")

    def fake_mark_xiaoxin_control_tts_done(sentence_id):
        order.append(f"mark:{sentence_id}")

    conn.supports_reliable_notification_tts = supports_reliable_notification_tts
    conn.begin_tts_ack_wait = begin_tts_ack_wait
    conn.wait_for_tts_ack = wait_for_tts_ack
    conn.config["tts_done_ack_timeout_ms"] = 5000

    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        fake_wait_for_audio_completion,
    )
    monkeypatch.setattr(
        conn,
        "mark_xiaoxin_control_tts_done",
        fake_mark_xiaoxin_control_tts_done,
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-1"))

    assert order == [
        "server-audio-complete",
        "begin:done:sentence-1",
        "wait:done:sentence-1:5000",
        "mark:sentence-1",
    ]


def test_send_tts_message_stop_keeps_ordinary_stale_sentence_guard(
    monkeypatch,
):
    conn = FakeConn()
    conn.client_is_speaking = True
    conn.sentence_id = "ordinary-new"
    conn.xiaoxin_control_tts_deliveries = {}
    stop_tts_notify_calls = []
    ack_events = []

    def supports_tts_done_ack():
        return True

    def begin_tts_ack_wait(state, sentence_id):
        ack_events.append(f"begin:{state}:{sentence_id}")

    async def wait_for_tts_ack(state, sentence_id, timeout_ms):
        ack_events.append(f"wait:{state}:{sentence_id}:{timeout_ms}")
        return True

    async def fake_wait_for_audio_completion(conn_arg):
        return None

    async def fake_send_audio(conn_arg, audios):
        return None

    conn.supports_tts_done_ack = supports_tts_done_ack
    conn.begin_tts_ack_wait = begin_tts_ack_wait
    conn.wait_for_tts_ack = wait_for_tts_ack

    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        fake_wait_for_audio_completion,
    )
    monkeypatch.setattr(
        "core.handle.sendAudioHandle.sendAudio",
        fake_send_audio,
    )
    monkeypatch.setattr(
        "core.handle.sendAudioHandle.audio_to_data",
        lambda *args, **kwargs: stop_tts_notify_calls.append((args, kwargs)),
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="ordinary-old"))

    assert conn.client_is_speaking is True
    assert conn.done_calls == []
    assert conn.server.xiaoxin_runtime.dispatcher.done == []
    assert conn.websocket.sent == []
    assert ack_events == []


def test_send_tts_message_stop_waits_for_device_done_ack_when_supported(monkeypatch):
    conn = FakeConn()
    conn.sentence_id = "sentence-1"
    conn.config["tts_done_ack_timeout_ms"] = 5000
    order = []

    def supports_tts_done_ack():
        return True

    def begin_tts_ack_wait(state, sentence_id):
        order.append(f"begin:{state}:{sentence_id}")

    async def wait_for_tts_ack(state, sentence_id, timeout_ms):
        assert (
            conn.websocket.sent
        ), "expected tts:stop to be sent before waiting for done ack"
        payload = json.loads(conn.websocket.sent[-1])
        assert payload["type"] == "tts"
        assert payload["state"] == "stop"
        assert payload["sentence_id"] == "sentence-1"
        order.append(f"wait:{state}:{sentence_id}:{timeout_ms}")
        return True

    async def fake_wait_for_audio_completion(conn_arg):
        order.append("server-audio-complete")

    conn.supports_tts_done_ack = supports_tts_done_ack
    conn.begin_tts_ack_wait = begin_tts_ack_wait
    conn.wait_for_tts_ack = wait_for_tts_ack

    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        fake_wait_for_audio_completion,
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-1"))

    assert order == [
        "server-audio-complete",
        "begin:done:sentence-1",
        "wait:done:sentence-1:5000",
    ]
    assert conn.client_is_speaking is False


def test_send_tts_message_includes_explicit_sentence_id(monkeypatch):
    conn = FakeConn()
    conn.sentence_id = "sentence-current"

    asyncio.run(send_tts_message(conn, "start", sentence_id="sentence-explicit"))

    payload = json.loads(conn.websocket.sent[-1])
    assert payload["type"] == "tts"
    assert payload["state"] == "start"
    assert payload["sentence_id"] == "sentence-explicit"


def test_send_tts_message_includes_current_sentence_id_when_omitted(monkeypatch):
    conn = FakeConn()
    conn.sentence_id = "sentence-current"

    asyncio.run(send_tts_message(conn, "start"))

    payload = json.loads(conn.websocket.sent[-1])
    assert payload["type"] == "tts"
    assert payload["state"] == "start"
    assert payload["sentence_id"] == "sentence-current"


def test_control_console_tts_waits_for_ready_ack_before_queueing_text(monkeypatch):
    events = []

    class CapturingQueue(queue.Queue):
        def put(self, item, *args, **kwargs):
            events.append(f"queue:{item.sentence_type.name}")
            super().put(item, *args, **kwargs)

    class FakeTts:
        def __init__(self):
            self.tts_text_queue = CapturingQueue()

        def store_tts_text(self, sentence_id, text):
            events.append(f"store:{sentence_id}:{text}")

    async def fake_send_tts_message(conn_arg, state, text=None, sentence_id=None):
        events.append(f"tts:{state}:{conn_arg.sentence_id}")

    conn = _make_connection_handler()
    conn.websocket = FakeWebSocket()
    conn.tts = FakeTts()
    conn.features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn.config["tts_ready_ack_timeout_ms"] = 700

    def begin_tts_ack_wait(state, sentence_id):
        events.append(f"begin:{state}:{sentence_id}")

    async def wait_for_tts_ack(state, sentence_id, timeout_ms):
        events.append(f"wait:{state}:{sentence_id}:{timeout_ms}")
        return TtsAckResult("ready", sentence_id)

    conn.begin_tts_ack_wait = begin_tts_ack_wait
    conn.wait_for_tts_ack = wait_for_tts_ack
    conn.mark_tts_streaming = lambda sentence_id: True

    monkeypatch.setattr("core.connection.send_tts_message", fake_send_tts_message)

    asyncio.run(
        conn.speak_from_control_console(
            "notification body", "delivery-1", "sentence-fixed"
        )
    )

    assert events == [
        "begin:ready:sentence-fixed",
        "tts:start:sentence-fixed",
        "wait:ready:sentence-fixed:700",
        "store:sentence-fixed:notification body",
        "queue:FIRST",
        "queue:MIDDLE",
        "queue:LAST",
    ]


def test_control_console_tts_uses_start_delay_without_ready_ack(monkeypatch):
    events = []

    class CapturingQueue(queue.Queue):
        def put(self, item, *args, **kwargs):
            events.append(f"queue:{item.sentence_type.name}")
            super().put(item, *args, **kwargs)

    class FakeTts:
        def __init__(self):
            self.tts_text_queue = CapturingQueue()

        def store_tts_text(self, sentence_id, text):
            events.append(f"store:{sentence_id}:{text}")

    async def fake_send_tts_message(conn_arg, state, text=None, sentence_id=None):
        events.append(f"tts:{state}:{conn_arg.sentence_id}")

    async def fake_sleep(seconds):
        events.append(f"sleep:{seconds}")

    conn = _make_connection_handler()
    conn.websocket = FakeWebSocket()
    conn.tts = FakeTts()
    conn.features = {}
    conn.config["wakeup_response_start_delay_ms"] = 300

    monkeypatch.setattr("core.connection.send_tts_message", fake_send_tts_message)
    monkeypatch.setattr("core.connection.asyncio.sleep", fake_sleep)

    result = asyncio.run(
        conn.speak_from_control_console(
            "notification body", "delivery-1", "sentence-fixed"
        )
    )

    assert events == [
        "tts:start:sentence-fixed",
        "sleep:0.3",
        "store:sentence-fixed:notification body",
        "queue:FIRST",
        "queue:MIDDLE",
        "queue:LAST",
    ]
    assert result is None


def test_control_console_ready_timeout_retries_same_sentence_then_raises(monkeypatch):
    events = []
    conn = _make_connection_handler()
    conn.websocket = FakeWebSocket()
    conn.features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn.config["tts_ready_ack_timeout_ms"] = 700
    conn.config["tts_ready_start_retry_delays_ms"] = [300, 600, 1200]
    conn.tts = SimpleNamespace(
        tts_text_queue=queue.Queue(),
        store_tts_text=lambda sentence_id, text: events.append((sentence_id, text)),
    )

    async def fake_send(conn_arg, state, text=None, sentence_id=None):
        events.append((state, sentence_id))

    async def fake_wait(state, sentence_id, timeout_ms):
        events.append(("wait", sentence_id, timeout_ms))
        return None

    async def fake_sleep(seconds):
        events.append(("sleep", seconds))

    monkeypatch.setattr("core.connection.send_tts_message", fake_send)
    monkeypatch.setattr("core.connection.asyncio.sleep", fake_sleep)
    conn.wait_for_tts_ack = fake_wait

    with pytest.raises(TtsAttemptError) as exc:
        asyncio.run(
            conn.speak_from_control_console(
                "complete notification text", "delivery-1", "sentence-fixed"
            )
        )

    assert exc.value.reason == "ready_timeout"
    assert [event for event in events if event[0] == "start"] == [
        ("start", "sentence-fixed"),
        ("start", "sentence-fixed"),
        ("start", "sentence-fixed"),
        ("start", "sentence-fixed"),
    ]
    assert list(conn.tts.tts_text_queue.queue) == []
    assert not any(
        isinstance(event, tuple) and len(event) == 2 and event[0] == "sentence-fixed"
        for event in events
    )
    assert conn.xiaoxin_control_tts_deliveries == {}
    assert conn.xiaoxin_control_runtime.dispatcher.failed == [
        ("delivery-1", "sentence-fixed", "ready_timeout")
    ]


def test_control_console_ready_retries_are_capped_at_four_start_sends(monkeypatch):
    conn = _make_connection_handler()
    conn.websocket = FakeWebSocket()
    conn.features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn.config["tts_ready_start_retry_delays_ms"] = [1, 2, 3, 4, 5]
    conn.tts = SimpleNamespace(
        tts_text_queue=queue.Queue(),
        store_tts_text=lambda sentence_id, text: None,
    )
    starts = []

    async def fake_send(conn_arg, state, text=None, sentence_id=None):
        starts.append((state, sentence_id))

    async def fake_sleep(seconds):
        return None

    async def fake_wait(state, sentence_id, timeout_ms):
        return None

    monkeypatch.setattr("core.connection.send_tts_message", fake_send)
    monkeypatch.setattr("core.connection.asyncio.sleep", fake_sleep)
    conn.wait_for_tts_ack = fake_wait

    with pytest.raises(TtsAttemptError):
        asyncio.run(
            conn.speak_from_control_console("text", "delivery-1", "sentence-fixed")
        )

    assert starts == [("start", "sentence-fixed")] * 4


def test_control_console_queues_full_text_once_after_ready_retry(monkeypatch):
    events = []
    conn = _make_connection_handler()
    conn.websocket = FakeWebSocket()
    conn.features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn.config["tts_ready_start_retry_delays_ms"] = [300, 600, 1200]

    class FakeTts:
        def __init__(self):
            self.tts_text_queue = queue.Queue()
            self.stored = []

        def store_tts_text(self, sentence_id, text):
            self.stored.append((sentence_id, text))

    conn.tts = FakeTts()
    waits = 0

    async def fake_send(conn_arg, state, text=None, sentence_id=None):
        events.append((state, sentence_id))

    async def fake_wait(state, sentence_id, timeout_ms):
        nonlocal waits
        waits += 1
        if waits < 3:
            return None
        return TtsAckResult("ready", sentence_id)

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("core.connection.send_tts_message", fake_send)
    monkeypatch.setattr("core.connection.asyncio.sleep", fake_sleep)
    conn.wait_for_tts_ack = fake_wait

    asyncio.run(
        conn.speak_from_control_console(
            "complete notification text", "delivery-1", "sentence-fixed"
        )
    )

    queued = list(conn.tts.tts_text_queue.queue)
    assert [item.sentence_type for item in queued] == [
        SentenceType.FIRST,
        SentenceType.MIDDLE,
        SentenceType.LAST,
    ]
    assert queued[1].content_detail == "complete notification text"
    assert conn.tts.stored == [("sentence-fixed", "complete notification text")]
    assert events == [
        ("start", "sentence-fixed"),
        ("start", "sentence-fixed"),
        ("start", "sentence-fixed"),
    ]


@pytest.mark.parametrize("timeouts_before_error", [0, 2])
def test_control_console_ready_error_removes_mapping_and_notifies_once(
    monkeypatch, timeouts_before_error
):
    conn = _make_connection_handler()
    conn.websocket = FakeWebSocket()
    conn.features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn.tts = SimpleNamespace(
        tts_text_queue=queue.Queue(),
        store_tts_text=lambda sentence_id, text: None,
    )
    starts = []
    waits = 0

    async def fake_send(conn_arg, state, text=None, sentence_id=None):
        starts.append((state, sentence_id))

    async def fake_wait(state, sentence_id, timeout_ms):
        nonlocal waits
        waits += 1
        if waits <= timeouts_before_error:
            return None
        conn.mark_xiaoxin_control_tts_failed(sentence_id, "device_busy")
        return TtsAckResult("error", sentence_id, "device_busy")

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("core.connection.send_tts_message", fake_send)
    monkeypatch.setattr("core.connection.asyncio.sleep", fake_sleep)
    conn.wait_for_tts_ack = fake_wait

    with pytest.raises(TtsAttemptError) as exc:
        asyncio.run(
            conn.speak_from_control_console("text", "delivery-1", "sentence-fixed")
        )

    assert exc.value.reason == "device_busy"
    assert starts == [("start", "sentence-fixed")] * (timeouts_before_error + 1)
    assert list(conn.tts.tts_text_queue.queue) == []
    assert conn.xiaoxin_control_tts_deliveries == {}
    assert conn.xiaoxin_control_runtime.dispatcher.failed == [
        ("delivery-1", "sentence-fixed", "device_busy")
    ]


@pytest.mark.parametrize("cancel_stage", ["start_send", "ready_wait", "retry_sleep"])
def test_control_console_cancellation_reports_once_cleans_attempt_and_propagates(
    monkeypatch, cancel_stage
):
    conn = _make_connection_handler()
    conn.websocket = FakeWebSocket()
    conn.features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn.tts = SimpleNamespace(
        tts_text_queue=queue.Queue(),
        store_tts_text=lambda sentence_id, text: None,
    )

    async def fake_send(conn_arg, state, text=None, sentence_id=None):
        if cancel_stage == "start_send":
            raise asyncio.CancelledError

    async def fake_wait(state, sentence_id, timeout_ms):
        if cancel_stage == "ready_wait":
            raise asyncio.CancelledError
        return None

    async def fake_sleep(seconds):
        if cancel_stage == "retry_sleep":
            raise asyncio.CancelledError

    conn.wait_for_tts_ack = fake_wait
    monkeypatch.setattr("core.connection.send_tts_message", fake_send)
    monkeypatch.setattr("core.connection.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            conn.speak_from_control_console("text", "delivery-1", "sentence-fixed")
        )

    assert conn.xiaoxin_control_tts_deliveries == {}
    assert list(conn.tts.tts_text_queue.queue) == []
    assert conn.xiaoxin_control_runtime.dispatcher.failed == [
        ("delivery-1", "sentence-fixed", "attempt_cancelled")
    ]
    assert not any(key[1] == "sentence-fixed" for key in conn.tts_ack_waiters)
    assert "sentence-fixed" not in conn._tts_ack_active_phases


def test_control_console_store_failure_publishes_no_dtos_and_reports_failure(
    monkeypatch,
):
    conn = _make_connection_handler()
    conn.websocket = FakeWebSocket()
    conn.features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }

    class FailingStoreTts:
        def __init__(self):
            self.tts_text_queue = queue.Queue()

        def store_tts_text(self, sentence_id, text):
            raise RuntimeError("store unavailable")

    conn.tts = FailingStoreTts()
    conn.wait_for_tts_ack = lambda state, sentence_id, timeout_ms: asyncio.sleep(
        0, result=TtsAckResult("ready", sentence_id)
    )
    monkeypatch.setattr(
        "core.connection.send_tts_message", lambda *args, **kwargs: asyncio.sleep(0)
    )

    with pytest.raises(RuntimeError, match="store unavailable"):
        asyncio.run(
            conn.speak_from_control_console(
                "complete notification text", "delivery-1", "sentence-fixed"
            )
        )

    assert list(conn.tts.tts_text_queue.queue) == []
    assert conn.xiaoxin_control_tts_deliveries == {}
    assert conn.xiaoxin_control_runtime.dispatcher.failed == [
        ("delivery-1", "sentence-fixed", "store_tts_text_failed")
    ]


def test_reliable_control_stop_does_not_complete_when_done_times_out(monkeypatch):
    conn = FakeConn()
    conn.sentence_id = "sentence-1"
    conn.xiaoxin_control_tts_deliveries = {"sentence-1": "delivery-1"}
    failures = []
    conn.supports_reliable_notification_tts = lambda: True
    conn.wait_for_tts_ack = lambda *args: asyncio.sleep(0, result=None)
    conn.mark_xiaoxin_control_tts_failed = lambda sentence_id, reason: failures.append(
        (sentence_id, reason)
    )
    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        lambda conn_arg: asyncio.sleep(0),
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-1"))

    assert failures == [("sentence-1", "done_timeout")]
    assert conn.done_calls == []


def test_stale_reliable_control_stop_timeout_fails_without_global_side_effects(
    monkeypatch,
):
    conn = FakeConn()
    conn.sentence_id = "sentence-current"
    conn.xiaoxin_control_tts_deliveries = {"sentence-stale": "delivery-1"}
    failures = []
    conn.supports_reliable_notification_tts = lambda: True
    conn.wait_for_tts_ack = lambda *args: asyncio.sleep(0, result=None)
    conn.mark_xiaoxin_control_tts_failed = lambda sentence_id, reason: failures.append(
        (sentence_id, reason)
    )
    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        lambda conn_arg: asyncio.sleep(0),
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-stale"))

    assert failures == [("sentence-stale", "done_timeout")]
    assert conn.done_calls == []
    assert conn.client_is_speaking is True


def test_reliable_control_stop_completes_only_after_matching_done(monkeypatch):
    conn = FakeConn()
    conn.sentence_id = "sentence-1"
    conn.xiaoxin_control_tts_deliveries = {"sentence-1": "delivery-1"}
    completed = []
    failed = []
    conn.supports_reliable_notification_tts = lambda: True
    conn.wait_for_tts_ack = lambda *args: asyncio.sleep(
        0, result=TtsAckResult("done", "sentence-1")
    )
    conn.mark_xiaoxin_control_tts_done = completed.append
    conn.mark_xiaoxin_control_tts_failed = lambda sentence_id, reason: failed.append(
        (sentence_id, reason)
    )
    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        lambda conn_arg: asyncio.sleep(0),
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-1"))

    assert completed == ["sentence-1"]
    assert failed == []


def test_reliable_control_stop_error_is_idempotent_with_handler_failure(monkeypatch):
    conn = FakeConn()
    conn.sentence_id = "sentence-1"
    conn.xiaoxin_control_tts_deliveries = {"sentence-1": "delivery-1"}
    conn.supports_reliable_notification_tts = lambda: True

    async def fake_wait(state, sentence_id, timeout_ms):
        conn.mark_xiaoxin_control_tts_failed(sentence_id, "device_busy")
        return TtsAckResult("error", sentence_id, "device_busy")

    conn.wait_for_tts_ack = fake_wait
    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        lambda conn_arg: asyncio.sleep(0),
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-1"))

    assert conn.server.xiaoxin_runtime.dispatcher.failed == [
        ("delivery-1", "sentence-1", "device_busy")
    ]
    assert conn.server.xiaoxin_runtime.dispatcher.done == []


@pytest.mark.parametrize(
    "result",
    [
        TtsAckResult("ready", "sentence-1"),
        TtsAckResult("done", "sentence-other"),
    ],
)
def test_reliable_control_stop_rejects_wrong_done_result(monkeypatch, result):
    conn = FakeConn()
    conn.sentence_id = "sentence-1"
    conn.xiaoxin_control_tts_deliveries = {"sentence-1": "delivery-1"}
    completed = []
    failed = []
    conn.supports_reliable_notification_tts = lambda: True
    conn.wait_for_tts_ack = lambda *args: asyncio.sleep(0, result=result)
    conn.mark_xiaoxin_control_tts_done = completed.append
    conn.mark_xiaoxin_control_tts_failed = lambda sentence_id, reason: failed.append(
        (sentence_id, reason)
    )
    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        lambda conn_arg: asyncio.sleep(0),
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-1"))

    assert completed == []
    assert failed == [("sentence-1", "done_timeout")]


def test_connection_handle_connection_registers_and_unregisters_runtime_connection(
    monkeypatch,
):
    conn = _make_connection_handler()
    conn.xiaoxin_control_tts_deliveries = {
        "sentence-1": "delivery-1",
        "sentence-2": "delivery-2",
    }
    conn._set_tts_attempt_phase("ordinary-active", "STREAMING")
    ws = FakeConnectionWebSocket()
    saved = []

    async def fake_background_initialize():
        return None

    async def fake_check_timeout():
        return None

    async def fake_save_and_close(ws_arg):
        saved.append(ws_arg)

    monkeypatch.setattr(conn, "_background_initialize", fake_background_initialize)
    monkeypatch.setattr(conn, "_check_timeout", fake_check_timeout)
    monkeypatch.setattr(conn, "_save_and_close", fake_save_and_close)

    asyncio.run(conn.handle_connection(ws))

    assert conn.xiaoxin_control_runtime.registry.registered == [
        ("device-1", conn, "websocket")
    ]
    assert conn.xiaoxin_control_runtime.registry.unregistered == [("device-1", conn)]
    assert conn.xiaoxin_control_runtime.dispatcher.failed == [
        ("delivery-1", "sentence-1", "connection_closed_before_done"),
        ("delivery-2", "sentence-2", "connection_closed_before_done"),
    ]
    assert conn.xiaoxin_control_tts_deliveries == {}
    assert conn._tts_attempt_phases["ordinary-active"] == "TERMINAL"
    assert (
        conn._tts_terminal_results["ordinary-active"].reason
        == "connection_closed_before_done"
    )
    assert saved == [ws]


def test_control_console_reliable_start_quiesces_old_sender_before_start():
    async def scenario():
        conn = _make_connection_handler()
        conn.features = {
            "tts_ready_ack": True,
            "tts_done_ack": True,
            "tts_preroll_buffer": True,
        }
        conn.config["tts_ready_ack_timeout_ms"] = 50
        conn.config["tts_ready_start_retry_delays_ms"] = []
        conn.sentence_id = "ordinary-old"
        conn.audio_flow_control = {
            "packet_count": 5,
            "sequence": 5,
            "sentence_id": "ordinary-old",
        }
        old_send_entered = asyncio.Event()
        old_send_release = asyncio.Event()
        old_binary_sent = []

        async def blocked_old_send(packet):
            old_send_entered.set()
            await old_send_release.wait()
            old_binary_sent.append(packet)

        controller = AudioRateController(frame_duration=60)
        conn.audio_rate_controller = controller
        controller.add_audio(b"old-audio-1")
        controller.add_audio(b"old-audio-2")
        old_task = controller.start_sending(blocked_old_send)
        await old_send_entered.wait()

        cleared_text = []
        conn.tts = SimpleNamespace(
            tts_text_queue=queue.Queue(),
            tts_audio_queue=queue.Queue(),
            tts_audio_first_sentence=False,
            clear_tts_text=lambda sentence_id: cleared_text.append(sentence_id),
            store_tts_text=lambda sentence_id, text: None,
            reset_stream_state=lambda: None,
        )
        conn.tts.tts_text_queue.put("old-text")
        conn.tts.tts_audio_queue.put((SentenceType.MIDDLE, b"old-audio", None))

        sent = []

        async def send(data):
            sent.append(data)
            if isinstance(data, str):
                payload = json.loads(data)
                if payload.get("state") == "start":
                    assert old_task.done()
                    assert list(controller.queue) == []
                    assert conn.tts.tts_audio_queue.empty()
                    conn.resolve_tts_ack("ready", "notification-new")

        conn.websocket = SimpleNamespace(send=send)
        await conn.speak_from_control_console(
            "new notification", "delivery-1", "notification-new"
        )
        return old_task, old_binary_sent, cleared_text, sent, conn

    old_task, old_binary_sent, cleared_text, sent, conn = asyncio.run(scenario())
    assert old_task.done()
    assert old_binary_sent == []
    assert cleared_text == ["ordinary-old"]
    assert json.loads(sent[0])["state"] == "start"
    assert conn.tts.tts_audio_queue.empty()
    queued = list(conn.tts.tts_text_queue.queue)
    assert [item.sentence_id for item in queued] == ["notification-new"] * 3


def test_binary_send_rechecks_captured_sentence_ownership_immediately_before_send():
    async def scenario():
        conn = FakeConn()
        conn.conn_from_mqtt_gateway = False
        conn.sentence_id = "notification-new"
        flow_control = {
            "packet_count": 5,
            "sequence": 7,
            "sentence_id": "ordinary-old",
        }
        sent = await _do_send_audio(conn, b"stale", flow_control)
        return sent, conn.websocket.sent, flow_control

    sent, websocket_messages, flow_control = asyncio.run(scenario())
    assert sent is False
    assert websocket_messages == []
    assert flow_control == {
        "packet_count": 5,
        "sequence": 7,
        "sentence_id": "ordinary-old",
    }
