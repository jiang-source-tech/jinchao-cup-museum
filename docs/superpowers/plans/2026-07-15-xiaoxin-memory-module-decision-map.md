# 小芯可信记忆模块决策地图

本地图记录“小芯记忆能力从分层原型升级为可信长期陪伴记忆”的关键设计决策。产品效果已经对齐；本文件只保存仍需讨论、原型验证或验收确认的决策。详细实施顺序见 [2026-07-15-xiaoxin-memory-module-improvement.md](2026-07-15-xiaoxin-memory-module-improvement.md)。

## #1: 第一阶段记忆产品效果是什么？

Blocked by: 无
Type: Discuss

### Question

第一阶段应追求“尽可能多地记住”，还是追求有限范围内的准确、连续和可控制？

### Answer

追求“准确、克制、连续、可解释、可纠正、可遗忘”。系统只保存明确资料、稳定偏好、短期状态和经过筛选的成长事件；普通事实问答不形成长期记忆。记忆必须归属于确定主体、带有来源和时间，并能跨会话支持自然连续的陪伴。用户纠正、删除或拒绝后，相关内容立即停止影响回复、主动关心和成长回望。

## #2: 第一阶段交付范围是什么？

Blocked by: #1
Type: Discuss

### Question

第一阶段要同时覆盖所有成长主题、主动关心和学年回顾吗？

### Answer

不覆盖。第一阶段固定交付：称呼、专业、年级、学校/学院等资料；少量学习和提醒偏好；短期情绪与临时状态；C 语言学习单主题成长弧线；跨会话召回；来源解释；纠正；定向遗忘；主体隔离。主动关心、关系成熟策略、学年回顾、多主题扩展和向量召回均在可信证据闭环之后实施。

## #3: MemoryEngine 的外部接口放在哪里？

Blocked by: #1, #2
Type: Prototype

### Question

如何让 runtime 不再了解 profile、episodic、companion、growth arc 和 relationship 的内部调用顺序，同时仍支持回复前召回与回复后提交？

### Answer

未决。推荐原型接口为 `prepare_turn -> PreparedMemoryTurn`、`commit_turn(prepared, reply) -> MemoryCommitResult`、`control` 和 `explain`。原型必须证明同一轮分析只生成一次、提交可幂等重试、现有 runtime 行为不回退，并且新模块不是只做参数转发的浅包装。

## #4: TurnAnalysis 如何表达混合持久化？

Blocked by: #2, #3
Type: Prototype

### Question

同一轮同时包含长期资料、短期情绪和禁止保存内容时，如何避免全局 `persistence_allowed` 无法表达部分保存？

### Answer

未决。推荐由 TurnAnalysis 输出零到多个带独立 retention 的 `MemoryCandidate`，以及优先级更高的 `MemoryDirective`。至少验证“我叫小林，但今天有点烦”“不是我项目跑通，是我室友”“不用记这个”“别再提 C 语言”等混合与否定场景。

## #5: 统一记忆证据存储采用什么事实模型？

Blocked by: #3, #4
Type: Prototype

### Question

Profile、Companion、Episode、Growth Arc 和 Relationship 应继续各自保存事实，还是共享统一 Evidence 事实源并生成投影？

### Answer

未决。推荐以不可变或追加式 Evidence 为事实源，各记忆层成为可重建投影。原型必须证明一条成长事件能回答来源、时间、主体、状态、替代关系和是否允许注入提示词，并能在事务中同步更新成长弧线与 followup。

## #6: 统一证据存储使用 JSON 还是 SQLite？

Blocked by: #5
Type: Prototype

### Question

现有按主体拆分的 JSON 文件是否足以支持并发写、幂等提交、纠正、遗忘和投影重建？

### Answer

未决。推荐使用独立 `xiaoxin_memory.db`、SQLite WAL 和临时数据库测试，旧 JSON 只作为一次性迁移来源。原型必须覆盖两个并发提交不丢失、相同 turn_id 重试不重复、事务失败不留下半更新状态，以及服务重启后的投影一致性。

## #7: 哪些记忆主体有资格推动个人宠物成长？

Blocked by: #2, #5
Type: Discuss

### Question

confirmed speaker、subject alias、device_unknown 和 device_fallback 中，哪些互动可以改变关系成熟度与成长弧线？

### Answer

未决。推荐第一版仅允许 confirmed speaker 和已明确 alias 到 confirmed speaker 的主体推动个人成长；unknown/fallback 可以隔离保存设备级临时记忆，但在用户确认合并前不得影响个人宠物成长。

## #8: 纠正、忘记和彻底删除分别是什么语义？

Blocked by: #5, #6
Type: Discuss

### Question

如何同时满足后续不再使用、能够确定性回答“是否已忘记”和用户要求删除原内容？

### Answer

未决。需要区分 correction、forget 和 purge：纠正保留替代关系；忘记禁用证据并保留最小状态墓碑；彻底删除清除内容和安全来源文本，只保留不含用户内容的防重放墓碑。所有投影必须从统一状态重新计算。

## #9: C 语言成长弧线采用什么状态机？

Blocked by: #4, #5, #8
Type: Prototype

### Question

如何支持担心、尝试、受挫、进展、暂停、重启、里程碑和回望，而不是只按等级单向上升？

### Answer

未决。第一条完整原型固定使用 C 语言学习，必须覆盖跨会话时间链、倒退、重启、错误归属纠正、来源解释、定向遗忘和投影重建。

## #10: 召回采用什么排序与硬过滤合同？

Blocked by: #5, #7, #8
Type: Prototype

### Question

何时使用 Profile、Episode、Companion 和 Growth Arc，如何避免已遗忘、过期、错误主体或低可靠证据进入提示词？

### Answer

未决。必须先进行主体、状态、过期、topic block 和注入许可硬过滤，再做关键词或语义排序。第一阶段保留关键词检索并建立离线召回样本；向量检索只能作为后续内部 adapter，不能绕过硬过滤。

## #11: 旧 JSON 记忆如何迁移和回滚？

Blocked by: #6, #8, #10
Type: Prototype

### Question

如何迁移现有 profile、companion、episodic、growth arc 和 relationship 文件，同时避免长期双写和无法回滚？

### Answer

未决。推荐一次性、可重复运行的只读导入器：扫描、清洗、生成迁移报告、写入新事实源、校验后切换读取；旧文件保留只读备份。不得长期维持两个可写事实源。

## #12: 什么证据足以宣告第一阶段完成？

Blocked by: #3, #4, #5, #7, #8, #9, #10, #11
Type: Discuss

### Question

哪些合同测试、跨会话测试、并发测试和真实对话样本能够证明记忆模块已经达到第一阶段目标？

### Answer

未决。最低要求包括黄金故事全链路、账号零串记、禁止记忆零误写、遗忘后零召回、纠正后零双重生效、相同 turn_id 幂等、并发提交不丢失、投影可重建，以及模型回复不引用未授权证据。
