# Xiaoxin Tenant Documentation Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove obsolete tenant-aware MQTT documents and make every active Xiaoxin operations, development, requirements, and Overview document describe the current single-organization, device-scoped MQTT contract without treating tenant work as a prerequisite.

**Architecture:** Delete the two superseded 2026-07-08 artifacts, then reconcile active documentation against one canonical contract: identity is `openid -> owner_user_id -> device_id`, MQTT authorization is device credential plus ACL, and topics are `device/{device_id}/...`. Preserve accurate references to historical code paths such as `tenant_config.py`, but label them as implementation details rather than protocol requirements.

**Tech Stack:** Markdown, YAML, PowerShell validation, Git.

## Global Constraints

- Do not modify Python, C++, mini-program code, SQLite schemas, deployed MQTT credentials, or runtime configuration.
- Delete the obsolete tenant-aware spec and implementation plan instead of archiving them.
- Current MQTT topics are `device/{device_id}/status`, `device/{device_id}/notification`, and `device/{device_id}/overview`.
- Current OTA and Overview payload contracts do not require `tenant_id`.
- Student authorization uses authenticated session, device binding, and current owner verification.
- Device isolation uses per-device MQTT credentials and Broker ACL.
- Existing `tenant_config.py`, `TenantConfig`, database fields, and configuration keys may remain only as accurately named historical implementation details.
- Future multi-organization support is not a current blocker, acceptance item, or rollout step.
- Source specification: `docs/superpowers/specs/2026-07-10-xiaoxin-tenant-documentation-cleanup-design.zh-CN.md`.

---

### Task 1: Delete Superseded Tenant-Aware Artifacts

**Files:**
- Delete: `docs/superpowers/specs/2026-07-08-doorbell-mqtt-tenant-aware-design.zh-CN.md`
- Delete: `docs/superpowers/plans/2026-07-08-doorbell-mqtt-tenant-aware-implementation.md`

**Interfaces:**
- Consumes: the approved deletion decision in the cleanup specification.
- Produces: no executable tenant-aware 2026-07-08 spec or plan remains in the active document tree.

- [ ] **Step 1: Record the failing existence check**

Run:

```powershell
$obsolete = @(
  'docs/superpowers/specs/2026-07-08-doorbell-mqtt-tenant-aware-design.zh-CN.md',
  'docs/superpowers/plans/2026-07-08-doorbell-mqtt-tenant-aware-implementation.md'
)
$obsolete | Where-Object { Test-Path $_ }
```

Expected: both paths are printed, proving the obsolete documents still exist.

- [ ] **Step 2: Delete both files with `apply_patch`**

Delete both complete files. Do not create redirect, archive, or tombstone copies; Git history is the historical record.

- [ ] **Step 3: Verify deletion**

Run:

```powershell
$obsolete = @(
  'docs/superpowers/specs/2026-07-08-doorbell-mqtt-tenant-aware-design.zh-CN.md',
  'docs/superpowers/plans/2026-07-08-doorbell-mqtt-tenant-aware-implementation.md'
)
if ($obsolete | Where-Object { Test-Path $_ }) { throw 'obsolete tenant documents remain' }
```

Expected: exit code 0 with no output.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-08-doorbell-mqtt-tenant-aware-design.zh-CN.md docs/superpowers/plans/2026-07-08-doorbell-mqtt-tenant-aware-implementation.md
git commit -m "docs: remove obsolete tenant mqtt plans"
```

### Task 2: Correct Deployment And Control Console Documentation

**Files:**
- Modify: `docs/getting-started/deployment.md`
- Modify: `docs/development/xiaoxin-control-console.md`

**Interfaces:**
- Consumes: current device-scoped topics and owner-based authorization.
- Produces: operational instructions that match current wake/status behavior and the approved Overview transport boundary.

- [ ] **Step 1: Record the failing stale-contract scan**

Run:

```powershell
rg -n 'tenant/\{tenant_id\}|tenant/hzcu-iee|\{tenant_id\}:\{device_id\}|tenant_mismatch|租户匹配' docs/getting-started/deployment.md docs/development/xiaoxin-control-console.md
```

Expected: stale topic, credential, diagnostic, and authorization lines are printed.

- [ ] **Step 2: Replace the deployment checklist**

Use this contract in `docs/getting-started/deployment.md`:

```markdown
## Xiaoxin Device MQTT First Release

- Configure `xiaoxin_control.doorbell_mqtt.endpoint` before commercial OTA.
- Configure the server publisher credential as `xiaoxin_control.doorbell_mqtt.username` and `xiaoxin_control.doorbell_mqtt.password`.
- Generate Mosquitto `password_file` and `acl_file` from the server credential store before starting the broker.
- Verify a fresh OTA response contains `doorbell_mqtt.enabled: true`, `doorbell_mqtt.version: 1`, endpoint, opaque device credential, `status_topic`, `notification_topic`, and optional `overview_topic`.
- Verify the ESP32 uses the exact endpoint, credential, and topic strings returned by OTA instead of deriving a tenant prefix.
- Verify retained status publishes to `device/{device_id}/status`.
- Verify wake publishes `{"type":"wake"}` to `device/{device_id}/notification` without retain.
- After Overview rollout is enabled, verify QoS 1 retained snapshots publish to `device/{device_id}/overview`.
- Verify a student account cannot wake an unbound device and can act only on its own bound device.
- Verify student profile fields are ignored by wake and binding authorization.
```

Change the preceding Broker description so it says MQTT currently carries status and wake, and is the approved transport for retained Overview snapshots; notification bodies, TTS, ACK, and playback completion remain on WebSocket.

- [ ] **Step 3: Correct the control-console transport and authorization text**

Use these exact topic and responsibility statements:

```markdown
睡眠设备的唤醒依赖 `xiaoxin_control.doorbell_mqtt`。服务端向 `device/{device_id}/notification` 发布 non-retained `{"type":"wake"}`；设备 retained 在线状态使用 `device/{device_id}/status`。

MQTT 负责设备状态、wake 和 retained Overview 快照。通知正文、播报内容、TTS 音频、ACK 和播放完成态仍然通过 WebSocket 下发。
```

Diagnostics must contain:

```markdown
- `doorbell_mqtt_disabled`
- `doorbell_client_not_started`
- `wake_publish_failed`
- `doorbell_status_stale`
- `wake_timeout`
- `device_not_bound`
- `credential_disabled`
```

Authorization must read:

```markdown
学生侧资料字段由学生自行填写，不能参与唤醒授权或绑定判定。学生侧操作必须同时满足“账号已登录 + 设备已绑定 + 当前账号拥有该设备”；预绑定但未绑定学生的硬件唤醒仅允许运维侧执行。
```

- [ ] **Step 4: Verify active operations docs**

Run:

```powershell
rg -n 'tenant/\{tenant_id\}|tenant/hzcu-iee|\{tenant_id\}:\{device_id\}|tenant_mismatch|租户匹配' docs/getting-started/deployment.md docs/development/xiaoxin-control-console.md
rg -n 'device/\{device_id\}/(status|notification|overview)|当前账号拥有该设备' docs/getting-started/deployment.md docs/development/xiaoxin-control-console.md
```

Expected: the first command has no output; the second prints the device-scoped contract and owner authorization.

- [ ] **Step 5: Commit**

```bash
git add docs/getting-started/deployment.md docs/development/xiaoxin-control-console.md
git commit -m "docs: clarify device scoped mqtt operations"
```

### Task 3: Reconcile The Requirements Ledger

**Files:**
- Modify: `docs/requirements/requirements.yaml`

**Interfaces:**
- Consumes: the same device-scoped contract used by operations docs.
- Produces: requirements, risks, decisions, evidence, and remaining work that do not treat tenant migration as current work.

- [ ] **Step 1: Record contradictory requirements**

Run:

```powershell
rg -n 'tenant/\{tenant_id\}|tenant-aware|tenant_mismatch|租户匹配|第一租户|tenant \+ device|多租户 topic' docs/requirements/requirements.yaml
```

Expected: the current hardware state, HW-00, XIAOXIN-006, documentation evidence, RISK-005, and DEC-004 contradictions are printed.

- [ ] **Step 2: Correct hardware current state and HW-00**

Replace the two current-state lines with:

```yaml
    - 服务端已具备门铃 MQTT 设备凭据、OTA `doorbell_mqtt` 对象和 Mosquitto 认证文件导出能力。
    - 当前 MQTT 主题合同为 `device/{device_id}/status`、`device/{device_id}/notification` 和规划中的 `device/{device_id}/overview`；不要求先迁移到租户命名空间。
```

Replace the HW-00 OTA field description with:

```yaml
        - 服务端 OTA 响应已能按已知设备生成版本化 `doorbell_mqtt` 对象，包含 `version`、`enabled`、endpoint、client_id、username、password、status/notification topic、keepalive 和 qos；Overview 计划增加可选 `overview_topic`。
```

- [ ] **Step 3: Correct XIAOXIN-006 and documentation evidence**

The XIAOXIN-006 summary and implemented list must state:

```yaml
    summary: 服务端控制台可以向设备投递通知事件；门铃 MQTT 唤醒已具备设备级凭据、OTA 和诊断骨架。当前 wake 使用 `device/{device_id}/notification`，普通通知、课程提醒和待办提醒正文仍走 WebSocket。
```

Replace tenant-related implemented facts with:

```yaml
      - 已新增 `DoorbellCredentialStore`，为每台设备生成稳定高熵 MQTT credential，支持复用、轮换、禁用和 active 列表；内部历史命名空间不进入设备 topic 或业务 payload。
      - 学生侧 wake 授权要求登录账号拥有已绑定设备；未绑定预置设备会被拒绝。
      - 诊断状态已区分 `doorbell_mqtt_disabled`、`doorbell_client_not_started`、`wake_publish_failed` 和 `device_not_bound`。
```

Keep `main/xiaozhi-server/core/xiaoxin/tenant_config.py` as evidence, but change its note to:

```yaml
        note: 历史命名的 MQTT 配置解析与 device-scoped topic segment 校验；类名不代表设备协议要求租户化。
```

Update XIAOXIN-010 documentation evidence to say the deployment checklist covers endpoint, device credential, Mosquitto auth, OTA topics, ACL, wake, and owner authorization; remove tenant mismatch from diagnostics.

- [ ] **Step 4: Rewrite RISK-005 and DEC-004 as the current decision**

RISK-005 must describe all three device topics and prohibit contract drift:

```yaml
    summary: MQTT 按 `device/{device_id}/status`、`device/{device_id}/notification` 和 `device/{device_id}/overview` 隔离。wake payload 为纯 `{"type":"wake"}`，Overview 使用 QoS 1 retained 快照；通知正文、TTS、ACK 和播放完成态仍走 WebSocket。
```

Mitigation must require globally unique `device_id`, per-device ACL, exact OTA topic strings, and a new design review before any future multi-organization migration. It must not prescribe tenant topics as a pending migration.

DEC-004 must become:

```yaml
    title: 首发设备 MQTT 以服务端配置和 device-scoped topic 为事实源
    decision: 当前单组织部署使用 `device/{device_id}/status`、`device/{device_id}/notification` 和 `device/{device_id}/overview`。endpoint、设备 credential、topic、ACL 和 wake 授权由服务端配置、OTA 响应、凭据库和 owner 绑定关系统一生成；wake payload 为纯 `{"type":"wake"}`。
    rationale: 商用固件不能依赖硬编码公网 broker；学生操作必须绑定到登录账号和其拥有的设备。当前没有多组织共享部署需求，因此租户命名空间不是协议前置条件。
```

Tradeoffs must retain the credential database/auth export cost and state that future multi-organization support requires a new reviewed migration; it cannot be inferred from current historical fields.

- [ ] **Step 5: Parse and scan requirements YAML**

Run from the repository root:

```powershell
python -c "from pathlib import Path; import yaml; yaml.safe_load(Path('docs/requirements/requirements.yaml').read_text(encoding='utf-8')); print('requirements-yaml: PASS')"
rg -n 'tenant/\{tenant_id\}|tenant-aware|tenant_mismatch|租户匹配|第一租户|tenant \+ device|多租户 topic' docs/requirements/requirements.yaml
```

Expected: YAML parser prints `requirements-yaml: PASS`; stale-contract scan has no output. Accurate file paths such as `test_tenant_config.py` and `tenant_config.py` may remain.

- [ ] **Step 6: Commit**

```bash
git add docs/requirements/requirements.yaml
git commit -m "docs: align requirements with device mqtt contract"
```

### Task 4: Correct Overview Wording And Run Repository-Wide Documentation Verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-10-xiaoxin-mqtt-overview-sync-design.zh-CN.md`
- Verify: `docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-server.md`
- Verify: `docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-firmware.md`
- Verify: `docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-miniprogram.md`
- Verify: `docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-rollout.md`

**Interfaces:**
- Consumes: corrected active document contract from Tasks 2 and 3.
- Produces: one consistent current specification and a repository-wide proof that obsolete tenant topics cannot be selected by future work.

- [ ] **Step 1: Replace the residual Overview test requirement**

Change:

```markdown
- 不同用户和租户数据隔离；
```

to:

```markdown
- 不同用户和设备数据隔离；
```

- [ ] **Step 2: Run the active-document stale-contract scan**

Run:

```powershell
$active = @(
  'docs/getting-started/deployment.md',
  'docs/development/xiaoxin-control-console.md',
  'docs/requirements/requirements.yaml',
  'docs/superpowers/specs/2026-07-10-xiaoxin-mqtt-overview-sync-design.zh-CN.md',
  'docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-server.md',
  'docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-firmware.md',
  'docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-miniprogram.md',
  'docs/superpowers/plans/2026-07-10-xiaoxin-mqtt-overview-rollout.md'
)
$matches = Select-String -Path $active -Pattern 'tenant/\{tenant_id\}','tenant/hzcu-iee','tenant-scoped','tenant-aware','legacy_unscoped_topics_enabled','overview_tenant_mismatch'
if ($matches) { $matches; throw 'stale tenant MQTT contract remains' }
```

Expected: no output and exit code 0.

- [ ] **Step 3: Validate Markdown fences and whitespace**

Run:

```powershell
$markdown = @(
  'docs/getting-started/deployment.md',
  'docs/development/xiaoxin-control-console.md',
  'docs/superpowers/specs/2026-07-10-xiaoxin-mqtt-overview-sync-design.zh-CN.md',
  'docs/superpowers/specs/2026-07-10-xiaoxin-tenant-documentation-cleanup-design.zh-CN.md',
  'docs/superpowers/plans/2026-07-10-xiaoxin-tenant-documentation-cleanup.md'
)
foreach ($file in $markdown) {
  $text = Get-Content -Raw -Encoding utf8 $file
  if (([regex]::Matches($text, '(?m)^```')).Count % 2 -ne 0) { throw "unpaired code fence: $file" }
}
git diff --check
```

Expected: exit code 0 and no whitespace errors.

- [ ] **Step 4: Inspect final scope**

Run:

```powershell
git status --short
git diff --stat origin/main...HEAD
```

Expected: only the approved documentation files are deleted or modified; no product code or runtime configuration changed.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-07-10-xiaoxin-mqtt-overview-sync-design.zh-CN.md docs/superpowers/plans/2026-07-10-xiaoxin-tenant-documentation-cleanup.md
git commit -m "docs: prevent tenant mqtt prerequisite drift"
```
