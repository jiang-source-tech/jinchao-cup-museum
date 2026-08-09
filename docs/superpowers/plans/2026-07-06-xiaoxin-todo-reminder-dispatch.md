# Xiaoxin Todo Reminder Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dispatch due persisted student todos into the existing Xiaoxin delivery pipeline.

**Architecture:** Extend `XiaoxinIdentityStore` with due-query and reminded-marker methods, add a small `XiaoxinTodoReminderScheduler` service, and wire that service into `XiaoxinControlRuntime`.

**Tech Stack:** Python, sqlite3, pytest, existing Xiaoxin dispatcher.

## Global Constraints

- Keep scheduler core testable without running a background loop.
- Do not implement course reminders, recurring reminders, voice reminder parsing, or a retry state machine.
- Do not mark unbound-device todos as reminded.
- Use existing `XiaoxinControlEventRequest` and `XiaoxinEvent.TODO_REMINDER`.

---

### Task 1: Store Due Todo Query And Marker

**Files:**
- Modify: `main/xiaozhi-server/core/xiaoxin/identity/store.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_identity_store.py`

- [x] **Step 1: Write failing test**

Add `test_student_todos_list_due_unreminded_and_mark_reminded`.

- [x] **Step 2: Run failing test**

Run: `python -m pytest tests/xiaoxin/test_identity_store.py::test_student_todos_list_due_unreminded_and_mark_reminded -q`

Expected: fail because `list_due_student_todos` does not exist.

- [x] **Step 3: Implement store support**

Add `reminded_at`, `reminder_delivery_id`, schema migration helpers, `list_due_student_todos(now)`, and `mark_student_todo_reminded(...)`.

- [x] **Step 4: Run store focused tests**

Run: `python -m pytest tests/xiaoxin/test_identity_store.py::test_student_todos_list_due_unreminded_and_mark_reminded tests/xiaoxin/test_identity_store.py::test_student_todos_crud_sorting_and_user_isolation -q`

Expected: pass.

### Task 2: Todo Reminder Scheduler

**Files:**
- Create: `main/xiaozhi-server/core/xiaoxin/todo_reminder_scheduler.py`
- Create: `main/xiaozhi-server/tests/xiaoxin/test_todo_reminder_scheduler.py`

- [x] **Step 1: Write failing scheduler tests**

Cover dispatching one due bound todo exactly once, and skipping unbound due todos without marking them.

- [x] **Step 2: Run failing scheduler tests**

Run: `python -m pytest tests/xiaoxin/test_todo_reminder_scheduler.py -q`

Expected: fail because the module does not exist.

- [x] **Step 3: Implement scheduler**

Create `XiaoxinTodoReminderScheduler.dispatch_due_todos(now)`, mapping due todos to `XiaoxinControlEventRequest` with `event=TODO_REMINDER`.

- [x] **Step 4: Run scheduler tests**

Run: `python -m pytest tests/xiaoxin/test_todo_reminder_scheduler.py -q`

Expected: pass.

### Task 3: Runtime Wiring

**Files:**
- Modify: `main/xiaozhi-server/core/xiaoxin/control_runtime.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py`

- [x] **Step 1: Write failing wiring test**

Add `test_control_runtime_exposes_todo_reminder_scheduler`.

- [x] **Step 2: Run failing wiring test**

Run: `python -m pytest tests/xiaoxin/test_control_runtime.py::test_control_runtime_exposes_todo_reminder_scheduler -q`

Expected: fail because runtime has no `todo_reminder_scheduler`.

- [x] **Step 3: Wire scheduler into runtime**

Instantiate scheduler from the same identity store and dispatcher used by the runtime.

- [x] **Step 4: Run focused verification**

Run: `python -m pytest tests/xiaoxin/test_identity_store.py::test_student_todos_list_due_unreminded_and_mark_reminded tests/xiaoxin/test_todo_reminder_scheduler.py tests/xiaoxin/test_control_runtime.py::test_control_runtime_exposes_todo_reminder_scheduler -q`

Expected: pass.
