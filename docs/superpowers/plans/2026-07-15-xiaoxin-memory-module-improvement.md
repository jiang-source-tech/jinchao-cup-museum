# 小芯可信记忆模块改造实施计划

> 本计划把已经对齐的产品效果落实为可分步实施、可回滚、可验收的工程任务。相关未决设计集中记录在 [可信记忆模块决策地图](2026-07-15-xiaoxin-memory-module-decision-map.md)；实施过程中不得绕过未决票据直接固化高风险架构。

**目标：** 将当前分散在 profile、episodic、companion、growth arc 和 relationship 中的记忆原型，升级为以统一单轮分析和统一证据为事实源的可信长期陪伴记忆模块。

**第一阶段产品标准：** 准确、克制、连续、可解释、可纠正、可遗忘。

**第一阶段范围：** 用户称呼、专业、年级、学校/学院；少量学习和提醒偏好；短期状态；C 语言学习成长弧线；跨会话召回；来源解释；纠正；定向遗忘；主体隔离。

**非目标：** 全量聊天永久保存、所有主题成长弧线、复杂心理画像、学年回顾、自动主动关心、按聊天次数升级关系、跨 appid 自动认回、立即引入向量数据库。

**主要技术：** Python 3、dataclasses、SQLite WAL、pytest、现有 Xiaoxin runtime/identity/memory 模块。

---

## 1. 当前基线

当前已经具备：

- 稳定 `memory_subject_id`、confirmed/device_unknown/device_fallback 和 subject alias。
- profile、episodic、companion、growth arc、relationship state 分层实现。
- profile 的来源、置信度、active/superseded/forgotten 和字段级遗忘。
- companion 的来源文本、重要性、半衰期、定向遗忘和 forgotten history。
- memory orchestrator、memory use policy、存储降级和部分跨层遗忘同步。
- 正在进行的统一 TurnAnalysis 与 runtime 单轮复用改造。

当前关键缺口：

- 全局 persistence 无法稳定表达一轮中的长期事实、短期状态和禁止内容并存。
- 各层仍可能从原始文本重复识别并得出不同结论。
- growth arc 证据来源为空，状态基本只允许单向上升，遗忘直接删除。
- episodic 逐轮写入没有可靠 session/outcome 语义。
- JSON 原子替换不能避免并发丢失更新。
- 哪些 memory subject 可以推动个人宠物成长仍无明确合同。
- 召回主要依靠关键词，缺少统一硬过滤、离线评测和证据许可。

### 工作区保护约束

当前工作区已有未提交的记忆和需求修改。执行本计划前必须：

- 记录 `git status --short`。
- 不覆盖、回退或格式化无关用户修改。
- 先将当前 TurnAnalysis 切片独立验证和提交，或在明确命名的工作分支中保存。
- 每个任务只修改其文件清单中的文件；发现重叠修改时先重新读取最新内容。

---

## 2. 目标模块与外部接口

runtime 最终只跨一个记忆 seam：

```python
@dataclass(frozen=True)
class MemoryTurnRequest:
    turn_id: str
    subject_id: str
    session_id: str
    user_text: str
    route: dict[str, object]
    history: tuple[dict[str, object], ...]
    occurred_at: str


@dataclass(frozen=True)
class PreparedMemoryTurn:
    request: MemoryTurnRequest
    analysis: TurnAnalysis
    prompt_context: str
    used_evidence_ids: tuple[str, ...]
    memory_policy: dict[str, object]


class MemoryEngine:
    def prepare_turn(self, request: MemoryTurnRequest) -> PreparedMemoryTurn: ...
    def commit_turn(self, prepared: PreparedMemoryTurn, assistant_reply: str) -> MemoryCommitResult: ...
    def control(self, subject_id: str, command: MemoryControlCommand) -> MemoryControlResult: ...
    def explain(self, subject_id: str, memory_id: str) -> MemoryExplanation: ...
```

接口不暴露：

- profile/growth/episode 文件路径；
- 各投影的调用顺序；
- 关键词或向量检索实现；
- SQLite 表结构；
- prompt 拼装细节；
- 衰减和状态迁移内部规则。

### 删除测试

删除 `MemoryEngine` 后，以下复杂度应重新散落到 runtime 和多个记忆层，证明该模块具有足够深度：

- 单轮分析唯一性；
- 记忆控制优先级；
- 召回硬过滤；
- Evidence 原子提交；
- 投影更新；
- 幂等与错误恢复；
- 解释、纠正和遗忘。

如果 `MemoryEngine` 只调用现有函数并原样返回结果，则该任务未完成。

---

## 3. 统一合同

### 3.1 MemoryCandidate

```python
@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    kind: str
    topic: str
    content: dict[str, object]
    source: str
    confidence: float
    retention: str
    expires_at: str | None
    allow_prompt_injection: bool
```

`retention` 第一版仅允许：

- `none`：禁止持久化。
- `ephemeral`：短期状态，必须带 `expires_at`。
- `durable`：稳定事实、偏好或成长证据。

### 3.2 MemoryDirective

```python
@dataclass(frozen=True)
class MemoryDirective:
    action: str
    target_kind: str
    target_query: str
    reason: str
    source_text_safe: str
```

`action` 第一版支持：

- `correct`
- `forget`
- `purge`
- `block_topic`
- `allow_topic`

### 3.3 TurnAnalysis

```python
@dataclass(frozen=True)
class TurnAnalysis:
    schema_version: int
    turn_kind: str
    topic: str
    mood: str
    candidates: tuple[MemoryCandidate, ...]
    directives: tuple[MemoryDirective, ...]
    subject_eligible_for_growth: bool
```

不得继续使用一个全局布尔值决定整轮所有内容是否持久化。

### 3.4 MemoryEvidence

```python
@dataclass(frozen=True)
class MemoryEvidence:
    evidence_id: str
    turn_id: str
    subject_id: str
    kind: str
    topic: str
    content: dict[str, object]
    source_type: str
    source_text_safe: str
    occurred_at: str
    recorded_at: str
    confidence: float
    retention: str
    expires_at: str | None
    status: str
    supersedes_id: str | None
    allow_prompt_injection: bool
```

`status` 第一版允许：

- `candidate`
- `active`
- `superseded`
- `forgotten`
- `expired`
- `rejected`

---

## 4. 推荐数据模型

在决策票据 #6 通过后，推荐新增独立 `data/xiaoxin_memory.db`，避免将高频记忆写入与身份控制数据混在同一数据库。

### 核心表

```text
memory_turns
memory_evidence
memory_evidence_links
memory_topic_controls
profile_projection
episodes
growth_arcs
growth_arc_events
relationship_projection
followups
memory_migrations
```

### 必须由数据库保证的不变量

- `(turn_id, candidate_id)` 唯一，提交重试不重复。
- 同一 `subject_id + profile field` 最多一个 active 投影。
- 每条 growth event 必须关联 active 或历史 Evidence。
- `forgotten/expired/rejected` Evidence 不允许 prompt injection。
- Evidence 状态更新与受影响投影更新处于同一事务。
- topic block 生效后，相关 Evidence 即使仍 active 也不得召回。

### SQLite 运行约束

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

所有写操作使用显式事务；测试使用临时 SQLite 数据库，不为 SQLite 再引入只存在一个实现的外部 port。

---

## 5. 黄金验收故事

固定主体 `subject-xiaolin`，固定 Asia/Shanghai 时间，分不同 session 执行：

1. `我叫小林，是自动化专业大一学生。最近有点担心 C 语言跟不上。`
2. `我开始每天练半小时了。`
3. `链表还是没搞懂。`
4. `链表终于搞懂了。`
5. `我的 C 语言项目跑通了。`
6. `我这段时间有什么变化？`
7. `昨天不是我的项目跑通，是我室友的。`
8. `别再提 C 语言了。`
9. `我最近有什么变化？`

必须证明：

- 资料和成长事件被拆为独立候选。
- 短期焦虑不会成为永久人格标签。
- C 语言弧线支持 concern/attempt/setback/progress/milestone。
- 第 6 步按时间使用真实 Evidence，且能返回依据。
- 第 7 步撤销错误里程碑并重算状态。
- 第 8 步建立 topic block、关闭 followup、禁止后续注入。
- 第 9 步不再引用 C 语言，不通过同义改写绕过拒绝。

---

## 6. 文件规划

### 新建

- `main/xiaozhi-server/core/xiaoxin/memory/contracts.py`
- `main/xiaozhi-server/core/xiaoxin/memory/engine.py`
- `main/xiaozhi-server/core/xiaoxin/memory/evidence_store.py`
- `main/xiaozhi-server/core/xiaoxin/memory/growth_state.py`
- `main/xiaozhi-server/core/xiaoxin/memory/legacy_importer.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_contracts.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_engine.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_evidence_store.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_growth_story.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_concurrency.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_legacy_importer.py`

### 修改

- `main/xiaozhi-server/core/xiaoxin/turn_analysis.py`
- `main/xiaozhi-server/core/xiaoxin/runtime.py`
- `main/xiaozhi-server/core/xiaoxin/memory/memory_orchestrator.py`
- `main/xiaozhi-server/core/xiaoxin/memory/profile_memory.py`
- `main/xiaozhi-server/core/xiaoxin/memory/companion_memory.py`
- `main/xiaozhi-server/core/xiaoxin/memory/episodic_memory.py`
- `main/xiaozhi-server/core/xiaoxin/memory/growth_arc.py`
- `main/xiaozhi-server/core/xiaoxin/memory/relationship_state.py`
- `main/xiaozhi-server/tests/xiaoxin/test_identity_runtime_integration.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_runtime.py`
- `docs/requirements/requirements.yaml`

### 最终可删除或降为迁移兼容

- 各记忆层从原始文本重新识别候选的重复规则。
- runtime 对 profile/episodic/companion/growth/relationship 的直接知识。
- 新接口测试已经覆盖后的浅层重复单元测试。
- 迁移窗口结束后的 JSON 写路径。

---

## 7. 实施任务

### Task 0：冻结基线和补充反例合同

**目标：** 在任何架构改造前，把当前正确行为和已知错误场景变成测试。

**测试样本：**

```text
我叫小林，但今天只是有点烦。
C 语言里的指针是什么？
不是我项目跑通，是我室友。
不用记这个。
别再提 C 语言了。
我以前想参加智能车比赛，现在不想参加了。
```

- [ ] 记录当前相关测试命令和通过数量。
- [ ] 为每个样本断言 turn kind、候选、retention、directive 和禁止项。
- [ ] 增加 confirmed/unknown/fallback 的成长资格测试。
- [ ] 增加普通事实问答不更新 relationship/growth 的集成测试。
- [ ] 保存测试失败清单，不在同一提交中实现修复。

运行：

```powershell
python -m pytest tests/xiaoxin/test_turn_analysis.py tests/xiaoxin/test_memory_runtime.py tests/xiaoxin/test_identity_runtime_integration.py -q
```

建议提交：

```text
test: define trustworthy memory baseline
```

### Task 1：建立不可变合同类型

**目标：** 用类型取代跨层松散字典，并支持混合持久化。

- [ ] 新建 `contracts.py`。
- [ ] 定义 MemoryCandidate、MemoryDirective、TurnAnalysis、MemoryEvidence。
- [ ] 为枚举值、置信度范围、ephemeral 过期时间和安全来源增加校验。
- [ ] 为序列化/反序列化增加 schema version 测试。
- [ ] 保留临时 `to_legacy_dict()` 兼容路径，但禁止新实现依赖它。

运行：

```powershell
python -m pytest tests/xiaoxin/test_memory_contracts.py -q
```

建议提交：

```text
feat: define trustworthy memory contracts
```

### Task 2：重构统一 TurnAnalysis

**目标：** 一轮生成零到多个候选和高优先级 directive。

优先级固定为：

```text
危机/硬边界
→ 用户控制
→ 主体资格
→ 硬事实与纠正
→ 成长事件
→ 稳定偏好
→ 短期状态
→ 普通知识问答/闲聊
```

- [ ] 移除全局 `all(signal.persistence_allowed)` 决策语义。
- [ ] 支持一轮多候选。
- [ ] 增加否定、转述、纠正和混合输入规则。
- [ ] 让确定性规则裁决硬事实、用户控制和边界。
- [ ] 暂不引入 LLM 分析；保留未来低确定性补充 seam。
- [ ] 证明同一分析对象被 prepare 和 commit 复用。

运行：

```powershell
python -m pytest tests/xiaoxin/test_turn_analysis.py tests/xiaoxin/test_identity_runtime_integration.py -q
```

建议提交：

```text
refactor: emit per-candidate memory retention
```

### Task 3：建立深 MemoryEngine seam

**目标：** runtime 只了解 prepare/commit/control/explain。

- [ ] 新建 `engine.py` 和接口测试。
- [ ] 将分析、召回策略、上下文构造和提交顺序移入 engine。
- [ ] PreparedMemoryTurn 保存分析和 used evidence IDs。
- [ ] commit 必须接受 PreparedMemoryTurn，禁止重新分析用户文本。
- [ ] 兼容现有 JSON 存储实现，暂不改变最终数据结果。
- [ ] runtime 删除对 memory orchestrator 参数顺序的直接知识。
- [ ] 验证 hard template、memory control 和禁用持久化路径。

运行：

```powershell
python -m pytest tests/xiaoxin/test_memory_engine.py tests/xiaoxin/test_runtime.py tests/xiaoxin/test_identity_runtime_integration.py -q
```

建议提交：

```text
refactor: place runtime memory behind one engine
```

### Task 4：实现 SQLite Evidence Store

**目标：** 建立事务化、幂等和可解释的统一事实源。

- [ ] 新建 `evidence_store.py`。
- [ ] 创建 memory_turns、memory_evidence、links 和 topic_controls 表。
- [ ] 相同 turn_id/candidate_id 重试返回同一 Evidence。
- [ ] 实现 active/superseded/forgotten/expired/rejected 状态迁移。
- [ ] 实现 explain 和按主体/主题/状态查询。
- [ ] 实现 WAL、busy timeout 和事务回滚测试。
- [ ] 增加两个并发 writer 不丢失更新的测试。

运行：

```powershell
python -m pytest tests/xiaoxin/test_memory_evidence_store.py tests/xiaoxin/test_memory_concurrency.py -q
```

建议提交：

```text
feat: persist memory evidence transactionally
```

### Task 5：迁移 Profile 为 Evidence 投影

**目标：** 保持现有 Profile 能力，同时让统一 Evidence 成为事实源。

- [ ] profile candidate 写入 Evidence。
- [ ] profile projection 每字段最多一个 active 值。
- [ ] 纠正时旧值 superseded，新值 active，并建立替代关系。
- [ ] explain 返回 Evidence 的安全来源。
- [ ] forget/purge 后 projection 立即更新。
- [ ] 保持称呼、专业、年级、学校现有合同。

运行：

```powershell
python -m pytest tests/xiaoxin/test_memory_engine.py tests/xiaoxin/test_memory_runtime.py -k "profile or correction or forget" -q
```

建议提交：

```text
refactor: project profile facts from evidence
```

### Task 6：实现 C 语言成长状态机

**目标：** 完成第一条可倒退、可纠正、可重建的成长弧线。

事件：

```text
concern attempt setback progress pause restart milestone reflection resolve
```

- [ ] 新建 `growth_state.py`，纯函数计算状态迁移。
- [ ] Growth event 必须引用 Evidence ID。
- [ ] 支持 progressing/milestone 后再次 setback。
- [ ] 支持 pause/restart/resolve/reopen。
- [ ] 同一 Evidence 不重复推动计数。
- [ ] 从 Evidence 重建得到与增量更新相同的投影。

运行：

```powershell
python -m pytest tests/xiaoxin/test_memory_growth_story.py -k "state or rebuild" -q
```

建议提交：

```text
feat: model reversible c language growth
```

### Task 7：实现纠正、忘记、topic block 和 purge

**目标：** 用户控制一次生效于所有投影和召回路径。

- [ ] correction 建立 supersedes/rejects 关系。
- [ ] forget 禁止 prompt injection 并重建投影。
- [ ] block_topic 关闭相关 followup 和主动入口。
- [ ] purge 清除内容与来源，只保留无内容墓碑。
- [ ] explain 能区分 active、superseded 和 forgotten。
- [ ] 遗忘后 profile、companion、episode、growth、relationship 均不再使用。

运行：

```powershell
python -m pytest tests/xiaoxin/test_memory_growth_story.py tests/xiaoxin/test_memory_engine.py -k "correct or forget or block or purge" -q
```

建议提交：

```text
feat: unify memory correction and forgetting
```

### Task 8：重建 Episode 语义

**目标：** Episode 表示完成的互动事件，而不是值得保存的单句。

- [ ] 保存 session_id、用户意图、用户摘要、assistant action 和 outcome。
- [ ] 只在存在值得延续的结果时生成 Episode。
- [ ] 首次见面、会话摘要和每日摘要关联 Evidence IDs。
- [ ] 重复会话提交幂等。
- [ ] 不保存原始完整聊天历史作为长期事实。

运行：

```powershell
python -m pytest tests/xiaoxin/test_memory_engine.py -k "episode or session" -q
```

建议提交：

```text
refactor: make episodes represent interaction outcomes
```

### Task 9：统一召回硬过滤和排序

**目标：** 先保证“绝不召回错误内容”，再优化召回率。

硬过滤顺序：

```text
subject
→ alias resolution
→ growth eligibility
→ active status
→ not expired
→ no topic block
→ allow_prompt_injection
```

- [ ] 将 memory use policy 移入 engine。
- [ ] prepare_turn 必须传入真实 route，不再丢失路由信息。
- [ ] 普通知识问答默认不注入陪伴和成长记忆。
- [ ] 普通回复最多使用一条旧成长线索。
- [ ] 明确回望最多使用少量按时间排序的 Evidence。
- [ ] 建立离线查询-期望证据数据集。
- [ ] 第一阶段保留 KeywordEpisodeIndex，不接向量数据库。

运行：

```powershell
python -m pytest tests/xiaoxin/test_memory_engine.py tests/xiaoxin/test_memory_growth_story.py -k "recall or prompt or reflection" -q
```

建议提交：

```text
feat: enforce evidence-safe memory recall
```

### Task 10：确定主体成长资格与合并

**目标：** 未确认说话人的内容不会污染个人宠物成长。

- [ ] confirmed speaker 可以写个人 Evidence 和推动成长。
- [ ] alias 到 confirmed 的主体使用 canonical subject。
- [ ] device_unknown/fallback 只写隔离临时证据。
- [ ] 用户确认合并时记录显式 provenance。
- [ ] 账号 A/B、设备更换和未知说话人建立完整隔离测试。

运行：

```powershell
python -m pytest tests/xiaoxin/test_identity_resolver.py tests/xiaoxin/test_identity_store.py tests/xiaoxin/test_memory_engine.py -k "subject or alias or unknown or fallback" -q
```

建议提交：

```text
feat: gate personal growth by confirmed subject
```

### Task 11：迁移旧 JSON 数据

**目标：** 单次导入、可观察、可重复、可回滚，不长期双写。

- [ ] 新建 legacy importer。
- [ ] dry-run 输出文件数、记录数、跳过数、冲突数和错误数。
- [ ] 每条迁移 Evidence 标记 legacy 来源与原文件摘要哈希。
- [ ] 重复导入不产生重复记录。
- [ ] 验证成功后切换读取到 SQLite。
- [ ] 旧 JSON 改为只读备份；保留明确回滚窗口。
- [ ] 回滚只切换读取，不把新 Evidence 反向覆盖旧文件。

运行：

```powershell
python -m pytest tests/xiaoxin/test_memory_legacy_importer.py -q
```

建议提交：

```text
feat: import legacy memory into evidence store
```

### Task 12：黄金故事和跨会话端到端验收

**目标：** 使用真实 MemoryEngine、临时数据库和不同 session 完成全部九轮故事。

- [ ] 固定时钟和 Asia/Shanghai 日期。
- [ ] 每轮断言 TurnAnalysis、Evidence、投影和 prompt context。
- [ ] 模拟服务重启后继续故事。
- [ ] 第 6 轮回望必须按时间且所有陈述可解释。
- [ ] 第 7 轮纠正后旧 milestone 不再生效。
- [ ] 第 8 轮 block 后第 9 轮不再召回 C 语言。
- [ ] 增加账号 B 和未知说话人穿插对话，证明零串记。

运行：

```powershell
python -m pytest tests/xiaoxin/test_memory_growth_story.py -q
```

建议提交：

```text
test: verify trustworthy memory growth story
```

### Task 13：删除重复实现并更新需求状态

**目标：** 完成 replace-not-layer，避免新旧逻辑永久并存。

- [ ] 删除各层重复的候选识别规则或降为 importer 专用。
- [ ] memory_orchestrator 降为兼容 adapter 或删除。
- [ ] 删除被 MemoryEngine 接口测试完全覆盖的浅层重复测试。
- [ ] 保留存储安全、状态机纯函数和 identity 等内部 seam 测试。
- [ ] 更新 requirements.yaml 中 CG-01/02/04 的 implemented、remaining 和 acceptance 证据。
- [ ] 记录未完成的 CG-03/05/06/07，不得顺带标记完成。

运行：

```powershell
python -m pytest tests/xiaoxin -q
python -m pytest ..\..\docs\requirements\test_requirements_workbench.py -q
```

建议提交：

```text
refactor: retire legacy memory write paths
```

---

## 8. 测试分层

### 合同测试

通过 MemoryEngine 外部接口验证：

- 混合候选；
- 幂等 prepare/commit；
- 用户控制优先级；
- 召回许可；
- explain 结果；
- 错误模式。

### 不变量测试

- 同一主体同一 profile 字段最多一个 active 值。
- forgotten/expired/rejected 永不进入提示词。
- 每条 growth event 必须有 Evidence。
- 投影重建等于增量结果。
- 相同 turn 重试不重复。
- 不同 subject 零交叉。

### 并发测试

- 两个 session 同时写不同候选，二者都保留。
- 同一 turn 并发重试只生成一份 Evidence。
- forget 与 recall 并发时，提交完成后的 recall 不得返回被忘记内容。

### 模型表达测试

模型测试不断言完整句子，断言：

- 只引用 `used_evidence_ids` 中的内容；
- 不引用 forgotten/topic-blocked Evidence；
- 普通回复引用数量不超限；
- 回望中的具体变化全部可解释；
- 证据不足时不生成成长评价。

---

## 9. 发布门槛

以下任一失败均阻止第一阶段发布：

- 跨账号或跨主体串记。
- 用户要求不记仍写入长期 Evidence。
- forgotten/topic-blocked 内容重新进入提示词。
- 纠正后新旧事实同时 active。
- 普通知识问答推动成长弧线或关系成熟度。
- Growth Arc 出现没有来源 Evidence 的具体结论。
- 相同 turn 重试生成重复事件。
- 并发写导致已确认记忆丢失。
- 服务重启后投影与重启前不一致。

第一阶段完成条件：

- 黄金故事全部通过。
- 关键禁止场景自动化测试 100% 通过。
- 账号与主体泄漏为 0。
- 遗忘后自动化召回为 0。
- C 语言回望的每条具体变化都能返回 Evidence。
- 全量 Xiaoxin 测试与需求工作台测试通过。

---

## 10. 风险与控制

### 风险：一次性重写导致当前记忆能力回退

控制：先建立 MemoryEngine seam，再按 Profile、C 语言成长、Episode、Relationship 顺序迁移；每次只迁移一个事实类别。

### 风险：新旧事实源长期双写产生分叉

控制：旧 JSON adapter 只用于迁移和短期兼容；为删除旧写路径设置明确 Task 13。

### 风险：TurnAnalysis 规则快速膨胀

控制：规则只负责高确定性和安全优先级；主题扩展必须先增加合同数据集，不在各投影中增加文本判断。

### 风险：向量召回提高召回率但绕过安全控制

控制：所有检索 adapter 只能在硬过滤之后排序候选，不能决定主体、状态和注入许可。

### 风险：保留遗忘历史与彻底删除冲突

控制：区分 forget 和 purge；purge 后只保留不含用户内容的墓碑和操作时间。

---

## 11. 推荐执行顺序与里程碑

### Milestone A：合同可信

完成 Task 0-3。

产出：统一类型、混合持久化、深 MemoryEngine seam，现有行为无回退。

### Milestone B：事实可信

完成 Task 4-7。

产出：事务化 Evidence、Profile 投影、C 语言可逆状态机、统一纠正和遗忘。

### Milestone C：召回可信

完成 Task 8-10。

产出：真实 Episode、统一硬过滤、主体成长资格和可靠回望。

### Milestone D：迁移与发布可信

完成 Task 11-13。

产出：旧数据迁移、黄金故事、全量回归、旧写路径删除和需求状态更新。

只有 Milestone D 完成后，才进入关系行为策略、主动关心、成长足迹和学年回顾。
