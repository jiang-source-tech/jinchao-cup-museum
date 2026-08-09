# 小芯提醒播报状态闭环设计

## 背景

小程序创建的提醒到点后，服务端调度器会向绑定硬件投递提醒。当前投递成功后，`student_todos` 只写入 `reminded_at` 和 `reminder_delivery_id`，`status` 仍保持 `pending`，因此小程序继续显示“待提醒”。

本产品中的提醒是一次性提醒事件，不是需要用户勾选完成的任务清单。因此只保留一个生命周期状态：硬件完成提醒即代表该提醒完成。

## 状态语义

`student_todos.status` 是提醒生命周期状态：

| status | 含义 | 小程序文案 |
| --- | --- | --- |
| `pending` | 尚未完成硬件提醒，仍可被调度 | 待提醒 |
| `done` | 硬件提醒投递流程已经成功完成 | 已提醒 |

本次不增加 `reminderState`，也不增加第二套状态维度。

`done` 不表示用户已经执行提醒内容。例如“提交实验报告”变成 `done`，只表示小芯已经完成提醒，不表示报告已经提交。

## 成功状态更新

调度器完成以下步骤后才能把提醒改为 `done`：

1. 找到到期且 `status='pending'` 的提醒。
2. 找到该学生绑定的硬件。
3. 成功认领提醒，防止重复调度。
4. 调用 `dispatcher.submit()` 创建投递任务并获得稳定的 `delivery_id`。
5. 调用 `dispatcher.wait_for_delivery_task(delivery_id)` 等待通知与 TTS 后台任务结束。
6. 从 dispatcher 的投递存储读取最终 delivery 记录。
7. 只有最终 delivery 状态为 `done` 时，才调用 `mark_student_todo_reminded()` 写入最终状态。

最终数据库状态：

```text
status = done
reminded_at = 本次调度时间
reminder_delivery_id = 对应投递记录 ID
```

`mark_student_todo_reminded()` 必须在同一个 SQLite 更新语句中写入三个字段，避免出现投递 ID 已保存但状态仍为 `pending` 的半完成数据。

## 失败与取消

如果 dispatcher 抛出异常或调度任务被取消：

- `status` 保持 `pending`。
- 现有释放逻辑清空临时 `reminded_at`。
- `reminder_delivery_id` 保持空字符串。
- 后续调度循环仍可重新尝试。

`dispatcher.submit()` 返回只表示投递任务已经创建，不表示硬件已经播报。只有等待后台任务结束并确认最终 delivery 状态为 `done`，提醒才进入 `done`。

如果后台任务结束后 delivery 状态不是 `done`，调度器必须释放提醒认领，使 `status` 保持 `pending`。第一版把所有非 `done` 终态都视为未完成提醒，不在提醒事项表中引入额外失败状态。

## 历史数据修复

现有数据库可能已经存在以下半完成记录：

```text
status = pending
reminded_at != ''
reminder_delivery_id != ''
```

旧版本会在 `dispatcher.submit()` 返回后立即写入 `reminder_delivery_id`，因此该字段非空不能单独证明播报成功。服务端启动时必须同时查询持久化通知历史，只修复对应 delivery 最终状态为 `done` 的记录。

身份数据库的最终修复语句为：

```sql
UPDATE student_todos
SET status = 'done',
    updated_at = ?
WHERE status = 'pending'
  AND reminded_at != ''
  AND reminder_delivery_id != '';
```

其中 delivery ID 参数只能来自通知历史中 `state='done'` 的记录。参数使用项目现有的 `utc_now_iso()`，保持 `updated_at` 格式一致。

迁移必须具备幂等性，每次启动执行都不会改变已经正确的数据。

不修复以下记录：

- `reminded_at` 和 `reminder_delivery_id` 都为空：尚未提醒。
- 只有 `reminded_at`、没有 `reminder_delivery_id`：可能是进程中断时遗留的认领记录，不能证明投递完成。
- 有 `reminder_delivery_id`，但通知历史为 `failed` 或不存在：不能证明播报完成，保持 `pending`。
- 已经是 `done`：保持不变。

## 服务端接口

现有小程序接口保持不变：

- `GET /api/miniprogram/todos`
- `POST /api/miniprogram/todos`
- `PATCH /api/miniprogram/todos/{todo_id}`
- `DELETE /api/miniprogram/todos/{todo_id}`

接口继续返回 `status`。不增加 `reminderState`，不要求小程序根据时间或投递字段进行推断。

新建提醒仍返回：

```json
{
  "status": "pending"
}
```

硬件提醒成功后，列表接口返回：

```json
{
  "status": "done"
}
```

## 小程序行为

小程序已经支持：

- `pending` → “待提醒”
- `done` → “已提醒”

本次原则上不需要修改小程序业务逻辑。服务端部署完成并重启后，小程序重新进入提醒页或点击重试刷新，即可读取新的 `done` 状态。

仍需运行小程序现有测试，确认状态格式化和列表刷新行为没有回归。

## 测试策略

### 身份存储测试

- 先增加失败测试：`mark_student_todo_reminded()` 成功后，记录的 `status` 必须为 `done`。
- 验证 `reminded_at` 与 `reminder_delivery_id` 同时正确保存。
- 验证未认领、非 `pending` 或字段条件不满足时不会错误更新。

### 调度器测试

- 成功投递后状态为 `done`。
- 第二次调度不会再次投递同一提醒。
- dispatcher 异常、任务取消或最终 delivery 状态不是 `done` 时，状态仍为 `pending`，认领字段被释放。
- 测试 dispatcher 必须明确模拟“后台任务完成”和最终 delivery 状态，不能再把 `submit()` 返回等同于播报成功。
- 没有绑定设备时状态仍为 `pending`。

### 历史迁移测试

- 构造 `pending + reminded_at + reminder_delivery_id` 的旧记录，只有通知历史对应状态为 `done` 时才自动变成 `done`。
- 通知历史对应状态为 `failed` 或缺失时保持 `pending`。
- 只有 `reminded_at` 的记录不得自动变成 `done`。
- 多次初始化迁移结果保持一致。

### 小程序回归

- `done` 显示“已提醒”。
- 页面重新显示时重新获取服务端列表。
- 运行 `npm test`。

## 验收标准

1. 新提醒创建后显示“待提醒”。
2. 硬件投递与 TTS 后台任务完成，并且 delivery 最终状态为 `done` 后，服务端记录才变为 `done`。
3. 小程序刷新后显示“已提醒”。
4. 投递失败、取消或没有绑定设备时仍显示“待提醒”。
5. 已经成功投递的旧记录在服务端重启后自动修复为 `done`。
6. 同一条已经完成的提醒不会被再次调度。
