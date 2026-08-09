# Xiaoxin Course Reminder Dispatch Design

## Goal

把小程序课表接入 Xiaoxin 控制投递链路：课程到点后，服务端把当前课次转换为 `course_reminder`，发送到学生账号绑定的设备。

## Scope

- `student_courses` 增加 `reminded_at` 和 `reminder_delivery_id`，记录最近一次已处理课次。
- 课程提醒按课次 occurrence 去重，不按课程永久去重。同一门课本周提醒后，下周同一时间仍可提醒。
- 只有存在 `startsAt`、当前日期属于学期周范围、weekday 匹配、且已到“开始时间减去统一提前量”的课程进入 due 列表。
- 可延迟投递的课程提醒只使用绝对上课时间，不生成“X 分钟后”之类会随设备离线时间失真的相对文案。
- 课程开始后不再补发提醒；排队中的提醒必须在 `startsAt` 到达时按 TTL 过期。
- 无绑定设备时跳过课程且不写提醒标记，方便绑定设备后继续投递。
- runtime 共享一个 reminder loop，按配置分别调用 todo 和 course scheduler。

## Non-Goals

- 不做提前 N 分钟提醒；第一版使用 `remind_before_min=0`。
- 不做复杂单双周规则；第一版复用数字周范围解析，例如 `1-18`。
- 不把课程提醒结果写成课程完成状态。

## Event Mapping

- `event`: `course_reminder`
- `title`: `上课提醒`
- `body`: `{course_title} {starts_at} {classroom}`
- `tag`: `course:{course_id}:{occurrence_at}`
- `ttl_ms`: 从创建提醒到本次课程 `startsAt` 的剩余时间
- `speak`: `true`
- `course_name`: 课程名
- `classroom`: 教室
- `starts_at`: 课程开始时间
- `remind_before_min`: 学生统一课程提醒提前量

## Reliability Rule

调度器先用 `claim_student_course_for_reminder()` 原子写入当前 `occurrence_at` 且保持 `reminder_delivery_id` 为空，再提交 dispatcher。提交成功后写入 delivery id。并发 tick 只能有一个拿到同一课次的 claim。

如果投递被取消或抛出异常，调度器释放 claim，让下一次 tick 可以重试。课程被更新时清空提醒标记，避免旧课次时间阻塞新课次提醒。
