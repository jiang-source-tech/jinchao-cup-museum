# 服务端技术目录

`main` 目录承载金潮杯博物馆项目的服务端运行代码和浏览器调试工具。

## 当前模块

| 路径 | 当前职责 | 重建处理 |
| --- | --- | --- |
| `xiaozhi-server/core/connection.py` | WebSocket 会话、音频与模型调用协调 | 保留，改为依赖通用对话运行时接口 |
| `xiaozhi-server/core/providers` | ASR、LLM、TTS、VAD、工具适配 | 保留比赛链路所需适配器 |
| `xiaozhi-server/core/museum` | 展品上下文、审核事实、回答和交互审计 | 当前比赛业务主链路 |
| `xiaozhi-server/core/api` | OTA 和设备管理接口 | 只保留当前有效接口与传输兼容路径 |
| `museum-web-test` | 浏览器语音链路调试 | 仅作开发工具，不作为比赛主产品 |

## 目标模块

目标结构和接口见 [`../docs/architecture/business-rebuild.md`](../docs/architecture/business-rebuild.md)。服务端启动路径当前只创建 `MuseumRuntime`；历史 `core/xiaoxin` 业务代码已从仓库移除。
