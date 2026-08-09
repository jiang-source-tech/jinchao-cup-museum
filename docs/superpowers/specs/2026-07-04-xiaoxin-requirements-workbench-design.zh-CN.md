# Xiaoxin 项目状态工作台设计

日期：2026-07-04

## 背景

当前 Xiaoxin 项目横跨两个仓库：

- `D:\AI_Pet\xiaoxin-esp32-server`：服务端、控制台、语音链路、记忆、设备投递、OTA/WebSocket 配置。
- `D:\AI_Pet\hzcu_xiaoxin_firmwire_private`：ESP32-S3 固件、宠物 UI、通知中心、Overview、设置页、低功耗、电池与设备状态。

项目已经不是单纯的小智后端部署，也不是单纯的固件改屏幕 UI，而是一个“小新 / 小芯 AI Pet”产品闭环。现有信息分散在 README、开发文档、superpowers specs/plans、固件路线图、测试文件和代码模块里，继续依赖散落文档会带来两个问题：

- 已实现、待实现、可优化、风险和验收证据没有统一入口。
- 服务端与固件的边界容易被误读，后续任务会重复规划或漏掉关键链路。

用户指定第一版主入口放在：

```text
D:\AI_Pet\xiaoxin-esp32-server\docs\requirements\
```

参考形态为：

```text
D:\AI_Pet\ai-pet-chat-debugger\docs\requirements
```

即用 YAML 作为单一事实源，再用 HTML 本地渲染成可筛选、可阅读、可维护的可视化界面。

## 目标

建立 Xiaoxin 项目的总状态工作台，第一版需要同时服务“产品判断”和“后续开发执行”：

- 记录已经实现的能力。
- 记录未来还需要做的能力。
- 记录可以优化但不一定立即做的项。
- 记录风险、阻塞、关键设计决策和验收证据。
- 把服务端、固件、产品路线放在同一张图里，而不是拆成两份互相漂移的文档。
- 通过本地 HTML 页面让用户快速筛选模块、优先级、状态和工作类型。

## 非目标

第一版不做以下事情：

- 不引入数据库或外部服务。
- 不做在线协作、账号、权限或评论系统。
- 不自动扫描代码生成状态，内容由 YAML 手工维护。
- 不把固件仓库已有 `xiaoxin-feature-map.yaml/html` 原样迁移成主入口。
- 不拆成服务端工作台和固件工作台两份文档。
- 不追求花哨动画，优先保证信息密度、可读性和可持续维护。

## 方案选择

采用“服务端仓库中的总状态工作台”。

```text
docs/requirements/
  requirements.yaml
  requirements.html
  server.py
```

理由：

- 服务端仓库是当前对话和后续控制台、身份、记忆、投递链路的主工作区。
- Xiaoxin 的真实产品边界跨服务端与固件，单放固件仓库会把身份、记忆、控制台和服务端数据接入降级成附录。
- 第一版只维护一份总账，避免两边文档不同步。

## YAML 结构

`requirements.yaml` 是唯一事实源。顶层结构为：

```yaml
meta:
taxonomy:
repositories:
modules:
milestones:
items:
risks:
decisions:
```

### meta

记录文档元信息：

- `title`
- `display_title`
- `source_file`
- `version`
- `updated`
- `owner`
- `description`

### taxonomy

定义枚举，供 HTML 筛选和校验使用：

- `statuses`
  - `done`：已完成
  - `partial`：部分完成
  - `active`：进行中
  - `todo`：待实现
  - `optimize`：可优化
  - `blocked`：阻塞
  - `deferred`：暂缓
- `areas`
  - `server`
  - `firmware`
  - `product`
  - `ops`
  - `docs`
- `kinds`
  - `feature`
  - `architecture`
  - `data`
  - `ui`
  - `runtime`
  - `test`
  - `documentation`
  - `risk`
- `priorities`
  - `P0`
  - `P1`
  - `P2`
  - `P3`

### repositories

记录跨仓库来源，至少包含：

- `xiaoxin-server`
- `xiaoxin-firmware`
- `ai-pet-chat-debugger-reference`

每个仓库包含：

- `id`
- `name`
- `path`
- `role`
- `notes`

### modules

记录项目模块导航。第一版模块建议包括：

- `voice-runtime`：服务端语音链路。
- `ota-websocket-paths`：私有 OTA/WebSocket 路径。
- `xiaoxin-runtime-memory`：Xiaoxin runtime 与分层记忆。
- `identity-control-console`：8003 控制台、登录、设备绑定、说话人、记忆主体。
- `delivery-wakeup`：事件投递、ACK、MQTT 唤醒。
- `pet-home`：固件宠物主页。
- `notification-center`：通知中心与 heads-up。
- `overview`：天气、课程、待办、设备状态总览。
- `local-settings`：本机设置页。
- `low-power-health`：低功耗、电池、运行健康。
- `server-data-sync`：服务端数据接入与演示控制。
- `product-risks`：产品化风险与优化。

### milestones

记录阶段路线：

- `M1`：语音与设备连接底座。
- `M2`：固件 UI 骨架与本地状态。
- `M3`：真实通知与总览数据闭环。
- `M4`：服务端控制台、投递、演示控制。
- `M5`：身份、声纹说话人、记忆隔离。
- `M6`：产品化稳定性、低功耗、长期运行。

### items

每条状态项是页面最核心的数据单元。字段为：

```yaml
- id: XIAOXIN-001
  title: 真实通知中心
  area: firmware
  module: notification-center
  milestone: M3
  status: partial
  priority: P0
  kind: feature
  summary: ...
  implemented:
    - ...
  remaining:
    - ...
  optimizations:
    - ...
  acceptance:
    - ...
  evidence:
    - type: test
      path: ...
      note: ...
  related:
    - type: doc
      path: ...
      note: ...
  confidence: high
```

字段语义：

- `implemented`：已经实现且可以被文档、测试或代码佐证的内容。
- `remaining`：为了达到目标仍需要做的内容。
- `optimizations`：不阻塞第一版闭环，但值得记录的改进项。
- `acceptance`：未来判断完成时的验收标准。
- `evidence`：测试、文档、代码路径、手工验证说明。
- `related`：关联文档、计划、源码模块或外部参考。
- `confidence`：对当前状态判断的置信度，取 `high`、`medium`、`low`、`unknown`。

### risks

记录跨模块风险。字段为：

- `id`
- `title`
- `severity`
- `summary`
- `impact`
- `mitigation`
- `related_items`

### decisions

记录已确认的关键取舍。字段为：

- `id`
- `title`
- `date`
- `decision`
- `rationale`
- `tradeoffs`
- `related_items`

## HTML 界面设计

页面工作方式与 `ai-pet-chat-debugger` 参考实现保持一致：

- 浏览器打开 `requirements.html`。
- 页面请求 `/requirements.json`。
- `server.py` 读取并校验 `requirements.yaml`，返回 JSON。
- 用户编辑 YAML 后刷新页面即可看到更新。

### 首屏结构

第一版使用“项目雷达工作台”结构：

```text
┌──────────────────────────────────────────────────────────────┐
│ Xiaoxin 项目状态工作台 | updated/version/source               │
├──────────────┬───────────────────────────────┬───────────────┤
│ 模块导航      │ 状态矩阵 / 条目列表             │ 条目详情        │
│ server        │ P0/P1/P2 + done/partial/todo  │ 已实现          │
│ firmware      │ 搜索与筛选                     │ 还需要做        │
│ product       │                               │ 可优化          │
│ ops/docs      │                               │ 证据/关联       │
├──────────────┴───────────────────────────────┴───────────────┤
│ 里程碑路线：M1 -> M2 -> M3 -> M4 -> M5 -> M6                  │
└──────────────────────────────────────────────────────────────┘
```

### 顶部统计

显示：

- 总条目数。
- 已完成数。
- 部分完成数。
- P0 未完成数。
- 风险数。
- 最近更新时间。

### 筛选能力

支持：

- 全文搜索。
- area 筛选。
- module 筛选。
- status 筛选。
- priority 筛选。
- kind 筛选。
- milestone 筛选。

### 详情面板

选中条目后显示：

- 标题、状态、优先级、模块。
- summary。
- 已实现。
- 还需要做。
- 可优化。
- 验收标准。
- 证据。
- 关联文档。

### 视觉方向

这是一个工程状态工作台，不做营销页。视觉应安静、密集、可扫描：

- 布局：三栏应用界面。
- 色彩：浅色背景，墨色正文，青色表示设备/连接，绿色表示完成，琥珀表示部分完成，红色表示风险。
- 字体：系统中文字体，保证 Windows 本地稳定。
- 交互：表格 hover、选中高亮、筛选即时更新。
- 响应式：窄屏改为上下布局，详情面板在列表下方。

避免：

- 大 hero。
- 装饰性渐变。
- 夸张卡片堆叠。
- 与信息无关的动画。

## server.py 设计

`server.py` 负责：

- 读取 `requirements.yaml`。
- 使用 PyYAML 解析。
- 校验必需顶层字段。
- 校验 taxonomy 引用。
- 校验 module、milestone、repository 引用。
- 校验 item id 唯一。
- 将日期转换为 JSON 可序列化格式。
- 暴露：
  - `/`
  - `/requirements.html`
  - `/requirements.json`
  - `/requirements.yaml`

启动命令：

```powershell
Set-Location D:\AI_Pet\xiaoxin-esp32-server\docs\requirements
python server.py --port 8080
```

浏览器访问：

```text
http://127.0.0.1:8080/
```

## 首版内容范围

第一版 YAML 至少沉淀以下条目：

- 服务端语音链路。
- OTA/WebSocket 私有路径。
- Xiaoxin runtime 与分层记忆。
- 8003 Xiaoxin 控制台。
- 身份与 `memory_subject_id`。
- 事件投递、ACK、MQTT 唤醒。
- 固件宠物主页。
- 通知中心。
- Overview 总览页。
- 设置页。
- 低功耗、电池、运行健康。
- 服务端数据接入：天气、课程、待办、提醒。
- 产品化风险与优化项。

每个条目都要尽量包含现有证据，例如：

- 服务端文档路径。
- 固件文档路径。
- 测试文件路径。
- 关键源码路径。
- 已知限制。

## 验收标准

第一版完成时应满足：

- `docs/requirements/requirements.yaml` 存在，并包含上述顶层结构。
- `docs/requirements/requirements.html` 可以在本地浏览器渲染 YAML 内容。
- `docs/requirements/server.py` 可以启动本地 HTTP 服务。
- `/requirements.json` 返回校验后的 JSON。
- HTML 页面可以搜索、筛选、查看条目详情。
- YAML 至少覆盖 12 个核心项目条目。
- 页面能清楚区分“已实现”、“还需要做”、“可优化”。
- 页面能显示跨仓库证据路径。
- 不需要网络即可使用。

## 后续演进

第一版稳定后再考虑：

- 从固件仓库 `docs/visualization/xiaoxin-feature-map.yaml` 导入或同步部分字段。
- 给每条 item 增加 owner、target_date、last_reviewed。
- 增加导出 Markdown 或 CSV。
- 增加 Mermaid 依赖图。
- 增加“本周建议做什么”的自动排序视图。
- 拆分为 `server.yaml`、`firmware.yaml` 后再由总工作台聚合，但只有当单文件维护变重时才做。

## 自查

- 没有未定义的临时项。
- 第一版聚焦一个总工作台，不拆多个文档入口。
- 数据源明确为 YAML，HTML 只负责渲染。
- 服务端和固件边界都纳入首版内容范围。
- 验收标准能通过文件存在、服务启动、页面渲染和内容覆盖来检查。
