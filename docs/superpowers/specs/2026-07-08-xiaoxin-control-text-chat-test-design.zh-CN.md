# 小芯控制台文本测试对话设计

## 背景

`/xiaoxin/control/` 目前已有设备绑定、通知/课程/待办事件下发、演示数据同步、说话人和记忆主体管理等能力。现有事件下发链路适合“让设备收到一条通知并可选播报指定文本”，但不适合测试“小芯把一段用户输入交给 LLM，再用板子语音回答”的真实对话链路。

本设计新增一个控制台板块，用于在设备在线且处于聆听/联调状态时，从浏览器直接输入文本，并把这段文本注入当前设备连接的真实聊天流程。目标是方便调试 ASR 之后的文本处理、Xiaoxin runtime、LLM、记忆、边界回复和 TTS 播放，而不是把控制台扩展成完整聊天客户端。

## 目标

- 在 `/xiaoxin/control/` 增加“文本测试对话”板块。
- 用户选择自己已绑定且当前在线的设备，输入一段文本并发送。
- 服务端把文本视为该设备本轮用户输入，调用当前连接上的真实聊天入口。
- 设备通过现有 TTS 链路播放 LLM 生成的回答。
- 支持设备正在聆听时测试；发送文本前应清理当前音频输入态，避免控制台文本和麦克风 ASR 结果混入同一轮。
- 第一版只面向在线联调设备，不处理离线唤醒、补发、排队或跨设备聊天。

## 非目标

- 不新增完整聊天记录页。
- 不把控制台做成 IM 或小程序聊天入口。
- 不复用 `/api/xiaoxin/events` 通知投递状态机。
- 不处理设备离线、睡眠唤醒、MQTT doorbell 或稍后补发。
- 不新增独立 LLM 调用路径；必须复用设备连接已有的会话、runtime、TTS 和对话历史。
- 不在第一版提供语音文件上传或浏览器录音。

## 用户体验

控制台应用壳内新增一个独立 section：

- 标题：`文本测试对话`
- 设备选择：复用现有设备列表中的已绑定设备，只允许选择 `connected` 设备。
- 文本输入：多行 textarea，适合输入一两句话。
- 发送按钮：点击后提交文本。
- 状态提示：显示 `已提交到设备`、`请选择在线设备`、`文本不能为空`、`设备未在线`、`发送失败` 等结果。

该板块应保持调试工具属性，不展示大量解释性文案，不增加聊天气泡、不长期轮询聊天记录。发送成功只说明请求已进入设备连接的聊天流程；实际回答由设备语音播放。

## 后端 API

新增接口：

```text
POST /api/xiaoxin/devices/{device_id}/text-chat
```

请求体：

```json
{
  "text": "你现在能听到我吗？"
}
```

成功响应：

```json
{
  "success": true,
  "message": "submitted"
}
```

错误响应沿用控制台 JSON 风格：

```json
{
  "success": false,
  "message": "device not connected"
}
```

接口约束：

- 必须登录。
- `device_id` 必须属于当前登录用户的已绑定设备。
- `text` 去除首尾空白后不能为空。
- `text` 第一版最大长度为 500 字符。
- 目标设备必须在 `runtime.registry` 中为 `connected`，且能取到 WebSocket connection。
- 该接口不创建 `XiaoxinDeliveryRecord`，不出现在投递记录列表中。

## 服务端流程

1. `XiaoxinControlHandler.add_routes` 注册新的 `POST /api/xiaoxin/devices/{device_id}/text-chat`。
2. handler 读取 JSON，请求无效时返回 400。
3. handler 使用现有 `_deny_if_unauthorized` 和 `_deny_if_device_not_owned` 做账号与设备归属校验。
4. handler 从 `runtime.registry.get_connection(device_id)` 获取当前连接。
5. 没有连接时返回 `409 Conflict`，消息为 `device not connected`。
6. handler 调用连接上的文本注入方法，例如 `await conn.submit_control_text_chat(text)`。
7. 连接方法负责先清理正在聆听中的音频输入状态，再把文本交给现有 `chat(text)` 流程。
8. handler 不等待整段 LLM/TTS 播放完成，只在成功提交后返回。

## 连接层行为

在 `ConnectionHandler` 上新增一个小的控制台文本注入入口，职责应尽量薄：

- 确认 TTS、LLM、runtime 初始化由现有连接生命周期负责，不在这里新建独立模型实例。
- 如果当前正在聆听或已有 ASR 缓冲，调用既有音频状态清理逻辑。
- 如果当前正在播放，沿用现有中断/开始新一轮的行为，避免两轮 TTS 并发。
- 将文本送入 `chat(text)`，让它自然执行：
  - `dialogue.put(user)`
  - XiaoxinRuntime 处理
  - LLM fallback
  - 工具调用路径
  - TTS FIRST/MIDDLE/LAST 队列
  - assistant 回复写入对话历史

第一版不新增“禁用记忆”的特殊开关。原因是本功能用于测试真实文本回合，应该与 ASR 识别出的文本尽量一致。长期记忆是否写入，沿用当前 `chat(text)` 对语音文本的行为。

## 并发与聆听中处理

这是第一版最容易出错的地方，必须明确：

- 控制台文本发送代表人为触发的一轮新输入。
- 发送前清理当前 ASR 音频缓存，避免稍后 `listen stop` 又把旧音频识别成另一轮。
- 如果设备同时又通过麦克风送来语音，服务端只保证本次提交时清理已有状态，不做复杂队列仲裁。
- 若后续发现真实设备会产生“文本提交后又立刻 ASR 一轮”的问题，再增加更严格的短时间输入抑制窗口。

## 前端改动

`main/xiaozhi-server/core/api/static/xiaoxin_control.html` 增加：

- 新的 section，位置建议放在设备板块和事件下发之间。
- textarea：`#textChatInput`
- button：`#sendTextChatBtn`
- 发送函数：读取 `deviceSelect` 或独立设备选择值，POST 到新接口。
- 成功后清空输入或保留输入均可。第一版建议保留输入，方便重复调试同一句话。
- 刷新设备列表时，禁用非在线设备选项或在提交时拦截。

由于现有静态页面是单文件控制台，第一版继续保持单文件实现，不引入构建链路。

## 测试策略

新增或扩展 Python 测试：

- handler 路由注册包含 `POST /api/xiaoxin/devices/{device_id}/text-chat`。
- 未登录访问返回 401。
- 当前用户不能向其他用户设备发送文本。
- 空文本返回 400。
- 超过 500 字符返回 400。
- 设备未连接返回错误，不调用 connection。
- 在线设备调用 fake connection 的文本注入方法。
- connection 文本注入会清理音频状态并调用 `chat(text)`。

前端静态测试可扩展现有 control console static 测试，检查页面包含文本测试对话表单、输入框和接口路径。

## 验收标准

- 登录控制台后可以看到“文本测试对话”板块。
- 选择在线已绑定设备，输入 `你现在能听到我吗？` 并发送，板子用语音回答。
- 设备正在聆听时发送文本，不应把之前缓存的麦克风音频再混成同一轮回答。
- 未绑定设备、其他用户设备、离线设备均不能被该接口控制。
- 不新增投递记录。
- 现有通知/课程/待办下发能力不受影响。

## 风险

- `chat(text)` 是同步方法，handler 若直接等待可能阻塞到 LLM 流程结束。实现时应考虑用事件循环安全的提交方式，避免 HTTP 请求长时间挂起。
- 当前页面存在历史编码异常文本，新增中文文案应使用 UTF-8 保存，避免扩大乱码范围。
- 真实设备在“聆听中”收到服务端 TTS start 后，固件侧是否自动停止录音取决于设备实现；服务端清理缓存只能解决服务端已收到的音频，不保证端侧完全不再上传旧帧。
