# Xiaoxin MQTT Overview Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the server, persistence, weather, MQTT publisher, Broker ACL, API triggers, and diagnostics required to publish each bound device's latest weather/course/todo Overview as a device-scoped QoS 1 retained MQTT snapshot.

**Architecture:** Add a focused `core/xiaoxin/overview/` package containing the persistent snapshot store, IP/weather providers, projection builder, sync service, and MQTT publication coordinator. Reuse the existing service MQTT connection through explicit publish/connect/PUBACK hooks, keep HTTP handlers thin, and preserve course/todo writes even when MQTT or weather is unavailable.

**Tech Stack:** Python 3, aiohttp, SQLite, paho-mqtt, pytest, Mosquitto ACL/password files, Open-Meteo REST APIs.

## Global Constraints

- Production Overview topics are `device/{device_id}/overview`.
- MQTT Overview publishes use QoS 1 and `retain=true`.
- WebSocket remains the voice/audio/ACK path and is not used by automatic Overview synchronization.
- Overview payload version is exactly `1`; device revision is monotonically increasing.
- Remote Overview fields are weather, course, todo, binding metadata, revision, and generation time only.
- Weather means the current city's daily forecast, not real-time observations.
- Normal weather cadence is one successful provider request per city per local date.
- Raw public IPs are not persisted; compare keyed HMAC values.
- Weather/IP failures never fail course, todo, semester, binding, or unbinding writes.
- Unbinding publishes a higher-revision `bound=false` snapshot that removes the previous student's data.
- Do not expose openid, user ID, student number, MQTT password, or raw public IP in Overview payloads or ordinary logs.
- Keep the legacy WebSocket Overview endpoint only as a temporary debug path; production triggers call the MQTT service.

---

## File Map

**Create:**

- `main/xiaozhi-server/core/xiaoxin/overview/__init__.py` — public Overview package exports.
- `main/xiaozhi-server/core/xiaoxin/overview/models.py` — immutable normalized location, weather, and snapshot records.
- `main/xiaozhi-server/core/xiaoxin/overview/store.py` — SQLite location, daily weather, snapshot, and pending-publication persistence.
- `main/xiaozhi-server/core/xiaoxin/overview/providers.py` — IP location and Open-Meteo provider interfaces/implementations.
- `main/xiaozhi-server/core/xiaoxin/overview/service.py` — projection, revision, trigger, weather-cache, retry, and publish coordination.
- `main/xiaozhi-server/tests/xiaoxin/test_overview_store.py` — persistence and coalescing tests.
- `main/xiaozhi-server/tests/xiaoxin/test_overview_providers.py` — provider parsing, timeout, and normalization tests.
- `main/xiaozhi-server/tests/xiaoxin/test_overview_service.py` — projection, triggers, revision, retry, and isolation tests.

**Modify:**

- `main/xiaozhi-server/core/xiaoxin/tenant_config.py` — preserve existing status/notification topics and add the device-scoped Overview topic helper; the historical class name is not part of the device protocol.
- `main/xiaozhi-server/core/xiaoxin/doorbell_ota.py` — emit `overview_topic`.
- `main/xiaozhi-server/core/xiaoxin/broker_auth.py` — Overview ACL rules.
- `main/xiaozhi-server/core/xiaoxin/doorbell_client.py` — retained Overview publish and connect/PUBACK listener hooks.
- `main/xiaozhi-server/core/xiaoxin/control_runtime.py` — construct/start/stop Overview service and retry loop.
- `main/xiaozhi-server/core/api/xiaoxin_control_handler.py` — weather-location APIs, heartbeat, write triggers, manual sync, diagnostics.
- `main/xiaozhi-server/core/api/ota_handler.py` — record device public IP during OTA.
- `main/xiaozhi-server/config.yaml` — declared Overview configuration defaults without real secrets.
- `main/xiaozhi-server/tests/xiaoxin/test_tenant_config.py`
- `main/xiaozhi-server/tests/xiaoxin/test_doorbell_ota.py`
- `main/xiaozhi-server/tests/xiaoxin/test_broker_auth.py`
- `main/xiaozhi-server/tests/xiaoxin/test_doorbell_client.py`
- `main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py`
- `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`
- `main/xiaozhi-server/tests/xiaoxin/test_ota_activation_handler.py`

## Interfaces Shared Across Tasks

```python
@dataclass(frozen=True)
class IpCityLocation:
    province: str
    city: str
    country_code: str
    located_at: str

@dataclass(frozen=True)
class DailyWeather:
    province: str
    city: str
    date: str
    weather_code: int
    weather_text: str
    temperature_min_c: float
    temperature_max_c: float
    fetched_at: str

@dataclass(frozen=True)
class OverviewSnapshot:
    device_id: str
    owner_user_id: str | None
    revision: int
    content_hash: str
    payload: dict[str, object]
    publish_state: str
    publish_attempts: int
    next_attempt_at: str | None
```

```python
class XiaoxinOverviewStore:
    def get_location(self, device_id: str) -> dict[str, object] | None: ...
    def set_automatic_location(self, device_id: str, public_ip_hmac: str, location: IpCityLocation) -> dict[str, object]: ...
    def set_manual_location(self, device_id: str, province: str, city: str) -> dict[str, object]: ...
    def set_location_mode(self, device_id: str, mode: str) -> dict[str, object] | None: ...
    def get_daily_weather(self, province: str, city: str, date_text: str, provider: str) -> DailyWeather | None: ...
    def put_daily_weather(self, weather: DailyWeather, provider: str) -> DailyWeather: ...
    def record_weather_failure(self, province: str, city: str, date_text: str, provider: str, error: str, attempts: int, next_attempt_at: str | None) -> None: ...
    def list_due_weather_retries(self, now_iso: str, limit: int = 50) -> list[dict[str, object]]: ...
    def upsert_snapshot(self, device_id: str, owner_user_id: str | None, content: dict[str, object], generated_at: str) -> tuple[OverviewSnapshot, bool]: ...
    def mark_publish_attempt(self, device_id: str, revision: int, next_attempt_at: str | None, error: str | None) -> None: ...
    def mark_published(self, device_id: str, revision: int, published_at: str) -> bool: ...
    def list_pending_snapshots(self, now_iso: str, limit: int = 100) -> list[OverviewSnapshot]: ...
```

```python
class OverviewSyncService:
    async def refresh_device(self, device_id: str, reason: str, date_text: str | None = None) -> dict[str, object]: ...
    async def refresh_user_devices(self, user_id: str, reason: str, date_text: str | None = None) -> list[dict[str, object]]: ...
    async def clear_unbound_device(self, device_id: str, reason: str) -> dict[str, object]: ...
    async def observe_device_ip(self, device_id: str, public_ip: str, reason: str) -> dict[str, object]: ...
    async def drain_pending(self) -> int: ...
```

### Task 1: Device-Scoped Overview MQTT Topic Contract

**Files:**
- Modify: `main/xiaozhi-server/core/xiaoxin/tenant_config.py`
- Modify: `main/xiaozhi-server/core/xiaoxin/doorbell_ota.py`
- Modify: `main/xiaozhi-server/core/xiaoxin/broker_auth.py`
- Test: `main/xiaozhi-server/tests/xiaoxin/test_tenant_config.py`
- Test: `main/xiaozhi-server/tests/xiaoxin/test_doorbell_ota.py`
- Test: `main/xiaozhi-server/tests/xiaoxin/test_broker_auth.py`

**Interfaces:**
- Produces: `TenantConfig.overview_topic(device_id: str) -> str`; retain the existing class to avoid an unrelated configuration refactor.
- Produces: OTA `doorbell_mqtt.overview_topic`.
- Produces: service write/device read Overview ACL rules.

- [ ] **Step 1: Write failing device topic tests**

```python
def test_device_topics_remain_unscoped_and_include_overview():
    config = load_tenant_config({"xiaoxin_control": {}})
    assert config.status_topic("aa:bb") == "device/aa:bb/status"
    assert config.notification_topic("aa:bb") == "device/aa:bb/notification"
    assert config.overview_topic("aa:bb") == "device/aa:bb/overview"

def test_ota_emits_device_topics_without_tenant_protocol_field(tmp_path):
    config = load_tenant_config(
        {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "mqtt.example:1883"}}}
    )
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")
    payload = build_doorbell_mqtt_ota(config, store, "aa:bb")
    assert "tenant_id" not in payload
    assert payload["overview_topic"] == "device/aa:bb/overview"
```

- [ ] **Step 2: Run tests to verify the missing Overview helper fails**

Run: `python -m pytest tests/xiaoxin/test_tenant_config.py tests/xiaoxin/test_doorbell_ota.py tests/xiaoxin/test_broker_auth.py -q`

Expected: FAIL because `overview_topic()` and OTA `overview_topic` do not exist yet.

- [ ] **Step 3: Add only the device-scoped Overview topic helper**

```python
def overview_topic(self, device_id: str) -> str:
    safe_device_id = validate_mqtt_topic_segment(device_id, "device_id")
    return f"device/{safe_device_id}/overview"
```

Remove the current OTA `tenant_id` field and add `"overview_topic": tenant.overview_topic(device_id)` to `build_doorbell_mqtt_ota()`. Keep `client_id` and `username` opaque; firmware must not parse deployment structure from them.

Add ACL lines:

```python
"topic write device/+/overview"
f"topic read {tenant.overview_topic(credential.device_id)}"
```

Do not change the existing `device/{device_id}/status` and `device/{device_id}/notification` contract. The server ACL must contain `topic read device/+/status`, `topic write device/+/notification`, and `topic write device/+/overview`; each device receives only its exact three topic grants.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/xiaoxin/test_tenant_config.py tests/xiaoxin/test_doorbell_ota.py tests/xiaoxin/test_broker_auth.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/core/xiaoxin/tenant_config.py main/xiaozhi-server/core/xiaoxin/doorbell_ota.py main/xiaozhi-server/core/xiaoxin/broker_auth.py main/xiaozhi-server/tests/xiaoxin/test_tenant_config.py main/xiaozhi-server/tests/xiaoxin/test_doorbell_ota.py main/xiaozhi-server/tests/xiaoxin/test_broker_auth.py
git commit -m "feat: add device overview mqtt topic"
```

### Task 2: Overview Persistence Store

**Files:**
- Create: `main/xiaozhi-server/core/xiaoxin/overview/__init__.py`
- Create: `main/xiaozhi-server/core/xiaoxin/overview/models.py`
- Create: `main/xiaozhi-server/core/xiaoxin/overview/store.py`
- Create: `main/xiaozhi-server/tests/xiaoxin/test_overview_store.py`

**Interfaces:**
- Produces: `IpCityLocation`, `DailyWeather`, `OverviewSnapshot`, `XiaoxinOverviewStore` signatures from the shared interface section.

- [ ] **Step 1: Write failing schema and coalescing tests**

```python
def test_snapshot_store_increments_revision_only_when_content_changes(tmp_path):
    store = XiaoxinOverviewStore(tmp_path / "overview.db")
    first, first_changed = store.upsert_snapshot("device-1", "user-1", {"bound": True, "course": {"title": "数学"}}, "2026-07-10T08:00:00+08:00")
    same, same_changed = store.upsert_snapshot("device-1", "user-1", {"bound": True, "course": {"title": "数学"}}, "2026-07-10T08:01:00+08:00")
    changed, changed_flag = store.upsert_snapshot("device-1", "user-1", {"bound": True, "course": {"title": "体育"}}, "2026-07-10T08:02:00+08:00")
    assert (first.revision, first_changed) == (1, True)
    assert (same.revision, same_changed) == (1, False)
    assert (changed.revision, changed_flag) == (2, True)
```

Also test manual location precedence, city/date weather cache keys, persisted weather failure attempts/next retry, pending overwrite, stale ACK rejection, and restart persistence.

- [ ] **Step 2: Run the store test and confirm red**

Run: `python -m pytest tests/xiaoxin/test_overview_store.py -q`

Expected: FAIL with missing `core.xiaoxin.overview` modules.

- [ ] **Step 3: Implement normalized JSON hashing and SQLite tables**

Use stable JSON for content hashing:

```python
def overview_content_hash(content: dict[str, object]) -> str:
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

Create tables `device_weather_locations`, `daily_city_weather`, and `device_overview_snapshots` exactly as defined in the approved spec. Add `fetch_attempts`, `next_attempt_at`, and `last_error` to the weather cache row so failed city/date lookups survive restart. Use one transaction for revision comparison and snapshot upsert.

- [ ] **Step 4: Run store tests**

Run: `python -m pytest tests/xiaoxin/test_overview_store.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/core/xiaoxin/overview main/xiaozhi-server/tests/xiaoxin/test_overview_store.py
git commit -m "feat: persist device overview snapshots"
```

### Task 3: IP Location And Daily Weather Providers

**Files:**
- Create: `main/xiaozhi-server/core/xiaoxin/overview/providers.py`
- Create: `main/xiaozhi-server/tests/xiaoxin/test_overview_providers.py`

**Interfaces:**
- Consumes: `IpCityLocation`, `DailyWeather`.
- Produces: `IpLocationProvider`, `PconlineIpLocationProvider`, `WeatherProvider`, `OpenMeteoWeatherProvider`.

- [ ] **Step 1: Write failing provider tests with fake HTTP responses**

```python
async def test_open_meteo_provider_normalizes_daily_forecast():
    provider = OpenMeteoWeatherProvider(http_get=fake_open_meteo_get)
    result = await provider.daily("浙江", "杭州", "2026-07-10")
    assert result.weather_text == "多云"
    assert result.temperature_min_c == 26.0
    assert result.temperature_max_c == 35.0
```

Test IP response parsing for `pro`/`city`, private IP rejection, geocoding ambiguity constrained to `country_code=CN`, request timeout, unknown WMO codes, and malformed provider JSON.

- [ ] **Step 2: Run provider tests and confirm red**

Run: `python -m pytest tests/xiaoxin/test_overview_providers.py -q`

Expected: FAIL with missing provider classes.

- [ ] **Step 3: Implement injectable async providers**

```python
class IpLocationProvider(Protocol):
    async def locate(self, public_ip: str) -> IpCityLocation | None: ...

class WeatherProvider(Protocol):
    async def daily(self, province: str, city: str, date_text: str) -> DailyWeather: ...
```

Use aiohttp-compatible injected `http_get(url, params, timeout)` callables. Open-Meteo requests only daily `weather_code`, `temperature_2m_min`, and `temperature_2m_max`, with `timezone=auto` and the requested date.

- [ ] **Step 4: Run provider tests**

Run: `python -m pytest tests/xiaoxin/test_overview_providers.py -q`

Expected: PASS without external network access.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/core/xiaoxin/overview/providers.py main/xiaozhi-server/tests/xiaoxin/test_overview_providers.py
git commit -m "feat: add overview location and weather providers"
```

### Task 4: MQTT Retained Publish And PUBACK Hooks

**Files:**
- Modify: `main/xiaozhi-server/core/xiaoxin/doorbell_client.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_doorbell_client.py`

**Interfaces:**
- Produces: `publish_overview(device_id: str, payload: dict[str, object]) -> int | None`.
- Produces: `add_connect_listener(listener: Callable[[], None])`.
- Produces: `add_publish_ack_listener(listener: Callable[[int], None])`.

- [ ] **Step 1: Extend the fake Paho client and write failing tests**

```python
def test_publish_overview_is_qos1_retained():
    client, fake = started_client()
    mid = client.publish_overview("aa:bb", {"type": "xiaoxin_overview_update", "revision": 7})
    assert mid == 1
    topic, payload, qos, retain = fake.published[-1]
    assert topic == "device/aa:bb/overview"
    assert json.loads(payload)["revision"] == 7
    assert (qos, retain) == (1, True)
```

Also verify the existing device-scoped status subscription and wake publication remain unchanged, plus connect listeners and Paho VERSION2 `on_publish` forwarding.

- [ ] **Step 2: Run focused tests and confirm red**

Run: `python -m pytest tests/xiaoxin/test_doorbell_client.py -q`

Expected: FAIL because `publish_overview` and listeners do not exist.

- [ ] **Step 3: Implement publish and callback hooks**

```python
def publish_overview(self, device_id: str, payload: dict[str, object]) -> int | None:
    if self._client is None and self.settings.enabled:
        self._start_client()
    if self._client is None:
        return None
    result = self._client.publish(
        self.tenant.overview_topic(device_id),
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        qos=self.tenant.doorbell.qos,
        retain=True,
    )
    return int(result.mid) if getattr(result, "rc", 1) == 0 else None
```

Register `client.on_publish = self._on_publish`; marshal listener calls through the runtime loop when it is running.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/xiaoxin/test_doorbell_client.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/core/xiaoxin/doorbell_client.py main/xiaozhi-server/tests/xiaoxin/test_doorbell_client.py
git commit -m "feat: publish retained overview mqtt snapshots"
```

### Task 5: Overview Projection And Sync Service

**Files:**
- Create: `main/xiaozhi-server/core/xiaoxin/overview/service.py`
- Create: `main/xiaozhi-server/tests/xiaoxin/test_overview_service.py`
- Modify: `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`

**Interfaces:**
- Consumes: identity store course/todo/device methods, Overview store, providers, `publish_overview`.
- Produces: `OverviewSyncService` public methods from the shared interface section.
- Produces: reusable `build_student_overview(user_id: str, date_text: str, *, device_id: str | None = None)` so the HTTP overview response and MQTT snapshot share course/todo semantics.

- [ ] **Step 1: Write failing projection tests**

```python
async def test_refresh_device_builds_bound_weather_course_todo_payload(runtime):
    result = await runtime.overview_service.refresh_device("device-1", "course_created", "2026-07-10")
    payload = result["payload"]
    assert payload["version"] == 1
    assert payload["bound"] is True
    assert payload["weather"]["summary"] == "杭州 · 多云"
    assert payload["course"]["title"] == "体育 15:25"
    assert payload["todo"]["count"] == 1
    assert "openid" not in json.dumps(payload)
```

Add tests for no location, weather failure, same-content no publish, unbound clear payload, different owner isolation, 2 KiB/text limits, and GET projection without MQTT side effects.

- [ ] **Step 2: Run service tests and confirm red**

Run: `python -m pytest tests/xiaoxin/test_overview_service.py -q`

Expected: FAIL with missing service.

- [ ] **Step 3: Extract projection semantics from the handler**

Move curriculum/course/todo card construction behind an injected projection service. Keep handler wrappers so existing miniprogram tests remain valid:

```python
def _student_overview(self, user_id: str, date_text: str, *, device_id: str | None = None) -> dict[str, object]:
    return self.runtime.overview_service.build_student_overview(user_id, date_text, device_id=device_id)
```

Do not include latest notification history in MQTT payload; it may remain in the miniprogram HTTP dashboard response.

- [ ] **Step 4: Implement revision, publish mapping, and pending drain**

Maintain `mid -> (device_id, revision)` in the service. On PUBACK call `store.mark_published`. On publish refusal set the next attempt using `[1, 2, 5, 15, 30]` seconds. New content overwrites the older pending snapshot.

- [ ] **Step 5: Run service and existing overview tests**

Run: `python -m pytest tests/xiaoxin/test_overview_service.py tests/xiaoxin/test_control_handler.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main/xiaozhi-server/core/xiaoxin/overview/service.py main/xiaozhi-server/core/api/xiaoxin_control_handler.py main/xiaozhi-server/tests/xiaoxin/test_overview_service.py main/xiaozhi-server/tests/xiaoxin/test_control_handler.py
git commit -m "feat: build reliable student overview snapshots"
```

### Task 6: Runtime Wiring And Daily Refresh Loop

**Files:**
- Modify: `main/xiaozhi-server/core/xiaoxin/control_runtime.py`
- Modify: `main/xiaozhi-server/config.yaml`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_config_contract.py`

**Interfaces:**
- Produces: `runtime.overview_store`, `runtime.overview_service`.
- Produces: one retry/daily-refresh runtime task.

- [ ] **Step 1: Write failing runtime lifecycle tests**

```python
def test_runtime_exposes_overview_service_and_store(tmp_path, monkeypatch):
    runtime = create_xiaoxin_control_runtime(config_with_paths(tmp_path))
    assert runtime.overview_store.db_path == tmp_path / "overview.db"
    assert runtime.overview_service is not None
```

Test start registers MQTT connect/PUBACK listeners, stop cancels the loop, and a tick drains pending plus refreshes missing city/date weather.

- [ ] **Step 2: Run runtime tests and confirm red**

Run: `python -m pytest tests/xiaoxin/test_control_runtime.py tests/xiaoxin/test_config_contract.py -q`

Expected: FAIL because runtime has no Overview components.

- [ ] **Step 3: Add explicit configuration**

```yaml
xiaoxin_control:
  overview_mqtt:
    enabled: false
    db: data/xiaoxin_overview.db
    ip_hmac_secret: ""
    retry_tick_seconds: 1
    daily_refresh_hour: 0
    daily_refresh_minute: 5
```

An empty HMAC secret disables automatic IP persistence with diagnostic state `overview_ip_hmac_unconfigured`; it must not silently use a hard-coded secret.

- [ ] **Step 4: Construct and manage the service**

Add dataclass fields and one `_overview_task`. The loop calls `drain_pending()` and `list_due_weather_retries()` every configured tick and runs the daily city refresh once per local date after 00:05. Weather backoff values are exactly 600, 1800, and 7200 seconds; after the third failed retry, persist `next_attempt_at=NULL` until manual refresh or a new local date.

- [ ] **Step 5: Run runtime tests**

Run: `python -m pytest tests/xiaoxin/test_control_runtime.py tests/xiaoxin/test_config_contract.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main/xiaozhi-server/core/xiaoxin/control_runtime.py main/xiaozhi-server/config.yaml main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py main/xiaozhi-server/tests/xiaoxin/test_config_contract.py
git commit -m "feat: run overview retry and daily refresh service"
```

### Task 7: CRUD, Binding, And Unbinding Triggers

**Files:**
- Modify: `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`

**Interfaces:**
- Consumes: `refresh_user_devices`, `clear_unbound_device`.

- [ ] **Step 1: Write one failing test per write family**

```python
async def test_course_create_refreshes_bound_device_after_commit(runtime, handler, token):
    response = await handler.handle_miniprogram_course_create(course_request(token))
    assert response.status == 200
    assert runtime.overview_service.calls == [(runtime.user.id, "course_created")]
```

Cover semester update, course create/update/delete, todo create/update/delete, activation bind, miniprogram unbind, console bind, and console unbind. Verify GET endpoints make zero calls.

- [ ] **Step 2: Run focused handler tests and confirm red**

Run: `python -m pytest tests/xiaoxin/test_control_handler.py -k "overview_refresh or overview_clear" -q`

Expected: FAIL because writes do not trigger Overview.

- [ ] **Step 3: Add a non-failing trigger helper**

```python
async def _refresh_user_overview(self, user_id: str, reason: str) -> None:
    service = getattr(self.runtime, "overview_service", None)
    if service is None:
        return
    try:
        await service.refresh_user_devices(user_id, reason)
    except Exception:
        self.logger.bind(tag="xiaoxin.overview").exception(f"overview refresh failed reason={reason}")
```

Call it only after the database write succeeds. For unbind, retain `device_id` and call `clear_unbound_device()` after the identity store returns success.

- [ ] **Step 4: Run handler tests**

Run: `python -m pytest tests/xiaoxin/test_control_handler.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/core/api/xiaoxin_control_handler.py main/xiaozhi-server/tests/xiaoxin/test_control_handler.py
git commit -m "feat: refresh overview after student data changes"
```

### Task 8: Device Public-IP Observation And Weather Location APIs

**Files:**
- Modify: `main/xiaozhi-server/core/api/ota_handler.py`
- Modify: `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`
- Modify: `main/xiaozhi-server/core/xiaoxin/doorbell_credentials.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_ota_activation_handler.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_doorbell_credentials.py`

**Interfaces:**
- Produces: `GET/PATCH /api/miniprogram/weather-location`.
- Produces: `POST /api/xiaoxin/device/location-heartbeat`.
- Produces: `DoorbellCredentialStore.verify_password(username, device_id, password) -> bool`; the heartbeat authenticates with the opaque device credential returned by OTA and does not carry a tenant field.

- [ ] **Step 1: Write failing API tests**

Test that OTA with public remote `203.0.113.10` calls `observe_device_ip`, private remotes do not call the provider, manual mode cannot be overwritten by OTA, students cannot edit another device, and heartbeat rejects a wrong device credential.

```python
def test_verify_password_uses_constant_time_comparison(tmp_path):
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")
    credential = store.get_or_create("default", "device-1")
    assert store.verify_password(credential.username, "device-1", credential.password) is True
    assert store.verify_password(credential.username, "device-1", "wrong") is False
```

The first `get_or_create()` argument is only the existing credential-store namespace and is never sent to firmware or placed in a topic/payload. Add a second assertion that the same password with another `device_id` is rejected. Implement lookup by `username + device_id + active status` and compare the stored password with `secrets.compare_digest()`.

- [ ] **Step 2: Run focused tests and confirm red**

Run: `python -m pytest tests/xiaoxin/test_doorbell_credentials.py tests/xiaoxin/test_ota_activation_handler.py tests/xiaoxin/test_control_handler.py -k "weather_location or location_heartbeat or observe_device_ip or verify_password" -q`

Expected: FAIL with missing routes and methods.

- [ ] **Step 3: Implement trusted public-IP extraction**

Create a private handler helper that accepts forwarded headers only when the direct peer is in configured trusted proxy CIDRs; otherwise use `request.remote`. Reject private, loopback, link-local, multicast, and invalid addresses with `ipaddress.ip_address()`.

- [ ] **Step 4: Implement authenticated heartbeat and student APIs**

Heartbeat headers:

```text
Device-Id: {device_id}
Device-Username: {device MQTT username}
Authorization: Bearer {device MQTT password}
```

The endpoint reads no caller-supplied IP. It passes the observed remote IP to `observe_device_ip`.

Student PATCH shapes:

```json
{"mode":"automatic"}
```

```json
{"mode":"manual","province":"浙江","city":"杭州"}
```

- [ ] **Step 5: Run API tests**

Run: `python -m pytest tests/xiaoxin/test_doorbell_credentials.py tests/xiaoxin/test_ota_activation_handler.py tests/xiaoxin/test_control_handler.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main/xiaozhi-server/core/api/ota_handler.py main/xiaozhi-server/core/api/xiaoxin_control_handler.py main/xiaozhi-server/core/xiaoxin/doorbell_credentials.py main/xiaozhi-server/tests/xiaoxin/test_ota_activation_handler.py main/xiaozhi-server/tests/xiaoxin/test_control_handler.py main/xiaozhi-server/tests/xiaoxin/test_doorbell_credentials.py
git commit -m "feat: locate device weather city from network"
```

### Task 9: Diagnostics And Manual Resync

**Files:**
- Modify: `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`
- Modify: `main/xiaozhi-server/core/api/static/xiaoxin_control.html`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_console_static.py`

**Interfaces:**
- Produces: `POST /api/xiaoxin/devices/{device_id}/overview-mqtt-sync`.
- Produces: per-device `overview` diagnostics in `/api/xiaoxin/devices`.

- [ ] **Step 1: Write failing API and static UI tests**

Assert device payload contains revision/state/published_at/error/weather city and the console has one `data-sync-overview-mqtt` button, while the old row-level WebSocket Overview button remains absent.

- [ ] **Step 2: Run tests and confirm red**

Run: `python -m pytest tests/xiaoxin/test_control_handler.py tests/xiaoxin/test_control_console_static.py -k overview -q`

Expected: FAIL.

- [ ] **Step 3: Implement diagnostics and manual refresh**

The manual endpoint calls `refresh_device(device_id, "manual_resync")`; it does not require a live WebSocket. Return the queued snapshot revision and publish state.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/xiaoxin/test_control_handler.py tests/xiaoxin/test_control_console_static.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/xiaozhi-server/core/api/xiaoxin_control_handler.py main/xiaozhi-server/core/api/static/xiaoxin_control.html main/xiaozhi-server/tests/xiaoxin/test_control_handler.py main/xiaozhi-server/tests/xiaoxin/test_control_console_static.py
git commit -m "feat: expose mqtt overview diagnostics"
```

### Task 10: Server Verification And Documentation

**Files:**
- Modify: `docs/operations/xiaoxin-real-device-acceptance-ledger.md`
- Modify: `docs/requirements/requirements.yaml`

- [ ] **Step 1: Run complete Xiaoxin server tests**

Run: `python -m pytest tests/xiaoxin -q`

Expected: all tests pass with zero failures.

- [ ] **Step 2: Run targeted formatting and diff checks**

Run: `git diff --check`

Expected: no output and exit code 0.

- [ ] **Step 3: Update requirements and acceptance ledger**

Record implementation status without marking true-device acceptance as passed. Add explicit pending evidence for MQTT retained receipt, city weather, course/todo refresh, offline reconnect, and unbind clearing.

- [ ] **Step 4: Re-run focused contract tests after docs edits**

Run: `python -m pytest tests/xiaoxin/test_config_contract.py tests/xiaoxin/test_control_handler.py tests/xiaoxin/test_overview_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/operations/xiaoxin-real-device-acceptance-ledger.md docs/requirements/requirements.yaml
git commit -m "docs: prepare mqtt overview acceptance"
```
