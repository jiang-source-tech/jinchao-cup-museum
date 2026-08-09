# 服务端技术目录

`main` 目录承载小芯服务端运行代码和浏览器调试工具。

## 当前模块

| 路径 | 当前职责 | 重建处理 |
| --- | --- | --- |
| `xiaozhi-server/core/connection.py` | WebSocket 会话、音频与模型调用协调 | 保留，改为依赖通用对话运行时接口 |
| `xiaozhi-server/core/providers` | ASR、LLM、TTS、VAD、Memory、工具适配 | 保留需要的适配器，比赛配置关闭旧 Memory 业务 |
| `xiaozhi-server/core/xiaoxin` | 旧学生陪伴、身份、记忆、提醒和控制业务 | 标记为遗留，逐步从运行路径移除 |
| `xiaozhi-server/core/api/xiaoxin_control_handler.py` | 旧控制台和小程序接口 | 不继续扩展；由新的博物馆管理接口替代 |
| `digital-human` | 浏览器语音链路调试 | 保留为开发工具，不作为比赛主产品 |

## 目标模块

目标结构和接口见 [`../docs/architecture/business-rebuild.md`](../docs/architecture/business-rebuild.md)。在新业务链路验收完成前，不删除仍被生产路径调用的遗留代码。
