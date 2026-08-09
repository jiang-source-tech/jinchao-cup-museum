# Mosquitto Doorbell Broker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a server-side Mosquitto MQTT broker for Xiaoxin doorbell wake so sleeping-but-networked ESP32 devices can be woken for scheduled reminders, while reminder content and TTS continue over WebSocket.

**Architecture:** Mosquitto runs beside `xiaozhi-server` in Docker and exposes port `1883`. `xiaozhi-server` uses the existing `XiaoxinDoorbellClient` to subscribe to `device/+/status` and publish `{"type":"wake"}` to `device/{device_id}/notification`; all real reminder payloads, TTS audio, ACKs, and playback state stay on WebSocket.

**Tech Stack:** Docker Compose, Eclipse Mosquitto 2.x, existing Python `paho-mqtt` doorbell client, existing `xiaoxin_control.doorbell_mqtt` configuration.

## Global Constraints

- MQTT is only the doorbell control plane; do not send reminder text, course content, todo body, TTS audio, or conversation payloads through MQTT.
- WebSocket remains the primary data plane for reminders, TTS, ACKs, and device interaction.
- The broker must be reachable by ESP32 devices at `SERVER_IP:1883`.
- For first deployment, use Mosquitto without persistence-heavy features, clustering, or rule engines.
- If the device enters deep sleep and drops Wi-Fi/MQTT, this broker cannot wake it; this plan assumes Wi-Fi and MQTT stay connected during idle sleep.

---

## File Structure

- `main/xiaozhi-server/mosquitto/config/mosquitto.conf`: broker listener configuration for Docker.
- `main/xiaozhi-server/docker-compose.yml`: add broker to the single-server Docker deployment.
- `main/xiaozhi-server/docker-compose_all.yml`: add broker to the full-module Docker deployment.
- `docs/getting-started/deployment.md`: document port `1883`, broker startup, Xiaoxin config, and verification.
- `docs/development/xiaoxin-control-console.md`: document the doorbell broker runtime expectation for scheduled reminders.

### Task 1: Add Mosquitto Broker Config

**Files:**
- Create: `main/xiaozhi-server/mosquitto/config/mosquitto.conf`

**Interfaces:**
- Produces: a Docker-mounted Mosquitto config listening on `0.0.0.0:1883`.
- Consumes: no application code.

- [ ] **Step 1: Create the broker config**

Create `main/xiaozhi-server/mosquitto/config/mosquitto.conf`:

```conf
listener 1883 0.0.0.0
allow_anonymous true
persistence false
log_dest stdout
connection_messages true
```

- [ ] **Step 2: Validate the config path exists**

Run:

```bash
test -f main/xiaozhi-server/mosquitto/config/mosquitto.conf
```

Expected: command exits with status `0`.

- [ ] **Step 3: Commit**

```bash
git add main/xiaozhi-server/mosquitto/config/mosquitto.conf
git commit -m "chore: add mosquitto doorbell broker config"
```

### Task 2: Add Mosquitto To Docker Compose

**Files:**
- Modify: `main/xiaozhi-server/docker-compose.yml`
- Modify: `main/xiaozhi-server/docker-compose_all.yml`

**Interfaces:**
- Consumes: `main/xiaozhi-server/mosquitto/config/mosquitto.conf`.
- Produces: Docker service `xiaoxin-doorbell-mqtt` reachable on host port `1883`.

- [ ] **Step 1: Add service to `docker-compose.yml`**

Add this service beside `xiaozhi-esp32-server`:

```yaml
  xiaoxin-doorbell-mqtt:
    image: eclipse-mosquitto:2
    container_name: xiaoxin-doorbell-mqtt
    restart: always
    ports:
      - "1883:1883"
    volumes:
      - ./mosquitto/config/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
```

- [ ] **Step 2: Add service to `docker-compose_all.yml`**

Add this service on the default network:

```yaml
  xiaoxin-doorbell-mqtt:
    image: eclipse-mosquitto:2
    container_name: xiaoxin-doorbell-mqtt
    restart: always
    networks:
      - default
    ports:
      - "1883:1883"
    volumes:
      - ./mosquitto/config/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
```

- [ ] **Step 3: Validate compose syntax**

Run:

```bash
cd main/xiaozhi-server
docker compose -f docker-compose.yml config
docker compose -f docker-compose_all.yml config
```

Expected: both commands print normalized compose YAML without errors.

- [ ] **Step 4: Commit**

```bash
git add main/xiaozhi-server/docker-compose.yml main/xiaozhi-server/docker-compose_all.yml
git commit -m "chore: add mosquitto broker to docker compose"
```

### Task 3: Document Runtime Configuration

**Files:**
- Modify: `docs/getting-started/deployment.md`
- Modify: `docs/development/xiaoxin-control-console.md`

**Interfaces:**
- Consumes: existing `xiaoxin_control.doorbell_mqtt` config.
- Produces: operator instructions for opening port `1883`, starting Mosquitto, and pointing `xiaozhi-server` at the broker.

- [ ] **Step 1: Update deployment ports**

Add `1883` to the deployment port list:

```markdown
- `1883`: MQTT doorbell broker for wake notifications
```

- [ ] **Step 2: Add broker configuration instructions**

Add this runtime config example:

```yaml
xiaoxin_control:
  doorbell_mqtt:
    endpoint: "SERVER_IP:1883"
    username: null
    password: null
    keepalive_seconds: 240
```

For Docker-internal-only testing, document that `endpoint` may be `xiaoxin-doorbell-mqtt:1883`, but ESP32 firmware must use the externally reachable `SERVER_IP:1883`.

- [ ] **Step 3: Add verification commands**

Document:

```bash
docker compose -f docker-compose_all.yml up -d xiaoxin-doorbell-mqtt
docker logs -n 80 xiaoxin-doorbell-mqtt
```

Expected log includes Mosquitto startup and listener on port `1883`.

- [ ] **Step 4: State the payload boundary**

Add:

```markdown
MQTT doorbell payloads must stay small. Use `{"type":"wake"}` only. Reminder content, TTS, ACK, and playback completion stay on WebSocket.
```

- [ ] **Step 5: Commit**

```bash
git add docs/getting-started/deployment.md docs/development/xiaoxin-control-console.md
git commit -m "docs: document mosquitto doorbell deployment"
```

### Task 4: Manual End-To-End Verification

**Files:**
- No code files.

**Interfaces:**
- Consumes: running broker, running `xiaozhi-server`, ESP32 firmware that keeps MQTT online during idle sleep.
- Produces: verified doorbell wake path.

- [ ] **Step 1: Start services**

Run:

```bash
cd main/xiaozhi-server
docker compose -f docker-compose_all.yml up -d
docker ps
```

Expected: `xiaoxin-doorbell-mqtt`, `xiaozhi-esp32-server`, `xiaozhi-esp32-server-web`, MySQL, and Redis are running.

- [ ] **Step 2: Configure server**

Edit `main/xiaozhi-server/data/.config.yaml` or the deployed `/opt/xiaozhi-server/data/.config.yaml`:

```yaml
xiaoxin_control:
  doorbell_mqtt:
    endpoint: "SERVER_IP:1883"
    username: null
    password: null
    keepalive_seconds: 240
```

Restart:

```bash
docker restart xiaozhi-esp32-server
```

Expected: server no longer logs `Xiaoxin doorbell MQTT is disabled`.

- [ ] **Step 3: Verify broker reachability**

From a machine with Mosquitto clients installed:

```bash
mosquitto_sub -h SERVER_IP -p 1883 -t 'device/+/notification' -v
```

In another terminal:

```bash
mosquitto_pub -h SERVER_IP -p 1883 -t 'device/test-device/notification' -m '{"type":"wake"}'
```

Expected: the subscriber prints `device/test-device/notification {"type":"wake"}`.

- [ ] **Step 4: Verify real device wake**

Put the ESP32 into idle sleep with Wi-Fi and MQTT still connected. Trigger a reminder from the Xiaoxin control console or a scheduled reminder worker.

Expected:

- The device receives MQTT `{"type":"wake"}`.
- The device reconnects to `ws://SERVER_IP:8000/xiaoxin/v1/`.
- The reminder content and TTS are delivered over WebSocket.
- Delivery reaches ACK or TTS-done state.

## Self-Review

- Spec coverage: the plan covers broker config, Docker deployment, documentation, runtime config, and manual verification.
- Placeholder scan: no `TBD`, `TODO`, or unspecified edge handling remains.
- Type consistency: service name `xiaoxin-doorbell-mqtt`, port `1883`, topic `device/{device_id}/notification`, and payload `{"type":"wake"}` are consistent across tasks.
