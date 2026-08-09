# XiaoXin Companion Memory V2 Integrity Audit

Date: 2026-07-18

## Verdict

Slice 12 completes the replace-not-layer cutover. Runtime turns cross `CompanionMind.prepare_turn()` / `commit_turn()`, control reads and writes cross `CompanionMind.project()` / `apply_control()`, and background reflection crosses `run_due_work()`. The legacy memory package, legacy configuration, legacy control routes and legacy structural tests are removed rather than left disabled.

Explicit preferred names, origin facts, preferences and completion-type user growth facts now enter V2 as owner-scoped Evidence before `prepare_turn`, survive restart and relationship reset, and never create topic-specific schema. Replacement `profile_fact` values are superseded transactionally. Chat text does not update the structured academic-stage source. Control replay excludes server-generated time from the semantic idempotency digest, so the same idempotency key can be retried later without creating a second epoch.

Confidence: high for the server-side cutover and focused product stories; final deployment and full-suite evidence is recorded by Slice 13.

## Current Fact Sources

| Concern | Fact source | Control/read seam | Integrity rule |
| --- | --- | --- | --- |
| Account, device ownership, personal pet, student profile, memory subject | `xiaoxin_control.db` | `XiaoxinIdentityStore` inside the authenticated handler | Current owner must own the requested subject; merged subjects are rejected |
| Companion Evidence, relationship epoch, adjustments, chapters, jobs, controls | `xiaoxin_companion.db` | `CompanionMind.project/apply_control` | Store transactions enforce owner, pet, subject, epoch, status and idempotency |
| Natural-conversation candidate source | `xiaoxin_companion.db` 的 `companion_turn_sources` | `commit_turn/run_due_work` | 仅 confirmed conversation 当前用户一句；job 不复制原文；整句最多 24 小时，终态立即删除 |
| Evidence 词面检索索引 | `xiaoxin_companion.db` 的 `companion_evidence_fts` | `CompanionMind.prepare_turn` → `CompanionStore.recall_evidence` | SQLite FTS5 trigram；按 pet/subject 隔离；trigger 同步；`content_json` 在索引前移除 `source_quote` |
| 短期召回审计 | `xiaoxin_companion.db` 的 `companion_retrieval_audits` | operator Projection | 只保存 query/hints SHA-256、Evidence ID、无文本分数和耗时；不保存查询原文；7 天清理；purge 删除 |
| 关系阶段事件 | `xiaoxin_companion.db` 的 `relationship_stage_events` | `commit_turn/observe/apply_control` 后刷新，operator Projection 读取 | 只保存主体/epoch、前后阶段、四维整数快照、无文本 reason code、策略版本和时间；purge 删除 |
| Reflection | configured LLM adapter, background only | `CompanionMind.run_due_work` | Model proposes; Validator and Store decide; failure cannot disable control API |
| Legacy JSON/JSONL archive | deployment-created read-only file backup only | no runtime seam | no read, write, ownership inference, automatic import or reverse export |

## Control API Integrity

`GET /api/xiaoxin/memory-subjects/{subject_id}/memory` supports only `operator` and `miniprogram` projections. Returned Evidence is limited to opaque ID, kind, safe source summary and status. Projection state may include active adjustments, chapter summaries, current epoch and job diagnostics, but not model prompts, chain-of-thought, full internal profiles or unnecessary raw text.

`POST /api/xiaoxin/memory-subjects/{subject_id}/memory/control` accepts a typed action, caller idempotency key and action payload. Server time is injected by deterministic code. Supported user actions are:

- `forget_evidence`
- `forget_theme`
- `correct_evidence`
- `set_boundary`
- `revoke_boundary`
- `reset_relationship`
- `purge_personal_memory`
- `confirm_candidate`
- `reject_candidate`

自然对话提取始终从 `candidate`、`prompt_eligible=false` 开始。`confirm_candidate` 才能把候选变为 active；`reject_candidate` 将其置为 forgotten。两个动作都写 Observation、Evidence 血缘和幂等控制审计。候选引用片段在确认、拒绝、纠正、遗忘、冲突替代或 30 天过期时擦除，只保留摘要哈希和结构化事实值；关系 reset 会删除被取消 job 的短期来源，purge 会删除该 pet 的全部短期来源。

Reset and purge have deliberately different visible semantics. Reset preserves user facts and explicit controls while deactivating the old relationship epoch. Purge removes personal companion/growth content while identity, device binding, personal-pet ownership and student profile remain in the identity database.

## Retrieval Integrity

当前轮用户文本作为 `CompanionTurnRequest.retrieval_query` 参与本地召回，最长 500 字；结构化 `retrieval_hints` 只允许 fact key、Evidence kind、时间范围和排除敏感度。召回子系统不把 query 原文写入索引或审计；同一 confirmed conversation 若符合 CP-02 候选提取条件，仍可作为 `companion_turn_sources` 最多短存 24 小时，但不会由召回审计再复制一份。Store 先执行 owner、pet、memory subject、状态、prompt eligibility、有效期、当前 relationship epoch 和 sensitivity 等硬过滤，再从最多 64 条候选中确定性重排。本地召回不调用远程模型。

排序信号包括 exact fact key、kind hint、中文 trigram 词面覆盖、confidence、importance、freshness、当前 epoch、sensitivity 和最近 8 次引用历史。明确 query/hint 无相关命中时返回空，不用最新无关 Evidence 补位。voice 策略最多注入 2 条 Evidence，每条安全摘要最多 240 字；general QA 和 memory budget 为 0 时零私人召回、零召回审计；initiative 强制排除 `sensitive` Evidence。

FTS 仅是可重建的派生索引，不是第二事实源。`companion_evidence` 的 INSERT/UPDATE/DELETE trigger 维护索引，schema v8→v9 迁移会回填现有 Evidence。候选 `content_json.source_quote` 在写入 FTS 前由 `json_remove` 移除，避免临时原文被复制到长期索引。

召回审计只面向 operator，保留 7 天。审计包含主体/时期/交互类型、query/hints SHA-256、候选数量、入选 Evidence ID、无文本确定性分数与耗时，不包含查询原文、Evidence 摘要或内容。Store 初始化、retention worker 和每次召回会清理过期审计，`purge_personal_memory` 会删除该 pet 的全部召回审计；miniprogram 不获得这些诊断字段。

## Relationship Quality Integrity

`companion-policy-v2` 把内部关系质量明确分成四维，外部仍只投影四个稳定阶段：

- `continuity`：当前 relationship epoch 内已提交 turn 覆盖的不同本地日期；原始 turn 数仍是门槛之一，但不能单独升级。
- `knowledge`：当前有效且置信度不低于 0.8 的 profile、preference、boundary、life event、relationship context、wellbeing 和 goal Evidence；`meaningful_moment` 不冒充用户知识。
- `helpfulness`：用户明确接受帮助或完成 followup 的次数；Delivery Outcome、ignored 和系统投递失败不计入。
- `attunement`：用户明确给出的正向互动反馈；not helpful、too proactive、too personal 和 initiative rejected 不增加该维度。

默认阶段门槛依次为 `familiar=3 turns/2 days/1 knowledge/1 helpfulness/1 attunement`、`attuned=8/5/3/2/2`、`long_term_companion=20/15/6/4/4`。因此大量无结果闲聊和单日高密度互动不能绕过质量与时间底线。当前没有活跃度衰减，短期不互动不会降级；`reset_relationship` 仍是唯一新 epoch 入口。

真实关系输入来自统一事实链：显式完成待办生成 `followup_completed`，小程序 `companion_feedback`、语音强表达和 initiative 最终用户结果生成正负反馈 Evidence。语音只识别“刚才有帮助/没帮到我/太主动/太私人”等少量强表达；引用、转述和假设零写入，不让远程模型猜测用户态度。

最新负反馈优先于派生 Adjustment。`too_proactive` 停止追问，`too_personal` 停止私人记忆引用，负反馈降低主动程度和收尾亲密度；后续明确正向反馈可以解除压制。initiative 决策仍保留 `rejection_cooldown` 等更具体的 reason code。

Schema v10 的 `relationship_stage_events` 是可解释派生审计，不是第二事实源。阶段发生变化时记录四维快照和无文本 reason code；相同阶段不重复写入。commit、Observation 和控制结果完成后立即刷新；派生审计故障只写安全 warning，不把已经提交成功的对话伪报为失败。operator 只能读取当前主体、当前 epoch 的事件，miniprogram 不获得内部质量数字；reset 后从新 epoch 的 first meeting 开始，purge 删除全部阶段事件。

## Initiative Scheduling Integrity

Schema v11 的 `initiative_opportunities` 是主动调度事实，不是第二套记忆。每条 opportunity 必须记录 owner、pet、memory subject、当前 relationship epoch、有限 opportunity kind、reason code、active Evidence IDs、安全 brief、到期时间、claim lease、attempt、decision、delivery 和 outcome。第一阶段只允许 followup、reminder result、goal progress、future event、celebration 和用户配置 check-in；模型不能自行创建机会。

`CompanionMind.run_due_work()` 是异步 interface。ReflectionModel 在线程中运行，InitiativeScheduler 在同一后台 tick 使用独立扫描预算；二者不会因一方 backlog 饿死另一方。scheduler 先检查当前 epoch、Evidence 状态、prompt eligibility、sensitivity、明确禁用、拒绝冷却、每日预算和连续未回复降频，再做 SQLite 原子 claim。只有 claim 成功的 worker 才能调用 `LLMInitiativeComposer`；Composer 只收到 kind、reason code 和 safe brief，输出必须是只有 `content` 的 JSON 且不超过 160 字。

生产 `XiaoxinInitiativeDeliveryPort` 复用现有 dispatcher，不建立第二条 MQTT/TTS 链路。投递前会重新校验 memory subject 未合并、设备仍绑定当前 owner、设备在线、当前不在静默时段且没有高优先级通知。设备重新绑定、敏感 Evidence、旧 epoch 或被遗忘依据均为零生成或零投递；forget/purge 会擦除 opportunity 的 Evidence IDs 和安全 brief。

Delivery Outcome 与 User Outcome 严格分开：TTS done 只记录 `delivered`，不会生成 accepted；无回复保持 delivered，连续两次后进入 `unanswered_backoff`，不会被改写成 rejected；delivery failed 使用 `observed_delivery_outcome`；accepted、rejected 和 ignored 只能来自明确反馈控制。rejected 进入同 reason cooldown，不能换句话立即重试。

发布开关默认全部关闭。`companion_initiative_scheduler_enabled=true` 且 delivery=false 是 dry-run，只记录机会和阻挡原因；真实投递还要求 `companion_worker_enabled=true` 和 `companion_initiative_delivery_enabled=true`。模型 provider 初始化失败时真实投递失败关闭，不回退到确定性文案发送。

## Authorization and Subject Isolation

- Authentication is required before subject resolution.
- `get_memory_subject_for_user(subject_id, current_user.id)` is the owner filter.
- A subject already merged into another subject is treated as not found; the alias source cannot remain a second access path.
- Confirmed `user_speaker` subjects can receive private projections and controls.
- `device_unknown` and `device_fallback` receive only a neutral, private-memory-free projection and cannot execute controls.
- Cross-owner subject identifiers return 404 rather than disclosing ownership.

## Runtime Failure Isolation

Control runtime always constructs a Store-backed `CompanionMind`, even if `companion_worker_enabled` is false. If ReflectionModel/provider initialization fails, the effective worker switch is disabled and the control Mind remains available. This prevents a remote-model configuration failure from disabling deterministic viewing, correction, deletion, reset or purge.

## Removed Endpoints

The old legacy-memory, DELETE memory and query-forget routes are no longer registered. There is no compatibility route that enumerates or mutates legacy files. Callers use the typed V2 projection and control endpoints.

## Remaining Cutover Risks

1. Existing legacy data is intentionally not semantically migrated. Deployment must preserve a read-only file-level backup and must not infer owner/subject from filenames.
2. Relationship thresholds, retention periods, initiative frequency and hardware expression intensity still require production-data calibration; they are deterministic configuration risks, not a reason to restore the legacy system.
3. Mini-program UI and firmware consumption are outside this server-side phase. The server contract is ready, but those clients must be implemented and accepted separately.

## Release Gate for Slice 13

Run the complete Xiaoxin suite, requirements workbench, compileall and static gates; verify the deployment backup and rollback procedure; perform the documented real-device smoke; and record any manual step that was not actually executed. Production import graph, configuration and server-side tests must remain free of the retired memory system.
