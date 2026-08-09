# 小芯开发任务看板设计

## 摘要

在现有“小芯商业产品需求地图”基础上，新增一个可本地浏览的开发任务看板。产品需求地图继续回答“为什么做、做什么、验收什么”；开发任务看板回答“先做什么、改哪里、依赖什么、如何验证”。两者通过任务字段 `source_requirements` 建立可追溯关系。

目标不是替代项目管理工具，而是在仓库内维护一份 Git 可追踪、可审阅、可派发给开发者或 agent 的任务源数据，并通过 HTML 页面渲染成高密度任务看板。

## 目标

- 保留 `docs/requirements/product-requirements.yaml` 作为产品需求源数据。
- 新增 `docs/requirements/development-board.yaml` 作为开发任务源数据。
- 新增 `docs/requirements/development-board.html` 渲染开发任务看板。
- 扩展 `docs/requirements/server.py`，同时服务产品需求地图和开发任务看板。
- 全路线图进入开发看板，但第一版只把“本地测试控制台 MVP”拆到可执行粒度。
- 每张开发任务卡必须能追溯到一个或多个 `XIAOXIN-PROD-xxx` 产品需求。

## 不做

- 不做拖拽式任务状态编辑。
- 不接入数据库、GitHub Issues、飞书或第三方项目管理系统。
- 不自动从产品需求生成任务；第一版任务由 YAML 明确维护。
- 不在本任务中实现 Product API、设备投递、固件协议或小程序功能。
- 不把产品需求和开发任务混入同一个 `requirements` 列表。

## 选定方案

采用“双 YAML 关联模式”：

```text
product-requirements.yaml
  -> 产品模块、用户旅程、API 草案、里程碑、产品需求

development-board.yaml
  -> 开发阶段、看板泳道、工程模块、开发任务
  -> task.source_requirements 引用 XIAOXIN-PROD-xxx

server.py
  -> /product-requirements.html
  -> /product-requirements.json
  -> /product-requirements.yaml
  -> /development-board.html
  -> /development-board.json
  -> /development-board.yaml
```

这种结构保持产品和工程的边界清晰。产品需求可以继续演进，开发任务可以按实现顺序、依赖关系和验证方式独立维护。

## 备选方案

### 单 YAML 合并模式

把产品需求和开发任务都放进 `product-requirements.yaml`。

优点是文件少；缺点是产品字段和工程字段会互相污染，后续会出现一批既不像 PRD、也不像任务卡的条目。该方案不采用。

### 自动生成任务模式

根据产品需求自动生成开发任务。

优点是形式统一；缺点是当前产品地图仍在快速演进，自动生成会制造大量“看起来完整但不可执行”的任务。该方案后续可以作为辅助工具，不作为第一版。

### 双 YAML 关联模式

产品需求和开发任务分开维护，通过显式引用建立追溯关系。

这是第一版采用方案。它牺牲少量维护成本，换来清晰边界、可审阅任务和更可靠的开发派发能力。

## 数据模型

`development-board.yaml` 顶层结构：

```yaml
meta:
taxonomy:
phases:
lanes:
modules:
tasks:
```

### meta

描述看板标题、版本、更新时间、维护者和用途。

### taxonomy

定义稳定枚举：

- `statuses`: backlog, ready, active, blocked, done, deferred
- `priorities`: critical, high, medium, low
- `types`: design, backend, frontend, firmware, protocol, validation, documentation, operation
- `risk_levels`: high, medium, low

### phases

路线图阶段：

- `phase-1-local-console-mvp`: 本地测试控制台 MVP
- `phase-2-product-api-mvp`: 正式 Product API MVP
- `phase-3-mini-program-mvp`: 微信小程序 MVP
- `phase-4-commercial-operations`: 商业化与运营能力

第一版要求 phase 1 拆到可执行粒度；phase 2 到 phase 4 只放粗粒度任务。

### lanes

看板泳道：

- `backlog`: 待拆解
- `ready`: 可执行
- `active`: 进行中
- `blocked`: 阻塞
- `done`: 已完成

### modules

工程模块，不要求与产品模块一一对应。建议第一版包含：

- `requirements-workbench`
- `control-console-ui`
- `control-console-api`
- `device-registry`
- `delivery-store`
- `event-dispatcher`
- `doorbell-wake`
- `tts-delivery`
- `firmware-protocol`
- `end-to-end-validation`
- `product-api`
- `mini-program`
- `commercialization`

### tasks

每张任务卡字段：

```yaml
- id:
  title:
  status:
  lane:
  phase:
  priority:
  type:
  module:
  source_requirements:
  summary:
  details:
  files:
  depends_on:
  blocks:
  acceptance:
  verification:
  risks:
  estimate:
```

字段约束：

- `id` 使用 `XIAOXIN-DEV-001` 递增。
- `source_requirements` 必须引用 `product-requirements.yaml` 中存在的需求 ID。
- `depends_on` 和 `blocks` 必须引用存在的任务 ID。
- `acceptance` 必须是非空列表。
- `verification` 至少包含人工验收步骤；如果有自动化命令，也放入该字段。
- `files` 可以为空，但 phase 1 的可执行任务应尽量列出预期涉及模块或路径。

## 第一版看板范围

第一版看板包含全路线图，但任务粒度不同。

### 阶段 1：本地测试控制台 MVP

拆到可开发粒度，覆盖：

- 需求工作台扩展：新增 development board 数据源和页面。
- 控制台页面四区布局：设备区、事件下发区、快捷模板区、投递记录区。
- 设备在线/可唤醒状态：connected, wakeable, offline。
- 本地 HTTP API：设备列表、事件投递、投递列表、投递详情。
- 普通通知投递。
- 课表提醒模拟。
- 待办提醒模拟。
- MQTT doorbell wake。
- TTS 播报。
- delivery 状态机。
- 固件 `xiaoxin_event` 解析。
- 固件 `xiaoxin_ack` 回传。
- 投递记录详情与失败原因展示。
- 端到端验收。

### 阶段 2：正式 Product API MVP

只放粗粒度任务：

- 用户与设备绑定。
- Product API 资源模型。
- 通知、课表、投递记录持久化。
- 用户数据隔离。
- Product API 与 xiaozhi-server 内部投递接口。

### 阶段 3：微信小程序 MVP

只放粗粒度任务：

- 设备绑定体验。
- 设备总览。
- 课表与提醒管理。
- 通知历史。
- 投递状态反馈。

### 阶段 4：商业化与运营能力

只放粗粒度任务：

- 套餐与额度模型。
- TTS 使用统计。
- 设备生命周期与售后状态。
- 运营模板与提醒内容策略。

## HTML 工作台

`development-board.html` 采用静态 HTML/CSS/JS，复用现有 `product-requirements.html` 的本地工作台风格，但页面语义转为开发看板。

页面结构：

- 顶部概览：总任务、Ready、Active、Blocked、Critical、Phase 1 任务数。
- 筛选栏：phase、lane、module、priority、status、搜索。
- 主看板：按 lane 分列显示任务卡。
- 详情面板：显示任务详情、关联产品需求、依赖、阻塞、涉及文件、验收标准、验证步骤、风险。

不做拖拽编辑。状态由 YAML 修改，刷新页面后生效。

## 服务端校验

扩展 `server.py` 的校验逻辑：

- 校验开发看板 YAML 顶层键。
- 校验 taxonomy 枚举引用。
- 校验 phase、lane、module 是否存在。
- 校验 task ID 唯一。
- 校验 `source_requirements` 引用产品需求 ID。
- 校验 `depends_on` 和 `blocks` 引用任务 ID。
- 校验 `acceptance` 为非空列表。
- 将 `date` 和 `datetime` 转成 JSON 字符串。

当开发看板 YAML 有错误时，`/development-board.json` 返回：

```json
{
  "ok": false,
  "errors": ["..."],
  "data": null
}
```

页面显示错误列表，不静默失败。

## 测试

实现完成后至少验证：

- `python -m py_compile docs/requirements/server.py`
- `python -c "import yaml; yaml.safe_load(open('docs/requirements/development-board.yaml', encoding='utf-8')); print('yaml ok')"`
- 启动 `python docs/requirements/server.py --port 8090`
- 访问 `/development-board.html` 能正常加载。
- 访问 `/development-board.json` 返回 `ok: true`。
- 页面筛选、搜索、任务详情、依赖/关联需求显示正常。
- 故意写入一个错误引用时，JSON 返回校验错误，页面能显示错误。

## 范围核对

这是一个文档与本地可视化工作台改造，适合单次实现。它不会改变服务端运行时、设备协议或固件逻辑。后续真正开发控制台、Product API、小程序时，应从 `development-board.yaml` 中按 phase 和依赖顺序拆出独立实现任务。

## 设计自检

- Placeholder scan: 没有 TBD/TODO 占位。
- Consistency: 双 YAML 关联、路由、页面、校验逻辑保持一致。
- Scope: 只实现开发任务看板，不实现看板内列出的业务功能。
- Ambiguity: 明确第一版全路线图进入看板，但只有 phase 1 拆到可执行粒度。
