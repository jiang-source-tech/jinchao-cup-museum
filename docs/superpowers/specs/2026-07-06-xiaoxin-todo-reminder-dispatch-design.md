# Xiaoxin Todo Reminder Dispatch Design

## Goal

把已经持久化的小程序提醒事项接入服务端投递链路：当提醒事项到点后，服务端可以把它转换为 `todo_reminder`，复用现有 Xiaoxin dispatcher 发送到绑定硬件端。

## Context

上一阶段已经完成 `student_todos`、小程序 CRUD 和 Overview `todo` 摘要。功能地图中 P0/P1 的共同缺口仍然是“真实数据接入后缺调度/端到端接入”。本阶段先做可测试的核心投递器，不直接启动后台常驻循环。

## Scope

- `student_todos` 增加 `reminded_at` 和 `reminder_delivery_id`，用于去重。
- Identity store 提供 `list_due_student_todos(now)` 和 `mark_student_todo_reminded(...)`。
- 新增 `XiaoxinTodoReminderScheduler.dispatch_due_todos(now)`。
- 到点且未提醒的 pending todo 会提交为 `XiaoxinEvent.TODO_REMINDER`。
- 仅当学生账号有已绑定设备时投递；无绑定设备时跳过且不标记，便于后续绑定后重试。
- `create_xiaoxin_control_runtime()` 暴露 `todo_reminder_scheduler`。

## Non-Goals

- 不启动后台定时循环。
- 不做课程提醒调度。
- 不做重复提醒规则。
- 不做失败重试状态机。
- 不把提醒完成状态自动改为 `done`。

## Event Mapping

到点 todo 映射为：

- `event`: `todo_reminder`
- `title`: `提醒事项`
- `body`: todo 标题
- `tag`: `todo:{todo_id}`
- `ttl_ms`: 30 分钟
- `speak`: `true`
- `speak_text`: `小芯提醒你，{title}。`
- `todo_title`: todo 标题
- `due_at`: todo 到点时间

## Reliability Rule

投递器先用 `claim_student_todo_for_reminder()` 原子写入 `reminded_at`、保持 `reminder_delivery_id` 为空，再调用 `dispatcher.submit()`。提交成功后写入 `reminder_delivery_id`。这样并发 tick 只能有一个拿到 claim，避免同一事项重复投递。

如果 `dispatcher.submit()` 抛出异常，投递器释放 claim，让下一次 tick 可以重试。若没有绑定设备，不写入 reminded 字段。

用户修改 `dueAt` 或把事项重新设为 `pending` 时，会清空旧 `reminded_at` 和 `reminder_delivery_id`，使它重新具备提醒资格。

## Next Step

下一步可以把 `todo_reminder_scheduler.dispatch_due_todos()` 挂到 runtime 的周期性任务，或先暴露一个控制台/测试 API 做手动 tick。后台循环应有可配置间隔和清晰的启动/停止测试。

## Runtime Background Loop

运行时现在支持可配置的周期 tick：

```yaml
xiaoxin_control:
  reminder_tick_seconds: 30
  course_reminder_scheduler_enabled: true
  todo_reminder_scheduler_enabled: true
```

`create_xiaoxin_control_runtime()` 在代码层面默认不启动后台循环，除非配置显式打开；仓库提交的默认 `config.yaml` 会为 Xiaoxin 控制台部署打开它。每次 tick 会按开关调用 `todo_reminder_scheduler.dispatch_due_todos(datetime.now(timezone.utc))` 和 `course_reminder_scheduler.dispatch_due_courses(...)`；tick 异常会被记录，循环继续运行。`XiaoxinControlRuntime.stop()` 会取消正在执行的 tick，并等待任务结束后再停止 doorbell client。
