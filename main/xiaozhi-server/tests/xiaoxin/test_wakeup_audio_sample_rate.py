import asyncio
from types import SimpleNamespace

from core.handle.helloHandle import checkWakeupWords
from core.providers.tts.qwen_realtime import TTSProvider as QwenRealtimeTTSProvider


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


def _logger():
    return SimpleNamespace(
        bind=lambda **kwargs: SimpleNamespace(
            debug=lambda *args, **kw: None,
            info=lambda *args, **kw: None,
            warning=lambda *args, **kw: None,
            error=lambda *args, **kw: None,
        )
    )


def test_wakeup_cache_audio_uses_connection_sample_rate(monkeypatch):
    audio_calls = []
    sent_audio = []
    sent_tts = []

    async def fake_audio_to_data(file_path, **kwargs):
        audio_calls.append((file_path, kwargs))
        return [b"opus-frame"]

    async def fake_send_audio_message(conn, sentence_type, audios, text, sentence_id=None):
        sent_audio.append((sentence_type, audios, text, sentence_id))

    async def fake_send_tts_message(conn, state, text=None, sentence_id=None):
        sent_tts.append((state, text, sentence_id))

    monkeypatch.setattr("core.handle.helloHandle.audio_to_data", fake_audio_to_data)
    monkeypatch.setattr(
        "core.handle.helloHandle.sendAudioMessage",
        fake_send_audio_message,
    )
    monkeypatch.setattr(
        "core.handle.helloHandle.send_tts_message",
        fake_send_tts_message,
    )
    monkeypatch.setattr(
        "core.handle.helloHandle.wakeup_words_config.get_wakeup_response",
        lambda voice: {
            "file_path": "config/assets/wakeup_words_short.wav",
            "time": 9999999999,
            "text": "鎴戝湪杩欓噷鍝︼紒",
        },
    )

    conn = SimpleNamespace(
        config={
            "enable_wakeup_words_response_cache": True,
            "wakeup_words": ["你好小芯"],
        },
        tts=SimpleNamespace(voice="Stella"),
        sample_rate=24000,
        sentence_id=None,
        client_abort=True,
        dialogue=SimpleNamespace(messages=[], put=lambda message: None),
        logger=_logger(),
        websocket=FakeWebSocket(),
        session_id="session-1",
    )

    result = asyncio.run(checkWakeupWords(conn, "你好小芯"))

    assert result is True
    assert audio_calls == [
        (
            "config/assets/wakeup_words_short.wav",
            {"is_opus": True, "use_cache": False, "sample_rate": 24000},
        )
    ]
    assert sent_tts == [("start", None, None)]
    assert sent_audio[0][1] == [b"opus-frame"]


def test_wakeup_cache_audio_uses_connection_audio_format(monkeypatch):
    audio_calls = []

    async def fake_audio_to_data(file_path, **kwargs):
        audio_calls.append((file_path, kwargs))
        return [b"pcm-frame"]

    async def fake_send_audio_message(conn, sentence_type, audios, text, sentence_id=None):
        return None

    async def fake_send_tts_message(conn, state, text=None, sentence_id=None):
        return None

    monkeypatch.setattr("core.handle.helloHandle.audio_to_data", fake_audio_to_data)
    monkeypatch.setattr(
        "core.handle.helloHandle.sendAudioMessage",
        fake_send_audio_message,
    )
    monkeypatch.setattr(
        "core.handle.helloHandle.send_tts_message",
        fake_send_tts_message,
    )
    monkeypatch.setattr(
        "core.handle.helloHandle.wakeup_words_config.get_wakeup_response",
        lambda voice: {
            "file_path": "config/assets/wakeup_words_short.wav",
            "time": 9999999999,
            "text": "鎴戝湪杩欓噷鍝︼紒",
        },
    )

    conn = SimpleNamespace(
        config={
            "enable_wakeup_words_response_cache": True,
            "wakeup_words": ["浣犲ソ灏忚姱"],
        },
        tts=SimpleNamespace(voice="Stella"),
        audio_format="pcm",
        sample_rate=24000,
        sentence_id=None,
        client_abort=True,
        dialogue=SimpleNamespace(messages=[], put=lambda message: None),
        logger=_logger(),
        websocket=FakeWebSocket(),
        session_id="session-1",
    )

    result = asyncio.run(checkWakeupWords(conn, "浣犲ソ灏忚姱"))

    assert result is True
    assert audio_calls == [
        (
            "config/assets/wakeup_words_short.wav",
            {"use_cache": False, "sample_rate": 24000, "is_opus": False},
        )
    ]


def test_wakeup_cache_audio_uses_tts_playback_format_override(monkeypatch):
    audio_calls = []

    class TtsWithOpusPlaybackOverride:
        voice = "Cherry"

        def wakeup_response_is_opus(self, conn):
            return True

    async def fake_audio_to_data(file_path, **kwargs):
        audio_calls.append((file_path, kwargs))
        return [b"opus-frame"]

    async def fake_send_audio_message(conn, sentence_type, audios, text, sentence_id=None):
        return None

    async def fake_send_tts_message(conn, state, text=None, sentence_id=None):
        return None

    monkeypatch.setattr("core.handle.helloHandle.audio_to_data", fake_audio_to_data)
    monkeypatch.setattr(
        "core.handle.helloHandle.sendAudioMessage",
        fake_send_audio_message,
    )
    monkeypatch.setattr(
        "core.handle.helloHandle.send_tts_message",
        fake_send_tts_message,
    )
    monkeypatch.setattr(
        "core.handle.helloHandle.wakeup_words_config.get_wakeup_response",
        lambda voice: {
            "file_path": "config/assets/wakeup_words_short.wav",
            "time": 9999999999,
            "text": "我在这里哦！",
        },
    )

    conn = SimpleNamespace(
        config={
            "enable_wakeup_words_response_cache": True,
            "wakeup_words": ["你好小芯"],
        },
        tts=TtsWithOpusPlaybackOverride(),
        audio_format="pcm",
        sample_rate=24000,
        sentence_id=None,
        client_abort=True,
        dialogue=SimpleNamespace(messages=[], put=lambda message: None),
        logger=_logger(),
        websocket=FakeWebSocket(),
        session_id="session-1",
    )

    result = asyncio.run(checkWakeupWords(conn, "你好小芯"))

    assert result is True
    assert audio_calls == [
        (
            "config/assets/wakeup_words_short.wav",
            {"use_cache": False, "sample_rate": 24000, "is_opus": True},
        )
    ]


def test_qwen_realtime_wakeup_cache_audio_uses_opus_playback_format():
    provider = QwenRealtimeTTSProvider.__new__(QwenRealtimeTTSProvider)

    assert provider.wakeup_response_is_opus(None) is True


def test_wakeup_audio_waits_for_ready_ack_before_first_audio(monkeypatch):
    events = []

    async def fake_audio_to_data(file_path, **kwargs):
        return [b"opus-frame"]

    async def fake_send_audio_message(conn, sentence_type, audios, text, sentence_id=None):
        events.append("audio")

    async def fake_send_tts_message(conn, state, text=None, sentence_id=None):
        events.append(f"tts:{state}")

    monkeypatch.setattr("core.handle.helloHandle.audio_to_data", fake_audio_to_data)
    monkeypatch.setattr("core.handle.helloHandle.sendAudioMessage", fake_send_audio_message)
    monkeypatch.setattr("core.handle.helloHandle.send_tts_message", fake_send_tts_message)
    monkeypatch.setattr(
        "core.handle.helloHandle.wakeup_words_config.get_wakeup_response",
        lambda voice: {
            "file_path": "config/assets/wakeup_words_short.wav",
            "time": 9999999999,
            "text": "鎴戝湪杩欓噷鍝︼紒",
        },
    )

    class Conn(SimpleNamespace):
        def supports_tts_ready_ack(self):
            return True

        def begin_tts_ack_wait(self, state, sentence_id):
            events.append(f"begin:{state}:{sentence_id}")

        async def wait_for_tts_ack(self, state, sentence_id, timeout_ms):
            events.append(f"wait:{state}:{sentence_id}:{timeout_ms}")
            return True

    conn = Conn(
        config={
            "enable_wakeup_words_response_cache": True,
            "wakeup_words": ["浣犲ソ灏忚姱"],
            "tts_ready_ack_timeout_ms": 700,
        },
        tts=SimpleNamespace(voice="Stella"),
        audio_format="opus",
        sample_rate=24000,
        sentence_id=None,
        client_abort=True,
        dialogue=SimpleNamespace(messages=[], put=lambda message: None),
        logger=_logger(),
        websocket=FakeWebSocket(),
        session_id="session-1",
    )

    result = asyncio.run(checkWakeupWords(conn, "浣犲ソ灏忚姱"))

    assert result is True
    assert events[0].startswith("begin:ready:")
    assert events[1] == "tts:start"
    assert events[2].startswith("wait:ready:")
    assert events[3] == "audio"


def test_wakeup_audio_uses_fallback_delay_without_ready_ack(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def fake_audio_to_data(file_path, **kwargs):
        return [b"opus-frame"]

    async def fake_send_audio_message(conn, sentence_type, audios, text, sentence_id=None):
        return None

    async def fake_send_tts_message(conn, state, text=None, sentence_id=None):
        return None

    monkeypatch.setattr("core.handle.helloHandle.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("core.handle.helloHandle.audio_to_data", fake_audio_to_data)
    monkeypatch.setattr("core.handle.helloHandle.sendAudioMessage", fake_send_audio_message)
    monkeypatch.setattr("core.handle.helloHandle.send_tts_message", fake_send_tts_message)
    monkeypatch.setattr(
        "core.handle.helloHandle.wakeup_words_config.get_wakeup_response",
        lambda voice: {
            "file_path": "config/assets/wakeup_words_short.wav",
            "time": 9999999999,
            "text": "鎴戝湪杩欓噷鍝︼紒",
        },
    )

    conn = SimpleNamespace(
        config={
            "enable_wakeup_words_response_cache": True,
            "wakeup_words": ["浣犲ソ灏忚姱"],
            "wakeup_response_start_delay_ms": 300,
        },
        tts=SimpleNamespace(voice="Stella"),
        audio_format="opus",
        sample_rate=24000,
        sentence_id=None,
        client_abort=True,
        dialogue=SimpleNamespace(messages=[], put=lambda message: None),
        logger=_logger(),
        websocket=FakeWebSocket(),
        session_id="session-1",
    )

    result = asyncio.run(checkWakeupWords(conn, "浣犲ソ灏忚姱"))

    assert result is True
    assert sleeps == [0.3]
