# Xiaoxin Todo Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persisted student reminder items and feed them into the Xiaoxin overview todo card.

**Architecture:** Extend the existing `XiaoxinIdentityStore` SQLite boundary with `student_todos`, then expose authenticated miniprogram routes from `XiaoxinControlHandler`. Reuse `_student_overview()` so both miniprogram overview and device overview sync receive the same todo summary.

**Tech Stack:** Python, aiohttp handlers, sqlite3, pytest.

## Global Constraints

- Work inside `main/xiaozhi-server`.
- Preserve the existing auth boundary: miniprogram todo APIs require the same Bearer token/session logic as profile, courses, and overview.
- Use camelCase JSON externally and snake_case internally, matching the existing course APIs.
- Do not add a scheduler, notification dispatch, voice reminder parsing, recurring rules, or weather integration in this task.

---

### Task 1: Student Todo Store

**Files:**
- Modify: `main/xiaozhi-server/core/xiaoxin/identity/store.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_identity_store.py`

**Interfaces:**
- Produces: `list_student_todos(user_id: str) -> list[dict[str, Any]]`
- Produces: `create_student_todo(user_id: str, fields: dict[str, Any]) -> dict[str, Any]`
- Produces: `get_student_todo(user_id: str, todo_id: str) -> dict[str, Any] | None`
- Produces: `update_student_todo(user_id: str, todo_id: str, fields: dict[str, Any]) -> dict[str, Any] | None`
- Produces: `delete_student_todo(user_id: str, todo_id: str) -> bool`

- [x] **Step 1: Write failing store test**

Add a test that creates two users, stores reminders for each, verifies pending reminders sort by `due_at`, updates one reminder to `done`, deletes another, and confirms user isolation.

- [x] **Step 2: Run failing test**

Run: `python -m pytest tests/xiaoxin/test_identity_store.py::test_student_todos_crud_sorting_and_user_isolation -q`

Expected: fail because `create_student_todo` does not exist.

- [x] **Step 3: Implement store**

Add the `student_todos` table to `_init_schema()` and implement the methods listed above. Normalize `title`, `dueAt`/`due_at`, `notes`, and `status`; reject blank title, blank due time, and statuses other than `pending` or `done`.

- [x] **Step 4: Run store tests**

Run: `python -m pytest tests/xiaoxin/test_identity_store.py -q`

Expected: all identity store tests pass.

### Task 2: Miniprogram Todo API And Overview Card

**Files:**
- Modify: `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`

**Interfaces:**
- Consumes: store methods from Task 1.
- Produces: `GET/POST/PATCH/DELETE /api/miniprogram/todos`.
- Produces: `_todo_overview_card(user_id: str, date_text: str) -> dict[str, Any]`.

- [x] **Step 1: Write failing handler tests**

Add one test for todo CRUD through authenticated miniprogram requests and extend the existing overview test to assert the `todo` card uses the nearest pending reminder.

- [x] **Step 2: Run failing tests**

Run: `python -m pytest tests/xiaoxin/test_control_handler.py::test_miniprogram_todos_crud_and_overview_summary tests/xiaoxin/test_control_handler.py::test_miniprogram_overview_uses_real_bound_device_and_curriculum -q`

Expected: fail because routes/handler helpers do not exist or overview still returns the empty todo card.

- [x] **Step 3: Implement handler routes**

Register the four `/api/miniprogram/todos` routes, add CRUD handlers, add `_validate_todo_payload`, `_student_todo_payload`, and `_todo_overview_card`, then call `_todo_overview_card` from `_student_overview()`.

- [x] **Step 4: Run focused handler tests**

Run: `python -m pytest tests/xiaoxin/test_control_handler.py -q`

Expected: handler tests pass.

### Task 3: Verification And Commit

**Files:**
- Modify only files touched by Tasks 1-2 plus this plan/spec if needed.

- [x] **Step 1: Run focused Xiaoxin tests**

Run: `python -m pytest tests/xiaoxin/test_identity_store.py tests/xiaoxin/test_control_handler.py -q`

Expected: all selected tests pass.

- [x] **Step 2: Inspect diff**

Run: `git diff --stat` and `git diff --check`.

Expected: no whitespace errors; diff limited to todo store/API/overview docs.

- [ ] **Step 3: Commit**

Run:

```bash
git add docs/superpowers/specs/2026-07-06-xiaoxin-todo-overview-design.md docs/superpowers/plans/2026-07-06-xiaoxin-todo-overview.md main/xiaozhi-server/core/xiaoxin/identity/store.py main/xiaozhi-server/core/api/xiaoxin_control_handler.py main/xiaozhi-server/tests/xiaoxin/test_identity_store.py main/xiaozhi-server/tests/xiaoxin/test_control_handler.py
git commit -m "feat: add xiaoxin todo overview source"
```
