# RAG 实施计划（需求执行版）

## 0. 文档定位

| 项目 | 内容 |
| --- | --- |
| 更新时间 | 2026-08-11 |
| 适用范围 | `REQ-003` 至 `REQ-009` 已有能力之后的 RAG 落地 |
| 直接关联需求 | `REQ-006`、`REQ-008`、`REQ-009`、`REQ-013`、`REQ-014`、`REQ-017`、`REQ-018` |
| 当前结论 | 先把事实级 RAG 做成可导入、可发布、可评测的多展品闭环，再根据评测缺口决定是否引入向量召回 |
| 下一项实施需求 | `RAG-NEXT-07`：真机前置检查与设备状态闭环 |

这不是“未来愿景”或技术名词清单，而是一份可以逐项执行、验收和归档的任务单。每个任务都写明输入、修改边界、验收条件和完成证据。完成状态仍以 `docs/requirements/requirements.yaml` 为准。

当前执行状态：`RAG-NEXT-01` 至 `RAG-NEXT-06` 已于 2026-08-11 实现；下一项是 `RAG-NEXT-07` 真机前置检查与设备状态闭环。

## 1. 先明确当前基线

项目已经有第一阶段的 RAG 骨架，不应从零重写：

- `ExhibitResolver`：显式展品、别名、歧义、未收录和会话继承；
- `MuseumStore`：展品、来源、内容版本、事实、来源绑定和 FTS5；
- `QueryUnderstanding`：粗意图、细意图和事实类型约束；
- `GroundedAnswerService`：依据快照、回答守卫、资料不足兜底；
- `interaction_trace`：请求级解析、意图、依据和守卫审计；
- `museum_state`：服务端与固件状态合同，AC-010-4 已完成协议和构建验证。

`RAG-NEXT-06` 完成后，当前真正剩下的不是“再加一个 RAG 框架”，而是：

1. 在目标硬件上补齐真实麦克风、ASR、TTS、扬声器和屏幕证据；
2. 用连续运行和故障恢复证明比赛现场稳定性；
3. 用真实未命中导出持续校准内容、别名和 ASR 分类；
4. 只有未命中数据证明现有召回不足时，才进入向量召回决策门；
5. 核心真机闭环稳定后，再扩展到 20 至 30 件展品。

## 2. 产品边界与不可违反的约束

### 2.1 目标闭环

```text
游客原话
  -> 展品实体解析
  -> 唯一 exhibit_id 或明确失败状态
  -> 当前展品 + 当前发布版本过滤
  -> 事实类型和关键词/FTS5 召回
  -> EvidenceSnapshot
  -> LLM 表达或确定性回答
  -> AnswerGuard
  -> TTS / museum_state / interaction_trace
```

### 2.2 不做的事情

- 不用设备点位猜测游客正在看的展品；
- 不强制游客按路线、站点或下一展品操作；
- 不让 LLM 自由生成展品 ID；
- 不直接对全馆长文档开放检索；
- 不用草稿、撤回版本或无来源事实回答；
- 不因“项目叫 RAG”就提前建设独立向量数据库；
- 不把测试文本通过当成真实麦克风、扬声器和屏幕验收。

### 2.3 事实边界

最终回答只能引用本轮 `EvidenceSnapshot` 中的事实。模型输出只能改变表达，不得增加快照之外的数字、人物、地点、年代、因果或传说。

## 3. 交付顺序总览

| 顺序 | 任务 | 关联需求 | 依赖 | 交付结果 |
| --- | --- | --- | --- | --- |
| 0 | RAG-NEXT-00 基线冻结 | REQ-006/008/009 | 已完成 | 记录当前查询、回答和审计接口，不反复重写核心链路 |
| 1 | RAG-NEXT-01 内容包契约与 3 至 5 件展品 fixture | REQ-006/017 | 00 | 可版本控制的展品、别名、事实、来源和评测输入 |
| 2 | RAG-NEXT-02 内容导入 CLI | REQ-013 AC-013-1/2 | 01 | 不改 Python 代码即可校验并导入草稿 |
| 3 | RAG-NEXT-03 发布、撤回和回滚 | REQ-013 AC-013-3/4/5 | 02 | 发布版本唯一、事务完整、历史依据可复核 |
| 4 | RAG-NEXT-04 多展品检索闭环（已完成） | REQ-006/008 | 03 | 当前展品和发布版本内的事实级检索稳定工作 |
| 5 | RAG-NEXT-05 自然问法和真实 LLM 评测（已完成） | REQ-007/008/011/017 | 04 | 随机换问法、多轮、切换、诱导编造均有结果记录 |
| 6 | RAG-NEXT-06 未命中回收导出（已完成） | REQ-014 | 05 | 运营可按原因和展品回收问题 |
| 7 | RAG-NEXT-07 真机前置检查 | REQ-010/012/015 | 05 | 服务端状态、固件状态和真实链路验收脚本对齐 |
| 8 | RAG-NEXT-08 向量检索决策门 | REQ-018 | 05/06 | 只有量化缺口成立时才进入 Embedding 方案 |
| 9 | RAG-NEXT-09 扩展到 20 至 30 件展品 | REQ-017 | 03/05 | 规模化内容和评测集，不阻塞 P0 闭环 |

## 4. 任务详细说明

### RAG-NEXT-00：冻结已有接口和基线

**目的**：避免为了引入 RAG 再次改动已经通过的上下文、事实守卫和设备合同。

**输入**：

- `main/xiaozhi-server/core/museum/contracts.py`
- `main/xiaozhi-server/core/museum/exhibit_resolver.py`
- `main/xiaozhi-server/core/museum/store.py`
- `main/xiaozhi-server/core/museum/answering.py`
- `main/xiaozhi-server/core/museum/runtime.py`
- 现有 `tests/test_exhibit_resolver.py`、`test_museum_runtime.py`、`test_answer_guard.py`。

**动作**：

1. 固定 `EvidenceSnapshot` 的字段：`exhibit_id`、`content_revision_id`、`content_version`、`fact_ids`、`source_ids`。
2. 固定失败状态：`missing_context`、`not_found`、`ambiguous`、`unsupported`、`temporary_failure`。
3. 固定 `interaction_trace` 必须能按 `request_id` 追溯解析、检索、回答和守卫。
4. 把当前自动化基线输出写入任务记录，不修改业务逻辑。

**验收**：既有测试全部通过；新增任务不改变已通过的显式指称、继承、切换和越界回退行为。

**证据**：测试命令、提交号、基线结果和本计划中的差异记录。

### RAG-NEXT-01：内容包契约与最小多展品 fixture

**目的**：从单件“战国水晶杯”演示升级为可比较的多展品输入，但先控制在 3 至 5 件，避免内容扩张掩盖检索缺陷。

**建议新增文件**：

- `main/xiaozhi-server/tests/fixtures/museum_content/valid-content.yaml`
- `main/xiaozhi-server/tests/fixtures/museum_content/invalid-content.yaml`
- `docs/requirements/museum-content-contract.md`

**每件展品的最小字段**：

```yaml
schema_version: 1
museum:
  id: fixture-museum
  name: 自动化测试博物馆
  status: active
zones:
  - id: fixture-gallery
    name: 自动化测试展区
    sort_order: 1
sources:
  - id: fixture-source-bronze
    title: 测试铜铃资料
    source_type: test_fixture
    locator: fixture://bronze-bell
    rights_note: 自动化测试专用。
exhibits:
  - id: fixture-bronze-bell
    zone_id: fixture-gallery
    name: 测试青铜铃
    aliases: [测试铜铃]
    status: active
    revision:
      id: fixture-bronze-bell-r1
      number: 1
      status: draft
      facts:
        - id: fixture-fact-bronze-material
          type: material
          statement: 这是一条测试事实。
          keywords: [材质, 青铜]
          confidence: test_fixture
          sources: [fixture-source-bronze]
```

**规则**：

- `id`、规范名称、事实 ID 和来源 ID 在输入包内唯一；
- 每个事实必须绑定至少一个来源；
- 每件展品最多一个活动 `published` revision；
- 活动别名跨展品冲突时必须报错；
- 原始问题保留，不用规范化文本覆盖用户审计记录；
- 内容 fixture 覆盖规范名称与别名；同义问法、资料不足和诱导编造问题归入 `RAG-NEXT-05` 的独立评测集。

**验收**：已完成。有效三展品内容包能够完整解析；非草稿状态、来源缺失和别名冲突能够一次返回；数据库中途失败不会留下半份写入。

### RAG-NEXT-02：内容导入与草稿事务

**关联**：`REQ-013 / AC-013-1 / AC-013-2`。

**建议修改文件**：

- `main/xiaozhi-server/core/museum/content_import.py`
- `main/xiaozhi-server/scripts/import_museum_content.py`
- `main/xiaozhi-server/tests/test_museum_content_import.py`

**CLI 形态**：

```powershell
python scripts/import_museum_content.py validate --input tests/fixtures/museum_content/valid-content.yaml
python scripts/import_museum_content.py import --input tests/fixtures/museum_content/valid-content.yaml --database data/museum.db
```

**实现步骤**：

1. 读取 YAML/JSON，并执行字段、ID、枚举、时间和引用校验。
2. 在导入前构建别名冲突表和事实来源图。
3. 通过单个数据库事务写入展品、来源、revision、fact 和 fact_source。
4. 导入默认只允许写入 `draft`，不得隐式发布。
5. 任一错误回滚整个批次，并在 CLI 输出展品 ID、字段和原因。
6. 重建受影响事实的 FTS5 行，禁止留下旧索引结果。

**验收**：

- 不改 Python 代码即可导入一件新展品；
- 无来源事实、重复 ID、坏外键和别名冲突均被拒绝；
- 故意制造一项错误时，数据库中没有半份导入结果；
- 导入后仍只能从 `published` 事实生成游客回答。

**证据**：CLI 输出、临时 SQLite 数据库快照、聚焦测试和 `git diff --check`。

**状态**：已完成 `validate`、可选数据库冲突检查和单事务 `draft import`；发布相关命令不在本任务内。

### RAG-NEXT-03：审核、发布、撤回和回滚

**关联**：`REQ-013 / AC-013-3 / AC-013-4 / AC-013-5`。

**建议修改文件**：

- `main/xiaozhi-server/core/museum/content_import.py`
- `main/xiaozhi-server/core/museum/store.py`
- `main/xiaozhi-server/scripts/import_museum_content.py`
- `main/xiaozhi-server/tests/test_museum_content_import.py`

**实现步骤**：

1. 发布前强制检查审核人、审核时间、来源绑定、别名冲突和事实非空。
2. 发布操作在事务中将目标 revision 设为 `published`，并撤回同一展品旧版本。
3. 不允许同一展品出现两个活动发布版本。
4. `withdraw` 使新回答不可见，但不能删除历史 revision、fact 或 source。
5. 新会话读取当前发布版本；历史 `interaction_trace` 保留原 `content_revision_id`。
6. 提供只读 `show` 命令输出版本差异和发布状态。

**验收**：

- 发布后新会话使用新版本；
- 撤回后同一问题返回 `unsupported` 或明确资料不足；
- 历史交互仍能按请求 ID 查到旧事实和来源；
- 任一发布校验失败都不改变原发布版本。

**状态**：已完成。发布门会聚合检查审核信息、事实来源、事实非空和活动别名冲突；发布、自动替代、撤回、回滚及生命周期事件在单一事务中完成；`show` 输出版本差异，`audit` 可按 `request_id` 还原旧 revision、事实和来源。

### RAG-NEXT-04：多展品事实级检索闭环

**关联**：`REQ-006 / REQ-008`。

**实现边界**：优先复用 `MuseumStore` 的 SQLite + FTS5，不先接外部向量库。

**检索顺序**：

1. `ExhibitResolver` 先得到唯一 `exhibit_id`；
2. SQL/FTS 查询同时限定 `exhibit_id`、当前发布 `revision_id`、有来源事实和允许的 `fact_type`；
3. 对 `price` 等强意图优先使用对应事实类型，不能被“时期”等弱词覆盖；
4. 查询结果转成不可变 `EvidenceSnapshot`；
5. `GroundedAnswerService` 只把快照交给 LLM 或确定性回答器；
6. `AnswerGuard` 检查事实 ID、数字、长度、展品边界和回答状态；
7. 每轮写入 `interaction_trace` 和 `museum_state`。

**验收**：

- A 展品问题不能返回 B 展品事实；
- 草稿和撤回事实不可见；
- 无检索命中时返回 `unsupported`，不调用通用知识补全；
- 回答中的数字和事实均能回指 `EvidenceSnapshot`；
- 内容发布后 FTS 与事实版本一致。

**完成证据**：

- `main/xiaozhi-server/content/museum/` 保存良渚博物院、杭州西湖博物馆总馆和中国丝绸博物馆 5 件藏品、26 条事实及官方来源定位；
- `exhibit_fact_fts` 增加 `exhibit_id` 和 `revision_id`，旧六列索引初始化时从关系表重建；
- `GroundedAnswerService` 对已知意图先执行受限检索，无候选时直接返回 `unsupported`，不调用 LLM；
- `main/xiaozhi-server/tests/test_museum_rag_multiexhibit.py` 覆盖跨馆切换、跨展品隔离、草稿和撤回、数字依据快照、旧 FTS 迁移及新旧版本隔离。

**状态**：已完成。聚焦验收 8 项全部通过，现有博物馆运行时、回答守卫、内容发布和展品解析回归保持通过。

### RAG-NEXT-05：自然问法、真实 LLM 和多轮评测

**关联**：`REQ-007 / REQ-008 / REQ-011 / REQ-017`。

**建议新增/扩展文件**：

- `main/xiaozhi-server/tests/fixtures/museum_conversation_eval.json`
- `main/xiaozhi-server/tests/test_museum_conversation_eval.py`
- `main/xiaozhi-server/tests/test_museum_llm_contract.py`
- `main/xiaozhi-server/scripts/evaluate_museum_rag.py`
- `docs/requirements/rag-evaluation-report.md`

**评测样本必须覆盖**：

- 规范名称、简称、审核别名和 ASR 常见误识别；
- “它多高”“这个怎么做出来的”“为什么长得像玻璃杯”等随机换问法；
- 首轮明确展品，后续省略展品名的追问；
- 同一会话明确切换另一展品；
- 未收录展品、共享别名歧义、展品明确但资料不足；
- 价格、年代、地点、材质、工艺等细意图优先级；
- 诱导模型编造人物、数字、传说或馆方未确认结论；
- 普通问候、身份问题和结束语，不误进入事实检索。

**真实 LLM 接入规则**：

1. 线上模型由配置选择，当前准备使用 `deepseek-v4-flash` 时必须先做一次配置连通性和响应格式检查；
2. LLM 只能接收当前问题、受限意图、候选事实和 `EvidenceSnapshot`，不能直接查询数据库；
3. 要求结构化返回 `status`、`fact_ids`、`answer`，解析失败立即走确定性回退；
4. 模型名称、请求 ID、提示版本、响应摘要和守卫结果写入审计，但不把密钥写入日志；
5. pytest 只用可控的伪模型验证结构化合同和守卫，真实模型通过独立评测脚本运行并生成报告，不能让外部模型波动污染常规回归测试；
6. 评测中同时保留规则回答和真实 LLM 回答，避免把模型波动误判为检索能力。

**验收目标**：

| 指标 | P0 门槛 |
| --- | --- |
| 规范名称解析准确率 | 100% |
| 审核别名解析准确率 | 不低于 95% |
| 歧义错误绑定率 | 0% |
| 未收录展品静默继承率 | 0% |
| 有依据回答越界率 | 0% |
| 资料不足编造率 | 0% |
| 依据快照可复核率 | 100% |
| 自然问法回答流畅度 | 单独人工评分，不用事实准确率替代 |

**完成证据**：

- `tests/fixtures/museum_conversation_eval.json` 保存 35 个用例、45 轮固定输入，覆盖 5 件杭州馆方藏品、12 个审核别名、5 个 ASR 误识别别名、多轮追问、显式切换、歧义、未收录、资料不足和诱导编造；
- `scripts/evaluate_museum_rag.py` 在隔离数据库中自动导入、审核、发布内容，并使用同一评测集分别运行规则基线和真实 LLM；
- `core/museum/llm_contract.py` 固定 `museum-grounded-router-v1` 提示版本和 JSON Object 响应合同；解析失败、非法事实 ID 或越界措辞继续走确定性回退；
- `interaction_trace` 已保存是否调用 LLM、模型名、提示版本、解析结果和不含原始回答文本的 SHA-256 响应摘要；
- `tests/test_museum_llm_contract.py` 覆盖 JSON 响应格式、错误字段结构、确定性回退和审计持久化；
- `rag-evaluation-report.md` 记录规则模式与 `deepseek-v4-flash` 各 45 轮、0 失败；规范名和审核别名准确率分别为 100%，歧义错误绑定、静默继承、事实越界和资料不足编造均为 0；
- 真实模型 37 次调用均成功解析结构化响应，并比纯规则额外接住 5 轮自然语义问法；严格守卫使多数模型措辞回退，项目内流畅度观察为 3/5，尚不等于儿童独立评审完成。

**状态**：已完成。P0 事实、上下文和审计门槛通过；儿童表达的独立人工评审继续归入 `REQ-011 / AC-011-4`，不虚报为完成。

### RAG-NEXT-06：未命中问题回收

**关联**：`REQ-014`。

**建议修改文件**：

- `main/xiaozhi-server/core/museum/store.py`
- `main/xiaozhi-server/scripts/export_museum_unanswered.py`
- `main/xiaozhi-server/tests/test_museum_unanswered_export.py`

**导出字段**：`request_id`、原问题、展品解析状态、展品 ID、`unanswered_reason`、粗/细意图、出现次数、最近时间、事实候选和守卫结果。

**分类**：

- `exhibit_not_found`：展品名称未收录；
- `exhibit_ambiguous`：别名共享或信息不足；
- `fact_not_covered`：展品明确但当前发布事实没有答案；
- `out_of_scope`：问题不属于展品问答范围；
- `asr_suspected`：文本疑似语音识别错误；
- `retrieval_failure`：应有事实但检索或系统异常。

**验收**：运营人员可以用一次 CLI 导出按展品、原因、次数和最近时间聚合的问题，并通过 `request_id` 回到完整审计记录。

**实现规则**：

1. 同一 `request_id` 只计算最新一条 trace，防止传输重试抬高出现次数；
2. 按 `exhibit_id + 分类 + 规范化原问题` 聚合，只合并大小写、全半角、空白和标点差异，不擅自把语义改写聚成一类；
3. `not_found` 和 `ambiguous` 直接采用展品解析证据；明确展品且无发布事实归为 `fact_not_covered`；比较等非单展品问题归为 `out_of_scope`；检索超时或系统错误归为 `retrieval_failure`；
4. `asr_suspected` 只比较解析出的未知展品文本与审核名称/别名，文本至少 4 个字符且编辑距离受限；短词不猜测；
5. 社交轮次和只缺少展品名称、没有未收录证据的请求不进入内容缺口报表；
6. JSON 和 CSV 均保留最新代表请求、原问题、原始 `unanswered_reason`、分类、事实候选和守卫结果；`audit` 子命令输出完整结构化 trace。

**命令**：

```powershell
python scripts/export_museum_unanswered.py export --database data/museum.db --output data/unanswered.json --format json
python scripts/export_museum_unanswered.py export --database data/museum.db --output data/unanswered.csv --format csv
python scripts/export_museum_unanswered.py audit --database data/museum.db --request-id <request_id> --output data/unanswered-audit.json
```

**完成证据**：提交 `0dac644`；`tests/test_museum_unanswered_export.py` 的 5 项测试通过；服务端完整回归 `112 passed in 8.86s`。数据库和输出路径均为显式参数，数据库不存在时返回 `database_not_found` 且不创建空库。

**状态**：已完成。该结论只覆盖服务端审计数据和文件导出，不代表真实 ASR 音频已验证；ASR 分类仍需用真机运行数据持续抽查。

### RAG-NEXT-07：真机前置检查与设备状态闭环

**关联**：`REQ-010 / REQ-012 / REQ-015`。

**前置条件**：本计划不假设硬件已经连接。未连接期间只能做服务器、协议、固件构建和测试；不能宣称真实语音链路通过。

**检查项**：

1. `museum_state` 的 `missing_context`、`retrieving`、`grounded`、`unsupported`、`temporary_failure` 与固件枚举一致；
2. 设备初始状态不绑定展品，首轮明确展品后才建立会话；
3. 重置后不继承上一组展品；
4. 真机脚本逐轮记录设备 ID、固件提交、服务端提交、原话、ASR、解析、事实、TTS ACK、屏幕和扬声器；
5. 只有刷写并实际观察设备后，才能解除 `REQ-015` 的 blocked。

### RAG-NEXT-08：向量检索决策门

**关联**：`REQ-018 / AC-018-2`。

第一阶段明确不做 Embedding。先准备至少 3 件展品、每件不少于 20 条可回答的语义改写问题；完成两轮关键词和事实类型词表修订后，达到以下条件之一，才开向量召回评审任务：

- 语义改写子集的正确事实 `Recall@5` 仍低于 95%；
- 同一种语义缺口在不同展品中重复出现至少 3 次，且继续新增词表会造成冲突或不可维护的特例；
- 内容规模达到 20 至 30 件展品、每件数十条事实后，FTS 的召回或延迟出现稳定、可重复的退化。

即使启用向量检索，也必须遵守：

1. 先限定 `exhibit_id` 和当前发布 `revision_id`，再做向量候选召回；
2. 向量结果只负责候选，不直接成为回答依据；
3. 最终仍回到事实 ID、来源绑定、版本状态和 `AnswerGuard`；
4. 必须用同一离线评测集比较 FTS 与 FTS+向量的召回率、错误绑定率、延迟和可解释性；
5. 混合召回至少提升 5 个百分点的 `Recall@5`，同时保持跨展品错误召回率和撤回事实可见率均为 0，才允许进入实现；
6. 没有量化收益时，不引入独立向量数据库、额外运维服务或全馆开放检索。

### RAG-NEXT-09：扩展到 20 至 30 件展品

**关联**：`REQ-017`。

只有 `RAG-NEXT-03` 发布闭环和 `RAG-NEXT-05` 评测门槛通过后才启动批量扩容。

**每件展品交付物**：

- 规范名称、审核别名和别名来源；
- 展区与基础标识；
- 至少一个发布 revision；
- 每类核心事实的来源绑定；
- 资料不足问题和诱导编造问题；
- 首轮、追问、切换和 ASR 误识别样本；
- 运营审计和未命中回收入口。

**验收**：至少 20 件展品均可导入、审核、发布和回滚；扩容不降低单展品事实边界、审计完整性和未命中分类质量。

## 5. 推荐的实际执行顺序

下一步不要先做向量数据库，也不要先把所有展品录完。按下面顺序推进：

1. `RAG-NEXT-01` 已完成：建立 3 件展品的最小内容 fixture 和内容契约；
2. `RAG-NEXT-02` 已完成：导入 CLI、完整校验和草稿事务；
3. `RAG-NEXT-03` 已完成：发布、撤回、回滚、生命周期事件和历史版本复核；
4. `RAG-NEXT-04` 已完成：FTS5 在 SQL 层绑定展品和发布版本，5 件馆方藏品未发生跨展品泄漏；
5. `RAG-NEXT-05` 已完成：真实 `deepseek-v4-flash` 与规则基线各执行 45 轮，自然问法和事实边界 P0 门槛通过；
6. `RAG-NEXT-06` 已完成：六类确定性归因、问题聚合、JSON/CSV 导出和代表请求审计反查；
7. 下一步执行 `RAG-NEXT-07` 的服务端、协议和固件前置检查；硬件接入后再完成真实链路证据；
8. 最后才批量扩展到 `RAG-NEXT-09`。

## 6. 每个任务的完成证据格式

每完成一个任务，在对应需求条目和本计划中留下同样格式的证据：

```text
任务 ID：RAG-NEXT-xx
代码提交：<commit>
输入样本：<fixture 或命令>
验证命令：<可复制命令>
结果：<passed / rejected / metrics>
数据库证据：<revision、fact、source 或审计查询>
未覆盖范围：<明确写出>
```

缺少其中任一项时，只能标记 `in_progress`，不能把目标能力写成 `done`。

## 7. 风险与停止条件

| 风险 | 识别信号 | 停止/处理方式 |
| --- | --- | --- |
| 内容导入半成功 | 批次失败后数据库出现部分行 | 立即回滚事务并补回归测试 |
| 别名错误绑定 | 新展品问题继承旧展品 | 阻断发布，先修解析索引和歧义规则 |
| LLM 编造 | 出现快照外数字、人物或地点 | AnswerGuard 回退；不通过验收 |
| 资料不足被补全 | `unsupported` 变成流畅但无来源答案 | 禁止通用 LLM 回退，保留 unanswered_reason |
| 多展品串库 | A 问题出现 B 事实 ID | 阻断检索发布，检查 exhibit/revision 过滤 |
| 向量技术先行 | 没有评测缺口就新增服务 | 退回 RAG-NEXT-08 决策门 |
| 硬件未接却宣称真机通过 | 只有文本或构建日志 | 保持 REQ-015 blocked |

## 8. 完成定义

RAG 阶段只有同时满足以下条件，才能把核心闭环称为完成：

1. 至少 3 至 5 件展品可以通过内容包导入、审核、发布和撤回；
2. 游客直接说出展品并提问时，系统唯一绑定展品并检索当前发布事实；
3. 随机换问法、自然追问和显式切换不串上下文；
4. 资料不足、歧义、未收录和越界问题都能明确停止；
5. 真实 LLM 输出经过事实 ID、来源和回答守卫；
6. 每轮能按 `request_id` 复核展品、版本、事实、来源和回答；
7. 未命中问题可以导出并区分原因；
8. 真机验收仍单独遵守 `REQ-015`，不被服务器测试或固件构建替代。
