# 小芯陪伴成长决策地图

本地图把“陪着学生成长”拆成可逐项解决的决策票据。`docs/requirements/requirements.yaml` 记录需求状态，本文件只记录尚需澄清或验证的设计决策。

## #1: 成长是否拆成四条独立轴线？

Blocked by: 无
Type: Discuss

### Question

校园阶段、陪伴年轮、关系成熟度和成长弧线是否必须独立建模？

### Answer

是。校园阶段描述学生处境；陪伴年轮描述共同经历的校园年度；关系成熟度决定相处策略；成长弧线描述具体主题的时间变化。四者可以相互影响，但不能互相替代。

## #2: 个人宠物生命周期存放在哪里？

Blocked by: #1
Type: Discuss

### Question

个人宠物的稳定 `created_at`、陪伴年轮和设备换新连续性应以微信 `openid` 对应的本地主体、记忆主体还是设备为事实源？

### Answer

采用微信 `openid` 映射出的本地微信主体拥有的独立个人宠物记录作为事实源，并继续存放在现有 `xiaoxin_control.db` 身份中心中。第一版不声称已经绑定或认证学生账号；一个微信主体最多拥有一个 active 个人宠物。个人宠物具有稳定 `pet_id`、不可因换设备重置的 `companion_started_at` 和生命周期状态。

准确归属链路为 `openid -> 本地 users.id（微信主体）-> personal_pet`。个人宠物是生命周期聚合，设备只是可替换的硬件入口，`memory_subject_id` 只负责记忆隔离与说话人归属。个人宠物记录可以在微信主体建立时创建为 pending，但只有第一次成功激活或绑定设备时才设置一次 `companion_started_at`；后续解绑、维修、换新或重新绑定不得覆盖。陪伴天数和陪伴年轮按 Asia/Shanghai 本地日期从该时间推导，不再读取当前设备 `bound_at`。

现有微信主体迁移时，优先使用可证明的最早成功绑定/激活时间回填；证据不足时不得伪造历史，应从迁移确认日开始并记录来源。清空记忆不会重置个人宠物、陪伴起点或陪伴年轮。未来若增加学校认证，只能把认证身份关联到现有微信主体，不能自动重建或覆盖个人宠物。第一版连续性的前提是使用同一个小程序 `appid` 和对应 `openid`；更换 `appid` 后无法仅凭新 openid 自动认回原个人宠物，必须另做显式迁移或身份关联。

## #3: 每轮对话如何生成统一成长信号？

Blocked by: #1, #2
Type: Prototype

### Question

如何稳定生成 `stage_signal`、`mood`、`topic`、`memory_worthy`、成长事件和后续跟进信号，替代运行时传入空 `turn_analysis`？

### Answer

采用“确定性规则优先、单轮唯一分析、受约束模型只补低确定性信号”的 TurnAnalysis 合同。运行时在语义路由后只生成一次分析，并把同一结果交给上下文准备、记忆提交和关系状态。每个 TurnSignal 必须记录 value、source、confidence 和 persistence_allowed；硬资料、禁止记忆、拒绝继续话题、危机边界和明确事实问答由规则直接裁决，模型以后不得覆盖这些结果。

第一版固定先覆盖 C 语言学习、作息、课程压力、社交适应、短期情绪和普通问答。profile facts、companion memory type、growth event 与 followup 必须消费统一结果；旧关键词规则只保留为 legacy 调用兼容路径。受约束模型补充、ASR 错字、多主题与复杂否定仍需后续验证。

## #4: 关系成熟度如何映射为确定性行为？

Blocked by: #1, #3
Type: Prototype

### Question

“初见、熟悉、默契、同行”分别怎样约束主动程度、追问方式、记忆引用深度、回答长度和收尾方式？

### Answer

未决。必须输出结构化陪伴策略，再由 LLM 负责自然表达；不能只向提示词注入一个裸关系等级。

## #5: 成长记忆采用什么生命周期与证据合同？

Blocked by: #1, #2, #3
Type: Discuss

### Question

成长事件如何经历 `candidate -> active -> superseded | forgotten`，并回答来源、时间、状态、替代关系和是否允许用于回复？

### Answer

未决。现有 profile 和 companion 可作为参考，episodic 与 growth arc 需要补齐来源、解释和遗忘历史。

## #6: 如何生成可信的跨阶段成长回望？

Blocked by: #3, #4, #5
Type: Prototype

### Question

如何把同一主题的担心、尝试、受挫、进展和里程碑合成为简短回望，同时允许倒退、重启并禁止虚构？

### Answer

未决。第一条验证弧线使用“C 语言学习”，必须支持跨会话召回、依据解释、拒绝继续提及和删除。

## #7: 主动关心如何学习又不越界？

Blocked by: #4, #5
Type: Discuss

### Question

课程、提醒、作息和历史反馈怎样共同决定触发、冷却、表达强度和用户拒绝后的收住策略？

### Answer

未决。主动关心必须有可追溯依据，并能从“太频繁、太早、别再提”等反馈中改变后续策略。

## #8: 成长如何呈现在硬件和小程序？

Blocked by: #4, #6, #7
Type: Prototype

### Question

陪伴年轮、关系阶段提示、成长足迹和学年回顾分别放在哪个入口，硬件端如何轻量表达而不挤占通知和语音主流程？

### Answer

未决。小程序承担可查看、纠正和删除的成长足迹；硬件只承担当下互动和轻量仪式，不展示工程计数或完整画像。

## #9: 如何证明用户真的感到“小芯长大了”？

Blocked by: #4, #6, #7, #8
Type: Discuss

### Question

哪些自动化合同和真机体验指标能同时证明行为差异、记忆正确、主动关心可接受且没有制造监控感？

### Answer

未决。至少需要覆盖同一句话在不同关系阶段产生稳定策略差异、跨会话成长回望、错误记忆纠正、定向遗忘和主动关心拒绝后的行为变化。

## #10: 哪些记忆主体可以推动个人宠物关系成长？

Blocked by: #2
Type: Discuss

### Question

同一微信主体下可能同时存在 confirmed speaker、device_unknown、device_fallback 和 subject alias；哪些互动可以更新个人宠物的关系成熟度与成长弧线？

### Answer

未决。个人宠物生命周期始终归微信主体，但未知或未绑定设备的记忆不能自动进入该主体的个人成长。需要在记忆安全与缺少稳定声纹时的可用性之间确定明确资格与合并规则。
