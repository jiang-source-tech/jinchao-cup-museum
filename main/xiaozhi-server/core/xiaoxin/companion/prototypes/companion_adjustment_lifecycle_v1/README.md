# Companion Adjustment Lifecycle V1 Throwaway Prototype

运行全部合成时间线审计：

```powershell
python main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_adjustment_lifecycle_v1/cli.py --audit
```

当前审计结果：`15 passed, 0 failed`，每条时间线均包含一次相同输入的确定性重放。

运行交互式终端原型：

```powershell
python main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_adjustment_lifecycle_v1/cli.py
```

也可以直接打开某个场景：

```powershell
python main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_adjustment_lifecycle_v1/cli.py --scenario sustained_counterevidence
```

## 原型问题

本原型只回答决策地图 #7：什么 Evidence 有资格形成隐式相处调整，以及调整怎样在 `candidate`、`trial`、`active`、`superseded`、`expired` 和 `revoked` 之间确定性转换。

它是纯内存、一次性的业务逻辑原型：

- 不连接数据库；
- 不读取真实用户聊天或记忆；
- 不修改生产 `CompanionMind`、Reflection prompt 或关系阈值；
- 不把模型置信度当成晋级票数；
- 所有时间线均使用合成 Evidence。

## 操作

- `n`：下一个场景；
- `p`：上一个场景；
- `r`：用相同输入重放并检查确定性；
- `a`：审计全部场景；
- `j`：显示当前场景的完整 JSON 状态；
- `q`：退出。

终端会显示每个时间点的有效调整、候选状态、有效日期数、证据准入结果和固定原因码。

## 三条证据通道

1. `qualifying`：本人在当前关系中，对小芯某个具体行为给出可核对的一手反馈。它可以贡献一个跨日确认日期。
2. `candidate_only`：泛化点赞、完成后续事项等间接结果。它最多提出一个待观察猜想，永远不能靠自身重复晋级。
3. `rejected`：未知说话人、转述、假设、玩笑、ASR 不确定、短期情绪、用户资料、模型推断或与具体行为无关的内容。它不进入调整状态机。

明确的长期设置、边界和明确纠正走独立控制通道，不伪装成隐式 Evidence。
