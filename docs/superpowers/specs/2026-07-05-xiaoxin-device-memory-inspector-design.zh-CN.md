# Xiaoxin 设备记忆审计面板设计

日期：2026-07-05

## 结论

需要做一个控制台内的 **Device Memory Inspector / 设备记忆审计面板**。

它可以从设备维度进入，但不能把记忆简单表述为“设备里的记忆”。正确模型是：

```text
设备
  -> 绑定账号
  -> 该设备关联的 memory subjects
      -> user_speaker
      -> device_unknown
      -> device_fallback
  -> 每个 subject 下的分层记忆
      -> profile
      -> companion
      -> episodic
      -> growth_arc
```

原因很直接：正常产品流程里首次联网需要强制绑定设备，但记忆归属仍然是 `memory_subject_id`。设备是入口，不是记忆主体。

## 目标

1. 让开发者和管理员能评估当前记忆架构是否真的有效。
2. 快速判断“用户告诉名字后，记忆写进了哪个 subject，重启后是否从同一个 subject 读出”。
3. 发现 unknown speaker、fallback subject、legacy memory 等污染风险。
4. 查看每层记忆的来源、状态、历史和完整性缺口。
5. 为后续 unknown/legacy 人工迁移提供可审计基础。

## 非目标

第一版不做这些事：

- 不做自动合并 unknown/legacy 记忆。
- 不做跨账号查看。
- 不做复杂编辑器。
- 不把 raw source text 注入 LLM prompt。
- 不把设备直接作为个人记忆主体。

## 当前事实

服务端已有这些基础：

- `ConnectionHandler` 已通过 `XiaoxinIdentityResolver` 把对话解析到 `memory_subject_id`。
- 正常管理 API 绑定链路下，未绑定设备会进入 `need_bind`，用户语音会被拦截并播放绑定码提示。
- identity store 已有 devices、speaker_profiles、memory_subjects、subject_aliases。
- 控制台已有 `/api/xiaoxin/memory-subjects`。
- 控制台已有 `/api/xiaoxin/memory-subjects/{subject_id}/memory`，但目前更接近摘要视图。
- profile 记忆已支持来源、状态、历史、解释和字段级遗忘。
- companion 记忆已支持 `source_text`、`status`、`history`、active/forgotten 解释。
- episodic 和 growth_arc 尚未达到同等可审计级别。

## 信息架构

### 设备列表

设备列表是入口。每个设备至少展示：

- `device_id`
- 设备显示名
- 绑定账号状态
- `bind_status`
- 最近连接时间
- 固件版本，如已有
- 在线/离线状态，如已有
- 关联 memory subject 数量
- 风险徽标

风险徽标示例：

- `unbound`：未绑定设备，不应产生用户主体记忆。
- `unknown-heavy`：该设备下 unknown speaker 记忆数量偏多。
- `fallback-present`：存在 device_fallback 记忆，需要排查绑定或 resolver 降级。
- `legacy-present`：存在 legacy scope 记忆，可人工审核。
- `degraded`：某层记忆读取失败。

### Memory Subject 列表

选择设备后展示该设备关联的 memory subjects。

字段：

- `subject_id`
- `kind`
- `display_name`
- `owner_user_id`
- `device_id`
- `speaker_profile_id`
- `merged_into_subject_id`
- active 记忆数量
- forgotten/history 数量
- 最近更新时间
- 完整性状态

`kind` 的产品语义：

- `user_speaker`：已绑定账号下的确定说话人，正常个人记忆主体。
- `device_unknown`：已绑定设备上的未知说话人，允许短期存在，但需要审计。
- `device_fallback`：未绑定或身份链路异常的兜底主体，不能当作正式个人记忆。

### 分层记忆详情

选择 subject 后展示四层 Tabs。

#### Profile

展示硬事实用户画像。

字段：

- `field`
- `value`
- `confidence`
- `source`
- `source_text`
- `status`
- `updated_at`
- history：`superseded` / `forgotten`

第一版必须支持完整展示，因为 profile 已经具备这些数据。

#### Companion

展示软连续性记忆。

字段：

- `id`
- `kind`
- `topic`
- `summary`
- `source_text`
- `importance`
- `strength`
- `status`
- `privacy_level`
- `created_at`
- `last_accessed`
- `mention_count`
- history：至少展示 forgotten 记录和 `forget_source_text`

第一版必须支持完整展示，因为 companion 已经具备这些数据。

#### Episodic

展示会话摘要和 session 摘要。

第一版可以先展示：

- `summary`
- `title`
- `topics`
- `created_at` / session id，如已有
- integrity warning：`missing_forgotten_history`
- integrity warning：`missing_explain_contract`

后续实现 episodic forgotten history 后，再补 source/status/history。

#### Growth Arc

展示成长线摘要。

第一版可以先展示：

- arc summary
- evidence summary
- topic
- privacy level，如已有
- integrity warning：`missing_source_explain_contract`

后续实现 growth_arc 生命周期后，再补 source/status/history。

## API 设计

第一版优先扩展现有接口，而不是新增一组平行 API。

### 设备到 subject 的审计数据

可以新增：

```http
GET /api/xiaoxin/devices/{device_id}/memory-inspector
```

返回：

```json
{
  "device": {
    "device_id": "desk-xiaoxin",
    "display_name": "Desk XiaoXin",
    "bind_status": "bound",
    "owner_user_id": "usr_xxx"
  },
  "subjects": [
    {
      "subject_id": "sub_xxx",
      "kind": "user_speaker",
      "display_name": "江江",
      "counts": {
        "profile": 4,
        "companion": 3,
        "episodes": 2,
        "growth_arcs": 1,
        "history": 2
      },
      "warnings": []
    }
  ],
  "warnings": []
}
```

也可以先在前端组合：

- `GET /api/xiaoxin/devices`
- `GET /api/xiaoxin/memory-subjects`
- `GET /api/xiaoxin/memory-subjects/{subject_id}/memory`

但最终更推荐提供 device inspector endpoint，因为它能在服务端统一处理 owner 校验、subject 过滤、legacy/fallback 风险标记。

### Subject 记忆详情

扩展现有：

```http
GET /api/xiaoxin/memory-subjects/{subject_id}/memory
```

新增结构：

```json
{
  "subject": {
    "subject_id": "sub_xxx",
    "kind": "user_speaker",
    "display_name": "江江",
    "device_id": "desk-xiaoxin"
  },
  "layers": {
    "profile": {
      "status": "ok",
      "facts": [],
      "history": []
    },
    "companion": {
      "status": "ok",
      "entries": [],
      "history": []
    },
    "episodic": {
      "status": "partial",
      "episodes": [],
      "warnings": ["missing_forgotten_history", "missing_explain_contract"]
    },
    "growth_arc": {
      "status": "partial",
      "arcs": [],
      "warnings": ["missing_source_explain_contract"]
    }
  },
  "summary": {
    "memory_count": 0,
    "warnings": []
  }
}
```

兼容性要求：

- 保留当前摘要字段，避免破坏已有控制台调用。
- 新字段放在 `subject`、`layers`、`summary` 下。
- 所有返回都必须先通过 owner 校验。

## UI 设计

页面位置：

```text
/xiaoxin/control/
  -> Memory
      -> Device Memory Inspector
```

布局：

```text
┌──────────────────────┬────────────────────────┬────────────────────────────┐
│ Devices              │ Memory Subjects         │ Layer Detail                │
│                      │                        │ Profile | Companion | ...   │
│ Desk XiaoXin         │ user_speaker 江江       │ field/value/source/history  │
│ bound / online       │ device_unknown          │                            │
│ 3 subjects           │ device_fallback warning │                            │
└──────────────────────┴────────────────────────┴────────────────────────────┘
```

交互：

- 点击设备，刷新 subject 列表。
- 点击 subject，刷新分层详情。
- Tabs 切换 profile/companion/episodic/growth_arc。
- 每条记忆可展开 source/history。
- 第一版只读。
- 保留已有“清空 subject 记忆”和“按关键词遗忘”操作，但放在危险区。

视觉原则：

- 这是审计工具，不是营销页面。
- 使用密集、安静、可扫描的信息布局。
- 用状态徽标和表格，不做大卡片堆叠。
- 优先显示异常：unknown、fallback、legacy、degraded、missing explain。

## 安全与权限

必须满足：

- 未登录不能访问。
- 只能查看当前账号拥有或绑定设备关联的 subject。
- 不能跨账号列出 memory subject。
- source_text 只展示给当前账号/管理员控制台，不进入 LLM prompt。
- legacy memory 默认只读，不自动迁移。
- unknown/fallback 迁移必须人工确认。

## 验收标准

1. 已绑定设备能看到其关联的 `user_speaker` 和 `device_unknown` subjects。
2. 未绑定或异常兜底 subject 会被明确标为 `device_fallback` 风险。
3. profile 层能展示 active facts 和 history。
4. companion 层能展示 active entries 和 forgotten history。
5. episodic/growth_arc 在未补齐前显示 integrity warning，而不是假装完整。
6. 用户告诉“你可以叫我江江”后，可以从面板看到该事实写入哪个 subject、来源文本是什么、状态是否 active。
7. 用户执行“忘掉我的名字”后，可以从面板看到 active fact 消失，并在 history 中出现 forgotten 记录。
8. 任何账号都不能查看其他账号绑定设备或 subject 的记忆。

## 推荐实现顺序

1. 后端先扩展 subject memory detail，返回 `layers.profile` 和 `layers.companion` 的审计结构。
2. 再增加 device inspector 聚合接口，按设备列出关联 subjects、counts 和 warnings。
3. 控制台新增只读三栏审计视图。
4. 补控制台 handler 测试，验证 owner 隔离、profile/companion source/history 输出。
5. 补 Playwright 或浏览器验收，覆盖设备选择、subject 选择、layer tab 展示。
6. 后续再做 episodic/growth_arc 的完整生命周期升级。

## 关键判断

“首次联网强制绑定”让设备维度成为合理入口，但不改变记忆归属模型。

因此页面应该让用户从设备进入，但必须在 UI 上明确展开 `memory_subject_id`。这能同时满足直觉上的设备排查和架构上的记忆隔离。
