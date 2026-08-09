# 小芯陪伴记忆 V2 实施计划

日期：2026-07-18
时区：Asia/Shanghai（UTC+8）
对应设计：[小芯陪伴记忆 V2 设计规格](../specs/2026-07-18-xiaoxin-companion-memory-v2-design.md)

## 1. 结论与执行原则

本计划不是继续修补当前 `MemoryEngine + MemoryOrchestrator + JSON/JSONL + relationship_state`，而是实施一个新的深模块 `core.xiaoxin.companion.CompanionMind`，验证完成后一次性切换运行时、提示词、后台整理和控制入口，最后删除旧实现。

实施采用 14 个小提交。每个提交都必须具备：

- 先写会失败的 RED 测试；
- 只修改该切片列出的文件；
- 通过聚焦测试后才进入下一切片；
- 不长期双写、不建立第三套协调层；
- 不把 C 语言、竞赛或任何具体主题写进通用 schema、状态机或策略；
- 重要判断只保存安全依据，不保存模型内部推理；
- 实时对话不等待远程整理模型；
- 旧测试只有在新 `CompanionMind` 故事测试覆盖同等产品行为后才能删除。

最终目标状态：

```text
Connection / Control API / Background Loop
                    |
                    v
              CompanionMind
        prepare / commit / control
          project / run_due_work
                    |
     +--------------+---------------+
     |              |               |
 CompanionStore  CompanionPolicy  ReflectionModel
   SQLite          deterministic    adapter only
```

外部代码不得导入 `CompanionStore`、表名、内部 Validator 或整理 prompt。所有产品行为通过 `CompanionMind` 暴露。

## 2. 当前基线与必须消除的复杂度

当前实现已经确认存在以下事实：

- `main/xiaozhi-server/core/xiaoxin/runtime.py` 同时持有 `MemoryOrchestrator` 和 `MemoryEngine`；
- runtime 在 `trusted`、`legacy`、`split` 三种提交所有权之间切换；
- `main/xiaozhi-server/core/xiaoxin/prompts.py` 直接解释旧 `relationship_state`；
- `main/xiaozhi-server/core/api/xiaoxin_control_handler.py` 直接操作 `memory_dir` 和旧 JSON 文件；
- `main/xiaozhi-server/core/xiaoxin/turn_analysis.py` 与当前 Engine/Store 包含大量 C 语言专用判断；
- 身份、学生资料和 personal pet 已由 `xiaoxin_control.db` 管理；
- 小程序学生年级事实源是 `student_profiles.grade`；
- 同一微信主体的 personal pet 事实源是 `personal_pets`，设备和声纹只负责访问与身份确认；
- 已有 14 个 `test_memory*.py` 与旧结构或 C 语言垂直切片耦合；
- 当前不要求完整迁移旧记忆，可采用干净切换。

本次实施必须删除下列复杂度，而不是把它包进新 facade：

```text
MemoryOrchestrator
旧 MemoryEngine / EvidenceStore 的主题专用实现
trusted / legacy / split 提交所有权
profile / episodic / companion / growth arc / relationship 文件投影
prompt 对 relationship_state 的直接依赖
控制台对 memory_dir 的直接读写
C 语言专用成长状态机和固定话术规则
```

## 3. 目标目录与模块边界

新增目录：

```text
main/xiaozhi-server/core/xiaoxin/companion/
  __init__.py
  contracts.py
  mind.py
  store.py
  policy.py
  reflection.py
  controls.py
  projections.py
  worker.py
  adapters/
    __init__.py
    llm_reflection.py
```

职责：

- `contracts.py`：外部不可变数据合同、枚举和输入校验；
- `mind.py`：唯一外部 interface，编排本地事务、策略、控制、投影和后台工作；
- `store.py`：SQLite schema、事务、查询、不变量和 job claim；
- `policy.py`：确定性关系阶段、年龄形态、边界和行为策略；
- `reflection.py`：`ReflectionModel` Protocol、proposal schema 和 Validator；
- `controls.py`：重置、纠正、遗忘、边界和 purge 的事务语义；
- `projections.py`：voice、miniprogram、hardware、initiative、operator 投影；
- `worker.py`：session capsule、adjustment、chapter、失效重算的后台任务执行；
- `adapters/llm_reflection.py`：将现有 LLM adapter 约束为 `ReflectionModel`。

禁止为了“分层整洁”继续拆出只有一两个简单函数的 repository、service、manager、facade。SQLite 只有一个实现，直接在 `store.py` 中使用真实 SQLite；远程模型才需要 adapter seam。

## 4. 全局合同与固定命名

所有切片统一使用设计规格中的名称，不得自行改成新的近义词：

- 数据库：`data/xiaoxin_companion.db`；
- 外部 interface：`CompanionMind`；
- 主体上下文：`CompanionSubjectContext`；
- 实时策略：`CompanionPolicy`；
- 关系时期：`RelationshipEpoch`；
- 基础证据：`CompanionEvidence`；
- 会话整理：`SessionCapsule`；
- 相处调整：`CompanionAdjustment`；
- 年级阶段总结：`CompanionChapter`；
- 远程整理 seam：`ReflectionModel`；
- 主动决策：`InitiativeDecision`。

主要表名固定为：

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

关系阶段固定为：

```text
first_meeting
familiar
attuned
long_term_companion
```

年级标准化固定为：

```text
大一 -> freshman -> 1
大二 -> sophomore -> 2
大三 -> junior -> 3
大四 -> senior -> 4
空值或未知值 -> unknown -> null
```

## 5. 开发与提交门禁

每个切片开始前：

```powershell
git status --short
```

必须保留现有无关修改，不修改或提交 `.codex_tmp/`。每个切片结束至少执行：

```powershell
git diff --check
```

Python 命令默认在 `main/xiaozhi-server` 中执行；requirements 测试在仓库根目录执行。禁止在同一提交中混入自动格式化造成的大面积无关 diff。

测试方法：

- 行为测试通过 `CompanionMind`；
- Store 不变量使用真实临时 SQLite；
- Reflection 使用确定性 fake；
- 时间使用固定 Asia/Shanghai aware datetime；
- 不断言 LLM 完整自然语言，断言 policy、Evidence ID、状态、引用数量和禁止内容；
- 任何 RED 测试必须先证明因缺少目标行为而失败，不能因 import typo 或 fixture 错误失败。

---

## Slice 0：修正需求语义并冻结迁移基线

### 目标

先消除文档中仍把“小芯年龄”解释为真实陪伴年轮、把成长弧线解释为核心记忆层的旧语义，并冻结当前代码基线。否则后续实现会同时服从两份冲突需求。

### RED

在 `docs/requirements/test_requirements_workbench.py` 增加或更新合同测试，要求：

1. `CG-00`、`CG-03`、`CG-04`、`CG-05`、`CG-06`、`CG-07` 使用 V2 术语；
2. requirements 不再声明“小芯年龄按 companion_started_at 或陪伴年度计算”；
3. requirements 明确年级未知时 `xiaoxin_age=null`；
4. requirements 明确关系重置保留用户事实、停用关系记忆；
5. requirements 不把 C 语言或任一具体主题定义为通用记忆层。

先运行并记录失败结果：

```powershell
python -m pytest docs/requirements/test_requirements_workbench.py -q
```

### 修改文件

- `docs/requirements/requirements.yaml`
- `docs/requirements/test_requirements_workbench.py`
- `docs/product/domain-language.md`
- `docs/superpowers/specs/2026-07-18-xiaoxin-companion-memory-v2-design.md`

### 实现

- 将旧“陪伴年轮决定 1 岁/2 岁”改为“学生年级决定小芯年龄”；
- 保留真实相处时长，但不再用它推导年龄和共同经历；
- 将主题成长弧线降为可选索引；
- 将“关系成熟度”统一改为“关系阶段”，并说明不是裸数值等级；
- 将“学年回顾”改为证据充分时的陪伴章节投影；
- 将主动关心统一为有依据、低频、可关闭、有冷却和反馈闭环；
- 在 requirements 的 `remaining` 与 `acceptance` 中引用本设计和实施计划；
- 记录旧实现测试命令和当前通过数量，作为切换回归基线。

### 禁止扩展

- 不修改 Python 业务代码；
- 不顺带把 CG 项目标记为 implemented；
- 不声称小程序或硬件已实现 V2 投影。

### 验收

- requirements、领域词汇、设计规格三者不存在年龄事实源冲突；
- 搜索“陪伴年轮”只允许出现在历史说明或明确的旧语义清理上下文；
- 文档不再暗示大二首次使用者有两年共同经历。

### 验证

```powershell
python -m pytest docs/requirements/test_requirements_workbench.py -q
rg -n "陪伴年轮|companion_started_at|C 语言.*记忆层" docs/requirements docs/product docs/superpowers/specs
git diff --check
```

建议提交：

```text
docs: align companion memory v2 requirements
```

---

## Slice 1：建立 CompanionMind 合同与黄金故事骨架

### 目标

先定义唯一外部 seam 和产品故事，不实现数据库细节。后续任何实现若绕过该 seam，应由 import 或故事测试直接失败。

### RED

新增 `test_companion_mind_contract.py`，覆盖：

- 可以从 `core.xiaoxin.companion` 导入且只需导入 `CompanionMind` 与外部合同；
- `prepare_turn`、`commit_turn`、`apply_control`、`project`、`run_due_work` 签名符合设计；
- invalid/unknown speaker 不允许私人长期读写；
- prepared token 与 request digest 不匹配时提交失败；
- 同一 `turn_id + pet_id` 内容不同的二次提交被拒绝；
- C 语言和“室友关系”两种主题使用同一种 Evidence 合同，不需要主题枚举。

新增 `test_companion_mind_story.py`，先写 12 个待实现故事：

1. 大二首次使用为 2 岁、关系初见、共同经历为零；
2. 年级未填时年龄为空；
3. 明确边界立即生效；
4. 用户事实跨关系重置保留；
5. 旧共同经历跨关系重置停用；
6. 未知说话人零私人读写；
7. 删除依据使派生对象失效；
8. 模型失败不阻塞实时提交；
9. 两种主题无需修改 Store；
10. 主动陪伴遵守冷却与静默时段；
11. 三端投影的年龄和关系阶段一致；
12. 重启后状态可重建。

### 新增文件

- `main/xiaozhi-server/core/xiaoxin/companion/__init__.py`
- `main/xiaozhi-server/core/xiaoxin/companion/contracts.py`
- `main/xiaozhi-server/core/xiaoxin/companion/mind.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_mind_contract.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_mind_story.py`

### 实现

- 定义设计规格中的不可变 dataclass；
- 所有 ID、时间、枚举、confidence、scope 和状态在边界处验证；
- `PreparedCompanionTurn` 携带 opaque token、request digest、policy、prompt context 和 used evidence IDs；
- `CompanionTurnOutcome` 只接收用户可见结果、assistant action、delivery status 和安全反馈线索；
- `mind.py` 先定义构造与方法边界；尚未具备 Store 的行为必须明确 fail closed，不创建临时内存事实源；故事测试保留明确的分阶段标记；
- `__init__.py` 只导出外部合同和 `CompanionMind`，不导出 Store。

### 禁止扩展

- 不创建 SQLite schema；
- 不实现关键词抽取器；
- 不把旧 MemoryEngine 包装成 CompanionMind；
- 不在合同里出现 `trusted/legacy/split`、profile layer、growth arc layer 或 C 语言字段。

### 验收

- 调用者只知道五个方法；
- 合同可表达 user/relationship 两种 ownership；
- 同一接口可表达不同生活主题；
- 不暴露表名和文件路径。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_mind_contract.py -q
python -m compileall core/xiaoxin/companion
git diff --check
```

建议提交：

```text
feat: define companion mind contracts
```

---

## Slice 2：建立新的 SQLite CompanionStore

### 目标

以 `xiaoxin_companion.db` 建立唯一事务事实源，先完成 schema、幂等和数据不变量，不接 runtime。

### RED

新增 `test_companion_store.py` 与 `test_companion_store_concurrency.py`，覆盖：

- 初始化后存在设计规格中的 13 张表；
- `PRAGMA journal_mode=WAL`、`foreign_keys=ON`、`busy_timeout>=5000`；
- 每个 pet 最多一个 active epoch；
- relationship Evidence 必须关联同 pet 的 epoch；
- user Evidence 不要求 epoch；
- forgotten/superseded/expired Evidence 不能保持 `prompt_eligible=1`；
- 同一 `turn_id + pet_id` 幂等，相同 digest 返回 already committed，不同 digest 冲突；
- correction 生成不可变 supersession relation；
- job idempotency key 唯一；
- 两个 writer 并发提交不丢失更新；
- 事务中途异常不留下半个 turn、Evidence 或 job；
- 数据库重开后状态不变。

### 新增文件

- `main/xiaozhi-server/core/xiaoxin/companion/store.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_store.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_store_concurrency.py`

### 修改文件

- `main/xiaozhi-server/core/xiaoxin/companion/mind.py`
- `main/xiaozhi-server/core/xiaoxin/companion/contracts.py`

### 实现

- `CompanionStore` 初始化 schema 和索引；
- 采用显式事务，连接入口统一设置 PRAGMA；
- JSON content 使用稳定序列化和版本字段；
- 所有查询按 `pet_id`、`memory_subject_id`、`epoch_id` 和状态硬过滤；
- 提供 Mind 内部需要的事务方法，但不设计通用 CRUD repository；
- 存储 `source_ref` 和安全 `source_summary`，不复制完整聊天原文；
- 为 status、epoch、occurred_at、job due time 建立必要索引；
- 以数据库约束和事务校验共同守住跨 pet 引用。

### 禁止扩展

- 不导入旧 JSON；
- 不实现主题状态机；
- 不接远程模型；
- 不为 SQLite 增加假的 Repository Protocol。

### 验收

- 所有关键不变量在绕过 Mind 直接调用 Store 时仍能失败；
- 并发与重启测试通过；
- schema 中没有 `c_language`、`competition` 或其他主题列。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_store.py tests/xiaoxin/test_companion_store_concurrency.py -q
python -m compileall core/xiaoxin/companion
git diff --check
```

建议提交：

```text
feat: persist companion evidence transactionally
```

---

## Slice 3：实现关系时期和用户控制事务

### 目标

实现 relationship epoch、关系重置、纠正、遗忘、边界和 purge，并用保留矩阵固定语义。

### RED

新增 `test_companion_relationship_controls.py`，固定保留矩阵：

| 对象 | reset_relationship | purge_personal_memory |
|---|---|---|
| 学生资料 | 保留，仍由 identity DB 管理 | 保留，仍由 identity DB 管理 |
| 明确称呼/用户事实 | 保留 | 删除 Companion DB 中个人内容 |
| 明确边界 | 保留 | 按用户可见 purge 合同清除 |
| 用户成长事实 | 保留 | 删除 |
| 旧共同经历 | 停用 | 删除 |
| 旧 session capsule | 停用 | 删除 |
| 关系阶段 | 新 epoch 回到 first_meeting | 新 epoch 回到 first_meeting |
| 隐式偏好/自我调整 | 停用 | 删除 |
| 旧章节 | 停用 | 删除 |
| 账号、设备、pet 归属 | 保留 | 保留 |

测试还必须覆盖：

- reset 在单事务内关闭旧 epoch、创建一个新 epoch；
- 并发 reset 最终仍只有一个 active epoch；
- forget 立即取消 prompt eligibility；
- correct 使旧 Evidence superseded、新 Evidence active；
- set/revoke boundary 的优先级高于 inferred adjustment；
- 控制结果返回 retained/deactivated/forgotten/requeued 计数；
- 控制操作写入 `memory_controls`，日志不包含完整原文。

### 新增文件

- `main/xiaozhi-server/core/xiaoxin/companion/controls.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_relationship_controls.py`

### 修改文件

- `main/xiaozhi-server/core/xiaoxin/companion/mind.py`
- `main/xiaozhi-server/core/xiaoxin/companion/store.py`
- `main/xiaozhi-server/core/xiaoxin/companion/contracts.py`

### 实现

- `CompanionMind.apply_control()` 是唯一控制入口；
- `reset_relationship` 不物理删除旧 epoch，而是结束并退出 active 投影；
- `forget_evidence` 与 `forget_theme` 立即停止 prompt、initiative 和投影使用，并排队失效重算；
- `correct_evidence` 创建新 Evidence 和 supersession relation；
- `purge_personal_memory` 清除 Companion DB 的私人内容，仅保留不含内容的必要操作墓碑；
- 所有控制命令校验 pet/owner/subject 权限，禁止跨 owner 操作。

### 禁止扩展

- 不修改身份 DB 的用户、设备、学生资料或 pet；
- 不提供“手动设置关系阶段”命令；
- 不让 LLM 决定是否执行用户控制；
- 不实现小程序 UI。

### 验收

- reset 之后旧 relationship Evidence 在数据库中可审计但不再影响当前互动；
- 用户事实仍可召回；
- purge 的返回文案能准确说明保留账号、设备和学生资料；
- 并发 reset 无双 active epoch。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_relationship_controls.py tests/xiaoxin/test_companion_store.py -q
git diff --check
```

建议提交：

```text
feat: add companion relationship epochs and controls
```

---

## Slice 4：接入学生年级和 personal pet 主体上下文

### 目标

把身份系统现有事实准确转换为 `CompanionSubjectContext`，实现小芯年龄和真实相处时长的彻底分离。

### RED

新增 `test_companion_subject_context.py` 和 `test_companion_academic_stage.py`，覆盖：

- 大一/大二/大三/大四及常见安全格式标准化；
- 空字符串、未识别文本、研究生等未定义值返回 `unknown/null`，不得猜测；
- 大二首次对话：`xiaoxin_age=2`、`relationship_stage=first_meeting`、`shared_history_count=0`；
- personal pet ID 来自 `identity_store.get_personal_pet_for_user()`；
- pet 归微信 owner，不随设备切换；
- confirmed user speaker 可以持久化；
- device_unknown/device_fallback/解析失败不能私人读写；
- 年级更新只写 system Evidence 并安排章节边界工作，不重置 epoch；
- 对话中的“我大二了”不能直接覆盖结构化学生资料。

### 修改文件

- `main/xiaozhi-server/core/connection.py`
- `main/xiaozhi-server/core/xiaoxin/identity/store.py`
- `main/xiaozhi-server/core/xiaoxin/identity/models.py`
- `main/xiaozhi-server/core/xiaoxin/companion/contracts.py`
- `main/xiaozhi-server/core/xiaoxin/companion/mind.py`
- `main/xiaozhi-server/tests/xiaoxin/test_connection_integration.py`
- `main/xiaozhi-server/tests/xiaoxin/test_identity_store.py`

### 新增文件

- `main/xiaozhi-server/tests/xiaoxin/test_companion_subject_context.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_academic_stage.py`

### 实现

- 在合同层提供纯函数 `normalize_academic_stage()` 和 `xiaoxin_age_for_stage()`；
- `connection.py` 在身份解析成功后读取 owner 的 personal pet 和 student profile，构造 `CompanionSubjectContext`；
- `speaker_identity` 只使用 `confirmed/unknown/invalid`，不把设备绑定等同于确认说话人；
- profile update 后通过 Mind 写入幂等 `academic_stage_changed` system Evidence；
- 不复制 student profile 到 Companion DB，Evidence 只记录阶段变化和来源引用；
- 真实相处时长继续来自 personal pet 生命周期，仅在需要时作为独立投影字段，不参与年龄和共同经历推断。

### 禁止扩展

- 不修改小程序表单；
- 不从学号、入学年份、当前日期反推年级；
- 不让聊天文本直接改年级；
- 不把 `companion_started_at` 重新命名后继续当年龄。

### 验收

- 大二新用户不会获得大一章节、共同经历或更高关系阶段；
- 年级为空时各投影均为 `xiaoxin_age=null`；
- 换设备不换 pet；
- 未知说话人无私人长期读写。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_subject_context.py tests/xiaoxin/test_companion_academic_stage.py tests/xiaoxin/test_identity_store.py tests/xiaoxin/test_connection_integration.py -q
git diff --check
```

建议提交：

```text
feat: derive xiaoxin age from student grade
```

---

## Slice 5：实现确定性 CompanionPolicy

### 目标

把年龄、关系阶段、明确边界、自我调整、surface 限制和短期状态合成为结构化行为策略，不再让 LLM 解释裸关系等级。

### RED

新增 `test_companion_policy.py`，覆盖：

- 优先级严格为：核心人格/安全 > 明确边界 > surface 限制 > 年龄形态 > 关系阶段 > active adjustment > 短期状态；
- 明确“少问一点”可把 question budget 降为 0，隐式 adjustment 不得覆盖；
- first_meeting 不引用未经当前 epoch 验证的共同经历；
- familiar/attuned/long_term 的差异体现在预算和表达方式，不锁定基础问答、提醒和设备能力；
- 小芯年龄只影响表达形态与硬件强度，不直接升级关系；
- 普通知识问答 memory reference budget 为 0 或受限；
- 明确回望允许少量可解释 Evidence；
- 未知说话人返回中性策略并禁止私人引用；
- 配置阈值集中、版本化，不散落魔法数字。

### 新增文件

- `main/xiaozhi-server/core/xiaoxin/companion/policy.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_policy.py`

### 修改文件

- `main/xiaozhi-server/core/xiaoxin/companion/mind.py`
- `main/xiaozhi-server/core/xiaoxin/companion/contracts.py`
- `main/xiaozhi-server/core/xiaoxin/companion/store.py`

### 实现

- 以纯函数构造 `CompanionPolicy`；
- 关系阶段升级使用跨日期、有意义互动、可靠事实、有效反馈和完成 followup 的质量门槛；
- 原始 turn 数只可作为辅助最低样本数，不能单独升级；
- 年龄与关系阶段完全独立；
- 所有阈值由一个版本化策略配置对象集中管理；
- `prepare_turn` 先做硬过滤，再生成 policy 和受限 prompt context；
- policy 中输出 prohibited behaviors 和 hardware expression。

### 禁止扩展

- 不调用 LLM；
- 不建立用户心理画像；
- 不以具体主题关键词决定关系阶段；
- 不允许 API 手动指定关系阶段。

### 验收

- 给定同一存储状态和时间，policy 完全确定；
- 用户不看阶段数字，也能通过预算和行为约束感知差异；
- 大二初见仍是克制的 first_meeting 策略。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_policy.py tests/xiaoxin/test_companion_mind_contract.py -q
git diff --check
```

建议提交：

```text
feat: compute deterministic companion policy
```

---

## Slice 6：接入实时 XiaoxinRuntime 与提示词

### 目标

让 V2 路径在一次对话中完成 `prepare_turn -> LLM/local reply -> commit_turn`，且 prompt 只消费 `CompanionPolicy` 与安全 Evidence，不再直接读取旧 relationship state。

### RED

新增 `test_companion_runtime_integration.py`，覆盖：

- runtime 对 V2 路径只调用一个 `CompanionMind`；
- prepare 在生成回复前调用，commit 只在可见回复成功后调用；
- commit 不重新分析原始用户文本；
- local-rule 回复和普通 LLM 回复均遵守幂等 turn ID；
- LLM 失败时不提交“已完成的 assistant action”；
- SQLite 提交失败时回复可以返回，但结果明确 `memory_commit_failed`，不得说“我记住了”；
- prompt 不含 forgotten、expired、旧 epoch 或 topic-blocked Evidence；
- prompt 使用结构化 policy，不导入 `relationship_state`；
- existing tool route、提醒、IoT 和退出路径不被接管。

### 修改文件

- `main/xiaozhi-server/core/xiaoxin/runtime.py`
- `main/xiaozhi-server/core/xiaoxin/prompts.py`
- `main/xiaozhi-server/core/xiaoxin/types.py`
- `main/xiaozhi-server/core/connection.py`
- `main/xiaozhi-server/config.yaml`
- `main/xiaozhi-server/data/.config.yaml`
- `main/xiaozhi-server/tests/xiaoxin/test_runtime.py`
- `main/xiaozhi-server/tests/xiaoxin/test_full_runtime_contract.py`
- `main/xiaozhi-server/tests/xiaoxin/test_connection_integration.py`

### 新增文件

- `main/xiaozhi-server/tests/xiaoxin/test_companion_runtime_integration.py`

### 实现

- `XiaoxinConfig` 增加临时开发期开关和 `companion_db_path`；
- `XiaoxinRuntime` 接受可注入 `CompanionMind`，V2 开启时不调用旧系统；
- `build_system_messages()` 接受 voice projection 或 policy，不再接受裸 relationship dict；
- 对用户控制命令调用 `apply_control()`，控制结果可直接产生 local-rule 回复；
- 每轮只使用一份 prepared object；
- 将 commit status、evidence kinds、job IDs 和 policy version 写入结构化日志；
- V2 开启时绝不双写旧 JSON 或旧 SQLite。

### 禁止扩展

- 暂不删除旧模块；
- 不在本切片实现异步模型整理；
- 不改变工具路由、TTS 或设备协议；
- 不保留“V2 写、旧系统读”的混合路径。

### 验收

- V2 flag 开启时 runtime 只有一个写事实源；
- prompt 不直接调用 `relationship_state`；
- 实时路径没有远程 reflection 调用；
- 提醒和设备能力回归测试不受影响。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_runtime_integration.py tests/xiaoxin/test_runtime.py tests/xiaoxin/test_full_runtime_contract.py tests/xiaoxin/test_connection_integration.py -q
python -m pytest tests/xiaoxin/test_todo_reminder_scheduler.py tests/xiaoxin/test_course_reminder_scheduler.py -q
git diff --check
```

建议提交：

```text
feat: wire companion mind into xiaoxin runtime
```

---

## Slice 7：实现异步 job、ReflectionModel 和失败隔离

### 目标

建立可重试的后台整理通道。实时 commit 只排队，`run_due_work` 才允许调用远程模型。

### RED

新增 `test_companion_worker.py` 与 `test_companion_reflection.py`，覆盖：

- commit 成功后创建稳定 idempotency key 的 job；
- `run_due_work(limit=20)` claim due jobs，避免两个 worker 重复执行；
- 模型超时/异常/job 进程重启后可重试；
- 非法 schema 整批拒绝，不写半条 adjustment/chapter；
- proposal 只能引用 request 中给出的 Evidence ID；
- proposal 只能使用允许的 adjustment dimension；
- forgotten/superseded/expired Evidence 不能被 proposal 激活；
- 模型返回未提供依据的用户事实时 Validator 拒绝；
- 实时 prepare/commit 在 fake model 阻塞时仍可完成；
- 日志不输出完整 Evidence content 和用户原文。

### 新增文件

- `main/xiaozhi-server/core/xiaoxin/companion/reflection.py`
- `main/xiaozhi-server/core/xiaoxin/companion/worker.py`
- `main/xiaozhi-server/core/xiaoxin/companion/adapters/__init__.py`
- `main/xiaozhi-server/core/xiaoxin/companion/adapters/llm_reflection.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_worker.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_reflection.py`

### 修改文件

- `main/xiaozhi-server/core/xiaoxin/companion/mind.py`
- `main/xiaozhi-server/core/xiaoxin/companion/store.py`
- `main/xiaozhi-server/core/xiaoxin/control_runtime.py`
- `main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py`

### 实现

- 定义 `ReflectionModel` Protocol 与版本化 request/proposal；
- production adapter 复用现有 LLM adapter，但使用独立 prompt、超时和严格 JSON schema；
- `control_runtime.py` 的后台循环增加 companion work tick；
- claim 使用 lease/attempt/next_attempt_at，进程崩溃后可回收；
- 指数退避有上限，永久 schema 错误进入 failed，不无限重试；
- `run_due_work` 返回 claimed/succeeded/retried/failed 计数；
- 所有 proposal 经 Validator 后在单事务写入。

### 禁止扩展

- 不让 adapter 持有 Store；
- 不在实时线程调用 `reflect()`；
- 不保存 chain-of-thought；
- 不为每种主题写独立 reflection prompt。

### 验收

- 模型离线时聊天、提醒和设备功能仍正常；
- 重启后未完成 job 可继续；
- 非法提案零部分写入；
- 两个 worker 不重复生成派生对象。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_worker.py tests/xiaoxin/test_companion_reflection.py tests/xiaoxin/test_control_runtime.py -q
git diff --check
```

建议提交：

```text
feat: run companion reflection asynchronously
```

---

## Slice 8：实现 SessionCapsule 和小芯自我调整

### 目标

把“共同成长”首先落到有意义互动结果和可撤销相处调整，而不是主题进度条或人格标签。

### RED

新增 `test_companion_capsule_adjustment_story.py`，覆盖：

- 普通时间、天气、事实问答、工具调用和无结果闲聊不生成 capsule；
- 明确边界、有效反馈、完成 followup、被接受的帮助可生成 capsule；
- capsule 必须引用当前 epoch 的 Evidence；
- 单次隐含行为最多产生 candidate adjustment；
- 跨日期重复证据才允许 trial/active；
- 明确边界立即生效，无需等待 adjustment；
- 反证、用户纠正、Evidence 删除和关系重置可降级或撤销 adjustment；
- adjustment 只允许设计规格中的八个维度；
- “C 语言反馈”和“宿舍相处反馈”走同一代码路径；
- 过期规则分别覆盖 capsule 90 天、candidate 30 天、trial 60 天。

### 修改文件

- `main/xiaozhi-server/core/xiaoxin/companion/worker.py`
- `main/xiaozhi-server/core/xiaoxin/companion/reflection.py`
- `main/xiaozhi-server/core/xiaoxin/companion/store.py`
- `main/xiaozhi-server/core/xiaoxin/companion/policy.py`
- `main/xiaozhi-server/core/xiaoxin/companion/contracts.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_mind_story.py`

### 新增文件

- `main/xiaozhi-server/tests/xiaoxin/test_companion_capsule_adjustment_story.py`

### 实现

- worker 先确定是否存在有意义互动结果，再请求或执行 capsule 整理；
- candidate/trial/active 状态迁移由确定性 Validator 和 Store 事务决定；
- ReflectionModel 只提议，不直接决定激活；
- adjustment 必须保存 evidence_ids、confidence、generated_by 和有效期；
- policy 只读取 active adjustment；
- 删除 Evidence 时同步使相关 capsule/adjustment invalidated 并排重算 job；
- 保留安全解释摘要，不生成“你是某某类型的人”标签。

### 禁止扩展

- 不做主题成长状态机；
- 不把每轮聊天都压缩成 capsule；
- 不修改核心人格；
- 不让一个隐式信号永久改变行为。

### 验收

- 小芯行为改变可追溯、可撤销；
- 没有为了“显得有记忆”而保存普通闲聊；
- 两个不同主题无需新增 schema、枚举或分支；
- 关系重置后旧 adjustment 不再进入 policy。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_capsule_adjustment_story.py tests/xiaoxin/test_companion_policy.py tests/xiaoxin/test_companion_worker.py -q
git diff --check
```

建议提交：

```text
feat: consolidate sessions into companion adjustments
```

---

## Slice 9：实现陪伴章节与跨阶段回望

### 目标

按 academic stage 和 relationship epoch 生成证据充分的陪伴章节，替代固定主题成长层和空泛学年总结。

### RED

新增 `test_companion_chapter_story.py`，覆盖：

- 年级变化关闭旧 stage chapter、创建新 stage 工作，但不重置 epoch；
- 大二首次使用不能生成大一 chapter；
- chapter 只能引用当前 epoch 的共同经历和跨 epoch 保留的 user Evidence；
- user Evidence 被引用时不得写成“小芯和你共同经历过”；
- 证据不足时只显示阶段和已知设置，不生成成长评价；
- 删除 Evidence 使 chapter invalidated，重新整理后不再出现相关陈述；
- 关系重置使旧 chapter 退出当前投影；
- chapter version 可 supersede，不能原地静默改写历史；
- C 语言、社团、宿舍和家庭等内容只作为通用 Evidence，不改变章节算法；
- 回望中的每个具体陈述都能返回 Evidence IDs。

### 修改文件

- `main/xiaozhi-server/core/xiaoxin/companion/worker.py`
- `main/xiaozhi-server/core/xiaoxin/companion/reflection.py`
- `main/xiaozhi-server/core/xiaoxin/companion/store.py`
- `main/xiaozhi-server/core/xiaoxin/companion/contracts.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_mind_story.py`

### 新增文件

- `main/xiaozhi-server/tests/xiaoxin/test_companion_chapter_story.py`

### 实现

- chapter 以 `pet_id + epoch_id + academic_stage + version` 组织；
- 章节生成前做 ownership、epoch、status 和 attribution 校验；
- `safe_narrative` 只由已验证 proposal 产生；
- Evidence 不足时生成结构化 empty/insufficient result，不调用模型凑字数；
- 年级变化以 system Evidence 和 idempotent job 驱动；
- chapter 与 Evidence 通过 `chapter_evidence` 显式关联；
- project 的回望只返回少量 active chapter 和可解释变化。

### 禁止扩展

- 不实现“每年必须生成一篇报告”；
- 不把 chapter 当完整聊天摘要；
- 不生成下一年人生建议；
- 不把可选主题弧线恢复成核心表。

### 验收

- 大二初见只有 2 岁形态，没有虚构大一章节；
- 证据不足时系统选择沉默而不是鸡汤；
- 删除和重置能真实改变回望；
- 所有具体变化都有依据。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_chapter_story.py tests/xiaoxin/test_companion_relationship_controls.py tests/xiaoxin/test_companion_worker.py -q
git diff --check
```

建议提交：

```text
feat: build evidence-backed companion chapters
```

---

## Slice 10：实现主动陪伴和多端投影

### 目标

从同一事实源生成 voice、miniprogram、hardware、initiative 和 operator 投影，并实现有依据、低频、可关闭的主动陪伴决策。

### RED

新增 `test_companion_projections.py` 和 `test_companion_initiative.py`，覆盖：

- voice/miniprogram/hardware 三端对 `xiaoxin_age` 与 relationship stage 一致；
- 小程序只展示安全依据、边界、调整和章节摘要，不暴露内部画像；
- 硬件不包含原始 Evidence、工程计数和长篇 narrative；
- operator 可按 pet/subject/epoch/evidence/job 定位，但默认不含完整原文；
- 主动陪伴必须有 evidence_ids 和 reason code；
- 默认每天最多一次低优先级主动陪伴；
- 静默时段、用户关闭、设备不可用时不发起；
- 课程、待办和系统通知优先，不被陪伴消息挤占；
- 不允许纯随机“我想你了”；
- ignored/accepted/rejected/delivery_failed 都产生反馈 Evidence；
- rejected 后降低同类 initiative，不换句话立即重试；
- 旧 epoch 或 forgotten Evidence 不得触发主动陪伴。

### 新增文件

- `main/xiaozhi-server/core/xiaoxin/companion/projections.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_projections.py`
- `main/xiaozhi-server/tests/xiaoxin/test_companion_initiative.py`

### 修改文件

- `main/xiaozhi-server/core/xiaoxin/companion/mind.py`
- `main/xiaozhi-server/core/xiaoxin/companion/store.py`
- `main/xiaozhi-server/core/xiaoxin/companion/policy.py`
- `main/xiaozhi-server/core/xiaoxin/dispatcher.py`
- `main/xiaozhi-server/core/xiaoxin/control_runtime.py`
- `main/xiaozhi-server/tests/xiaoxin/test_dispatcher.py`
- `main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py`

### 实现

- `CompanionMind.project()` 根据 surface 返回类型化 projection；
- initiative 先做通知优先级、用户设置、静默、冷却、设备可用性和 Evidence 资格硬过滤；
- decision 先持久化再投递，投递结果回写；
- dispatcher 只消费 content brief 和 hardware expression，不读取 Store；
- miniprogram projection 提供后续控制 API 所需的稳定 Evidence ID；
- operator projection 提供 job/schema/prompt version 和 failure reason。

### 禁止扩展

- 本切片不实现小程序页面和固件动画；
- 不建立随机主动聊天；
- 不让关系阶段降低依据要求；
- 不把提醒、课程和待办复制到 Companion DB。

### 验收

- 五种 surface 使用同一个 `project()` seam；
- 主动陪伴每次都可解释、可冷却、可反馈；
- 硬件和小程序看不到不必要的私人内容；
- 通知优先级回归不受影响。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_projections.py tests/xiaoxin/test_companion_initiative.py tests/xiaoxin/test_dispatcher.py tests/xiaoxin/test_control_runtime.py -q
git diff --check
```

建议提交：

```text
feat: project companion growth across surfaces
```

---

## Slice 11：改造控制台与小程序服务端 API

### 目标

控制入口全部改为调用 `CompanionMind.project/apply_control`，不再读取旧 `memory_dir` 文件；第一阶段只改服务端 API，不改小程序界面。

### RED

新增 `test_companion_control_api.py`，更新控制 handler 测试，覆盖：

- 当前登录 owner 只能查看自己的 pet；
- 可列出安全 Evidence、来源摘要、状态、epoch 和派生对象引用；
- 可执行 forget、correct、set/revoke boundary、reset relationship 和 purge；
- 控制结果返回明确保留/停用/删除计数；
- reset 与 purge 的用户可见文案准确区分；
- API 不返回 chain-of-thought、完整内部画像和无必要原文；
- unknown/fallback subject 不能越权查看 owner 记忆；
- handler 不导入 `legacy_memory`、`subject_summary` 或调用 `_memory_dir()`；
- 旧 `/api/xiaoxin/legacy-memory` 在切换前标记 deprecated，切换时删除；
- miniprogram projection 可读取年龄、模糊关系阶段、最近变化和章节摘要。

### 修改文件

- `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`
- `main/xiaozhi-server/core/xiaoxin/control_runtime.py`
- `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`
- `main/xiaozhi-server/tests/xiaoxin/test_control_console_static.py`
- `docs/development/xiaoxin-control-console.md`
- `docs/development/xiaoxin-memory-integrity-audit.md`

### 新增文件

- `main/xiaozhi-server/tests/xiaoxin/test_companion_control_api.py`

### 实现

- handler 从 control runtime 获取同一 `CompanionMind` 实例；
- 设备入口先解析 owner/pet，再通过 operator projection 查询；
- 用户控制映射为类型化 `CompanionControlCommand`；
- 所有 ID 使用 opaque ID，不接受文件名或任意路径；
- 返回安全 source summary 和现有聊天记录引用，不返回模型 prompt；
- 增加小程序后续页面需要的只读 projection endpoint 与控制 endpoint；
- 保持现有鉴权和 owner 过滤合同。

### 禁止扩展

- 不在 handler 中拼 SQL；
- 不保留“新 API 写 SQLite、旧 API 写文件”的分叉；
- 不实现前端 UI；
- 不允许管理员因设备 ID 猜测跨 owner 读取。

### 验收

- handler 中无旧 memory file 直接操作；
- 所有控制通过 `CompanionMind`；
- API 足够支持后续小程序查看、纠正、删除和重置；
- 现有控制台非记忆功能不回退。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_control_api.py tests/xiaoxin/test_control_handler.py tests/xiaoxin/test_control_console_static.py -q
rg -n "legacy_memory|clear_subject_memory|forget_subject_memory|_memory_dir\(" core/api/xiaoxin_control_handler.py
git diff --check
```

建议提交：

```text
feat: expose companion memory controls through api
```

---

## Slice 12：干净切换并删除旧记忆系统

### 目标

完成 replace-not-layer：默认只运行 CompanionMind，删除旧实现、旧配置、旧结构性测试和开发期开关。

### RED

新增 `test_companion_cutover.py`，要求：

- `XiaoxinRuntime` 只有一个 `companion_mind`，不存在 `memory` 和 `trusted_memory`；
- runtime 源码中不存在 `trusted/legacy/split` 提交所有权；
- prompt 不导入 `relationship_state`；
- control handler 不导入旧 memory helper；
- config 不再包含 `memory_dir`；
- 启动和对话不会创建 profile/episodic/companion/growth/relationship JSON/JSONL；
- Companion 核心源码不包含 C 语言专用常量；
- 旧 `core.xiaoxin.memory` 无生产 import；
- 默认数据库路径为 `data/xiaoxin_companion.db`；
- 旧数据只读备份步骤有部署文档，不存在运行时 importer 或双写。

### 删除文件

- `main/xiaozhi-server/core/xiaoxin/memory/memory_orchestrator.py`
- `main/xiaozhi-server/core/xiaoxin/memory/profile_memory.py`
- `main/xiaozhi-server/core/xiaoxin/memory/episodic_memory.py`
- `main/xiaozhi-server/core/xiaoxin/memory/episode_index.py`
- `main/xiaozhi-server/core/xiaoxin/memory/companion_memory.py`
- `main/xiaozhi-server/core/xiaoxin/memory/growth_arc.py`
- `main/xiaozhi-server/core/xiaoxin/memory/growth_state.py`
- `main/xiaozhi-server/core/xiaoxin/memory/legacy_memory.py`
- `main/xiaozhi-server/core/xiaoxin/memory/relationship_state.py`
- `main/xiaozhi-server/core/xiaoxin/memory/memory_use_policy.py`
- `main/xiaozhi-server/core/xiaoxin/memory/engine.py`
- `main/xiaozhi-server/core/xiaoxin/memory/evidence_store.py`
- `main/xiaozhi-server/core/xiaoxin/memory/contracts.py`
- `main/xiaozhi-server/core/xiaoxin/memory/subject_summary.py`
- `main/xiaozhi-server/core/xiaoxin/memory/__init__.py`

删除或重写以下旧结构性测试：

- `main/xiaozhi-server/tests/xiaoxin/test_memory_engine.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_engine_zero_effects.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_growth_correction.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_growth_forget.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_growth_resolve.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_growth_story.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_milestone_story.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_pause_story.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_preferred_name_correction.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_restart_story.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_runtime.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_runtime_s3_attribution.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_runtime_trusted_ownership.py`
- `main/xiaozhi-server/tests/xiaoxin/test_memory_setback_story.py`

只有当对应 V2 故事覆盖行为后才删除。仍有产品价值的测试场景应迁移到 `test_companion_*`，不能机械删除断言。

### 修改文件

- `main/xiaozhi-server/core/xiaoxin/runtime.py`
- `main/xiaozhi-server/core/xiaoxin/prompts.py`
- `main/xiaozhi-server/core/xiaoxin/types.py`
- `main/xiaozhi-server/core/xiaoxin/turn_analysis.py`
- `main/xiaozhi-server/core/xiaoxin/fact_attribution.py`
- `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`
- `main/xiaozhi-server/config.yaml`
- `main/xiaozhi-server/data/.config.yaml`
- 与旧 memory imports 相关的测试文件

### 新增文件

- `main/xiaozhi-server/tests/xiaoxin/test_companion_cutover.py`

### 实现

- 移除临时 V2 flag，CompanionMind 成为默认且唯一实现；
- 从 `turn_analysis.py` 删除 C 语言专用成长判断，只保留与实时路由/安全相关的通用分析；
- 将 `fact_attribution.py` 对旧 profile helper 的依赖改为通用归属判断；
- 删除 `memory_dir` 配置，保留 `companion_db_path`；
- 删除 legacy memory API；
- 停止创建所有旧文件；
- 不编写旧数据语义 importer；部署前只做文件级只读备份。

### 禁止扩展

- 不保留旧模块“以防以后用”；
- 不做新旧双读比较；
- 不把 C 语言规则搬到 companion 目录；
- 不删除 identity 的 `memory_subject_id`，它仍负责说话人隔离。

### 验收

- 生产 import graph 中没有 `core.xiaoxin.memory`；
- runtime、prompt、控制台只有 CompanionMind；
- 新运行不产生旧文件；
- 所有有价值旧场景已在 V2 测试中表达。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_cutover.py tests/xiaoxin/test_companion_mind_story.py -q
rg -n "MemoryOrchestrator|MemoryEngine|relationship_state|trusted|split|memory_dir|growth_arc" core/xiaoxin core/api/xiaoxin_control_handler.py config.yaml data/.config.yaml
rg -n "C 语言|c_language|指针|链表" core/xiaoxin/companion
python -m compileall core/xiaoxin
git diff --check
```

建议提交：

```text
refactor: retire legacy xiaoxin memory system
```

---

## Slice 13：全量验收、部署切换与回滚文档

### 目标

证明 V2 不是“测试能跑的模块”，而是可以安全部署、观察和回滚的唯一服务端记忆系统。

### RED

完善 `test_companion_mind_story.py`，移除全部阶段性 skip/xfail，加入最终黄金故事：

```text
场景 A：大二首次使用
  资料 grade=大二
  -> 小芯 2 岁
  -> 关系 first_meeting
  -> 无大一共同经历

场景 B：宏观共同成长
  用户明确边界
  -> 小芯立即少追问
  多次跨日反馈表明短回答更合适
  -> adjustment candidate -> trial -> active
  用户之后要求恢复详细一点
  -> 旧 adjustment 被撤销

场景 C：关系重置
  已有姓名、用户成长事实、共同经历、隐式调整和章节
  -> reset
  -> 姓名和用户成长事实保留
  -> 旧共同经历、关系阶段、调整和章节停用

场景 D：模型离线
  ReflectionModel 超时
  -> 对话、提醒和设备功能继续
  -> job 可重试

场景 E：删除依据
  删除被 adjustment/chapter 引用的 Evidence
  -> 下一轮立即不再使用
  -> 后台重算后派生对象 invalidated/superseded

场景 F：主动陪伴
  有真实 Evidence 且满足冷却
  -> 生成 decision
  用户拒绝
  -> 同类主动程度降低且不立即重试
```

### 修改文件

- `main/xiaozhi-server/tests/xiaoxin/test_companion_mind_story.py`
- `main/xiaozhi-server/tests/xiaoxin/test_import_smoke.py`
- `main/xiaozhi-server/tests/xiaoxin/test_config_contract.py`
- `docs/operations/backup-and-upgrade.md`
- `docs/operations/troubleshooting.md`
- `docs/operations/xiaoxin-real-device-acceptance-ledger.md`
- `docs/development/runtime-paths.md`
- `docs/development/architecture.md`
- `docs/requirements/requirements.yaml`
- `docs/README.md`

### 实现

- 部署前备份 `data/xiaoxin_memory/` 与旧 `data/xiaoxin_memory/xiaoxin_memory.db`；
- 明确备份是只读归档，不在运行时导入；
- 首次启动创建 `data/xiaoxin_companion.db`；
- 健康检查包含 DB open、schema version、pending/failed job 数；
- 回滚步骤为停止新版本、保留新 DB、恢复旧代码和旧只读数据副本，禁止把 V2 Evidence 反向写回旧 JSON；
- 真机 smoke 覆盖确认说话人、未知说话人、重启、年级为空、大二初见、关系重置和提醒不受阻塞；
- requirements 只把确实完成且有测试证据的项目标记 implemented；
- 记录仍需真实数据校准的关系阈值、保留期、主动频率和硬件强度。

### 禁止扩展

- 不在上线前临时加入旧数据复杂迁移；
- 不因文档完成而把小程序 UI/固件投影标记完成；
- 不用单次人工演示代替自动化验收；
- 不在日志中开启完整用户原文。

### 最终验证命令

在 `main/xiaozhi-server`：

```powershell
python -m pytest tests/xiaoxin/test_companion_mind_contract.py -q
python -m pytest tests/xiaoxin/test_companion_store.py tests/xiaoxin/test_companion_store_concurrency.py -q
python -m pytest tests/xiaoxin/test_companion_relationship_controls.py tests/xiaoxin/test_companion_academic_stage.py -q
python -m pytest tests/xiaoxin/test_companion_policy.py tests/xiaoxin/test_companion_runtime_integration.py -q
python -m pytest tests/xiaoxin/test_companion_worker.py tests/xiaoxin/test_companion_reflection.py -q
python -m pytest tests/xiaoxin/test_companion_capsule_adjustment_story.py tests/xiaoxin/test_companion_chapter_story.py -q
python -m pytest tests/xiaoxin/test_companion_projections.py tests/xiaoxin/test_companion_initiative.py -q
python -m pytest tests/xiaoxin/test_companion_control_api.py tests/xiaoxin/test_companion_cutover.py -q
python -m pytest tests/xiaoxin/test_companion_mind_story.py -q
python -m pytest tests/xiaoxin -q
python -m compileall core/xiaoxin
```

在仓库根目录：

```powershell
python -m pytest docs/requirements/test_requirements_workbench.py -q
git diff --check
git status --short
```

静态门禁：

```powershell
rg -n "MemoryOrchestrator|MemoryEngine|relationship_state|trusted|split|memory_dir" main/xiaozhi-server/core/xiaoxin main/xiaozhi-server/core/api/xiaoxin_control_handler.py
rg -n "C 语言|c_language|指针|链表" main/xiaozhi-server/core/xiaoxin/companion
rg -n "陪伴年轮|companion_started_at.*年龄|按陪伴.*1 岁|按陪伴.*2 岁" docs/product docs/requirements docs/superpowers/specs docs/superpowers/plans
```

预期：前三条代码搜索无生产命中；C 语言搜索无 Companion 核心命中；旧术语只允许出现在历史问题或明确禁止语境。

建议提交：

```text
docs: finalize companion memory v2 rollout
```

---

## 6. 里程碑与停止条件

### Milestone A：领域和事实源可信

完成 Slice 0-4。

产出：统一词汇、CompanionMind 合同、新 SQLite、关系时期、控制语义、年级和 pet 主体上下文。

停止条件：如果无法证明大二初见不虚构大一共同经历，不得进入策略和运行时接线。

### Milestone B：实时行为可信

完成 Slice 5-7。

产出：确定性 policy、实时 runtime、异步 reflection 和失败隔离。

停止条件：如果任何远程整理调用仍位于实时回复路径，或 V2 开启时仍双写旧系统，不得进入成长整理。

### Milestone C：成长与主动行为可信

完成 Slice 8-10。

产出：session capsule、自我调整、陪伴章节、主动陪伴和多端投影。

停止条件：如果具体判断没有 Evidence、删除后仍可召回，或主题需要修改 schema，不得进入切换。

### Milestone D：唯一实现可信

完成 Slice 11-13。

产出：统一控制 API、旧系统删除、全量测试、部署与回滚手册。

停止条件：只要生产 import graph、配置、控制台或 prompt 仍依赖旧记忆系统，就不能宣称 V2 完成。

## 7. 发布阻断条件

以下任一情况阻止发布：

- 跨 owner、pet、subject 或 epoch 串记；
- unknown/invalid speaker 读取或写入私人长期记忆；
- 年级未知被显示为 1 岁；
- 大二首次使用被描述为有大一共同经历；
- 关系重置删除姓名和用户成长事实，或继续使用旧共同经历；
- forgotten/superseded/expired/旧 epoch Evidence 进入 prompt、主动陪伴或章节；
- correction 后新旧事实同时 active；
- 无 Evidence 的 adjustment/chapter 被激活；
- LLM 整理失败阻塞聊天、提醒或设备功能；
- 主动陪伴没有依据、违反静默/冷却，或拒绝后立即换话术重试；
- V2 开启时仍写旧 JSON/JSONL 或旧 SQLite；
- Companion 核心出现 C 语言或其他具体主题状态机；
- 相同 turn 重试生成重复 Evidence；
- 并发写或并发 reset 破坏数据库不变量；
- 控制 API 暴露完整内部画像、模型推理或跨 owner 数据。

## 8. 完成定义

只有同时满足以下条件，服务端第一阶段才算完成：

- `XiaoxinRuntime` 只持有一个 `CompanionMind`；
- `CompanionMind` 五个方法覆盖实时、控制、投影和后台工作；
- 学生年级是小芯年龄唯一事实源，未知为 null；
- personal pet 由微信主体拥有，设备和说话人只负责访问；
- user Evidence 与 relationship Evidence 的保留语义通过自动化测试；
- relationship epoch 重置、并发和审计合同通过；
- policy 确定、版本化、受边界约束；
- session capsule、adjustment 和 chapter 均有 Evidence；
- ReflectionModel 只异步运行，失败可重试；
- 主动陪伴有依据、冷却、静默和反馈闭环；
- voice、miniprogram、hardware 和 operator 投影来自同一事实源；
- 控制台不操作文件，用户可纠正、删除和重置；
- 旧 memory 包、旧配置、旧 API 和旧结构性测试已删除；
- 聚焦测试、Xiaoxin 全量测试、requirements、compileall 和静态门禁全部通过；
- 部署、备份、观察和回滚步骤可执行；
- `.codex_tmp/` 和其他无关工作区文件没有进入提交。

## 9. 后续阶段，不属于本计划

服务端 V2 完成后，再分别规划：

1. 小程序低存在感陪伴设置、二级记忆与隐私入口、依据查看、纠正、删除、关系重置和上下文主动反馈；成长通过对话回顾与可关闭轻量卡片表达，不建设常驻阶段页；
2. 固件年龄形态、表情动作和轻量阶段事件消费；
3. 基于真实用户数据校准关系门槛、保留期、主动频率和静默默认值；
4. 在通用 Evidence 之上的可选主题索引，但任何主题索引都不得重新成为记忆层；
5. 数据导出、账户注销和更严格的数据保留策略。

这些工作不能反向改变本计划的核心不变量：年龄不等于相处时长，关系阶段不等于聊天次数，主题不等于记忆层，AI 提议不等于事实。
