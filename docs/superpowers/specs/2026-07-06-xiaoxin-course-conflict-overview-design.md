# Xiaoxin Course Conflict Overview Design

## Goal

让 `/api/miniprogram/curriculum/overview` 在返回今日课程时，同时给出服务端计算的课程冲突信息，避免课程冲突只停留在前端临时展示字段。

## Scope

- 只计算当前日期、当前教学周内的 `todayCourses`。
- 两门课程节次区间有交集时视为冲突，例如 `3-4` 与 `4-5` 冲突。
- overview 顶层返回 `conflictCount`，表示当天冲突课程对数量。
- 每个今日课程返回：
  - `conflictCount`: 与该课程冲突的课程数量。
  - `conflictCourseIds`: 冲突课程 id 列表。

## Non-Goals

- 不在课程 CRUD 列表里计算全学期冲突。
- 不处理跨校区通勤时间、教师冲突或教室占用冲突。
- 不改变课程保存规则；第一版只提供冲突提示。

## Reliability Rule

冲突计算基于服务端已有的学期周、weekday 和节次字段。只有 `_course_active_in_week()` 判定为当天有效的课程会进入冲突计算，因此假期、非本周课程和其他 weekday 的课程不会产生冲突提示。
