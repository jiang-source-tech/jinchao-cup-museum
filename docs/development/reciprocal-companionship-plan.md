# 双向陪伴最小闭环计划

状态：进行中；Slice A 至 Slice E 工程实现已完成，双设备压缩验收待完成

目标：让小芯的主动行为不再只围绕待办、目标和未来事件，而是能够基于关系本身主动寻找用户；用户的回应或忽略会反过来改变小芯的下一次主动方式。

## 1. 产品合同

- 小芯表达的是“想和用户建立联系”，不是要求用户签到，也不是用失落、责怪或威胁制造依赖。
- 用户没有回应时，小芯降低频率并等待；用户明确拒绝时进入主题冷却；普通回复不能直接冒充“接受主动”。
- 主动内容必须仍然有安全来源、主体归属、静默时间、每日预算和设备绑定门禁。
- 两只小芯的差异必须同时体现在触发等待、主动表达和忽略后的退避，不只更换一句文案。

## 2. 最小状态模型

新增关系需要状态，唯一归属为 `owner_user_id + pet_id + memory_subject_id + relationship_epoch_id`：

- `need_kind=connection`
- `last_meaningful_interaction_at`
- `last_bid_at`
- `pending_decision_id`
- `ignored_streak`
- `cooldown_until`
- `next_eligible_at`
- `updated_at` 与乐观版本号

连接需要的分数由时间、当前关系阶段、气质中的 `companion_initiative` 和反馈历史确定性计算；不建立一个每分钟随机增长的“情绪黑箱”。

## 3. 状态机

```text
settled
  -> growing             超过性格阈值且不在静默/冷却
  -> bid_ready            生成 connection_bid opportunity
  -> waiting_feedback     真实投递完成
  -> settled              用户在反馈窗口内进行有意义回应
  -> backoff              用户忽略或设备投递失败
  -> cooldown             用户明确拒绝或关闭主动陪伴
```

`backoff` 只延长下一次 `next_eligible_at`，不降低关系阶段，也不写入用户负面画像。

## 4. 实现切片

### Slice A：持久化与迁移（已完成）

- Schema v20 已将主动机会扩展为 `connection_bid`；Slice C 继续演进到 schema v21，为机会状态增加 `deferred`。启动时可从 v19 或 v20 重建机会表并保留旧数据，初始化后继续执行 SQLite 完整性与外键检查。
- 已新增主体/宠物/记忆主体/关系 epoch 隔离的 `companion_relationship_needs`，关系需要写入与 turn、Evidence、opportunity 共用 Store 事务；claim 和唯一未完成约束复用现有主动机会状态机。

### Slice B：机会生成（已完成）

- CompanionMind 在已确认且允许持久化的普通对话提交时，用对应短期对话 Evidence 原子更新关系需要。
- Scheduler 仅在 `next_eligible_at` 到期且同一主体/epoch 没有现存 `connection_bid` 时物化一次机会，再沿既有 claim、Composer 和 delivery 链路处理。
- 测试配置支持 2 至 5 分钟缩时；生产默认按内敛 72 小时、适中 48 小时、主动 24 小时计算，并由关系阶段确定性调整。
- 首次会话的连接节奏直接使用 `pet_id` 确定的出生气质；已有关系使用当前 CompanionPolicy，两个主体的状态与阈值互不串联。

### Slice C：顺延调度（已完成）

- 静默时段、高优先级提醒和暂时设备不可用已改为 `deferred`，分别顺延到静默结束、2 分钟后和 5 分钟后；条件恢复后复用原 opportunity 和 decision，不重复生成连接机会。
- 资格检查后、真实 dispatcher 提交前再次出现冲突时同样退回 `deferred`；dispatcher 仍沿用 MQTT、WebSocket、TTS 和既有有限重试，只有真实尝试耗尽后才进入 `delivery_failed`。
- 永久关闭、敏感 Evidence、旧 epoch 等确定性门禁仍进入 `blocked`，不会被瞬态重试绕过。
- connection bid 永久阻断或最终投递失败后会清空 `pending_decision_id`，按原性格阈值推进 `next_eligible_at`；系统失败不增加 `ignored_streak`，避免把设备问题归因成用户忽略。

### Slice D：人格化表达（已完成）

- Store 在读取 `connection_bid` 时从现有关系需要状态联结 `initiative_bias`、`relationship_stage` 和 `threshold_seconds`，并按超过触发时间的比例计算 `light/steady/clear` 三档连接需要强度；不新增第二份人格事实源。
- LLM Composer 只接收 `reserved/timely/proactive`、四档关系阶段和三档需要强度，所有值在进入模型前经过白名单校验；不投影阈值秒数、忽略次数、主体标识、Evidence ID、原始私密文本或模型思维过程。
- Prompt v3 将这些枚举解释为表达姿态：内敛更克制留白，适中温和直接，主动可以坦率表达“小芯也想来聊聊”；关系越熟悉可以越松弛，但不得假装共同经历。
- 需要强度只调整表达意愿的清晰程度，不允许转化成紧急、委屈、催促或依赖；提示词明确要求自由生成自然的一句话，不套固定模板，也不机械逐项体现标签。

### Slice E：反馈闭环（工程实现已完成）

- 默认反馈窗口为 30 分钟，可通过 `companion_connection_feedback_window_minutes` 配置；截止时刻仍属于窗口，只有超过截止时刻才结算为忽略。
- 同一 owner、pet、memory subject、关系 epoch 和 pending decision 的真实 `delivered` connection bid，只有在反馈窗口内收到有意义对话时才写入独立 `connection_responded` Observation/Evidence；普通回复不会写成 `accepted`。
- 回应会清空 pending decision、重置 `ignored_streak` 和冷却，并从当前真实互动按现有气质与关系阶段重新计算 `next_eligible_at`。
- 忽略会清空 pending decision 并增加 `ignored_streak`；内敛、适中、主动分别使用 2.5、2.0、1.5 倍基础阈值，连续忽略按 `2^(streak-1)` 确定性增长，最长退避 30 天。
- 明确 `too_proactive` 反馈会把当前 connection bid 标记为 rejected，并进入 7 天连接主题冷却；系统投递失败仍只按原阈值退避，不增加用户忽略次数。

## 5. 自动化保护

当前 CP-08 聚焦测试保护以下十一类核心行为；Slice E 新增两个反馈回归用例，并扩展既有端到端机会与投递失败测试：

1. v20 到 v21 迁移保留原有 `connection_bid`，并把 `deferred` 纳入活动机会唯一约束。
2. `connection_bid` 到期前不生成，到期后只生成、claim 和投递一次。
3. 内敛与主动气质产生不同等待阈值，且跨 owner 读取被拒绝。
4. 静默冲突先顺延，未到 `retry_at` 不处理，恢复后复用同一 opportunity/decision 完成投递。
5. connection bid 最终投递失败会清空挂起决策并按性格阈值退避，不形成 scheduler 每个 tick 重复生成循环。
6. 到期的 connection bid 会从当前主体关系需要中投影气质、关系阶段和需要强度，且不改变原 opportunity/decision 链路。
7. Composer 只接收白名单表达上下文，拒绝不受控自由文本；Prompt v3 保留自然措辞自由度并禁止机械模板、装熟和依赖压力。
8. 真实 delivered connection bid 在同主体、同 epoch、同 pending decision 和反馈窗口内收到对话后，写入 `connection_responded` 并重置连接需要。
9. 反馈窗口截止时刻仍保持 waiting feedback，超过截止时刻才结算为 ignored。
10. 内敛与主动气质在忽略后产生不同退避时间，连续忽略可继续确定性增长；系统投递失败不增加 `ignored_streak`。
11. 明确 `too_proactive` 反馈将当前 connection bid 标记为 rejected，并进入 7 天冷却。

## 6. 双设备验收

- 使用两台已绑定设备分别作为两个用户主体。
- 同一自然起点完成 4 至 6 轮连续文字输入，等待上一轮 TTS 结束再进入下一轮。
- 将关系阈值临时缩短，验证两台设备均能真实唤醒并 TTS 播报 `connection_bid`。
- 一台设备回应、一台设备忽略，检查后续 `next_eligible_at`、`ignored_streak` 和表达差异。
- 记录设备、主体、`pet_id`、Evidence、decision、delivery、TTS 终态、串口状态及人工听感；不把机器日志当作听感或表情结论。

## 7. 发布顺序

聚焦测试通过后，使用中文提交说明推送 `origin/main`；再次刷新并核对本地、真实 GitHub 和服务器 HEAD，服务器执行 `git pull --ff-only origin main` 与 `docker compose up -d --build`，随后检查 Compose、业务日志、HTTP 入口和双设备文字输入、真机输出链路。
