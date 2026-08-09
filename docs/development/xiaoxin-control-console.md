# 小芯调控控制台

本文记录 `main/xiaozhi-server` 内置的小芯调控控制台本地启动方式、接口约束与手工验收步骤。

## 本地配置

`main/xiaozhi-server` 启动时会先读取 `data/.config.yaml`，再用根目录 `config.yaml` 里的默认值补齐未覆盖项。开发联调请优先修改 `main/xiaozhi-server/data/.config.yaml`，或使用等价的本地覆盖配置文件；不要直接改提交进仓库的默认配置。

至少需要确认以下配置存在：

```yaml
server:
  http_port: 8003

xiaoxin_control:
  enabled: true
  reminder_tick_seconds: 30
  todo_reminder_replay_window_minutes: 120
  course_reminder_scheduler_enabled: true
  todo_reminder_scheduler_enabled: true
  doorbell_mqtt:
    endpoint: "127.0.0.1:1883"
    username: null
    password: null

xiaoxin_runtime:
  companion_db_path: "data/xiaoxin_companion.db"
  companion_worker_enabled: true
```

说明：

- `xiaoxin_control.enabled: true` 用于启用控制台运行时和 HTTP 路由。
- `xiaoxin_control.todo_reminder_scheduler_enabled` 和 `xiaoxin_control.course_reminder_scheduler_enabled` 用于启动到点待办/课程提醒后台循环；`reminder_tick_seconds` 控制共享轮询间隔。
- `xiaoxin_control.todo_reminder_replay_window_minutes` 控制普通提醒的补播有效期，默认 `120` 分钟。设备在截止时间前上线时仍可补播；达到或超过截止时间后提醒记为错过，不再创建新的投递或播报。
- `xiaoxin_control.doorbell_mqtt` 用于睡眠设备的 MQTT 唤醒能力；`endpoint` 为空时不会启用门铃 MQTT 客户端。
- `server.http_port` 默认是 `8003`。控制台页面和控制 API 都走这个 HTTP 端口。
- `xiaoxin_control.secret` 不再作为 8003 Xiaoxin 控制台的访问门禁；控制台入口公开，账号登录和用户隔离是当前鉴权边界。
- control runtime 始终创建 Store-backed `CompanionMind` 供控制 API 使用；V2 已是唯一实现，不再有开发期开关。
- `companion_worker_enabled` 只控制异步 ReflectionModel 整理循环。关闭 worker 或模型初始化失败时，查看、纠正、删除、重置和 purge 仍可用。

## 启动服务

从服务仓库根目录进入 `main/xiaozhi-server` 后启动。正式文档路径以主仓检出为准：

```powershell
Set-Location D:\Learn\xiaoxin-esp32-server\main\xiaozhi-server
python app.py
```

默认控制台地址：

```text
http://127.0.0.1:8003/xiaoxin/control/
```

如果 `server.http_port` 改成其他端口，控制台地址中的端口也要同步改掉。例如 `http_port: 9000` 时，入口地址变为 `http://127.0.0.1:9000/xiaoxin/control/`。

## 控制 API

控制台使用以下接口：

- `GET /api/xiaoxin/devices`
- `POST /api/xiaoxin/events`
- `GET /api/xiaoxin/deliveries`
- `GET /api/xiaoxin/deliveries/{delivery_id}`
- `GET /api/xiaoxin/memory-subjects/{subject_id}/memory?surface=operator`
- `GET /api/xiaoxin/memory-subjects/{subject_id}/memory?surface=miniprogram`
- `POST /api/xiaoxin/memory-subjects/{subject_id}/memory/control`

常见用法：

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:8003/api/xiaoxin/devices -UseBasicParsing
Invoke-WebRequest -Uri http://127.0.0.1:8003/api/xiaoxin/deliveries -UseBasicParsing
```

陪伴记忆读取只允许 `operator` 和 `miniprogram` 两种 surface。响应来自 `CompanionMind.project()`，包含小芯年龄、模糊关系阶段、安全 Evidence 摘要、状态、当前 epoch、调整、章节和 job 定位信息；不返回模型 prompt、chain-of-thought、完整画像或无必要原文。

`operator` 是开发与验收 surface，额外返回主体内的安全诊断数据：最多 250 条 Evidence 时间线、关系 epoch、Evidence 替代关系、最多 100 个 Session Capsule、Adjustment/Chapter Evidence 血缘、最多 20 条未过期召回审计、当前 epoch 的关系阶段事件，以及 Evidence/job 状态计数。诊断查询仍按当前账号、personal pet 和 `memory_subject_id` 隔离；`miniprogram` 不获得这些字段。

召回审计区只展示 interaction kind、候选数量、turn ID、query digest、入选 Evidence ID、确定性总分、耗时和过期时间。服务端与页面都不提供查询原文、Evidence 内容或摘要；审计默认 7 天过期，执行 `purge_personal_memory` 时删除。

关系阶段事件区展示阶段变化、relationship epoch、continuity、knowledge、helpfulness、attunement、无文本 reason code、策略版本和发生时间。它用于解释“为什么进入这个阶段”，不展示用户原文，也不向 miniprogram 返回内部四维数字。大量空闲聊天不应单独升级；负反馈应先降低主动、追问或私人记忆引用。

控制请求示例：

```json
{
  "action": "set_boundary",
  "idempotency_key": "console-boundary-20260718-1",
  "payload": {
    "boundary_key": "question_frequency",
    "value": "none",
    "source_summary": "用户明确要求不要追问。"
  }
}
```

支持的用户控制为 `forget_evidence`、`forget_theme`、`correct_evidence`、`set_boundary`、`revoke_boundary`、`confirm_candidate`、`reject_candidate`、`reset_relationship` 和 `purge_personal_memory`。服务器生成控制时间，handler 只做鉴权、上下文转换和输入校验，确定性行为由 `CompanionMind.apply_control()` 执行。

自然对话候选在 Evidence 时间线中显示为 `candidate` 且 `prompt blocked`。候选卡片提供“确认候选”和“拒绝候选”：确认后才可进入安全召回，拒绝后不会被后台整理重新激活。工作台只展示候选的最小安全摘要，不展示完整 turn source；临时来源数量通过 health 诊断定位。

`reset_relationship` 保留学生资料、账号、设备、明确边界与用户事实，只停用旧关系记忆；`purge_personal_memory` 清除 Companion DB 中的陪伴和成长内容，但仍保留账号、设备绑定、personal pet 归属和学生资料。两者不是同义操作。

## 开发者记忆工作台

登录 `/xiaoxin/control/` 后，在“记忆主体”中点击“查看记忆”，开发者工作台显示：

- 当前设备、personal pet、`memory_subject_id` 和 relationship epoch 的归属链；
- 小芯年龄、关系阶段、当前可用 Evidence、章节和待整理任务数量；
- active、candidate、superseded、forgotten、expired Evidence 时间线；
- Capsule、Adjustment、Chapter 和 Evidence relation 的派生血缘；
- 最近 7 天的短期召回审计，只含查询摘要哈希、Evidence ID、无文本分数和耗时；
- 当前 relationship epoch 的阶段事件和四维质量快照；
- Evidence 与后台 job 状态计数、历史/当前 epoch；
- 九类类型化控制操作及其执行结果。

控制台中的“设备”只是接入和诊断入口，不能被解释为记忆所有者。稳定归属始终是 `owner -> personal pet -> memory_subject_id`，换设备不得自动重建长期记忆。

Evidence 行可以预填纠正、忘记和撤销边界操作。`correct_evidence` 的替代内容必须填写 JSON 对象。`reset_relationship` 要求输入 `RESET_RELATIONSHIP`，`purge_personal_memory` 要求输入 `PURGE_PERSONAL_MEMORY`；这些确认只防止页面误触，服务端仍独立执行鉴权、幂等和确定性校验。

## 鉴权行为

- 控制台页面 `/xiaoxin/control/` 允许公网访问，用于展示注册和登录界面。
- `POST /api/xiaoxin/auth/register` 与 `POST /api/xiaoxin/auth/login` 公开，用于创建和获取 `xiaoxin_session`。
- 设备、speaker、memory subject、陪伴记忆、投递和 delivery API 需要 `xiaoxin_session`。
- 账号登录之后，控制 API 按当前账号过滤设备、投递历史和记忆主体。
- 当前 owner 只能读取自己的 subject；跨 owner 和已合并的旧 subject 返回 404。
- `device_unknown` / `device_fallback` 只能得到不含私人 Evidence 的中性投影，且不能执行个人记忆控制。
- 旧 legacy-memory、DELETE memory 和 query-forget 路由已经删除；客户端必须使用类型化 V2 投影和控制端点。

## 唤醒与投递行为

睡眠设备的唤醒依赖 `xiaoxin_control.doorbell_mqtt`。服务端向 `device/{device_id}/notification` 发布下面的 non-retained payload：

```json
{"type":"wake"}
```

当 `xiaoxin_control.doorbell_mqtt.endpoint` 已配置时，断开 WebSocket 的设备即使当前运行时状态是
`offline`，投递也会先尝试 MQTT 唤醒。此时投递记录应先进入 `waking`；若设备没有在
`wake_timeout_seconds` 内重连，最终失败原因是 `wake_timeout`。只有在没有 WebSocket 连接且没有可用
门铃 MQTT 唤醒路径时，才会直接失败为 `device_offline`。

MQTT 负责设备在线状态、wake，以及 Overview 上线后的 retained 状态快照。通知正文、播报内容和 TTS 音频不经 MQTT 发送；它们仍然通过 WebSocket 下发，并沿用现有 TTS 流水线完成播放、ACK 处理与完成态回写。

## 手工验收清单

建议至少准备一台可连 WebSocket 的设备，并在需要时保持门铃 MQTT 在线。

下面清单是人工 / 真机验收步骤。本文档记录了检查项，但自动化会话不会把它们记成已执行。

1. 打开控制台，确认已连接设备显示为 `connected`。
2. 断开 WebSocket、仅保留门铃 MQTT 在线，确认设备显示为 `wakeable`。
3. 让设备完全离线，确认设备显示为 `offline`。
4. 发送 `notification`，关闭播报，确认设备弹出通知，投递记录进入 `device_received` 或 `done`。
5. 发送 `course_reminder`，开启播报，确认设备先收到通知，再走语音播报，最终记录进入 `done`。
6. 发送 `todo_reminder`，关闭播报，确认只有提醒通知，没有语音播放，最终记录进入 `done`。
7. 让设备进入睡眠但保持门铃 MQTT 在线，再发送提醒，确认记录先进入 `waking`，设备被 MQTT 唤醒并重连后进入 `sent`，随后继续完成。
8. 在配置了门铃 MQTT 但设备没有重连时投递，确认记录先进入 `waking`，最终失败原因是 `wake_timeout`。
9. 移除或清空 `doorbell_mqtt.endpoint` 后对断开设备投递，确认最终失败原因是 `device_offline`。
10. 人为制造设备 ACK 超时，确认最终失败原因是 `ack_timeout`。
11. 人为制造 TTS 未完成或音频链路失败，确认最终失败原因是 `tts_failed`。

## 最小联调步骤

1. 在 `data/.config.yaml` 打开 `xiaoxin_control.enabled` 并填好 `doorbell_mqtt.endpoint`。
2. 在 `main/xiaozhi-server` 执行 `python app.py`。
3. 访问 `http://127.0.0.1:8003/xiaoxin/control/`。
4. 调用 `/api/xiaoxin/devices`，确认能看到设备列表。
5. 通过控制台或 `POST /api/xiaoxin/events` 发起一条提醒，检查 `/api/xiaoxin/deliveries` 与详情接口中的状态流转。

## Mosquitto Doorbell Broker

定时提醒需要服务端 Mosquitto broker。设备主监听流睡眠时，应保持 ESP32 的 Wi-Fi/MQTT 门铃连接在线。

Docker 服务：

```bash
python -m core.xiaoxin.broker_auth --config data/.config.yaml --db data/xiaoxin_doorbell_credentials.db --out mosquitto/auth
docker compose up -d xiaoxin-doorbell-mqtt
docker logs -n 80 xiaoxin-doorbell-mqtt
```

`xiaoxin-doorbell-mqtt` uses `allow_anonymous false`; regenerate `mosquitto/auth/password_file` and `mosquitto/auth/acl_file` after device credential rotation or before field testing a fresh database.

运行时配置：

```yaml
xiaoxin_control:
  doorbell_mqtt:
    endpoint: "SERVER_IP:1883"
    username: null
    password: null
    keepalive_seconds: 240
```

仅 Docker 内部联调时，`endpoint` 可以是 `xiaoxin-doorbell-mqtt:1883`。真机 ESP32 需要使用设备可访问的服务器地址。

服务端向 `device/{device_id}/notification` 发布 non-retained `{"type":"wake"}`；设备 retained 在线状态使用 `device/{device_id}/status`；Overview 上线后使用 `device/{device_id}/overview` 接收 QoS 1 retained 快照。提醒正文、TTS 音频、ACK 和播放完成态仍然走 WebSocket。

## Doorbell MQTT Diagnostics

同样是“设备无法被唤醒”，后端诊断必须继续细分，至少区分以下原因：

- `doorbell_mqtt_disabled`
- `doorbell_client_not_started`
- `wake_publish_failed`
- `doorbell_status_stale`
- `wake_timeout`
- `device_not_bound`
- `credential_disabled`

学生侧资料字段由学生自行填写，不能参与唤醒授权或绑定判定。学生侧操作必须同时满足“账号已登录 + 设备已绑定 + 当前账号拥有该设备”；预绑定但未绑定学生的硬件唤醒仅允许运维侧执行。

## Identity Console Notes

The Xiaoxin control console on port `8003` uses a local SQLite identity database for console accounts, sessions, device ownership, speakers, and memory subjects.

Recommended local config:

```yaml
xiaoxin_control:
  enabled: true
  identity_db: "data/xiaoxin_control.db"

xiaoxin_runtime:
  enabled: true
  companion_db_path: "data/xiaoxin_companion.db"
  companion_worker_enabled: true
```

- `xiaoxin_control.identity_db` is a local SQLite file used for console users, sessions, device ownership, speakers, and memory subjects.
- `xiaoxin_runtime.companion_db_path` is the sole V2 companion-memory fact store. Identity and student profile data remain in `xiaoxin_control.db`.
- V2 control endpoints never accept filenames or arbitrary paths。运行配置中已不存在旧 memory 文件目录。

## Registration And Login

- Open `/xiaoxin/control/` and register a local console account if one does not exist.
- Later visits should use the login form instead of re-registering.
- Successful register/login sets the `xiaoxin_session` cookie.
- Control APIs that read account-scoped data require this session once auth is enabled.

## Device Binding

- Seen devices can appear before they are bound to a console account.
- Binding is a manual action from the control console.
- Binding assigns ownership in the local SQLite identity database; it does not write companion Evidence or infer memory ownership from display names.
- A device already bound to another local console user cannot be claimed by a second user through the UI.

## Memory Subject Isolation

- Persisted Xiaoxin memory must resolve to a stable opaque `memory_subject_id`.
- The system must not persist user memory under raw speaker text, display names, usernames, or a global unknown bucket.
- Unknown speakers resolve to a device/account-scoped unknown subject.
- Unbound devices resolve to a device fallback subject rather than writing into any user subject.
- If identity resolution fails, prefer non-persistent or device-scoped fallback behavior over cross-user writes.

## Legacy Memory Cutover Policy

- V2 已完成 replace-not-layer 切换：生产 runtime、prompt、后台 job 和控制入口只使用 `CompanionMind` 与 `xiaoxin_companion.db`。
- 旧生产模块、旧配置、旧 API 和旧结构性测试已经删除；新运行不会生成旧 profile、episodic、companion、growth 或 relationship JSON/JSONL。
- 现存旧数据只允许作为部署前创建的只读文件级归档保存，不自动导入 V2，也不按文件名猜测 owner 或 subject。
- 回滚只能恢复旧代码并挂载旧只读副本；禁止把 V2 Evidence 反向写回旧 JSON/JSONL。
