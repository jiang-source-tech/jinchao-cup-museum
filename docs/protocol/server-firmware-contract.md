# 服务端与固件合同

## 原则

服务端与固件只使用 `/museum/v1/` WebSocket 和 `/museum/ota/` OTA。服务端拒绝其他 WebSocket 路径，也不提供旧项目 OTA 路径、activation 别名或文件名固件下载接口。博物馆业务通过新增消息类型扩展，不复用课程、待办或陪伴年龄字段。

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
    "zone_id": "zone-demo",
    "exhibit_id": "warring-states-crystal-cup",
    "exhibit_name": "战国水晶杯",
    "source": "device_placement"
  },
  "journey": {
    "route_id": "",
    "current_stop": 1,
    "total_stops": 1,
    "next_exhibit_name": ""
  },
  "prompt": {
    "title": "像现代杯子的古代水晶杯",
    "body": "观察杯口、杯壁和圈足，找找它与现代玻璃杯相似的地方。"
  },
  "grounding": {
    "status": "grounded",
    "source_count": 2,
    "content_version": 1
  },
  "navigation": {
    "can_previous": false,
    "can_next": false,
    "can_end": true
  }
}
```

固件必须原子应用整条状态。任一必填字段非法时拒绝整条消息，不保留一半新状态。

`grounding.status` 第一版允许：

- `ready`
- `retrieving`
- `grounded`
- `unsupported`
- `missing_context`

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

服务器部署目录、端口、正式路径和健康检查入口已于 2026 年 8 月 10 日确认。具体生产命令、数据白名单、备份和验收记录统一维护在 `docs/production-deployment-plan.md`，本协议文档不重复保存部署命令。

## Phase C 现场验收状态

当前已有源码检查、自动化测试，以及 2026 年 8 月 10 日生产服务器模拟文本 WebSocket 和数据库审计证据，但尚无可复核的目标设备验收记录。模拟检查证明了文字输入、博物馆业务回答和 TTS 文本状态链路，不证明麦克风、ASR、音频内容、扬声器播放、TTS ACK 或屏幕状态已经在真机贯通。

现场验收至少记录目标设备标识、固件提交、服务端提交、测试时间、操作步骤、ASR 文本、审计记录、TTS ACK、屏幕与扬声器实际表现以及异常日志。只有实际经过并留有证据的链路才可标记为通过。
