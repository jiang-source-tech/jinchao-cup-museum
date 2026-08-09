# 小芯个体人格与长期成长 V1 设计规格

日期：2026-07-25
时区：Asia/Shanghai（UTC+8）
状态：规格已冻结，生产实现尚未开始
决策来源：[小芯个体人格与四年成长决策地图](../plans/2026-07-25-xiaoxin-individuality-growth-decision-map.md)

## 1. 结论

本规格把“新生入学开始养小芯，每个人养出的性格不同，并在四年中逐渐成长”定义为一个可验证的产品合同：所有个人小芯共享同一个核心身份、能力和安全边界，但每个 `pet_id` 拥有终身稳定的出生气质；小芯在真实相处中只形成有 Evidence、局部、可撤销的表达调整；年龄、关系阶段、关系姿态和短期 VA 分别改变成熟表达、关系包络和当下状态。

生产实现必须扩展现有 `core.xiaoxin.companion.CompanionMind`。禁止新建 `PersonalityEngine`、平行人格数据库、自由文本人格 Prompt 或第二套成长事实源。现有 `prepare_turn`、`commit_turn`、`observe`、`apply_control`、`project` 和 `run_due_work` 继续是唯一产品边界。

当前项目已经具备 CompanionMind、SQLite Store、Evidence、relationship epoch、Adjustment、Chapter、GrowthMoment、主动陪伴和多端投影骨架。本规格不是重做 2026-07-18 的 CompanionMind，也不重复执行旧创建计划，而是在现有实现上补齐个体人格和四年成长。

## 2. 产品承诺

V1 必须同时满足以下承诺：

1. **仍是同一个小芯**：核心身份、基础能力、事实可靠性、安全规则和声线不因个体化而变化。
2. **出生时已经不同**：同一输入下，不同 `pet_id` 可以稳定表现出不同的探索、节奏、组织、玩心和主动偏好。
3. **差异可以解释**：任何差异都能归因到出生气质、合格相处调整、年龄成熟表达、关系包络、互动契约、当前场景或 VA，不依赖每轮随机噪声。
4. **相处后确实会变**：只有具体行为在跨日 Evidence 支撑下形成局部调整；没有合格变化时不得制造“成长感”。
5. **四年不是四个等级**：大一到大四只迁移处理问题和表达陪伴的重心，不解锁能力，也不保证越长大越长、越主动或越亲密。
6. **用户始终能控制**：明确边界和设置立即优先；纠正、撤销、恢复默认表达、重新磨合与清空个人记忆具有互不混淆的语义。
7. **短期状态不会变成人格**：VA 只影响分钟到小时尺度的当下表达，自动回归基线，不累计为亲密度、性格或成长等级。
8. **真实设备上仍成立**：服务端、语音、小程序与 ESP32 使用同一策略事实，重启、重连和重复投递不能造成漂移、串用或重复表达。

## 3. 非目标

V1 不建设：

- MBTI、固定性格类型、人格分数或用户人格画像；
- 用户可反复抽取、选择或编辑出生气质的界面；
- 外观、服装、道具、经验值、签到或任务化升级；
- 因年龄、关系或气质解锁知识、工具、提醒和设备能力；
- 以全量聊天、自由文本总结或模型内部推理作为人格事实源；
- 让用户维护五轴仪表盘、Evidence 计数或候选/试行状态；
- 让 VA 镜像用户情绪、制造“小芯受伤”或索取用户安慰；
- V1 的“重新养一只小芯”入口；
- 以留存率、聊天次数或单次演示代替长期辨识和正确性验证。

## 4. 当前基线与生产差距

### 4.1 已有且必须复用

| 能力 | 当前生产基线 |
|---|---|
| 唯一深模块 | `core.xiaoxin.companion.CompanionMind` |
| 外部操作 | `prepare_turn`、`commit_turn`、`observe`、`apply_control`、`project`、`run_due_work` |
| 数据库 | `CompanionStore`，当前 schema version 13 |
| 长期依据 | `companion_evidence` 与统一 Evidence 生命周期 |
| 关系 | `relationship_epochs` 与四个 relationship stage |
| 相处派生 | `companion_adjustments`、`companion_chapters`、`companion_growth_moments` |
| 主动陪伴 | opportunity、decision、冷却、投递和用户结果闭环 |
| 多端 | voice、miniprogram、hardware、initiative、operator 投影 |
| 用户控制 | Evidence 纠正/遗忘、边界、关系重置和个人记忆清除基础 |

### 4.2 尚未实现

| 缺口 | 当前表现 | V1 目标 |
|---|---|---|
| 出生气质 | 无生产 schema 和稳定生成 | 五轴语义枚举按 `pet_id` 一次生成并持久化 |
| 本轮表达 | `CompanionPolicy v3` 无 `expression_style` | 增加结构化、确定性的表达投影 |
| 隐式磨合 | Adjustment 已有通用生命周期，但资格过宽 | 增加具体行为、场景、三日晋级与反向学习合同 |
| 年龄表达 | 粗略 `age_expression` 和数量型变化 | 使用冻结的六维成熟矩阵 |
| 非标准学业 | 任意 stage 变化语义混合 | revision、状态、转换类型和纠正语义分离 |
| 长期关系 | 当前累计门槛可在约 14 天达到长期 | 最短 14/90/365 天、分布门槛、单调阶段与短期姿态 |
| 四年叙事 | 仅大一到大二成长时刻完整 | 阶段、周年、毕业、跳级、纠正和合并窗口完整覆盖 |
| VA | 只有隔离原型 | 定点状态、幂等事件、跨重启衰减和受限投影 |
| 用户体验 | 原型已验证，真实端未接入 | A 概览 + B 自然纠正 + C 二级控制页 |
| 发布证据 | 有研究方案，无生产研究工具与 HIL 结果 | 自动化、HIL、D7/D30/D90 顺序门禁 |

所有隔离原型都只是决策证据，不进入服务器运行时，不读取生产数据库，也不代表对应需求已经实现。

## 5. 单一所有权边界

```text
Connection / Control API / Background Loop / Overview Sync
                            |
                            v
                      CompanionMind
       prepare / commit / observe / control / project / due work
                            |
          +-----------------+------------------+
          |                 |                  |
   CompanionStore    deterministic policy   model adapters
      SQLite          and projections       proposals only
```

关键约束：

- 只有 CompanionMind 可以组合人格、记忆、关系、年龄、VA 和用户控制；
- Store 负责事实、不变量、事务、租约、幂等和主体隔离，不决定自然语言；
- `policy.py` 负责纯确定性合成，相同结构化输入得到相同策略和原因码顺序；
- 远程模型只能提出受验证候选或消费最终策略，不能直接写气质、关系、VA 坐标或数据库；
- 小程序与硬件只消费投影，不在端侧重新计算年龄、关系、气质或 VA；
- 不要求为了文件整齐新增 `projections.py`。当前投影归属可以继续保留在 `mind.py` 与 `policy.py`，只有复杂度真实增长时才拆分。

## 6. 个体人格模型

个体化使用四层输入，最终只产生一个本轮策略：

```text
核心身份与能力（不可竞争的硬不变量）
  + 出生气质（终身稳定默认倾向）
  + 当前关系时期的相处调整（局部一档、可撤销）
  + 年龄 / 关系阶段 / 关系姿态 / VA（本轮允许范围）
  + 互动契约 / 场景 / 负反馈 / 端侧 / 安全（继续收紧）
  = CompanionPolicy
```

### 6.1 五轴语义枚举

领域合同必须保存每个轴自己的语义枚举，禁止统一持久化为 `low / medium / high`。

| 轴 | 字段 | 合法值 | 只负责 |
|---|---|---|---|
| 探索取向 | `exploration_orientation` | `focused / balanced / exploratory` | 思路向外展开的倾向 |
| 表达活力 | `expression_energy` | `calm / natural / lively` | 语言节奏与动力 |
| 思路组织 | `thought_organization` | `intuitive / balanced / structured` | 已有内容怎样排列 |
| 玩心倾向 | `playfulness` | `restrained / lighthearted / playful` | 本轮幽默候选 |
| 陪伴主动性 | `companion_initiative` | `reserved / timely / proactive` | 合格主动机会的排序偏好 |

五轴正交，全部 243 种组合合法。`reserved` 不等于冷淡，`lively` 不等于话多，`playful` 不等于幼稚，`structured` 不等于输出长篇，`proactive` 不增加主动权限。

建议内部合同：

```python
@dataclass(frozen=True)
class BirthTemperament:
    pet_id: str
    generator_version: str
    exploration_orientation: ExplorationOrientation
    expression_energy: ExpressionEnergy
    thought_organization: ThoughtOrganization
    playfulness: Playfulness
    companion_initiative: CompanionInitiative
    generated_at: str
    source_kind: Literal["pet_created", "legacy_backfill"]
```

### 6.2 稳定生成

每个轴独立计算：

```text
digest = SHA-256("xiaoxin-temperament-v1", pet_id, axis_key)
bucket = digest[0]
0..63   -> 该轴第一档
64..191 -> 该轴中档
192..255 -> 该轴第三档
```

每轴分布为 25% / 50% / 25%。生成只用于首次构造与一致性审计：

- 新 `personal_pet` 创建时生成并冻结；
- legacy pet 只允许按原 `pet_id` 一次 `legacy_backfill`，记录真实回填时间；
- 运行时从持久化记录读取，禁止算法升级后重算覆盖；
- 换设备、解绑、重启、关系重置和清空个人记忆都保留原值；
- 只有真正创建新 `pet_id` 才生成新气质；
- 用户资料、VA、聊天历史和全局配额不参与 seed。

### 6.3 本轮表达风格

`CompanionPolicy` 增加非持久化 `expression_style`：

```python
@dataclass(frozen=True)
class CompanionExpressionStyle:
    exploration_orientation: ExplorationOrientation
    expression_energy: ExpressionEnergy
    thought_organization: ThoughtOrganization
    humor_level: HumorLevel
    initiative_bias: InitiativeBias
```

`expression_style` 只承载本轮最终结果，不是出生气质副本。玩心编译为 `humor_level`；主动性编译为机会排序偏好，现有 `initiative_level` 继续表示硬权限上限。`response_length`、`question_budget`、`memory_reference_budget` 和硬件能力都不能被五轴绕过。

### 6.4 气质显露

| 关系阶段 | 允许显露 |
|---|---|
| `first_meeting` | 表达活力和思路组织可初步可辨；压住探索、玩心和主动 |
| `familiar` | 放开探索和玩心；主动最多到 `timely` |
| `attuned` | 五轴可在硬边界内完整表达 |
| `long_term_companion` | 不继续增强，只强调一致性和有依据的磨合 |

显露由既有关系阶段控制，不新增气质经验值、显露百分比或聊天次数状态机。

## 7. 确定性策略合成

策略合成先产生个体化候选，再对每个维度求所有约束共同允许的更严格结果。优先级不是简单覆盖顺序：

1. 身份、主体隔离、事实可靠性和安全为不可变硬门禁；
2. 端侧能力、用户互动契约、明确边界和当前负反馈继续收紧；
3. 当前场景、关系姿态和关系阶段限制亲密度、记忆与主动范围；
4. 年龄提供成熟表达方式，不提供权限；
5. VA 在剩余空间内调整当下姿态、节奏与硬件建议；
6. 出生气质提供默认倾向；
7. active 相处调整只在具体行为与场景内相对出生气质偏移相邻一档。

策略决策轨迹只包含固定原因码、受影响维度和非私人枚举结果。它不保存聊天原文、私人 Evidence、安全摘要全文、模型推理或自由文本用户标签；不进入 Prompt，不作为长期记忆。相同输入的策略、轨迹内容和原因码顺序必须一致。

## 8. 后天相处调整

### 8.1 Evidence 资格

隐式调整只接受：已确认本人、当前 relationship epoch、针对小芯某个具体行为和具体场景的一手可核对反馈。

以下只允许成为观察线索，不能贡献晋级日期：泛化点赞、笼统 helpful、完成 followup、单纯高频互动。

以下必须拒绝：unknown speaker、用户事实、第三方转述、假设、玩笑、引用、ASR 不确定内容、短期情绪、模型推断、旧 epoch、与具体行为无关的结果。模型置信度不能改变资格。

### 8.2 三日晋级

同一 `behavior_key + context_scope + direction`：

```text
第 1 个合格上海日期 -> candidate（不进入策略）
第 2 个合格上海日期 -> trial（不进入策略）
第 3 个合格上海日期 -> active（进入策略）
```

同日最多贡献一次。candidate 30 天、trial 60 天未继续验证则 `expired`。active 不因沉默自动衰减。明确设置、边界与纠正立即生效，不等待三日。

### 8.3 一档上限与冲突

- active 调整相对对应出生轴最多移动相邻一档；
- 多条 Evidence 和调整不得叠加突破一档；
- 调整必须限定具体行为和场景，禁止生成覆盖出生气质的“有效人格”；
- 同一行为与场景同一时刻最多一条 active 调整；
- 明确互动契约替代同一行为的隐式调整，并且优先级更高。

### 8.4 反向学习、撤销与重算

- 单次相反隐式信号只发起或推进 challenge，不立即翻转；
- 相反方向跨日重复后，先终止旧 active 并回到出生基线；
- 新方向必须从 candidate 独立完成三日晋级；
- 用户明确纠正立即撤销目标调整，不等待 challenge；
- `superseded`、`expired`、`revoked` 为不可变终态，不能原地复活；
- Evidence 遗忘或过期后，旧派生记录终止，排除失效 Evidence 后按剩余 0/1/2/3 个合格日期不重建/candidate/trial/active 新记录；
- 恢复默认表达、关系重置和明确撤销形成控制边界，旧 Evidence 不得跨边界自动复活。

## 9. 年龄成熟表达与学业路径

年龄只来自权威学生资料 `academic_stage`：freshman/sophomore/junior/senior 对应 1/2/3/4 岁，未知为 `null`。完整六维合同见[四年年龄成熟表达矩阵](../plans/2026-07-25-xiaoxin-age-maturity-matrix.md)。

| 年龄 | 行为重心 | 提问 | 问题组织 | 旧线索 | 主动姿态 | 硬件节奏 |
|---|---|---|---|---|---|---|
| 1 | 陪用户开始尝试 | `exploratory` | `action_seed` | `concrete_cue` | `light_invitation` | `quick_single` |
| 2 | 稳定承接目标与条件 | `clarifying` | `bounded_plan` | `progress_continuity` | `contextual_followup` | `steady_sequence` |
| 3 | 梳理方案与代价 | `tradeoff` | `option_tradeoff` | `evidence_comparison` | `decision_point` | `deliberate_sequence` |
| 4 | 提炼判断、风险与边界 | `judgment_check` | `principle_risk` | `revisable_long_view` | `restrained_acknowledgement` | `restrained_single` |

这些值由 academic stage 派生，不单独持久化，不由模型写入。年级未知使用年龄中性表达，但保留其他合法个体化。

非标准路径必须遵守[学业路径转换矩阵](../plans/2026-07-25-xiaoxin-academic-path-transition-matrix.md)：

- 分离 `academic_stage`、`academic_status`、`transition_kind`、`effective_at` 和 `source_revision`；
- 中途加入直接使用当前年龄，关系仍从初见开始，不补写缺失年份；
- 跳级只进入真实目标阶段，不补中间章节；
- 留级、休学和延毕不增加年龄；毕业后冻结最后确认形态，不产生 5 岁；
- 转专业本身不改变年龄；真实回退可改变当前形态，但中性表达，不叙述为退化；
- 资料纠正使错误派生读模型失效并重建，不产生成长仪式；
- 乱序或旧 revision 不得让已确认年龄来回跳动；明确清除年级才回到中性；
- 账号合并只能显式选择一只 pet 迁移所有权，禁止合并两只宠物的气质、关系、Evidence、章节或调整。

## 10. 长期关系节奏与当前姿态

关系阶段保持四档，并在同一 epoch 内单调晋级。V1 候选最短跨度：

| 目标阶段 | 最短跨度 |
|---|---:|
| `familiar` | 14 天 |
| `attuned` | 90 天 |
| `long_term_companion` | 365 天 |

晋级还必须同时满足跨日/周/月分布、可靠知识、按上海日期去重的已确认帮助和正向磨合、近期健康窗口。等待、消息量、年龄和 VA 都不能单独贡献晋级。阈值配置必须版本化，并在真实数据中校准；校准不能删除最短跨度、分布和 Evidence 门禁。

历史阶段与当前关系姿态分离：

| 姿态 | 进入 | 行为 | 恢复 |
|---|---|---|---|
| `steady` | 默认 | 使用阶段正常包络 | 不适用 |
| `reunion_cautious` | familiar/attuned/long-term 分别 30/60/120 天无互动 | 关闭主动、最多一条记忆，调整增益 0.5/0.75/1.0 | 三个不同返回日期渐进恢复 |
| `repairing` | 有效负反馈 | 关闭记忆引用、主动和隐式调整，暂停晋级 | 明确正向反馈或三个无新增负反馈互动日期 |

沉默、负反馈与遗忘不降低历史阶段；关系重置是唯一回到初见的操作。遗忘只收紧当前记忆资格。

## 11. 四年叙事与成长时刻

流程固定为：

```text
结构化叙事边界
  -> Evidence 派生 CompanionChapter
  -> 一次性 CompanionGrowthMoment
  -> voice / miniprogram / hardware 受限投影
```

叙事边界只能来自权威学业变化、真实陪伴周年与毕业。模型不能创造边界。章节至少需要 2 条有效 Evidence、至少 1 条 `shared_experience`、跨 2 个上海日期，最多选择 3 条。

- 学业/毕业无合格章节时可生成不声明共同历史的 `boundary_only`；
- 周年只有时间经过时只保留生命周期事实，有合格章节才生成仪式；
- 只有周年可与 30 天内一个学业或毕业边界合并；两个学业边界不能互相吞并；
- 学业、周年、毕业候选窗口分别为 30、14、90 天，过期不补播；
- 成长时刻只能附着在下一次合适的普通对话，不能主动推送或挤占设备任务；
- `reunion_cautious` 和 `repairing` 禁止认领；
- voice 最多 1 至 2 句并最多引用 1 个共同锚点；miniprogram 最多 3 条安全摘要；hardware 只接收低强度、短时长语义；
- `pending -> reserved -> expressed` 使用租约保证失败可重试、成功不重复；关闭回顾变为 `suppressed`，不可补播；
- 遗忘导致章节重建或时刻降级/失效，认领期间必须释放旧 turn；已表达内容不能收回，但未来投影必须移除失效依据。

## 12. VA 短期状态

### 12.1 坐标与动力学

- V/A 使用 `[-1000, 1000]` 千分位定点整数，对外语义范围 `[-1.000, +1.000]`；
- 基线固定为 `V=+150, A=0`；
- 基础 Valence/Arousal 半衰期为 90/35 分钟，并除以版本化 `recovery_rate`；
- 合法事件先按真实经过时间衰减，再以 `event_strength * reactivity / inertia` 的受限比例向目标靠近；
- 不使用无限累加增量；最后合法事件 6 小时后精确回基线，读取不续期。

V1 事件只允许：`shared_success`、`confirmed_help`、`ordinary_chat`、`user_low_mood`、`negative_feedback`。事件只保存受控枚举，远程模型不能写目标、强度、坐标或衰减参数。用户低落和负反馈都强制在非负 V、低 A 的支持/收住区间，禁止委屈和索取安慰。

### 12.2 归属、幂等与持久化

快照归属为 `pet_id + memory_subject_id + relationship_epoch_id`，只保存坐标、版本、状态时间、最后事件时间和动力学元数据，不保存聊天原文或用户情绪标签。

- 重启后校验主体和 epoch，再按墙上时间继续衰减；
- 不匹配、损坏、未来或过期快照失败关闭到基线；
- 重复事件不得二次推进，乱序事件不得回卷状态；
- 权威事件幂等凭据必须跨快照 TTL 和关系重置保留；
- 重新磨合与清空个人记忆立即回基线；
- 设备低电量与网络异常只覆盖硬件输出，不写回 VA。

### 12.3 受限投影

VA 只影响现有 `emotional_posture`、语音节奏和 `hardware_expression`。年龄与关系通过版本化 `reactivity`、`recovery_rate`、`expression_gain`、`inertia` 改变曲线，不直接设置坐标。VA 不创建主动机会，不改变问题/记忆预算，不参与年龄或关系晋级，不写回气质、Evidence 或 Adjustment。

## 13. 用户控制与多端职责

用户体验采用已经验证的组合：A 状态概览主页面 + B 对话内自然纠正 + C 二级“相处与记忆”控制页。

| 入口 | 职责 | 禁止 |
|---|---|---|
| 语音/聊天 | “只纠正这次”、具体行为反馈、发起长期设置确认 | 直接执行清空或关系重置；展示工程状态 |
| 小程序概览 | 最近可解释变化、低存在感陪伴设置入口 | 人格五轴仪表盘、升级进度 |
| 二级相处与记忆 | 安全摘要、设置、单项撤销、恢复默认、重新磨合、清空个人记忆 | 要求用户日常维护画像 |
| 硬件 | 表现最终语义结果 | 执行高风险控制；保存人格事实 |
| operator | Evidence、轨迹、租约、迁移和健康诊断 | 向学生暴露私人原文或模型推理 |

控制保留矩阵：

| 操作 | pet/出生气质 | 用户事实与契约 | 关系与共同经历 | 隐式调整 | VA |
|---|---|---|---|---|---|
| 撤销单项调整 | 保留 | 保留 | 保留 | 仅目标项 revoked | 保留 |
| 恢复默认表达 | 保留 | 保留 | 保留 | 当前 epoch 全部 revoked | 保留 |
| 重新磨合 | 保留 | 保留 | 旧 epoch 停用，新 epoch 初见 | 旧调整退出 | 回基线 |
| 清空个人记忆 | 保留 | Companion DB 中删除，身份库资料保留 | 删除并新建初见 epoch | 全部删除 | 回基线 |
| 新建 pet（V1 无入口） | 新生成 | 不继承旧宠物关系内容 | 不继承 | 不继承 | 新基线 |

学生侧只看到具体行为、适用范围和“相处中学会/你明确设置”等来源摘要。禁止显示五轴档位、出生 seed、Evidence 数量、置信度、候选/试行、关系工程指标和原始 VA。

## 14. 数据、迁移与并发不变量

生产实现必须满足：

- 每个 `pet_id` 恰有一条出生气质；legacy backfill 原子、幂等、可审计；
- schema 迁移前创建 SQLite 在线备份，迁移后 `integrity_check=ok` 且 `foreign_key_check` 无违规；
- 旧 pet 回填不能改变 `companion_started_at`、relationship epoch 或 Evidence；
- 所有私有读取和写入同时校验 owner、pet、memory subject 和需要时的 epoch；
- adjustment 晋级、challenge、重算与终态转换在事务中完成；
- stage promotion、posture 恢复、growth moment claim 和 VA event 使用稳定幂等键；
- 并发调用不能产生两条 active epoch、同 scope 多条 active adjustment、重复 growth moment 或 VA 二次推进；
- 不长期双写旧/新人格表示，不把新数据反向写入旧格式；
- rollback 通过发布开关停用新投影，保留可迁移数据，不重抽出生气质；
- 时间计算使用 aware datetime；日期去重使用 Asia/Shanghai，衰减使用真实经过秒数。

## 15. Prompt 与模型边界

Prompt 只接收最终 `CompanionPolicy` 的用户不可见结构化约束和少量合格记忆上下文：

- 不注入随机人格传记、出生 seed、五轴解释长文或决策轨迹；
- 不让模型决定关系晋级、调整晋级、VA 数值、Evidence 资格或成长时刻认领；
- 不要求模型逐字输出固定人格口头禅；
- 自动化断言结构化策略、引用 ID、原因码和禁止内容，不断言完整自然语言；
- 模型离线或非法输出时保持事实、安全和确定性状态，不能回退到无约束随机人格。

## 16. 验证与发布门槛

完整合同见[小芯个体辨识与长期成长验证方案](../research/2026-07-25-xiaoxin-longitudinal-individuality-validation.md)。发布顺序固定：

```text
L0 静态与确定性门禁
 -> L1 隔离服务端集成
 -> L2 真实 ESP32 HIL
 -> L3 D7 初始辨识
 -> L4 D30 磨合对照
 -> L5 D90 持续成长
```

核心门槛：

- 243 种五轴组合合法，相同输入重放 20 次策略和原因码 100% 一致；
- 跨主体、虚假共同经历、遗忘后复活、边界绕过、重复表达等 P0 为 0；
- HIL 每条关键路径连续 30 次无身份串用、重复表达和旧状态复活，并完成 24 小时长稳；
- D7/D30/D90 总辨识率至少 65%/70%/75%，同时满足预注册 CI 和 style-only 门槛；
- D90 至少 60 名有效完成者，且至少 60% 答对 12 题中的 9 题；
- 记忆引用精确率至少 98%，参与者聚类 95% CI 下界至少 95%；
- 虚假事实、跨主体、已删除内容引用为 0；
- 主动陪伴“不想收到”和高监控感均受点估计与单侧 CI 上界约束；关闭或拒绝后继续主动为 0。

在真实研究通过前，可以发布工程基础和受控 candidate/dry-run，但不能对外宣称“越养越懂你”。若人格辨识通过而成长辨识失败，只能宣称稳定个体差异。

## 17. 完成定义

V1 只有在以下条件全部满足时才算完成：

- 生产 CompanionMind 使用持久化出生气质并生成 `expression_style`；
- 相处调整资格、三日晋级、反向学习、撤销和重算全部通过真实 SQLite 测试；
- 关系阶段不再可被短期刷到长期，当前关系姿态可跨重启恢复；
- 四学年、非标准路径、周年、毕业与成长时刻合同全部实现；
- VA 可跨重启安全衰减，控制与主体隔离正确；
- 小程序、语音、硬件和 operator 各自完成规定职责；
- 全矩阵、迁移、并发、HIL 和纵向研究达到发布门槛；
- requirements 中对应条目按真实证据逐项更新，不因代码存在就提前标记 done；
- 旧行为、临时开关和不再使用的投影只在迁移完成且回滚窗口结束后删除。

## 18. 权威资产

- [个体人格与四年成长决策地图](../plans/2026-07-25-xiaoxin-individuality-growth-decision-map.md)
- [四年年龄成熟表达矩阵](../plans/2026-07-25-xiaoxin-age-maturity-matrix.md)
- [非标准学业路径转换矩阵](../plans/2026-07-25-xiaoxin-academic-path-transition-matrix.md)
- [长期辨识与成长验证方案](../research/2026-07-25-xiaoxin-longitudinal-individuality-validation.md)
- [CompanionMind V2 设计规格](2026-07-18-xiaoxin-companion-memory-v2-design.md)
- [领域词汇表](../../product/domain-language.md)
- [项目需求状态](../../requirements/requirements.yaml)
