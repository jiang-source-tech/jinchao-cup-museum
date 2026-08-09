# 小芯记忆模块执行台账

本台账记录可信记忆模块改造期间的任务分工、线程状态、工作区隔离方式、验收和整合结果。产品目标与实施顺序分别见：

- [可信记忆模块决策地图](2026-07-15-xiaoxin-memory-module-decision-map.md)
- [可信记忆模块改造实施计划](2026-07-15-xiaoxin-memory-module-improvement.md)

## 总体职责

主任务负责规划、分工、状态监督、关键代码复核、冲突管理、最终整合、项目级测试和最终报告。后台任务的结论不得直接视为验收通过；所有关键结论和代码必须由主任务复查。

## 工作区规则

- 调研和只读审计任务可以使用当前项目目录。
- 会修改代码且可能重叠的任务必须使用独立 Codex worktree。
- 当前 `main` 已完成 S8 整合并保持干净；后续实现任务必须从已封板的 `main` 精确对象创建独立 worktree。
- 不允许后台任务修改、暂存、提交或回退不在其工作范围内的文件。
- 每个实现任务必须运行其聚焦测试并提交独立 commit；最终由主任务复核和整合。

## 当前阶段

阶段：Milestone A、S1、S3、S4、S5、S6、S7 与 S8 已封板；进入 S9“精确 correction unit forget + active Evidence rebuild”最小纵向切片实现阶段。

已完成目标：统一 Evidence 已覆盖资料、短期情绪、称呼纠正、C 语言 concern/attempt/progress、subject-scoped durable topic block 与固定 progress -> attempt correction；schema 已升级到 v6。S8 能在同一事务中把唯一 trusted progress 变为 superseded、创建 replacement attempt 和不可变 relation，重启后按 active Evidence 从 progressing 回退到 trying，同时保持原 concern/attempt 和跨主体隔离。

当前目标：只实现固定双目标 forget 句，对唯一合法 S8 correction unit 的 old progress 与 replacement attempt 执行 forgotten + prompt suppression，并将 schema v6 -> v7；projection 必须按剩余 active Evidence 重建，不得硬编码 stage。标准链保留 original attempt 后为 trying；无 original attempt 的对照链才为 struggling。本阶段不得顺带实现 purge、topic-wide/preferred-name/original-attempt forget、其它成长事件、Episode、vector、关系等级或跨端协议。固件端和小程序端继续保持只读。

## 后台任务

### 任务 A：记忆模块｜当前改动基线审计

- Thread ID：`019f64b4-de2f-7432-b4e3-db7e94b87e99`
- 环境：当前项目目录，只读。
- 状态：进行中。
- 目标：审计当前所有记忆相关未提交改动、合同冲突、回归和测试基线。
- 工作范围：当前 memory/runtime/TurnAnalysis/相关测试、需求和计划差异。
- 禁止修改：整个仓库；禁止写文件、暂存、提交、回退和清理。
- 验收标准：逐文件风险分类；精确测试结果；明确当前工作区能否作为 Task 0 基线；结论带文件行号。
- 应运行测试：TurnAnalysis、memory runtime、runtime、identity runtime integration 聚焦测试。
- 交付内容：只读审计报告。

### 任务 B：记忆模块｜合同与 TDD 切片审计

- Thread ID：`019f64b4-f87c-78b3-8901-5c83ef8c0939`
- 环境：当前项目目录，只读。
- 状态：进行中。
- 目标：确定 MemoryEngine/TurnAnalysis/Evidence 的第一个最小纵向 tracer bullet。
- 工作范围：产品目标、决策地图、实施计划、当前公共行为和相关测试。
- 禁止修改：整个仓库；禁止实现原型和提交。
- 验收标准：只详细设计一个 RED→GREEN 切片；给出后续三个切片顺序；识别浅模块风险；结论带文件行号。
- 应运行测试：仅用于确认当前公共行为的聚焦测试。
- 交付内容：TDD 切片建议和风险清单。

### 任务 C：记忆模块｜存储迁移并发审计

- Thread ID：`019f64b5-1252-70b1-8d58-ed129023e8ae`
- 环境：当前项目目录，只读。
- 状态：进行中。
- 目标：评估 JSON、复用控制数据库和独立 Evidence SQLite 的并发、幂等、迁移与回滚方案。
- 工作范围：memory 存储、identity SQLite 实现、计划文档和存储测试。
- 禁止修改：整个仓库及真实数据库/JSON 数据。
- 验收标准：逐层存储风险；三方案比较；最小表结构；迁移/校验/切换/回滚步骤；至少三个并发测试设计。
- 应运行测试：现有存储测试和临时目录实验，不接触真实 data。
- 交付内容：技术审计和明确推荐。

### 任务 D：增加成长陪伴感（继承中的既有任务）

- Thread ID：`019f63fe-a626-7571-ab3b-990b280e3a8e`
- 环境：当前项目目录，历史上具有写权限。
- 状态：已停止写入并完成交接，等待主任务验收。
- 原目标：实现 CG-01 统一 TurnAnalysis、runtime 接线及各记忆消费者兼容。
- 已知工作范围：TurnAnalysis、runtime、profile/episodic/companion/growth/relationship、相关测试和需求文档。
- 当前风险：与基线审计共享同一目录，审计期间仍发生文件变化，导致基线漂移。
- 监督要求：仅完成正在运行的原子测试；随后停止修改、格式化、暂存、提交和回退；提交完整改动与测试报告。
- 验收标准：主任务逐文件复核，重新运行聚焦测试；在确认归属和风险前不得视为完成或继续扩展。
- 交付内容：当前 CG-01 实现交接报告和“已停止写入”确认。

交接结果：

- 声明实际修改 15 个文件，覆盖 TurnAnalysis、runtime、五层记忆消费者、相关测试和需求文档。
- 最后一次全量 Xiaoxin 测试报告为 `793 passed, 1 skipped, 2 warnings`；需求工作台此前为 `10 passed`。
- 明确未完成多主题混合、复杂否定、主体转述、完整提前返回 seam、daily_rhythm 端到端一致性、旧关键词消除和异常降级。
- 未暂存、未提交、未回退；已明确回复停止写入。

## 计划中的后续任务

后续任务在上述审计完成、主任务复核并固定基线后创建：

1. Task 0 基线测试与当前 TurnAnalysis 切片稳定化。
2. MemoryEngine 第一个纵向 tracer bullet。
3. 每候选 retention 与 MemoryDirective 合同。
4. 最小 SQLite Evidence Store 和幂等/并发测试。
5. Profile Evidence 投影。
6. C 语言可逆成长状态机。
7. 统一纠正、遗忘、topic block 和 purge。
8. Episode、召回、主体资格和旧数据迁移。
9. 全量回归、独立 review 和最终整合。

具体实现任务将按文件重叠关系分配到独立 worktree，且不会同时修改同一核心文件。

## 状态更新记录

### 2026-07-15

- 完成项目结构、Git 状态、AGENTS.md 和记忆计划初检。
- 仓库内未发现 `AGENTS.md`；应用当前主任务中用户给出的执行规则。
- 确认当前分支为 `main`，工作区存在大量未提交的记忆模块和需求改动。
- 创建并固定三个只读后台审计任务。
- 暂缓创建实现 worktree，等待基线审计确认当前改动的归属和可整合性。
- 发现既有任务“增加成长陪伴感”仍在当前目录实施 CG-01，并造成审计期间基线漂移；已发送停止新增写入和交接要求。
- 既有任务已停止写入并交接；主任务复跑聚焦测试为 `78 passed`，需求工作台为 `10 passed`，但反例探测仍发现主体转述误归属、混合信息丢失和偏好取消误分类。
- 当前 `main` 已干净并与 `origin/main` 完全同步，基线提交为 `a369d55 feat(xiaoxin): add initial unified memory analysis`。
- 主任务再次复跑 TurnAnalysis、memory runtime、runtime、identity runtime integration：`78 passed`；需求工作台：`10 passed`；`git diff --check` 通过。
- 创建只读任务 `contract_seam_audit`：负责首个 tracer bullet 的 MemoryEngine 最小接口、公共行为测试、删除测试和禁止扩展范围审计。
- 创建只读任务 `sqlite_evidence_audit`：负责最小 Evidence Store 表结构、事务边界、幂等、过期和并发测试设计审计。
- 创建只读任务 `three_repo_impact_audit`：负责服务端、硬件端和小程序端的改动矩阵、协议兼容性及未来跨端任务边界审计。
- 三个新任务均禁止修改任何仓库文件；实现任务将在审计结论由主任务复核后使用独立 Git worktree 创建。
- 创建独立后台 Codex 任务 `记忆模块｜纵向切片与冲突图`（Thread ID：`019f651a-6685-7921-addc-386491f4550f`）：只读重排后续纵向切片、依赖、并行边界与 worktree 冲突图。
- 创建独立后台 Codex 任务 `记忆模块｜普通事实零证据切片`（Thread ID：`019f651a-83e7-7b93-a557-dcbb066c7725`）：只读设计第二个 tracer bullet，要求普通事实问答零 Evidence、零成长/关系提升和零无关记忆注入。
- 创建实现 worktree `D:\Learn\xiaoxin-esp32-server\.worktrees\memory-mixed-retention`，分支 `codex/memory-mixed-retention`；所有并行代码修改均隔离在该 worktree，主目录只保留本台账改动。
- 实现分支依次产生提交：`6978e23`（首个 mixed-retention tracer）、`f86ac16`（召回后保留 legacy commit）、`9dab519`（Evidence/时间/幂等约束加固）、`feae5b8`（迁移与旁路收口）、`fef5729`（subject mismatch 迁移回滚）、`3b8e923`（统一称呼召回判定）、`1441949`（真实 legacy mood 时间戳）、`a6a8298`（称呼变体与 legacy mood TTL 收口）。
- 创建独立审查任务 `审查 mixed-retention 切片`（Thread ID：`019f6524-b0ef-79b1-a277-e418a459a5d6`）和 `审查 SQLite Evidence 事务`（Thread ID：`019f6524-b0f2-73f0-b2d1-4b8ce3b3a48b`）。两者均只读、使用独立 Codex worktree/临时导出树，不修改实现分支。
- 首轮独立审查拒绝 `6978e23`：发现召回问句写伪姓名、`persistence_allowed=False` 仍写 legacy JSON、称呼召回吞掉 growth commit、过期 SQLite mood 被 stale relationship JSON 旁路、offset 等价重试冲突、首次/重试 `EvidenceRef` 不一致、schema 允许非法 expiry/confidence、召回排序不稳定等 P1/P2。
- 主任务逐项复现并退回修复；后续继续发现并修复：混合召回句丢失同句 `grade`、turn/Evidence 主体不一致、旧 schema 不升级、JSON 标量导致 recall 崩溃、迁移在外键关闭时无法回滚纯 subject mismatch、legacy mood 被永久压制或永久注入、称呼问句自然变体穿透等问题。
- SQLite 最终独立复验确认：合法旧库无损升级；非法旧行 fail-closed 且事务回滚；复合主体外键、JSON object、retention/expiry、status/prompt permission、offset 幂等、并发提交和故障回滚全部通过；未发现 SQLite 范围 P0-P3。
- 最终独立验收任务 `final_a6_acceptance` 对 `a6a8298` 运行公共 seam、删除测试和临时数据库攻击探针，结论为未发现 P0/P1、可以合并，置信度高；独立结果为 MemoryEngine `51 passed`、runtime/connection 聚焦 `170 passed`、Xiaoxin 全量 `846 passed`。
- 主任务亲自复核关键代码、迁移事务和反例，并将 8 个实现/修复提交无冲突 cherry-pick 到服务端 `main`；整合后 HEAD 为 `f26b4ea fix(xiaoxin): bound legacy mood and recall variants`。
- 整合后的服务端项目级测试：`846 passed`；需求工作台：`10 passed`；`python -m compileall -q main/xiaozhi-server/core/xiaoxin` 通过；`git diff --check` 通过；变更文件乱码扫描无命中。
- 三端影响复核结论保持不变：本切片只修改服务端；固件和小程序协议没有 Evidence/retention 消费者，不应为当前切片制造无消费者改动。固件基线仍为 `340 passed, 14 failed`，14 个失败均为既有 `InitializeProtocol` 返回类型静态测试漂移；小程序基线 `npm test` 全部通过。
- 第二个 tracer bullet 已由独立任务完成只读 TDD 设计：必须先制造已有 Evidence，再证明 `C 语言里的指针是什么？` 通过 `MemoryEngine.prepare_turn/commit_turn` 得到零召回、零注入、零 Evidence 和零投影提升；与首切片核心文件重叠，必须在同一串行实现通道继续，不能并行修改 `engine.py/evidence_store.py/turn_analysis.py`。
- 创建独立后台实现任务 `记忆模块｜S1 普通事实零副作用实现`（Client Thread ID：`client-new-thread:bad04b80-d0e5-492a-a708-8e1f2faa2c90`），使用新的 Codex worktree，从已整合的 `main` 工作树状态启动。职责：以 RED→GREEN 实现普通事实问答在已有 Evidence 下零召回、零注入、零 Evidence 和零 growth/relationship 提升；禁止关键词补丁、成长状态机、控制生命周期、跨端改动和任何 push。
- S1 worktree 创建完成，实际 Thread ID：`019f657d-658f-7bb1-aeb2-e009aaa5664a`，路径：`C:\Users\dell\.codex\worktrees\e14b\xiaoxin-esp32-server`；任务已被要求先同步最新安全基线 `e03e6dc` 再继续实现。
- 原始 mixed-retention 对抗审查在较晚返回的最终轮次中发现 `a6a8298` 尚有一个 P1：整轮疑问线索会把“我叫小林，你呢？”等真实声明误判为召回。主任务独立复现后在 `main` 追加 `e03e6dc fix(xiaoxin): scope name recall to local phrases`，改用局部短语判定并增加三类声明反例；整合全量更新为 `849 passed`，需求工作台仍为 `10 passed`。

## S1 普通事实零副作用整合记录

- 姓名安全基线链（均已在当前 `main` 的祖先中）：`e03e6dc`、`88820b2`、`bf37ae9`、`b6ae64b`、`9164bbb`、`b762a55`、`679375d`、`22dd171`、`d27246c`、`b79d066`、`dc9c5ad`。这些提交已解决疑问式姓名、空格变体、声明优先和结构性否定的首轮边界，不得重复 cherry-pick。
- S1 实现源提交（独立 worktree `codex/memory-zero-effects`）：`ae678de`、`3ff2fe9`、`df99577`、`bb39152`、`0541352`、`b9c7b35`、`257210f`。
- S1 已整合到 `main` 的 cherry-pick 结果：`2df4003`、`abfd667`、`dd0e3eb`、`1790a47`、`18dad70`、`9026bc4`、`1ebc665`；当前主分支 HEAD 为 `1ebc665`，未 push。
- 主任务整合后亲自验证：从 `main/xiaozhi-server` 目录运行 `tests/xiaoxin` 为 `878 passed`；需求工作台为 `10 passed`；聚焦 memory/runtime/identity 为 `102 passed`；`compileall`、`git diff --check` 和变更文件乱码扫描均通过。第一次从仓库根目录运行产生的 `test_voice_reminder.py::test_default_config_registers_voice_reminder_tool` 失败是测试 cwd 不满足其直接打开 `config.yaml` 的既有假设，改用服务端目录重跑后全绿，不属于 S1 回归。
- S1 第一独立审查批准 `257210f` 无 P0/P1；保留 P2：无标点自然前缀（如“其实你可以叫我江江”）可能漏记/漏召回，且 runtime 仍用 `prepared.analysis.candidates` 是否为空作为 Engine-owned 提交信号。
- S1 第二独立审查复现并拒绝当前整合，两个 P1 必须修复：
  1. `别误以为，我叫小王，我很烦`、`不要觉得：我叫小王，我很烦` 中停顿标点错误切断否定作用域，伪姓名会以 durable Evidence 写入 SQLite。
  2. `我叫小林但我很烦`、`我叫小林你呢`、`你可以叫我江江我很烦` 等 ASR 无标点多分句会把后续内容吞入 durable 姓名。
- 已创建独立 parser safety 修复任务：Thread ID `019f660e-7230-7de2-988d-adea01f19d6b`，worktree `C:\Users\dell\.codex\worktrees\9dfb\xiaoxin-esp32-server`；源提交 `fa3ab36`，已整合为 `2ba20e2 fix(xiaoxin): harden unpunctuated name boundaries`。职责：TDD 真实 parser→Engine→runtime→SQLite fail-closed 修复；范围仅 `profile_memory.py` 和相关 Xiaoxin 测试；禁止跨端和 Evidence schema 变更。该条为封板前历史状态，最终复审与整合结果见下节。

## S1 最终封板记录

- 最终候选分支：`codex/memory-s1-parser-final`；独立 worktree：`C:\Users\dell\.codex\worktrees\s1parser\xiaoxin-esp32-server`；精确封板对象：`55f9b1be703540f29f8dd8e2b53cfb0ab279552b`。
- 在中间候选 `4ca7918` 之后追加的 parser 安全提交为：`39c30a7`、`3b5f1f9`、`ad1c9f6`、`37363ca`、`e358666`、`55f9b1b`。它们依次关闭空 permission stem、弱宾语、开放 permission 尾词、合法昵称、角色型歧义和弱 permission recall 处理不一致等问题。
- 独立审查任务一 `审查 mixed-retention 切片`（Thread ID：`019f6524-b0ef-79b1-a277-e418a459a5d6`）批准精确对象 `55f9b1b`：P0/P1 均无；聚焦 `168 passed`、Xiaoxin 全量 `992 passed`、requirements `10 passed`、ownership 定向组 `11 passed`，compileall/diff/UTF-8/乱码/行尾检查通过，置信度高。
- 独立审查任务二 `验收记忆零副作用`（Thread ID：`019f65a1-a371-77c0-abe0-28ba239adb76`）批准精确对象 `55f9b1b`：P0/P1 均无；真实 Runtime→SQLite→prompt、普通事实零副作用、ownership、partial migration、幂等和无原文存储合同均通过，置信度高。
- 主任务亲自复核候选 worktree：HEAD 为 `55f9b1b`，`git status --short` 为空；主仓库 `main` 在 `293492f` 上 clean，且 Git 确认可以 fast-forward。
- 主任务执行 `git merge --ff-only codex/memory-s1-parser-final`，`main` 从 `293492f` 纯快进到 `55f9b1b`，无冲突、无历史改写、未 push。
- main 上最终项目级验收：`tests/xiaoxin` 为 `992 passed in 43.67s`；requirements workbench 为 `10 passed in 0.99s`；`python -m compileall -q core/xiaoxin` 通过；`git diff --check 293492f..55f9b1b` 通过。
- main 上最终静态扫描：7 个变更文件严格 UTF-8 全通过，乱码命中 0，混合行尾 0，孤立 CR 0，冲突标记 0；`git status --short` 为空。
- S1 最终判定：P0 无，P1 无，批准并封板。非阻断 P2 为 permission 口语变体仍有矩阵外浅规则风险，以及第三方角色识别仍依赖有限前缀表；这些不得继续阻塞 S1，也不得在 S3 偷渡成 parser 词表扩展。

## 已知迁移债务（不在 parser P1 中偷渡解决）

- correction/forget/supersede 尚未完成统一生命周期：例如 `T0: 我叫小王，但我很烦`，`T1: 别叫我小王，以后叫我小林，我很烦` 的旧小王 Evidence 仍可能保留。该债务必须在独立 correction/forgotten/purge 切片处理，不能继续堆 parser 特例。
- 部分迁移轮目前按整轮 ownership 选择 trusted 或 legacy；尚未支持 effect-level split commit。当前合同可接受，但后续迁移收口应避免长期维持双路 disposition。
- `growth_allowed` 当前仍是死字段；主体 alias canonical ID 与 kind/owner 元数据可能不一致；`create_subject_alias` 缺少 owner/kind 兼容校验。这些属于 S3 主体归属切片，不得在 S1 修复中顺带修改。

## S3 只读审计结论

- S3 只读审计 Thread ID：`019f6594-3c7e-7152-9ae1-61eb05a43d31`。
- 审计确认三个概念必须拆开：speaker identity、fact attribution、growth eligibility；当前 `growth_allowed` 未真正门控成长，alias 后 canonical ID 与 policy 元数据可能错配，alias 创建缺少 owner/kind 兼容校验，均为 P1。
- 建议 S3 新建 `core/xiaoxin/fact_attribution.py`，引入 `speaker/other/shared/ambiguous` 归属合同；Engine 组合归属与主体 policy，非 speaker claim fail-closed。S3 必须等待 S1 parser safety P1 修复并整合后再创建实现 worktree。
- 固件端 `D:\Learn\hzcu_xiaoxin_firmwire_private` 与小程序端 `D:\Learn\微信小程序\Hzcu_xiaoxin_miniprogram` 当前没有 Evidence/retention/subject policy 消费者；不得为 S1/S3 制造跨端协议变更。固件已有 `340 passed, 14 failed` 的 `InitializeProtocol` 静态测试漂移，小程序现有验证通过，均与本次记忆模块无关。

## S3 后台任务登记

- 实现任务 `记忆模块｜S3 主体归属实现`：Thread ID `019f66d5-e8da-7a82-aa0a-6b2881d579a9`；worktree `C:\Users\dell\.codex\worktrees\2c0c\xiaoxin-esp32-server`；分支 `codex/xiaoxin-s3-attribution`；精确基线 `4187a0dbab096dec26525183cb951ea0a750c5ad`。
- 实现任务目标：以 TDD 实现 speaker identity、fact attribution、growth eligibility 三层合同，关闭他人转述、共同主体、歧义主体、unknown/fallback/device subject、A/B 用户、设备切换和 alias/canonical 错配导致的个人 Evidence 或 growth/relationship 污染。
- 实现任务范围：identity、runtime、turn_analysis、MemoryEngine contracts/engine/evidence_store、可选新建 `fact_attribution.py` 及直接相关测试；禁止修改 S1 parser 口语词表、生命周期、成长状态机、topic/Episode/vector、固件、小程序和无证据的 requirements 产品范围。
- 实现任务验收：真实 Runtime→MemoryEngine→SQLite/legacy 投影闭环；alias owner/kind/policy fail-closed；S1 mixed-retention、普通事实零副作用、ownership、partial migration、幂等、无原文和 `persistence_allowed=False` 全部不回归；运行聚焦、Xiaoxin 全量、requirements、compileall、diff、编码/行尾/冲突标记检查；独立 commit、worktree clean、不得 push。
- 独立验收任务 `记忆模块｜S3 主体归属独立验收`：Thread ID `019f66d5-e810-7190-af1d-49ef63b618f3`；环境为主项目目录，只读。任务先建立基线攻击矩阵，未收到总任务给出的精确候选 commit 前不得批准或拒绝；收到候选后使用只读 Git object 或临时 archive 验证，不切换或污染 main。
- 独立验收任务必须亲自检查关键代码、真实 SQLite/投影、deletion test、S1 回归和全套静态检查；P0/P1 均无时才可批准，所有问题需带绝对路径和精确行号。
- 两个任务均已收到目标、工作范围、禁止修改范围、验收标准、应运行测试和交付内容；固件与小程序继续只读。

## S3 主体归属最终封板记录

- 实现任务最终候选分支为 `codex/xiaoxin-s3-attribution`，独立 worktree 为 `C:\Users\dell\.codex\worktrees\2c0c\xiaoxin-esp32-server`；精确批准对象为 `8860f932164ff4d4f311660a1db4d87b77e01bf9`，候选 worktree clean，未 push。
- S3 完整提交链为：`479f795 feat(xiaoxin): enforce S3 memory attribution ownership`、`a613605 fix(xiaoxin): recover split legacy commits`、`38e7e75 fix(xiaoxin): isolate followup effect attribution`、`45ca264 fix(xiaoxin): bind memory scope to canonical subject`、`8860f932 fix(xiaoxin): unify attribution connector grammar`。
- 多轮 P1 退回依次关闭：split/legacy 故障恢复与并发旧快照覆盖；followup effect 误归属；unknown/fallback/invalid subject 与 alias/canonical policy 错配；subject/scope 不一致；无标点 connector grammar、路径毒化、symlink 和 receipt/transcript 泄漏。实现任务每轮均收到追加修复要求，未以“测试全绿”替代产品合同验收。
- 最终合同将 speaker identity、fact attribution、growth eligibility 分离；在 clause/effect/candidate 粒度 fail-closed。非 speaker、共同、转述、歧义事实不得形成个人 durable Evidence 或推动 growth/relationship；unknown/device fallback 只允许受限 ephemeral 路径。
- alias 只允许同 owner、同 kind；解析后从 canonical subject 重建 owner/kind/policy 元数据。Runtime、MemoryEngine 和 Evidence Store 三层均拒绝 subject/scope mismatch；SQLite schema v3 使用 `FOREIGN KEY(turn_id, scope) REFERENCES memory_turns(turn_id, subject_id)`，v2→v3 迁移对非法历史数据回滚并拒绝启动。
- split commit 使用持久化 outbox、幂等发布和故障恢复；projection 路径按 scope 白名单约束并使用独立锁库串行化同 scope 写入。无 active followup 时 `next_hook` 失活，有其他 active followup 时指向正确项。
- 唯一授权的范围扩展是最小修改 `relationship_state.py`：只维护 followup active 集合与 `next_hook` 的一致性；未扩展 relationship level、mood TTL、growth 计数、correction、forget 或 purge。
- 独立验收任务批准精确对象 `8860f932`：P0 无，P1 无，置信度高；独立验收运行聚焦 `314 passed`、Xiaoxin 全量 `1120 passed`、requirements `10 passed`，compileall、diff、编码和行尾检查通过。唯一非阻断 P3 是 `test_control_handler.py` 两处在 S3 基线前已存在的乱码，不是本切片引入。
- 主任务亲自复核候选、关键代码和冲突风险后，在 clean 的 `main` 上执行非快进合并；共同基线为 `4187a0d`，主线文档提交为 `d711c2b`，合并提交为 `ca5cfd7b46397cda79fc1c7de255809504caba4f`。合并无冲突，完整保留已批准对象 `8860f932`，未 rebase、未 cherry-pick、未 push。
- main 上最终项目级验收：`tests/xiaoxin` 为 `1120 passed in 50.43s`；requirements workbench 为 `10 passed in 1.02s`；`python -m compileall -q core/xiaoxin` 通过；`git diff --check 4187a0d..ca5cfd7` 通过。
- main 上最终静态检查：18 个相对 `4187a0d` 的变更文件严格 UTF-8 失败 0、BOM 0、混合行尾 0、孤立 CR 0、新增乱码命中 0、冲突标记 0；工作区在台账修改前 clean。仓库 checkout 受 Git 行尾策略影响显示为 CRLF，但不存在混合行尾。
- S3 最终判定：P0 无，P1 无，批准并封板，置信度高。固件 `D:\Learn\hzcu_xiaoxin_firmwire_private` 与小程序 `D:\Learn\微信小程序\Hzcu_xiaoxin_miniprogram` 均未修改、未创建协议消费者、未运行写操作。
- 封板后剩余债务：correction/forget/supersede/purge 尚未进入统一生命周期；memory-control/forget 尚未全部进入统一 projection writer；全局 SQLite projection lock 仍可能阻塞无关 scope；多文件发布期间读者可能看到短暂不一致；目录项缺少掉电级 `fsync`；outbox 缺少运维指标、容量告警和人工恢复工具；`fact_attribution` 对 `profile_memory.preferred_name_intent` 有层次耦合；矩阵外复杂省略和长距离指代列为 P2，不继续无界扩词。

## S4 后台任务登记

- 只读审计任务 `记忆模块｜S4 称呼纠正生命周期只读审计`：Thread ID `019f67a4-4c7a-7a41-a9af-46fb31141d75`；环境为当前主项目目录，只读；启动基线为已整合 S3 的 `ca5cfd7b46397cda79fc1c7de255809504caba4f`。
- 审计目标：围绕 `T0: 我叫小王，但我很烦`、`T1: 别叫我小王，以后叫我小林，我很烦` 设计单一 RED→GREEN tracer bullet；旧“小王”Evidence 必须 superseded，新“小林” active，mood 继续按 ephemeral 独立处理，下一 session 只召回“小林”，explain 表达替代关系，T1 重放幂等。
- 审计范围：只读检查 contracts、engine、evidence_store、profile projection、turn analysis、runtime 与直接测试，建立 Runtime→MemoryEngine→SQLite→projection→next-session recall 攻击矩阵；给出最小 schema/API/state transition、迁移、并发、幂等和失败恢复边界。
- 禁止范围：不得编辑、提交、切换、回退或 push；不得扩 parser 词表；不得实现完整 forget/purge、C 语言成长状态机、topic block、Episode 或 vector；不得修改固件和小程序；不得制造长期 JSON/SQLite 双事实源。
- 验收与交付：结论必须带绝对路径和精确行号，列出 P0/P1/P2、冲突热点、RED 测试名与断言、聚焦及项目级测试命令，并交付可直接分配给独立 worktree 实现任务的 agent-ready brief；明确置信水平。
- 只读审计最终结论：只实现 preferred-name correction，不包含 standalone forget、完整 purge 或墓碑，置信度高。审计确认五个确定性断点：stop-address 在候选提取前整轮返回；新 Evidence 固定 active；schema 只允许 active/expired；召回扫描全部 active preferred-name；Runtime explain 仍读取旧 Profile JSON。建议以 schema v4、supersession relation、preferred-name projection 和同事务状态迁移关闭这些断点。
- 实现任务 `记忆模块｜S4 称呼纠正生命周期实现`：Client Thread ID `client-new-thread:1398ec92-0d23-4643-aa31-fc9bbd2dcca1`；实际 Thread ID `019f67b0-6fd5-77f3-b6ed-69e1c6ad8885`；独立 worktree `C:\Users\dell\.codex\worktrees\eb68\xiaoxin-esp32-server`；分支 `codex/s4-preferred-name-correction`；精确基线 `9b2f8f030db2e7bb0c3732cce8dd8bcde5c17818`。
- 实现任务目标：以 TDD 贯通 T0/T1 的 parser→MemoryEngine→SQLite→projection→next-session recall/explain。旧“小王”必须 superseded 且禁止 prompt injection，新“小林” active，替代边和 projection 唯一，T1 mood 独立 ephemeral，相同 turn_id 重放幂等，并发与事务失败不产生半纠正。
- 实现任务范围：turn_analysis、memory contracts/engine/evidence_store，必要时最小修改 runtime、profile explain/orchestrator seam 及直接相关 Xiaoxin 测试；允许 schema v3→v4 无损迁移。禁止扩 parser 词表、完整 forget/purge、成长状态机、topic block、Episode、vector、identity alias、requirements、固件和小程序；禁止长期 JSON/SQLite 双事实源和任何 push。
- 实现任务验收：真实 SQLite 与 Runtime 链证明 active→superseded、新值单一 active、替代关系、restart recall、deterministic explain、幂等、并发、乱序、回滚、migration 和 S3 attribution 防回归；运行聚焦、Xiaoxin 全量、requirements、compileall、diff、strict UTF-8/BOM/乱码/行尾/冲突标记检查；提交独立 commit 并保持 worktree clean。
- 独立验收任务 `记忆模块｜S4 称呼纠正独立验收`：Thread ID `019f67b9-5a88-78a2-9a1a-28dd2330e0d5`；环境为主项目目录，只读。候选提交产生前只建立 parser、schema v4、迁移、原子性、幂等、并发/乱序、restart recall、explain、S3 ownership、legacy/outbox 与 deletion-test 攻击矩阵；收到总任务发送的精确 commit 后才可验收，P0/P1 均无时方可批准，禁止修改、切换、push 或实现修复。

## S4 称呼纠正最终封板记录

- 实现分支为 `codex/s4-preferred-name-correction`，独立 worktree 为 `C:\Users\dell\.codex\worktrees\eb68\xiaoxin-esp32-server`，精确基线为 `9b2f8f030db2e7bb0c3732cce8dd8bcde5c17818`。最终提交链为：`325e153 feat(memory): implement preferred name correction lifecycle`、`f788e14 fix(memory): validate supersession audit history`、`83228eb fix(memory): harden preferred-name ordering and validation`；各提交均未 amend、未 push，最终 worktree clean。
- 第一候选 `325e153eea3c96af0e41f8b0f9d2c811b6746d88` 被主任务和独立验收共同拒绝。两个 P1 为：current-v4 只校验 projection、未扫描既有 supersession relation，因而接受跨 subject、错误 kind/status/replacement turn 的坏关系；relation 只禁止 UPDATE、不禁止 DELETE，审计边可被静默删除并使 explain 丢失旧值链。
- 第二候选 `f788e14a9dad33129643bb3fff94a64dc6b0e1f9` 关闭前述两个 P1，但再次被拒绝。三个新 P1 为：带时区偏移的合法 v3 历史按 ISO 文本而非真实时刻排序，导致 projection 倒退；current-v4 不拒绝缺少 outgoing relation 的孤立 superseded preferred-name Evidence；伪 `memory_evidence` 表只保留少量 status 片段即可绕过 JSON、confidence、retention、expiry、prompt 值域、复合 FK 和唯一键校验。
- 第三轮未提交修复期间，主任务复核发现 SQLite `julianday()` 会把相差 1 微秒的事件压成同一时刻，和 Python winner 比较形成双重排序语义；该实现未进入提交。最终统一使用 aware UTC `datetime` 的微秒精度 total-order，提交、current-v4 relation 验证和 explain 多入边选择复用同一比较器；非法或无时区 timestamp fail-closed，v3 迁移失败保持整体回滚。
- 最终对象 `83228eb978e68f3a0fc82ab4c0f29a986bb38f53` 完成 schema v4：`superseded` 状态、不可 UPDATE/DELETE 的 old→new 关系、preferred-name 唯一 active projection、完整 Evidence 表形状/FK/UNIQUE/CHECK 和既有行 `integrity_check`、每条 superseded preferred-name 必有审计边。T0/T1 后旧“小王”不可 prompt injection，新“小林”唯一 active；T1 mood 保持独立 ephemeral；restart/下一 session 只召回小林；same-turn replay 幂等；Runtime explain 走 `MemoryEngine.prepare_turn` 结构化短路，不读旧 JSON、不调用 LLM、不保存或返回 transcript。
- 独立验收任务在全新 archive 中批准精确对象 `83228eb`：P0 为 0，P1 为 0，置信度高。独立攻击覆盖跨 offset 正反序、同瞬间不同 offset tie-break、1 微秒排序、非法时间迁移回滚、existing relation 语义、orphan relation、弱 schema、非法既有行、projection-only recall、Runtime Evidence IDs、v2 整体回滚、并发升级、S3 ownership/scope/outbox。结果为 correction `51 passed`、memory/runtime/ownership 聚焦 `397 passed`、Xiaoxin 全量 `1171 passed`、requirements `10 passed`，compileall、diff、编码和行尾检查通过。
- 主任务亲自复核精确对象、祖先链和关键代码，运行新增 P1 定向组 `20 passed`，并用独立 SQLite 探针确认跨时区结果为“小林 active / 小王 superseded / projection 小林”，缺失审计边的 current-v4 启动为 `RuntimeError`。随后在 clean `main` 上执行非快进合并，无冲突；合并提交为 `2a83202 merge: integrate S4 preferred-name correction`，完整保留三条实现/修复提交，未 rebase、未 cherry-pick、未 push。
- main 上最终项目级验收：`tests/xiaoxin` 为 `1171 passed in 53.18s`；requirements workbench 为 `10 passed in 0.94s`；`python -m compileall -q main/xiaozhi-server/core/xiaoxin` 通过；`git diff --check 9b2f8f0..2a83202` 通过。
- main 上最终静态检查：相对 `9b2f8f0` 的 10 个变更文件严格 UTF-8 失败 0、BOM 0、混合行尾 0、孤立 CR 0、乱码命中 0、冲突标记 0；台账更新前工作区 clean。
- S4 最终判定：P0 无，P1 无，批准并封板。唯一非阻断 P2 为无标点输入 `别叫我小王以后叫我小林我很烦` 仍会退化为纯 memory control 并丢失新称呼和 mood；该变体不属于本次固定结构合同，不继续扩展为无界 parser 词表补丁。
- 跨端复核：固件仓库 `D:\Learn\hzcu_xiaoxin_firmwire_private` 保持 `2958246864953d6941f986cd962d2cfdc8202395`、工作区 clean；小程序仓库 `D:\Learn\微信小程序\Hzcu_xiaoxin_miniprogram` 保持 `c822f41008d9ad0f4e56c30b837a43ee022b076a`、工作区 clean。两端均未修改、未创建新协议消费者、未运行写操作。

## S5 C 语言 concern 后台任务登记

- 只读审计任务 `小新记忆模块｜S5 C 语言 concern 纵向切片只读审计`：Thread ID `019f6812-bb96-7882-a50a-77f7e21f19f9`；环境为当前主项目目录，只读；启动基线为 S4 封板后的 `ce44d3248bc325b9a0fd10dab634b398a9252737`。
- 审计任务目标：确定一个最小 RED→GREEN 输入和下一 session 查询，使真实 concern 成为有来源的 durable Evidence，同时将短期焦虑保留为独立 ephemeral mood；普通 C 语言知识问答零注入，主体归属、幂等、并发、乱序、迁移和 legacy/outbox 不回归。
- 审计任务范围：只读检查 TurnAnalysis、MemoryEngine、Evidence Store、Runtime、fact attribution 和 growth/relationship/companion 旁路，并用临时 SQLite/Runtime 公共接口复现当前断点；交付最小 Evidence 合同、recall gate、deletion test、RED 测试矩阵和可直接分配给独立 worktree 的实现 brief。
- 审计任务禁止范围：不得修改任何文件；不得把“C 语言”单关键词等同于 concern；不得实现 topic block、完整 concern/attempt/setback/progress/milestone 状态机、forget/purge、Episode、vector 或跨端协议；不得保存 transcript 或恢复 JSON 双事实源。
- 独立验收准备任务 `小新记忆模块｜S5 C 语言 concern 独立验收准备`：Thread ID `019f6812-e266-7d03-95c1-b754e6139c59`；环境为当前主项目目录，只读。候选产生前建立 parser、mixed retention、Evidence 最小化、recall 硬过滤、attribution、事务/幂等/并发/乱序、migration、legacy/outbox、deletion/mutation 和 S1/S3/S4 回归攻击矩阵；未收到精确候选 commit 前不得批准或拒绝，禁止实现修复。
- 两个任务均已收到目标、工作范围、禁止修改范围、验收标准、应运行测试和交付内容。会修改代码的实现任务必须等待只读审计收束后，使用新的独立 Git worktree 创建；固件与小程序继续只读。
- 两份只读任务已完成并由主任务用独立 Unicode/临时 SQLite 探针交叉复核。确定性 P1 断点为：目标句“最近有点担心 C 语言跟不上”被空白边界错误拆成 `speaker mood + other growth`，只写入 ephemeral mood；自然变体“我最近学 C 语言有点担心”则把 concern 写入 legacy companion/growth/relationship JSON，而 SQLite 仍只有 mood；显式回望目前可从 legacy JSON 注入 concern，普通指针知识问答则继续保持零注入。
- S5 最小合同固定为：`kind=concern`、`content={"topic":"c_language_learning","label":"C 语言学习"}`、`source=deterministic_rule`、`retention=durable`、`expires_at=NULL`、active、允许 prompt 但必须经过 Engine concern recall gate、speaker attribution；短期 anxious mood 继续作为独立 ephemeral Evidence。通用 schema v4 足够，本切片不新增 schema、migration 或专用 projection。
- 实现任务 `小新记忆模块｜S5 C 语言 concern Evidence 纵向切片实现`：Client Thread ID `client-new-thread:38df99c8-f0a1-46d4-a394-94f887a4e531`；实际 Thread ID `019f6820-b9b0-71c0-b2b4-7a94cc55e633`；独立 worktree `C:\Users\dell\.codex\worktrees\a13b\xiaoxin-esp32-server`；创建基线为当前 `main` 的 `a825b18fde0d0f4fe4296ed9c9e3a1cce06f3b53`，任务必须自行核对祖先链、分支和 clean 状态。
- 实现任务目标：严格 TDD 贯通精确 concern 句的 parser/fact attribution→MemoryEngine→SQLite→restart/new-session recall；同 turn 生成 durable concern 与 ephemeral mood，回望最多召回一条 active trusted concern 且自身不再写 concern，知识问答零注入，同一 concern 不再进入 legacy JSON 或 stale relationship/followup prompt。
- 实现任务范围：优先仅修改 `turn_analysis.py`、`fact_attribution.py`、`memory/engine.py`，确有必要时最小修改 `memory/contracts.py`、`runtime.py` 和直接相关测试；禁止修改 Evidence Store schema、台账、requirements、固件、小程序，禁止实现完整成长状态机、topic block、forget/purge、Episode、vector、关系等级或主动关心，禁止 push、rebase、reset、clean 和改写已有提交。
- 实现任务验收：目标句及无显式“我”的时间前缀变体均归属 speaker；other/shared/ambiguous/unknown/fallback/A-B 隔离 fail-closed；same-turn 幂等、并发、乱序和故障回滚不产生重复或半提交；旧 legacy concern 不进入 trusted recall prompt；schema 保持 v4；运行新增聚焦测试、turn analysis、memory engine/runtime/ownership、Xiaoxin 全量、requirements、compileall、diff、严格 UTF-8/BOM/行尾/乱码/冲突标记检查，提交独立 commit 并保持 worktree clean，不得 push。

## S5 C 语言 concern 快速落地记录

- 实现分支为 `codex/s5-c-language-concern-evidence`，独立 worktree 为 `C:\Users\dell\.codex\worktrees\a13b\xiaoxin-esp32-server`。最终提交链为：`98b00a7 feat(memory): persist and recall c language concern`、`eea5770 fix(memory): gate concern assertions and sanitize legacy prompts`、`6ecc8e4 fix(memory): isolate concern subjects and stale hooks`、`957b2d2 fix(memory): isolate per-clause concern effects`；各提交均为追加提交，未 amend、未 rebase、未 push，最终候选 worktree clean。
- 多轮独立验收先后拒绝前三个候选，关闭的问题包括：否定/假设/历史/引用误写；stale legacy concern 在 recall/free-chat 中旁路注入；concern 与 mood 跨子句错误绑定；later-current speaker 丢失；他人 concern 误归当前用户；有限人物词表导致姓名/第二人称/修饰角色 durable 污染；whole-turn growth 优先级吞掉合法 speaker concern；neutral C 语言子句吸附 generic code concern；malformed followup 冒充 progress 保护 stale hook。
- 最终实现按 clause/effect 分离 concern subject 与 mood experiencer；只有可证明为 speaker、current、affirmative 的 C 语言 concern 才产生 durable Evidence。非空且无法证明为 self 的 possessor fail-closed；speaker mood 可独立保留。无 kind 或损坏的 hook/followup 只有存在肯定 progress 证据时才允许保留；否定 progress、未知 kind 和缺失结构不能保护 stale hook。普通知识问答与普通 free-chat 继续零 trusted concern recall。
- S5 Evidence 合同保持：`kind=concern`、`content={"topic":"c_language_learning","label":"C 语言学习"}`、`source=deterministic_rule`、`retention=durable`、`expires_at=NULL`、active、`allow_prompt_injection=1`；anxious mood 为独立 ephemeral Evidence。schema 保持 v4，无 migration、专用 projection、固件或小程序协议变更。
- 用户明确要求先快速落地、由用户进行产品验收，过程中发现不足再追加整改。因此最终对象 `957b2d29095a8b1029830f41c02e7e4e7bae55c9` 在完成核心安全门槛后直接整合，不再等待新一轮穷举式预合并独立验收。主任务在候选上亲自运行 parser/engine/runtime/ownership 聚焦冒烟 `323 passed`，父链、worktree clean、`git diff --check` 和三方冲突分析通过。
- 主任务在 clean `main` 上执行非快进合并，合并提交为 `6ff28fa464b7c4031e3bfdb7d619508c31257cfe`（`merge: integrate S5 c language concern evidence`），完整保留四条实现/修复提交，无冲突，未 push。
- main 上项目级验收：`python -m pytest tests/xiaoxin -q` 为 `1270 passed in 58.69s`；requirements workbench 为 `10 passed in 1.16s`；`python -m compileall -q core/xiaoxin`、`git diff --check 22a79c4..6ff28fa` 均通过；合并后工作区在台账修改前 clean。
- 当前交付状态为“工程集成通过，等待用户产品验收”。若用户验收发现自然语言覆盖或交互体验不足，作为新的有界整改切片处理，不回写或改写 S5 历史提交。

## S6 C 语言成长状态最小切片任务登记

- 只读审计任务 `小新记忆模块｜S6 C 语言成长状态机最小纵向切片只读审计`：Thread ID `019f68e9-25fb-7b83-b180-fc4c24b1f796`；环境为当前主项目目录，只读；启动基线为 S5 快速整合与台账提交后的 `0c41e30d626e80ca1aa68b0d7d969b2242207bd3`。
- 审计职责：用当前真实 TurnAnalysis、MemoryEngine、SQLite 与 Runtime 探针对比 concern 后的 `attempt`、`progress` 及二者合并方案，选择唯一最小下一切片；交付黄金故事、Evidence 合同、最小 `growth_state.py` 纯函数 seam、Evidence ID 引用、incremental/rebuild 等价、legacy ownership、recall gate、P0/P1 攻击矩阵、RED→GREEN 测试和 agent-ready 实现 brief。
- 禁止范围：不得修改任何文件、提交、切换或 push；不得实现完整九事件状态机、topic block、forget/purge、Episode、vector、主动关心、关系等级、跨端协议或无界 parser 词表。固件与小程序继续零修改。
- 独立验收准备任务 `小新记忆模块｜S6 最小成长切片独立验收准备`：Thread ID `019f68ed-232f-7bd3-9a6a-c4f092b44a3c`；环境为当前主项目目录，只读；启动基线为 `e2c953640ec3eca19c4a5d547dcdef1bb95678fc`。任务在实现候选产生前并行建立 attempt/progress 共用的 P0/P1 攻击矩阵、候选验收命令、关键代码审查点、删除/变异测试与冲突热点；收到精确候选 commit 前不得批准或拒绝。
- 验收准备禁止修改整个服务端仓库、固件和小程序，不得提前选择产品方案，不得扩展到完整状态机、forget/purge、topic、Episode、vector、关系等级或 UI。允许的交付仅为只读代码证据、临时 SQLite/Runtime 探针结果及候选到达后可直接执行的验收 brief。
- 主任务依据当前代码与 Unicode 探针确定下一切片为 `attempt-only`：`我今天开始练 C 语言指针了。` 已稳定产生 speaker-owned 规范 `growth_event=attempt`，但没有 trusted candidate，仍落入 legacy；对照句 `我这次终于把指针题做出来了。` 当前没有规范成长事件且归属分析不稳定。为避免把 parser 扩展、归属修复和两个事件绑成一个高风险切片，本轮不实现 progress。
- 实现任务 `小新记忆模块｜S6 C 语言 attempt Evidence 最小纵向切片实现`：Client Thread ID `client-new-thread:3ba5b2f8-a3b4-44ce-b641-5f5befd1c9e1`；实际 Thread ID `019f68f3-bb55-7352-8392-1b9bc44b1c03`；独立 worktree `C:\Users\dell\.codex\worktrees\4a52\xiaoxin-esp32-server`；创建基线为 `main` 的 `9da8d1c`，目标分支 `codex/s6-c-language-attempt-evidence`。
- 实现职责：按公共接口 TDD 贯通 concern→attempt→restart/new-session 明确回望；新增最小纯函数 `growth_state.py`，只承诺 concern/attempt，状态为 struggling→trying，事件必须携带 Evidence ID，增量 apply 与 rebuild 对乱序/重复输入等价；attempt 写入最小 durable Evidence，普通知识/闲聊零注入，并停止 attempt 的 legacy growth/relationship/followup 双写。禁止 progress、完整九事件状态机、schema v5/新表、topic/forget/purge/Episode/vector/关系等级、requirements/台账与跨端修改；最终须提交独立 commit 链、全量测试、requirements、compileall、diff 与编码检查，不得 push。
- 自此登记之后，新创建的后台 Codex 任务统一指定模型 `gpt-5.6-luna`、思考强度 `max`；已经运行中的任务不为切换模型而中断或重建，后续追加整改 turn 同样使用 Luna + max。
- 后续只读预审任务 `小新记忆模块｜S6 后续成长事件最小切片只读预审`：Thread ID `019f690a-1f1a-7a32-a5bf-53ddd8458018`；环境为当前主项目目录，只读；模型为 `gpt-5.6-luna`、思考强度 `max`，启动时 `main` 预期为 `10660df29fb17927d082dad96cf776bcc7e7a899`。职责是在 attempt-only 候选收尾期间，用真实 TurnAnalysis、归属分析、Engine 与临时 SQLite/Runtime 探针对比 `progress/setback/pause/restart/milestone/reflection/resolve/reopen`，选择唯一下一最小事件切片，并交付黄金故事、Evidence/状态合同、P0/P1 攻击矩阵和可直接分配的实现 brief；禁止修改代码、创建 worktree、批准当前 attempt 候选或扩展到 Task 7 以后范围。

## S6 C 语言 attempt 快速落地记录

- 实现分支为 `codex/s6-c-language-attempt-evidence`，独立 worktree 为 `C:\Users\dell\.codex\worktrees\4a52\xiaoxin-esp32-server`。最终追加提交链为：`ebc802cbe94fa2cfb5f81aaad954b3b299db102d feat(memory): persist and recall c language attempts`、`ac8cd21ae6d90752d246246c58eb338a67583ccf fix(memory): filter legacy growth on trusted recall`；未 amend、未 rebase、未 push，最终 worktree clean。
- 首个候选由独立验收拒绝，P1 为明确 trusted growth reflection 仍会读取 legacy `progress` followup：SQLite 没有 progress Evidence 时，旧 relationship JSON 的 `LEGACY_PROGRESS_RAW` 仍进入 system prompt，形成双事实源。整改只在 Runtime 构造 prompt 的 relationship 副本上过滤 C 语言 legacy followup/next_hook/growth_intent/core_concern/recent_topic，不修改磁盘 legacy 文件；ordinary free-chat 继续保留未迁移 affirmative progress 的既有 legacy prompt。
- 独立验收任务使用 `gpt-5.6-luna`、思考强度 `max`，对精确新对象 `ac8cd21` 在 fresh archive 中最终 `APPROVE`，P0/P1 均为 0、置信度高。验收覆盖空 trusted Evidence 与 concern+attempt 两种 reflection、relationship 文件字节不变、ordinary progress 兼容、删除/变异杀伤、主体隔离、并发、回滚、schema v4、无 transcript 和 S1–S5 回归。
- 最终实现新增 `growth_state.py` 纯 reducer，只承诺 `concern -> struggling` 与 `attempt -> trying`；事件按 UTC `occurred_at -> turn_id -> sequence -> evidence_id` 排序，重复 Evidence 幂等，冲突 duplicate 整体 fail-closed，非法 kind/topic/status/time fail-closed，incremental/rebuild/continuation 等价。成长回望最多取最近一条 concern 与一条 attempt，使用真实 Evidence IDs 和安全结构化摘要；普通 C 语言知识、闲聊和单纯提及零成长注入。
- attempt Evidence 合同为 `kind=attempt`、`content={topic=c_language_learning,label=C 语言学习,action=practice,focus=pointer}`、`source=deterministic_rule`、`confidence=0.9`、durable、active、`expires_at=NULL`、允许 prompt；首次提交 `growth_advanced=true`、重放为 false，`relationship_advanced` 始终 false。trusted attempt 停止 companion/episode/growth_arc/relationship/followup/next_hook legacy 双写；progress 与 attempt+progress 同轮仍保持未迁移边界。
- 主任务亲自运行候选聚焦 `413 passed`，整改后故事与 ownership 聚焦 `89 passed`；候选全量 `1314 passed`、requirements `10 passed`，commit blob UTF-8/BOM/混合行尾/孤立 CR/乱码/冲突标记和 merge-tree 均通过。随后在 `main` 非快进合并，合并提交为 `9356d983704c6ba7dbd073c19d2fafd361dda0ec`（`merge: integrate S6 c language attempt evidence`）。
- main 合并后项目级验收：`python -m pytest tests/xiaoxin -q` 为 `1314 passed in 60.07s`；requirements workbench 为 `10 passed in 1.19s`；compileall、`git diff --check 6949206..9356d98` 与 7 个合并文件的 commit-blob 严格扫描均通过。固件保持 `2958246864953d6941f986cd962d2cfdc8202395`、小程序保持 `c822f41008d9ad0f4e56c30b837a43ee022b076a`，两端工作区 clean 且零修改。
- 主目录存在与本切片无关的用户未跟踪目录 `output/patent-disclosure`；全程原样保留，未读取内容用于实现、未加入任何提交，也不影响候选变更路径或 merge-tree 结果。

## S6 下一成长事件决策

- 只读预审任务 `019f690a-1f1a-7a32-a5bf-53ddd8458018` 最终选择唯一下一切片为 `progress-only`，置信度高；固定输入为 `我最近终于搞懂 C 语言指针了。`。该句当前稳定产生 speaker-owned `growth_event.kind=progress`，但仍只有 legacy；对照句 `我这次终于把指针题做出来了。` 当前归属与事件语义不稳定，禁止顺带支持。
- progress 最小合同：新增 durable active `kind=progress` Evidence，content 仅保留 `topic=c_language_learning`、`label=C 语言学习`，不保存原始句、summary 或 assistant reply；schema 保持 v4。`growth_state.py` 仅增加 `progress -> progressing`，黄金链为 concern -> attempt -> progress -> restart/new-session reflection，回望只使用 trusted Evidence IDs。
- progress 切片必须停止其 legacy growth/relationship/followup/companion/episode 双写，但 ordinary 未迁移事件继续兼容。setback/pause/restart/milestone/reflection/resolve/reopen 暂缓：它们当前要么无稳定事件、要么被误判、要么依赖历史状态/纠正语义，不与 progress 绑定实现。
- progress 实现任务 `小新记忆模块｜S6 C 语言 progress Evidence 最小纵向切片实现`：Client Thread ID `client-new-thread:4918a80d-39ed-4bc9-b3fb-e984e3dd5bad`；实际 Thread ID `019f694e-42fa-73c0-8b0f-a6d114e861ec`；独立 worktree `C:\Users\dell\.codex\worktrees\18d4\xiaoxin-esp32-server`；模型 `gpt-5.6-luna`、思考强度 `max`，创建代码基线为 `9356d983704c6ba7dbd073c19d2fafd361dda0ec`，目标分支 `codex/s6-c-language-progress-evidence`。职责是严格 TDD 贯通固定 progress 句的 TurnAnalysis -> trusted Evidence -> `progressing` reducer -> Runtime restart reflection，并关闭 progress 自身全部 legacy 双写；禁止其它成长事件、Task 7、schema/requirements/台账/跨端和用户 `output/` 修改。
- progress 独立验收准备任务 `小新记忆模块｜S6 progress-only 独立验收准备`：Thread ID `019f694e-e481-7c40-be6e-90ce1770f5c3`；当前主项目目录只读；模型 `gpt-5.6-luna`、思考强度 `max`。职责是在候选产生前建立至少 20 条 P0/P1 攻击矩阵、archive 验收命令、关键审查点、删除/变异杀伤与冲突热点；重点覆盖 progress 与 other mood 的 effect-level 隔离、ac8cd21 trusted-reflection/ordinary-progress 双向合同、主体/幂等/并发/回滚/乱序/schema v4/无 transcript。收到精确候选 commit 前不得批准或拒绝，禁止修改任何仓库。

## S6 C 语言 progress 快速落地记录

- 实现分支为 `codex/s6-c-language-progress-evidence`，独立 worktree 为 `C:\Users\dell\.codex\worktrees\18d4\xiaoxin-esp32-server`，创建基线为 `9356d983704c6ba7dbd073c19d2fafd361dda0ec`。最终追加提交链为：`b3bf32063a6bf4ceb0dae7d3633c68ce0c7bd08f feat(memory): add trusted c language progress evidence`、`4378165d52f98853f56c0f9aecbfc9b6c90c4906 fix(memory): fail closed unsafe progress legacy paths`、`d51df98a3814fc9b4d30ed9b0198a57c4ca69b83 fix(memory): fail closed temporal-negative progress markers`；未 amend、未 rebase、未 push，最终 worktree clean。
- 首候选 `b3bf320` 被主任务和独立验收拒绝。P0/P1 断点为：`我最近终于搞懂 C 语言指针了，可能是室友的成果。` 与 `我最近终于搞懂 C 语言指针了吗？` 虽无 trusted candidate，却仍由 legacy 写入 companion、episode、growth arc 和 relationship；纯 `了？/了?` 因 clause splitter 丢失问号而误生成 trusted progress。整改增加精确 target persistence block、原始问号断言门控和 progress legacy disposition 清理，同时保持 statement + other question clause 的 effect 边界。
- 第二候选 `4378165` 再次被拒绝。`我最近没有/还没/并没有/尚未/不算 搞懂 C 语言指针。` 及同结构 `跑通 C 语言代码` 会被宽泛 outcome marker 误判为 legacy progress。最终改为统一 progress marker assertion seam：现有 `跑通/搞懂/会了/解决了/有进展/好多了` 只有至少一个 marker 的 `_assertion_mode_for_marker` 为 current 时才形成 progress；否定、历史和假设全部 fail-closed，不改写为 positive concern。affirmative legacy `我最近在 C 语言上有进展了。` 与 `我把 C 语言代码跑通了。` 继续兼容。
- 最终 trusted progress 合同为 `kind=progress`、`content={topic=c_language_learning,label=C 语言学习}`、`source=deterministic_rule`、`confidence=0.9`、durable、active、`expires_at=NULL`、允许 prompt；不保存原句、summary、assistant reply 或 transcript。`growth_state.py` 只新增 `progress -> progressing`；黄金链 concern -> attempt -> progress 在 restart/new session 回望中按真实 `occurred_at -> turn_id -> sequence -> evidence_id` 顺序重建，prompt 使用真实 Evidence IDs 和安全结构化摘要。
- trusted progress 首次提交 `growth_advanced=true`，same-turn 重放为 false，`relationship_advanced` 始终 false；固定 progress 自身停止 companion/episode/growth_arc/relationship/followup/next_hook legacy 双写。progress + speaker mood 产生 durable progress 与 ephemeral mood 两条独立 Evidence；progress + other mood 保留合法 speaker progress、不误存他人 mood；preferred-name + progress 两条 trusted candidate 均保留。topic control `别再提 C 语言了。` 继续保留既有 `next_hook.active=false`，但零 progress 污染。
- 独立验收任务使用 `gpt-5.6-luna`、思考强度 `max`，对精确最终对象 `d51df98` 给出 `APPROVE`，P0/P1 均为 0、置信度高。独立验收覆盖 10/10 temporal-negative Engine/真实 Runtime 矩阵、mixed outcome、吗问句、全角/半角问号、other question clause、affirmative legacy 双向兼容、unknown/fallback、growth/persistence disabled、A/B/alias、topic control、reflection、schema v4、无原文、幂等/并发/回滚/乱序；结果为 `tests/xiaoxin` 1388 passed、requirements 10 passed，compileall、commit-blob 与 merge-tree 通过。
- 主任务亲审三条 production diff 和冲突风险，亲自复现并推动两轮追加整改；最终候选上运行聚焦 `490 passed`、全量 `1388 passed`、requirements `10 passed`，commit-blob 严格扫描和虚拟 merge-tree 通过。随后在 `main` 非快进合并，无冲突；合并提交为 `d9d894df3d9b7dea01a12f404ec792262c465b40`（`merge: integrate S6 c language progress evidence`），完整保留三条实现/整改提交，未 push。
- main 合并后项目级验收：`python -B -m pytest tests/xiaoxin -q -p no:cacheprovider` 为 `1388 passed in 67.41s`；聚焦为 `490 passed in 18.03s`；requirements workbench 为 `10 passed in 1.38s`；compileall、`git diff --check 6811a68..d9d894d` 与 4 个合并文件的 commit-blob 严格扫描均通过。固件保持 `2958246864953d6941f986cd962d2cfdc8202395`、小程序保持 `c822f41008d9ad0f4e56c30b837a43ee022b076a`，两端工作区 clean 且零修改。
- 主目录用户未跟踪目录 `output/patent-disclosure` 全程原样保留，未读取内容用于实现、未加入提交。当前状态为“progress 工程集成通过，可交用户产品验收”；自然语言覆盖不足若在产品验收中出现，作为新的有界整改切片处理，不改写本提交链。
- 下一切片排序只读审计任务 `小新记忆模块｜progress 合并后的唯一下一切片排序审计`：Thread ID `019f6979-3fbf-7e40-b211-37b0ec4cb53e`；模型 `gpt-5.6-luna`、思考强度 `max`。职责是基于合并后的真实代码比较 Task 6 剩余事件与 Task 7 correction/forget/topic block/purge，选择唯一下一 tracer bullet，交付合同、至少 15 条 P0/P1 矩阵和 agent-ready brief；严格只读，禁止创建实现任务或修改任何仓库。

## S7 C 语言 topic block 快速落地任务登记

- 下一切片排序审计 `019f6979-3fbf-7e40-b211-37b0ec4cb53e` 已完成，唯一结论为 `Task 7.1 topic block-only`，优先级 P0、置信度高（0.92）。原因是当前“别再提 C 语言了”只会失活 legacy `next_hook`，没有持久化 subject-scoped topic control；旧 trusted Evidence 与 legacy growth/followup 仍存在后续 prompt 旁路。剩余 Task 6 事件缺少稳定 parser 或依赖 correction/forget 生命周期，因此暂缓。
- 固定黄金闭环：先建立 C 语言 concern -> attempt -> progress trusted Evidence；用户在独立 session 输入 `别再提 C 语言了。`；该轮写入 `topic=c_language_learning` 的 durable block control 并立即关闭 legacy followup/next_hook；服务重启或新 session 后，普通回望、闲聊和主动入口对 C 语言零 Evidence 召回、零 prompt injection、零 legacy 线索。原 Evidence 保留，不在本切片实现 forget、purge、unblock 或 reopen。
- schema 决策固定为 v5：新增独立 `memory_topic_controls` 控制表，禁止把 block 伪装成普通 `MemoryEvidence`。v4 -> v5 必须无损、事务化、并发与幂等安全；控制记录仅保存 canonical subject、固定 topic/action、turn/time/fingerprint 等安全结构，不保存原始句、assistant reply 或 transcript。
- 实现任务 `小新记忆模块｜S7 C 语言 topic block 最小纵向切片实现`：Client Thread ID `client-new-thread:e7075c28-daf9-42bd-9f6b-58a636d966f1`；实际 Thread ID `019f69bf-7865-7152-9cff-8a2496bcb8fb`；独立 worktree `C:\Users\dell\.codex\worktrees\4438\xiaoxin-esp32-server`；模型 `gpt-5.6-luna`、思考强度 `max`；创建基线为 `main` 的 `1f8d21fdba6d6dc6d298dd3864c0d6b7f1942962`，目标分支 `codex/s7-c-language-topic-block`。
- 实现职责：严格 RED -> GREEN 贯通 bounded topic-refusal -> MemoryDirective -> SQLite schema v5 control -> 原子 commit -> restart recall hard gate -> legacy prompt suppression；覆盖 A/B 与 alias、unknown/fallback、否定/假设/历史/引用/问句/other/shared/ambiguous/mixed、同 turn 重放、并发、回滚、迁移、旧 JSON 旁路和无原文。禁止修改 growth_state 其他事件、correction/forget/purge/unblock、Profile/Episode/vector/关系等级/UI/协议、requirements/计划/台账、固件、小程序和用户 `output/`；不得 push。
- 独立验收准备任务 `小新记忆模块｜S7 topic block 独立验收准备`：Thread ID `019f69bf-77b5-7d53-b415-a4d38dc668f3`；当前主项目目录严格只读；模型 `gpt-5.6-luna`、思考强度 `max`。职责是在候选产生前建立至少 25 条 P0/P1 攻击矩阵、archive/临时数据库验收命令、真实 Runtime -> SQLite -> restart -> prompt 探针、迁移/并发/回滚/编码/merge-tree 审查点；收到精确候选 HEAD 前不得批准或拒绝，禁止修改任何仓库。
- 后续依赖顺序固定为：topic block-only -> C 语言成长 Evidence correction/supersede -> forget 与 projection rebuild -> setback-only -> pause-only -> restart-only -> milestone-only -> resolve-only -> reopen-only -> reflection-as-event -> purge-only。不得把这些项目合并成一次性大状态机或完整 Task 7 重写。
- 后续只读预审任务 `小新记忆模块｜S8 C 语言成长 Evidence correction/supersede 只读预审`：Thread ID `019f69f2-17f0-7161-b203-4d780973ff95`；当前主项目目录严格只读；模型 `gpt-5.6-luna`、思考强度 `max`。职责是在 S7 收尾期间，基于当前真实 concern/attempt/progress Evidence、preferred-name supersession seam 与 S7 schema v5 前置依赖，选择唯一 correction/supersede tracer bullet，交付黄金跨 session 故事、状态/替代边/事务/迁移合同、至少 25 条 P0/P1 攻击矩阵和可直接分配的实现 brief。禁止修改代码、创建 worktree、批准或整改 S7、实现 forget/purge/unblock 或其它成长事件，并继续保持固件、小程序和用户 `output/` 零修改。
- S8 只读预审已完成，唯一推荐为固定句 `纠正一下：我之前说已经搞懂 C 语言指针了，其实还没有，我只是开始练 C 语言指针。` 的 progress -> attempt supersession，置信度高；只允许纠正同一 canonical subject 下唯一 active、durable、trusted、精确 `c_language_learning` progress。旧 progress 变为 superseded 且禁止 prompt，新 attempt active；concern 与原 attempt 保持 active，重启后 reducer 从 progressing 回退为 trying。主任务已亲自复核现有 trigger 只允许 preferred_name -> preferred_name，并用纯 reducer 探针确认 concern + attempt + superseded progress + replacement attempt 的结果为 `trying`，因此不得在 schema v5 下静默改 trigger。
- S8 schema 决策固定为 v6：依赖 S7 先完成 v4 -> v5，然后 v5 -> v6 事务化扩展既有 `memory_evidence_supersessions`，只增加精确 C 语言 progress -> attempt 合法边；不得泛化为任意 Evidence supersession。预审交付 60 条 P0/P1 矩阵，覆盖唯一目标、A/B/alias、unknown/fallback、否定/引用/问句/假设/他人/shared/ambiguous、active S7 block、幂等/并发/乱序/回滚、v5 -> v6 migration、future/invalid schema、legacy bypass 和 no transcript；当前 main 只读验证为聚焦 `432 passed`、Xiaoxin 全量 `1388 passed`。S8 实现不得早于 S7 整合，不包含 forget/purge/unblock/reopen 或其它成长事件。

## S7 C 语言 topic block 最终封板记录

- 实现分支为 `codex/s7-c-language-topic-block`，独立 worktree 为 `C:\Users\dell\.codex\worktrees\4438\xiaoxin-esp32-server`，实现差异基线为 `5b5a48a66c336cb6a51b834cd2912379efc0cef5`。最终提交链为：`7a68e396de922e110dda0a4bbcee43e401498f26 feat(memory): enforce c language topic block`、`25bd0ca9e394f1dd37fd4bf5a21efc37bf01ec61 fix(memory): close legacy topic block entry points`、`b030a40df4b5dfe10e6d77a20aa92028cc5170da test(memory): cover legacy prompt entry points`、`8ae45e8fb7aa5fdb1265f9a5999d69ee435986af fix(memory): restore generic greeting payload`；均为追加提交，未 amend、未 rebase、未 push，最终 worktree clean。
- schema 从 v4 升级到 v5，新增独立 `memory_topic_controls`；topic block 不伪装成 Evidence。合法 v4 无损事务化升级，未来/空壳/部分/非法 schema fail-closed，失败后版本、schema 与 journal mode 不被修补；同 turn 重放、双 Engine 并发与事务回滚保持幂等。
- exact block 与 block + preferred-name / mood 均走 deterministic local-rule，先提交 control 与允许的 trusted Evidence，再固定回复，LLM 调用为 0。pending directive 在 commit 前进入 recall hard gate；A/B、canonical alias、unknown/fallback、否定/假设/历史/引用/问句/other/shared/ambiguous/mixed 均按合同隔离。
- 第一候选 `7a68e396` 被独立验收拒绝。P1 为 legacy relationship JSON、公开 `XiaoxinTurnResult.relationship`、`followups_prompt()` 与 `greeting_payload()` 仍保留 active C hook/followup；另有 same-turn 过滤测试伪覆盖，删除 `blocked_topics` 过滤后仍会绿。整改改为复用 split/outbox 的 staged callback，只发布 relationship 投影、关闭精确 C hook/followup 与 C intent/core，保留非 C `course_rhythm`、非 C intent/core 和其他 legacy 文件；新增 mutation-kill 测试直接证明无过滤 recall 含 concern/attempt/progress，而 pending block 后全部过滤。
- 第二候选 `25bd0ca` 再次被拒绝。关闭纯 C 主动入口后，现有 `greeting_payload()` 落入未定义的 `_generic_payload()` 并抛 `NameError`。最终恢复历史稳定 generic payload 合同，并覆盖首次、同日重复与 restart；pure-C persisted projection 的 `followups_prompt`、`prompt_summary` 与 greeting 均零 C，mixed 非 C 入口继续可用。
- 独立验收任务使用 `gpt-5.6-luna`、思考强度 `max`，对最终对象 `8ae45e8fb7aa5fdb1265f9a5999d69ee435986af` 给出 `APPROVE`，P0/P1 均为 0。独立结果：修复专项 `24 passed`、四文件/扩展聚焦 `524 passed`、Xiaoxin 全量 `1450 passed`、requirements `10 passed`；compileall、diff、10 个 commit blob 编码/行尾/乱码/冲突标记检查与对 `main=b14fc0d` 的 merge-tree 均通过。
- 主任务亲自复现三个候选的主动入口、mutation-kill 与 pure-C generic greeting 断点，亲审生产 diff、schema 初始化顺序、legacy split/outbox、非 C 数据保留和冲突风险；最终候选上追加运行 topic-block 聚焦 `41 passed`、pure-C Runtime -> relationship -> greeting 探针、10 个 blob 扫描和 merge-tree，全部通过。
- 主任务在 clean `main=b14fc0dd0568f2fa81686c299d238a34750c4b9f` 上执行非快进合并，合并提交为 `b2c46860c8c755d43a795f3532f48f8b30b6ae61`（`merge: integrate S7 c language topic block`），无冲突，完整保留四条实现/整改提交，未 push。
- main 合并后项目级验收：`python -B -m pytest tests/xiaoxin -q -p no:cacheprovider` 为 `1450 passed in 65.37s`；requirements workbench 为 `10 passed in 1.02s`；隔离 compileall、`git diff --check b14fc0d..b2c4686` 与 10 个 main commit blob 严格扫描均通过。
- 固件仓库保持 `2958246864953d6941f986cd962d2cfdc8202395`、小程序仓库保持 `c822f41008d9ad0f4e56c30b837a43ee022b076a`，两端工作区 clean 且零修改。主目录用户未跟踪 `output/` 原样保留，未读取其业务内容、未加入提交。S7 当前状态为“工程集成通过，可交用户产品验收”；后续进入 S8 correction/supersede。

## S8 C 语言 progress -> attempt correction/supersede 任务登记

- 实现任务 `小新记忆模块｜S8 C 语言 progress→attempt correction/supersede 最小纵向切片实现`：Client Thread ID `client-new-thread:48f63aa8-a12c-4624-b5bd-090da6f0d526`；实际 Thread ID `019f6a90-c40e-7742-8ca7-ce0421433c49`；独立 worktree `C:\Users\dell\.codex\worktrees\4240\xiaoxin-esp32-server`；分支 `codex/s8-c-language-progress-attempt-correction`；创建基线为 `main=9d613b2d0bd64274d92defc21d037af2338f00d6`；模型 `gpt-5.6-luna`、思考强度 `max`。
- 实现任务唯一职责：固定句 `纠正一下：我之前说已经搞懂 C 语言指针了，其实还没有，我只是开始练 C 语言指针。` 只纠正同一 canonical subject 下唯一 active/durable/trusted C-language progress；schema v5 -> v6，在同一事务中将旧 progress superseded/禁止 prompt、新 attempt active、建立唯一不可变 progress -> attempt edge，重启后 reducer `progressing -> trying`。禁止 forget/purge/unblock/reopen、其它成长事件、Episode/vector/UI/协议、固件、小程序、requirements/计划/台账、真实 data 与 `output/`。
- 实现任务验收：严格 RED -> GREEN 与 mutation-kill，覆盖预审 C-01..C-60、prepare 固定 target、commit-time S7 block 复查、幂等/并发/乱序/回滚、v5 -> v6/fake v5/v4/future/invalid、legacy bypass 与 no transcript；运行 S8 专项/扩展聚焦、Xiaoxin 全量、requirements、隔离 compileall、diff、编码扫描和 merge-tree，提交独立 commit 链并保持 worktree clean，不得 push。
- 独立验收准备任务 `小新记忆模块｜S8 progress→attempt correction/supersede 独立验收准备`：Thread ID `019f6a90-c200-7691-99e3-1a5ccdd9f973`；环境为当前主项目目录严格只读；模型 `gpt-5.6-luna`、思考强度 `max`。职责是把预审 C-01..C-60 转为可执行 P0/P1 矩阵，准备候选 archive、Runtime -> SQLite -> restart -> prompt、v5 -> v6 migration/rollback、并发/竞态、no transcript、commit blob 与 merge-tree 探针；候选到达前不得批准/拒绝或实现修复。
- S8 两个任务均已明确目标、工作范围、禁止修改范围、验收标准、应运行测试与交付内容。实现与验收分离；只有独立验收 P0/P1 均为 0、主任务亲审并完成 main 项目级门禁后才允许整合。
- 原 S8 实现任务在完成三条提交 `e03a062`、`e822077`、`e75d1ca` 及一组未提交 RED→GREEN 后发生后台调度停滞；原 worktree `C:\Users\dell\.codex\worktrees\4240\xiaoxin-esp32-server` 保持原状，不重置、不清理、不继续作为唯一关键路径。主任务创建 recovery 实现任务 `小新记忆模块｜S8 progress→attempt correction recovery 实现与封板`：Client Thread ID `client-new-thread:fe153841-2ce8-4dfc-a888-5e979c622293`；实际 Thread ID `019f6ac9-59d4-7a33-bfdf-42e4337ad453`；独立 worktree `C:\Users\dell\.codex\worktrees\7920\xiaoxin-esp32-server`；模型 `gpt-5.6-luna`、思考强度 `max`；从原分支已提交 `e75d1ca` 起步，目标分支 `codex/s8-correction-recovery`。
- recovery 任务职责是只读提取原 worktree 相对 `e75d1ca` 的未提交 diff，在新 worktree 用补丁精确重建并继续当前 near-miss RED（无前缀 correction-shaped、他人引用、C 语言链表 legacy growth_event），随后补齐 C-01..C-60、mutation-kill、Runtime→SQLite→restart→prompt、v5→v6 migration/rollback/fake schema、全量测试与 clean candidate。禁止修改原 worktree、改写原三条提交、缩减 S8 合同、触碰 S9/后续事件、跨端、真实 data 或 `output/`。

## S9 forget + projection rebuild 只读预审任务登记

- 只读预审任务 `小新记忆模块｜S9 forget + projection rebuild 唯一下一切片只读预审`：Thread ID `019f6aa1-e741-7f30-91b4-3f11f678c3e9`；环境为当前主项目目录严格只读；模型 `gpt-5.6-luna`、思考强度 `max`。
- 任务目标是在 S8 实现与验收期间并行比较 preferred-name 字段级 forget、精确 C 语言成长 Evidence forget、C topic growth forget 等候选，只选择唯一最小 tracer bullet；交付黄金跨 session 故事、输入与 target 合同、forgotten/prompt permission/projection rebuild/legacy suppression/schema 迁移合同、至少 40 条 P0/P1 攻击矩阵和可直接分配的实现 brief。
- 禁止修改、暂存、提交、建分支、建 worktree 或 push；禁止实现 purge、setback/pause/restart/milestone/resolve/reopen/reflection-as-event、Episode/vector/关系等级/主动关心/UI/协议；禁止读取用户 `output/` 业务内容、真实 data、固件或小程序业务内容。该任务不得批准、拒绝或整改 S8，完成后只向主任务交付预审结论。
- S9 只读预审已完成：S7/v5 主线基线 `tests/xiaoxin` 为 `1450 passed`，S9 相关聚焦为 `421 passed`，仓库零修改。唯一推荐为在 S8 v6 已独立验收并整合后，忘记一组精确 `progress -> replacement attempt` correction unit，并在同一事务内重建 C 语言成长投影；schema 决策为 v7，新增 `forgotten` 状态与不可变 forget control/tombstone，不实现 purge、preferred-name forget 或 topic-wide forget。
- 主任务拒绝了预审首版只提旧 progress 却同时删除 replacement attempt 的过度删除语义。最终唯一固定句修订为：`请忘掉这次 C 语言指针进度纠正，包括“我已经搞懂 C 语言指针了”和“我只是开始练习 C 语言指针”这两条记录。`；只允许机械去空白和可选句末句号。只有该双目标句且唯一合法 S8 P/A edge 存在时，才固定两个 Evidence ID；任一端缺失、多个 pair、future、非 trusted、只提单端或 broad forget 均 fail-closed。
- 预审当时的状态合同为：旧 progress `superseded -> forgotten`、replacement attempt `active -> forgotten`，两端 `allow_prompt_injection=0`，concern 保持 active，topic block、preferred-name、mood、非 C Evidence 与 legacy 文件字节保持不变。该版把 restart reducer 误写为固定 `struggling`；后续基于 S8 真实 original-attempt 保留合同的复审已在下文修正为“按剩余 active Evidence 重建”。trusted gate 必须早于 broad legacy memory control。预审交付 60 条 F-01..F-60 P0/P1 矩阵、mutation-kill、v6 -> v7 migration/rollback 与 agent-ready brief；S8 未整合前不得创建 S9 实现任务。

## S8 C 语言 progress -> attempt correction/supersede 最终封板记录

- 原实现任务恢复后继续使用 `codex/s8-c-language-progress-attempt-correction` 与 worktree `C:\Users\dell\.codex\worktrees\4240\xiaoxin-esp32-server`；recovery worktree `C:\Users\dell\.codex\worktrees\7920\xiaoxin-esp32-server` 仅保留未提交备援状态，未作为候选、未清理、未合并。
- 精确基线为 `9d613b2d0bd64274d92defc21d037af2338f00d6`。最终提交链为：`e03a062793d6b4f9d7cfb657911f8ece92df0771 feat(xiaoxin): classify fixed growth correction command`、`e8220776ac1c74bbabcb8ecaa9279c40647c3b4a feat(xiaoxin): pin correction target during prepare`、`e75d1ca628bc626747f6c193a78800779902e049 feat(xiaoxin): atomically apply growth correction`、`19f823f34aa94cdfc8d23c9a940b5cb6ffd3a854 feat(xiaoxin): harden correction acceptance and replay`；均为追加提交，未 amend、未 rebase、未 push，最终实现 worktree clean。
- schema 从 v5 升级到 v6，只把既有 supersession relation 精确扩展为 preferred-name -> preferred-name 与 trusted C-language progress -> deterministic correction attempt 两类合法边。旧 progress 内容/source/confidence/retention/time 不改，只变 `superseded` 和 prompt permission；replacement source 固定 `deterministic_correction_v1`。relation 与 endpoint、subject、time、content、source、status、permission 均由应用层和 trigger/validator 双重约束。
- 主任务在 TDD 收口中发现并推动修复：near-miss 退化 legacy growth、v1-v4 migration、preferred-name v6 history、future schema 错误优先级、same-turn replay fingerprint、并发 loser old_* alias、Runtime 通用 aliases、deferred turn flags、commit-time block、fake-v6 trigger、transaction rollback，以及 SQLite `julianday()` 丢失微秒和 `strftime('%s')` 跨秒四舍五入。最终 trigger 使用 aware-time whole-second UTC epoch + 6 位 fraction 的 strict `<`，同时保留 raw timestamp 格式校验；同秒 1 微秒、跨秒 `.999999 -> .000001`、offset 字符串逆序、同 instant 不同 offset、非法 fraction 均有直接测试。
- 最终候选 `19f823f34aa94cdfc8d23c9a940b5cb6ffd3a854` 由独立验收任务使用 Git objects/archive 验收并 `APPROVE`：C-01..C-60 为 60/60 PASS，P0/P1 均为 0。独立结果为 correction `59 passed`、扩展聚焦 `662 passed`、Xiaoxin 全量 `1509 passed`、requirements `10 passed`、定向补验 `18 passed`；compileall、9 个 commit blob、diff、merge-tree 与五类 mutation-kill 映射均通过。
- 主任务亲审 parser、prepare 固定 target、commit transaction/replay、v6 trigger/validator、Runtime result 与 legacy suppression；独立运行真实 Runtime -> SQLite -> restart -> prompt 探针，确认 prompt 恰好包含 concern + replacement attempt、stage=`trying`，旧 progress/original attempt 不入 prompt，SQLite 状态为 concern/original attempt/replacement active、old progress superseded，relation 唯一且 old prompt permission 为 0。
- 主任务在 clean `main=d4148c4d9a96ddb223247f179a61cdf0536fa719` 上执行非快进合并，合并提交为 `196712a6c3ce69c87dd23328541ac6970ddb5e51`，无冲突，完整保留四条实现提交，未 push。main 合并后项目级验收为扩展聚焦 `662 passed`、Xiaoxin 全量 `1509 passed`、requirements `10 passed`；隔离 compileall、`git diff --check d4148c4..196712a` 与 9 个合并 blob 严格扫描通过。
- 固件仓库保持 `2958246864953d6941f986cd962d2cfdc8202395`，小程序仓库保持 `c822f41008d9ad0f4e56c30b837a43ee022b076a`；两端未修改。主目录用户未跟踪 `output/` 原样保留、未读取业务内容、未加入提交。S8 状态为“工程集成通过，可交用户产品验收”。

## S9 correction-unit forget 合同修订与实现任务登记

- S9 预审复审确认原“forget 两端后固定 struggling”结论错误。真实 reducer 只消费 active Evidence：标准 A 链 `concern -> original attempt -> progress -> correction` 忘记 correction unit 两端后仍保留 original attempt，结果必须为 `trying` 且 restart prompt 重新包含 original attempt；B 链 `concern -> progress -> correction` 才只剩 concern、结果为 `struggling`。不得为了得到 struggling 对 original attempt 做 broad delete 或 kind-wide suppression。
- 固定句保持：`请忘掉这次 C 语言指针进度纠正，包括“我已经搞懂 C 语言指针了”和“我只是开始练习 C 语言指针”这两条记录。`；只移除 Unicode whitespace、句末一个句号可省。target 必须是同一 canonical subject 下唯一合法 S8 progress -> replacement attempt relation，两端同时 forgotten、prompt permission 关闭，relation 保留，projection 按剩余 active Evidence 重建。
- 主任务进一步固定用户控制优先级：active S7 topic block 不得阻止该精确 forget；forget 成功后 block 保持 blocked、不得 unblock。原因是 forget 是更强的用户删除控制，执行它不会恢复或扩大 topic 使用；把 block 当作拒绝 forget 条件会违背用户显式遗忘请求。
- 实现任务 `小新记忆模块｜S9 精确 correction-unit forget + active-Evidence rebuild 最小纵向切片实现`：Client Thread ID `client-new-thread:f96a53b3-26f3-465c-b569-d6161fc7e063`；实际 Thread ID `019f6b38-f149-7441-80d8-dceb2c63673f`；独立 worktree `C:\Users\dell\.codex\worktrees\b752\xiaoxin-esp32-server`；创建基线 `main=196712a6c3ce69c87dd23328541ac6970ddb5e51`；目标分支 `codex/s9-c-language-correction-unit-forget`；模型 `gpt-5.6-luna`、思考强度 `max`。
- 实现任务职责：严格 TDD 贯通 fixed parser -> prepare 固定两端 -> schema v7 forgotten/tombstone -> 单事务双端状态迁移 -> active-Evidence rebuild -> Runtime restart A/B prompt；覆盖 F-01..F-60、幂等/并发/竞态/回滚、v6->v7/fake/future/invalid、no transcript、legacy parser priority、relation/tombstone不可变和 mutation-kill。允许修改 memory contracts/engine/evidence_store/turn_analysis、必要时最小 runtime/legacy gate 和直接测试；禁止 purge、topic-wide/preferred-name/original-attempt forget、其它成长事件、跨端、requirements/计划/台账/HANDOFF、真实 data 与 `output/`。交付独立追加提交链、真实 A/B 探针、全量门禁与 clean worktree，不得 push。
- 独立验收准备任务 `小新记忆模块｜S9 correction-unit forget 独立验收准备`：Thread ID `019f6b38-f0c6-7733-b95e-1ec2ebc8ff56`；当前主项目目录严格只读；模型 `gpt-5.6-luna`、思考强度 `max`。职责是在候选前把修订 F-01..F-60 转为可执行 P0/P1 矩阵，准备 Git archive、schema v7/migration rollback、Runtime -> SQLite -> restart A/B、block 保留、no transcript、mutation、blob 与 merge-tree 探针；候选到达前不得批准/拒绝或实现修复，收到精确候选后 P0/P1 均为 0 才可批准。
- S9 实现与验收继续分离。主任务必须亲审 target/transaction/schema/legacy gate、A/B stage 与 prompt、topic block 交互、测试结果和冲突风险；独立批准后才允许整合。

## S10 setback-only 只读预审任务登记

- 只读预审任务 `小新记忆模块｜S10 setback-only 下一最小成长事件只读预审`：Thread ID `019f6b47-3931-7670-892f-59e05553ab09`；环境为当前主项目目录严格只读；模型 `gpt-5.6-luna`、思考强度 `max`。
- 任务目标是在 S9 实现期间只读选择并固定 S9 之后唯一最小的 `setback-only` tracer bullet。任务必须用真实 TurnAnalysis、归属分析、growth reducer、MemoryEngine、Runtime 与临时 SQLite 比较安全候选句，给出唯一固定输入、机械归一化与 fail-closed 边界、黄金跨 session 故事、Evidence/reducer/Runtime 合同、至少 40 条 P0/P1 攻击矩阵和可直接分派的实现 brief。
- 工作范围仅限服务端决策文档、`turn_analysis.py`、`fact_attribution.py`、memory contracts/engine/evidence_store/growth_state、Runtime 与直接相关测试的只读检查；允许无污染临时目录、临时 SQLite 和 pytest basetemp 探针。任务必须比较 progress -> setback、无前史/重复/乱序 setback，并只把 milestone -> setback 作为依赖分析，禁止把 milestone 一并实现。
- 禁止任何仓库写操作、Git 状态修改、worktree 或 push；禁止批准、拒绝、整改或实现 S9；禁止 pause/restart/milestone/resolve/reopen/reflection-as-event、purge、其它 forget/unblock、Episode/vector、关系等级、主动关心、UI/协议与跨端改动；禁止读取用户 `output/` 业务内容或真实 data。
- 验收与交付必须明确允许/禁止文件、与 S9 的冲突热点、单行为 RED -> GREEN 顺序、公共接口断言、mutation-kill、只读测试命令及结果、P2/P3 和置信水平。由于实现文件与 S9 高度重叠，S10 实现 worktree 只能在 S9 独立批准并整合后创建。
- S10 只读预审已完成，唯一推荐固定句为 `我最近又搞不懂 C 语言指针了。`，置信度高。当前真实 parser 会把它识别为 speaker-owned concern + ephemeral frustrated mood，但没有 trusted setback；当前 reducer 也会忽略 synthetic setback。最窄实现 seam 是精确句 recategorization，并只增加 `setback -> struggling`，不扩展“卡住/退步/失败/受挫”等词表，不新增 stage 或 schema，不实现 milestone。
- 预审黄金合同为 progress -> setback 后 concern/attempt/progress/setback 保持 active、stage 回到 `struggling`；无前史 setback 只产生 setback 且同样为 `struggling`；S9 forgotten endpoints 不得复活，后续 setback 只基于剩余 active Evidence 重建；active S7 topic block 下不得新增 setback Evidence、不得回退 legacy，也不得改变 block 或 S9 精确 forget 优先级。
- 只读任务运行两组聚焦测试共 `610 passed`（418 + 192），Git HEAD 始终为启动对象 `2ca66c8d2cc6ab19035c6cd44a59fc37dc21a4ac`，tracked diff 为零，未读取 `output/` 业务内容。任务已交付 60 条 P0/P1 矩阵、RED -> GREEN 顺序、mutation-kill、冲突图及可直接分派的 S10 实现 brief；S9 未封板前不创建 S10 实现 worktree。

## S9 correction-unit forget 最终封板记录

- 原实现 worktree `C:\Users\dell\.codex\worktrees\b752\xiaoxin-esp32-server` 与分支 `codex/s9-c-language-correction-unit-forget` 保留为历史实现路径；最终批准对象来自 recovery worktree `C:\Users\dell\.codex\worktrees\b691\xiaoxin-esp32-server`、分支 `codex/s9-full-regression-recovery`，精确基线为 `196712a6c3ce69c87dd23328541ac6970ddb5e51`，最终 HEAD 为 `5e8a2afc51051ebd76dd4de0e2bffa3ffd183fd9`，worktree clean，未 push。
- 最终追加提交链为：`89e8bfd feat(xiaoxin): add v7 correction-unit forget seam`、`4bc62e7 test(xiaoxin): lock S9 migration and forget atomicity`、`20b2647 test(xiaoxin): reject half-built forget units`、`71420f5 fix(xiaoxin): scope v7 duplicate correction validation`、`5e8a2af fix(xiaoxin): recover S9 full regression suite`。首个候选全量曾为 `1514 passed, 20 failed`；其中 18 项为旧测试/伪旧 schema fixture，2 项为真实 validator 回归，最终分别修复 v7 correction replacement 去重误伤 preferred-name 合法 fan-in，以及 current-v7 漏验 preferred-name projection insert/update trigger。
- schema 从 v6 升级到 v7，新增 `forgotten` 状态与结构化 `memory_evidence_forget_units` tombstone。固定句只允许移除 Unicode whitespace 和省略句末一个 `。`；prepare 固定唯一合法 S8 progress/replacement 两端，commit 在单个 `BEGIN IMMEDIATE` 事务中复核 endpoint、relation、subject、status、source、time 与 fingerprint，随后原子写入 tombstone 并把两端设为 `forgotten`、`allow_prompt_injection=0`。S8 relation 保留且不可更新/删除，tombstone 不可更新/删除，forgotten Evidence 不可复活；不保存原句、history、assistant reply、transcript 或 source text。
- active S7 topic block 不阻止该精确 forget；forget 后 block 仍为 `blocked`，不得 unblock。projection 和 growth stage 只按剩余 active Evidence 重建：A 链保留 concern + original attempt，restart 为 `stage=trying` 且 prompt 包含 original attempt ID；B 链只保留 concern，restart 为 `stage=struggling`。forgotten progress/replacement 不进入 recall、structured growth、普通 prompt 或 restart prompt。
- 独立验收任务 `019f6b38-f0c6-7733-b95e-1ec2ebc8ff56` 使用 `gpt-5.6-luna`、思考强度 `max`，对精确对象 `5e8a2af` 给出 `APPROVE`，P0=0、P1=0。独立补验确认 F-19 prepare 后新增第二 correction unit 仍只忘记 pinned unit；F-20 endpoint/relation 竞态 deferred 且无部分写；F-43 v6->v7 注入失败时版本、DDL、数据和 journal 完整回滚；F-51/F-52 真实 Runtime A/B restart prompt 符合合同；F-55 无 transcript/blob/history/assistant 泄漏。独立结果为 S9 专项 `86 passed`、完整 tracked archive `1539 passed`、requirements `10 passed`，compileall、diff、blob 编码/行尾/乱码/冲突标记与 merge-tree 均通过。
- 主任务亲审 parser、legacy control bypass、prepare target pinning、commit transaction、v7 trigger/validator、migration、recall hard filter、Runtime result 与 block 交互；在候选上复跑 S9 专项 `86 passed`、requirements `10 passed`、隔离 compileall、diff、11 个变更 blob 扫描和 merge-tree `919fecf483e20216b4dcf759166ce366b6713516`，并独立补做 v6->v7 validation 注入失败与真实 Runtime A/B 探针，全部通过。`memory_orchestrator.py` 的 UTF-8 BOM 在 S9 基线已存在，不是本切片新增问题。
- 主任务在 tracked-clean、仅保留用户未跟踪 `output/` 的 `main=3bb52e6ca899506dae52682b00fa58fc4ddbdb17` 上执行非快进合并；合并提交为 `b7921de98f035a6ecf1933784c088b7438e03549`（`merge: integrate S9 correction-unit forget`），无冲突，完整保留五条实现/整改提交，未 rebase、未 cherry-pick、未 push。
- main 合并后项目级验收：`python -B -m pytest tests/xiaoxin -q -p no:cacheprovider` 为 `1539 passed in 81.23s`；requirements workbench 为 `10 passed in 1.09s`；隔离 compileall 与 `git diff --check 3bb52e6..b7921de` 通过。固件仓库保持 `2958246864953d6941f986cd962d2cfdc8202395`、小程序仓库保持 `c822f41008d9ad0f4e56c30b837a43ee022b076a`，两端工作区 clean 且零修改；用户未跟踪 `output/` 原样保留、未读取业务内容、未加入提交。S9 状态为“工程集成通过，可交用户产品验收”；下一切片进入 S10 setback-only。

## S10 setback-only 实现与独立验收任务登记

- 实现任务 `小新记忆模块｜S10 C-language setback-only 最小纵向切片实现`：Client Thread ID `client-new-thread:df1c14eb-6e7f-4f5d-8b1b-57d470fe64ff`；实际 Thread ID `019f6bd2-071e-75f2-839f-4527cf8677bd`；独立 worktree `C:\Users\dell\.codex\worktrees\e4c4\xiaoxin-esp32-server`；创建时为 detached HEAD `4a3d047319ce0e396b9b8f82fc8cb6a12b4bd523`，目标分支 `codex/s10-c-language-setback-only`；模型 `gpt-5.6-luna`、思考强度 `max`，不得静默降级。
- 实现任务唯一目标是用严格 TDD 为固定句 `我最近又搞不懂 C 语言指针了。` 增加 trusted setback Evidence，并只扩展 `setback -> struggling`。范围限于 turn analysis、memory contracts/engine/evidence_store/growth_state、必要时最小 runtime/fact attribution 和直接测试；不新增 schema v8、新表或新 stage，不扩展“卡住/退步/失败/受挫”等词表。
- 实现任务禁止修改 memory orchestrator、companion/growth arc/relationship state、S7/S8/S9 生命周期、requirements/HANDOFF/计划/台账、data/、`output/`、固件、小程序、UI/协议、Episode/vector/关系等级，以及 pause/restart event/milestone/resolve/reopen/reflection/purge/其他 forget/unblock。不得 amend、rebase 或 push。
- 实现验收覆盖预审 S10-01..S10-60：精确 parser/归属/route fail-closed、固定 Evidence 字段与 no transcript、no-history 和 progress->setback reducer、UTC offset/微秒/乱序/duplicate、事务回滚/幂等/two-writer、restart prompt、S7 block、S8 correction、S9 forgotten/tombstone 非回归、legacy bytes 与 non-C legacy 保留、mutation-kill；运行新增单文件、扩展聚焦、Xiaoxin 全量、requirements10、隔离 compileall、diff、commit blob 扫描、merge-tree 和真实 Runtime A/B/C/D 探针。交付独立追加提交链、精确候选 HEAD、测试结果和 clean worktree，不得 push。
- 独立验收任务 `小新记忆模块｜S10 setback-only 独立验收`：Thread ID `019f6bd2-071c-72b0-b9e5-faa3318491ca`；环境为当前主项目目录严格只读；模型 `gpt-5.6-luna`、思考强度 `max`，不得静默降级。职责是在候选到达前把 S10-01..S10-60 转为可执行 P0/P1 矩阵，准备 Git objects/完整 tracked archive、临时 SQLite、Runtime A/B/C/D、schema v7、block/forget/correction 非回归、mutation、blob 与 merge-tree 探针；收到总任务给出的精确候选后从零独立验收。
- 独立验收禁止修改、暂存、提交、切换分支、建 worktree 或 push，禁止实现整改、读取 `output/`/真实 data/跨端业务内容；任一 P0/P1 失败或关键证据未知必须 `REJECT`，只有 P0=0、P1=0 才可 `APPROVE`。实现与验收继续分离，主任务还必须亲审关键代码、测试覆盖、事务/迁移与冲突风险，并在 clean main 上完成项目级门禁后才允许整合。

## S11 pause-only 只读预审任务登记

- 只读预审任务 `小新记忆模块｜S11 pause-only 下一最小成长事件只读预审`：Thread ID `019f6bd8-2a7e-7423-b783-3d4d84c5bf28`；环境为当前主项目目录严格只读；启动基线为 `main=9e6bf2388a5c5548088473da7084c8259131fc51`；模型 `gpt-5.6-luna`、思考强度 `max`，不得静默降级。
- 任务目标是在 S10 实现与验收期间并行选择 S10 之后唯一最小的 C-language pause-only tracer bullet，比较 progress/setback/attempt/no-history -> pause，并严格区分 pause 与 S7 topic block、S10 setback、普通休息/疲劳 mood、abandon/resolve/forget/purge 及 future restart intent。若现有 parser 无稳定候选，只允许提出最窄 deterministic seam，不扩展“休息/累/不学了”等无界词表。
- 工作范围仅限服务端决策/实施文档、turn analysis、fact attribution、memory contracts/engine/evidence_store/growth_state、Runtime、legacy growth/relationship/companion 旁路和直接测试的只读检查；允许系统临时目录、临时 SQLite、Git objects/archive 与无缓存 pytest basetemp。禁止仓库写操作、批准/拒绝/整改 S10、实现 pause 或 restart/milestone/resolve/reopen/reflection/purge、改写 S7/S8/S9/S10、跨端/UI/协议/Episode/vector/关系等级/主动关心，以及读取 `output/`/真实 data。
- 预审必须交付唯一固定句、归一化与 fail-closed 边界、黄金跨 session 故事、Evidence/reducer/Runtime/legacy/no-transcript 合同、至少 60 条 P0/P1 攻击矩阵、RED->GREEN 顺序、mutation-kill、允许/禁止文件、与 S10 的冲突热点、只读测试结果和可直接分派的 S11 实现 brief。S11 实现 worktree 只有在 S10 独立批准并整合后才允许创建。

## S10 setback-only 最终封板记录

- 原实现任务 `019f6bd2-071e-75f2-839f-4527cf8677bd` 在 worktree `C:\Users\dell\.codex\worktrees\e4c4\xiaoxin-esp32-server` 长期停留于基线分析，未形成候选。主任务从 `4a3d047319ce0e396b9b8f82fc8cb6a12b4bd523` 创建 recovery worktree `C:\Users\dell\.codex\worktrees\s10r\xiaoxin-esp32-server` 与分支 `codex/s10-setback-recovery`，最终提交链为：`b071a40 feat(xiaoxin): add trusted c language setback`、`7bd7d6b fix(xiaoxin): fail closed setback routes`、`546fc44 test(xiaoxin): harden setback near misses`、`f7ed469 fix(xiaoxin): fail closed setback-shaped near misses`；均为追加提交，未 amend、未 rebase、未 push，最终 worktree clean。
- 固定句为 `我最近又搞不懂 C 语言指针了。`，只允许删除 Unicode whitespace 与省略句末一个全角 `。`。精确 `free_chat` 只产生 durable/trusted `setback` Evidence 与独立 ephemeral `frustrated` mood，不再留下 concern；Evidence 固定为 `kind=setback`、`content={"topic":"c_language_learning","label":"C 语言学习"}`、`source=deterministic_rule`、`confidence=0.9`、`retention=durable`、`status=active`、`allow_prompt_injection=true`。reducer 只增加 `setback -> struggling`，schema 保持 v7，无新表、新 trigger 或新 stage。
- 目标句在非 `free_chat`、缺 route 时完全 fail-closed。active S7 block 下只允许独立 mood，不新增 setback/concern/legacy C growth，block 原样保留；S9 forgotten progress/replacement 不复活；concern/attempt/progress/setback 均按 active Evidence 重建，多个 setback 只把最新一个注入明确成长回望；不保存原句、history、assistant reply、transcript 或 source text。
- 第一轮最终候选 `546fc443bea20a2a21d5eb0ee9805a44c90ed5fe` 被独立验收任务 `019f6bd2-071c-72b0-b9e5-faa3318491ca` 明确 `REJECT`：P0=1、P1=0。ASCII `.`、双 `。。` 与 U+200B 虽不生成 setback，却退回旧 parser 写入 durable concern；原测试只断言“无 setback”，属于伪绿。主任务亲自复现后采用 TDD 在 legacy concern parser 前增加窄 fail-closed seam，并新增 parser -> MemoryEngine -> 真实 Runtime 的 zero-Evidence/zero-legacy deletion test；相近 U+200C/U+200D/U+2060/U+FEFF 仅在移除后精确等于固定句时同样 fail-closed，不扩展普通 concern 词表。
- 辅助快速验收任务 `小新记忆模块 S10 setback-only 快速最终独立验收`：Thread ID `019f6c09-ddd5-7410-b2ad-8a079531a7ea`；模型 `gpt-5.6-luna`、思考强度 `max`、严格只读。该任务用于缩短旧候选复核等待，旧对象作废后被要求停止，不作为最终批准依据。
- 独立验收任务使用 `gpt-5.6-luna`、思考强度 `max`，对最终对象 `f7ed4694d7603c9d54373c69526bf5305a7496d4` 给出 `APPROVE`，P0=0、P1=0。独立结果为 S10 专项 `17 passed`、扩展聚焦 `830 passed`、Xiaoxin 全量 `1556 passed`、requirements `10 passed`；baseline/candidate Unicode-escape 黑盒、原 P0 zero pollution、Runtime A/B/C/D、S9 forget、schema v7、compileall、Git archive/blob 与 merge-tree 全部通过。
- 主任务亲审 parser 优先级、near-miss fail-closed seam、Engine topic block 过滤、legacy suppression、growth prompt selector 与 reducer；候选上运行专项 `17 passed`、相关聚焦 `250 passed`、Xiaoxin 全量 `1556 passed`、requirements `10 passed`，compileall、diff、4 个 commit blob UTF-8/LF/BOM/冲突标记扫描和对当时 main 的 merge-tree `f7db9e5dddeebd3e12b25c15046e5479cf737b8e` 均通过。
- 主任务在 tracked-clean、仅保留用户未跟踪 `output/` 的 `main=bc3058f353e70591cf7ec79c41a9262b44daf4e7` 上执行非快进合并；合并提交为 `4c8b6bcce0bca19c0947404e770fba8ae2237e2d`（`merge: integrate S10 c language setback evidence`），无冲突，完整保留四条实现/整改提交，未 push。
- main 合并后项目级验收：`python -B -m pytest tests/xiaoxin -q -p no:cacheprovider` 为 `1556 passed in 82.45s`；相关聚焦为 `250 passed in 12.17s`；requirements workbench 为 `10 passed in 1.16s`；compileall 与 `git diff --check bc3058f..4c8b6bc` 通过。固件保持 `2958246864953d6941f986cd962d2cfdc8202395`、小程序保持 `c822f41008d9ad0f4e56c30b837a43ee022b076a`，两端工作区 clean 且零修改；用户 `output/` 未读取、未修改、未提交。S10 状态为“工程集成通过，可交用户产品验收”。

## S11 pause-only 预审完成记录

- 只读预审任务 `019f6bd8-2a7e-7423-b783-3d4d84c5bf28` 已完成并交付可直接实现 brief，模型 `gpt-5.6-luna`、思考强度 `max`，置信度高。唯一固定句为 `我最近暂停练习 C 语言指针了。`；只允许删除 Unicode whitespace 与省略句末一个全角 `。`，ASCII 句点、双句号、额外子句、引用/转述/否定/假设/历史、future restart intent、非 `free_chat`、非 confirmed speaker 均 fail-closed。
- S11 Evidence 固定为 `kind=pause`、`content={"topic":"c_language_learning","label":"C 语言学习"}`、`source=deterministic_rule`、`confidence=0.9`、`retention=durable`、`status=active`、`allow_prompt_injection=true`；reducer 只增加 `pause -> trying`，保留 S10 `setback -> struggling`，不新增 `paused` stage，不升级 schema v7，不新增表或 trigger，不实现 restart。
- active S7 block 下不得写 pause 或 legacy fallback；S9 forgotten endpoints 不复活；S10 setback 保持 active；普通 prompt 不主动注入 pause，不生成 next hook、主动关心或 restart，只有明确成长回望/新 session 可用安全结构化摘要重建。禁止 legacy growth/followup/companion/episode/relationship 双写和任何 transcript/source text。
- 预审交付 90 条 P0/P1 攻击项、RED -> GREEN 顺序、mutation-kill、允许/禁止文件与 S10 冲突热点；只读聚焦基线为 `640 passed in 32.32s`。S10 已封板，下一步允许创建 S11 独立实现 worktree与独立验收任务。

## S11 pause-only 实现与独立验收任务登记

- 实现任务 `小新记忆模块｜S11 C-language pause-only 最小纵向切片实现`：Client Thread ID `client-new-thread:c47a92fa-8ee1-47c0-b6c8-3730f6ed0ed6`；实际 Thread ID `019f6c1e-9bf4-7471-9420-42852553a818`；独立 worktree `C:\Users\dell\.codex\worktrees\1af2\xiaoxin-esp32-server`；分支 `codex/s11-c-language-pause-only`；创建基线 `main=0c74d46f16433f0222d33d6d22cfac3f27af066a`；模型 `gpt-5.6-luna`、思考强度 `max`，不得静默降级。
- 实现任务唯一职责：用严格 TDD 为固定句 `我最近暂停练习 C 语言指针了。` 增加 trusted/durable/active `pause` Evidence，固定 content/source/confidence/permission，并只扩展 `pause -> trying`；保留 S10 `setback -> struggling`，不新增 schema、表、trigger 或 stage，不实现 restart。首次 pause `growth_advanced=false`、`relationship_advanced=false`；明确成长回望/新 session 只使用安全结构化 active Evidence，普通 prompt 不主动注入或生成 restart。
- 实现范围优先限于 `turn_analysis.py`、`memory/engine.py`、`memory/growth_state.py` 与新 `test_memory_pause_story.py`；确有必要才最小修改 fact attribution/contracts/evidence store/runtime。必须在 legacy attempt fallback 前关闭 ASCII `.`、双句号、U+200B/U+FEFF、额外子句和 future-restart 等 pause-shaped near-miss，并用 parser -> Engine -> 真实 Runtime deletion test 证明 0 pause/attempt/concern/legacy C growth。active S7 block、S9 forgotten、S10 setback、subject/scope/policy、幂等/并发/回滚/no-transcript 和 legacy 文件字节均为 P0 门禁。
- 实现任务禁止修改 requirements/HANDOFF/计划/台账、legacy growth/relationship/companion 实现本身、S7/S8/S9/S10 合同、restart/milestone/resolve/reopen/reflection/purge/其它 forget/unblock、Episode/vector/关系等级/主动关心/UI/协议/跨端、真实 data 与 `output/`；不得 amend、rebase 或 push。交付独立追加提交链、精确 HEAD、文件范围、专项/聚焦/全量/requirements/compileall/blob/merge-tree 与 Runtime 故事，最终 worktree clean。
- 独立验收任务 `小新记忆模块｜S11 pause-only 独立验收准备`：Thread ID `019f6c1e-d735-7752-9e32-7e280617b3a4`；当前主项目目录严格只读；启动基线 `main=0c74d46f16433f0222d33d6d22cfac3f27af066a`；模型 `gpt-5.6-luna`、思考强度 `max`，不得静默降级。
- 验收任务职责是在候选前把预审合同转成至少 60 条可执行 P0/P1 矩阵，准备 Git objects/fresh archive、临时 SQLite 与 Runtime A no-history、B progress、C setback、D S9 forget、E active block、F pause -> later setback 探针；候选到达后只验收总任务给出的精确 HEAD。任一 P0/P1 或关键证据未知必须 `REJECT`，只有 P0=0、P1=0 才可 `APPROVE`；严格禁止修改、实现整改、读取排除数据或 push。

## S12 restart-only 只读预审任务登记

- 只读预审任务 `小新记忆模块｜S12 restart-only 下一最小成长事件只读预审`：Thread ID `019f6c23-6efd-78f3-b65b-77ba76155d5d`；当前主项目目录严格只读；启动基线 `main=078c929dc93b0f478e38467455e0fa3983d8ac81`；模型 `gpt-5.6-luna`、思考强度 `max`，不得静默降级。
- 任务目标是在 S11 实现与验收期间并行比较 `我最近重新开始练 C 语言指针了。`、`我最近又开始练 C 语言指针了。` 等候选，选择 S11 之后唯一最小 restart-only tracer bullet；必须严格区分首次 attempt、已有 pause 后恢复、setback 后再次尝试、future restart intent、topic unblock/resolve/forget 和普通“重新看了一遍”。
- 预审必须决定 restart 是否只允许在已有 active pause 前置下提交，并研究 prepare 后 pause 新增/失效/阻断的 commit-time 竞态复核；交付固定句与机械归一化、Evidence/reducer/Runtime/schema/legacy/no-transcript 合同、至少 70 条 P0/P1 攻击矩阵、mutation-kill、RED -> GREEN 顺序、允许/禁止文件、S11 冲突热点与可复制 S12 实现 brief。
- 任务禁止修改仓库、批准/拒绝/整改或实现 S11、创建 S12 worktree、实现 milestone/resolve/reopen/reflection/purge/其它 forget/unblock、Episode/vector/关系等级/主动关心/UI/协议/跨端，以及读取 `output/`、真实 data、固件或小程序业务内容。只有 S11 独立批准并整合后才允许创建 S12 实现任务。

## S11 pause-only 最终封板记录

- 实现任务 `019f6c1e-9bf4-7471-9420-42852553a818` 使用独立 worktree `C:\Users\dell\.codex\worktrees\1af2\xiaoxin-esp32-server` 与分支 `codex/s11-c-language-pause-only`，精确基线为 `0c74d46f16433f0222d33d6d22cfac3f27af066a`。最终追加提交链为：`f4f18edabe6dbe879d883acc6953f176cd3b1cea feat: add S11 C language pause slice`、`d09007f7e2ad70cb806f2b0b7f4913e60bbe51d2 fix: preserve progress fail-closed suppression`；未 amend、未 rebase、未 push，最终 worktree clean。
- 固定句为 `我最近暂停练习 C 语言指针了。`，只允许删除 Unicode whitespace 与省略句末一个全角 `。`。精确 `free_chat` 只产生一个 trusted/durable/active `pause` Evidence：`content={"topic":"c_language_learning","label":"C 语言学习"}`、`source=deterministic_rule`、`confidence=0.9`、`retention=durable`、`expires_at=null`、`allow_prompt_injection=true`；reducer 只增加 `pause -> trying`，schema 保持 v7，无新表、trigger、status 或 stage。
- pause 不删除、supersede 或 forget 既有 concern/attempt/progress/setback；首次 pause `growth_advanced=false`、`relationship_advanced=false`，later setback 按 event time 回到 `struggling`。普通 prompt 不注入 pause，不生成 next hook、主动关心或 restart；明确成长回望只使用结构化 Evidence id/kind/stage，多个 pause 只选择最新 pause，不保存原句、history、assistant reply、transcript 或 source text。
- ASCII `.`、双 `。。`、U+200B/U+FEFF、额外子句、future restart、引用/转述/否定/假设/历史、非 `free_chat` 与缺 route 均在 legacy fallback 前 fail-closed。active S7 block、unknown/fallback、other/shared、persistence/growth disabled 下均为 0 pause、0 attempt/concern/progress/setback、0 legacy C growth；S9 forgotten endpoints 不复活，S10 setback 合同保持不变。
- 首个候选 `f4f18ed` 在主任务代码审查中被立即作废：pause suppression 重排时漏掉既有 `progress_persistence_blocked` 对 `suppress_progress` 的覆盖。主任务亲自运行既有 progress fail-closed 两组测试，复现 `8 failed, 20 passed`，失败输入为“可能是室友成果”与三个问号变体，`commit_owner` 回退 legacy。实现任务先新增 mutation-kill RED，再以追加提交 `d09007f` 恢复 `has_trusted_progress or progress_persistence_blocked or pause_persistence_blocked`，同一命令修复为 `28 passed`；旧候选不再作为任何批准依据。
- 独立验收任务 `019f6c1e-d735-7752-9e32-7e280617b3a4` 使用 `gpt-5.6-luna`、思考强度 `max`，对最终对象 `d09007f7e2ad70cb806f2b0b7f4913e60bbe51d2` 给出 `APPROVE`，P0=0、P1=0。fresh archive 独立进程确认 parser/engine/runtime 均来自候选对象；专项 `23 passed`、聚焦 `274 passed`、requirements `10 passed`，三项纠正后的黑盒 probe 3/3 PASS，Runtime A-F、并发、幂等、回滚、schema v7/WAL、no-transcript、Git blob、diff 与 merge-tree 均通过。验收过程中出现的 route 缺失、错误数据库选择和 nested `signals.growth_event` 子串误报均被证明为仓库外 probe 缺陷，不是候选失败。
- 主任务亲审 parser 优先级、pause-shaped fail-closed seam、candidate growth eligibility、legacy suppression、active block 过滤、growth prompt selector、reducer 与 progress 非回归；在候选上运行 S11 专项 `23 passed`、指定 progress mutation-kill `28 passed`、相关聚焦 `449 passed`、Xiaoxin 全量 `1579 passed`、requirements `10 passed`，compileall、diff、4 个 Git commit blob UTF-8/LF/BOM/乱码/冲突标记扫描和对 main 的 merge-tree 均通过。
- 主任务在 tracked-clean、仅保留用户未跟踪 `output/` 的 `main=b6d99f17df456d9608fa905d906c4909a0d78397` 上执行非快进合并；合并提交为 `034db0194563e3b32d81601305c98be432c0839e`（`merge: integrate S11 c language pause evidence`），无冲突，完整保留两条实现/整改提交，未 push。
- main 合并后项目级验收：`python -B -m pytest tests/xiaoxin -q -p no:cacheprovider` 为 `1579 passed in 81.43s`；requirements workbench 为 `10 passed in 1.02s`；隔离 compileall、`git diff --check b6d99f1..034db01` 与 4 个合并 blob 严格扫描通过。固件仓库保持 `2958246864953d6941f986cd962d2cfdc8202395`、小程序仓库保持 `c822f41008d9ad0f4e56c30b837a43ee022b076a`，两端工作区 clean 且零修改；用户 `output/` 未读取、未修改、未提交。S11 状态为“工程集成通过，可交用户产品验收”。

## S12 restart-only 预审完成记录

- 只读预审任务 `019f6c23-6efd-78f3-b65b-77ba76155d5d` 已完成并交付 `S12_PREAUDIT_COMPLETE`，模型 `gpt-5.6-luna`、思考强度 `max`，置信度高。唯一固定句为 `我最近重新开始练 C 语言指针了。`；“我最近又开始练……”“继续练”“恢复练”“重新看了一遍”以及 future intent 均拒绝，不扩展通用“又/重新/继续/恢复”词表。
- restart 必须有同 canonical subject、同 `c_language_learning` topic、trusted/durable/active/promptable 且 occurred_at 严格更早的 S11 pause Evidence。no-history、只有 concern/attempt/progress/setback/旧 restart、同 instant pause、晚 pause、错 subject/topic/source、expired/superseded/forgotten/permission=0 pause 均 deferred，trusted 与 legacy 双路径零副作用；setback 不能替代 pause，restart 不能自授权或降级为 attempt。
- 固定 Evidence 合同为 `kind=restart`、`content={"topic":"c_language_learning","label":"C 语言学习"}`、`source=deterministic_restart_v1`、`confidence=0.9`、`retention=durable`、`status=active`、`allow_prompt_injection=true`；reducer 只增加 `restart -> trying`，schema 保持 v7。有效首次 restart `growth_advanced=true`、`relationship_advanced=false`；普通 prompt 不注入，明确成长回望只使用结构化 pause/restart 摘要，新 session 只 rebuild，不自动生成 restart。
- prepare 后新增合法 pause 可由 commit-time 事务复核后接受；prepare 后 pause 被 forget/失效/阻断则 deferred。active S7 block 优先且不删除 pause，不得借 restart 自动 unblock、resolve、reopen、forget 或 purge。预审交付 R-01..R-100、mutation-kill、RED -> GREEN 顺序、允许/禁止文件与可直接分派 brief；只读聚焦共 `557 passed`，tracked diff 为零。

## S12 restart-only 实现与独立验收任务登记

- 实现任务 `小新记忆模块｜S12 C-language restart-only 最小纵向切片实现`：Client Thread ID `client-new-thread:b0ce1f65-06b2-4ec2-b99f-609c32eb3fe4`；实际 Thread ID `019f6c4f-1a25-7aa3-af43-0946acbeaab2`；独立 worktree `C:\Users\dell\.codex\worktrees\b73f\xiaoxin-esp32-server`；创建基线 `main=034db0194563e3b32d81601305c98be432c0839e`；目标分支 `codex/s12-c-language-restart-only`；模型 `gpt-5.6-luna`、思考强度 `max`，不得静默降级。
- 实现任务职责：严格 TDD 贯通 exact restart parser -> active-pause prepare gate -> commit-time pause/block/status/time 竞态复核 -> trusted restart Evidence -> `restart -> trying` reducer -> structured growth recall，并关闭 valid 与 deferred/no-pause restart 的 legacy attempt/growth/followup/next_hook/companion/episode/relationship 旁路。覆盖 R-01..R-100、strict earlier event time、UTC offset/微秒、incremental==rebuild、subject/alias/policy、S7-S11、幂等/冲突/并发/回滚/no-transcript；schema 保持 v7。
- 实现允许优先修改 turn analysis、memory engine/growth_state/evidence_store/contracts、必要时最小 runtime 与新 `test_memory_restart_story.py`；禁止修改 requirements/HANDOFF/计划/台账、S7-S11 既有合同、legacy 实现本身、milestone/resolve/reopen/reflection/purge/其它 forget/unblock、Episode/vector/关系等级/主动关心/UI/协议/跨端、真实 data 与 `output/`。交付追加提交链、精确 HEAD、专项/聚焦/全量/requirements/compileall/blob/merge-tree、真实 Runtime 故事与 clean worktree，不得 push。
- 独立验收任务 `小新记忆模块｜S12 restart-only 独立验收准备`：Thread ID `019f6c4f-1977-7833-916c-ea6e7ccf6bff`；当前主项目目录严格只读；启动基线 `main=034db0194563e3b32d81601305c98be432c0839e`；模型 `gpt-5.6-luna`、思考强度 `max`，不得静默降级。
- 验收任务职责是在候选前把 R-01..R-100 转为至少 100 条可执行 P0/P1 矩阵，准备 Git objects/fresh archive、临时 SQLite 与 Runtime A pause -> restart、B no-pause rejection、C setback-only rejection、D active block、E prepare 后 pause 失效、F restart -> later setback/progress 探针；候选到达后只验收总任务给出的精确 HEAD。严格禁止修改或整改；任一 P0/P1 或关键证据未知必须 `REJECT`，只有 P0=0、P1=0 才可 `APPROVE`。

## S12 restart-only 最终封板记录

- 实现任务 `019f6c4f-1a25-7aa3-af43-0946acbeaab2` 使用独立 worktree `C:\Users\dell\.codex\worktrees\b73f\xiaoxin-esp32-server` 与分支 `codex/s12-c-language-restart-only`，精确基线为 `034db0194563e3b32d81601305c98be432c0839e`。最终追加提交链为：`908211a681c9056906bccd5f3d31bb527a8e4b66 feat: add S12 C language restart gate`、`35864f488a36d39e56f801562eb9286fd1310b25 fix: defer filtered C language restart intents`、`e78cb3d5c5f29f05b37513474f8ab6e7ecbb86a2 test: cover restart runtime recovery and schema`、`505867758f0d07b180edddb542cad14c1e971195 test: isolate runtime restart route seam`、`9d9a5e5d45ba12787c13a58c3f44262b4a8058de fix: seal restart-only commit disposition`、`36e05760e86e7689252b4dee08fe7ba27d80094a fix: bind restart gate to exact analysis`；均为追加提交，未 amend、未 rebase、未 push，最终 worktree clean。
- 固定句为 `我最近重新开始练 C 语言指针了。`，只允许删除 Unicode whitespace 与省略唯一句末全角 `。`。合法 restart 必须有同 canonical subject、同 topic、trusted/durable/active/promptable、source=`deterministic_rule` 且 occurred_at 严格更早的 S11 pause。Evidence 固定为 `kind=restart`、`content={"topic":"c_language_learning","label":"C 语言学习"}`、`source=deterministic_restart_v1`、`confidence=0.9`、durable/active/promptable；reducer 只增加 `restart -> trying`，schema 保持 v7。
- 前五个候选暴露并修复了真实 P0：restart 可夹带 durable attempt 或 `block_topic` directive；普通 `hello` 可借合法 Prepared restart plan；ready/deferred correction-unit-forget 可早于 restart gate 绕过 sealed contract。最终 Store gate 在所有 correction/forget/effect 分支之前重验固定 user_text、完整 analysis signal key/value/source/confidence/persistence、唯一 candidate/effect/control flow、candidate_id=`<turn_id>:restart:c_language_learning`、subject/policy/route 与 disposition；所有 malformed prepared turn 均 deferred、无空 turn/副作用，clean retry 可成功。
- 主任务在最终候选上运行 S12 专项 `74 passed`、restart/pause/setback/zero-effects 四文件聚焦 `219 passed`、Xiaoxin 全量 `1653 passed`、requirements `10 passed`；隔离 compileall、`git diff --check`、6 个变更 blob UTF-8/LF/BOM/冲突标记扫描和对当时 main 的 merge-tree 均通过。额外仓库外伪造 probes 覆盖 extra candidate/directive、普通文本、ASCII 句点、零宽、analysis source/value/attribution/persistence、candidate_id、ready/deferred forget，全部 deferred、`turn_recorded=false`、数据库零副作用且同 turn clean retry 只写一条 restart。
- 独立验收任务 `019f6c4f-1977-7833-916c-ea6e7ccf6bff` 使用 `gpt-5.6-luna`、思考强度 `max`，对精确对象 `36e05760e86e7689252b4dee08fe7ba27d80094a` 给出 `APPROVE`、P0=0、P1=0、P2=0、P3=0。fresh archive SHA-256 为 `f9243c7f0494b1d3a6793de389ecef39be5748cd8159e25924dadd9a1ba09958`；R-01..R-100 全部 PASS，专项 `74 passed`、adversarial `29 passed`、聚焦 `794 passed`、Xiaoxin 全量 `1653 passed`、requirements `10 passed`、Runtime A-F、schema v7/WAL/事务/幂等/冲突/并发/回滚/no-transcript/new-session rebuild、Git object/blob/diff/只读 merge-tree 全部通过。
- 主任务在只保护 tracked `HANDOFF.md`、保留用户未跟踪 `.codex-review/` 与 `output/` 的 `main=7390fdca0f404840180164510e400cbd6311098c` 上执行非快进合并；合并提交为 `039b9caf4280bad809292c2fed405c64e6169484`，父提交精确为 `7390fdca0f404840180164510e400cbd6311098c` 与 `36e05760e86e7689252b4dee08fe7ba27d80094a`，无冲突，未 push。
- main 合并后项目级验收：`tests/xiaoxin` 为 `1653 passed in 86.35s`；requirements workbench 为 `10 passed in 1.01s`；隔离 compileall 通过，仅有既存 `core/providers/llm/system_prompt.py:101` 的无关 `SyntaxWarning`；`git diff --check 7390fdc..039b9ca` 通过；直接读取 `git cat-file blob` 证明 6 个合并对象均为严格 UTF-8、LF、无 BOM、无冲突标记。固件保持 `2958246864953d6941f986cd962d2cfdc8202395` 且 clean，小程序保持 `c822f41008d9ad0f4e56c30b837a43ee022b076a` 且 clean；用户 `.codex-review/`、`output/` 与真实 data 未读取、未修改、未提交。S12 状态为“工程封板并整合完成”。

## S13 milestone-only 预审完成与任务登记

- 只读预审交付位于 `C:\Users\dell\AppData\Local\Temp\xiaoxin-s13-preaudit-20260717\S13_PREAUDIT.md`，状态为 `S13_PREAUDIT_COMPLETE`。唯一固定句为 `我最近把 C 语言项目上线了。`；只允许删除 Unicode whitespace 与省略唯一句末全角 `。`。ASCII 句点、双句号、零宽、额外子句、否定/假设/引用/历史/future、other/shared/mixed、非法 route/subject/policy 全部 fail-closed。
- milestone 不要求已有 progress、pause 或 restart；no-history 也可提交。Evidence 固定为 `kind=milestone`、`content={"topic":"c_language_learning","label":"C 语言学习"}`、`source=deterministic_milestone_v1`、`confidence=0.9`、durable/active/promptable、attribution=speaker；reducer 只增加 `milestone -> milestone`，更晚 setback/progress 仍按 event time 回到 `struggling`/`progressing`；schema 保持 v7。普通 prompt 零注入，明确成长回望只使用结构化 Evidence ID/kind/stage。
- 预审交付 M-001..M-100、RED-1..RED-8、Runtime A-G、S12 同构 sealed-turn mutation-kill、允许/禁止文件、冲突热点、fresh archive/SQLite/Git/no-transcript 验收合同。active S7 block 优先；不得在 milestone 切片中夹带 attempt/mood/directive/correction/forget/block/unblock/resolve/reopen/purge/next_hook/主动关心/Episode/vector/relationship/UI/协议或跨端改动。
- 实现任务 `小新记忆模块｜S13 C-language milestone-only 最小纵向切片实现`：Client Thread ID `client-new-thread:7272d1c0-f57e-4364-bb16-a9fc883613ab`；实际 Thread ID `019f6dcb-378a-77a2-8e05-4f739123d22b`；独立 worktree `C:\Users\dell\.codex\worktrees\7d35\xiaoxin-esp32-server`；启动对象 `039b9caf4280bad809292c2fed405c64e6169484`；目标分支 `codex/s13-c-language-milestone-only`；模型 `gpt-5.6-luna`、思考强度 `max`。任务已启动，必须严格 TDD、只允许追加提交，不得 push。
- 独立验收准备任务 `小新记忆模块｜S13 milestone-only 独立验收准备`：Thread ID `019f6dcb-361c-71d0-aea2-07f9dc0936f6`；当前主项目目录严格只读；启动对象 `039b9caf4280bad809292c2fed405c64e6169484`；模型 `gpt-5.6-luna`、思考强度 `max`。候选到达前负责把 M-001..M-100 转为可执行矩阵与仓库外 runner/probes；候选到达后只验收总任务提供的 exact HEAD，任一 P0/P1 或关键证据未知必须 `REJECT`，只有 P0=0、P1=0 才可 `APPROVE`。

## S13 milestone-only 最终封板记录

- 实现任务 `019f6dcb-378a-77a2-8e05-4f739123d22b` 使用独立 worktree `C:\Users\dell\.codex\worktrees\7d35\xiaoxin-esp32-server` 与分支 `codex/s13-c-language-milestone-only`，精确基线为 `039b9caf4280bad809292c2fed405c64e6169484`。最终批准候选为 `ca476b4e2cb55cdd8b24b7127f88ef6b895f0902`；实现与整改均为追加提交，未 amend、未 rebase、未 push，最终 worktree clean。
- 固定句为 `我最近把 C 语言项目上线了。`，只允许删除 Unicode whitespace 与省略唯一句末全角 `。`。合法 no-history milestone 产生唯一 trusted/durable/active/promptable `milestone` Evidence，source=`deterministic_milestone_v1`，reducer 只增加 `milestone -> milestone`，schema 保持 v7；普通 prompt 零注入，明确成长回望只使用结构化 Evidence ID/kind/stage，更晚 setback/progress 按 event time 覆盖当前 stage。
- 候选开发过程中主任务与独立审计发现并修复多项真实 sealed Store 缺陷：analysis signal-only replay 可绕过 existing-turn seal；owner/speaker/fact attribution 未完整绑定；首次 milestone 未设置 `growth_advanced=true`；合法 whitespace/句号变体 fingerprint 不统一；near-miss predicate 误杀 generic legacy；exact text 的 signals+candidates 同时清空可写空 turn并毒化 clean retry；post-commit route 或 owner+snapshot 协同变异可错误返回 `already_committed`。旧候选 `0897db83174d85301450713f0871b8cf84031c5b` 永久作废。
- 最终追加修复 `bf3db84` 与 `ca476b4` 让 exact milestone text 始终进入 sealed gate，并把 route、subject ownership/policy 与 milestone owner snapshot 纳入 commit fingerprint；非法/缺失 route 仍可稳定 fingerprint 后由 gate deferred。总经办亲自复测：empty analysis 为 deferred、零 turn/evidence、clean retry committed；route/owner mutated replay 为 `IdempotencyConflict`，clean exact replay 为 `already_committed`。
- 主审门禁：milestone story `146 passed`、trusted ownership `45 passed`、相关聚焦 `608 passed`、Xiaoxin 全量 `1798 passed`、requirements `10 passed`；隔离 compileall、`git diff --check`、7 个允许路径、严格 UTF-8/LF/无 BOM/无冲突标记和对当时 main 的 merge-tree 均通过。
- 独立验收任务 `019f6dcb-361c-71d0-aea2-07f9dc0936f6` 对 exact HEAD `ca476b4e2cb55cdd8b24b7127f88ef6b895f0902` 给出 `APPROVE / P0=0 / P1=0`。M-001..M-100 最终为 `100 PASS / 0 FAIL / 0 UNKNOWN`；Runtime A-G、三条新增 Store recheck、sealed mutations、schema v7/WAL、事务/幂等/冲突/并发/回滚、legacy ownership、no-transcript 与 new-session rebuild 全部闭合。M-098 legacy suppression mutation 与 M-099 reducer/prompt/correction-order mutations 均被杀死。独立全量中的唯一 dispatcher TTL timing 波动经 exact archive 单项重跑为 `1 passed`，且总经办稳定全量为 `1798 passed`，确认与 memory/S13 无关。
- 主任务在 tracked-clean、仅保留用户未跟踪 `.codex-review/` 与 `output/` 的 `main=55b35748379fd60922ce61deab142dcc5b70768b` 上执行非快进合并；合并提交为 `14404df9b9e3276dd518a04a558f215f1db2893d`，父提交精确为 `55b35748379fd60922ce61deab142dcc5b70768b` 与 `ca476b4e2cb55cdd8b24b7127f88ef6b895f0902`，无冲突，未 push。
- main 合并后项目级验收：`tests/xiaoxin` 为 `1798 passed in 94.32s`；requirements workbench 为 `10 passed in 1.06s`；隔离 compileall、merge parent/candidate ancestor 与 `git diff --check` 均通过。用户 `.codex-review/`、`output/`、真实 data、固件和小程序业务内容未读取、未修改、未提交。S13 状态为“工程封板并整合完成”；下一切片为 resolve-only。

## resolve-only 只读预审任务登记

- 只读预审任务 `小新可信记忆模块｜resolve-only 下一最小切片只读预审`：Thread ID `019f6e95-0dc5-7481-84f2-2962710c5576`；当前主项目目录严格只读；启动对象 `main=e106cc9a31d85a123b5e2d691f77913ed05c2f04`；模型 `gpt-5.6-luna`、思考强度 `max`，禁止静默降级。
- 任务目标：基于决策地图、实施计划、requirements、schema v7、S7 topic block、S8/S9 correction/forget 与 S10-S13 growth events，独立选择唯一最小 resolve-only tracer bullet；必须明确 resolve 的真实 canonical target、固定句、前置状态、状态转换、event-time、subject/policy、active block、prepare 后竞态、schema 是否升级、reducer/prompt/rebuild/legacy/no-transcript 合同，以及与 reopen/unblock/forget/purge 的严格边界。
- 预审必须交付 `RESOLVE_PREAUDIT_COMPLETE`、至少 100 条 P0/P1 矩阵、Runtime A-G、sealed Prepared turn mutation、幂等/冲突/并发/回滚、mutation-kill、Git/blob/merge-tree、RED→GREEN 顺序、允许/禁止文件、冲突热点、实现任务 brief 与独立验收 brief。材料写入 `C:\Users\dell\AppData\Local\Temp\xiaoxin-resolve-preaudit-20260717`；不得修改仓库、创建 worktree、实现 resolve/reopen/reflection/purge、批准候选、读取用户受保护目录或跨端业务内容。

## resolve-only 预审完成与实现/验收任务登记

- 预审任务 `019f6e95-0dc5-7481-84f2-2962710c5576` 已交付 `RESOLVE_PREAUDIT_COMPLETE`。唯一固定句为 `我最近把 C 语言指针问题解决了。`；只允许删除 Unicode White_Space 与省略唯一句末全角 `。`。canonical target 是同 canonical subject 下 `c_language_learning` topic-level active trusted growth arc projection；matching block、no-history、forgotten/superseded/expired target、already resolved、subject/policy/route/attribution 不匹配和 prepare 后竞态均 deferred/零副作用。
- schema 决策固定为继续使用 v7：追加 active/durable `resolve` Evidence，source=`deterministic_resolve_v1`，reducer 增加 `resolve -> resolved`；target snapshot IDs/digest 只用于 Prepared/fingerprint/commit-time gate，不持久化、不参与 rebuild。resolve committed 后旧 target 的后续 forget/supersede 不撤销独立 resolve 事实；resolve Evidence 自身被 forget/supersede 后 rebuild 才排除它，无其它 active growth event 时回到既有 `unstarted`。
- 预审基线为相关服务端 `896 passed`、requirements `10 passed`；最终机器矩阵 R001-R173 连续，P0=160、P1=13，无缺字段和产品决策阻塞。交付目录为 `C:\Users\dell\AppData\Local\Temp\xiaoxin-resolve-preaudit-20260717`。
- 实现任务 `小新可信记忆模块｜resolve-only 最小纵向切片实现`：Client Thread ID `client-new-thread:fd9d09f2-f058-40d3-ac84-20d93624836b`；实际 Thread ID `019f6ed3-cfe9-7791-b02d-93a85252fc88`；独立 worktree `C:\Users\dell\.codex\worktrees\4da9\xiaoxin-esp32-server`；创建基线 `main=349c1f08a86a7590daaf6c51703bd590b3492646`；目标分支 `codex/resolve-only-growth-arc`；模型 `gpt-5.6-luna`、思考强度 `max`。职责是严格 TDD 贯通 exact parser、target snapshot、sealed commit、resolve Evidence、resolved reducer、Runtime A-G、legacy suppression、no-transcript 与 R001-R173；只允许追加提交，不得 push。
- 独立验收准备任务 `小新可信记忆模块｜resolve-only 独立验收准备`：Thread ID `019f6ed3-cf56-76e2-9360-29ec955b68d2`；当前主项目目录严格只读；启动对象 `349c1f08a86a7590daaf6c51703bd590b3492646`；模型 `gpt-5.6-luna`、思考强度 `max`。候选前准备 R001-R173 的 fresh archive/SQLite/Runtime/Git/mutation runner；候选后只验收总经办给出的 exact HEAD，任一 P0/P1 或 UNKNOWN 必须 REJECT，只有 P0=0/P1=0 才可 APPROVE。

## resolve-only 快速落地最终记录

- 用户在实现阶段明确将策略调整为“先快速落地，实机测试发现问题后再修改或补测试策略”。因此完整 R001-R173、Runtime B-G 全矩阵、系统 mutation、并发压力与罕见竞态/回滚不再作为本次合并前阻塞门禁，但被明确登记为 post-landing hardening debt；低成本的专项、全量、requirements、compileall、schema 和 Git 门禁仍全部保留。
- 实现任务 `019f6ed3-cfe9-7791-b02d-93a85252fc88` 在独立 worktree `C:\Users\dell\.codex\worktrees\4da9\xiaoxin-esp32-server`、分支 `codex/resolve-only-growth-arc` 上形成最终候选 `f0baf0a83d1990f7a015f8ed52296c9159059bb3`（`feat(memory): add resolve-only growth arc slice`），父提交精确为 `349c1f08a86a7590daaf6c51703bd590b3492646`；worktree clean，未 amend、rebase、push。
- 实现覆盖固定句机械 parser、关键 near-miss fail-closed、`resolve -> resolved` reducer、frozen `ResolvePreparedToken`、target snapshot/digest、`BEGIN IMMEDIATE` commit-time revalidation、no-history/block/forgotten/superseded/expired 零写、成功单 turn/单 active resolve Evidence、schema v7、replay/conflict、零 legacy followup、普通 prompt 零注入和真实 Runtime happy/no-history smoke。
- 总经办发现并推动修复真实 Runtime P0：生产 `handle_turn` 原先固定以 `client=None/model=None` 调路由器，固定句必得 `source=fallback`；早期 smoke 用 monkeypatch 伪造可信 route，导致“测试绿但实机永远 deferred”。最终只对冻结 exact surface 增加 `deterministic_resolve_route_v1`，Store 未放宽接受普通 fallback/unknown。
- 实现任务快速门禁：resolve `50 passed`、Xiaoxin 全量 `1848 passed`、requirements `10 passed`、schema/WAL/FK/future-schema 选择集 `32 passed`、rollback/concurrency 选择集 `3 passed`、compileall、允许路径、diff/check、parent chain 与 merge-tree 全部通过。
- 独立 QUICK 验收任务 `019f6ed3-cf56-76e2-9360-29ec955b68d2` 使用 exact candidate Git object、fresh archive 和 fresh SQLite。外置 runner 在验收过程中修复了 allowlist/registration delta、forget 文案、IdempotencyConflict 捕获、ordinary prompt 判据与 gate 完整性等 probe 缺陷；旧 QUICK_REJECT run 保留为历史证据，未把 UNKNOWN 强行改绿。
- 最终独立结论为 `QUICK_APPROVE HEAD=f0baf0a83d1990f7a015f8ed52296c9159059bb3 P0=0`，19/19 gate PASS；fresh archive SHA-256=`43113d940edb42da48332b9c25918e16249b9fd3ecc207febd0e5e608ab68127`，merge-tree=`631d52398410a4fbecc970e0f3a9e79edb5acd00`。独立计数为 resolve `50 passed`、相关聚焦 `437 passed`、Xiaoxin 全量 `1848 passed`、requirements `10 passed`、compileall PASS。
- 总经办在 tracked-clean 的 `main=1334d5eb29ac2afa7b58e1fd7a5da800a4f6d7a7` 上执行 `git merge --no-ff`，合并提交为 `5bd6c8af8829459c9f3602ffdfcc1a10dbcd1755`（`merge: land resolve-only quick slice`），父提交精确为 main 与候选，无冲突，未 push。
- main 合并后项目级复验：resolve `50 passed in 1.37s`、`tests/xiaoxin` `1848 passed in 90.70s`、requirements `10 passed in 1.25s`、compileall PASS；合并 tree=`631d52398410a4fbecc970e0f3a9e79edb5acd00`，Git 双父和 tracked-clean 门禁通过。
- 用户 `.codex-review/`、`output/`、真实 data、固件和小程序业务内容未读取、未修改、未提交。本切片状态为“快速工程集成完成，待实机观察与后补 hardening”；下一产品切片仍为 reopen-only，但应先根据实机观察决定 hardening 与 reopen 的优先级。

### resolve-only post-landing hardening debt

1. R001-R173 完整 fresh-archive 矩阵、P0/P1 和零 UNKNOWN 收口。
2. Runtime B-G、no-transcript/new-session rebuild 的完整端到端矩阵。
3. parser/target digest/block/forgotten/legacy/empty-turn/idempotency/reducer/prompt 系统 mutation-kill。
4. resolve 专项并发压力、事务故障注入、罕见 rollback/竞态。
5. old target 与 resolve Evidence 自身后续 forgotten/superseded 的完整 post-resolve 端到端验收。
