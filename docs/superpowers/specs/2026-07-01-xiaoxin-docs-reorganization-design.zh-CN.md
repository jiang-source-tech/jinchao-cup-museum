# 小芯文档重组设计

## 背景

当前 `docs` 目录主要继承自上游 `xiaozhi-esp32-server`，包含部署、固件、插件、模型接入、多语言 README、图片资产等大量资料。小芯当前目标是先跑通项目，再基于它二次开发为一个新的产品。继续让上游文档直接占据 `docs` 根目录，会让部署、改造、决策记录和产品文档混在一起，影响后续维护。

本次重组的目标是把 `docs` 变成“小芯 / xiaoxin 文档中心”，同时保留上游资料作为参考归档。

## 目标

- 让 `docs/README.md` 成为小芯文档入口。
- 建立面向当前阶段的文档结构：启动部署、二次开发、运维、决策记录。
- 保留原项目文档和图片，但移动到 `docs/upstream-archive/`，降低日常干扰。
- 保留 `docs/superpowers/` 原位，用于记录已经完成和后续产生的设计、计划。
- 让后续二次开发文档可以持续补充，而不是继续堆到上游文档里。

## 非目标

- 不删除上游资料。
- 不重写所有上游教程。
- 不修改运行时代码。
- 不调整 README 顶层品牌文案之外的代码行为。
- 不在本次整理中解决所有部署细节，只建立当前阶段够用的文档骨架和关键内容。

## 目标目录结构

```text
docs/
  README.md
  getting-started/
    deployment.md
    model-providers.md
    first-run-checklist.md
  development/
    architecture.md
    customization.md
    runtime-paths.md
  operations/
    troubleshooting.md
    backup-and-upgrade.md
  decisions/
    README.md
  upstream-archive/
    original-docs/
    original-readme/
    original-images/
    docker/
  superpowers/
```

## 第一批新文档

### `docs/README.md`

小芯文档中心入口。说明当前策略是先跑通默认项目，再逐步改造为新产品。入口链接指向部署、模型组合、首次跑通清单、架构、定制入口和上游归档。

### `docs/getting-started/deployment.md`

面向当前服务器条件：4 核 8G、无 GPU。推荐 Docker 全模块部署，说明端口、服务、`server.secret`、WebSocket/OTA 地址、基础验证命令。

### `docs/getting-started/model-providers.md`

记录当前最快实用模型组合：

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

文档需要解释为什么无 GPU 服务器不优先本地跑大模型、ASR 或 TTS，以及什么时候可以把 `Intent` 改为 `nointent`。

### `docs/getting-started/first-run-checklist.md`

首次跑通清单，覆盖后台注册、模型配置、`server.secret`、`server.websocket`、`server.ota`、ESP32 联调和日志检查。

### `docs/development/architecture.md`

用二次开发视角说明主要模块职责：

- `main/xiaozhi-server`：语音链路、WebSocket、OTA、模型编排、插件调用。
- `main/manager-api`：智控台后端、用户、设备、智能体、模型配置、参数配置。
- `main/manager-web`：智控台前端。
- `main/manager-mobile`：历史移动端管理入口，已从仓库移除；不代表当前微信小程序。
- `main/digital-human`：浏览器测试和数字人相关能力。

### `docs/development/customization.md`

说明后续改造入口：人设、音色、唤醒词、模型组合、插件、品牌名、管理后台文案。强调先跑通默认项目，再逐项修改。

### `docs/development/runtime-paths.md`

记录小芯当前使用 `/xiaoxin` 运行时路径，包括 WebSocket、OTA、manager-api context path、manager-web proxy。说明哪些 `xiaozhi` 引用可以暂时保留，例如包名、目录名、数据库名和上游仓库链接。

### `docs/operations/troubleshooting.md`

先建立排障骨架，覆盖部署启动失败、模型 Key 配置错误、ESP32 连不上、TTS 无声音、端口未开放等问题。内容可以随着联调持续补充。

### `docs/operations/backup-and-upgrade.md`

说明全模块部署时需要备份的内容：MySQL 数据目录、`uploadfile`、`data/.config.yaml`、模型和自定义配置。升级前先备份，不直接覆盖旧配置文件。

### `docs/decisions/README.md`

作为后续产品和技术决策记录入口。初期只说明记录格式和用途，不强制补齐所有 ADR。

## 归档规则

- `docs/*.md` 中的上游文档移动到 `docs/upstream-archive/original-docs/`。
- `docs/readme/` 移动到 `docs/upstream-archive/original-readme/`。
- `docs/images/` 移动到 `docs/upstream-archive/original-images/`。
- `docs/docker/` 移动到 `docs/upstream-archive/docker/`。
- `docs/superpowers/` 保留原位。
- 新建的小芯文档不放入归档目录。

## 链接策略

新文档优先链接小芯文档。需要引用上游教程时，链接到 `docs/upstream-archive/...`。顶层 `README.md` 后续可以逐步改为链接新的 `docs/README.md`，但本次设计不要求立刻重写仓库根 README。

## 验证

实施后需要检查：

- `docs/README.md` 存在，且入口链接可读。
- 新文档目录存在。
- 上游资料已进入 `docs/upstream-archive/`。
- `docs/superpowers/` 仍在原位置。
- `git status` 只显示预期的移动和新增文档。
- 搜索 `docs/Deployment_all.md` 等旧路径时，确认已经迁移到归档路径，必要时由新入口指向归档。

## 风险与缓解

- 风险：移动上游文档会打断旧文档之间的相对链接。
  缓解：归档文档只作为参考，不保证所有内部链接完全有效；新文档中只链接已确认的重要归档入口。

- 风险：一次性重写太多内容会拖慢当前“先跑通”的目标。
  缓解：第一批文档只写当前阶段够用的部署、模型组合、首次跑通和二开入口。

- 风险：未来产品名或路径再变更。
  缓解：在 `runtime-paths.md` 中集中记录 `/xiaoxin` 约定，避免散落修改。
