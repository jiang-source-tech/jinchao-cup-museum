# TTS Playback ACK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace guessed wakeup-audio timing with a product-grade TTS playback handshake so the ESP32 starts receiving audio only after its playback pipeline is ready, while preserving compatibility with old firmware.

**Architecture:** Add a small TTS playback ACK module at the WebSocket text-message seam. The server sends existing `tts start` and `tts stop` messages, but when the device advertises `tts_ready_ack` or `tts_done_ack`, the server waits for device `tts ready` before audio frames and can wait for `tts done` before treating playback as truly finished. Old devices fall back to a configurable wakeup first-packet delay.

**Tech Stack:** Python asyncio WebSocket server, existing text message handler registry, pytest, ESP32 firmware WebSocket message handling.

## Global Constraints

- Preserve old firmware compatibility: devices that do not advertise `features.tts_ready_ack` must still play wakeup audio using `wakeup_response_start_delay_ms`.
- Preserve existing server-to-device message format for `{"type":"tts","state":"start"}` and `{"type":"tts","state":"stop"}`.
- Device-to-server ACK messages use `{"type":"tts","state":"ready"|"done","session_id":"...","sentence_id":"..."}`.
- `sentence_id` is required for new ACK matching; missing `sentence_id` must be ignored and logged, not crash the connection.
- Do not block normal audio send forever: ready and done waits must have timeouts and fallback behavior.
- Keep the change focused on TTS playback synchronization; do not refactor unrelated ASR, VAD, LLM, memory, or control-console behavior.

---

## File Structure

- Modify `main/xiaozhi-server/config.yaml`
  - Add default compatibility settings:
    - `wakeup_response_start_delay_ms: 300`
    - `tts_ready_ack_timeout_ms: 700`
    - `tts_done_ack_timeout_ms: 5000`
- Modify `main/xiaozhi-server/core/connection.py`
  - Store feature flags from hello.
  - Own per-connection ACK waiters keyed by `(state, sentence_id)`.
  - Provide a small interface used by handlers and send code:
    - `supports_tts_ready_ack() -> bool`
    - `supports_tts_done_ack() -> bool`
    - `begin_tts_ack_wait(state: str, sentence_id: str) -> asyncio.Event`
    - `resolve_tts_ack(state: str, sentence_id: str) -> bool`
    - `wait_for_tts_ack(state: str, sentence_id: str, timeout_ms: int) -> bool`
- Create `main/xiaozhi-server/core/handle/textHandler/ttsMessageHandler.py`
  - Handle device-to-server `tts ready` and `tts done` ACKs.
  - Ignore server-originating style states such as `start`, `stop`, and `sentence_start`.
- Modify `main/xiaozhi-server/core/handle/textMessageType.py`
  - Add `TTS = "tts"`.
- Modify `main/xiaozhi-server/core/handle/textMessageHandlerRegistry.py`
  - Register `TtsTextMessageHandler`.
- Modify `main/xiaozhi-server/core/handle/helloHandle.py`
  - Preserve existing feature parsing.
  - Use `wait_for_tts_ack("ready", sentence_id, timeout)` before sending cached wakeup audio when supported.
  - Use `wakeup_response_start_delay_ms` fallback for old devices or ACK timeout.
- Modify `main/xiaozhi-server/core/handle/sendAudioHandle.py`
  - Include `sentence_id` in server-to-device TTS state messages when known.
  - Optionally wait for `tts done` after server audio send completes and before clearing speak status, only when supported.
  - Keep existing `_wait_for_audio_completion` as fallback and as the source of server-side send completion.
- Add or modify `main/xiaozhi-server/tests/xiaoxin/test_tts_playback_ack.py`
  - Unit tests for ACK event registration, resolution, missing `sentence_id`, timeout fallback, and handler registration.
- Modify `main/xiaozhi-server/tests/xiaoxin/test_wakeup_audio_sample_rate.py`
  - Add tests for wakeup `ready` wait before first audio and timeout fallback.
- Modify `main/xiaozhi-server/tests/xiaoxin/test_connection_ack.py`
  - Add tests for `send_tts_message("stop")` waiting for `done` only when supported.
- Create or update firmware-side notes in `docs/development/xiaoxin-tts-playback-ack.md`
  - Document ESP32 expected behavior and JSON examples.

---

## Scope Check

This change touches two subsystems:

- Server protocol and playback synchronization, which is implementable and testable in this repository.
- ESP32 firmware playback state machine, which may live outside this repository.

Do not pretend the server-only change fully fixes the product. The server can implement the protocol and fallback now; the product-grade guarantee only arrives after firmware sends `tts ready` and `tts done` at the correct hardware states.

---

### Task 1: Add Connection-Level ACK Waiter Interface

**Files:**
- Modify: `main/xiaozhi-server/core/connection.py`
- Test: `main/xiaozhi-server/tests/xiaoxin/test_tts_playback_ack.py`

**Interfaces:**
- Produces: `ConnectionHandler.supports_tts_ready_ack() -> bool`
- Produces: `ConnectionHandler.supports_tts_done_ack() -> bool`
- Produces: `ConnectionHandler.begin_tts_ack_wait(state: str, sentence_id: str) -> asyncio.Event`
- Produces: `ConnectionHandler.resolve_tts_ack(state: str, sentence_id: str) -> bool`
- Produces: `ConnectionHandler.wait_for_tts_ack(state: str, sentence_id: str, timeout_ms: int) -> bool`
- Consumes: `self.features`, already populated from device hello.

- [ ] **Step 1: Write the failing tests**

Add `main/xiaozhi-server/tests/xiaoxin/test_tts_playback_ack.py`:

```python
import asyncio

from core.connection import ConnectionHandler


class FakeServer:
    xiaoxin_runtime = None


def make_conn(features=None):
    conn = ConnectionHandler(
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
    conn.features = features
    return conn


def test_tts_ack_support_flags_default_to_false():
    conn = make_conn(features=None)

    assert conn.supports_tts_ready_ack() is False
    assert conn.supports_tts_done_ack() is False


def test_tts_ack_support_flags_read_device_features():
    conn = make_conn(features={"tts_ready_ack": True, "tts_done_ack": True})

    assert conn.supports_tts_ready_ack() is True
    assert conn.supports_tts_done_ack() is True


def test_resolve_tts_ack_releases_matching_waiter():
    async def scenario():
        conn = make_conn()
        waiter = conn.begin_tts_ack_wait("ready", "sentence-1")

        resolved = conn.resolve_tts_ack("ready", "sentence-1")

        return resolved, waiter.is_set()

    assert asyncio.run(scenario()) == (True, True)


def test_resolve_tts_ack_ignores_wrong_sentence():
    async def scenario():
        conn = make_conn()
        waiter = conn.begin_tts_ack_wait("ready", "sentence-1")

        resolved = conn.resolve_tts_ack("ready", "sentence-2")

        return resolved, waiter.is_set()

    assert asyncio.run(scenario()) == (False, False)


def test_wait_for_tts_ack_returns_false_on_timeout():
    async def scenario():
        conn = make_conn()
        conn.begin_tts_ack_wait("ready", "sentence-1")

        return await conn.wait_for_tts_ack("ready", "sentence-1", timeout_ms=1)

    assert asyncio.run(scenario()) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_tts_playback_ack.py -q
```

Expected: FAIL because `ConnectionHandler` does not define the ACK methods yet.

- [ ] **Step 3: Implement the minimal ACK waiter interface**

In `ConnectionHandler.__init__`, near the other TTS state fields:

```python
self.tts_ack_waiters = {}
```

Add methods near `clearSpeakStatus`:

```python
def supports_tts_ready_ack(self) -> bool:
    return bool((self.features or {}).get("tts_ready_ack"))

def supports_tts_done_ack(self) -> bool:
    return bool((self.features or {}).get("tts_done_ack"))

def _tts_ack_key(self, state: str, sentence_id: str):
    return (state, sentence_id)

def begin_tts_ack_wait(self, state: str, sentence_id: str) -> asyncio.Event:
    event = asyncio.Event()
    self.tts_ack_waiters[self._tts_ack_key(state, sentence_id)] = event
    return event

def resolve_tts_ack(self, state: str, sentence_id: str) -> bool:
    event = self.tts_ack_waiters.pop(self._tts_ack_key(state, sentence_id), None)
    if event is None:
        return False
    event.set()
    return True

async def wait_for_tts_ack(self, state: str, sentence_id: str, timeout_ms: int) -> bool:
    event = self.tts_ack_waiters.get(self._tts_ack_key(state, sentence_id))
    if event is None:
        event = self.begin_tts_ack_wait(state, sentence_id)
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout_ms / 1000)
        return True
    except asyncio.TimeoutError:
        self.tts_ack_waiters.pop(self._tts_ack_key(state, sentence_id), None)
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_tts_playback_ack.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/core/connection.py main/xiaozhi-server/tests/xiaoxin/test_tts_playback_ack.py
git commit -m "feat: add tts playback ack waiters"
```

---

### Task 2: Add Device-to-Server TTS ACK Message Handler

**Files:**
- Modify: `main/xiaozhi-server/core/handle/textMessageType.py`
- Modify: `main/xiaozhi-server/core/handle/textMessageHandlerRegistry.py`
- Create: `main/xiaozhi-server/core/handle/textHandler/ttsMessageHandler.py`
- Test: `main/xiaozhi-server/tests/xiaoxin/test_tts_playback_ack.py`

**Interfaces:**
- Consumes: `ConnectionHandler.resolve_tts_ack(state: str, sentence_id: str) -> bool`
- Produces: registered text handler for `TextMessageType.TTS`.

- [ ] **Step 1: Write the failing tests**

Append to `test_tts_playback_ack.py`:

```python
from types import SimpleNamespace

from core.handle.textHandler.ttsMessageHandler import TtsTextMessageHandler
from core.handle.textMessageHandlerRegistry import TextMessageHandlerRegistry
from core.handle.textMessageType import TextMessageType


class FakeLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **kwargs):
        return SimpleNamespace(
            debug=lambda message, *args, **kwargs: self.messages.append(("debug", message)),
            info=lambda message, *args, **kwargs: self.messages.append(("info", message)),
            warning=lambda message, *args, **kwargs: self.messages.append(("warning", message)),
            error=lambda message, *args, **kwargs: self.messages.append(("error", message)),
        )


def test_tts_message_type_is_registered():
    registry = TextMessageHandlerRegistry()

    assert registry.get_handler("tts").message_type == TextMessageType.TTS


def test_tts_handler_resolves_ready_ack():
    async def scenario():
        conn = make_conn()
        conn.logger = FakeLogger()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        handler = TtsTextMessageHandler()

        await handler.handle(
            conn,
            {
                "type": "tts",
                "state": "ready",
                "session_id": "session-1",
                "sentence_id": "sentence-1",
            },
        )

        return await conn.wait_for_tts_ack("ready", "sentence-1", timeout_ms=1)

    assert asyncio.run(scenario()) is True


def test_tts_handler_ignores_missing_sentence_id():
    async def scenario():
        conn = make_conn()
        logger = FakeLogger()
        conn.logger = logger
        handler = TtsTextMessageHandler()

        await handler.handle(conn, {"type": "tts", "state": "ready"})

        return logger.messages

    messages = asyncio.run(scenario())
    assert any(level == "warning" for level, _ in messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_tts_playback_ack.py -q
```

Expected: FAIL because `ttsMessageHandler.py` and `TextMessageType.TTS` do not exist yet.

- [ ] **Step 3: Implement message type and handler**

In `textMessageType.py`:

```python
TTS = "tts"
```

Create `core/handle/textHandler/ttsMessageHandler.py`:

```python
from typing import Any, Dict, TYPE_CHECKING

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__


class TtsTextMessageHandler(TextMessageHandler):
    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.TTS

    async def handle(self, conn: "ConnectionHandler", msg_json: Dict[str, Any]) -> None:
        state = msg_json.get("state")
        if state not in {"ready", "done"}:
            conn.logger.bind(tag=TAG).debug(f"Ignoring device tts state: {state}")
            return

        sentence_id = msg_json.get("sentence_id")
        if not sentence_id:
            conn.logger.bind(tag=TAG).warning(
                f"Ignoring tts {state} ack without sentence_id"
            )
            return

        resolved = conn.resolve_tts_ack(state, sentence_id)
        if not resolved:
            conn.logger.bind(tag=TAG).debug(
                f"Ignoring unmatched tts {state} ack for sentence_id={sentence_id}"
            )
```

In `textMessageHandlerRegistry.py`, import and register:

```python
from core.handle.textHandler.ttsMessageHandler import TtsTextMessageHandler
```

Add `TtsTextMessageHandler()` to the default handler list.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_tts_playback_ack.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/core/handle/textMessageType.py main/xiaozhi-server/core/handle/textMessageHandlerRegistry.py main/xiaozhi-server/core/handle/textHandler/ttsMessageHandler.py main/xiaozhi-server/tests/xiaoxin/test_tts_playback_ack.py
git commit -m "feat: handle device tts playback acks"
```

---

### Task 3: Add sentence_id to Server-to-Device TTS State Messages

**Files:**
- Modify: `main/xiaozhi-server/core/handle/sendAudioHandle.py`
- Test: `main/xiaozhi-server/tests/xiaoxin/test_connection_ack.py`

**Interfaces:**
- Consumes: existing `send_tts_message(conn, state, text=None, sentence_id=None)`.
- Produces: outbound JSON includes `sentence_id` when `sentence_id` is passed or `conn.sentence_id` exists.
- Produces: firmware can echo the exact `sentence_id` in `tts ready` and `tts done`.

- [ ] **Step 1: Write the failing tests**

Append to `test_connection_ack.py`:

```python
import json


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_connection_ack.py::test_send_tts_message_includes_explicit_sentence_id tests/xiaoxin/test_connection_ack.py::test_send_tts_message_includes_current_sentence_id_when_omitted -q
```

Expected: FAIL because outbound TTS JSON does not include `sentence_id`.

- [ ] **Step 3: Implement sentence_id propagation**

In `sendAudioHandle.py`, inside `send_tts_message`, after constructing `message`:

```python
message_sentence_id = sentence_id if sentence_id is not None else conn.sentence_id
if message_sentence_id is not None:
    message["sentence_id"] = message_sentence_id
```

Do not change the existing `session_id` behavior.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_connection_ack.py::test_send_tts_message_includes_explicit_sentence_id tests/xiaoxin/test_connection_ack.py::test_send_tts_message_includes_current_sentence_id_when_omitted -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/core/handle/sendAudioHandle.py main/xiaozhi-server/tests/xiaoxin/test_connection_ack.py
git commit -m "feat: include sentence id in tts state messages"
```

---

### Task 4: Wait for Ready ACK Before Wakeup Audio First Packet

**Files:**
- Modify: `main/xiaozhi-server/config.yaml`
- Modify: `main/xiaozhi-server/core/handle/helloHandle.py`
- Test: `main/xiaozhi-server/tests/xiaoxin/test_wakeup_audio_sample_rate.py`

**Interfaces:**
- Consumes: `conn.supports_tts_ready_ack()`
- Consumes: `conn.begin_tts_ack_wait("ready", sentence_id)`
- Consumes: `conn.wait_for_tts_ack("ready", sentence_id, timeout_ms)`
- Produces: wakeup audio send ordering: `tts start` -> ready wait or fallback delay -> first audio frame.

- [ ] **Step 1: Write the failing tests**

Append to `test_wakeup_audio_sample_rate.py`:

```python
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
            "text": "我在这里哦！",
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
            "wakeup_words": ["你好小芯"],
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

    result = asyncio.run(checkWakeupWords(conn, "你好小芯"))

    assert result is True
    assert events[0] == "tts:start"
    assert events[1].startswith("begin:ready:")
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
            "text": "我在这里哦！",
        },
    )

    conn = SimpleNamespace(
        config={
            "enable_wakeup_words_response_cache": True,
            "wakeup_words": ["你好小芯"],
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

    result = asyncio.run(checkWakeupWords(conn, "你好小芯"))

    assert result is True
    assert sleeps == [0.3]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_wakeup_audio_sample_rate.py -q
```

Expected: FAIL because wakeup audio currently sends immediately after `audio_to_data`.

- [ ] **Step 3: Implement ready wait and fallback delay**

In `config.yaml`, add near the TTS timing config:

```yaml
wakeup_response_start_delay_ms: 300
tts_ready_ack_timeout_ms: 700
tts_done_ack_timeout_ms: 5000
```

In `helloHandle.py`, after generating `conn.sentence_id` and before sending audio:

```python
ready_ack_supported = (
    hasattr(conn, "supports_tts_ready_ack") and conn.supports_tts_ready_ack()
)
if ready_ack_supported:
    conn.begin_tts_ack_wait("ready", conn.sentence_id)
```

Keep `await send_tts_message(conn, "start")` before loading/sending audio.

After `audio_to_data(...)` and before `sendAudioMessage(... FIRST ...)`:

```python
if ready_ack_supported:
    timeout_ms = int(conn.config.get("tts_ready_ack_timeout_ms", 700))
    await conn.wait_for_tts_ack("ready", conn.sentence_id, timeout_ms)
else:
    delay_ms = int(conn.config.get("wakeup_response_start_delay_ms", 300))
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_wakeup_audio_sample_rate.py tests/xiaoxin/test_tts_playback_ack.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/config.yaml main/xiaozhi-server/core/handle/helloHandle.py main/xiaozhi-server/tests/xiaoxin/test_wakeup_audio_sample_rate.py
git commit -m "fix: wait for tts ready before wakeup audio"
```

---

### Task 5: Wait for Done ACK Before Clearing Speaking State

**Files:**
- Modify: `main/xiaozhi-server/core/handle/sendAudioHandle.py`
- Test: `main/xiaozhi-server/tests/xiaoxin/test_connection_ack.py`

**Interfaces:**
- Consumes: `conn.supports_tts_done_ack()`
- Consumes: `conn.begin_tts_ack_wait("done", sentence_id)`
- Consumes: `conn.wait_for_tts_ack("done", sentence_id, timeout_ms)`
- Produces: `send_tts_message(conn, "stop", sentence_id=...)` waits for local device playback completion when supported.

- [ ] **Step 1: Write the failing test**

Append to `test_connection_ack.py`:

```python
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
        "begin:done:sentence-1",
        "server-audio-complete",
        "wait:done:sentence-1:5000",
    ]
    assert conn.client_is_speaking is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_connection_ack.py::test_send_tts_message_stop_waits_for_device_done_ack_when_supported -q
```

Expected: FAIL because `send_tts_message` does not wait for device `done`.

- [ ] **Step 3: Implement done ACK wait**

In `sendAudioHandle.py`, inside `send_tts_message`, in the `state == "stop"` branch, before `_wait_for_audio_completion(conn)`:

```python
done_ack_supported = (
    hasattr(conn, "supports_tts_done_ack") and conn.supports_tts_done_ack()
)
if done_ack_supported:
    conn.begin_tts_ack_wait("done", stop_sentence_id)
```

After `_wait_for_audio_completion(conn)` and before marking control delivery or clearing speak status:

```python
if done_ack_supported:
    timeout_ms = int(conn.config.get("tts_done_ack_timeout_ms", 5000))
    await conn.wait_for_tts_ack("done", stop_sentence_id, timeout_ms)
```

Keep the stale ordinary sentence guard intact: if `stop_sentence_id != conn.sentence_id` and this is not a control delivery, return before any global side effects.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_connection_ack.py tests/xiaoxin/test_tts_playback_ack.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/core/handle/sendAudioHandle.py main/xiaozhi-server/tests/xiaoxin/test_connection_ack.py
git commit -m "feat: wait for device tts done ack"
```

---

### Task 6: Document ESP32 Firmware Contract

**Files:**
- Create: `docs/development/xiaoxin-tts-playback-ack.md`

**Interfaces:**
- Produces: firmware-facing protocol contract for `tts_ready_ack` and `tts_done_ack`.
- Consumes: server behavior from Tasks 1-4.

- [ ] **Step 1: Write the documentation**

Create `docs/development/xiaoxin-tts-playback-ack.md`:

```markdown
# Xiaoxin TTS Playback ACK Contract

## Purpose

The device must not receive TTS audio before its playback pipeline is ready. The server therefore supports an optional playback ACK protocol.

## Hello Feature Flags

Devices that implement this protocol add these flags to the existing hello features object:

```json
{
  "features": {
    "mcp": true,
    "tts_ready_ack": true,
    "tts_done_ack": true
  }
}
```

## Ready ACK

After the device receives:

```json
{"type":"tts","state":"start","session_id":"..."}
```

the device must:

1. Stop microphone upload for the playback transition.
2. Clear stale playback buffers.
3. Initialize or resume the Opus decoder, I2S output, amplifier, and speaker task.
4. Send:

```json
{
  "type": "tts",
  "state": "ready",
  "session_id": "...",
  "sentence_id": "..."
}
```

The server may fall back after `tts_ready_ack_timeout_ms`.

## Done ACK

After the device receives:

```json
{"type":"tts","state":"stop","session_id":"..."}
```

the device must drain its local playback queue. Only after the final audio sample has been submitted to the speaker path should it send:

```json
{
  "type": "tts",
  "state": "done",
  "session_id": "...",
  "sentence_id": "..."
}
```

The device should send `listen start` only after `tts done` has been sent.

## Compatibility

Devices that do not advertise `tts_ready_ack` and `tts_done_ack` continue using the old protocol. The server protects old devices with `wakeup_response_start_delay_ms`.
```

- [ ] **Step 2: Verify docs are linked or discoverable**

Run:

```bash
cd D:/AI_Pet/xiaoxin-esp32-server
rg -n "tts_ready_ack|tts_done_ack|wakeup_response_start_delay_ms" docs main/xiaozhi-server
```

Expected: finds config, server code, tests, and this contract doc.

- [ ] **Step 3: Commit**

```bash
git add docs/development/xiaoxin-tts-playback-ack.md
git commit -m "docs: document tts playback ack contract"
```

---

### Task 7: Run Full Focused Verification

**Files:**
- No code changes unless verification reveals failures.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified server behavior for ACK protocol and fallback path.

- [ ] **Step 1: Run focused pytest suite**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin/test_tts_playback_ack.py tests/xiaoxin/test_wakeup_audio_sample_rate.py tests/xiaoxin/test_connection_ack.py -q
```

Expected: PASS.

- [ ] **Step 2: Run broader xiaoxin tests**

Run:

```bash
cd main/xiaozhi-server
pytest tests/xiaoxin -q
```

Expected: PASS or only unrelated pre-existing failures. Any failure touching TTS, listen, ACK, connection, or wakeup is blocking.

- [ ] **Step 3: Inspect diff for protocol safety**

Run:

```bash
git diff -- main/xiaozhi-server/core/connection.py main/xiaozhi-server/core/handle/helloHandle.py main/xiaozhi-server/core/handle/sendAudioHandle.py main/xiaozhi-server/core/handle/textMessageType.py main/xiaozhi-server/core/handle/textMessageHandlerRegistry.py main/xiaozhi-server/core/handle/textHandler/ttsMessageHandler.py main/xiaozhi-server/config.yaml docs/development/xiaoxin-tts-playback-ack.md
```

Check:

- No unrelated ASR/VAD/LLM changes.
- Old-device fallback exists.
- ACK waits have timeouts.
- Missing `sentence_id` is ignored safely.
- Stale sentence guards remain intact.

- [ ] **Step 4: Commit verification fixes only if needed**

If Step 1 or Step 2 requires fixes, commit them:

```bash
git add <fixed-files>
git commit -m "fix: stabilize tts playback ack behavior"
```

---

## Plan Self-Review

### Spec Coverage

- Product-grade `tts ready` handshake: covered by Tasks 1, 2, 3, and 4.
- Product-grade `tts done` handshake: covered by Tasks 1, 2, 3, and 5.
- Old firmware compatibility: covered by Task 4 fallback delay and config defaults.
- Timeout safety: covered by Tasks 1, 4, and 5.
- Device firmware contract: covered by Task 6.
- Focused verification: covered by Task 7.

### Plan Risks and Countermeasures

- **Risk: The server sends `tts start` without `sentence_id`, while ACK matching requires `sentence_id`.**
  - Countermeasure: Task 3 makes outbound TTS state messages include `sentence_id` and tests both explicit and current-sentence cases.
- **Risk: Existing ESP32 firmware may treat unknown fields badly.**
  - Countermeasure: adding fields to JSON is normally backward-compatible, but if firmware uses strict parsing, start with feature-gated behavior and verify old firmware ignores `sentence_id`.
- **Risk: Device sends `ready` before server begins waiting.**
  - Countermeasure: Task 3 begins the waiter before `send_tts_message("start")`.
- **Risk: Device never sends `ready` or `done`.**
  - Countermeasure: waits have explicit config timeouts and fallback behavior.
- **Risk: The same `tts` type is now bidirectional.**
  - Countermeasure: handler only acts on `ready` and `done`; it ignores `start`, `stop`, and `sentence_start`.
- **Risk: `done` ACK may arrive before server starts waiting, especially for very short cached audio.**
  - Countermeasure: Task 4 begins the done waiter before waiting for server audio completion.
- **Risk: Control-console TTS and normal chat TTS have different sentence-id lifecycles.**
  - Countermeasure: Task 4 tests existing stale-control behavior and preserves the current stale ordinary sentence guard.

### Placeholder Scan

No task contains forbidden placeholder markers or unbounded error-handling instructions. Every task has concrete file paths, commands, and expected outcomes.

### Type Consistency

All tasks use the same state strings: `"ready"` and `"done"`. All ACK interfaces accept `(state: str, sentence_id: str)`. Feature flags are consistently named `tts_ready_ack` and `tts_done_ack`. Config values are consistently named `wakeup_response_start_delay_ms`, `tts_ready_ack_timeout_ms`, and `tts_done_ack_timeout_ms`.

### Hard Review Verdict

This plan is implementable after the self-review correction above. The strongest remaining risk is firmware state accuracy: if the ESP32 sends `ready` before the speaker path is genuinely ready or `done` before its playback queue is drained, the protocol will look correct in logs while still behaving badly. Firmware review must treat those two ACK emission points as hardware-state assertions, not generic WebSocket callbacks.
