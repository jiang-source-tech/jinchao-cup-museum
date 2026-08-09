# 小芯个体人格与长期成长 V1 实施计划

日期：2026-07-25
时区：Asia/Shanghai（UTC+8）
对应规格：[小芯个体人格与长期成长 V1 设计规格](../specs/2026-07-25-xiaoxin-individuality-growth-v1-design.md)
当前状态：待实施；规格与 requirements 冻结不等于生产功能完成

## 1. 基线判断

本计划扩展现有 `core.xiaoxin.companion.CompanionMind`，不创建新的 CompanionMind，也不重新执行 2026-07-18 的模块创建计划。当前生产已有：

- `contracts.py`、`mind.py`、`store.py`、`policy.py`、`controls.py`、`worker.py`；
- `prepare_turn`、`commit_turn`、`observe`、`apply_control`、`project`、`run_due_work`；
- schema v13 的 pet、epoch、Evidence、Adjustment、Chapter、GrowthMoment 和 Initiative 表；
- `CompanionPolicy v3`，但没有 `expression_style`；
- voice、miniprogram、hardware、initiative、operator 投影；
- 真实临时 SQLite、主体隔离、并发、迁移和故事测试基础。

因此实施采用 15 个纵向切片。每一片都先完成用户可观察的可运行行为，再按可信回归风险决定是否补充最小测试，避免先创建测试矩阵或一批长期不可用的表。

## 2. 全局执行规则

每个切片必须：

1. 先完成最小可运行实现，并通过手工验证或轻量冒烟检查确认主路径；不得把 RED 测试作为开始实现的前置条件；
2. 行为稳定后，只针对最可信的回归风险补充自动化测试；整个用户任务默认总计 0 至 3 个展开后的测试用例，超过前必须取得用户明确同意；
3. 需要验证持久化语义时使用真实临时 SQLite，不用内存假 Store 代替；
4. 时间相关验证使用固定 Asia/Shanghai aware datetime；VA 衰减确需自动化验证时使用 elapsed seconds；
5. 自动化测试只断言用户可观察行为或必要的事务不变量，不逐项镜像内部状态、文档文字或完整 LLM 文案；
6. 实现必须保持 unknown speaker、owner、pet、subject、epoch 隔离和控制幂等，但不得默认把所有组合展开成测试矩阵；
7. 每次 schema 迁移先备份并做 SQLite 完整性校验；
8. 不长期双写，不反向写旧格式，不把原型导入生产；
9. 不在一个提交中混入服务端、小程序和固件的大面积改动；
10. 保留用户已有未提交修改，提交说明使用简洁中文。

本文后续各切片中的“回归风险”、测试文件建议和验证命令仅用于说明需要防范的问题，不规定测试先行，也不授权突破整个用户任务 0 至 3 个新增测试用例的上限。历史措辞与本节冲突时，以本节和仓库根目录 `AGENTS.md` 为准。

不得按切片逐条执行后文的 `pytest`、Playwright 或固件测试命令。先完成本次用户任务的生产实现，再在任务末尾从后文命令中选择一个最小相关集合集中验证一次；只有进入发布门禁或用户明确要求时才执行全量测试。

每片开始与结束至少执行：

```powershell
git status --short
git diff --check
```

确需执行的 Python 聚焦测试默认在 `main/xiaozhi-server` 运行。需求文档优先通过人工审阅、结构化解析和 `git diff --check` 验证，不为静态段落或枚举建立逐句镜像测试。

## 3. 固定命名与禁止新增

固定名称：

- 深模块：`CompanionMind`；
- 出生气质：`BirthTemperament`；
- 本轮表达：`CompanionExpressionStyle`；
- 短期状态：`CompanionVAState`；
- 历史关系：`relationship_stage`；
- 当前关系：`relationship_posture`；
- 用户明确设置：`InteractionContract` 或沿用现有明确边界合同；
- 派生长期对象：`CompanionAdjustment`、`CompanionChapter`、`CompanionGrowthMoment`。

禁止新增：

- `PersonalityEngine`、`MemoryEngineV3`、平行 SQLite；
- 自由文本 personality profile、MBTI、人格分数或 experience points；
- 新的外部 manager/service/facade；
- 由端侧、Prompt 或模型直接写入的 temperament、stage、VA 或 adjustment；
- 以具体课程、C 语言或固定校园主题作为通用 schema 枚举。

---

## Slice 0：冻结规格、需求和回归基线

### 目标

让 requirements 明确区分“已有 CompanionMind 基础”和“个体人格 V1 未实现”，并以合同测试防止后续把原型或设计误标为 done。

### 回归风险

核对 `docs/requirements/requirements.yaml`，确认：

- `companion_growth_requirements` 包含个体人格 V1 条目且状态不是 done；
- 条目引用本设计和实施计划；
- 明确五轴语义枚举、出生气质不变、相处调整一档上限、VA 独立和 7/30/90 天门禁；
- 文档不得要求创建新的 PersonalityEngine；
- priority order 与 columns 顺序一致。

### 修改文件

- `docs/requirements/requirements.yaml`
- `docs/product/domain-language.md`（只在术语缺失时补充）
- 本设计与实施计划

### 验收

- 未实现能力均为 `todo` 或 `partial`，没有原型被记作生产实现；
- requirements、规格、领域词汇的年龄、关系、气质、调整和 VA 语义一致；
- 记录本次实际执行的检查，不沿用历史 passed 数，也不为了形成基线而提前运行全量测试。

### 验证

```powershell
git diff --check
```

### 禁止扩展

- 不修改生产 Python、数据库、Prompt、关系阈值或发布开关；
- 不把 CG/CP/PROD 旧条目无证据地改成 done。

建议提交：`文档：冻结小芯个体人格与长期成长规格`

---

## Slice 1：完成首个人格表达切片与最小探针

### 目标

先完成一个可运行的人格表达主路径，再用最小探针确认目标轴差异和安全约束，避免用测试矩阵代替生产实现。

### 回归风险

实现完成后最多选择三个最可信风险建立结构化探针：

- 一个目标轴的代表性输入能够改变预期行为性质；
- 低落、负反馈或明确边界中的一个代表场景证明硬约束高于人格差异；
- 相同结构化输入重放时 policy 与 reason code 保持确定性。

其他轴、组合和场景先通过手工抽查记录，不默认展开成自动化矩阵。

### 修改文件

- `main/xiaozhi-server/tests/xiaoxin/test_companion_personality_probes.py`
- `main/xiaozhi-server/tests/xiaoxin/fixtures/companion_personality_probes.py`（确有复用需要时）
- `main/xiaozhi-server/core/xiaoxin/companion/` 中完成该切片所需的最小生产实现

### 验收

- 探针描述输入和允许差异，不保存开发者挑选的完整回答；
- 至少手工确认一个边界反例；
- 自动化测试只覆盖已落地行为，不为后续未实现切片预建失败门禁。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_personality_probes.py -q
```

### 禁止扩展

- 不先写随机文案生成器；
- 不调用真实远程 LLM；
- 不把隔离原型复制进生产测试。

建议提交：`功能：落地首个人格表达切片`

---

## Slice 2：出生气质持久化与 legacy backfill

### 目标

为每个 pet 一次生成、永久保存五轴气质，并证明重启、换设备、关系重置和迁移不会重抽。

### 回归风险

在 `test_companion_store.py`、`test_companion_store_concurrency.py` 和新 `test_companion_temperament.py` 中覆盖：

- SHA-256 v1 向量得到冻结枚举；
- 243 种组合均可通过合同校验；
- 同 pet 并发初始化只生成一行；
- 新 pet 使用 `pet_created`，旧 pet 只一次 `legacy_backfill`；
- 存储值与算法审计不一致时报警但不覆盖；
- 关系重置、purge、重启与换设备保留原气质；
- owner/pet 不匹配读取失败关闭；
- v13 旧库迁移前备份，迁移后完整性通过。

### 修改文件

- `core/xiaoxin/companion/contracts.py`
- `core/xiaoxin/companion/temperament.py`（纯生成与校验深模块）
- `core/xiaoxin/companion/store.py`
- `core/xiaoxin/companion/mind.py`
- 对应测试与迁移 fixture

### 实现

- schema v14 增加一宠物一行的出生气质表或等价受约束结构；
- 五轴使用各自语义枚举和数据库 CHECK；
- 唯一键为 `pet_id`，记录 generator version、source kind、generated_at；
- CompanionMind 在合法 pet 首次进入时幂等确保记录存在；
- backfill 失败不得用临时随机值继续对话，可回退为中档无个体化并记录健康原因。

### 验收

- 同一 pet 在进程重启和数据库重开后值完全一致；
- 不同 pet 的碰撞是合法的，不强制全局唯一人格；
- 运行时不每轮重算覆盖持久化值。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_temperament.py tests/xiaoxin/test_companion_store.py tests/xiaoxin/test_companion_store_concurrency.py -q
```

### 禁止扩展

- 不修改 Prompt；
- 不接入相处调整；
- 不增加用户重抽接口。

建议提交：`功能：持久化个人小芯出生气质`

---

## Slice 3：CompanionPolicy 最小 expression_style 投影

### 目标

让 `prepare_turn()` 返回可以承载五轴结果的结构化策略，但先只投影出生气质中档包络，不混入长期学习。

### 回归风险

更新 `test_companion_policy.py`、`test_companion_mind_contract.py` 和人格探针：

- `CompanionPolicy` 包含不可变 `expression_style`；
- style 包含 exploration、energy、organization、humor、initiative bias；
- `initiative_bias` 不改变 `initiative_level` 权限；
- `expression_energy` 不改变 `response_length`；
- 序列化、prepared token digest 与重放包含新结构；
- 旧调用者未读取新字段时保持兼容。

### 修改文件

- `core/xiaoxin/companion/contracts.py`
- `core/xiaoxin/companion/policy.py`
- `core/xiaoxin/companion/mind.py`
- `core/xiaoxin/prompts.py`（只消费结构化结果的最小必要变化）
- 对应合同、策略和 runtime 测试

### 实现

- policy version 升级为 v4；
- style 每轮确定性派生，不单独持久化；
- 玩心只编译为现有 humor 语义，主动只成为已合格 opportunity 的排序偏好；
- prepared token 必须签入规范化 style，防止 prepare/commit 间篡改。

### 验收

- 同输入输出一致；
- 五轴差异不改变能力、事实、记忆资格与安全；
- 模型收到的是有限约束，不是人格传记。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_policy.py tests/xiaoxin/test_companion_mind_contract.py tests/xiaoxin/test_companion_runtime_integration.py tests/xiaoxin/test_companion_personality_probes.py -q
```

### 禁止扩展

- 不实现年龄、VA 或关系新阈值；
- 不新增随机口头禅；
- 不持久化 expression_style。

建议提交：`功能：投影确定性本轮表达风格`

---

## Slice 4：气质显露与硬边界合成

### 目标

按关系阶段逐步允许气质被感知，并证明用户边界、场景、负反馈和端侧能力始终只会收紧结果。

### 回归风险

实现完成且本任务仍有测试额度时，从 `test_companion_policy.py` 与人格探针中选择最关键风险验证：

- first_meeting 压住探索、玩心和主动；
- familiar 放开探索/玩心但主动最多 timely；
- attuned 完整显露；long-term 不比 attuned 更强；
- 短回复、禁止主动、严肃/低落、低电量和硬件白名单逐维求交；
- 五轴全高在硬边界场景仍保持合法；
- reason code 顺序固定且不含私人文本。

### 修改文件

- `core/xiaoxin/companion/policy.py`
- 必要的 `contracts.py` 枚举
- 聚焦测试

### 验收

- 阶段只改变显露包络，不修改持久化气质；
- 不同维度能同时成立，同一维度冲突取更严格值；
- 用户边界不能被年龄、关系、气质或 VA 反向放宽。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_policy.py tests/xiaoxin/test_companion_personality_probes.py -q
```

### 禁止扩展

- 不增加显露经验值；
- 不修改关系晋级门槛；
- 不将 trace 持久化为记忆。

建议提交：`功能：合成气质显露与表达硬边界`

---

## Slice 5：可信相处调整资格与三日晋级

### 目标

让现有 Adjustment 生命周期只吸收针对具体行为、具体场景的一手反馈，并在三个不同上海日期后才影响策略。

### 回归风险

更新 `test_companion_capsule_adjustment_story.py`、`test_companion_observations.py` 和 worker 测试：

- eligible、clue-only、rejected 三类资格矩阵；
- candidate/trial 不影响 policy，active 才影响；
- 同日重复不晋级，跨三个日期依次 candidate/trial/active；
- candidate 30 天、trial 60 天过期；active 沉默不衰减；
- unknown speaker、旧 epoch、用户情绪、模型推断和泛化 helpful 不贡献日期；
- 相对出生气质最多一档，同 scope 不可多条 active。

### 修改文件

- `core/xiaoxin/companion/contracts.py`
- `core/xiaoxin/companion/worker.py`
- `core/xiaoxin/companion/store.py`
- `core/xiaoxin/companion/policy.py`
- `core/xiaoxin/companion/observation_ingress.py`（只做来源映射）
- 对应测试

### 实现

- 为 Evidence 用途增加确定性 qualification 结果，而非新事实源；
- adjustment 明确 behavior key、context scope、direction 和 qualification lineage；
- 日期以 Asia/Shanghai 去重，数据库事务负责状态转换；
- 旧宽松 adjustment 不直接升级，迁移为不进入策略的 legacy candidate 或按真实 Evidence 重算。

### 验收

- 任何 active 调整都能列出三个合格日期和 Evidence；
- 删除泛化线索不误伤独立合格调整；
- 用户明确设置不走三日流程。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_capsule_adjustment_story.py tests/xiaoxin/test_companion_observations.py tests/xiaoxin/test_companion_worker.py tests/xiaoxin/test_companion_policy.py -q
```

### 禁止扩展

- 不用模型 confidence 替代资格；
- 不把每次聊天变成人格证据；
- 不让 trial 进入 Prompt。

建议提交：`功能：收紧相处调整证据与三日晋级`

---

## Slice 6：反向学习、撤销与 Evidence 重算

### 目标

补齐相反反馈、显式纠正、Evidence 遗忘和终态不可复活，避免人格来回跳或删除后复活。

### 回归风险

覆盖：

- 单次相反信号不翻转；旧方向重新确认会终止弱 challenge；
- 相反证据达到门槛后先撤旧回基线，新方向独立三日晋级；
- 用户明确纠正立即 revoked；
- superseded/expired/revoked 不能原地复活；
- 删除 Evidence 后按剩余日期新建 active/trial/candidate/无记录；
- 恢复默认、明确撤销和 reset 之后旧 Evidence 不参与重算；
- 并发删除与晋级不产生两个 active。

### 修改文件

- `core/xiaoxin/companion/store.py`
- `core/xiaoxin/companion/worker.py`
- `core/xiaoxin/companion/controls.py`
- `core/xiaoxin/companion/mind.py`
- 调整、控制、并发和故事测试

### 验收

- 终态保留安全审计但永不进入策略；
- forget/correct/reset/purge 对派生对象的失效语义一致；
- 其他无关调整、用户事实和契约不被误伤。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_capsule_adjustment_story.py tests/xiaoxin/test_companion_control_api.py tests/xiaoxin/test_companion_relationship_controls.py tests/xiaoxin/test_companion_store_concurrency.py -q
```

### 禁止扩展

- 不原地降级 active；
- 不删除终态审计记录；
- 不把普通单轮纠正自动升级为永久契约。

建议提交：`功能：完成相处调整反向学习与重算`

---

## Slice 7：长期关系节奏与当前关系姿态

### 目标

把关系阶段从短期累计量升级为最短 14/90/365 天的长期 Evidence 节奏，同时增加不改写历史阶段的重逢与修复姿态。

### 回归风险

把隔离原型的八条时间线改写为 CompanionMind + 真实 SQLite 故事：

- 新生高频、月两次、月一次、三日刷量、久别、负反馈、跨设备幂等、遗忘知识；
- stage 在 epoch 内单调，只有 reset 回到初见；
- 等待、年龄和原始消息不晋级；
- 30/60/120 天进入 reunion_cautious，三返回日增益 0.5/0.75/1.0；
- repairing 关闭记忆/主动/调整并暂停晋级，正向反馈或三健康日恢复；
- posture 跨重启、并发和乱序事件稳定。

### 修改文件

- `core/xiaoxin/companion/contracts.py`
- `core/xiaoxin/companion/policy.py`
- `core/xiaoxin/companion/store.py`
- `core/xiaoxin/companion/mind.py`
- 关系质量、策略、投影、并发和故事测试

### 实现

- 新 relationship policy version；
- 保存历史最高 stage 与必要的 posture 恢复状态，不从当前可删除计数反推降级；
- 帮助与磨合按上海日期去重并要求来源资格；
- 旧生产阈值迁移为历史配置，不长期双跑两套 stage；
- operator 提供非私人缺口原因和配置版本。

### 验收

- 高频用户最早一年达到 long-term；
- 低频但长期稳定用户不会被永久排除；
- 久别与修复改变当前表达，不抹掉真实历史。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_relationship_quality.py tests/xiaoxin/test_companion_policy.py tests/xiaoxin/test_companion_relationship_controls.py tests/xiaoxin/test_companion_projections.py -q
```

### 禁止扩展

- 不把 14/90/365 只做成等待计时器；
- 不因负反馈扣亲密度；
- 不让 VA 参与晋级。

建议提交：`功能：实现长期关系节奏与关系姿态`

---

## Slice 8：大一到大四结构化成熟表达

### 目标

用冻结六维矩阵替代粗略自由文本 `age_expression` 和随年龄单调增加的数量型行为。

### 回归风险

实现完成且本任务仍有测试额度时，从 `test_companion_academic_stage.py` 和 policy 风险中选择最关键项验证：

- 1/2/3/4 岁映射到完整六维语义；unknown 为中性；
- 四年知识、工具、事实、安全和用户控制完全相同；
- 年龄不直接提高 response length、question budget、memory budget、initiative frequency 和硬件强度；
- 合法记忆相同，只改变组织和使用方式；
- 气质、年龄、关系和 VA 输入保持独立；
- 大二首次使用仍 first_meeting 且零共同经历。

### 修改文件

- `core/xiaoxin/companion/contracts.py`
- `core/xiaoxin/companion/policy.py`
- `core/xiaoxin/companion/mind.py`
- Prompt 消费点与对应测试

### 验收

- `age_expression` 成为结构化派生结果或被兼容适配，不再由自由文本承载核心规则；
- 未知年级不猜大一，不关闭气质和用户契约；
- 端侧只接收设备无关节奏语义。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_academic_stage.py tests/xiaoxin/test_companion_policy.py tests/xiaoxin/test_companion_mind_story.py -q
```

### 禁止扩展

- 不改学生资料事实源；
- 不以年龄解锁能力；
- 不在本片实现周年或毕业。

建议提交：`功能：实现四学年成熟表达矩阵`

---

## Slice 9：非标准学业路径

### 目标

让中途加入、跳级、留级、休学、延毕、回退、纠正、毕业后和账号迁移具有确定、可追溯且不虚构历史的语义。

### 回归风险

把转换矩阵逐项转成参数化测试，覆盖 source revision 幂等和乱序：

- stage/status/transition/effective/source revision 分离；
- 跳级不补中间阶段，留级/休学/延毕不增龄，毕业后无 5 岁；
- 暂时缺值与旧 revision 不让年龄跳动；明确清除才中性；
- 真实回退中性表达，资料纠正不产生成长时刻；
- 转专业不改年龄；
- pet、气质、epoch 默认连续；
- 账号合并必须选择一只 pet，冲突或无法验证时拒绝。

### 修改文件

- `core/xiaoxin/companion/academic.py`（转换判定足够复杂时建立）
- `core/xiaoxin/companion/contracts.py`
- `core/xiaoxin/companion/store.py`
- `core/xiaoxin/companion/mind.py`
- observation ingress 与学业测试

### 验收

- 所有派生结果能追溯 source revision；
- 纠正与真实成长可区分；
- 迁移不合并两只小芯的私人状态。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_academic_stage.py tests/xiaoxin/test_companion_observations.py tests/xiaoxin/test_companion_subject_context.py -q
```

### 禁止扩展

- 不从聊天或预计毕业时间推断年级；
- 不自动选择账号合并中的宠物；
- 不补写不存在的共同经历。

建议提交：`功能：覆盖小芯非标准学业路径`

---

## Slice 10：章节、周年、毕业与成长时刻

### 目标

完成四年叙事边界、合格章节和一次性受限表达，替代只覆盖 freshman→sophomore 的实现。

### 回归风险

把 14 条叙事原型时间线迁为真实 SQLite 故事，至少覆盖：

- sophomore→junior、junior→senior、跳级、毕业、周年、周年合并、纠正和遗忘；
- 章节 2 条 Evidence、1 条 shared experience、2 个日期、最多 3 条；
- 学业/毕业 boundary-only 与周年无章节不仪式；
- 30/14/90 天窗口，过期不补播；
- cautious/repairing 不认领；
- claim 租约并发唯一、失败释放、成功不重复、suppressed 不补播；
- 章节归属实际离开的旧阶段；
- 遗忘期间释放旧 turn 并按剩余依据重建/降级/失效。

### 修改文件

- `core/xiaoxin/companion/contracts.py`
- `core/xiaoxin/companion/store.py`
- `core/xiaoxin/companion/worker.py`
- `core/xiaoxin/companion/mind.py`
- chapter、growth moment、projection 和并发测试

### 验收

- 成长时刻不主动发送、不阻塞任务；
- 语音、小程序、硬件共享 moment id 和边界事实；
- 资料纠正不伪装成长，周年与学业合并不吞掉真实边界。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_chapter_story.py tests/xiaoxin/test_companion_academic_stage.py tests/xiaoxin/test_companion_projections.py tests/xiaoxin/test_companion_store_concurrency.py -q
```

### 禁止扩展

- 不建立年度报告事实源；
- 不主动推送仪式；
- 不保存虚构总结。

建议提交：`功能：完成四年陪伴章节与成长时刻`

---

## Slice 11：VA 状态、幂等事件与跨重启恢复

### 目标

把隔离 VA 合同接入 CompanionMind，使分钟到小时状态可恢复但不会变成人格、关系或主动权限。

### 回归风险

实现完成且本任务仍有测试额度时，最多从 VA 与故事风险中选择最关键项验证；仅确有新增测试需要时创建 `test_companion_va.py`：

- 基线 +150/0、90/35 分钟半衰期和 6 小时精确过期；
- 五类事件、目标靠近、定点确定性和范围 clamp；
- 用户低落/负反馈保持非负 V、低 A 并触发支持/收住；
- 重复事件、乱序、unknown event、unknown speaker 拒绝；
- 重启后按 elapsed time 衰减，不因读取续期；
- future/corrupt/wrong epoch 快照回基线；
- reset/purge 回基线但旧 event id 重放仍无效；
- 低电量只覆盖硬件，不写回 VA；
- VA 不创建 initiative、不放宽预算、不参与 stage。

### 修改文件

- `core/xiaoxin/companion/va.py`
- `core/xiaoxin/companion/contracts.py`
- `core/xiaoxin/companion/store.py`
- `core/xiaoxin/companion/mind.py`
- `core/xiaoxin/companion/policy.py`
- observation ingress、controls、projection 和测试

### 实现

- schema 新增快照与跨 TTL 保留的事件幂等凭据；
- 状态计算使用整数/明确舍入，禁止 float 平台漂移；
- 年龄和关系动力学参数版本化；
- Prompt 与学生端不接收原始坐标。

### 验收

- 原型十条时间线在生产接口上全部通过；
- 相同事件序列跨重启得到相同快照；
- VA 失败关闭不影响正常问答。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_va.py tests/xiaoxin/test_companion_observations.py tests/xiaoxin/test_companion_policy.py tests/xiaoxin/test_companion_store_concurrency.py -q
```

### 禁止扩展

- 不保存用户情绪标签；
- 不让模型写坐标或参数；
- 不用设备状态修改服务端 VA。

建议提交：`功能：接入可恢复的短期 VA 状态`

---

## Slice 12：小程序、语音与硬件用户控制

### 目标

把验证过的信息架构接到真实投影与控制 API，先完成服务端合同，再分别提交小程序和固件消费者。

### 回归风险

服务端生产实现完成后，按剩余测试额度选择最关键风险验证：

- 相处方式摘要不暴露五轴、seed、置信度、候选状态和 VA；
- 只纠正本轮、撤销单项、长期契约、恢复默认、重新磨合、清空个人记忆语义分离；
- 高风险控制 owner-only、幂等且需要规定确认；
- voice 只能发起高风险操作，不能直接执行；
- hardware 白名单且不执行控制；
- reset/purge 保留矩阵完整。

小程序和固件生产实现完成后，先做真实页面与设备主路径手工验收；Playwright 和固件自动化只在剩余测试额度内选择最关键风险，不默认展开五视口或状态矩阵。

### 修改文件

- 服务端 companion contracts/mind/controls/projections 与 API 测试
- 历史计划中的 `main/manager-mobile` 页面、API client 和 Playwright 测试（该移动端已废弃；当前微信小程序位于仓库外的独立目录）
- 固件 Overview/表达适配及主机侧测试

### 提交拆分

1. 服务端投影与控制；
2. 小程序页面与交互；
3. 固件受限表达。

### 验收

- 用户不进管理页也能正常使用；
- 用户能在上下文立即收住，并在二级页管理长期设置；
- 小程序和硬件不复制事实源；
- 页面无重叠、控制台错误或隐私工程字段。

### 验证

```powershell
python -m pytest tests/xiaoxin/test_companion_control_api.py tests/xiaoxin/test_companion_projections.py tests/xiaoxin/test_companion_relationship_controls.py -q
```

小程序和固件使用各自仓库现有 lint/test/build 命令；实际命令在实施前从 package scripts 和固件构建配置读取，禁止在计划中臆造。

### 禁止扩展

- 不做人格仪表盘；
- 不开放 V1 重新养宠入口；
- 不把 prototype 静态数据接到生产页面。

建议提交：`功能：接入个体陪伴控制与多端投影`

---

## Slice 13：完整矩阵、研究工具与真实 ESP32 HIL

### 目标

把自动化正确性、匹配反事实、研究数据和真实设备证据变成可重复门禁，而不是人工挑样例。

### 回归风险/交付物

- 243 气质组合与成对交互矩阵；
- 七类探针、硬边界、控制和状态重放器；
- 匹配反事实生成器，确保事实、模型、能力和长度条件一致；
- 记忆真值集与引用资格验证器；
- 冻结策略快照、12 题 2AFC、随机化和研究数据字典；
- HIL 驱动与证据清单：服务端 Git SHA、固件版本、设备、subject、policy hash、event id、串口和服务日志；
- PASS/FAIL/INCONCLUSIVE 报告器。

### 修改文件

- `main/xiaozhi-server/tests/xiaoxin/` 的门禁测试
- 独立 `tools/` 或 `scripts/` 研究/HIL 工具，不能进入实时 runtime
- `docs/verification/` 或项目已有验收台账位置的证据模板

### HIL 验收

- 候选服务端部署在服务器，开发电脑连接目标 ESP32；
- 冷启动、对话、成功、低落、负反馈、回落；
- Wi-Fi/WebSocket/服务端/设备重启与 outbox 重放；
- 低电量覆盖、语音/TTS/字幕/表情一致；
- reset、purge、关闭主动和关闭回顾；
- 两主体/两设备隔离与 OTA 连续性；
- 每条关键路径连续 30 次零身份串用、零重复表达、零旧状态复活；
- 一次 24 小时长稳；
- 使用届时已经冻结的真实设备 SLO，若仍没有 SLO 则发布证据不完整。

### 验证

```powershell
python -m pytest tests/xiaoxin -q
```

HIL 命令必须在真实服务器、串口和开发板信息可用时由工具输出并归档，不在无设备环境伪造 PASS。

### 禁止扩展

- 不使用生产用户私人数据做隔离 HIL；
- 不把 30 次零失败宣传成生产可靠率；
- 不以截图替代结构化日志和版本证据。

建议提交：`测试：建立个体成长矩阵与真机门禁`

---

## Slice 14：受控发布、纵向验证与旧行为清理

### 目标

按顺序开启功能、收集 D7/D30/D90 证据，并只在回滚窗口结束后删除旧行为。

### 发布顺序

```text
schema/backfill shadow
 -> expression_style 诊断
 -> temperament limited cohort
 -> adjustment candidate-only
 -> adjustment active limited cohort
 -> relationship v2 shadow/compare
 -> relationship v2 active
 -> narrative and VA limited cohort
 -> CP-06 controls
 -> HIL pass
 -> D7 pilot
 -> D30 controlled study
 -> D90 confirmation
```

### 停止条件

任一以下事件立即停止扩大并回到最近安全模式：

- 主体、pet 或 epoch 串用；
- 虚假事实/共同经历、遗忘后复活；
- 边界、主动关闭、静默或负反馈被绕过；
- 重复成长时刻、重复主动消息或 VA 事件二次推进；
- schema 完整性失败、backfill 不确定或迁移无法恢复；
- HIL 关键路径失败；
- 真实研究未达到预注册门槛或结果 INCONCLUSIVE。

### 研究验收

- 12 人 D7 校准只用于修订题目和样本估计；
- 确认性队列至少招募 72 人并获得至少 60 名 D90 有效完成者；
- D7/D30/D90 达到规格中的辨识率、CI、style-only、成长与记忆门槛；
- 正常磨合与延迟磨合按 1:1 比较，明确边界和安全在两组都立即生效；
- 版本、Prompt hash、策略、气质生成、VA 配置和排除规则预注册；
- 只输出 PASS/FAIL/INCONCLUSIVE，不用留存和喜欢度替代正确性。

### 旧行为清理

只有当新策略全量稳定、数据库备份可恢复、HIL 和发布观察通过后，才删除：

- 旧 policy version 的运行分支；
- 旧年龄自由文本和数量型投影；
- 不再使用的宽松 adjustment 晋级路径；
- 临时 shadow 比较字段和过期 feature flags。

出生气质持久化记录和迁移审计不得因回滚或清理删除。

### 最终验证

```powershell
python -m pytest tests/xiaoxin -q
python -m compileall core/xiaoxin/companion
git diff --check
git status --short
```

### 禁止扩展

- 不在研究未完成前宣称“越养越懂你”；
- 不为了指标放宽 P0 门禁；
- 不在同一提交中同时清理旧路径和修改新算法。

建议提交：`发布：完成小芯个体成长受控切换`

## 4. 切片依赖与可交付结果

```text
S0 规格/需求
 -> S1 探针
 -> S2 出生气质
 -> S3 expression_style
 -> S4 显露/硬边界
 -> S5 资格/晋级
 -> S6 反向/重算
 -> S7 长期关系
 -> S8 四年成熟
 -> S9 非标准路径
 -> S10 叙事时刻
 -> S11 VA
 -> S12 多端控制
 -> S13 矩阵/HIL
 -> S14 发布/研究/清理
```

S2-S4 完成后可以交付“稳定个体差异”的受控内部版本；S5-S6 完成后才有资格称为“会磨合”；S7-S11 完成后才具备完整长期成长机制；S12-S14 和纵向门槛通过后，才有资格面向用户承诺完整 V1。

## 5. requirements 更新纪律

每片合并后只更新真实完成部分：

- `implemented` 写代码位置、schema version、测试与部署事实；
- `remaining` 删除已经完成项并保留真机、真实数据和研究缺口；
- `acceptance` 不因实现存在而删除；
- `status=done` 必须同时满足代码、迁移、多端、真机和该条定义的发布证据；
- 原型通过数只能写在决策资产或历史依据中，不得计入生产 implemented。
