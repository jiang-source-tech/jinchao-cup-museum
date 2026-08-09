# 小芯双学生真机长期记忆与个性化陪伴验证

## 验证结论

- 验证时间：2026-07-29 19:23-20:36（Asia/Shanghai）。
- 真实设备：两块 ESP32-S3，固件均为 `ai_pet 0.1.3`。
- 学生 A：设备 `1c:db:d4:48:d1:50`，关系阶段 `attuned`。
- 测试学生 B：设备 `a0:f2:62:e3:91:d8`，关系阶段 `first_meeting`。
- 验证入口：生产控制台 `POST /api/xiaoxin/devices/{device_id}/text-chat`，使用各设备自己的 confirmed speaker profile，由真实硬件接收并播放 TTS。
- 时间推进方式：写入互斥事实后断开并重启生产服务，再通过 MQTT 唤醒两块设备建立新 WebSocket 连接；重复召回以排除当前连接短期上下文和偶然排序命中。
- 最终结论：两名学生的身份链、长期 Evidence、召回审计和播报结果均隔离；同题回复已呈现可观察的表达与策略差异。跨自然日、数周累积后的稳定养成尚未由本次同日测试证明。

## 测试身份与互斥事实

| 项目 | 学生 A | 测试学生 B |
| --- | --- | --- |
| pet | `pet_If3AV5MgzWVmfzGCcd8NIKlh` | `pet_KWMbiJc3kNvcMqPVyvUMQHZv` |
| memory subject | `ms_10j9s2kszy7z8sUoXX8WW27V` | `ms_YvavKuO4OClkRDSYgrR68EN` |
| 测试代号 | 青松17（因与旧稳定 profile 冲突，保持 candidate，不作为 A 最终召回断言） | 海盐42（active） |
| 最近活动 | 准备机器人竞赛（active） | 练习校园乐队键盘（active） |
| 做事习惯 | 先列计划再行动（active） | 边尝试边调整（active） |

## 发现问题1

输入“你别复述我的资料”时，`boundary_guard` 把“资料”同时当成竞赛领域词和受限资源词，误触发 `competition_resources`，真实设备播放“不能替你联系上届学长，也不能给你源文件或私下资料”等无关硬拒绝。

## 修改1

竞赛资源边界改为必须同时命中真实竞赛领域词和受限资源词。修复后原句回到 `open_chat/free_chat`；“智能车竞赛 + 联系学长 + 源文件”仍保持硬拒绝。两块设备生产复测均不再播放错误模板。

## 发现问题2

跨服务重启后，多事实召回不完整。B 原策略只允许引用 1 条记忆，A 最多 2 条；“最近在做什么”也没有映射到 `goal/interest`，导致三项问题只能取到部分 Evidence。

## 修改2

显式多事实召回预算提高到最多 3 个语义槽位，并为“最近/当前/正在做或准备”增加 `goal/interest` hints。B 的代号、当前活动和做事习惯可以在同一轮选中。

## 发现问题3

检索审计已选中 B 的三条正确 active Evidence，但 `<memory>` 只注入裸值：

```text
海盐42
边尝试边调整
练习校园乐队键盘
```

模型无法稳定判断这些值分别对应代号、习惯和当前活动，真实回答为：“我这边目前没有查到相关的记录呢……”。这不是数据库丢失，而是提示语义缺失。

## 修改3

显式召回的记忆项改为包含 `fact`、`fact_key`、`kind` 的紧凑 JSON，并增加明确消费契约：`<memory>` 是当前主体本轮已成功召回的可靠事实，用户主动询问时直接回答，不得把已召回内容说成“没有记录”，也不得补充标签外事实。普通聊天和情绪续接仍使用自然语言摘要。

修复后 B 的真实播报为：

> 你的测试代号是海盐42。你最近在练习校园乐队键盘。你的做事习惯是边尝试边调整。

## 发现问题4

“我最近在做的事情和做事习惯分别是什么？”没有“我的测试代号”等旧触发词，被分类成普通 `conversation`，因此 A 的失败轮完全没有 retrieval audit。

## 修改4

增加第一人称活动/习惯召回分类：必须同时包含“我”、最近或当前活动/做事习惯领域词、明确问句词，才进入 `explicit_recall`。陈述句不会被误判为召回请求。

## 发现问题5

进入召回后，A 首次只答对“机器人竞赛”，却把做事习惯轮换成旧的“先画等效电路”，还额外提到“无糖乌龙茶”。根因有两个：

1. 用于普通陪伴多样性的近期引用惩罚也作用于显式召回，每引用一次扣 12 分，重复询问会把最新事实轮换成旧事实。
2. `interest + goal` 是“最近活动”的两个候选类型，却被预算算法算成两个独立问题槽位，使两项问题错误取 3 条 Evidence。

## 修改5

显式召回关闭近期引用轮换惩罚，保证用户重复询问时答案稳定；`interest/goal/future_event` 合并为一个 `current_activity` 槽位。普通陪伴仍保留近期引用惩罚。

修复后 A 的真实播报为：

> 小脑袋里记着两件事。你最近在准备机器人竞赛。做事习惯是先列计划再行动。

## 最终测试矩阵

| 场景 | 真实结果 | 结论 |
| --- | --- | --- |
| 两设备 MQTT 唤醒并建立 WebSocket | 两台均为 `connected`，固件均为 `0.1.3` | 通过 |
| A 跨重启召回两项 active 事实 | 机器人竞赛、先列计划再行动 | 通过 |
| B 跨重启召回三项 active 事实 | 海盐42、校园乐队键盘、边尝试边调整 | 通过 |
| A/B 交叉诱导 | 两边均纠正为自己的互斥事实，诱导后再次召回未串写 | 通过 |
| Evidence 归属审计 | A 仅引用 A 的 pet/subject；B 仅引用 B 的 pet/subject | 通过 |
| 真机 TTS 完成回执 | A、B 最终轮均收到 `tts_state=terminal` | 通过 |
| 数据库完整性 | companion/control 数据库均为 `integrity_check=ok` | 通过 |
| 同题个性化表达 | A 更结构化、主动；B 更简短、低主动，限时任务策略也不同 | 通过，但差异仍属克制型 |
| 跨自然日/数周逐渐养成 | 本次采用服务重启和新连接推进，未等待真实多日 | 未验证 |

## 最终召回审计

- A turn `17680ddcfbf5427f9ffab02924c7045f`：候选 6 条，预算 2，最终只选择 `goal:robot_competition_preparation` 与 `preference:planning_habit`，两条均为 active，pet/subject 与 A 完全一致。
- B turn `76f61edd18c140a5b7be1c3fe6caa461`：候选 3 条，预算 3，最终选择 `profile:test_codename`、`preference:working_style`、`interest:keyboard_practice`，三条均为 active，pet/subject 与 B 完全一致。
- 两轮没有跨 pet、跨 memory subject 引用。

## 能力判断

- 不同学生拥有独立长期记忆：已实现并由双真机跨重启召回证明，置信度高。
- 不同学生的小芯有不同感觉：当前已出现可观察差异，但主要是表达组织、主动性和应对策略的克制差异，不是夸张人格，置信度中高。
- 长期隐式养成：已有 Evidence、interaction contract、relationship stage 和 expression style 的持久化基础，但跨周稳定性、遗忘策略和旧事实冲突收敛仍需持续观测，置信度中。
- A 的“青松17”保持 candidate 是冲突保护的预期结果；若要替换旧稳定 profile，应走明确更正流程，不能为测试绕过门禁。
