# 小芯调控控制台设计

## 摘要

在 `xiaozhi-server` 内集成一个本地网页调控控制台，用来服务 `D:\AI_Pet\hzcu_xiaoxin_firmwire` 固件功能测试。

第一版控制台通过真实服务端链路向开发板下发三类 `xiaoxin_event`：普通通知、课表提醒、待办提醒。事件可以触发小芯通知弹窗，也可以通过服务端现有 TTS 管线生成语音并下发到设备播放。控制台需要展示在线设备、可唤醒设备、投递状态和失败原因。

产品显示名统一为“小芯”。代码、协议字段和路径继续使用既有 `xiaoxin_*` 命名，避免大范围重命名。

## 目标

- 提供一个可在浏览器打开的“小芯调控控制台”。
- 控制台能列出可控制设备，并区分 `connected`、`wakeable`、`offline`。
- 控制台能下发普通通知、课表提醒、待办提醒。
- 每类事件都支持弹窗和可选语音播报。
- 语音播报由 `xiaozhi-server` 通过现有 TTS 和音频链路完成。
- 设备休眠但 MQTT 门铃在线时，控制台能先唤醒设备，再下发事件。
- 控制台能看到 `created`、`waking`、`sent`、`device_received`、`speaking`、`done`、`failed` 等状态。
- 固件能解析 `xiaoxin_event` 并回传 `xiaoxin_ack`。

## 不做

- 不做微信小程序、手机 App 或正式用户客户端。
- 不做账号体系、多用户权限、商业套餐、支付和额度统计。
- 不做真实课表自动调度；第一版课表提醒由控制台手动触发。
- 不把完整通知内容或音频放进 MQTT 门铃 payload。
- 不把 `manager-web` 或 `manager-api` 作为本地调控控制台入口。

## 选定方案

控制台直接集成到 `xiaozhi-server`。

```text
浏览器控制台
  -> xiaozhi-server HTTP API
  -> 在线/门铃设备注册表
  -> ConnectionHandler 或 MQTT doorbell wake
  -> 固件 xiaoxin_event 解析
  -> 通知弹窗 / TTS 播报 / ACK 回传
  -> 控制台状态时间线
```

这个方案复用现有 WebSocket、MQTT Gateway、TTS、音频发送和设备连接生命周期，不新增一个独立 Product API 服务。

## 备选方案

### 独立调控服务

单独起一个 HTTP 服务承载控制台，再调用 `xiaozhi-server`。边界清楚，但第一版需要额外解决跨服务连接注册、TTS 调用和状态同步，增加调试成本。

### 接入 manager-api / manager-web

更像正式管理后台，但 Java/Vue 管理端会带来较大范围改造。当前目标是固件功能测试和本地演示，不需要完整后台。

### 串口调试控制台

网页或脚本包装串口命令最快，但不能验证真实服务端投递、远程唤醒和 TTS 链路。串口命令可继续作为拍摄和调试兜底，不作为第一版主链路。

## 服务模块

在 `main/xiaozhi-server/core` 下新增或扩展以下内部模块：

- `XiaoxinDeviceRegistry`
  - 维护 `device_id -> ConnectionHandler` 的活跃连接。
  - 记录最后活动时间、连接来源、当前连接状态。
  - 维护 MQTT 门铃状态：`online`、`offline`、最后状态更新时间。

- `XiaoxinDeliveryStore`
  - 内存保存最近 N 条投递记录。
  - 保存状态时间线、失败原因、事件 payload 摘要。
  - 第一版不落数据库，服务重启后历史清空。

- `XiaoxinEventDispatcher`
  - 校验控制台提交的事件。
  - 生成 `delivery_id` 和 `xiaoxin_event` payload。
  - 在线设备直接通过 `ConnectionHandler.websocket.send(...)` 下发。
  - 可唤醒设备先发 MQTT wake，再等待设备重新注册后投递。
  - `speak=true` 时触发当前连接的 TTS 队列播放 `speak_text`。

- `XiaoxinDoorbellClient`
  - 连接配置中的 MQTT broker。
  - 订阅 `device/+/status`，读取 retained `online` / `offline` 状态。
  - 发布 `{"type":"wake"}` 到 `device/{device_id}/notification`。
  - 将门铃状态变化写入 `XiaoxinDeviceRegistry`。

- `XiaoxinControlHandler`
  - 挂载控制台 HTML 页面和 HTTP API。
  - 处理设备列表、事件投递、投递记录查询。

- `ConnectionHandler`
  - 连接建立后注册到 `XiaoxinDeviceRegistry`。
  - 连接关闭时注销或标记为未连接。
  - 收到 `xiaoxin_ack` 时更新 `XiaoxinDeliveryStore`。
  - 暴露一个用于控制台播报的 TTS 方法，不让控制台直接操作 TTS 内部队列。

## HTTP 路由

控制台挂在现有 HTTP 服务端口上。

```text
GET  /xiaoxin/control/
GET  /api/xiaoxin/devices
POST /api/xiaoxin/events
GET  /api/xiaoxin/deliveries
GET  /api/xiaoxin/deliveries/{delivery_id}
```

### 设备列表接口：`GET /api/xiaoxin/devices`

返回设备列表。

```json
{
  "devices": [
    {
      "device_id": "aa:bb:cc:dd:ee:ff",
      "state": "connected",
      "transport": "websocket",
      "last_seen_at": "2026-07-03T10:30:00+08:00",
      "doorbell_state": "online"
    }
  ]
}
```

`state` 取值：

- `connected`：已有活跃 WebSocket/MQTT Gateway 语音通道，可直接投递。
- `wakeable`：语音通道不在线，但 MQTT 门铃在线，可唤醒后投递。
- `offline`：语音通道和门铃都不在线。

### 事件投递接口：`POST /api/xiaoxin/events`

请求字段：

```json
{
  "device_id": "aa:bb:cc:dd:ee:ff",
  "event": "course_reminder",
  "title": "上课提醒",
  "body": "15分钟后 高等数学 @ 3教204",
  "tag": "课程",
  "priority": 1,
  "ttl_ms": 0,
  "speak": true,
  "speak_text": "小芯提醒你，十五分钟后有高等数学课，地点在三教二零四。",
  "course_name": "高等数学",
  "classroom": "3教204",
  "starts_at": "2026-07-03T10:10:00+08:00",
  "remind_before_min": 15
}
```

响应字段：

```json
{
  "delivery_id": "del_20260703_103000_abcd",
  "state": "created"
}
```

## 控制台界面

页面第一版分四块：

- 设备栏
  - 展示设备 ID、连接状态、门铃状态、连接来源、最后活动时间。
  - 只允许选择 `connected` 或 `wakeable` 设备发送事件。

- 事件下发
  - 三个标签页：普通通知、课表提醒、待办提醒。
  - 每个表单包含标题、内容、优先级、TTL、是否播报、播报文本。
  - 课表提醒增加课程名、教室、开始时间、提前提醒分钟数。
  - 待办提醒增加待办标题和截止时间。

- 快速模板
  - 一键填充“15 分钟后上课”。
  - 一键填充“今天记得完成待办”。
  - 一键填充“普通提醒”。

- 投递记录
  - 展示 `delivery_id`、事件类型、目标设备、当前状态、失败原因。
  - 点击记录展示状态时间线。

## 设备协议

服务端向固件下发统一消息：`type=xiaoxin_event`。

### 普通通知

```json
{
  "type": "xiaoxin_event",
  "delivery_id": "del_...",
  "event": "notification",
  "title": "通知标题",
  "body": "通知内容",
  "tag": "通知",
  "priority": 2,
  "ttl_ms": 0,
  "speak": true,
  "speak_text": "小芯提醒你，通知内容。"
}
```

### 课表提醒

```json
{
  "type": "xiaoxin_event",
  "delivery_id": "del_...",
  "event": "course_reminder",
  "course_name": "高等数学",
  "classroom": "3教204",
  "starts_at": "2026-07-03T10:10:00+08:00",
  "remind_before_min": 15,
  "title": "上课提醒",
  "body": "15分钟后 高等数学 @ 3教204",
  "tag": "课程",
  "priority": 1,
  "ttl_ms": 0,
  "speak": true,
  "speak_text": "小芯提醒你，十五分钟后有高等数学课，地点在三教二零四。"
}
```

### 待办提醒

```json
{
  "type": "xiaoxin_event",
  "delivery_id": "del_...",
  "event": "todo_reminder",
  "todo_title": "交实验报告",
  "due_at": "2026-07-03T18:00:00+08:00",
  "title": "待办提醒",
  "body": "记得提交实验报告",
  "tag": "待办",
  "priority": 2,
  "ttl_ms": 0,
  "speak": true,
  "speak_text": "小芯提醒你，今天傍晚前记得提交实验报告。"
}
```

## ACK 协议

固件回传：

```json
{
  "type": "xiaoxin_ack",
  "delivery_id": "del_...",
  "state": "device_received",
  "reason": null,
  "device_time": 1783065600000
}
```

首版 ACK 状态：

- `device_received`：固件收到并成功显示弹窗。
- `speaking`：固件进入或确认播报阶段。服务端也会在开始 TTS 时记录 `speaking`。
- `done`：事件处理完成。
- `failed`：事件解析、显示或播放失败。

服务端状态：

- `created`
- `waking`
- `sent`
- `device_received`
- `speaking`
- `done`
- `failed`

失败 reason：

- `device_offline`
- `wake_timeout`
- `device_busy`
- `send_failed`
- `tts_failed`
- `ack_timeout`
- `invalid_payload`
- `unknown`

## MQTT 门铃唤醒

门铃 MQTT 只承担唤醒职责。

固件已有门铃模型：

```text
status topic:       device/{device_id}/status
notification topic: device/{device_id}/notification
wake payload:       {"type":"wake"}
```

服务端需要使用与固件一致的 broker 配置。第一版可以复用 `server.mqtt_gateway` 和 `server.mqtt_signature_key`，或新增明确的 `xiaoxin_control.doorbell_mqtt` 配置；实现计划中应优先沿用现有配置，避免出现两套 broker 来源。

投递流程：

```text
设备已连接：
控制台 -> HTTP API -> ConnectionHandler -> xiaoxin_event -> TTS 音频 -> ACK

设备休眠但门铃在线：
控制台 -> HTTP API
  -> delivery=waking
  -> MQTT publish device/{device_id}/notification {"type":"wake"}
  -> 固件收到 wake 后建立 WebSocket
  -> server 等待 device_id 注册
  -> xiaoxin_event -> TTS 音频 -> ACK

设备门铃也离线：
控制台 -> failed=device_offline
```

超时规则：

- 发送 wake 后，服务端等待设备注册。
- 超过配置的 `wake_timeout_seconds` 仍未连接，状态变为 `failed`，reason 为 `wake_timeout`。
- 如果设备连接成功但事件发送失败，reason 为 `send_failed`。

## TTS 流程

`speak=true` 时：

1. 服务端先发送 `xiaoxin_event`，让固件显示弹窗。
2. 固件回 `device_received`。
3. 服务端调用目标 `ConnectionHandler` 的控制台播报方法。
4. 该方法生成新的 `sentence_id`，把 `speak_text` 放入现有 TTS 队列。
5. TTS Provider 生成音频并通过既有 `sendAudioMessage` / `sendAudio` 下发。
6. 服务端记录 `speaking`。
7. TTS 发送完成后服务端记录 `done`。如果固件回传 `done`，时间线也记录该 ACK。

`speak=false` 时：

- 服务端只发送 `xiaoxin_event`。
- 固件显示弹窗并回 `device_received`。
- 服务端将最终状态更新为 `done`。

## 固件改动

固件项目 `D:\AI_Pet\hzcu_xiaoxin_firmwire` 需要配合以下改动：

- `Application::OnIncomingJson` 新增 `xiaoxin_event` 分支。
- 解析 `delivery_id`、`event`、`title`、`body`、`tag`、`priority`、`ttl_ms`、`speak`、`speak_text`。
- `notification` 映射为普通通知。
- `course_reminder` 映射为课程提醒。
- `todo_reminder` 映射为待办提醒。
- 缺少 `delivery_id`、`event`、`title` 或 `body` 时回 `xiaoxin_ack failed invalid_payload`。
- 收到并显示后回 `xiaoxin_ack device_received`。
- 播报阶段可回 `speaking` 和 `done`。如果固件无法准确判断 TTS 完成，第一版允许服务端以音频发送完成作为 `done`。
- Doorbell MQTT 继续只解析 `{"type":"wake"}`，不解析完整提醒内容。

## 安全

控制台默认用于本地开发环境。

- 未配置控制台密钥时，只允许来自本机的 HTTP 请求。
- 配置 `xiaoxin_control.secret` 后，控制台 API 要求请求头携带该密钥。
- 控制台不暴露 TTS API key。
- 控制台不允许向非 `connected` 或 `wakeable` 设备投递。

## 测试

服务端测试：

- 在线设备注册和注销。
- 门铃状态更新后设备状态从 `offline` 变为 `wakeable`。
- `POST /api/xiaoxin/events` 对三类事件生成正确 payload。
- `connected` 设备直接发送事件。
- `wakeable` 设备先进入 `waking`，注册后继续发送事件。
- wake 超时变为 `failed wake_timeout`。
- 收到 `xiaoxin_ack` 后更新投递时间线。
- `speak=true` 调用 TTS 播放方法，`speak=false` 不调用。

固件测试：

- `xiaoxin_event notification` 显示普通通知。
- `xiaoxin_event course_reminder` 显示课程提醒。
- `xiaoxin_event todo_reminder` 显示待办提醒。
- 缺关键字段时回 `failed invalid_payload`。
- `{"type":"wake"}` 能在空闲时建立 WebSocket。
- 忙碌时 wake 不强制打断，并保留日志或 ACK 供服务端标记 `device_busy`。

端到端验收：

- 控制台能看到开发板为 `connected` 或 `wakeable`。
- 普通通知能在设备上弹窗，控制台看到 `device_received`。
- 课表提醒开启播报后，设备弹窗并播放小芯语音，控制台状态到 `done`。
- 待办提醒关闭播报后，只弹窗不播报，控制台状态到 `done`。
- 设备休眠但门铃在线时，控制台状态先到 `waking`，设备被唤醒后收到事件并播报。
- 设备完全离线时，控制台显示 `device_offline`。
- TTS 失败、ACK 超时、唤醒超时都有可见失败原因。

## 范围核对

这是一项跨服务端和固件的小型闭环改造，适合拆成一个实现计划：

1. 服务端在线设备注册、门铃状态和投递记录。
2. 服务端控制台 API 和页面。
3. 服务端事件投递、MQTT wake 和 TTS 播报。
4. 固件 `xiaoxin_event` 解析和 ACK。
5. 端到端验证。

后续真实课表导入、小程序客户端、离线队列和长期投递历史应单独进入后续设计。
