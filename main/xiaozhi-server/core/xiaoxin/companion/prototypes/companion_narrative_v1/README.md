# Companion Narrative V1 Throwaway Prototype

运行全部合成时间线审计：

```powershell
python -B main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_narrative_v1/cli.py --audit
```

运行交互式终端原型：

```powershell
python -B main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_narrative_v1/cli.py
```

也可以从指定场景开始：

```powershell
python -B main/xiaozhi-server/core/xiaoxin/companion/prototypes/companion_narrative_v1/cli.py --scenario four_year_continuity
```

## 原型问题

本原型只回答决策地图 #11：怎样让真实学业边界、陪伴周年、毕业、久别重逢和 Evidence-backed CompanionChapter 形成一条连续但克制的四年叙事。

它是纯内存、一次性的业务逻辑原型：

- 不连接数据库；
- 不读取真实用户聊天、学生资料或记忆；
- 不调用远程模型或生成自然语言正文；
- 不修改生产章节、成长时刻、Prompt 或跨端投影；
- 所有时间线均使用合成的结构化边界与 Evidence。

## 候选模型

学业变化、周年和毕业先形成不可由模型创造的结构化叙事边界。CompanionChapter 只是对当前关系时期内少量有效 Evidence 的派生读模型；章节不足时不生成评价，边界事实仍可独立存在。

每个阶段边界关闭的是实际离开的阶段，不把旧阶段经历标到新阶段。跳级只关闭真实经历过的来源阶段并进入目标阶段；资料纠正不生成仪式；毕业冻结最后确认年龄，不产生 5 岁。

一次性成长时刻从边界和可用章节投影而来。语音最多一到两句，小程序最多显示三条安全 Evidence 摘要，硬件只接收低强度、短时长语义。仪式不能主动发起消息，只能附着在下一次合适的用户互动中。

## 覆盖场景

- 四个真实学业阶段与毕业的完整时间线；
- 只有年龄变化、没有共同经历；
- Evidence 不足；
- 同日 Evidence 不能伪装成纵向章节；
- 用户关闭成长回顾并重新开启；
- 并发单次认领、失败释放和成功不重复；
- 设备控制任务不能消费成长时刻；
- 表达前、认领期间与表达后遗忘 Evidence；
- 遗忘后章节失效、降级或建立不可变新版本；
- 跳级、留级、真实回退、资料纠正、休学、复学和迁移；
- 资料纠正只移除错误学业边界并保留独立周年；
- 久别期间的过期周年与当前成长边界；
- 无共同 Evidence 的毕业；
- 只有相处时间、没有共同 Evidence 的周年。

当前 14 条合成时间线已通过确定性重放审计，结果为 `14 passed, 0 failed`。冻结结论和生产差距见 [`NOTES.md`](NOTES.md)。
