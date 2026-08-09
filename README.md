# 小芯 ESP32 Server

小芯是在 `xiaozhi-esp32-server` 基础上做的二次开发项目。当前版本的重点不是复刻上游 README 里的完整生态展示，而是把 ESP32 语音 AI 后端改造成“小芯电子陪伴宠物”的服务端底座。

本仓库当前负责：

- ESP32 WebSocket 语音链路。
- OTA HTTP 接口。
- ASR、LLM、VLLM、TTS、VAD、Intent、Memory 等模型编排。
- Xiaoxin console, device binding, notification delivery and runtime status management.
- 小芯私有 `/xiaoxin` 运行路径。
- `8003/xiaoxin/control/` 小芯控制台。
- 主动事件投递、设备 ACK、门铃 MQTT 唤醒。
- 学生身份、设备绑定、speaker 解析和 memory subject 隔离。
- Xiaoxin 分层记忆、知识约束、人格提示词和回答边界。

上游小智文档和图片已经归档到 [`docs/upstream-archive/`](docs/upstream-archive/)。根目录 README 只描述本仓库当前版本的事实。

## 当前定位

小芯不是普通聊天机器人，也不是单纯的小智部署教程。

当前产品方向是“电子陪伴宠物”：硬件端常驻在学生身边，负责语音交互、提醒、通知和陪伴反馈；服务端负责模型调用、身份隔离、记忆、投递、控制台和 OTA/WebSocket 运行路径。

第一阶段策略很明确：

- 先保证继承来的小智语音链路稳定运行。
- 再在服务端加小芯自己的薄层能力。
- 暂时不做大规模目录、包名、数据库名重命名。
- 暂时不把移动端作为首跑路径。

因此你仍会在代码中看到 `xiaozhi-server`、`xiaozhi.*`、`xiaozhi_esp32_server` 等继承名称。这些不是当前设备运行路径，不等于项目仍停留在上游版本。

## 当前运行路径

小芯当前使用 `/xiaoxin` 作为主动运行路径。上游 `/xiaozhi` 路径只作为历史兼容或归档资料存在。

直连服务器时：

```text
WebSocket: ws://SERVER_IP:8000/xiaoxin/v1/
OTA:       http://SERVER_IP:8003/xiaoxin/ota/
控制台:    http://SERVER_IP:8003/xiaoxin/control/
```

通过公网域名和 TLS 暴露时：

```text
WebSocket: wss://DOMAIN/xiaoxin/v1/
OTA:       https://DOMAIN/xiaoxin/ota/
控制台:    https://DOMAIN/xiaoxin/control/
```

端口含义：

| 端口 | 模块 | 用途 |
| --- | --- | --- |
| `8000` | `main/xiaozhi-server` | ESP32 WebSocket 服务 |
| `8003` | `main/xiaozhi-server` | OTA、视觉分析、小芯控制台和控制 API |

更详细的路径说明见 [`docs/development/runtime-paths.md`](docs/development/runtime-paths.md) 和 [`docs/development/xiaoxin-ota-websocket-paths.md`](docs/development/xiaoxin-ota-websocket-paths.md)。

## 代码结构

| 路径 | 当前职责 |
| --- | --- |
| `main/xiaozhi-server` | Python 语音运行时；负责 WebSocket、OTA、模型编排、插件调用、Xiaoxin runtime、控制台投递、身份解析和分层记忆 |
| `main/digital-human` | 浏览器音频交互和数字人测试工具；可辅助测试，不是小芯核心运行时 |
| `docs` | 当前小芯文档中心 |
| `docs/upstream-archive` | 上游小智原始文档、README 和图片归档 |

## 当前能力

当前版本已经具备以下小芯相关能力：

- 私有运行路径：OTA 使用 `/xiaoxin/ota/`，WebSocket 使用 `/xiaoxin/v1/`。
- Python HTTP 服务：`8003` 承载 OTA、视觉分析、小芯控制台和 `/api/xiaoxin/...` 控制 API。
- 小芯控制台：可查看设备、创建主动事件、查看投递记录和投递详情。
- 投递状态机：支持 WebSocket 下发、设备 ACK、TTS 完成态和超时失败记录。
- 门铃 MQTT 唤醒：用于睡眠设备的轻量唤醒，通知正文和 TTS 仍走 WebSocket。
- 本地身份中心：控制台账号、session、设备所有权、speaker 和 memory subject 使用 SQLite 管理。
- 身份隔离记忆：运行时将对话解析到稳定 `memory_subject_id`，避免把不同学生记忆写进同一桶。
- Xiaoxin runtime：包含人格、知识库约束、语义路由、边界回复、回答质量保护和分层记忆。
- 分层记忆：覆盖 profile、episodic、companion、growth arc、relationship state 等层。
- 测试覆盖：`main/xiaozhi-server/tests/xiaoxin/` 下有 Xiaoxin runtime、identity、control、memory、delivery、connection 等测试。

仍需谨慎表述的边界：

- OTA 和 WebSocket 路径迁移基本完成，但完整 OTA 发布闭环还需要真实固件发布和真机 OTA 验收记录。
- `8003/xiaoxin/control/` uses independent SQLite identity and session storage; it does not depend on the removed Java manager services.
- 旧 JSON/JSONL 记忆文件不会自动迁移进 SQLite；迁移或清理由后续阶段显式处理。
- 移动端管理入口存在，但当前不是首跑路径。

## 推荐部署

第一阶段推荐使用 Docker 单服务部署。目标服务器建议：

| 项 | 建议 |
| --- | --- |
| CPU | 4 核 |
| 内存 | 8 GB |
| GPU | 不需要 |
| 系统 | Ubuntu Server |
| 模式 | Docker 单服务部署 |

? GPU ???????????WebSocket?OTA??????? VAD?

在服务器执行：

```bash
cd /opt
git clone https://github.com/jiang-source-tech/xiaoxin-esp32-server.git xiaoxin-esp32-server
cd /opt/xiaoxin-esp32-server/main/xiaozhi-server
mkdir -p data
docker compose up -d --build
```

## Xiaoxin Control Console

Start the server and open:

```text
http://SERVER_IP:8003/xiaoxin/control/
```

Console accounts, device bindings, speaker profiles and memory subjects use local SQLite storage in the Python service.


重启服务端容器：

```bash
docker restart xiaozhi-esp32-server
docker logs -f xiaozhi-esp32-server
```

完整部署说明见 [`docs/getting-started/deployment.md`](docs/getting-started/deployment.md)。

## 推荐模型链路

对 4 核 8 GB、无 GPU 服务器，第一阶段推荐云端流式链路：

```yaml
selected_module:
  VAD: SileroVAD
  ASR: AliyunBLStreamASR
  LLM: AliLLM
  VLLM: QwenVLVLLM
  TTS: AliBLTTS
  Memory: nomem
  Intent: function_call
```

理由很直接：

- `AliyunBLStreamASR` 可以降低首字识别等待。
- `qwen-flash` 更适合首跑低延迟验证。
- `AliBLTTS` 流式 TTS 可以更早开始播放。
- `Memory: nomem` 能先建立延迟基线，避免记忆摘要额外调用模型。
- `Intent: function_call` 保留插件、IoT 和工具调用能力。

如果只测基础语音聊天，可以临时设为：

```yaml
selected_module:
  Intent: nointent
```

模型服务商配置见 [`docs/getting-started/model-providers.md`](docs/getting-started/model-providers.md)。

## 首次验证

启动后先检查容器：

```bash
docker ps
docker logs -n 80 xiaozhi-esp32-server
```

期望结果：


检查 OTA：

```powershell
Invoke-WebRequest -Uri http://SERVER_IP:8003/xiaoxin/ota/ -UseBasicParsing
```

期望 OTA 返回的 WebSocket 地址是：

```text
ws://SERVER_IP:8000/xiaoxin/v1/
```

如果走公网 TLS，则应是：

```text
wss://DOMAIN/xiaoxin/v1/
```

首跑检查清单见 [`docs/getting-started/first-run-checklist.md`](docs/getting-started/first-run-checklist.md)。

## 小芯控制台

小芯控制台入口：

```text
http://SERVER_IP:8003/xiaoxin/control/
```

常用配置位于 `main/xiaozhi-server/data/.config.yaml` 或默认 `config.yaml`。`identity_db` 未显式配置时，代码会使用 `data/xiaoxin_control.db` 作为内置默认值。

```yaml
xiaoxin_control:
  enabled: true
  delivery_history_limit: 100
  wake_timeout_seconds: 15
  ack_timeout_seconds: 10
  doorbell_mqtt:
    endpoint: null
    username: null
    password: null
    keepalive_seconds: 240
  identity_db: data/xiaoxin_control.db

tts_ready_ack_timeout_ms: 700
tts_ready_start_retry_delays_ms: [300, 600, 1200]
tts_delivery_retry_delays_ms: [2000, 5000, 15000, 30000]
tts_done_ack_timeout_ms: 10000

xiaoxin_runtime:
  enabled: true
  knowledge_dir: data/xiaoxin_knowledge
  memory_dir: data/xiaoxin_memory
```

可靠通知 TTS 只有在设备声明 ready、done 和 pre-roll 能力后才启用强保证。`tts_done_ack_timeout_ms` 从 `tts:stop` 发出后开始计算，超时只会令当前尝试失败并以新的 `sentence_id` 从完整原文重试；30 秒是投递重试延迟上限，不是尝试次数上限。完整协议与兼容语义见 [`docs/development/xiaoxin-tts-playback-ack.md`](docs/development/xiaoxin-tts-playback-ack.md)。

`8003/xiaoxin/control/` 是公开入口，直接显示注册和登录界面。注册、登录公开；设备、speaker、memory subject、投递和 delivery API 依赖 `xiaoxin_session`，并按当前账号过滤数据。

控制台细节见 [`docs/development/xiaoxin-control-console.md`](docs/development/xiaoxin-control-console.md)。

## 文档入口

当前文档中心：

- [小芯文档中心](docs/README.md)
- [部署说明](docs/getting-started/deployment.md)
- [模型服务商配置](docs/getting-started/model-providers.md)
- [首次运行检查清单](docs/getting-started/first-run-checklist.md)
- [系统架构](docs/development/architecture.md)
- [定制开发说明](docs/development/customization.md)
- [运行路径说明](docs/development/runtime-paths.md)
- [OTA 与 WebSocket 路径闭环](docs/development/xiaoxin-ota-websocket-paths.md)
- [小芯控制台说明](docs/development/xiaoxin-control-console.md)
- [故障排查](docs/operations/troubleshooting.md)
- [备份与升级](docs/operations/backup-and-upgrade.md)
- [需求工作台](docs/requirements/requirements.html)

上游原始文档只作为参考：

- [原始单服务部署文档](docs/upstream-archive/original-docs/Deployment_all.md)
- [原始单服务部署文档](docs/upstream-archive/original-docs/Deployment.md)
- [原始 FAQ](docs/upstream-archive/original-docs/FAQ.md)

## 开发与测试

小芯相关测试集中在：

```text
main/xiaozhi-server/tests/xiaoxin/
```

常用验证命令：

```bash
cd main/xiaozhi-server
python -m pytest tests/xiaoxin
```

路径审计可参考 [`docs/development/runtime-paths.md`](docs/development/runtime-paths.md) 中的 PowerShell 检查脚本，重点确认运行文件里不再使用旧的 `/xiaozhi/ota/` 或 `/xiaozhi/v1/`。

## License

本项目沿用仓库中的 [MIT License](LICENSE)。
