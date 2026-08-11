# 展品级语音 RAG 实施计划

## 文档状态

- 状态：`in-progress`
- 编制日期：2026 年 8 月 11 日
- 产品依据：[`../product/PRD.md`](../product/PRD.md)
- 架构依据：[`../architecture/exhibit-rag-design.md`](../architecture/exhibit-rag-design.md)
- 当前代码依据：`main/xiaozhi-server/core/museum/`
- 实施范围：展品解析、临时会话、事实级 RAG、回答守卫、内容发布和真机语音验收
- 2026 年 8 月 11 日进度：服务端文本自由对话和真实 DeepSeek LLM 验收已完成；真机语音链路仍待现场验证

本文是执行计划，不是产品说明。每项工作都必须对应代码、测试或验收证据。没有完成定义和测试证据的任务，不得标记为完成。

## 1. 实施结论

下一阶段只解决一个核心问题：

> 游客直接说出展品并提问，服务端能够可靠识别展品、检索该展品的已发布事实、生成不越界的短回答，并允许游客自然追问和切换展品。

设备点位、路线推进、下一站和复杂触摸页面不属于这条主链路。现有 `device_placement` 和 `auto_assign_unknown_devices` 只能保留为开发演示兼容能力，生产默认必须关闭。

目标实现链路：

```text
ASR 文本
  -> 社交意图判断
  -> 展品名称/别名解析
  -> 会话当前展品建立、继承或切换
  -> 发布版本事实检索
  -> 依据快照
  -> 回答组合与守卫
  -> TTS / museum_state
  -> 交互审计
```

## 2. 当前基线

### 2.1 已存在并应复用

| 能力 | 现有位置 | 处理原则 |
| --- | --- | --- |
| 运行时接口 | `core/conversation_runtime.py` | 保留 `TurnRequest` / `TurnOutcome` seam |
| 运行时工厂 | `core/business_runtime_factory.py` | 继续创建 `MuseumRuntime` |
| 博物馆编排 | `core/museum/runtime.py` | 在入口处接入展品解析 |
| SQLite 内容模型 | `core/museum/store.py` | 增加迁移和解析索引，不重写存储层 |
| 事实检索 | `core/museum/store.py` | 保留 FTS5，先不引入向量数据库 |
| 回答和守卫 | `core/museum/answering.py` | 继续以 `EvidenceSnapshot` 为边界 |
| WebSocket/TTS | `core/connection.py` 及现有 TTS | 不重新设计音频协议 |
| 业务测试 | `tests/test_museum_runtime.py` | 改为显式展品问题并扩展场景 |

### 2.2 当前阻塞点

1. `MuseumRuntime._resolve_context()` 依赖设备点位或外部展品 ID。
2. `visitor_session.current_exhibit_id` 当前为非空字段，无法表达“刚开始还不知道展品”。
3. `MuseumStore.retrieve_evidence()` 需要先拿到展品 ID，当前没有问题到展品的解析模块。
4. `interaction_trace` 没有记录展品解析状态、匹配文本和候选展品。
5. 现有测试通过“演示设备默认展品”隐式建立上下文，不能证明真实硬件场景。
6. 当前内容只有演示展品，缺少批量导入、别名冲突检查和发布校验工具。

## 3. 任务总览

| 阶段 | 任务编号 | 目标 | 依赖 | 完成后结果 |
| --- | --- | --- | --- | --- |
| 0 | RAG-00 | 锁定新合同和生产开关 | 无 | 新旧模式边界明确 |
| 1 | RAG-10 至 RAG-13 | 实现展品解析 | RAG-00 | 首轮问题可确定展品 |
| 2 | RAG-20 至 RAG-23 | 改造会话与运行时 | 阶段 1 | 追问继承、显式切换可用 |
| 3 | RAG-30 至 RAG-33 | 完善证据、审计和状态 | 阶段 2 | 每轮回答可复核 |
| 4 | RAG-40 至 RAG-43 | 建立内容发布闭环 | 阶段 3 | 不改代码即可发布内容 |
| 5 | RAG-50 至 RAG-53 | 建立评测和真机验收 | 阶段 2、3 | 真实语音链路有证据 |
| 6 | RAG-60 | 扩展内容与可选能力 | 阶段 5 | 在核心闭环稳定后扩容 |

阶段必须按顺序推进。阶段 1 未通过前，不扩展展品数量；阶段 3 未通过前，不接入复杂内容后台；阶段 5 未通过前，不宣称比赛真机产品完成。

## 4. 阶段 0：锁定合同和生产开关

### RAG-00-01：定义展品解析结果

**修改文件**

- 新建 `main/xiaozhi-server/core/museum/exhibit_resolver.py`
- 修改 `main/xiaozhi-server/core/museum/contracts.py`

**实现内容**

新增不可变结果类型：

```python
ExhibitResolution(
    status="explicit|inherited|ambiguous|missing|not_found",
    exhibit_id=str | None,
    exhibit_name=str | None,
    matched_text=str | None,
    candidate_ids=tuple[str, ...],
    context_source="explicit_mention|inherited_session|ambiguous|missing",
)
```

状态含义必须与 PRD 和 `exhibit-rag-design.md` 完全一致。不要用布尔值表达解析结果，也不要把 `None` 同时表示缺失、歧义和未收录。

**完成定义**

- 类型可被运行时和测试共同导入；
- 非法状态不能构造；
- `context_source` 不允许出现设备默认展品作为生产来源；
- 新增单元测试覆盖全部状态。

### RAG-00-02：增加显式上下文运行模式

**修改文件**

- `main/xiaozhi-server/config.example.yaml`
- `main/xiaozhi-server/core/business_runtime_factory.py`

**配置建议**

```yaml
business_runtime:
  type: museum
  exhibit_context_mode: explicit
  auto_assign_unknown_devices: false
```

允许 `demo_placement` 仅用于本地演示和兼容旧测试；生产配置必须为 `explicit`。工厂启动时打印或记录有效模式，避免配置误读。

**验收**

- `explicit` 模式不会为未放置设备创建默认展品；
- `demo_placement` 模式明确标注为开发兼容模式；
- 配置错误时启动失败，而不是静默回退。

## 5. 阶段 1：实现展品解析

### RAG-10：建立展品候选索引

**修改文件**

- `main/xiaozhi-server/core/museum/store.py`
- 新建 `main/xiaozhi-server/core/museum/exhibit_resolver.py`

**第一版策略**

1. 读取活动展品规范名称。
2. 读取 `aliases_json` 中的别名。
3. 对名称和别名执行统一规范化：空白、常见标点、全半角和英文大小写。
4. 生成内存索引：规范化文本 → 展品候选 ID。
5. 规范名称精确匹配优先于别名匹配。
6. 多展品共享别名时保留全部候选，不静默覆盖。

**暂不做**

- 自动生成别名；
- 开放式同义词扩展；
- 让 LLM 直接输出展品 ID；
- 直接对整馆长文档做向量召回。

**完成定义**

- 同一名称和别名在同一数据库快照中可重复得到相同候选；
- 归档展品和归档别名不可参与解析；
- 索引构建失败时运行时明确失败，不使用半成品索引。

### RAG-11：实现唯一、歧义和缺失解析

**接口**

```python
class ExhibitResolver:
    def resolve(
        self,
        *,
        question: str,
        current_exhibit_id: str | None,
    ) -> ExhibitResolution: ...
```

**规则**

| 条件 | 结果 |
| --- | --- |
| 唯一规范名称命中 | `explicit` |
| 唯一活动别名命中 | `explicit` |
| 多个同等级候选 | `ambiguous` |
| 没有新展品指称且有有效会话展品 | `inherited` |
| 没有展品指称且无会话展品 | `missing` |
| 疑似新展品但词表没有命中 | `not_found` |

新展品指称优先于旧会话展品。`inherited` 只能在没有新候选时发生。

**测试文件**

- 新建 `main/xiaozhi-server/tests/test_exhibit_resolver.py`

**必须覆盖**

- “战国水晶杯为什么透明” → 唯一显式匹配；
- “水晶杯怎么做的” → 别名匹配；
- “它为什么这样” + 无会话 → `missing`；
- “它为什么这样” + 当前展品 → `inherited`；
- 新问题明确提到另一件展品 → `explicit` 切换；
- 共享简称 → `ambiguous`；
- 未收录展品 → `not_found`；
- 只改变标点、空格和大小写不改变结果。

### RAG-12：处理 ASR 展品别名

**新增内容**

建立可维护的语音别名格式，第一版可以继续写入 `aliases_json`，但每个别名必须注明来源和状态，不能直接把测试中的偶然错误写入正式词表。

建议记录：

- 原始识别文本；
- 目标展品；
- ASR 提供方；
- 设备型号；
- 录音样本或测试请求 ID；
- 确认人和确认时间。

**完成定义**

- 真实 ASR 误识别样本可以转为候选别名；
- 别名加入前能够检查冲突；
- 解析审计保留原始匹配文本。

### RAG-13：解析阶段门禁

阶段 1 只有同时满足以下条件才能进入阶段 2：

- 解析单元测试全部通过；
- 规范名称准确率达到 100%；
- 共享别名不发生错误自动绑定；
- 无上下文问题不再落入默认演示展品；
- 至少有一组真实 ASR 文本样本通过人工复核。

## 6. 阶段 2：改造会话与运行时

### RAG-20：允许空当前展品的会话

**修改文件**

- `main/xiaozhi-server/core/museum/store.py`
- `main/xiaozhi-server/core/museum/contracts.py`
- `main/xiaozhi-server/core/museum/runtime.py`

**数据变化**

目标设计允许会话先创建，再由首轮 `explicit` 解析建立展品。当前实现采用更保守的过渡方案：
在 `exhibit_context_mode=explicit` 下，未解析出展品时不创建 `visitor_session`，直接返回
`missing_context`；这样无需对现有 SQLite 表做破坏性迁移，也不会产生没有当前展品的可继承记录。

将 `visitor_session.current_exhibit_id` 改为可空属于后续需要长期连接会话时的独立迁移任务，当前文本验收不以此为完成前提。

不得直接对已有 SQLite 表执行可能破坏数据的 `ALTER COLUMN`。实现一个显式迁移步骤：

1. 读取当前 schema 版本；
2. 创建新表结构；
3. 复制已有会话数据；
4. 对仍有默认展品的旧会话保留其值；
5. 新表替换旧表；
6. 更新 `PRAGMA user_version`；
7. 迁移失败回滚整个事务。

**完成定义**

- 显式模式首轮没有展品时不会创建可继承会话；
- 过期会话不会提供继承上下文；
- 旧演示数据库可以继续读取；
- 后续 nullable 迁移必须单独设计、备份并验证幂等性。

### RAG-21：重排 MuseumRuntime 流程

**修改文件**

- `main/xiaozhi-server/core/museum/runtime.py`

`handle_turn()` 的目标顺序：

1. 识别有限社交意图；
2. 打开或恢复临时会话；
3. 读取当前会话展品；
4. 调用 `ExhibitResolver.resolve()`；
5. `explicit` 时建立或切换展品；
6. `inherited` 时使用当前会话展品；
7. `ambiguous`、`missing`、`not_found` 时返回澄清或未收录提示；
8. 唯一展品确定后调用现有回答服务；
9. 记录解析和回答审计；
10. 下发完整 `museum_state`。

设备点位解析只能在 `exhibit_context_mode=demo_placement` 下执行。生产模式不得在解析失败后回退到设备点位。

### RAG-22：调整回答服务接口

**修改文件**

- `main/xiaozhi-server/core/museum/answering.py`
- `main/xiaozhi-server/core/museum/store.py`

现有 `GroundedAnswerService.answer()` 可以保留事实守卫，但调用前提必须改成已经确定的展品 ID。回答服务不得承担展品识别职责。

必须确保：

- 展品解析失败时不调用 LLM 事实回答；
- 事实检索只限定当前展品和发布版本；
- `unsupported` 与 `missing_context` 分开；
- 追问每轮重新检索，而不是复制上轮回答；
- 用户明确提到新展品时旧展品事实不可进入新依据快照。

**分层问题理解实现**

- 新增 `core/museum/query_understanding.py`；
- 粗分类限定为社交、展品知识、比较、越界和不明确；
- 细分类第一版覆盖介绍、年代、材质、工艺、外形、出土、尺寸和价格；
- 细分类通过显式映射转换为数据库事实类型；
- `retrieve_evidence()` 接收允许的事实类型、查询词和介绍模式；
- 价格等强意图不能被句子中的“时期”等弱词覆盖；
- `interaction_trace` 保存粗分类、细分类和置信度。

**测试文件**

- `tests/test_query_understanding.py`：规则、优先级和事实类型映射；
- `tests/test_museum_conversation_eval.py`：多轮自然问法和实际回答结果。

### RAG-23：运行时集成测试

**修改文件**

- `main/xiaozhi-server/tests/test_museum_runtime.py`
- 新建 `main/xiaozhi-server/tests/test_museum_context_flow.py`

**测试场景**

1. 首轮明确展品 → `grounded`；
2. 首轮没有展品 → `missing_context`；
3. 首轮明确展品后省略名称追问 → `inherited`；
4. 同一会话明确说出新展品 → 切换；
5. 多候选 → `ambiguous`；
6. 展品明确但资料不足 → `unsupported`；
7. 无设备点位的生产模式仍可完成首轮问答；
8. 演示模式保留旧默认展品兼容，但测试必须显式标记。

### RAG-24：会话阶段门禁

阶段 2 只有同时满足以下条件才能进入阶段 3：

- 现有服务端测试全部通过；
- 新增上下文流测试全部通过；
- `auto_assign_unknown_devices=false` 时首轮显式问题可回答；
- 没有展品的首轮问题不会触发通用 LLM 回退；
- 追问和切换均能在数据库和 `museum_state` 中复核。

## 7. 阶段 3：证据、审计和设备状态

### RAG-30：扩展交互审计

**修改文件**

- `main/xiaozhi-server/core/museum/store.py`
- `main/xiaozhi-server/core/museum/runtime.py`

增加或结构化保存：

- `exhibit_resolution_status`；
- `exhibit_context_source`；
- `matched_exhibit_text`；
- `candidate_exhibit_ids`；
- `resolved_exhibit_id`；
- `content_revision_id`；
- `fact_ids`；
- `source_ids`；
- `guard_result`；
- `unanswered_reason`；
- `stage_latency`。

审计记录必须能够解释“为什么绑定到这件展品”，不能只记录最后的 `exhibit_id`。

### RAG-31：调整 museum_state

**修改文件**

- `main/xiaozhi-server/core/museum/runtime.py`
- `main/xiaozhi-server/core/connection.py`
- 固件仓库对应 `museum_state` 解析模块

`context.source` 第一版允许：

- `explicit_mention`；
- `inherited_session`；
- `missing`；
- `unassigned`（仅社交问候或未建立展品时）。

路线字段可以为了协议兼容保留，但不得作为当前产品页面或回答逻辑的必填业务状态。设备至少要能显示：等待、聆听、查阅、已依据、资料不足、请说出展品和系统失败。

### RAG-32：回答守卫测试

**修改文件**

- `main/xiaozhi-server/tests/test_museum_runtime.py`
- 新建 `main/xiaozhi-server/tests/test_museum_answer_guard.py`

覆盖：

- LLM 捏造人物、数字、地点时回退；
- LLM 返回不存在的事实 ID 时回退；
- 回答超长或句数不符时回退；
- 回答引用跨展品事实时拒绝；
- 资料撤回后新回答不可见；
- 历史审计仍保留原依据版本。

### RAG-33：审计阶段门禁

阶段 3 只有同时满足以下条件才能进入内容运营阶段：

- 每轮 `grounded`、`unsupported`、`missing_context` 都有清晰审计；
- 任意回答可以反查事实、来源和版本；
- `museum_state` 不会显示未确认的展品；
- 固件收到资料不足时不会显示成系统故障；
- 回答守卫测试全部通过。

## 8. 阶段 4：内容发布和未命中回收

### RAG-40：内容导入工具

**新建文件建议**

- `main/xiaozhi-server/core/museum/content_import.py`
- `main/xiaozhi-server/scripts/import_museum_content.py`
- `main/xiaozhi-server/tests/test_museum_content_import.py`

第一版输入格式采用版本化 JSON 或 YAML，至少包含：

```yaml
exhibit:
  id: warring-states-crystal-cup
  name: 战国水晶杯
  aliases:
    - 水晶杯
facts:
  - id: fact-crystal-cup-material
    type: material
    statement: 它由一整块天然水晶琢制而成。
    keywords: [材质, 水晶, 天然]
    sources: [source-people-daily-2026]
sources:
  - id: source-people-daily-2026
    title: 两千多年前的水晶杯
    type: publication
    locator: page-or-url
```

导入工具必须先校验，再在一个事务中写入草稿，不得导入半份内容。

### RAG-41：发布校验

发布前拒绝：

- 无来源事实；
- 空事实陈述；
- 引用不存在的来源；
- 活动别名冲突；
- 同一展品多个发布版本；
- 没有审核人或审核时间；
- 版本中的事实不属于目标展品。

### RAG-42：未命中导出

**修改文件**

- `main/xiaozhi-server/core/museum/store.py`
- 新建 `main/xiaozhi-server/scripts/export_museum_unanswered.py`
- 新建 `main/xiaozhi-server/tests/test_museum_unanswered_export.py`

输出按展品、解析状态、问题文本、出现次数和最近时间聚合。运营人员能够区分：

- 展品没识别；
- 展品有但事实没覆盖；
- 问题超出当前内容边界；
- ASR 文本明显错误。

### RAG-43：内容阶段门禁

阶段 4 只有同时满足以下条件才算完成：

- 不修改 Python 代码即可导入一件新展品；
- 发布前能拒绝无来源和冲突内容；
- 发布后检索可以使用新版本；
- 撤回后新回答不可再使用；
- 未命中问题可以导出并按原因分组。

## 9. 阶段 5：评测与真机验收

### RAG-50：建立固定评测集

**当前实现**

- `main/xiaozhi-server/tests/fixtures/museum_conversation_eval.json`
- `main/xiaozhi-server/tests/test_museum_conversation_eval.py`

当前评测集只验证 ASR 已经产出的文本，不宣称覆盖真实麦克风、ASR、TTS 或扬声器。真实 ASR 误识别样本必须在设备接入后采集，再加入评测集。

每件展品至少包含：

- 规范名称问题；
- 别名问题；
- ASR 误识别问题；
- 首轮明确展品；
- 后续省略展品名；
- 明确切换展品；
- 歧义；
- 展品未收录；
- 资料不足；
- 诱导编造；
- 普通问候。

### RAG-51：自动化指标

自动化检查：

- 规范名称解析准确率 100%；
- 审核别名解析准确率不低于 95%；
- 歧义错误绑定率 0%；
- 无上下文错误绑定率 0%；
- 资料外事实编造率 0%；
- 依据快照可复核率 100%。

流畅度、儿童理解度和趣味性由独立人工评审，不由模型自评。

**本地执行**

```powershell
$env:TEMP=(Resolve-Path .pytest-local).Path
$env:TMP=$env:TEMP
python -m pytest -q tests/test_museum_conversation_eval.py
```

当前评测集覆盖：规范名称、别名、礼貌前缀、代词追问、跨轮继承、未收录展品切换、无上下文、资料不足和价格类越界问题。

### 2026-08-11：服务端文本自由对话验收入口

已实现 `main/xiaozhi-server/scripts/museum_text_chat.py`，它直接复用真实
`MuseumRuntime`、SQLite 内容库和交互审计，不连接硬件也可以连续输入任意文字。

```powershell
cd main/xiaozhi-server
python scripts/museum_text_chat.py --no-llm
```

控制台支持 `/help`、`/reset`、`/audit` 和 `/quit`；`--once ... --json` 可用于单轮自动化检查。
当前私有配置已选择 `DeepSeekV4Flash`，模型为 `deepseek-v4-flash`。使用
`--require-llm` 可以强制校验真实 LLM 配置；没有凭据时会以退出码 2 明确失败，不能把规则模式冒充为模型验收。

本轮已验证：

- 规范问法和自然问法能够落到细意图与事实 ID；
- “咋做出来”等口语工艺问法能够归一化到已发布制作事实；
- 新控制台实例不会继承上一次进程的展品会话；
- 资料未覆盖的问题仍返回 `unsupported`，不借用相邻事实回答；
- 全量服务端自动化测试 `66 passed`。

2026 年 8 月 11 日使用真实 DeepSeek API 完成 7 轮连续文本验收，问题采用未预先固定答案的自然表达：

1. “你好，我想看看战国水晶杯，它到底是拿什么做的？” → 材质事实；
2. “它大概是什么时候的东西？” → 继承当前展品并命中年代事实；
3. “这么硬，古人当时是怎么把它做出来的？” → 继承当前展品并命中工艺事实；
4. “它是在哪儿发现的呀？” → 命中出土事实；
5. “现在拿去拍卖的话，大概值多少钱？” → `unsupported`；
6. “那越王勾践剑的材质呢？” → 未收录展品，未复用战国水晶杯；
7. “谢谢，刚才那个杯子为什么这么透明？” → 继承展品，资料不足，返回 `unsupported`。

每轮都生成了审计 ID、事实 ID或明确的资料不足状态。该记录证明真实 LLM 文本对话和 RAG 守卫可工作，不证明 ASR、TTS、扬声器、屏幕或真机 WebSocket ACK。

本入口不证明麦克风、ASR、TTS、扬声器、屏幕、WebSocket ACK 或真实 LLM 的效果；这些仍属于 RAG-52/RAG-53 的硬件和配置验收。

### RAG-52：真机验收脚本

每次验收必须记录：

- 设备 ID；
- 固件提交；
- 服务端提交；
- 说话内容和 ASR 文本；
- 展品解析结果；
- 依据事实、来源和内容版本；
- TTS `ready` / `done`；
- 屏幕状态；
- 扬声器实际播放；
- 总延迟和异常日志。

最小现场脚本：

1. “你好，你是谁？”；
2. “战国水晶杯是什么材质？”；
3. “它是怎么做出来的？”；
4. “越王勾践剑是什么材质？”（切换）；
5. “它的主人叫什么？”（资料不足）；
6. “那个杯子为什么这样？”（无上下文或歧义澄清）；
7. 中断播报后重新提问；
8. 弱网或服务失败后恢复。

### RAG-53：真机阶段门禁

只有在以下证据全部存在时，才能把比赛核心链路标记为通过：

- 真实麦克风输入和 ASR 文本；
- 服务端展品解析和依据审计；
- 真实 TTS 音频下发；
- 扬声器实际播放和 ACK；
- 屏幕状态正确；
- 至少 30 分钟连续运行；
- 至少一次断线或失败恢复；
- 记录中包含服务端和固件提交号。

## 10. 阶段 6：扩容和可选能力

只有阶段 5 通过后才能开始：

- 扩展到 20 至 30 件展品；
- 增加儿童表达模板和人工评测；
- 根据评测结果决定是否加向量召回；
- 增加可选主题路线或研学编排；
- 增加图片辅助识别；
- 增加离线缓存。

以下工作在阶段 5 前禁止启动：

- 独立向量数据库；
- 三条固定路线；
- 大而全运营后台；
- 多展馆；
- 多语言；
- 复杂设备页面。

## 11. 文件级变更清单

### 必改文件

| 文件 | 变更 |
| --- | --- |
| `core/museum/contracts.py` | 增加展品解析结果和上下文来源类型 |
| `core/museum/runtime.py` | 将展品解析置于事实检索之前 |
| `core/museum/store.py` | 会话迁移、别名索引、解析审计和内容查询 |
| `core/museum/answering.py` | 保持事实守卫，禁止承担展品识别 |
| `core/business_runtime_factory.py` | 增加显式上下文模式和生产默认值 |
| `config.example.yaml` | 声明 `exhibit_context_mode=explicit` |
| `tests/test_museum_runtime.py` | 改为显式展品首轮和追问场景 |

### 新增文件

| 文件 | 变更 |
| --- | --- |
| `core/museum/exhibit_resolver.py` | 展品名称、别名、歧义和继承解析 |
| `core/museum/query_understanding.py` | 粗分类、细分类和事实类型约束 |
| `tests/test_exhibit_resolver.py` | 展品解析单元测试 |
| `tests/test_query_understanding.py` | 分层问题理解单元测试 |
| `tests/test_museum_context_flow.py` | 首轮、追问、切换和澄清集成测试 |
| `tests/test_museum_answer_guard.py` | 回答守卫测试 |
| `core/museum/content_import.py` | 内容导入和发布前校验 |
| `scripts/import_museum_content.py` | 内容导入 CLI |
| `scripts/export_museum_unanswered.py` | 未命中问题导出 CLI |
| `tests/fixtures/museum_conversation_eval.json` | 自然问法和多轮会话评测集 |
| `tests/test_museum_conversation_eval.py` | 评测集自动检查 |

### 暂不修改

- 现有 ASR provider；
- 现有 TTS provider 和可靠播放协议；
- Opus、VAD 和 WebSocket 基础链路；
- OTA 发布逻辑；
- 浏览器调试端的 Live2D 和 MCP 能力。

## 12. 每阶段通用交付格式

每个阶段结束时必须提交一份短记录，包含：

1. 实际修改文件；
2. 新增或修改测试；
3. 测试命令和完整结果；
4. 通过的验收场景；
5. 未通过的场景和原因；
6. 数据库 schema 版本；
7. 服务端提交号；
8. 若涉及真机，记录固件提交号和设备 ID。

“代码已经写完”“本地能跑”“日志看起来正常”都不能替代阶段门禁。

## 13. 回滚策略

### 代码回滚

- 每个阶段使用独立提交；
- 阶段门禁未通过时回退到上一个通过阶段；
- 不通过删除旧文件来解决新链路故障；
- 不回滚用户已有的无关工作区修改。

### 配置回滚

- 生产默认保持 `exhibit_context_mode=explicit`；
- 本地演示可以显式切换 `demo_placement`；
- 配置切换必须写入启动日志和验收记录。

### 数据回滚

- schema 迁移前备份数据库并记录 SHA-256；
- 内容发布使用新版本，不覆盖历史发布版本；
- 撤回只影响新回答，不删除历史交互依据；
- 迁移失败必须恢复原数据库文件和 schema 版本。

## 14. 首个可执行迭代

第一轮不要同时修改固件、后台和内容规模，只完成以下 8 项：

1. 新增 `ExhibitResolution`；
2. 新增 `ExhibitResolver`；
3. 用规范名称和别名构建候选索引；
4. 在不改动现有 schema 的前提下，保证显式模式不会创建无展品会话；
5. 在 `MuseumRuntime.handle_turn()` 接入解析；
6. 将现有测试从默认设备展品改为显式展品问题；
7. 增加首轮、追问、切换、歧义和无上下文测试；
8. 在一个无设备点位的本地数据库上跑通完整文字链路。

首个迭代的完成标准：

```text
“战国水晶杯是什么材质？” -> grounded
“它是怎么做出来的？”     -> inherited + grounded
“越王勾践剑是什么材质？”  -> explicit switch + grounded/unsupported
“它为什么这样？”          -> missing_context 或 ambiguous
```

在这四类行为稳定之前，不增加路线、向量数据库或复杂后台。
