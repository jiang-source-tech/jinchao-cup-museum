# 服务端与固件合同

## 原则

现有 `/xiaoxin/v1/` WebSocket、`/xiaoxin/ota/` OTA 和 TTS 音频协议继续作为小芯设备基座。博物馆业务通过新增消息类型扩展，不复用课程、待办或陪伴年龄字段。

所有新增业务消息都包含整数 `version`。固件遇到不支持的版本时保持当前可用界面并记录错误，不能应用半份数据。

## 服务端下发：museum_state

```json
{
  "type": "museum_state",
  "version": 1,
  "request_id": "req-123",
  "session_id": "session-123",
  "context": {
    "museum_id": "museum-demo",
    "zone_id": "zone-ceramics",
    "exhibit_id": "exhibit-001",
    "exhibit_name": "青瓷莲花尊"
  },
  "visitor_mode": "family",
  "journey": {
    "route_id": "family-treasure-01",
    "current_stop": 2,
    "total_stops": 4,
    "next_exhibit_name": "越窑青瓷碗"
  },
  "prompt": {
    "title": "找一找莲花纹",
    "body": "看看器身上下两组花瓣有什么不同"
  },
  "grounding": {
    "status": "grounded",
    "source_count": 2,
    "content_version": 3
  }
}
```

固件必须原子应用整条状态。任一必填字段非法时拒绝整条消息，不保留一半新状态。

## 固件上行：museum_action

```json
{
  "type": "museum_action",
  "version": 1,
  "request_id": "action-123",
  "session_id": "session-123",
  "action": "next_stop",
  "payload": {}
}
```

第一版允许的 `action`：

- `select_exhibit`
- `select_mode`
- `start_route`
- `next_stop`
- `previous_stop`
- `end_session`

任何改变服务端状态的操作都必须由服务端返回提交结果。固件不能因为触摸完成就假定展品切换、路线推进或会话结束已经成功。

## 服务端确认：museum_action_result

```json
{
  "type": "museum_action_result",
  "version": 1,
  "request_id": "action-123",
  "status": "committed",
  "reason": null
}
```

`status` 只能为：

- `committed`
- `rejected`
- `stale_session`
- `invalid_action`
- `temporary_failure`

## 语音状态

现有 `tts`、`stt`、`emotion` 和音频二进制帧保持不变。博物馆运行时只生成这些消息所需的内容，不重新设计可靠 TTS 协议。

推荐的界面状态映射：

| 业务状态 | 设备表现 |
| --- | --- |
| 等待提问 | 当前展品名称和轻量待机表情 |
| 聆听 | 聆听状态，不覆盖当前展品 |
| 检索与生成 | 思考表情和“查阅馆方资料” |
| 有依据播报 | 播报文本、来源数量和观察任务 |
| 知识兜底 | 明确的资料不足状态，不显示错误红屏 |
| 网络或模型失败 | 可重试的系统状态，与知识兜底区分 |

## 固件目标界面

微雪 ESP32-S3-Touch-LCD-1.46 第一版使用三页：

1. **展品页**：展品名称、模式、知识状态和角色表情。
2. **路线页**：当前站点、总站点、观察任务和下一站。
3. **回顾页**：已完成站点、本次探索主题和结束会话操作。

旧天气、课程、待办和陪伴年龄 Overview 不再作为比赛界面数据模型。

## 配置一致性

发布固件前必须统一：

- 实际目标板与默认构建目标；
- OTA 地址与真实比赛服务器；
- WebSocket 路径；
- 协议版本；
- 服务端和固件 Git 提交；
- 真机验收设备标识。

当前服务器部署目录、域名、健康检查入口尚未确认，因此本文不提供生产部署命令。
