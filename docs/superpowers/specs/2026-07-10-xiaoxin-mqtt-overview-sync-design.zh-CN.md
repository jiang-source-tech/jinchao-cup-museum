# 小芯 MQTT Overview 真实数据同步设计

## 目标

把学生账号下的真实课程、待办事项和当天城市天气同步到绑定硬件的 Overview 总览页。

Overview 数据使用常驻、低功耗的设备 MQTT 连接传输，不依赖语音 WebSocket。设备在线时立即收到更新；设备断开后，Broker 保留每台设备最新一份 Overview 快照，设备重连即可自动恢复。

目标闭环：

```text
小程序修改课程或待办
  -> 服务端保存学生数据
  -> 生成绑定设备的 Overview 快照
  -> MQTT QoS 1 retained 发布
  -> 固件接收 xiaoxin_overview_update
  -> 硬件 Overview 刷新课程、待办和天气
```

天气闭环：

```text
设备通过 Wi-Fi 联网
  -> 服务端从设备 HTTP 请求获得公网出口 IP
  -> IP 定位推断省、市
  -> 查询该城市当天预报
  -> 合并到 Overview 快照
  -> MQTT retained 发布到硬件
```

## 背景

当前系统已经具备以下分散能力：

- 小程序首页能读取服务端真实设备、课表、待办和通知摘要。
- 服务端能为学生账号生成课程与待办 Overview 摘要。
- 服务端已有通过 WebSocket 手动发送 `xiaoxin_overview_update` 的调试接口。
- 固件已经能解析 `xiaoxin_overview_update` 并调用 `UpdateOverviewData()` 刷新 Overview。
- 固件已有独立于语音协议的常驻 `DoorbellMqtt` 连接，用于低功耗待机期间接收唤醒消息。
- 既有门铃 MQTT 已经定义无租户的设备级 status/notification 主题、设备级凭证、OTA 配置和 Broker ACL。

当前缺口不是 Overview UI，也不是课程和待办数据源，而是生产环境没有把真实学生数据自动、可靠地投递到硬件。硬件因此主要显示本地时间、Wi-Fi、电量和远程数据空态。

## 核心决策

### 传输职责

```text
WebSocket：语音、对话、音频、TTS、实时通知 ACK
MQTT：设备在线状态、唤醒命令、Overview 状态快照
```

Overview 不再依赖 WebSocket 在线，也不会为了刷新页面而打开语音链路。

### Overview 远程字段

MQTT 同步：

- 当天天气摘要；
- 今日课程或下一节课摘要；
- 未完成待办数量与最近待办摘要；
- 绑定状态、版本、revision 和生成时间。

硬件本地生成：

- 时间；
- 日期和星期；
- Wi-Fi 状态；
- 电量状态。

通知中心继续使用独立通知事件，不塞进 Overview 快照。Overview 更新不播放 TTS，不弹通知卡片。

### 天气语义

天气只显示“当天预报”，不声称是实时天气。

第一版天气字段：

- 省份；
- 城市；
- 当天主要天气；
- 当天最低温；
- 当天最高温；
- 预报日期和获取时间。

不包含：

- 实时温度；
- 体感温度；
- 风速；
- 逐小时预报；
- 多日预报；
- 空气质量；
- 降雨雷达。

### 位置语义

天气位置以设备连接 Wi-Fi 后的公网出口 IP 为主要来源，只要求推断省、市，不要求 GPS、区县或街道精度。

局域网地址如 `192.168.x.x`、`10.x.x.x` 不参与定位。服务端只使用可信代理头或 TCP 对端地址得到的公网 IP。

IP 定位可能因校园网统一出口、运营商 CGNAT、VPN 或代理出现城市偏差，因此小程序保留手动城市纠错。手动城市优先于自动 IP 定位，直到用户切回自动模式。

### 天气数据源

服务端直接调用结构化天气 REST API，不引入 MCP 进程或大模型工具调用。

第一版实现 `OpenMeteoWeatherProvider`：

- 城市地理编码使用 Open-Meteo Geocoding API；
- 当天预报使用 Open-Meteo Forecast API；
- provider 接口与业务层隔离，未来可替换为其他供应商；
- 测试不得依赖真实外网。

Open-Meteo 免费端点只适合作为当前非商业试运行数据源。业务接口不得把供应商响应格式直接暴露给 MQTT 或固件。

## 范围

本阶段包含：

- device-scoped Overview MQTT 主题；
- OTA 增加设备 Overview 主题配置；
- Broker ACL 增加 Overview 主题权限；
- 服务端生成、持久化、发布和重试 Overview 快照；
- 课程、学期、待办、绑定、解绑事件触发 Overview 刷新；
- 服务端从设备 OTA 或位置心跳请求记录公网 IP；
- IP 定位到省、市；
- 每城市每天一次天气预报缓存；
- 固件订阅 Overview 主题并刷新现有总览模型；
- 固件处理版本、revision、解绑清空和无效 payload；
- 小程序提供自动 IP 定位状态与手动城市纠错入口；
- 服务端、固件和真机闭环测试。

## 非目标

本阶段不做：

- GPS 或手机持续定位；
- 小程序默认申请 `wx.getLocation`；
- 根据局域网 IP 定位；
- 实时天气或半小时天气轮询；
- 天气图标、小时预报、七日预报、空气质量；
- 通过 MQTT 传输语音、TTS 或通知 ACK；
- 通过 WebSocket 自动同步生产 Overview；
- 多条 Overview 事件历史；
- 为每次中间编辑保留独立 Overview 消息；
- 把 MCP 引入生产天气查询链路。

## 总体架构

### OverviewSyncService

新增独立的 `OverviewSyncService`，作为 Overview 业务入口，避免继续把构建和投递逻辑堆进 HTTP Handler。

职责：

1. 根据绑定设备解析学生 owner。
2. 获取学生课程和待办摘要。
3. 获取设备天气位置和当天缓存。
4. 生成规范化 Overview payload。
5. 比较当前快照和上一个业务快照。
6. 业务内容变化时递增 revision。
7. 持久化最新快照并标记待发布。
8. 调用 MQTT Publisher 尝试发布。

主要接口形状：

```python
class OverviewSyncService:
    async def refresh_device(self, device_id: str, reason: str) -> OverviewSyncResult:
        ...

    async def clear_unbound_device(self, device_id: str, reason: str) -> OverviewSyncResult:
        ...

    async def refresh_user_devices(self, user_id: str, reason: str) -> list[OverviewSyncResult]:
        ...
```

`reason` 用于日志和诊断，例如：

- `device_bound`
- `device_unbound`
- `semester_updated`
- `course_created`
- `course_updated`
- `course_deleted`
- `todo_created`
- `todo_updated`
- `todo_deleted`
- `weather_city_changed`
- `weather_day_changed`
- `manual_resync`

### OverviewSnapshotStore

Overview 是状态，不是事件。每台设备只需要保存最新快照和发布状态。

新增 SQLite 表 `device_overview_snapshots`：

```text
device_id        PRIMARY KEY
owner_user_id    nullable
revision         integer
content_hash
payload_json
publish_state    pending/published
publish_attempts
next_attempt_at  nullable
last_error
generated_at
published_at     nullable
updated_at
```

规则：

- 新快照覆盖同一设备尚未发布的旧快照；
- revision 只在业务内容变化时递增；
- `generated_at` 不参与业务内容 hash，避免单纯刷新时间造成重复发布；
- 服务重启后扫描 `pending` 快照继续发布；
- 成功收到 MQTT PUBACK 后才标记 `published`；
- 不保留过时中间快照历史。

### OverviewMqttPublisher

新增专用 Publisher，复用常驻服务端 MQTT 客户端和现有设备级主题配置。

职责：

- 发布 QoS 1 retained Overview；
- 关联 publish message ID 和快照 revision；
- 收到 PUBACK 后回写 SnapshotStore；
- Broker 断线时停止立即重试，等待重连；
- Broker 重连后排空 pending 快照；
- 对同一设备串行发布，避免新旧 revision 乱序。

课程和待办 CRUD 成功不以 MQTT 成功为前提。业务数据提交后即向小程序返回成功；Overview 发布失败进入可靠重试。

### IpLocationProvider

把现有 IP 查询能力收敛到独立接口：

```python
class IpLocationProvider:
    async def locate(self, public_ip: str) -> IpCityLocation | None:
        ...
```

返回规范化字段：

```text
province
city
country_code
located_at
```

第一版可以复用现有 IP 查询实现，但必须：

- 保留省份与城市，而不是只返回城市字符串；
- 设置超时；
- 缓存相同公网 IP；
- 将供应商失败转换成可诊断状态；
- 在测试中使用 fake provider；
- 不让 IP 定位失败阻断 OTA、连接或课程待办同步。

### WeatherProvider

天气 provider 接口：

```python
class WeatherProvider:
    async def daily(self, province: str, city: str, date_text: str) -> DailyWeather:
        ...
```

规范化返回：

```text
province
city
date
weather_code
weather_text
temperature_min_c
temperature_max_c
fetched_at
```

Overview 不直接接触 Open-Meteo 原始字段。

## 数据模型

### 设备天气位置

新增表 `device_weather_locations`：

```text
device_id          PRIMARY KEY
mode               automatic/manual
public_ip_hmac     nullable
province
city
latitude           nullable
longitude          nullable
located_at
updated_at
```

规则：

- `automatic` 使用设备公网 IP 推断省市；
- `manual` 使用小程序选择的省市；
- manual 模式不被后续 IP 心跳覆盖；
- 切回 automatic 后立即使用最近设备公网 IP 重新定位；
- 不把天气位置当成学生实名资料；
- 不需要把原始公网 IP 长期写入数据库，使用服务端密钥计算 HMAC，用于判断 IP 是否变化；
- 不使用普通 hash，因为 IPv4 地址空间可以被枚举反推；
- 日志不得记录完整公网 IP和学生 openid 的组合。

### 每日城市天气缓存

新增表 `daily_city_weather`：

```text
cache_key          PRIMARY KEY  # country/province/city/date/provider
country_code
province
city
date
weather_code
weather_text
temperature_min_c
temperature_max_c
fetched_at
expires_at
fetch_attempts
next_attempt_at    nullable
last_error         nullable
updated_at
```

同一城市同一天共享一条缓存。设备数量不增加天气供应商请求次数。

## MQTT 主题与权限

沿用现有无租户、设备级 MQTT 命名：

```text
device/{device_id}/status
device/{device_id}/notification
device/{device_id}/overview
```

设备示例：

```text
device/1c:db:d4:48:d1:50/overview
```

Overview 发布参数：

```text
qos = 1
retain = true
```

设备凭证 ACL：

```text
topic write device/{device_id}/status
topic read device/{device_id}/notification
topic read device/{device_id}/overview
```

服务端 ACL：

```text
topic read device/+/status
topic write device/+/notification
topic write device/+/overview
```

设备不得读取其他设备的 Overview，也不得向 Overview 主题发布。服务端在生成快照前必须确认设备当前 owner，不能仅凭 device_id 接受小程序写操作。

## OTA 配置

在现有 `doorbell_mqtt.version = 1` 对象中增加可选字段：

```json
{
  "doorbell_mqtt": {
    "version": 1,
    "enabled": true,
    "endpoint": "SERVER_IP:1883",
    "client_id": "{device_client_id}",
    "username": "{device_username}",
    "password": "{device_secret}",
    "status_topic": "device/{device_id}/status",
    "notification_topic": "device/{device_id}/notification",
    "overview_topic": "device/{device_id}/overview",
    "keepalive_seconds": 240,
    "qos": 1
  }
}
```

兼容规则：

- 旧固件忽略未知 `overview_topic` 字段，继续保留门铃能力；
- 新固件在 `overview_topic` 缺失时只启用 status 与 notification，不订阅 Overview；
- 增加可选字段属于 version 1 的向后兼容扩展，不整体提升配置版本；
- 商业运行仍不得回退到固件硬编码公开 Broker；
- OTA 明确 `enabled: false` 时停止整个常驻 MQTT 连接。

## Overview Payload

规范 payload：

```json
{
  "type": "xiaoxin_overview_update",
  "version": 1,
  "revision": 24,
  "device_id": "1c:db:d4:48:d1:50",
  "bound": true,
  "generated_at": "2026-07-10T15:30:00+08:00",
  "weather": {
    "configured": true,
    "available": true,
    "province": "浙江",
    "city": "杭州",
    "date": "2026-07-10",
    "summary": "杭州 · 多云",
    "detail": "今日 26～35℃",
    "fetched_at": "2026-07-10T06:05:00+08:00"
  },
  "course": {
    "configured": true,
    "available_today": true,
    "title": "体育 15:25",
    "detail": "体育馆"
  },
  "todo": {
    "configured": true,
    "count": 3,
    "detail": "提交实验报告"
  }
}
```

约束：

- payload 使用 UTF-8 JSON；
- 最大 payload 目标不超过 2 KiB；
- `revision` 为设备级单调递增整数；
- `device_id` 必须与订阅主题和本机设备标识一致；
- `weather`、`course`、`todo` 三个对象必须始终存在；
- 文本字段在服务端截断到硬件 UI 允许的长度；
- 不包含 openid、user_id、学号或其他学生身份字段；
- 不包含通知历史列表和语音内容。

## 空态与解绑 Payload

### 未配置天气位置

```json
{
  "configured": false,
  "available": false,
  "province": "",
  "city": "",
  "date": "2026-07-10",
  "summary": "天气位置未知",
  "detail": "可在小程序中设置城市",
  "fetched_at": ""
}
```

### 当天天气查询失败

当天没有可用缓存时：

```json
{
  "configured": true,
  "available": false,
  "province": "浙江",
  "city": "杭州",
  "date": "2026-07-10",
  "summary": "杭州 · 天气暂不可用",
  "detail": "",
  "fetched_at": ""
}
```

### 设备解绑

解绑后必须以更高 revision 发布 `bound=false` 空快照，覆盖 Broker 中旧学生数据：

```json
{
  "type": "xiaoxin_overview_update",
  "version": 1,
  "revision": 25,
  "device_id": "1c:db:d4:48:d1:50",
  "bound": false,
  "generated_at": "2026-07-10T15:40:00+08:00",
  "weather": {
    "configured": false,
    "available": false,
    "province": "",
    "city": "",
    "date": "2026-07-10",
    "summary": "设备未绑定",
    "detail": "绑定后显示天气",
    "fetched_at": ""
  },
  "course": {
    "configured": false,
    "available_today": false,
    "title": "设备未绑定",
    "detail": "绑定后显示课程"
  },
  "todo": {
    "configured": false,
    "count": 0,
    "detail": "绑定后显示待办"
  }
}
```

重新绑定后，再以更高 revision 发布新 owner 的真实数据。

## 同步触发规则

以下写操作成功提交后触发异步 Overview refresh：

- 学期配置更新；
- 课程创建；
- 课程更新；
- 课程删除；
- 待办创建；
- 待办更新；
- 待办完成；
- 待办删除；
- 设备绑定；
- 设备解绑；
- 小程序手动天气城市更新；
- 小程序切回自动 IP 定位。

天气触发：

- 当前城市当天没有天气缓存；
- 日期切换到新的一天；
- 设备公网 IP 变化且推断出的城市变化；
- 用户手动城市变化；
- 服务重启后发现绑定设备缺少当天缓存；
- 当天天气查询失败后的有限重试。

读取接口保持无副作用：

- `GET /api/miniprogram/overview` 不发布 MQTT；
- `GET /api/miniprogram/courses` 不发布 MQTT；
- `GET /api/miniprogram/todos` 不发布 MQTT。

## 公网 IP 获取与天气位置更新

服务端从以下设备 HTTP 请求更新公网 IP：

- OTA 请求；
- 新增的轻量设备位置心跳；
- 其他明确携带设备身份并经过认证的设备 HTTP 请求。

设备位置心跳：

```text
POST /api/xiaoxin/device/location-heartbeat
```

调用时机：

- 设备完成 Wi-Fi 联网；
- Wi-Fi 重新连接或网络发生变化；
- OTA 请求已经完成同一位置更新时，不重复发送心跳。

服务端 IP 解析顺序：

1. 只在请求来自受信任反向代理时读取 `X-Real-IP` 或 `X-Forwarded-For`；
2. 否则使用 TCP 对端地址；
3. 私网、环回、链路本地或无效地址不调用外部定位；
4. 公网 IP HMAC 未变化时不重复定位；
5. 自动模式下城市变化才触发天气和 Overview 更新；
6. manual 模式下只记录诊断，不覆盖手动城市。

MQTT Broker 客户端来源 IP 不作为第一版业务接口，避免依赖 Broker 插件、日志格式或管理 API。

## 天气缓存与每日刷新

正常情况下每个城市每天只请求一次当天预报。

```text
cache key = provider + country + province + city + local_date
```

同一城市的所有设备共享缓存。即使一千台设备在同一城市，也不会产生一千次天气请求。

刷新规则：

- 当天缓存存在且有效：直接复用；
- 新的一天：查询新日期天气；
- 城市变化：立即查询新城市当天预报；
- 服务启动：只为缺少当天缓存的绑定设备城市查询；
- 成功后当天不做固定半小时轮询；
- 供应商响应改变但日期、城市和业务天气内容未变化时不重复发布 Overview。

失败重试：

```text
第一次失败 -> 10 分钟后
第二次失败 -> 30 分钟后
第三次失败 -> 2 小时后
第三次以后 -> 当天不再自动重试，可由手动刷新触发
```

天气失败不阻断课程和待办同步。

## 小程序天气设置

小程序新增最小设置：

```text
天气位置
  ○ 根据设备网络自动定位
  ● 固定城市：浙江 / 杭州
```

小程序展示：

- 当前模式；
- 自动推断的省市或手动省市；
- 最近定位时间；
- 最近天气获取状态；
- 切回自动定位动作；
- 手动选择省市动作。

小程序不默认申请手机位置权限，也不持续定位。

建议 API：

```text
GET   /api/miniprogram/weather-location
PATCH /api/miniprogram/weather-location
```

PATCH 自动模式：

```json
{"mode":"automatic"}
```

PATCH 手动模式：

```json
{"mode":"manual","province":"浙江","city":"杭州"}
```

服务端验证省市并完成地理编码。任意学生只能修改自己绑定设备的天气位置策略。

## 固件行为

### MQTT 连接

固件从 OTA 或最后一次持久化有效配置中读取 `overview_topic`，在门铃 MQTT 连接成功后同时订阅：

```text
notification_topic
overview_topic
```

`status_topic` 继续用于 retained 在线状态和 LWT。

### Topic 分流

MQTT DATA 事件必须保留 topic，不能只把 payload 传给统一 `OnMessage()`。

```text
notification_topic -> 处理 wake 命令
overview_topic     -> 处理 xiaoxin_overview_update
其他 topic         -> 忽略并记录受限日志
```

`wake` 消息保持 non-retained。Overview 保持 retained。两种语义不得混用同一个 topic。

### Payload 校验

固件只接受：

- `type == xiaoxin_overview_update`；
- `version == 1`；
- `device_id` 与本机一致；
- `revision` 大于当前运行期最后接受 revision；
- `weather`、`course`、`todo` 都是对象；
- 文本字段和数值字段类型正确且长度在上限内；
- payload 大小不超过配置的安全上限。

无效 payload 不清空当前页面，不重启设备，不打开 WebSocket。

### 页面刷新

成功接收后：

1. 更新 Overview 内存字段；
2. 更新当前运行期 revision；
3. 当前正在显示 Overview 时立即刷新；
4. 当前不在 Overview 时，下次进入显示最新快照；
5. `bound=false` 时清空旧学生课程、待办和天气；
6. 不播放 TTS；
7. 不弹通知卡片；
8. 不创建语音 WebSocket。

设备重启后运行期 revision 从初始值开始，Broker retained 最新快照会重新送达并被接受。不要求第一版把 Overview revision 写入 NVS。

## 错误处理

### MQTT 发布失败

- 课程、待办或绑定业务请求仍返回其数据库提交结果；
- SnapshotStore 保持 `pending`；
- Publisher 按 `1s -> 2s -> 5s -> 15s -> 30s` 退避；
- Broker 断开时等待重连事件，不进行忙循环；
- 新业务快照覆盖旧 pending 快照；
- 服务重启后继续发送 pending 快照。

### MQTT PUBACK 丢失

- 快照保持 pending；
- 允许 QoS 1 重复投递；
- 固件通过 revision 去重；
- 重复消息不得造成页面闪烁、TTS 或 WebSocket 行为。

### IP 定位失败

- 保留上一次成功省市；
- 从未成功且没有手动城市时显示“天气位置未知”；
- 不影响课程和待办；
- 记录 `ip_location_failed`，不记录完整公网 IP 与学生身份组合。

### 天气查询失败

- 当天已有成功缓存时继续使用；
- 当天没有成功缓存时显示“天气暂不可用”；
- 按有限重试计划执行；
- 不阻断 Overview 中课程与待办更新；
- 不把供应商原始错误下发到硬件。

### Schema 不兼容

- 固件忽略不支持的 `version`；
- 服务端保留 payload version 1 直到新固件覆盖率满足升级条件；
- 新增可选字段时保持旧字段语义不变；
- 不在同一 version 内改变字段类型。

## 安全与隔离

- Overview topic 必须 device-scoped；
- 每台设备只能订阅自己的 Overview；
- 服务端在生成 payload 前确认设备当前 owner；
- unbound 设备不得收到学生课程、待办或天气；
- 解绑必须发布更高 revision 的空 retained 快照；
- payload 不包含账号标识、openid、学号或个人资料；
- MQTT 密码不得写入日志、API、截图或 Overview payload；
- 手动天气城市修改要求有效小程序 session 和设备归属；
- 设备 HTTP 位置心跳必须校验设备身份，不能让任意请求覆盖设备位置；
- 只有受信任代理头可以影响公网 IP解析。

## 可观测性

结构化日志字段：

```text
tag
device_id
revision
reason
publish_state
publish_attempts
weather_city
weather_date
error_code
```

核心错误码：

- `overview_mqtt_disabled`
- `overview_publish_failed`
- `overview_puback_timeout`
- `overview_payload_invalid`
- `overview_revision_stale`
- `overview_device_mismatch`
- `overview_device_unbound`
- `ip_location_failed`
- `weather_geocoding_failed`
- `weather_forecast_failed`
- `weather_cache_missing`

控制台应能看到每台设备：

- 最新 Overview revision；
- pending/published 状态；
- 最近发布时间；
- 最近错误；
- 自动或手动天气城市；
- 当天天气缓存状态；
- 手动重新同步动作。

## 测试策略

### 服务端单元测试

覆盖：

- device-scoped Overview topic 渲染；
- OTA 返回 `overview_topic`；
- Broker ACL 包含设备 read 和服务端 write 权限；
- 课程、待办和天气 payload 规范化；
- 文本截断与 payload 大小限制；
- 相同业务内容不增加 revision；
- 内容变化增加 revision；
- 新快照覆盖旧 pending 快照；
- PUBACK 标记 published；
- Broker 断线和服务重启重试；
- 解绑生成更高 revision 空快照；
- 不同用户和设备数据隔离；
- IP 定位自动模式；
- manual 模式不被 IP 变化覆盖；
- 城市变化触发天气刷新；
- 同城同日共享天气缓存；
- 天气成功、失败和有限重试；
- 天气失败不阻断课程待办 payload；
- GET 接口不产生 MQTT 副作用。

### MQTT 集成测试

使用测试 Broker 验证：

- QoS 1 Overview 发布；
- retained 标志；
- 新订阅者立即收到最新 retained 快照；
- 同一设备最终只保留最新 revision；
- 设备无权订阅其他设备 Overview；
- 服务端无权以设备凭证发布 Overview；
- unbind 空快照覆盖旧学生快照；
- 服务端重连后排空 pending 快照。

### 固件测试

覆盖：

- OTA 解析和持久化 `overview_topic`；
- 旧配置缺少 `overview_topic` 时继续保持门铃功能；
- 同一 MQTT 连接订阅 notification 与 overview；
- MQTT DATA 事件按 topic 分流；
- wake 只走 notification topic；
- Overview 只走 overview topic；
- 有效 payload 更新 Overview model；
- 天气、课程、待办文案正确；
- 旧 revision 被忽略；
- 错误设备、version 和 JSON 被忽略；
- `bound=false` 清空旧数据；
- Overview 更新不打开 WebSocket；
- Overview 更新不播放 TTS；
- retained 重复投递不产生副作用。

### 真机验收

最低闭环：

1. OTA 返回设备级 MQTT 配置和 `overview_topic`。
2. 设备使用设备级凭证连接 Broker。
3. 设备公网 IP被推断为正确省市，或在小程序中手动纠正。
4. 硬件 Overview 显示城市、当天天气和最低/最高温。
5. 小程序新增课程后，MQTT 在线设备在 5 秒内显示课程摘要。
6. 修改课程时间或教室后，硬件自动刷新。
7. 删除当日课程后，硬件显示明确空态。
8. 新增待办后，硬件显示未完成数量和最近事项。
9. 完成或删除待办后，数量和摘要更新。
10. 设备 MQTT 离线期间修改数据，重连后自动收到最新 retained 快照。
11. 连续修改三次，只显示最终 revision。
12. 解绑后硬件清除旧学生天气、课程和待办。
13. 重新绑定后显示新 owner 数据。
14. 日期切换后，每城市只查询一次新一天预报并更新设备。
15. 天气查询失败时课程和待办仍能同步。
16. 整个 Overview 同步过程不建立语音 WebSocket、不播放 TTS。

## 兼容与发布顺序

1. 复用既有无租户 MQTT 配置和设备凭证，为 Broker ACL 增加 Overview 权限。
2. Broker ACL 增加 Overview topic 权限。
3. OTA 增加可选 `overview_topic`。
4. 固件升级为从 OTA 配置订阅 Overview，并完成 topic 分流。
5. 服务端部署 SnapshotStore、OverviewSyncService、Publisher、IP 定位和天气缓存。
6. 在配置中开启 `overview_mqtt.enabled`，开始发布 retained 快照。
7. 对一台测试设备完成课程、待办、天气、离线重连和解绑验收。
8. 扩展到其余设备。
9. 原 WebSocket Overview 接口保留为短期调试入口，不再作为生产自动同步路径。
10. 全部设备迁移完成后删除或明确标记 WebSocket Overview 调试接口。

发布顺序允许服务端提前发布 retained 快照：旧固件没有订阅新主题，不受影响；新固件升级后会立即收到 Broker 保留的最新快照。

## 完成标准

只有同时满足以下条件，才可以把硬件 Overview 真实数据标记为通过：

- 天气、课程、待办均来自服务端事实源；
- Overview 数据只通过 MQTT 生产链路自动同步；
- 设备离线后不会丢失最终状态；
- 解绑会清除旧学生数据；
- 设备不能订阅其他设备数据；
- 天气按省市显示当天预报，不伪装成实时天气；
- 每城市每天正常情况下只查询一次天气；
- 天气失败不影响课程和待办；
- 更新过程不打开语音 WebSocket、不播放 TTS；
- 服务端、MQTT、固件测试和真机验收均留有证据。
