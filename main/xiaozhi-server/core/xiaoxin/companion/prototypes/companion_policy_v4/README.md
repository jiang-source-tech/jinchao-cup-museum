# CompanionPolicy V4 Throwaway Prototype

运行交互原型：

```powershell
python main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_policy_v4/cli.py
```

运行全部冲突场景审计：

```powershell
python main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_policy_v4/cli.py --audit
```

## 原型问题

本原型只回答一个问题：核心身份、出生气质、年龄、关系阶段、后天调整、当前场景、负反馈、用户互动契约和端侧限制发生冲突时，能否通过一个确定性的 `CompanionPolicy` 合成接口得到稳定、可解释且不越界的结果？

这是一次性业务逻辑原型，不连接真实数据库，不读取真实用户记忆，不进入服务器运行时。确认结论后应删除终端壳，并把有效规则吸收到正式规格或后续实现中。

## 操作

- `n`：下一个冲突场景
- `p`：上一个冲突场景
- `r`：用相同输入重新合成并比较摘要
- `a`：审计全部场景
- `q`：退出

每次操作都会重绘输入、完整策略状态、实际改变结果的决策步骤和场景判定。
