# Xiaoxin Todo Overview Design

## Goal

把小程序侧的“提醒事项”接入服务端身份库，并让现有 `/api/miniprogram/overview` 与设备 `xiaoxin_overview_update` payload 的 `todo` 卡片展示真实摘要，而不是永久空态。

## Context

固件功能地图把“动态总览页”的缺口写得很直接：天气、课程、待办还没有真实服务端同步源。服务端当前已经完成课表源、小程序 Overview API，以及向在线设备发送 Overview 更新的控制接口。因此下一个服务端小目标不该重复做 Overview 框架，而应该补第一个真实待办/提醒源。

## Scope

本阶段只做“提醒事项”的持久化、CRUD API 和 Overview 摘要：

- 小程序用户可以创建、查看、更新、删除自己的提醒事项。
- 提醒事项包含标题、提醒时间、备注和完成状态。
- Overview `todo` 卡片显示最近一条未完成、提醒时间不早于查询日期当天 00:00 的事项，并显示未完成数量。
- 如果用户没有配置任何提醒事项，保持现有空态文案。
- 如果用户有提醒事项但当天及之后没有未完成事项，显示“暂无待提醒事项”。

## Non-Goals

- 不做到点调度器。
- 不自动下发通知到硬件。
- 不做语音创建提醒解析。
- 不做复杂重复规则。
- 不做天气源。

## Data Model

新增 SQLite 表 `student_todos`：

- `id`: 提醒事项 ID。
- `user_id`: 所属学生账号。
- `title`: 标题，必填。
- `due_at`: 带时区偏移的 ISO 时间字符串，必填，格式为 `YYYY-MM-DDTHH:MM:SS+08:00`。
- `notes`: 备注，默认空字符串。
- `status`: `pending` 或 `done`。
- `created_at`, `updated_at`: 服务端 UTC 时间。

## API

在现有小程序鉴权边界下新增：

- `GET /api/miniprogram/todos`
- `POST /api/miniprogram/todos`
- `PATCH /api/miniprogram/todos/{todo_id}`
- `DELETE /api/miniprogram/todos/{todo_id}`

请求字段使用前端友好的 camelCase，同时存储层继续使用 snake_case。更新接口允许局部更新。

## Overview Behavior

`_student_overview()` 调用 identity store 读取该用户所有提醒事项：

- `configured` 为是否存在任何提醒事项。
- `count` 为未完成事项总数。
- `detail` 为最近未完成事项的简短摘要。
- `nextTodo` 为最近未完成事项 payload；没有则为 `null`。

排序策略：未完成优先按 `due_at ASC, title ASC`。比较“今日及以后”时用 `date_text` 的 `YYYY-MM-DD` 前缀边界，不引入时区推断。

## Risks

- `due_at` 需要严格校验为带时区偏移的 ISO 时间；排序和到点扫描按 UTC 归一化比较。
- 只做摘要不做调度，用户会看到 Overview 数据，但不会自动到点推送。这是故意切分，避免把数据源和调度器绑成一个大任务。

## Test Plan

- identity store 测试覆盖创建、排序、更新、删除和用户隔离。
- control handler 测试覆盖小程序 todo CRUD。
- Overview 测试覆盖有未完成提醒时 `todo` 卡片显示真实数据。
- 设备 Overview sync 继续复用 `_student_overview()`，因此同一套测试能覆盖 payload 内容。
