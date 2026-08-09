# Xiaoxin MQTT Overview Firmware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the ESP32 firmware's lightweight MQTT connection to consume device-scoped retained Overview snapshots, update weather/course/todo cards without opening the voice WebSocket, and report network-location heartbeats after Wi-Fi connectivity changes.

**Architecture:** Persist the server-delivered `doorbell_mqtt` object as a versioned configuration, remove the hard-coded Broker fallback, subscribe to separate notification and Overview topics on one esp-mqtt client, and route payloads by exact topic. Reuse the existing `HandleXiaoxinOverviewUpdate()` display path behind a new public MQTT entry that validates device/version/revision before applying data.

**Tech Stack:** ESP-IDF C++, esp-mqtt, cJSON, NVS `Settings`, existing Xiaoxin Overview model, Python source-contract tests, host C tests.

## Global Constraints

- Execute in `D:\AI_Pet\hzcu_xiaoxin_firmwire_private`; create an isolated worktree at execution time.
- WebSocket remains voice/audio/ACK only.
- MQTT notification commands and retained Overview snapshots use separate topics.
- Status uses QoS 1 retained; wake uses QoS 1 non-retained; Overview uses QoS 1 retained.
- Firmware consumes the exact device-scoped topic strings supplied by OTA and does not derive topic prefixes locally.
- Commercial firmware does not use the built-in `121.43.33.0` Broker fallback.
- Overview payload version is `1`; invalid or stale messages keep the last valid UI state.
- Overview reception never starts listening, opens WebSocket, plays TTS, or shows a heads-up notification.
- `bound=false` clears weather/course/todo data from RAM.
- Payload size limit is 2 KiB before JSON parsing.
- Device reboot may reset the in-memory revision; retained latest state is accepted again after reconnect.

---

## File Map

**Create:**

- `main/doorbell_config.h` — versioned persistent MQTT config value object.
- `main/doorbell_config.cc` — OTA parse, validation, NVS save/load/disable.
- `main/device_location_heartbeat.h` — lightweight heartbeat interface.
- `main/device_location_heartbeat.cc` — HTTP request using device MQTT credential.
- `tests/xiaoxin_doorbell_config_path_test.py` — OTA/persistence source-contract tests.
- `tests/xiaoxin_mqtt_overview_path_test.py` — topic routing, validation, and no-WebSocket tests.
- `tests/xiaoxin_location_heartbeat_path_test.py` — heartbeat URL/auth/network-trigger tests.

**Modify:**

- `main/ota.h`
- `main/ota.cc`
- `main/doorbell_mqtt.h`
- `main/doorbell_mqtt.cc`
- `main/application.h`
- `main/application.cc`
- `main/CMakeLists.txt`
- `tests/xiaoxin_doorbell_notification_path_test.py`
- `tests/xiaoxin_protocol_compatibility_test.py`

## Shared Interfaces

```cpp
struct DoorbellMqttConfig {
    int version = 0;
    bool enabled = false;
    std::string endpoint;
    std::string client_id;
    std::string username;
    std::string password;
    std::string status_topic;
    std::string notification_topic;
    std::string overview_topic;
    int keepalive_seconds = 240;
    int qos = 1;

    bool IsUsable() const;
};

bool ParseDoorbellMqttConfig(const cJSON* root, DoorbellMqttConfig* output);
void SaveDoorbellMqttConfig(const DoorbellMqttConfig& config);
DoorbellMqttConfig LoadDoorbellMqttConfig();
void DisableDoorbellMqttConfig();
```

```cpp
class DoorbellMqtt {
public:
    void Start(const DoorbellMqttConfig& config, const std::string& device_id);
    bool IsConnected() const;

private:
    void OnMessage(const std::string& topic, const std::string& payload);
};
```

```cpp
class DeviceLocationHeartbeat {
public:
    void Configure(const std::string& ota_url, const DoorbellMqttConfig& mqtt_config, const std::string& device_id);
    void SendAsync();
};
```

### Task 1: Parse And Persist Device MQTT Configuration

**Files:**
- Create: `main/doorbell_config.h`
- Create: `main/doorbell_config.cc`
- Modify: `main/ota.h`
- Modify: `main/ota.cc`
- Modify: `main/CMakeLists.txt`
- Create: `tests/xiaoxin_doorbell_config_path_test.py`

**Interfaces:**
- Produces: `DoorbellMqttConfig`, parse/save/load/disable functions.
- Produces: `Ota::HasDoorbellMqttConfig()` and `Ota::GetDoorbellMqttConfig()`.

- [ ] **Step 1: Write failing source-contract tests**

```python
def test_ota_parses_overview_topic_and_persists_doorbell_config():
    ota = read_source(Path("main/ota.cc"))
    config = read_source(Path("main/doorbell_config.cc"))
    assert 'cJSON_GetObjectItem(root, "doorbell_mqtt")' in ota
    assert 'JsonString(root, "overview_topic")' in config
    assert 'settings.SetString("overview_topic"' in config
```

Also assert unsupported versions are rejected, `enabled:false` erases runtime enablement, password is never logged, and missing `overview_topic` remains valid wake-only configuration.

- [ ] **Step 2: Run the test and confirm red**

Run: `python -m pytest tests/xiaoxin_doorbell_config_path_test.py -q`

Expected: FAIL because firmware ignores `doorbell_mqtt`.

- [ ] **Step 3: Implement the config value object**

```cpp
bool DoorbellMqttConfig::IsUsable() const {
    return version == 1 && enabled && !endpoint.empty() && !client_id.empty() &&
           !username.empty() && !password.empty() && !status_topic.empty() &&
           !notification_topic.empty();
}
```

Treat `overview_topic` as optional for backward-compatible version 1 wake-only firmware. Persist fields under `Settings("doorbell_mqtt", true)`.

- [ ] **Step 4: Parse the top-level OTA object**

In `Ota::CheckVersion()`, parse `doorbell_mqtt` after activation and before protocol selection. If OTA explicitly returns `enabled:false`, call `DisableDoorbellMqttConfig()`; if the object is absent, load the last valid config.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/xiaoxin_doorbell_config_path_test.py tests/ota_url_config_test.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main/doorbell_config.h main/doorbell_config.cc main/ota.h main/ota.cc main/CMakeLists.txt tests/xiaoxin_doorbell_config_path_test.py
git commit -m "feat: persist doorbell mqtt config"
```

### Task 2: Remove Hard-Coded MQTT Broker And Use Configured Topics

**Files:**
- Modify: `main/doorbell_mqtt.h`
- Modify: `main/doorbell_mqtt.cc`
- Modify: `main/application.cc`
- Modify: `tests/xiaoxin_doorbell_notification_path_test.py`

**Interfaces:**
- Consumes: `DoorbellMqttConfig`.
- Produces: configured status/notification/overview subscriptions.

- [ ] **Step 1: Replace obsolete tests with failing production-contract tests**

```python
def test_doorbell_mqtt_uses_ota_topics_and_no_public_default_broker():
    source = read_source(DOORBELL_MQTT)
    assert "kDefaultBrokerHost" not in source
    assert "config_.status_topic" in source
    assert "config_.notification_topic" in source
    assert "config_.overview_topic" in source
```

Delete the current assertions that require simplified unscoped topics and the hard-coded `121.43.33.0` fallback.

- [ ] **Step 2: Run test and confirm red**

Run: `python -m pytest tests/xiaoxin_doorbell_notification_path_test.py -q`

Expected: FAIL on the current fallback and derived topics.

- [ ] **Step 3: Change `DoorbellMqtt::Start` to accept config**

```cpp
void DoorbellMqtt::Start(const DoorbellMqttConfig& config, const std::string& device_id) {
    if (!config.IsUsable() || device_id.empty()) {
        ESP_LOGW(TAG, "doorbell mqtt config unusable");
        return;
    }
    config_ = config;
    device_id_ = device_id;
    // Build esp_mqtt_client_config_t exclusively from config_.
}
```

On connect, publish online to `status_topic`, subscribe notification, and subscribe Overview only when `overview_topic` is non-empty.

- [ ] **Step 4: Start only from OTA/persisted config**

Replace:

```cpp
g_doorbell_mqtt.Start(SystemInfo::GetMacAddress(), "");
```

with:

```cpp
const auto& config = ota_->GetDoorbellMqttConfig();
if (ota_->HasDoorbellMqttConfig() && config.IsUsable()) {
    g_doorbell_mqtt.Start(config, SystemInfo::GetMacAddress());
}
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/xiaoxin_doorbell_notification_path_test.py tests/xiaoxin_doorbell_config_path_test.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main/doorbell_mqtt.h main/doorbell_mqtt.cc main/application.cc tests/xiaoxin_doorbell_notification_path_test.py
git commit -m "feat: use ota mqtt topics on device"
```

### Task 3: Route MQTT Messages By Exact Topic

**Files:**
- Modify: `main/doorbell_mqtt.h`
- Modify: `main/doorbell_mqtt.cc`
- Create: `tests/xiaoxin_mqtt_overview_path_test.py`

**Interfaces:**
- Produces: `OnMessage(topic, payload)`.
- Consumes: exact configured topic strings.

- [ ] **Step 1: Write failing routing tests**

```python
def test_mqtt_data_preserves_topic_and_routes_wake_separately_from_overview():
    source = read_source(Path("main/doorbell_mqtt.cc"))
    body = function_body(source, "void DoorbellMqtt::MqttEventHandler")
    assert "event->topic" in body
    assert "self->OnMessage(topic, payload);" in body
    on_message = function_body(source, "void DoorbellMqtt::OnMessage")
    assert "topic == config_.notification_topic" in on_message
    assert "topic == config_.overview_topic" in on_message
```

- [ ] **Step 2: Run test and confirm red**

Run: `python -m pytest tests/xiaoxin_mqtt_overview_path_test.py -q`

Expected: FAIL because current code discards the MQTT topic.

- [ ] **Step 3: Preserve exact topic bytes**

```cpp
std::string topic(event->topic, event->topic_len);
std::string payload(event->data, event->data_len);
self->OnMessage(topic, payload);
```

Route wake only from notification topic. Route Overview only from Overview topic. Ignore other topics without parsing their payload.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/xiaoxin_mqtt_overview_path_test.py tests/xiaoxin_doorbell_notification_path_test.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main/doorbell_mqtt.h main/doorbell_mqtt.cc tests/xiaoxin_mqtt_overview_path_test.py tests/xiaoxin_doorbell_notification_path_test.py
git commit -m "feat: route doorbell mqtt topics explicitly"
```

### Task 4: Validate And Apply Retained Overview Payloads

**Files:**
- Modify: `main/application.h`
- Modify: `main/application.cc`
- Modify: `main/doorbell_mqtt.cc`
- Modify: `tests/xiaoxin_mqtt_overview_path_test.py`
- Modify: `tests/xiaoxin_protocol_compatibility_test.py`

**Interfaces:**
- Produces: `Application::HandleXiaoxinOverviewMqttMessage(payload, expected_device)`.

- [ ] **Step 1: Write failing payload contract tests**

```python
def test_mqtt_overview_validates_metadata_and_never_opens_voice_channel():
    source = read_source(Path("main/application.cc"))
    body = function_body(source, "void Application::HandleXiaoxinOverviewMqttMessage")
    assert 'JsonIntOrDefault(root, "version", 0)' in body
    assert 'JsonStringOrEmpty(root, "device_id")' in body
    assert 'JsonIntOrDefault(root, "revision", 0)' in body
    assert "HandleXiaoxinOverviewUpdate(root);" in body
    assert "OpenAudioChannel" not in body
    assert "WakeForNotification" not in body
    assert "Play" not in body
```

Also require payload length check, `bound=false` clear behavior, stale revision rejection, and invalid JSON preservation.

- [ ] **Step 2: Run tests and confirm red**

Run: `python -m pytest tests/xiaoxin_mqtt_overview_path_test.py tests/xiaoxin_protocol_compatibility_test.py -q`

Expected: FAIL because the MQTT entry does not exist.

- [ ] **Step 3: Add the public application entry**

```cpp
void Application::HandleXiaoxinOverviewMqttMessage(
    const std::string& payload,
    const std::string& expected_device) {
    if (payload.empty() || payload.size() > 2048) return;
    cJSON* root = cJSON_ParseWithLength(payload.data(), payload.size());
    if (root == nullptr) return;
    // Validate type/version/device/revision.
    // Ignore revision <= last_overview_revision_.
    // Call HandleXiaoxinOverviewUpdate(root), then store revision.
    cJSON_Delete(root);
}
```

For `bound=false`, call `UpdateOverviewData(false, false, "设备未绑定", ... )` using the payload's explicit empty cards; do not preserve old student values.

- [ ] **Step 4: Call the entry from Overview topic routing**

```cpp
Application::GetInstance().HandleXiaoxinOverviewMqttMessage(
    payload, device_id_);
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/xiaoxin_mqtt_overview_path_test.py tests/xiaoxin_protocol_compatibility_test.py -q`

Expected: PASS.

- [ ] **Step 6: Run host Overview model tests**

Run:

```powershell
New-Item -ItemType Directory -Force build | Out-Null
gcc -std=c11 -Wall -Wextra -I main/boards/waveshare/esp32-s3-touch-lcd-1.46 tests/xiaoxin_overview_model_test.c main/boards/waveshare/esp32-s3-touch-lcd-1.46/xiaoxin_overview_model.c -o build/xiaoxin_overview_model_test.exe
if ($LASTEXITCODE -eq 0) { .\build\xiaoxin_overview_model_test.exe }
```

Expected: exit code 0 and `xiaoxin_overview_model tests passed`.

- [ ] **Step 7: Commit**

```bash
git add main/application.h main/application.cc main/doorbell_mqtt.cc tests/xiaoxin_mqtt_overview_path_test.py tests/xiaoxin_protocol_compatibility_test.py
git commit -m "feat: apply retained mqtt overview updates"
```

### Task 5: Device Network Location Heartbeat

**Files:**
- Create: `main/device_location_heartbeat.h`
- Create: `main/device_location_heartbeat.cc`
- Modify: `main/application.h`
- Modify: `main/application.cc`
- Modify: `main/CMakeLists.txt`
- Create: `tests/xiaoxin_location_heartbeat_path_test.py`

**Interfaces:**
- Consumes: OTA URL, device ID, persisted MQTT username/password.
- Produces: authenticated `POST /api/xiaoxin/device/location-heartbeat` after network readiness/reconnect.

- [ ] **Step 1: Write failing heartbeat source tests**

```python
def test_location_heartbeat_uses_device_identity_and_no_ip_payload():
    source = read_source(Path("main/device_location_heartbeat.cc"))
    assert 'SetHeader("Device-Id"' in source
    assert 'SetHeader("Device-Username", config_.username)' in source
    assert 'SetHeader("Authorization", "Bearer " + config_.password)' in source
    assert 'Open("POST", heartbeat_url_)' in source
    assert "public_ip" not in source
```

Also assert `HandleNetworkConnectedEvent()` schedules the heartbeat, the URL derives from the OTA origin, failures do not reboot or open WebSocket, and secrets are not logged.

- [ ] **Step 2: Run test and confirm red**

Run: `python -m pytest tests/xiaoxin_location_heartbeat_path_test.py -q`

Expected: FAIL because heartbeat files do not exist.

- [ ] **Step 3: Implement a non-blocking heartbeat worker**

Derive the endpoint from the OTA URL origin:

```text
http://host:8003/xiaoxin/ota/ -> http://host:8003/api/xiaoxin/device/location-heartbeat
```

Send `{}` as JSON; the server uses the request peer IP. Run the request on a background task and log only success/error class.

- [ ] **Step 4: Configure before the OTA object is released**

In activation completion, capture `ota_->GetCheckVersionUrl()` and `ota_->GetDoorbellMqttConfig()` before `ota_.reset()`. Send once after boot and again from `HandleNetworkConnectedEvent()` after a genuine reconnect.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/xiaoxin_location_heartbeat_path_test.py tests/xiaoxin_doorbell_config_path_test.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main/device_location_heartbeat.h main/device_location_heartbeat.cc main/application.h main/application.cc main/CMakeLists.txt tests/xiaoxin_location_heartbeat_path_test.py
git commit -m "feat: report device network location heartbeat"
```

### Task 6: Firmware Verification And Real-Device Build

**Files:**
- Modify: `docs/xiaoxin-server-driven-data-and-demo-control.zh-CN.md`

- [ ] **Step 1: Run all Xiaoxin Python contract tests**

Run: `python -m pytest tests/xiaoxin_*test.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Run host C tests for Overview/card models**

Run:

```powershell
New-Item -ItemType Directory -Force build | Out-Null
gcc -std=c11 -Wall -Wextra -I main/boards/waveshare/esp32-s3-touch-lcd-1.46 tests/xiaoxin_overview_model_test.c main/boards/waveshare/esp32-s3-touch-lcd-1.46/xiaoxin_overview_model.c -o build/xiaoxin_overview_model_test.exe
if ($LASTEXITCODE -eq 0) { .\build\xiaoxin_overview_model_test.exe }
gcc -std=c11 -Wall -Wextra -I main/boards/waveshare/esp32-s3-touch-lcd-1.46 tests/xiaoxin_card_pager_test.c main/boards/waveshare/esp32-s3-touch-lcd-1.46/xiaoxin_card_pager.c -o build/xiaoxin_card_pager_test.exe
if ($LASTEXITCODE -eq 0) { .\build\xiaoxin_card_pager_test.exe }
```

Expected: both executables exit 0.

- [ ] **Step 3: Build the target board firmware**

The checked-in `sdkconfig` already contains `CONFIG_BOARD_TYPE_WAVESHARE_ESP32_S3_TOUCH_LCD_1_46=y`. From the firmware worktree root, run:

```powershell
idf.py build
```

Expected: firmware links successfully with `doorbell_config.cc` and `device_location_heartbeat.cc` included.

- [ ] **Step 4: Update protocol documentation**

Document device-scoped `device/{device_id}/status`, `device/{device_id}/notification`, and `device/{device_id}/overview` topics, retained Overview semantics, payload version/revision checks, and heartbeat behavior. Remove statements saying MQTT is wake-only.

- [ ] **Step 5: Commit**

```bash
git add docs/xiaoxin-server-driven-data-and-demo-control.zh-CN.md
git commit -m "docs: document mqtt overview device path"
```
