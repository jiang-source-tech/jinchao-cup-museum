# Xiaoxin Reminder Done State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mark one-shot reminders as `done` after successful hardware delivery and automatically repair historical delivered records that remain `pending`.

**Architecture:** Keep `student_todos.status` as the single reminder lifecycle state. Make the final store update atomically write `status='done'`, `reminded_at`, and `reminder_delivery_id`; reconcile old records against persistent notification history and repair only delivery IDs whose final state is `done`; leave failure/cancellation release behavior unchanged.

**Tech Stack:** Python 3, SQLite, pytest, existing Xiaoxin identity store and todo reminder scheduler, WeChat Mini Program Node verification.

## Global Constraints

- `pending` means the hardware reminder has not completed and remains schedulable.
- `done` means the hardware reminder delivery flow completed successfully.
- Do not add `reminderState` or a second status dimension.
- Do not mark records `done` when only `reminded_at` exists without `reminder_delivery_id`.
- Historical repair must be idempotent, require no schema change, and trust only notification-history records whose final state is `done`.
- Dispatcher failure, cancellation, or missing bound device must leave the reminder `pending`.

---

## File Structure

- Modify `main/xiaozhi-server/core/xiaoxin/identity/store.py`: atomic final state update and startup repair.
- Modify `main/xiaozhi-server/tests/xiaoxin/test_identity_store.py`: store and migration regression tests.
- Modify `main/xiaozhi-server/tests/xiaoxin/test_todo_reminder_scheduler.py`: end-to-end scheduler state assertions.
- Verify `D:/AI_Pet/小程序/Hzcu_xiaoxin_miniprogram`: existing `done` label and list refresh regression suite.

### Task 1: Atomic Successful Reminder Completion

**Files:**
- Modify: `main/xiaozhi-server/core/xiaoxin/identity/store.py:938-965`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_identity_store.py:315-350`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_todo_reminder_scheduler.py:34-76`

**Interfaces:**
- Consumes: an already claimed `pending` todo with non-empty `reminded_at` and empty `reminder_delivery_id`.
- Produces: `mark_student_todo_reminded(...)` returning a todo with `status == "done"`, the supplied delivery ID, and the supplied reminder time.

- [ ] **Step 1: Add failing identity-store assertions**

In `test_student_todos_list_due_unreminded_and_mark_reminded`, capture the marked record and assert all final fields:

```python
marked = store.mark_student_todo_reminded(
    user.id,
    due["id"],
    "del_20260706_010000_abcd1234",
    "2026-07-06T09:00:00+08:00",
)

assert marked is not None
assert marked["status"] == "done"
assert marked["reminded_at"] == "2026-07-06T09:00:00+08:00"
assert marked["reminder_delivery_id"] == "del_20260706_010000_abcd1234"
```

- [ ] **Step 2: Add failing scheduler assertion**

In `test_todo_reminder_scheduler_dispatches_due_bound_todos_once`, add:

```python
assert stored["status"] == "done"
```

This checks the real scheduler path, not only a direct store call.

- [ ] **Step 3: Run focused tests to verify RED**

Run from `main/xiaozhi-server`:

```bash
pytest -q tests/xiaoxin/test_identity_store.py::test_student_todos_list_due_unreminded_and_mark_reminded tests/xiaoxin/test_todo_reminder_scheduler.py::test_todo_reminder_scheduler_dispatches_due_bound_todos_once
```

Expected: two failures showing actual status `pending` instead of expected `done`.

- [ ] **Step 4: Implement the atomic final update**

Change the SQL in `mark_student_todo_reminded()` to:

```python
cursor = conn.execute(
    """
    UPDATE student_todos
    SET status = 'done',
        reminded_at = ?,
        reminder_delivery_id = ?,
        updated_at = ?
    WHERE id = ?
      AND user_id = ?
      AND status = 'pending'
      AND reminded_at != ''
      AND reminder_delivery_id = ''
    """,
    (reminded_at_value, delivery_id, utc_now_iso(), todo_id, user_id),
)
```

Do not change claim or release SQL.

- [ ] **Step 5: Run focused tests to verify GREEN**

Run the same focused pytest command.

Expected: `2 passed`.

- [ ] **Step 6: Run failure-path scheduler tests**

Run:

```bash
pytest -q tests/xiaoxin/test_todo_reminder_scheduler.py
```

Expected: all scheduler tests pass; cancellation and missing-device cases remain `pending`.

- [ ] **Step 7: Commit**

```bash
git add main/xiaozhi-server/core/xiaoxin/identity/store.py main/xiaozhi-server/tests/xiaoxin/test_identity_store.py main/xiaozhi-server/tests/xiaoxin/test_todo_reminder_scheduler.py
git commit -m "fix: complete reminders after successful delivery"
```

### Task 2: Notification-History-Verified Historical State Repair

**Files:**
- Modify: `main/xiaozhi-server/core/xiaoxin/identity/store.py`
- Modify: `main/xiaozhi-server/core/xiaoxin/notification_history_store.py`
- Modify: `main/xiaozhi-server/core/xiaoxin/control_runtime.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_identity_store.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_notification_history_store.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py`

**Interfaces:**
- Consumes: pending todo delivery IDs plus persisted notification-history final states.
- Produces: runtime reconciliation that repairs only IDs confirmed as `done` by notification history.

- [ ] **Step 1: Add failing store, history, and runtime coordination tests**

Add tests proving: the identity store repairs only supplied delivery IDs; notification history returns final states by delivery ID; runtime passes only IDs whose history state is `done`.

- [ ] **Step 2: Verify RED**

Run the three focused tests. Expected: failure because the new coordination interfaces do not exist.

- [ ] **Step 3: Implement the three boundaries**

- `XiaoxinIdentityStore.list_pending_student_todo_delivery_ids()` lists candidate IDs.
- `XiaoxinNotificationHistoryStore.get_delivery_states(ids)` returns persisted final states.
- `_repair_completed_todos_from_history(...)` selects `done` IDs and calls `repair_completed_student_todos(ids)`.
- Call the coordinator after both stores are constructed in `create_xiaoxin_control_runtime()`.

- [ ] **Step 4: Verify GREEN and idempotency**

Run the three focused tests. Expected: `3 passed`; repeated repair returns zero additional changes.

- [ ] **Step 5: Run relevant server regression suite**

Run:

```bash
pytest -q tests/xiaoxin/test_identity_store.py tests/xiaoxin/test_todo_reminder_scheduler.py tests/xiaoxin/test_control_handler.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Verify the mini program already supports the server result**

Run from `D:/AI_Pet/小程序/Hzcu_xiaoxin_miniprogram`:

```bash
npm test
```

Expected: exit code `0`, final line `verification passed`; `done` remains mapped to “已提醒” and the reminders page refreshes in `onShow`.

- [ ] **Step 7: Commit**

```bash
git add main/xiaozhi-server/core/xiaoxin/identity/store.py main/xiaozhi-server/tests/xiaoxin/test_identity_store.py
git commit -m "fix: repair delivered reminder status on startup"
```

### Task 3: Wait for the Real Hardware Delivery Result

**Files:**
- Modify: `main/xiaozhi-server/core/xiaoxin/todo_reminder_scheduler.py`
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_todo_reminder_scheduler.py`

**Interfaces:**
- Consumes: `dispatcher.submit(request)`, `dispatcher.wait_for_delivery_task(delivery_id)`, and `dispatcher.store.get(delivery_id)`.
- Produces: a reminder marked `done` only when the final delivery record state is `XiaoxinDeliveryState.DONE`; all other outcomes release the claim and remain `pending`.

- [ ] **Step 1: Make the fake dispatcher model background completion**

Update the test fake so `submit()` creates a stored delivery record, `wait_for_delivery_task()` is an explicit async boundary, and `store.get()` returns the final state. Import `XiaoxinDeliveryState` and use:

```python
class FakeDispatcher:
    def __init__(self, final_state=XiaoxinDeliveryState.DONE):
        self.submitted = []
        self.final_state = final_state
        self.records = {}
        self.store = self

    async def submit(self, request):
        self.submitted.append(request)
        delivery_id = f"del-{len(self.submitted)}"
        record = type(
            "Record",
            (),
            {"delivery_id": delivery_id, "state": self.final_state},
        )()
        self.records[delivery_id] = record
        return record

    async def wait_for_delivery_task(self, delivery_id):
        assert delivery_id in self.records

    def get(self, delivery_id):
        return self.records.get(delivery_id)
```

- [ ] **Step 2: Add a failing non-done delivery test**

Add a scheduler test using `FakeDispatcher(XiaoxinDeliveryState.FAILED)` and assert:

```python
assert dispatched == []
assert stored["status"] == "pending"
assert stored["reminded_at"] == ""
assert stored["reminder_delivery_id"] == ""
```

- [ ] **Step 3: Verify RED**

Run:

```bash
pytest -q tests/xiaoxin/test_todo_reminder_scheduler.py
```

Expected: the failed-delivery test fails because the scheduler currently marks the reminder `done` immediately after `submit()`.

- [ ] **Step 4: Implement the real completion gate**

Import `XiaoxinDeliveryState`. After `submit()` returns:

```python
await self.dispatcher.wait_for_delivery_task(record.delivery_id)
final_record = self.dispatcher.store.get(record.delivery_id)
if final_record is None or final_record.state != XiaoxinDeliveryState.DONE:
    self.identity_store.release_student_todo_reminder_claim(
        todo["user_id"],
        todo["id"],
    )
    continue
```

Only call `mark_student_todo_reminded()` after this condition passes. Keep the existing cancellation and exception release blocks around submit, wait, and final-state lookup.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
pytest -q tests/xiaoxin/test_todo_reminder_scheduler.py
```

Expected: all scheduler tests pass, including successful, failed, cancelled, concurrent, and unbound paths.

- [ ] **Step 6: Run dispatcher integration tests**

Run:

```bash
pytest -q tests/xiaoxin/test_dispatcher.py tests/xiaoxin/test_todo_reminder_scheduler.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add main/xiaozhi-server/core/xiaoxin/todo_reminder_scheduler.py main/xiaozhi-server/tests/xiaoxin/test_todo_reminder_scheduler.py
git commit -m "fix: complete reminders after delivery finishes"
```

### Task 4: Final Verification

**Files:**
- Verify both repositories; no new production files.

**Interfaces:**
- Produces fresh evidence that successful delivery becomes `done`, old records are repaired, failure paths stay `pending`, and the mini program displays the result.

- [ ] **Step 1: Run server tests**

Run from `main/xiaozhi-server`:

```bash
pytest -q tests/xiaoxin/test_identity_store.py tests/xiaoxin/test_todo_reminder_scheduler.py tests/xiaoxin/test_control_handler.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run mini program tests**

Run from `D:/AI_Pet/小程序/Hzcu_xiaoxin_miniprogram`:

```bash
npm test
```

Expected: `verification passed`.

- [ ] **Step 3: Check diffs and repository state**

Run in both repositories:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and no uncommitted implementation changes.
