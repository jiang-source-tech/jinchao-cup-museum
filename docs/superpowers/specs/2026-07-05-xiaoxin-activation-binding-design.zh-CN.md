# 小新设备激活码绑定设计

日期：2026-07-05

## 背景

当前控制台可以用账号登录、列出用户已绑定设备，并且已有开发期的手动 `device_id` 绑定与 MQTT 敲门能力。但是正式绑定链路还没有闭环：服务端 OTA 响应没有返回 `activation` 字段，控制台也没有“输入设备播报验证码后绑定”的入口。

固件端已经具备激活码展示和本地音频播报能力。设备请求 OTA 时会解析响应中的 `activation.code`、`activation.message`、`activation.challenge`、`activation.timeout_ms`；存在 `activation.code` 时会调用 `ShowActivationCode()`，先播放 `activation.ogg`，再逐位播放 `0.ogg` 到 `9.ogg`。因此服务端不需要合成语音，只需要返回数字验证码。

## 目标

实现正式的首次绑定链路：

1. 设备首次连 WiFi 后请求 OTA，服务端记录稳定 `device_id`。
2. 如果设备未绑定，服务端生成短期 6 位绑定码，并在 OTA 响应中返回 `activation`。
3. 固件用本地 `.ogg` 播报绑定码。
4. 用户登录控制台，输入绑定码完成账号与设备绑定。
5. 设备轮询 `/xiaozhi/ota/activate`，绑定完成后收到 `200` 并结束激活。
6. 绑定后的设备长期出现在控制台；在线时可通过 MQTT “敲门”进入聆听。

## 非目标

本设计不实现服务端 TTS，不把验证码音频从服务端下发给固件。

本设计不改变 `device_id` 的来源。正式使用要求固件传入稳定设备 ID，当前优先沿用 `SystemInfo::GetMacAddress()`。

本设计不要求删除开发期手动 `device_id` 绑定。手动绑定保留为调试入口，但正式用户路径使用验证码绑定。

## 服务端接口

### OTA 响应

路径沿用现有 OTA 入口：

```text
POST /xiaozhi/ota/
```

设备请求头至少包含：

```text
Device-Id: <stable-device-id>
Client-Id: <client-id>
Activation-Version: 1 或 2
```

如果设备未绑定，服务端在现有 `server_time`、`firmware`、`mqtt` 或 `websocket` 响应基础上增加：

```json
{
  "activation": {
    "code": "482913",
    "message": "请在控制台输入绑定码 482913",
    "challenge": "random-challenge",
    "timeout_ms": 600000
  }
}
```

规则：

- `code` 是 6 位数字字符串。
- `message` 给设备屏幕展示，同时作为 `activation.ogg` 播报前的上下文文案。
- `challenge` 必须返回，因为当前固件 `Activate()` 在没有 challenge 时会直接失败。
- `timeout_ms` 默认 10 分钟。
- 如果设备已绑定，不再返回 `activation`。
- 如果同一未绑定设备在有效期内重复请求 OTA，服务端复用尚未过期的 code，避免设备反复播报不同验证码。

### 激活轮询

路径沿用固件现有逻辑：

```text
POST /xiaozhi/ota/activate
```

设备请求头继续携带 `Device-Id`。请求体可为空对象或包含固件已有 HMAC activation payload。第一阶段服务端只用 `Device-Id` 判断绑定状态，不强制校验 HMAC。

响应：

- `202 Accepted`：设备验证码仍在等待用户绑定。
- `200 OK`：设备已绑定，激活完成。
- `404 Not Found`：没有该设备的激活会话，设备应重新请求 OTA。
- `410 Gone`：激活码过期，设备应重新请求 OTA 获取新码。

### 控制台验证码绑定

新增接口：

```text
POST /api/xiaoxin/devices/activation-bind
```

请求要求用户已登录，使用现有 `xiaoxin_session` cookie。

请求体：

```json
{
  "code": "482913",
  "display_name": "桌面小新"
}
```

响应：

```json
{
  "success": true,
  "device": {
    "device_id": "aa:bb:cc:dd:ee:ff",
    "owner_user_id": "usr_xxx",
    "display_name": "桌面小新",
    "bind_status": "bound"
  }
}
```

错误：

- `400 code required`：验证码为空或格式错误。
- `404 activation code not found`：验证码不存在。
- `410 activation code expired`：验证码过期。
- `409 device is already bound to another user`：设备已被其他用户绑定。
- `401 login required`：用户未登录。

绑定成功后，该 activation code 标记为已消费。再次使用同一码应失败。

## 数据模型

新增轻量激活码存储。优先放在 `core/xiaoxin/identity` 或 `core/xiaoxin` 下，避免塞进控制台 handler。

建议表：

```sql
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
);
```

需要的 store 方法：

- `create_or_refresh_activation(device_id, ttl_seconds) -> ActivationSession`
- `get_activation_by_code(code) -> ActivationSession | None`
- `mark_activation_consumed(code) -> None`
- `get_activation_by_device_id(device_id) -> ActivationSession | None`
- `delete_expired_activations(now) -> int`

验证码冲突时重新生成，最多重试有限次数。

## 状态流

未绑定设备首次上线：

```text
OTA request
-> identity_store.upsert_seen_device(device_id)
-> activation_store.create_or_refresh_activation(device_id)
-> OTA response includes activation
-> device speaks code
-> device polls /activate
```

用户绑定：

```text
console activation-bind
-> validate login
-> lookup code
-> reject expired or consumed
-> bind_device(device_id, current_user.id, display_name)
-> mark code consumed
-> return bound device
```

设备激活完成：

```text
device polls /activate
-> device owner_user_id exists
-> return 200
-> firmware enters idle
-> firmware starts doorbell MQTT
```

后续上电：

```text
device requests OTA
-> service sees device already bound
-> no activation field
-> firmware completes activation normally
-> starts doorbell MQTT
-> console shows offline/wakeable/connected based on runtime state
```

## MQTT 敲门要求

绑定只解决“账号拥有哪个 `device_id`”。远程敲门还要求固件每次上电后自动保持 MQTT 门铃在线：

```text
connect WiFi
-> complete OTA activation
-> start doorbell MQTT
-> subscribe device/{device_id}/notification
-> publish device/{device_id}/status = online
```

服务端敲门发送：

```text
topic: device/{device_id}/notification
payload: {"type":"wake"}
```

固件收到后进入聆听。

## 安全与边界

验证码只作为“用户在设备附近”的短期证明，不作为长期凭证。

同一验证码只能消费一次。过期码不可绑定。已绑定到其他用户的设备不能被抢绑。

第一阶段不强制 HMAC activation 校验，因为当前目标是打通控制台与设备绑定闭环。后续如果要做量产级防伪，再把 `Activation-Version: 2`、`Serial-Number`、`challenge`、`hmac` 校验纳入服务端。

## 控制台体验

设备区增加“验证码绑定”表单：

```text
绑定码输入框
设备名称输入框
绑定按钮
```

绑定成功后刷新设备列表。手动 `device_id` 绑定保留为开发调试区，不作为主路径强调。

设备状态继续使用当前语义：

- `connected`：WebSocket/语音通道在线。
- `wakeable`：MQTT 门铃在线，可敲门。
- `offline`：已绑定或已知，但当前不可达。

## 测试范围

服务端单元/集成测试：

- 未绑定设备 OTA 响应包含 activation。
- 已绑定设备 OTA 响应不包含 activation。
- 同设备未过期 OTA 重试复用 code。
- 控制台可用有效 code 绑定设备。
- 空 code、错误 code、过期 code、已消费 code 被拒绝。
- 已绑定到其他用户的设备不能被 code 抢绑。
- `/xiaozhi/ota/activate` 在未绑定时返回 `202`，绑定后返回 `200`。
- 手动绑定与现有设备列表、敲门接口不回归。

固件侧验证：

- 收到 `activation.code` 后播放 `activation.ogg` 和数字 `.ogg`。
- 绑定完成后轮询 `/activate` 可退出激活。
- 激活完成后 doorbell MQTT 自动启动。
- 不按 BOOT，上电后设备可进入 `wakeable`。

## 实施顺序

1. 服务端新增 activation store 和测试。
2. OTA handler 接入未绑定设备 activation 返回。
3. 增加 `/xiaozhi/ota/activate` 处理。
4. 控制台 handler 增加 activation-bind API。
5. 控制台页面增加验证码绑定表单。
6. 回归现有 `tests/xiaoxin`。
7. 固件端联调 OTA activation 播报和 MQTT 常驻。

