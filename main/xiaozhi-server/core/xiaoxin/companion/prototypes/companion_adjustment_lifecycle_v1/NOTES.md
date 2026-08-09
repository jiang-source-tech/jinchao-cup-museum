# Prototype Findings

状态：自动审计完成；决策地图 #7 的产品规则已全部确认。

2026-07-25 执行非交互审计，15 条合成时间线全部通过，并对每条时间线执行两次规范化重放：`15 passed, 0 failed`。未发现不确定性分支。

## 现有实现核对

生产代码已有 `CompanionEvidence -> SessionCapsule -> CompanionAdjustment` 派生链，也已有一日 `candidate`、两日 `trial`、三日 `active`、候选 30 天和试行 60 天的基本门槛。

原型前检查发现四个需要在正式实现时收紧的地方：

1. `ReflectionEvidence` 没有携带 attribution、claim context、说话人确定性、时效和行为关联信息，提案校验因而无法严格判断 Evidence 是否有资格形成调整。
2. 当前 Session 过滤只要求本轮存在一个 meaningful kind；模型随后可以引用同一请求中的其他 active Evidence，Evidence kind 与调整维度之间没有结构化准入关系。
3. 当前一次相反候选会把旧 `active` 立即降为 `trial`，并让旧调整马上退出策略。这违反已确认的“单次反证不能让人格翻向”。
4. 当前遗忘或过期会撤销任何引用该 Evidence 的完整派生调整，不会按剩余独立 Evidence 重算，删除一个弱来源也可能误伤稳定调整。

## 原型中的推荐规则

### Evidence 资格

| 通道 | 典型来源 | 对生命周期的作用 |
|---|---|---|
| `qualifying` | 已确认本人对某个具体小芯行为的一手反馈 | 每个上海本地日期最多贡献一次确认，可推动晋级 |
| `candidate_only` | 泛化 helpful、response reaction、已完成 follow-up 等间接结果 | 只能建立或附着候选，不贡献确认日期，不刷新晋级窗口 |
| `rejected` | 未知说话人、用户事实、转述、假设、玩笑、引用、ASR 不确定、短期情绪、模型推断、旧 relationship epoch | 不建立、不强化、不挑战任何调整 |

模型 confidence 仅作审计元数据；它不能把弱 Evidence 变强，也不能减少跨日门槛。

### 正向晋级

- 第一个合格日期：`candidate`，不进入策略；30 天内等待强化。
- 第二个不同合格日期：`trial`，仍不进入策略；60 天内等待再次验证。
- 第三个不同合格日期：`active`，才进入 `CompanionPolicy`。
- 同一上海本地日期无论多少条消息只算一次；重复 Evidence ID 幂等忽略。
- `active` 不因沉默自动过期；candidate 和 trial 超过窗口分别进入 `expired`。

### 反向学习

- 旧方向已经 `active` 时，第一个相反合格日期只建立 challenger candidate，旧方向继续生效。
- 第二个相反合格日期使旧方向进入 `superseded`，新方向为 `trial`，策略先回出生气质基线。
- 第三个相反合格日期才使新方向 `active`。
- 如果旧方向在挑战者仍是 candidate 时得到新的合格确认，弱挑战者进入 `superseded`，避免交替噪声在后台偷偷累积。

### 立即停止与失效

- 用户明确纠正：相关隐式调整立即 `revoked`，不从同一句纠正中制造相反偏好。
- 用户明确长期设置或边界：走互动契约通道并立即约束；不创建 Adjustment。
- 决定晋级的 Evidence 被遗忘或过期：旧派生记录进入 `revoked`，剔除失效来源后用剩余 Evidence 创建新记录；至少三天保持 active、两天回 trial、一天回 candidate、零天不重建。
- 仅候选弱来源被删除：若调整已有独立合格 Evidence 支撑，不撤销或重算成熟调整。
- candidate 或 trial 未在窗口内强化：进入 `expired`。
- 新方向达到足够反证门槛：旧方向进入 `superseded`。
- 关系重置：旧 epoch 全部隐式调整进入 `revoked`；用户互动契约保留。

`superseded`、`expired` 和 `revoked` 都是不可变终态。Evidence 失效后的重算必须创建新 adjustment 记录；明确纠正、撤销调整、恢复默认表达和关系重置之后，边界以前的 Evidence 不得参与重算。

## 原型覆盖

- 跨日晋级；
- 同日刷量与重复投递；
- 模型置信度不计票；
- 只可候选的间接结果；
- 单次反证；
- 连续跨日反证与基线过渡；
- 旧方向重新得到确认；
- 用户明确纠正；
- 三条 Evidence 删除一条后重算为 trial；
- 五条 Evidence 删除一条后重算并保持 active；
- 仅候选弱来源删除不误伤 active；
- Evidence 自然过期后的确定性重算；
- candidate/trial 超时；
- 关系重置与互动契约保留；
- 未知说话人、转述、假设、玩笑、ASR 不确定、短期情绪、旧 epoch 和模型推断。

## 后续实施

产品语义已经写回决策地图和领域词汇表。正式实现仍需等待 #15 形成独立计划，不能直接复制此原型，也不能把原型审计通过视为生产功能已经完成。
