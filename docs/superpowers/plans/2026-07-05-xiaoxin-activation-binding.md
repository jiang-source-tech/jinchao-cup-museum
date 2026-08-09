# Xiaoxin Activation Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the service-side activation-code binding loop so a newly WiFi-connected device can speak a local `.ogg` binding code, be bound from the control console, and then become wakeable through MQTT.

**Architecture:** Add a focused activation-code store beside the existing Xiaoxin identity store, wire it into the runtime, have OTA return `activation` only for unbound devices, add `/xiaozhi/ota/activate` polling, and expose a logged-in console API/UI for code binding. Preserve the current manual `device_id` binding and MQTT wake controls as a development fallback.

**Tech Stack:** Python 3, aiohttp handlers, SQLite through the existing identity-store style, pytest, static HTML/vanilla JavaScript control console.

## Global Constraints

- Do not implement server-side TTS or return audio bytes; firmware plays existing `.ogg` assets locally.
- Use stable `device_id` from the device request, currently expected to be `SystemInfo::GetMacAddress()`.
- Return `activation.code`, `activation.message`, `activation.challenge`, and `activation.timeout_ms`; firmware activation polling requires `challenge`.
- Default activation code TTL is 600 seconds.
- Manual `device_id` binding remains available as a development fallback.
- First implementation does not enforce HMAC activation validation.
- Do not revert existing uncommitted changes in `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`, `main/xiaozhi-server/core/api/static/xiaoxin_control.html`, or `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`; they contain manual bind and wake work that this plan builds around.

---

## File Structure

- `main/xiaozhi-server/core/xiaoxin/activation_store.py`: new focused SQLite-backed activation-code store with `ActivationSession` dataclass and code lifecycle methods.
- `main/xiaozhi-server/core/xiaoxin/control_runtime.py`: instantiate and expose `activation_store` in `XiaoxinControlRuntime`.
- `main/xiaozhi-server/core/api/ota_handler.py`: accept optional runtime, upsert seen devices, include activation in OTA response for unbound devices, and handle activation polling.
- `main/xiaozhi-server/core/http_server.py`: pass `xiaoxin_runtime` into `OTAHandler` and register `/xiaozhi/ota/activate`.
- `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`: add `POST /api/xiaoxin/devices/activation-bind`.
- `main/xiaozhi-server/core/api/static/xiaoxin_control.html`: add code-binding form and JavaScript handler.
- `main/xiaozhi-server/tests/xiaoxin/test_activation_store.py`: new store tests.
- `main/xiaozhi-server/tests/xiaoxin/test_ota_activation_handler.py`: new OTA activation tests.
- `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`: add activation-bind API/static-route assertions while preserving existing manual bind/wake tests.

---

### Task 0: Checkpoint Existing Manual Bind And Wake WIP

**Files:**
- Modify: none
- Test: `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`

**Interfaces:**
- Consumes: existing uncommitted manual bind and wake API changes.
- Produces: a clean commit containing the current WIP so activation work can build on it without mixing unrelated deltas.

- [ ] **Step 1: Inspect the existing WIP diff**

Run:

```powershell
git diff -- main/xiaozhi-server/core/api/xiaoxin_control_handler.py main/xiaozhi-server/core/api/static/xiaoxin_control.html main/xiaozhi-server/tests/xiaoxin/test_control_handler.py
```

Expected: diff contains `/api/xiaoxin/devices/manual-bind`, `/api/xiaoxin/devices/{device_id}/wake`, manual-bind UI, wake UI, and tests for manual bind/wake.

- [ ] **Step 2: Run the focused tests**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin/test_control_handler.py -q
```

Expected: all tests in `test_control_handler.py` pass.

- [ ] **Step 3: Run the Xiaoxin suite**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin -q
```

Expected: all Xiaoxin tests pass.

- [ ] **Step 4: Validate control-console script syntax**

Run from repo root:

```powershell
node -e "const fs=require('fs'); const html=fs.readFileSync('main/xiaozhi-server/core/api/static/xiaoxin_control.html','utf8'); const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]); for (const script of scripts) new Function(script); console.log('scripts ok:', scripts.length);"
```

Expected: `scripts ok: 1`.

- [ ] **Step 5: Commit the WIP checkpoint**

Run:

```powershell
git add -- main/xiaozhi-server/core/api/xiaoxin_control_handler.py main/xiaozhi-server/core/api/static/xiaoxin_control.html main/xiaozhi-server/tests/xiaoxin/test_control_handler.py
git commit -m "feat: add manual device bind and wake"
```

Expected: a commit containing only the existing manual bind/wake changes.

---

### Task 1: Activation Store

**Files:**
- Create: `main/xiaozhi-server/core/xiaoxin/activation_store.py`
- Test: `main/xiaozhi-server/tests/xiaoxin/test_activation_store.py`

**Interfaces:**
- Produces:
  - `ActivationSession`
  - `XiaoxinActivationStore(db_path: str | Path)`
  - `create_or_refresh_activation(device_id: str, ttl_seconds: int = 600) -> ActivationSession`
  - `get_activation_by_code(code: str) -> ActivationSession | None`
  - `get_activation_by_device_id(device_id: str) -> ActivationSession | None`
  - `mark_activation_consumed(code: str) -> None`
  - `is_expired(session: ActivationSession) -> bool`
  - `delete_expired_activations() -> int`

- [ ] **Step 1: Write failing store tests**

Create `main/xiaozhi-server/tests/xiaoxin/test_activation_store.py`:

```python
from datetime import datetime, timedelta, timezone

from core.xiaoxin.activation_store import XiaoxinActivationStore


def test_create_or_refresh_activation_reuses_live_code_for_same_device(tmp_path):
    store = XiaoxinActivationStore(tmp_path / "activation.db")

    first = store.create_or_refresh_activation("device-1", ttl_seconds=600)
    second = store.create_or_refresh_activation("device-1", ttl_seconds=600)

    assert first.device_id == "device-1"
    assert first.code == second.code
    assert len(first.code) == 6
    assert first.code.isdigit()
    assert first.challenge == second.challenge
    assert first.consumed_at is None


def test_consumed_activation_is_not_reused(tmp_path):
    store = XiaoxinActivationStore(tmp_path / "activation.db")

    first = store.create_or_refresh_activation("device-1", ttl_seconds=600)
    store.mark_activation_consumed(first.code)
    second = store.create_or_refresh_activation("device-1", ttl_seconds=600)

    assert second.code != first.code
    assert store.get_activation_by_code(first.code).consumed_at is not None
    assert store.get_activation_by_code(second.code).consumed_at is None


def test_expired_activation_is_deleted(tmp_path):
    store = XiaoxinActivationStore(tmp_path / "activation.db")
    session = store.create_or_refresh_activation("device-1", ttl_seconds=-1)

    assert store.is_expired(session) is True
    assert store.delete_expired_activations() == 1
    assert store.get_activation_by_code(session.code) is None


def test_lookup_by_device_id_returns_latest_live_session(tmp_path):
    store = XiaoxinActivationStore(tmp_path / "activation.db")

    session = store.create_or_refresh_activation("device-1", ttl_seconds=600)

    assert store.get_activation_by_device_id("device-1").code == session.code
    assert store.get_activation_by_device_id("missing") is None
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin/test_activation_store.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'core.xiaoxin.activation_store'`.

- [ ] **Step 3: Implement the store**

Create `main/xiaozhi-server/core/xiaoxin/activation_store.py`:

```python
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.xiaoxin.identity.ids import new_id


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class ActivationSession:
    id: str
    device_id: str
    code: str
    challenge: str
    message: str
    expires_at: str
    consumed_at: str | None
    created_at: str
    last_seen_at: str


class XiaoxinActivationStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS device_activation_codes (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    code TEXT UNIQUE NOT NULL,
                    challenge TEXT NOT NULL,
                    message TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
                """
            )

    def create_or_refresh_activation(
        self, device_id: str, ttl_seconds: int = 600
    ) -> ActivationSession:
        safe_device_id = device_id.strip()
        if not safe_device_id:
            raise ValueError("device_id required")
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM device_activation_codes
                WHERE device_id = ?
                  AND consumed_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (safe_device_id,),
            ).fetchone()
            if row is not None:
                session = self._from_row(row)
                if not self.is_expired(session):
                    conn.execute(
                        """
                        UPDATE device_activation_codes
                        SET last_seen_at = ?
                        WHERE id = ?
                        """,
                        (now, session.id),
                    )
                    return self.get_activation_by_code(session.code)

            session_id = new_id("act")
            code = self._new_unique_code(conn)
            challenge = secrets.token_urlsafe(24)
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat()
            message = f"请在控制台输入绑定码 {code}"
            conn.execute(
                """
                INSERT INTO device_activation_codes (
                    id, device_id, code, challenge, message,
                    expires_at, consumed_at, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    safe_device_id,
                    code,
                    challenge,
                    message,
                    expires_at,
                    None,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM device_activation_codes WHERE id = ?",
                (session_id,),
            ).fetchone()
        return self._from_row(row)

    def get_activation_by_code(self, code: str) -> ActivationSession | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM device_activation_codes WHERE code = ?",
                (code.strip(),),
            ).fetchone()
        return self._from_row(row) if row else None

    def get_activation_by_device_id(self, device_id: str) -> ActivationSession | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM device_activation_codes
                WHERE device_id = ?
                  AND consumed_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (device_id.strip(),),
            ).fetchone()
        return self._from_row(row) if row else None

    def mark_activation_consumed(self, code: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE device_activation_codes
                SET consumed_at = ?
                WHERE code = ?
                  AND consumed_at IS NULL
                """,
                (utc_now_iso(), code.strip()),
            )

    def is_expired(self, session: ActivationSession) -> bool:
        return _parse_iso(session.expires_at) <= datetime.now(timezone.utc)

    def delete_expired_activations(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM device_activation_codes
                WHERE expires_at <= ?
                """,
                (utc_now_iso(),),
            )
            return cursor.rowcount

    def _new_unique_code(self, conn: sqlite3.Connection) -> str:
        for _ in range(20):
            code = f"{secrets.randbelow(1_000_000):06d}"
            row = conn.execute(
                "SELECT 1 FROM device_activation_codes WHERE code = ?",
                (code,),
            ).fetchone()
            if row is None:
                return code
        raise RuntimeError("failed to generate unique activation code")

    def _from_row(self, row: sqlite3.Row) -> ActivationSession:
        return ActivationSession(
            id=row["id"],
            device_id=row["device_id"],
            code=row["code"],
            challenge=row["challenge"],
            message=row["message"],
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
        )
```

- [ ] **Step 4: Run store tests**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin/test_activation_store.py -q
```

Expected: all activation store tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add -- main/xiaozhi-server/core/xiaoxin/activation_store.py main/xiaozhi-server/tests/xiaoxin/test_activation_store.py
git commit -m "feat: add xiaoxin activation store"
```

---

### Task 2: Runtime And OTA Route Wiring

**Files:**
- Modify: `main/xiaozhi-server/core/xiaoxin/control_runtime.py`
- Modify: `main/xiaozhi-server/core/http_server.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`

**Interfaces:**
- Consumes: `XiaoxinActivationStore` from Task 1.
- Produces: `runtime.activation_store` and HTTP route `POST /xiaozhi/ota/activate`.

- [ ] **Step 1: Add failing runtime and route tests**

In `main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py`, extend the runtime creation test with:

```python
def test_runtime_exposes_activation_store(tmp_path):
    from core.xiaoxin.control_runtime import create_xiaoxin_control_runtime

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
```

In `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`, update the fake OTA handler inside the app route test so it defines:

```python
async def handle_activate(self, request):
    return web.Response(text="ok")
```

Then assert:

```python
assert ("POST", "/xiaozhi/ota/activate") in routes
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin/test_control_runtime.py::test_runtime_exposes_activation_store tests/xiaoxin/test_control_handler.py::test_http_server_registers_expected_routes -q
```

Expected: FAIL because `activation_store` and the activate route do not exist.

- [ ] **Step 3: Wire runtime**

Modify `main/xiaozhi-server/core/xiaoxin/control_runtime.py`:

```python
from core.xiaoxin.activation_store import XiaoxinActivationStore
```

Add the dataclass field:

```python
activation_store: XiaoxinActivationStore
```

Inside `create_xiaoxin_control_runtime`, after `identity_store`:

```python
activation_db = control.get("activation_db") or "data/xiaoxin_activation.db"
activation_db_path = Path(activation_db)
if not activation_db_path.is_absolute():
    activation_db_path = Path(get_project_dir()) / activation_db_path
activation_store = XiaoxinActivationStore(activation_db_path)
```

Pass `activation_store` into the returned `XiaoxinControlRuntime`.

- [ ] **Step 4: Wire OTA handler and route**

Modify `main/xiaozhi-server/core/http_server.py`:

```python
self.ota_handler = OTAHandler(config, xiaoxin_runtime)
```

Add the route next to the existing OTA routes:

```python
web.post("/xiaozhi/ota/activate", self.ota_handler.handle_activate),
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin/test_control_runtime.py::test_runtime_exposes_activation_store tests/xiaoxin/test_control_handler.py::test_http_server_registers_expected_routes -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add -- main/xiaozhi-server/core/xiaoxin/control_runtime.py main/xiaozhi-server/core/http_server.py main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py main/xiaozhi-server/tests/xiaoxin/test_control_handler.py
git commit -m "feat: wire xiaoxin activation runtime"
```

---

### Task 3: OTA Activation Response And Polling

**Files:**
- Modify: `main/xiaozhi-server/core/api/ota_handler.py`
- Create: `main/xiaozhi-server/tests/xiaoxin/test_ota_activation_handler.py`

**Interfaces:**
- Consumes: `runtime.identity_store`, `runtime.activation_store`.
- Produces:
  - `OTAHandler(config: dict, xiaoxin_runtime: Any | None = None)`
  - `OTAHandler.handle_activate(request: web.Request) -> web.Response`

- [ ] **Step 1: Write failing OTA tests**

Create `main/xiaozhi-server/tests/xiaoxin/test_ota_activation_handler.py`:

```python
import json

from aiohttp.test_utils import make_mocked_request

from core.api.ota_handler import OTAHandler
from core.xiaoxin.activation_store import XiaoxinActivationStore
from core.xiaoxin.identity.auth import XiaoxinAuthService
from core.xiaoxin.identity.store import XiaoxinIdentityStore


class Runtime:
    def __init__(self, tmp_path):
        self.identity_store = XiaoxinIdentityStore(tmp_path / "identity.db")
        self.auth_service = XiaoxinAuthService(self.identity_store)
        self.activation_store = XiaoxinActivationStore(tmp_path / "activation.db")


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


def _ota_request(device_id="device-1"):
    request = make_mocked_request(
        "POST",
        "/xiaozhi/ota/",
        headers={"Device-Id": device_id, "Client-Id": "client-1"},
    )
    request._read_bytes = json.dumps(
        {"application": {"version": "1.0.0"}, "board": {"type": "default"}}
    ).encode("utf-8")
    return request


def test_unbound_device_ota_response_contains_activation(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        handler = OTAHandler(_config(), runtime)
        response = await handler.handle_post(_ota_request())
        return response.status, json.loads(response.text), runtime

    import asyncio

    status, body, runtime = asyncio.run(scenario())

    assert status == 200
    assert body["activation"]["code"].isdigit()
    assert len(body["activation"]["code"]) == 6
    assert body["activation"]["challenge"]
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

    import asyncio

    status, body = asyncio.run(scenario())

    assert status == 200
    assert "activation" not in body


def test_activate_returns_202_until_bound_then_200(tmp_path):
    async def scenario():
        runtime = Runtime(tmp_path)
        session = runtime.activation_store.create_or_refresh_activation("device-1")
        handler = OTAHandler(_config(), runtime)
        waiting = make_mocked_request(
            "POST",
            "/xiaozhi/ota/activate",
            headers={"Device-Id": "device-1"},
        )
        waiting._read_bytes = b"{}"
        waiting_response = await handler.handle_activate(waiting)
        user, _ = runtime.auth_service.register("liu", "secret-pass", "Liu")
        runtime.identity_store.upsert_seen_device("device-1")
        runtime.identity_store.bind_device("device-1", user.id, "Desk")
        done = make_mocked_request(
            "POST",
            "/xiaozhi/ota/activate",
            headers={"Device-Id": "device-1"},
        )
        done._read_bytes = b"{}"
        done_response = await handler.handle_activate(done)
        return waiting_response.status, done_response.status, session

    import asyncio

    waiting_status, done_status, session = asyncio.run(scenario())

    assert session.code
    assert waiting_status == 202
    assert done_status == 200
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin/test_ota_activation_handler.py -q
```

Expected: FAIL because `OTAHandler` does not accept runtime and `handle_activate` does not exist.

- [ ] **Step 3: Implement OTA runtime support**

Modify the constructor in `main/xiaozhi-server/core/api/ota_handler.py`:

```python
from typing import Any, Dict, List, Tuple
```

Change:

```python
def __init__(self, config: dict):
```

to:

```python
def __init__(self, config: dict, xiaoxin_runtime: Any | None = None):
```

and assign:

```python
self.xiaoxin_runtime = xiaoxin_runtime
```

- [ ] **Step 4: Add activation helper methods**

Add methods to `OTAHandler`:

```python
def _activation_ttl_ms(self) -> int:
    control = self.config.get("xiaoxin_control", {}) or {}
    return int(control.get("activation_timeout_ms", 600000))


def _maybe_attach_activation(self, return_json: dict, device_id: str) -> None:
    runtime = self.xiaoxin_runtime
    if runtime is None:
        return
    if not hasattr(runtime, "identity_store") or not hasattr(runtime, "activation_store"):
        return
    if not device_id:
        return
    device = runtime.identity_store.upsert_seen_device(device_id)
    if device.owner_user_id is not None:
        return
    ttl_ms = self._activation_ttl_ms()
    session = runtime.activation_store.create_or_refresh_activation(
        device_id,
        ttl_seconds=max(1, ttl_ms // 1000),
    )
    return_json["activation"] = {
        "code": session.code,
        "message": session.message,
        "challenge": session.challenge,
        "timeout_ms": ttl_ms,
    }
```

Call it in `handle_post` after `return_json` has firmware/server/mqtt/websocket fields and before creating the response:

```python
self._maybe_attach_activation(return_json, device_id)
```

- [ ] **Step 5: Add activation polling handler**

Add to `OTAHandler`:

```python
async def handle_activate(self, request):
    response = None
    try:
        device_id = request.headers.get("device-id") or request.headers.get("Device-Id") or ""
        device_id = device_id.strip()
        runtime = self.xiaoxin_runtime
        if not device_id or runtime is None:
            response = web.Response(status=404, text="")
            return response
        if not hasattr(runtime, "identity_store") or not hasattr(runtime, "activation_store"):
            response = web.Response(status=404, text="")
            return response
        device = runtime.identity_store.get_device_by_device_id(device_id)
        if device is not None and device.owner_user_id is not None:
            response = web.Response(status=200, text="")
            return response
        session = runtime.activation_store.get_activation_by_device_id(device_id)
        if session is None:
            response = web.Response(status=404, text="")
            return response
        if runtime.activation_store.is_expired(session):
            response = web.Response(status=410, text="")
            return response
        response = web.Response(status=202, text="")
        return response
    finally:
        if response is not None:
            self._add_cors_headers(response)
```

- [ ] **Step 6: Run OTA tests**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin/test_ota_activation_handler.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add -- main/xiaozhi-server/core/api/ota_handler.py main/xiaozhi-server/tests/xiaoxin/test_ota_activation_handler.py
git commit -m "feat: add xiaoxin ota activation flow"
```

---

### Task 4: Control Console Activation Bind API

**Files:**
- Modify: `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`

**Interfaces:**
- Consumes: `runtime.activation_store`, `runtime.identity_store`.
- Produces: `POST /api/xiaoxin/devices/activation-bind`.

- [ ] **Step 1: Add failing control-handler tests**

In `AuthRuntime.__init__` inside `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`, add:

```python
from core.xiaoxin.activation_store import XiaoxinActivationStore
self.activation_store = XiaoxinActivationStore(db_path.with_name("xiaoxin_activation.db"))
```

Add tests:

```python
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
```

Update static and route assertions:

```python
assert "/api/xiaoxin/devices/activation-bind" in html
assert "activationBindDevice" in html
assert ("POST", "/api/xiaoxin/devices/activation-bind") in routes
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin/test_control_handler.py -q
```

Expected: FAIL because route, handler, and static UI are missing.

- [ ] **Step 3: Add route and handler**

In `XiaoxinControlHandler.add_routes`, add before manual-bind:

```python
web.post(
    "/api/xiaoxin/devices/activation-bind",
    self.handle_activation_bind_device,
),
```

Add method:

```python
async def handle_activation_bind_device(self, request: web.Request) -> web.Response:
    denied = self._deny_if_unauthorized(request)
    if denied is not None:
        return denied

    if not hasattr(self.runtime, "identity_store") or not hasattr(
        self.runtime, "activation_store"
    ):
        return self._json({"success": False, "message": "auth unavailable"}, status=404)

    try:
        payload = json.loads(await request.text())
    except json.JSONDecodeError:
        return self._json(
            {"success": False, "message": "invalid json", "field": "body"},
            status=400,
        )

    code = str(payload.get("code") or "").strip()
    display_name = str(payload.get("display_name") or "").strip()
    if not code or not code.isdigit() or len(code) != 6:
        return self._json(
            {"success": False, "message": "code required", "field": "code"},
            status=400,
        )

    session = self.runtime.activation_store.get_activation_by_code(code)
    if session is None or session.consumed_at is not None:
        return self._json(
            {"success": False, "message": "activation code not found"},
            status=404,
        )
    if self.runtime.activation_store.is_expired(session):
        return self._json(
            {"success": False, "message": "activation code expired"},
            status=410,
        )

    user = request["xiaoxin_user"]
    self.runtime.identity_store.upsert_seen_device(
        session.device_id,
        display_name or session.device_id,
    )
    try:
        device = self.runtime.identity_store.bind_device(
            session.device_id,
            user.id,
            display_name or session.device_id,
        )
    except ValueError as exc:
        status = 409 if "already bound" in str(exc) else 400
        return self._json({"success": False, "message": str(exc)}, status=status)

    self.runtime.activation_store.mark_activation_consumed(code)
    return self._json({"success": True, "device": self._device_payload(device)})
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin/test_control_handler.py::test_logged_in_user_can_bind_device_by_activation_code tests/xiaoxin/test_control_handler.py::test_activation_bind_rejects_unknown_code tests/xiaoxin/test_control_handler.py::test_activation_bind_rejects_expired_code -q
```

Expected: activation-bind API tests pass; static assertion may still fail until Task 5.

- [ ] **Step 5: Commit API work only if static assertions are not part of the same test run**

If route/API tests pass and static UI tests are not failing in the focused selection, run:

```powershell
git add -- main/xiaozhi-server/core/api/xiaoxin_control_handler.py main/xiaozhi-server/tests/xiaoxin/test_control_handler.py
git commit -m "feat: add activation code bind api"
```

If the full file still fails only because static HTML is missing, continue to Task 5 and commit API+UI together.

---

### Task 5: Control Console Activation Bind UI

**Files:**
- Modify: `main/xiaozhi-server/core/api/static/xiaoxin_control.html`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`

**Interfaces:**
- Consumes: `POST /api/xiaoxin/devices/activation-bind`.
- Produces: user-visible code-binding form and `activationBindDevice(event)`.

- [ ] **Step 1: Add the activation form**

In the device section of `xiaoxin_control.html`, add a compact form near the manual-bind form:

```html
<form id="activationBindForm" class="device-bind-form">
  <input
    name="code"
    inputmode="numeric"
    maxlength="6"
    placeholder="绑定码"
    autocomplete="one-time-code"
  />
  <input name="display_name" placeholder="设备名称" />
  <button type="submit">验证码绑定</button>
</form>
```

Use existing classes/styles if the file already defines form styling around the manual bind form. Do not create a marketing-style block.

- [ ] **Step 2: Add JavaScript handler**

Inside the existing `<script>`, add:

```javascript
async function activationBindDevice(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  const code = String(formData.get("code") || "").trim();
  const displayName = String(formData.get("display_name") || "").trim();
  if (!/^\d{6}$/.test(code)) {
    setStatus("请输入 6 位绑定码");
    return;
  }
  const response = await apiFetch("/api/xiaoxin/devices/activation-bind", {
    method: "POST",
    body: JSON.stringify({
      code,
      display_name: displayName,
    }),
  });
  if (!response.success) {
    setStatus(response.message || "绑定失败");
    return;
  }
  form.reset();
  setStatus("设备已绑定");
  await loadDevices();
}
```

Register it near existing event listeners:

```javascript
document
  .getElementById("activationBindForm")
  .addEventListener("submit", activationBindDevice);
```

- [ ] **Step 3: Run syntax and static tests**

Run:

```powershell
node -e "const fs=require('fs'); const html=fs.readFileSync('main/xiaozhi-server/core/api/static/xiaoxin_control.html','utf8'); const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]); for (const script of scripts) new Function(script); console.log('scripts ok:', scripts.length);"
Set-Location main/xiaozhi-server
pytest tests/xiaoxin/test_control_handler.py -q
```

Expected: script syntax passes and control-handler tests pass.

- [ ] **Step 4: Commit UI work**

Run:

```powershell
git add -- main/xiaozhi-server/core/api/static/xiaoxin_control.html main/xiaozhi-server/tests/xiaoxin/test_control_handler.py
git commit -m "feat: add activation code bind console"
```

If Task 4 was not committed separately, include `xiaoxin_control_handler.py` in this commit too.

---

### Task 6: End-To-End Verification And Push

**Files:**
- Modify: none expected
- Test: full Xiaoxin suite and static console syntax

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified branch pushed to GitHub.

- [ ] **Step 1: Run full Xiaoxin tests**

Run:

```powershell
Set-Location main/xiaozhi-server
pytest tests/xiaoxin -q
```

Expected: all Xiaoxin tests pass.

- [ ] **Step 2: Run JS syntax check**

Run from repo root:

```powershell
node -e "const fs=require('fs'); const html=fs.readFileSync('main/xiaozhi-server/core/api/static/xiaoxin_control.html','utf8'); const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]); for (const script of scripts) new Function(script); console.log('scripts ok:', scripts.length);"
```

Expected: `scripts ok: 1`.

- [ ] **Step 3: Run diff check**

Run:

```powershell
git diff --check
```

Expected: no output.

- [ ] **Step 4: Inspect final status and log**

Run:

```powershell
git status --short
git log --oneline -6
```

Expected: no unstaged implementation changes except intentional local files; recent commits include activation store, runtime wiring, OTA flow, and console binding.

- [ ] **Step 5: Push**

Run:

```powershell
git push origin main
```

Expected: push succeeds.

---

## Self-Review

Spec coverage:

- OTA `activation` response for unbound devices: Task 3.
- Code TTL, challenge, message, and code reuse: Task 1 and Task 3.
- `/xiaozhi/ota/activate` 202/200/404/410 behavior: Task 3.
- Logged-in console code binding: Task 4.
- Control console UI: Task 5.
- Manual bind/wake preservation: Task 0 and regression runs in Task 6.
- Full verification and push: Task 6.

Placeholder scan:

- Placeholder scan passed outside this self-review section.

Type consistency:

- `ActivationSession`, `XiaoxinActivationStore`, `activation_store`, `handle_activate`, and `handle_activation_bind_device` are defined before later tasks consume them.
