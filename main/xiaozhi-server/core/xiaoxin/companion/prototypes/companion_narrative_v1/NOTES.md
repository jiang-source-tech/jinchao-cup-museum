# Prototype Findings

状态：已完成；自动审计 `14 passed, 0 failed`，并完成人工状态投影复核。

本文件冻结决策地图 #11 的原型结论。原型是纯内存合成时间线，不连接数据库、不读取真实用户数据，也没有修改生产章节、成长时刻、Prompt 或跨端投影；在 #15 前不得把候选状态机直接复制到生产实现。

## 结论

采用“结构化叙事边界 -> Evidence 派生陪伴章节 -> 一次性成长时刻 -> 各端受限投影”的单向模型。学业资料、真实相处时间和毕业事实先产生不可由模型创造的叙事边界；`CompanionChapter` 只是 Evidence 的可撤销读模型，不新增年度报告事实源；成长时刻只负责一次性认领和表达，不成为新的成长事实源。

因此，四年连续叙事不是每年强制生成一篇总结，而是保留真实边界，并仅在依据足够时回顾少量共同经历。没有共同历史时仍可中性陈述真实晋级或毕业，但不能说“我们一起”；只有时间经过而没有共同 Evidence 的周年不得自动庆祝。

## 冻结合同

### 叙事边界与阶段归属

- 合法边界来自带权威 revision 的真实学业变化、陪伴周年或毕业；模型和聊天内容不能创建边界。
- 阶段晋级关闭的是实际离开的来源阶段，旧阶段 Evidence 不能标到目标新阶段。
- 跳级只形成一次“真实来源 -> 实际目标”边界，不补写未经历的中间年份。
- 资料纠正使错误边界及其派生章节失效，但纠正本身不产生仪式；合并时刻中的独立周年必须保留。
- 毕业关闭最后实际阶段并冻结最后确认年龄，通常为 4 岁，不产生 5 岁。
- `academic_stage`、`academic_status`、`transition_kind`、`effective_at` 和 `source_revision` 必须保持不同语义，不能再压成单一“年级变化”。

### 陪伴章节资格

- 章节只使用当前 `relationship_epoch`、实际阶段和时段内仍有效的 Evidence。
- 候选至少包含 2 条 Evidence、至少 1 条 `shared_experience`，并跨至少 2 个上海本地日期；同日堆叠不能伪造长期连续性。
- 每个章节最多选 3 条 Evidence，跨端只使用安全摘要，不展示内部数量或资格分数。
- Evidence 删除后旧章节版本进入失效终态；剩余依据仍满足资格时创建不可变的新版本，否则不保留章节。

### 成长时刻资格与合并

- 学业晋级和毕业即使没有合格章节，也可形成 `boundary_only` 时刻：语义只陈述边界事实，不引用共同锚点。
- 陪伴周年必须有合格章节才能形成 `evidence_backed` 时刻；只有相处时间时仅记录周年边界。
- 一个周年可与 30 天内的一个学业晋级或毕业边界合并，由优先级更高的学业或毕业语义主导；两个独立学业边界不能合并。
- 独立周年若紧邻刚表达过的成长时刻则抑制重复仪式，但周年事实仍保留。
- 候选表达窗口分别为学业 30 天、周年 14 天、毕业 90 天；过期后不在重逢时补播。

### 认领、关闭与纠错

- 时刻采用 `pending -> reserved -> expressed` 的原子单次认领；同一时刻不能被两个 turn 同时消费。
- 生成或投递成功后进入 `expressed`，不再重复；失败或 5 分钟租约超时释放回 `pending`。
- 用户关闭成长回顾后，所有 `pending/reserved` 时刻以及关闭期间新产生的时刻进入 `suppressed`；重新开启不补播。
- 到期时进入 `expired`；边界、关系时期或必要 Evidence 失效时进入 `invalidated`。这些终态不能原地复活。
- Evidence 遗忘或权威资料纠正发生在 `reserved` 期间时，必须释放旧 turn 的认领；新 turn 只能重新认领收紧后的投影。
- Evidence 遗忘后，学业或毕业时刻可降为 `boundary_only`，周年时刻则失效。已经说过的话不能收回，但后续小程序回顾和再次投影必须去掉已删除依据。

### 跨端预算与上下文门禁

- 成长时刻不能主动发消息，只能附着于合适的普通用户对话；设备控制任务、谨慎重逢、修复中姿态或关闭回顾时不能认领。
- 语音最多 1 至 2 句；`boundary_only` 最多 1 句且共同锚点预算为 0，`evidence_backed` 最多引用 1 个共同锚点。
- 小程序最多显示 3 条安全 Evidence 摘要和必要边界事实，不显示内部计数。
- 硬件只接收设备无关的低强度、短时长确认语义；真实回退等中性重定向不触发庆祝动作。

## 审计覆盖

| 场景 | 冻结的反例或不变量 |
|---|---|
| `four_year_continuity` | 四个实际阶段各自关闭正确章节；周年与晋级合并；毕业保持 4 岁 |
| `age_change_without_shared_history` | 无共同历史时只表达边界；设备任务不能消费时刻 |
| `insufficient_evidence_stays_boundary_only` | 一条或同日 Evidence 不能伪造章节 |
| `disabled_reflections_do_not_backlog` | 关闭立即抑制，重新开启不补播 |
| `atomic_retry_delivery` | 并发只认领一次，失败可重试，成功不重复 |
| `forgetting_tightens_future_narrative` | 学业降级为事实表达，失去依据的周年失效 |
| `nonstandard_paths_do_not_invent_years` | 跳级不补年，回退中性，纠正/休学/迁移不庆祝 |
| `long_absence_does_not_replay_stale_rituals` | 久别阻止认领，过期周年不在重逢时补播 |
| `graduation_without_history_is_neutral` | 无共同历史的毕业不造章节、不生 5 岁、不主动发消息 |
| `anniversary_without_evidence_is_fact_only` | 纯计时周年幂等保留事实但无仪式 |
| `forgetting_after_expression_removes_future_anchor` | 已表达记录保留，未来投影删除被遗忘锚点 |
| `forgetting_rebuilds_still_supported_chapter` | 旧章节失效，剩余依据足够时创建新版本 |
| `forgetting_during_reservation_releases_old_turn` | 遗忘使旧 turn 失去认领，新 turn 获得收紧投影 |
| `correction_preserves_independent_anniversary` | 纠正只删除错误学业边界，保留并重新认领真实周年 |

所有场景对同一合成输入重复运行并比较规范化状态，结果确定一致。人工复核同时检查了章节阶段、边界状态、时刻终态、共同锚点、语音句数和硬件语义。

## 生产差距

- 当前只有 `freshman -> sophomore` 会创建成长时刻，后续晋级、周年和毕业没有完整闭环。
- 当前所有 stage 变化共用 `academic_stage_changed`，缺少 `academic_status`、`transition_kind` 和 `source_revision`，无法区分跳级、回退、纠正、休学与毕业。
- 当前章节会把来源阶段的 Evidence 标到目标新阶段，与已冻结的阶段归属相反。
- 当前成长时刻只有 `pending/reserved/expressed`，缺少关闭、过期、失效、合并和纠错拆分语义。
- 当前固定文案只检查是否存在任意关系 Evidence，就可能使用“我们一起”，没有章节所需的共同经历、跨日和数量门槛。
- 当前没有学业、周年与毕业的统一认领窗口，也没有 Evidence 删除或资料纠正发生在认领期间时的重投影合同。

这些差距必须在 #15 之后转成实施切片和生产测试；本轮只证明候选领域模型能够一致处理四年叙事及其反例。
