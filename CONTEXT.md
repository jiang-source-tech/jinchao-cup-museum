# Xiaoxin Companion Context

小芯陪伴上下文描述真实业务事件如何成为可审计的记忆依据，以及这些依据如何形成面向用户和设备的安全表达。

## Language

**Observation**:
由可信业务入口确认已经发生的事件；它记录事件本身，不直接等同于用户事实、关系贡献或任务结果。
_Avoid_: 人物画像记录、记忆信号、原始事件

**Evidence**:
从 Observation 或已完成对话中验证得到、可追溯且具有归属范围的事实依据；只有符合策略的 Evidence 才能参与召回、关系计算或主动陪伴。
_Avoid_: 画像字段、模型印象、用户标签

**Projection**:
从当前有效 Evidence 和策略生成、供语音、小程序、硬件或运营工具消费的安全视图。
_Avoid_: 事实源、画像数据库

**Memory Subject**:
个人小芯所识别的具体说话人；私人 Evidence 必须归属于已确认的 Memory Subject。
_Avoid_: 设备用户、当前账号

**Delivery Outcome**:
通知、提醒或主动陪伴内容在设备链路上的投递结果，例如已下发、已确认或已播放。
_Avoid_: 用户已完成、用户已接受

**User Outcome**:
由用户明确行为确认的业务结果，例如完成待办、接受帮助或拒绝主动陪伴；不能从 Delivery Outcome 自动推导。
_Avoid_: TTS done、通知已送达
