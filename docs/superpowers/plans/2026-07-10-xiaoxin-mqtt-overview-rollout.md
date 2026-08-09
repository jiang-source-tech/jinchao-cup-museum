# Xiaoxin MQTT Overview Cross-Repository Rollout Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute and validate the server, firmware, Broker, and mini-program plans in a safe order that ends with real weather/course/todo data appearing on hardware Overview through MQTT only.

**Architecture:** Implement the server publisher and device-scoped topic contract first, then upgrade firmware to consume the new retained topic, then add the mini-program manual weather correction UI. Keep publishing disabled until Broker ACL and at least one test device firmware are ready; enable one device first and use the acceptance ledger as the release gate.

**Tech Stack:** Git worktrees, Python/pytest, Mosquitto, ESP-IDF, WeChat Mini Program/Node verification, MQTT QoS 1 retained messages.

## Global Constraints

- Source specification: `docs/superpowers/specs/2026-07-10-xiaoxin-mqtt-overview-sync-design.zh-CN.md`.
- Server plan: `docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-server.md`.
- Firmware plan: `docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-firmware.md`.
- Mini-program plan: `docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-miniprogram.md`.
- Create isolated worktrees before implementation.
- Preserve the current mini-program uncommitted changes; do not reset, checkout, clean, or overwrite them.
- Do not enable production Overview publishing until device-scoped ACL and firmware subscription are verified.
- No phase may claim true-device success from unit tests alone.

---

### Task 1: Prepare Isolated Workspaces And Baselines

**Files:**
- No product file changes.

- [ ] **Step 1: Create a server worktree**

Use `superpowers:using-git-worktrees` from `D:\AI_Pet\xiaoxin-esp32-server`, starting from commit `1c6799f` or the latest commit containing the approved design and plans.

- [ ] **Step 2: Create a firmware worktree**

Use `superpowers:using-git-worktrees` from `D:\AI_Pet\hzcu_xiaoxin_firmwire_private`, starting from `main`.

- [ ] **Step 3: Preserve and isolate mini-program state**

The mini-program checkout currently has commits ahead of `origin/main` and uncommitted changes. Use a worktree that starts from the current working-tree state if the Codex app supports it; otherwise stop and ask the user to commit the existing changes before implementation. Do not use stash or destructive checkout without explicit approval.

- [ ] **Step 4: Record baseline tests**

From the server worktree's `main/xiaozhi-server` directory:

```powershell
python -m pytest tests/xiaoxin -q
```

From the firmware worktree root:

```powershell
python -m pytest tests/xiaoxin_*test.py -q
```

From the mini-program worktree root:

```powershell
npm test
```

Expected: capture exact baseline results before changing code.

### Task 2: Implement And Review The Server Plan

**Files:**
- Follow the server plan exactly.

- [ ] **Step 1: Execute Tasks 1-8 of the server plan**

This produces the device Overview topic, OTA contract, ACL, persistence, providers, publisher, service, triggers, heartbeat, and weather-location APIs.

- [ ] **Step 2: Run server review gate**

Run:

```powershell
python -m pytest tests/xiaoxin -q
git diff --check
```

Expected: zero failures and no whitespace errors.

- [ ] **Step 3: Keep `overview_mqtt.enabled=false` in deployed config**

Deploying code before firmware is allowed; retained publishing remains disabled until Task 4. Existing `device/{device_id}/status` and `device/{device_id}/notification` behavior must remain unchanged.

### Task 3: Configure And Verify Mosquitto ACL

**Files:**
- Generated Mosquitto password and ACL files; no secrets committed.

- [ ] **Step 1: Export auth files from the server credential store**

Run the repository's `core.xiaoxin.broker_auth` command using the deployment config and credential DB.

- [ ] **Step 2: Inspect one device ACL**

Required grants:

```text
topic write device/{device_id}/status
topic read device/{device_id}/notification
topic read device/{device_id}/overview
```

Required server grants:

```text
topic read device/+/status
topic write device/+/notification
topic write device/+/overview
```

- [ ] **Step 3: Test positive and negative subscriptions**

Verify the device credential can subscribe to its own Overview, cannot subscribe to another device, and cannot publish Overview.

### Task 4: Implement And Flash The Firmware Plan

**Files:**
- Follow the firmware plan exactly.

- [ ] **Step 1: Execute firmware Tasks 1-5**

This produces OTA config persistence, exact device-topic routing, payload validation, and network heartbeat.

- [ ] **Step 2: Run firmware tests and build**

Run the complete commands from firmware Task 6.

Expected: all Python/host tests pass and the target board firmware links.

- [ ] **Step 3: Flash exactly one acceptance device**

Use “我的小芯” (`1c:db:d4:48:d1:50`) unless the user selects another device. Record the exact firmware commit in the acceptance ledger before testing.

- [ ] **Step 4: Verify MQTT subscriptions**

Broker logs must show the device authenticated with its device credential and subscribed to notification plus Overview topics.

### Task 5: Enable One-Device Server Publishing

**Files:**
- Deployment config only; do not commit secrets.

- [ ] **Step 1: Configure HMAC and enable Overview**

Set a high-entropy deployment-only `overview_mqtt.ip_hmac_secret` and `overview_mqtt.enabled=true`.

- [ ] **Step 2: Restart the server and inspect diagnostics**

Expected: MQTT client connected, no `overview_ip_hmac_unconfigured`, and the test device has a pending or published Overview revision.

- [ ] **Step 3: Trigger manual MQTT resync**

Use the control console's MQTT Overview sync action. Expected: server reports a revision, Broker retains the payload, hardware updates without WebSocket or TTS.

### Task 6: Implement The Mini-Program Plan

**Files:**
- Follow the mini-program plan exactly.

- [ ] **Step 1: Execute mini-program Tasks 1-3**

- [ ] **Step 2: Run `npm test` and WeChat Developer Tools QA**

Expected: automatic IP mode and manual province/city mode both work; no phone location permission appears.

### Task 7: Real-Device Acceptance Matrix

**Files:**
- Modify: `docs/operations/xiaoxin-real-device-acceptance-ledger.md`

- [ ] **Step 1: Weather acceptance**

Record public-IP inferred province/city, daily forecast date, Broker payload revision, and hardware screenshot. Confirm the hardware text is a daily forecast such as `杭州 · 多云 / 今日 26～35℃`, not a claim of current temperature.

- [ ] **Step 2: Course acceptance**

Create, edit, and delete a course in the mini program. For each operation record revision and hardware result. Online MQTT updates must appear within 5 seconds.

- [ ] **Step 3: Todo acceptance**

Create, complete, and delete a todo. Record count/detail changes and revision.

- [ ] **Step 4: Offline retained acceptance**

Disconnect device MQTT, change course/todo, confirm the Broker retained payload advances, reconnect the device, and confirm only the latest revision appears.

- [ ] **Step 5: Unbind/rebind acceptance**

Unbind, confirm the hardware clears old student weather/course/todo, then rebind and confirm the new higher revision restores current data.

- [ ] **Step 6: Voice isolation acceptance**

Use server and device logs to confirm Overview updates do not open WebSocket, start listening, play TTS, or insert notification-center entries.

### Task 8: Final Verification And Rollout Decision

- [ ] **Step 1: Re-run all three repositories' test suites**

Use the exact server, firmware, and mini-program commands recorded in their plans.

- [ ] **Step 2: Run `git diff --check` in all repositories**

Expected: no output.

- [ ] **Step 3: Review acceptance evidence against the design completion standard**

Do not mark HW-03/CC-08 passed unless weather, course, todo, offline retained delivery, unbind clearing, cross-device isolation, and no-WebSocket behavior all have evidence.

- [ ] **Step 4: Roll out to remaining devices or stop**

If the acceptance matrix passes, enable/flash remaining devices in small batches. If any gate fails, keep the rollout limited to the single test device and file the exact failed acceptance row.
