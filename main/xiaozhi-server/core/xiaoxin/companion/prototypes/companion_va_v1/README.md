# Companion VA V1 Throwaway Prototype

运行全部合成时间线审计：

```powershell
python -B main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_va_v1/cli.py --audit
```

运行交互式终端原型：

```powershell
python -B main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_va_v1/cli.py
```

也可以直接查看指定场景：

```powershell
python -B main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_va_v1/cli.py --scenario user_distress_does_not_mirror
```

## 原型问题

本原型只回答决策地图 #13：怎样让分钟到小时尺度的 VA 状态参与小芯表达，并让年龄与关系改变反应曲线，而不把情绪累计成人格、亲密度或成长等级。

它是纯内存、一次性的业务逻辑原型：

- 不连接数据库；
- 不读取真实聊天、用户情绪或个人记忆；
- 不修改生产 `CompanionPolicy`、Prompt、关系阈值或硬件协议；
- 不允许模型直接写入 V/A 数值；
- 所有场景均使用合成事件和显式时间戳。

当前候选采用 `[-1, 1]` 内部定点范围、`(+0.15, 0)` 温暖中性基线、事件目标靠近、双半衰期回归和 6 小时快照 TTL。

2026-07-25 冻结审计结果为 `10 passed, 0 failed`。完整参数、投影合同、审计结果和生产差距见 [`NOTES.md`](NOTES.md)。
