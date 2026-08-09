# Relationship Timeline V1 Throwaway Prototype

运行全部合成时间线审计：

```powershell
python -B main/xiaozhi-server/core/xiaoxin/companion/prototypes/relationship_timeline_v1/cli.py --audit
```

运行交互式终端原型：

```powershell
python -B main/xiaozhi-server/core/xiaoxin/companion/prototypes/relationship_timeline_v1/cli.py
```

也可以直接查看指定场景：

```powershell
python -B main/xiaozhi-server/core/xiaoxin/companion/prototypes/relationship_timeline_v1/cli.py --scenario long_absence_reunion
```

## 原型问题

本原型只回答决策地图 #10：关系阶段怎样形成真实的长期节奏，同时让高频、低频、久别、负反馈和跨设备使用都得到合理结果。

它是纯内存、一次性的业务逻辑原型：

- 不连接数据库；
- 不读取真实用户聊天或记忆；
- 不修改生产 `CompanionPolicyConfig` 或关系阈值；
- 不把消息数、相处天数或负反馈做成亲密度分数；
- 所有时间线均使用合成事件。

## 候选模型

关系阶段在同一 relationship epoch 内单调晋级：`first_meeting -> familiar -> attuned -> long_term_companion`。沉默、负反馈和遗忘不会惩罚式降级；关系重置仍是重新进入初见的独立控制操作。

关系阶段是历史，当前关系姿态是此刻的表达约束。`reunion_cautious` 不阻止已经满足全部 Evidence 门槛的阶段晋级，也不会因晋级而自动清除；`repairing` 会把晋级推迟到修复完成，避免负反馈当天反而进入更深阶段。

候选最短跨度：

| 阶段 | 最短跨度 | 总体活跃分布 | 总体质量底线 | 近期健康窗口 |
|---|---:|---|---|---|
| `familiar` | 14 天 | 4 天 / 2 周 / 1 月 | 2 个可靠知识、1 个帮助日期、1 个磨合日期 | 近 60 天有 2 个活跃日、1 个帮助日、1 个磨合日 |
| `attuned` | 90 天 | 12 天 / 8 周 / 3 月 | 5 个可靠知识、4 个帮助日期、3 个磨合日期 | 近 180 天有 4 个活跃日、2 个帮助日、1 个磨合日 |
| `long_term_companion` | 365 天 | 36 天 / 24 周 / 9 月 | 10 个可靠知识、8 个帮助日期、6 个磨合日期 | 近 365 天有 8 个活跃日、2 个帮助日、2 个磨合日 |

原始消息数只保留诊断用途，不参与候选门槛。帮助和磨合按上海本地不同日期计数，避免同日刷量。

久别不会删除历史阶段或隐式调整。熟悉、默契和长期阶段分别在 30、60、120 天无互动后进入重逢姿态；前三个不同返回日期逐步恢复隐式调整表达。负反馈进入修复姿态，立即收紧记忆、主动和个体化表达，但不扣关系阶段。

## 覆盖场景

- 新生高频使用；
- 低频稳定使用；
- 每月一次但持续三年的稳定使用；
- 三日大量刷量并等待一年；
- 长期离开后的三日重逢恢复；
- 连续负反馈与修复；
- 同一宠物跨设备持续使用与幂等去重；
- 遗忘知识后保留历史关系、收紧当前记忆权限。

终端会同时显示生产当前门槛推导出的 `legacy_stage` 和候选模型的 `stage`，并在每个检查点展示跨度、活跃天/周/月、知识、帮助日期、磨合日期、临时姿态和当前策略收紧结果。

2026-07-25 冻结审计结果为 `8 passed, 0 failed`。完整结论、生产差距和保留校准项见 [`NOTES.md`](NOTES.md)。
