# Xiaoxin Control Text Chat Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow `/xiaoxin/control/` testing entry that injects typed text into an online device's real chat flow so the board speaks the LLM response.

**Architecture:** Keep the debug feature behind one small connection-layer interface, `ConnectionHandler.submit_control_text_chat(text: str) -> None`. The HTTP handler owns auth, ownership, text validation, and online connection lookup; the frontend only submits text and shows status. The existing `chat(text)` path continues to own XiaoxinRuntime, LLM, dialogue history, memory behavior, tools, and TTS.

**Tech Stack:** Python 3, aiohttp handlers, existing `ConnectionHandler`, existing Xiaoxin registry/runtime, single-file HTML/JavaScript control console, pytest.

## Global Constraints

- This is a test entry, not a product chat entry.
- First version only supports online `connected` devices.
- Do not implement offline wake, retry, queueing, delivery records, or chat history.
- Do not reuse `/api/xiaoxin/events`.
- Do not create an independent LLM/TTS path.
- Text input maximum length is 500 characters after trimming.
- The endpoint must require login and current-user device ownership.
- Sending text should clear the current audio input state before entering `chat(text)`.
- Leave unrelated uncommitted files untouched.

---

## File Structure

- Modify `main/xiaozhi-server/core/connection.py`
  - Add the connection-level debug interface `submit_control_text_chat`.
  - Keep implementation thin: reset audio state, reset abort flag for the new turn, call `chat(text)` through `asyncio.to_thread` so the HTTP request does not block the event loop.

- Modify `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`
  - Register `POST /api/xiaoxin/devices/{device_id}/text-chat`.
  - Add `handle_device_text_chat`.
  - Keep validation and ownership here.

- Modify `main/xiaozhi-server/core/api/static/xiaoxin_control.html`
  - Add a small `文本测试对话` section.
  - Add `textChatInput`, `sendTextChatBtn`, and a tiny submit function.
  - Reuse the currently selected target device from `deviceSelect`.

- Modify `main/xiaozhi-server/tests/xiaoxin/test_connection_integration.py`
  - Add connection-level tests for clearing audio state and invoking `chat`.

- Modify `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`
  - Add handler tests for auth, ownership, validation, disconnected device behavior, and successful submission.

- Modify `main/xiaozhi-server/tests/xiaoxin/test_control_console_static.py`
  - Add static assertions for the new section, IDs, and endpoint path.

---

### Task 1: Connection Text Injection Interface

**Files:**
- Modify: `main/xiaozhi-server/core/connection.py`
- Test: `main/xiaozhi-server/tests/xiaoxin/test_connection_integration.py`

**Interfaces:**
- Consumes: existing `ConnectionHandler.reset_audio_states() -> None`
- Consumes: existing `ConnectionHandler.chat(query: str, depth: int = 0) -> bool | None`
- Produces: `async def ConnectionHandler.submit_control_text_chat(self, text: str) -> None`

- [ ] **Step 1: Write the failing connection test**

Append these tests to `main/xiaozhi-server/tests/xiaoxin/test_connection_integration.py`:

```python
def test_submit_control_text_chat_clears_audio_state_and_calls_chat(monkeypatch):
    result = SimpleNamespace(
        handled=True,
        reply="收到",
        model="fake",
        route={},
        memory_result=None,
        relationship=None,
    )
    conn = make_conn(result)
    conn.client_have_voice = True
    conn.client_voice_stop = True
    conn.asr_audio.append(b"old-audio")
    calls = []

    def fake_chat(text):
        calls.append(text)
        return True

    monkeypatch.setattr(conn, "chat", fake_chat)

    asyncio.run(conn.submit_control_text_chat("  你能听到我吗？  "))

    assert calls == ["你能听到我吗？"]
    assert conn.client_have_voice is False
    assert conn.client_voice_stop is False
    assert conn.asr_audio == []
    assert conn.client_abort is False


def test_submit_control_text_chat_rejects_empty_text():
    conn = make_conn(SimpleNamespace(handled=False))

    with pytest.raises(ValueError, match="text is empty"):
        asyncio.run(conn.submit_control_text_chat("   "))
```

- [ ] **Step 2: Run the failing connection tests**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server\main\xiaozhi-server
python -m pytest tests/xiaoxin/test_connection_integration.py::test_submit_control_text_chat_clears_audio_state_and_calls_chat tests/xiaoxin/test_connection_integration.py::test_submit_control_text_chat_rejects_empty_text -q
```

Expected: FAIL because `ConnectionHandler` has no `submit_control_text_chat`.

- [ ] **Step 3: Implement the connection method**

In `main/xiaozhi-server/core/connection.py`, insert this method after `chat_and_close` and before `send_xiaoxin_event`:

```python
    async def submit_control_text_chat(self, text: str) -> None:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("text is empty")

        self.reset_audio_states()
        self.client_abort = False
        await asyncio.to_thread(self.chat, clean_text)
```

- [ ] **Step 4: Run the connection tests**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server\main\xiaozhi-server
python -m pytest tests/xiaoxin/test_connection_integration.py::test_submit_control_text_chat_clears_audio_state_and_calls_chat tests/xiaoxin/test_connection_integration.py::test_submit_control_text_chat_rejects_empty_text -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server
git add main/xiaozhi-server/core/connection.py main/xiaozhi-server/tests/xiaoxin/test_connection_integration.py
git commit -m "feat: add control text chat connection hook"
```

---

### Task 2: Control Handler Endpoint

**Files:**
- Modify: `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`

**Interfaces:**
- Consumes: `ConnectionHandler.submit_control_text_chat(text: str) -> None`
- Consumes: existing `XiaoxinDeviceRegistry.get_connection(device_id: str) -> Any | None`
- Produces: `POST /api/xiaoxin/devices/{device_id}/text-chat`
- Produces: `async def XiaoxinControlHandler.handle_device_text_chat(self, request: web.Request) -> web.Response`

- [ ] **Step 1: Write failing handler tests**

In `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`, add this helper near `OverviewConnection`:

```python
class TextChatConnection:
    def __init__(self):
        self.submitted_texts = []

    async def submit_control_text_chat(self, text):
        self.submitted_texts.append(text)
```

Append these tests near the existing control event ownership tests:

```python
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
            {"text": "  你能听到我吗？  "},
        )
        return response.status, json.loads(response.text), conn.submitted_texts

    status, body, submitted_texts = asyncio.run(scenario())

    assert status == 200
    assert body == {"success": True, "message": "submitted"}
    assert submitted_texts == ["你能听到我吗？"]


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


def test_text_chat_endpoint_rejects_other_users_device(tmp_path):
    async def scenario():
        runtime = AuthRuntime(tmp_path / "xiaoxin_control.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _alice, alice_token = runtime.auth_service.register("alice", "secret-pass", "Alice")
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

    assert status == 404
    assert body == {"success": False, "message": "device not found"}


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
```

In `test_http_server_build_app_mounts_control_routes_when_runtime_present`, add:

```python
    assert ("POST", "/api/xiaoxin/devices/{device_id}/text-chat") in routes
```

- [ ] **Step 2: Run failing handler tests**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server\main\xiaozhi-server
python -m pytest tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_submits_text_to_owned_connected_device tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_requires_session tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_rejects_other_users_device tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_rejects_empty_text tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_rejects_text_over_500_characters tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_rejects_disconnected_device tests/xiaoxin/test_control_handler.py::test_http_server_build_app_mounts_control_routes_when_runtime_present -q
```

Expected: FAIL because the route and handler do not exist.

- [ ] **Step 3: Register the route**

In `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`, inside `add_routes`, add this route immediately after the wake route:

```python
                web.post(
                    "/api/xiaoxin/devices/{device_id}/text-chat",
                    self.handle_device_text_chat,
                ),
```

- [ ] **Step 4: Implement the handler**

In `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`, add this method after `handle_wake_device` if present, or before `handle_sync_device_overview`:

```python
    async def handle_device_text_chat(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        device_id = str(request.match_info.get("device_id") or "").strip()
        device_denied = self._deny_if_device_not_owned(request, device_id)
        if device_denied is not None:
            return device_denied

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        text = str(payload.get("text") or "").strip()
        if not text:
            return self._json(
                {"success": False, "message": "text required", "field": "text"},
                status=400,
            )
        if len(text) > 500:
            return self._json(
                {"success": False, "message": "text too long", "field": "text"},
                status=400,
            )

        conn = self.runtime.registry.get_connection(device_id)
        if conn is None:
            return self._json(
                {"success": False, "message": "device not connected"},
                status=409,
            )

        try:
            await conn.submit_control_text_chat(text)
        except ValueError as exc:
            return self._json(
                {"success": False, "message": str(exc), "field": "text"},
                status=400,
            )
        except Exception:
            return self._json(
                {"success": False, "message": "text chat failed"},
                status=500,
            )

        return self._json({"success": True, "message": "submitted"})
```

- [ ] **Step 5: Run handler tests**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server\main\xiaozhi-server
python -m pytest tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_submits_text_to_owned_connected_device tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_requires_session tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_rejects_other_users_device tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_rejects_empty_text tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_rejects_text_over_500_characters tests/xiaoxin/test_control_handler.py::test_text_chat_endpoint_rejects_disconnected_device tests/xiaoxin/test_control_handler.py::test_http_server_build_app_mounts_control_routes_when_runtime_present -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server
git add main/xiaozhi-server/core/api/xiaoxin_control_handler.py main/xiaozhi-server/tests/xiaoxin/test_control_handler.py
git commit -m "feat: add control text chat endpoint"
```

---

### Task 3: Control Console UI

**Files:**
- Modify: `main/xiaozhi-server/core/api/static/xiaoxin_control.html`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_console_static.py`

**Interfaces:**
- Consumes: `POST /api/xiaoxin/devices/{device_id}/text-chat`
- Consumes: existing `deviceSelect`
- Produces: HTML controls with IDs `textChatInput` and `sendTextChatBtn`
- Produces: JavaScript function `sendTextChat()`

- [ ] **Step 1: Write failing static test**

Append this test to `main/xiaozhi-server/tests/xiaoxin/test_control_console_static.py`:

```python
def test_control_console_includes_text_chat_test_panel():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="textChatInput"' in html
    assert 'id="sendTextChatBtn"' in html
    assert "async function sendTextChat()" in html
    assert "/api/xiaoxin/devices/${encodeURIComponent(deviceId)}/text-chat" in html
    assert "textChatInput.value.trim()" in html
```

- [ ] **Step 2: Run the failing static test**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server\main\xiaozhi-server
python -m pytest tests/xiaoxin/test_control_console_static.py::test_control_console_includes_text_chat_test_panel -q
```

Expected: FAIL because the controls and function do not exist.

- [ ] **Step 3: Add the HTML section**

In `main/xiaozhi-server/core/api/static/xiaoxin_control.html`, add this section inside `#appShell`, after the first device/template grid and before the speakers/memory grid:

```html
    <section>
      <h2>文本测试对话</h2>
      <form id="textChatForm">
        <label class="full">测试文本
          <textarea id="textChatInput" name="text" maxlength="500"></textarea>
        </label>
        <button class="full secondary" type="submit" id="sendTextChatBtn">发送文本测试</button>
      </form>
    </section>
```

- [ ] **Step 4: Add the JavaScript submit function**

In `main/xiaozhi-server/core/api/static/xiaoxin_control.html`, after `syncDemoOverview()` and before `loadDeliveries()`, add:

```javascript
  async function sendTextChat() {
    const deviceId = deviceSelect.value;
    const textChatInput = document.querySelector("#textChatInput");
    const text = textChatInput.value.trim();
    if (!deviceId) {
      setStatus("请选择目标设备");
      return;
    }
    const device = state.devices.find((item) => item.device_id === deviceId);
    if (!device || device.state !== "connected") {
      setStatus("请选择在线设备");
      return;
    }
    if (!text) {
      setStatus("文本不能为空");
      return;
    }

    const response = await apiFetch(`/api/xiaoxin/devices/${encodeURIComponent(deviceId)}/text-chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await response.json().catch(() => ({}));
    setStatus(response.ok ? "已提交到设备" : (data.message || "发送失败"));
  }
```

Near the other event listeners, add:

```javascript
  document.querySelector("#textChatForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendTextChat();
  });
```

- [ ] **Step 5: Run static tests**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server\main\xiaozhi-server
python -m pytest tests/xiaoxin/test_control_console_static.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server
git add main/xiaozhi-server/core/api/static/xiaoxin_control.html main/xiaozhi-server/tests/xiaoxin/test_control_console_static.py
git commit -m "feat: add control text chat test panel"
```

---

### Task 4: Focused Regression Run

**Files:**
- No source changes expected.
- Verifies: connection, handler, static console tests.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: passing focused regression evidence.

- [ ] **Step 1: Run focused Xiaoxin tests**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server\main\xiaozhi-server
python -m pytest tests/xiaoxin/test_connection_integration.py tests/xiaoxin/test_control_handler.py tests/xiaoxin/test_control_console_static.py -q
```

Expected: PASS.

- [ ] **Step 2: Inspect working tree**

Run:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server
git status --short
```

Expected: Only pre-existing unrelated files may remain:

```text
 M main/xiaozhi-server/core/providers/tts/qwen_realtime.py
?? main/xiaozhi-server/tests/xiaoxin/test_qwen_realtime_tts.py
```

- [ ] **Step 3: Manual smoke test with hardware**

Run the server:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server\main\xiaozhi-server
python app.py
```

Open:

```text
http://127.0.0.1:8003/xiaoxin/control/
```

Manual checks:

- Log in.
- Confirm a bound device shows `connected`.
- Select that device in the event target dropdown.
- Enter `你现在能听到我吗？` in the text test panel.
- Click `发送文本测试`.
- Expected: browser status becomes `已提交到设备`; the board speaks an LLM-generated answer; no new delivery record is created.

- [ ] **Step 4: Commit manual-test note if docs are updated**

If the implementation adds a short docs note to `docs/development/xiaoxin-control-console.md`, commit it with:

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server
git add docs/development/xiaoxin-control-console.md
git commit -m "docs: document control text chat test"
```

If no docs file is changed, skip this commit.

---

## Self-Review

- Spec coverage: Task 1 covers the narrow connection interface and audio-state clearing. Task 2 covers auth, ownership, online-only behavior, separate endpoint, validation, and no delivery record creation. Task 3 covers the control console panel. Task 4 covers focused regression and hardware smoke testing.
- Red-flag scan: The plan contains no red-flag markers or unspecified implementation steps.
- Type consistency: The produced interface is consistently named `submit_control_text_chat(text: str) -> None`; the handler and tests call that exact method. The route is consistently `POST /api/xiaoxin/devices/{device_id}/text-chat`.
