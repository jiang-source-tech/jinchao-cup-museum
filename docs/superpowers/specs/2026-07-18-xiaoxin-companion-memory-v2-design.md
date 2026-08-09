# 小芯陪伴记忆 V2 设计规格

日期：2026-07-18
时区：Asia/Shanghai（UTC+8）
状态：产品语义与目标架构已确认，等待实施

## 1. 结论

小芯陪伴记忆 V2 不在现有 `MemoryEngine + MemoryOrchestrator + JSON/JSONL + relationship_state` 上继续叠加功能，而是建立一个新的深模块 `CompanionMind`，通过单一 interface 承担实时记忆、关系时期、行为策略、异步整理、用户控制和多端投影。

迁移采用 replace-not-layer：新模块通过验收后一次性切换运行时、prompt 和控制入口，旧记忆实现停止读写并被删除。当前不要求完整保留历史用户记忆，因此不建设复杂的旧数据语义迁移器。

具体课程或主题不得进入通用存储和状态机。C 语言只可作为测试样例或可选识别器，不是记忆层，也不能继续出现在通用 Engine、SQLite schema、关系策略和用户控制合同中。

## 2. 当前问题

当前服务端同时运行两套记忆实现：

- `core/xiaoxin/memory/engine.py` 与 `evidence_store.py` 组成 SQLite 可信 Evidence 路径；
- `MemoryOrchestrator` 继续读写 profile、episodic、companion、growth arc 和 relationship JSON/JSONL；
- `XiaoxinRuntime` 必须在 `trusted`、`legacy`、`split` 三种提交所有权之间切换；
- prompt 直接读取旧 `relationship_state`；
- 控制台记忆查看、清理和定向遗忘直接操作旧 `memory_dir`；
- C 语言固定句、状态判断和数据库约束进入通用 Engine 与 Store；
- 关系等级主要由互动次数和成长事件数量决定，尚未形成确定性行为策略；
- 当前 `companionYear` 按真实绑定时间计算，与本规格确认的“按当前年级映射小芯年龄”冲突。

这些问题不能通过增加第三个编排层解决。V2 必须统一事实源、调用 seam 和用户控制。

## 3. 目标

V2 必须实现：

1. 一个微信主体拥有一个个人小芯，换设备不产生新宠物；
2. 学生资料、用户事实与当前关系时期分开建模；
3. 小芯年龄由学生资料中的当前年级决定，年级未知时年龄为空；
4. 大二首次使用者获得 2 岁形态，但关系仍为初见且没有大一共同经历；
5. 核心人格稳定，个人小芯可根据真实相处结果形成可撤销的自我调整；
6. 不要求用户逐条确认记忆，但长期行为判断必须可解释、可纠正、可删除；
7. 陪伴成长以有意义时刻、相处学习、自我调整和陪伴章节为主，主题成长弧线仅为可选索引；
8. 实时对话只执行本地事务，AI 整理全部异步，失败不阻塞聊天、提醒或设备能力；
9. 关系重置保留用户事实和明确边界，停止使用旧共同经历、隐式偏好和自我调整；
10. 短期细节衰减，少量真实意义进入长期章节；
11. 服务端产生语音、小程序、硬件和主动陪伴所需的一致投影；
12. 所有运行时和主要测试通过同一个 `CompanionMind` interface 操作记忆。

## 4. 非目标

本阶段不做：

- 为所有生活领域编写主题状态机；
- 让 LLM 直接修改数据库、人格或关系状态；
- 把小芯年龄当作真实陪伴时长或关系等级；
- 因年龄或关系阶段锁定基础问答、提醒和设备能力；
- 完整迁移现有 JSON、JSONL 和 `xiaoxin_memory.db` 语义；
- 第一阶段同时改完小程序和固件；
- 保存或展示模型内部推理；
- 把完整聊天流水当作长期陪伴记忆；
- 允许未知说话人读取或改变主人的私人记忆。

## 5. 核心领域关系

```text
微信主体
  └─ 个人小芯
      ├─ 学生资料
      ├─ 用户事实
      ├─ 明确边界
      ├─ 当前关系时期
      │   ├─ 有意义时刻
      │   ├─ 会话胶囊
      │   ├─ 关系阶段
      │   ├─ 小芯自我调整
      │   └─ 陪伴章节
      └─ 多个设备与可信说话人入口
```

个人小芯归微信主体所有。设备和声纹只用于访问与身份确认。`memory_subject_id` 继续负责说话人隔离，但不承担宠物生命周期、学生年龄或关系阶段的事实源职责。

## 6. 三条独立轴线

| 轴线 | 事实源 | 含义 |
|---|---|---|
| 小芯年龄 | 学生资料 `grade` | 大一到大四映射 1 岁到 4 岁 |
| 相处时长 | 首次实际绑定或互动时间 | 双方真实认识多久 |
| 关系阶段 | 当前关系时期内的有效互动和反馈 | 小芯应该怎样陪用户 |

年级标准化：

```text
大一 -> freshman -> 1
大二 -> sophomore -> 2
大三 -> junior -> 3
大四 -> senior -> 4
空值或未知值 -> unknown -> null
```

普通对话和 LLM 不得直接改变年龄。年级只能由结构化学生资料驱动。年级变化写入系统 Evidence，并安排旧阶段章节关闭和新阶段投影生成。

大二首次使用者的合法状态：

```json
{
  "academic_stage": "sophomore",
  "xiaoxin_age": 2,
  "relationship_stage": "first_meeting",
  "shared_history_count": 0
}
```

任何投影不得由 `xiaoxin_age = 2` 推导出“两年共同经历”。

## 7. 记忆归属

每条长期 Evidence 必须声明 `ownership_scope`。

### 7.1 user

归微信主体所有，跨关系重置保留：

- 结构化学生资料的安全投影；
- 用户明确给出的稳定事实；
- 用户明确偏好；
- 用户明确边界；
- 用户自身经历和成长事实；
- 主动频率、静默时段和表达方式的明确设置。

`user` Evidence 只说明用户具有或表达过该事实，不自动说明小芯共同参与过该经历。

### 7.2 relationship

只属于一个关系时期：

- 具体互动结果；
- 会话胶囊；
- 共同经历；
- 隐式相处偏好；
- followup；
- 关系阶段贡献；
- 小芯自我调整；
- 陪伴章节。

关系重置后，旧时期的 `relationship` Evidence 和派生投影不再进入当前 prompt、主动陪伴或硬件行为。

## 8. Evidence 合同

```python
@dataclass(frozen=True)
class CompanionEvidence:
    evidence_id: str
    pet_id: str
    memory_subject_id: str
    ownership_scope: Literal["user", "relationship"]
    relationship_epoch_id: str | None
    kind: EvidenceKind
    content: Mapping[str, object]
    source_kind: SourceKind
    source_ref: str
    source_summary: str
    attribution: FactAttribution
    confidence: float
    occurred_at: str
    retention: Retention
    status: EvidenceStatus
    prompt_eligible: bool
    expires_at: str | None
```

关键不变量：

- `relationship` Evidence 必须带当前 `relationship_epoch_id`；
- `user` Evidence 不依赖关系时期；
- 未确认主人、未知说话人和 owner 缺失时禁止个人长期写入；
- `forgotten`、`superseded`、`expired` Evidence 永不进入 prompt；
- `source_summary` 是安全、简短依据，不保存模型内部推理；
- 原始聊天记录由聊天记录模块管理，Companion DB 不重复保存完整原文；
- 主题只允许作为可选标签或派生索引，不参与 schema 枚举和核心状态验证；
- 同一 `turn_id` 的提交必须幂等，内容不一致时拒绝。

建议 `EvidenceKind`：

```text
profile_fact
explicit_preference
explicit_boundary
user_life_event
interaction_feedback
assistant_action
short_term_state
meaningful_moment
system_event
```

## 9. 关系时期

每个个人小芯最多只有一个 active relationship epoch：

```python
@dataclass(frozen=True)
class RelationshipEpoch:
    epoch_id: str
    pet_id: str
    started_at: str
    ended_at: str | None
    start_reason: str
    end_reason: str | None
```

关系重置在一个事务中完成：

1. 关闭当前 epoch；
2. 创建新 epoch；
3. 关闭旧 followup；
4. 使旧胶囊、旧章节和旧隐式调整退出 active 投影；
5. 保留 `user` Evidence 和明确控制；
6. 生成不可变 `relationship_reset` 控制记录；
7. 返回逐类保留和停用数量。

关系重置不是账号删除，也不是清空所有个人数据。

## 10. 会话胶囊

只有存在有意义互动结果的会话才生成胶囊：

```python
@dataclass(frozen=True)
class SessionCapsule:
    capsule_id: str
    pet_id: str
    relationship_epoch_id: str
    evidence_ids: tuple[str, ...]
    safe_summary: str
    interaction_outcome: str
    adjustment_signals: tuple[str, ...]
    status: CapsuleStatus
    created_at: str
    expires_at: str | None
```

时间、天气、普通事实问答、工具调用和无结果闲聊不得为了增加陪伴感而强行生成胶囊。

## 11. 小芯自我调整

核心人格不可变，自我调整只改变个人小芯与当前用户的相处方式。

允许的维度：

```text
response_length
question_frequency
initiative_level
memory_reference_depth
emotional_posture
humor_level
closure_style
hardware_expression_intensity
```

```python
@dataclass(frozen=True)
class CompanionAdjustment:
    adjustment_id: str
    pet_id: str
    relationship_epoch_id: str
    dimension: AdjustmentDimension
    value: str
    scope: str
    status: Literal["candidate", "trial", "active", "superseded", "expired", "revoked"]
    evidence_ids: tuple[str, ...]
    confidence: float
    generated_by: str
    created_at: str
    valid_until: str | None
```

用户明确边界立即生效，不需要 AI 推断。单次隐含行为最多生成 candidate；跨时间重复证据才允许进入 trial 或 active。相反证据、用户纠正、关系重置和 Evidence 删除必须能够降级或撤销调整。

## 12. 陪伴章节

陪伴章节围绕学生阶段和当前关系时期整理，不要求绑定具体主题：

```python
@dataclass(frozen=True)
class CompanionChapter:
    chapter_id: str
    pet_id: str
    relationship_epoch_id: str
    academic_stage: str
    xiaoxin_age: int | None
    period_start: str
    period_end: str | None
    evidence_ids: tuple[str, ...]
    shared_moment_ids: tuple[str, ...]
    adjustment_ids: tuple[str, ...]
    safe_narrative: str
    status: Literal["draft", "active", "superseded", "invalidated"]
    version: int
```

章节只允许使用当前 epoch 中的共同经历和跨 epoch 保留的 `user` 事实。引用 `user` 事实时不得写成共同经历。证据不足时只输出校园阶段和已知设置，不生成空泛成长评价。

## 13. CompanionMind 深模块

外部 seam 位于 `core.xiaoxin.companion.CompanionMind`。调用者和主要测试只使用以下 interface：

```python
class CompanionMind:
    def prepare_turn(self, request: CompanionTurnRequest) -> PreparedCompanionTurn:
        ...

    def commit_turn(
        self,
        prepared: PreparedCompanionTurn,
        outcome: CompanionTurnOutcome,
    ) -> CompanionCommitResult:
        ...

    def apply_control(self, command: CompanionControlCommand) -> CompanionControlResult:
        ...

    def project(self, request: CompanionProjectionRequest) -> CompanionProjection:
        ...

    def run_due_work(self, *, now: str, limit: int = 20) -> CompanionWorkResult:
        ...
```

interface 不暴露 Store、表名、文件路径、关系计数、主题规则或 LLM prompt。

### 13.1 prepare_turn

同步、本地、无远程模型调用：

- 验证主体和权限；
- 读取当前年级与小芯年龄；
- 读取 active relationship epoch；
- 召回受限 Evidence；
- 合成确定性 `CompanionPolicy`；
- 返回 prompt context 和 opaque prepared token。

### 13.2 commit_turn

只在用户可见回复成功生成后调用：

- 验证 prepared token 与 request digest；
- 幂等记录 turn；
- 提交明确 Evidence 和短期状态；
- 记录 assistant action 与反馈线索；
- 安排异步整理任务；
- 不等待 ReflectionModel。

### 13.3 apply_control

统一处理：

```text
reset_relationship
forget_evidence
forget_theme
correct_evidence
set_boundary
revoke_boundary
purge_personal_memory
```

每个结果明确列出保留、停用、遗忘和重新整理的对象数量。

### 13.4 project

通过一个 interface 输出 `voice`、`miniprogram`、`hardware`、`initiative` 和 `operator` 投影。

### 13.5 run_due_work

由后台循环调用，负责会话胶囊、自我调整、章节和失效重算。它可以调用 ReflectionModel adapter，但不参与实时回复。

## 14. 主体上下文

运行时传入已经解析好的身份和学生上下文：

```python
@dataclass(frozen=True)
class CompanionSubjectContext:
    owner_user_id: str
    pet_id: str
    memory_subject_id: str
    speaker_identity: Literal["confirmed", "unknown", "invalid"]
    academic_stage: Literal["freshman", "sophomore", "junior", "senior", "unknown"]
    persistence_allowed: bool
```

未知说话人可以进行会话级交互，但不得读取或写入私人长期记忆，也不得改变关系阶段和自我调整。

## 15. CompanionPolicy

LLM 不再接收裸关系等级并自行解释。服务端生成：

```python
@dataclass(frozen=True)
class CompanionPolicy:
    xiaoxin_age: int | None
    relationship_stage: str
    response_length: str
    question_budget: int
    memory_reference_budget: int
    initiative_level: str
    emotional_posture: str
    closure_style: str
    prohibited_behaviors: tuple[str, ...]
    hardware_expression: Mapping[str, object]
```

优先级：

```text
核心人格与安全约束
> 用户明确边界
> surface 能力限制
> 小芯年龄形态
> 当前关系阶段
> active 自我调整
> 当前短期状态
> LLM 最终措辞
```

初始关系阶段：

```text
first_meeting
familiar
attuned
long_term_companion
```

关系升级必须同时具备跨日期互动、可靠事实、有效反馈或完成跟进等质量门槛。原始 turn 次数和小芯年龄不能单独升级关系。

## 16. ReflectionModel seam

ReflectionModel 是唯一需要单独 adapter 的远程依赖：

```python
class ReflectionModel(Protocol):
    def reflect(self, request: ReflectionRequest) -> ReflectionProposal:
        ...
```

生产使用现有 LLM adapter，测试使用确定性 fake adapter。SQLite 使用真实临时数据库测试，不增加假想 Repository seam。

ReflectionProposal 必须：

- 使用版本化 schema；
- 只引用请求中提供的 Evidence ID；
- 只能使用允许的调整维度；
- 不包含原始 chain-of-thought；
- 不得声明未提供依据的用户事实；
- 不得绕过 Validator 直接写入。

模型失败、超时或输出无效时，job 保持可重试状态，实时功能继续运行。

## 17. 多时间尺度整理

```text
Turn Evidence
  -> Session Capsule
  -> Periodic Consolidation
  -> Academic Stage Chapter
```

每轮只记录明确事实、边界、反馈和短期状态，不形成宏观人格结论。会话后只为有意义互动生成胶囊。周期整理负责衰减弱 candidate、激活或撤销自我调整、选择有意义时刻和重算被删除 Evidence 影响的派生对象。年级变化关闭旧阶段章节并生成新年龄形态，但不改变当前关系时期。

## 18. 保留与衰减

初始建议：

| 类型 | 初始规则 |
|---|---|
| 学生资料、明确边界、明确设置 | 持久，直到用户修改或撤销 |
| short_term_state | 72 小时内过期 |
| 普通 session capsule | 90 天后退出 active 召回，除非被章节或调整引用 |
| adjustment candidate | 30 天无强化则过期 |
| trial adjustment | 60 天内重新验证 |
| active adjustment | 持久但持续接受反证和用户纠正 |
| user_life_event | 保留历史，是否进入 prompt 由相关性和状态决定 |
| companion chapter | 持久，允许 supersede、invalidate 或关系重置停用 |

这些数值是可配置起点，不是产品真理；上线后通过真实使用数据校准。

## 19. 主动陪伴

服务端输出：

```python
@dataclass(frozen=True)
class InitiativeDecision:
    should_initiate: bool
    reason_code: str
    evidence_ids: tuple[str, ...]
    priority: str
    cooldown_until: str | None
    content_brief: str
    hardware_expression: Mapping[str, object]
```

默认每天最多一次低优先级陪伴主动；静默时段、用户关闭或设备不可用时不发起；课程、待办和系统通知优先；禁止纯随机“我想你了”；忽略、接受、拒绝和投递失败均形成反馈 Evidence。

## 20. 多端投影

### VoiceProjection

- `CompanionPolicy`；
- 受限安全记忆；
- 当前年龄与关系阶段；
- 禁止行为；
- 本轮硬件表情建议。

### MiniProgramProjection

- 学生资料驱动的小芯年龄；
- 模糊关系阶段；
- 最近一条可解释变化；
- 用户可见边界和调整；
- 陪伴章节摘要；
- 纠正、删除和关系重置入口。

### HardwareProjection

- 年龄形态；
- 表情、动作和强度；
- 轻量阶段事件；
- 不包含原始画像、工程计数或长篇章节。

### OperatorProjection

- job、失败原因和 schema 版本；
- 按 pet、subject、epoch 和 Evidence 定位故障；
- 不暴露不必要的私人原文。

## 21. SQLite 设计

新数据库建议为：

```text
data/xiaoxin_companion.db
```

身份、学生资料和 personal pet 继续由 `xiaoxin_control.db` 管理。Companion DB 保存稳定 `pet_id` 和 `owner_user_id` 引用，但不复制身份系统。

主要表：

```text
companion_turns
companion_evidence
evidence_relations
relationship_epochs
session_capsules
capsule_evidence
companion_adjustments
adjustment_evidence
companion_chapters
chapter_evidence
memory_controls
consolidation_jobs
initiative_decisions
```

关键约束：

- 每个 pet 最多一个 active relationship epoch；
- relationship Evidence 的 epoch 必须属于同一 pet；
- active 派生对象引用的 Evidence 必须未 forgotten、未 superseded、未 expired；
- `forgotten` 对象不得 `prompt_eligible=1`；
- job 使用稳定 idempotency key；
- turn 使用 `turn_id + pet_id` 唯一键和内容 digest；
- correction 使用不可变 supersession relation；
- 删除 Evidence 后通过 job 失效或重建全部派生对象；
- WAL、foreign keys、busy timeout 和事务回滚强制开启。

Companion DB 不保存完整聊天原文。需要解释时使用安全 `source_summary` 和现有聊天记录引用。

## 22. 用户控制语义

### reset_relationship

保留学生资料、小芯年龄、明确称呼、明确边界、明确提醒偏好、用户成长事实以及账号和设备归属。停用旧共同经历、会话胶囊、关系阶段、followup、隐式偏好、小芯自我调整和陪伴章节。

### forget_evidence

标记目标 Evidence forgotten，立即禁止 prompt 使用，失效相关胶囊、调整和章节，并安排重新整理。

### correct_evidence

旧 Evidence superseded，新 Evidence active，保存不可变替代关系，不允许新旧值同时生效。

### purge_personal_memory

清除全部陪伴和成长记忆，但账户、设备绑定和必要审计由身份与运维合同单独处理。用户可见文案必须明确保留了什么。

## 23. 失败模式

| 失败 | 行为 |
|---|---|
| 身份解析失败 | 禁止私人读写，允许无记忆会话降级 |
| SQLite 写失败 | 当前回复可返回，但不得伪报已记住 |
| ReflectionModel 超时 | job 重试，不影响实时对话 |
| ReflectionProposal schema 错误 | 整批拒绝 |
| Evidence 引用失效 | 派生对象不得激活 |
| 年级为空或未知 | `xiaoxin_age=null`，使用中性初见形态 |
| 关系重置并发 | 单事务保证只有一个新 active epoch |
| 同轮重复提交 | 返回 already committed，不重复 Evidence |
| 未知说话人 | 不读取或写入私人长期记忆 |
| 主动陪伴投递失败 | 记录失败，不增加接受度和关系贡献 |

## 24. 可观察性

记忆提交至少记录 `turn_id`、`pet_id`、`memory_subject_id`、`relationship_epoch_id`、`commit_status`、`evidence_kinds`、`control_action`、`job_ids` 和 `policy_version`。

后台 job 至少记录 `job_id`、`job_kind`、`idempotency_key`、`attempt`、`status`、`model`、`prompt_version`、`schema_version` 和 `failure_reason`。日志默认不写完整用户原文和完整 Evidence content。

## 25. 测试 seam

主要行为测试通过 `CompanionMind` interface，使用真实临时 SQLite 和 fake ReflectionModel。必须覆盖：

1. 大二首次使用为 2 岁但关系初见；
2. 年级未知时年龄为空；
3. 关系重置保留 user Evidence、停用旧 relationship Evidence；
4. 未知说话人零私人读写；
5. 明确边界立即优先生效；
6. 无 Evidence 的 AI 调整提案被拒绝；
7. 模型失败不阻塞实时对话；
8. 删除 Evidence 使相关章节和调整失效；
9. 两种不同生活主题无需修改 Store 和状态机；
10. 并发、幂等、事务回滚和重启重建；
11. 主动陪伴遵守冷却、静默时段和拒绝反馈；
12. 语音、小程序和硬件投影对年龄与关系状态一致。

旧浅模块测试在新的 interface 故事测试覆盖同等行为后删除，不保留两套结构性测试。

## 26. 切换策略

当前不要求完整保留历史记忆，因此采用干净切换：

1. 备份 `data/xiaoxin_memory/` 和旧 `xiaoxin_memory.db`；
2. 创建新的 `xiaoxin_companion.db`；
3. 将 runtime、prompt 和控制入口切换到 `CompanionMind`；
4. 禁止旧系统读写；
5. 运行聚焦、全量和真机 smoke；
6. 保留旧数据只读归档一段时间；
7. 删除旧模块、旧配置和旧测试。

禁止长期双写。允许开发阶段短暂存在代码级 feature switch，但部署切换后只能有一个写事实源。

## 27. 完成定义

V2 服务端第一阶段完成必须满足：

- `XiaoxinRuntime` 只持有一个 `CompanionMind`；
- runtime 中不存在 `trusted/legacy/split` 提交所有权；
- prompt 不再直接依赖 `relationship_state`；
- 控制台不再直接操作旧 `memory_dir` 文件；
- 不再生成 profile、episodic、companion、growth arc 和 relationship JSON/JSONL；
- Companion 核心代码中不存在 C 语言或其他具体主题常量；
- 年级未知不伪造 1 岁；
- 大二首次使用不伪造大一共同经历；
- 关系重置保留矩阵通过自动化故事；
- 重要长期判断可解释、可纠正、可删除；
- AI 整理失败不影响聊天、提醒和设备功能；
- 主动陪伴有依据、冷却和用户反馈闭环；
- 多端投影使用同一服务端事实源；
- 新 interface 测试、Xiaoxin 全量测试、requirements、compileall 和静态检查全部通过；
- 旧记忆实现和结构性测试已经删除，而不是仅停用。

## 28. 需要真实数据校准的参数

以下参数不阻塞架构实施，但上线后必须通过真实使用观察调整：

- 关系阶段质量门槛；
- session capsule 默认保留期；
- adjustment candidate 和 trial 的衰减时间；
- 主动陪伴频率和静默时段默认值；
- 不同年龄形态的硬件动作强度；
- 陪伴章节的信息密度和用户可见粒度。

这些参数必须配置化并带版本，不能散落为主题关键词和 prompt 魔法数字。
