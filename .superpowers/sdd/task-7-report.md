## Task 7 Report - CRUD, Binding, And Unbinding Triggers

### Status

Implemented the server-side Overview triggers requested by Task 7. Changes are limited to the Xiaoxin control handler and its tests; Tasks 8-9 weather APIs and diagnostics were not implemented, and no legacy WebSocket Overview synchronization was added.

### Trigger Matrix

| Successful write | Trigger |
| --- | --- |
| Semester PATCH | `refresh_user_devices(user_id, "semester_updated")` |
| Course POST | `refresh_user_devices(user_id, "course_created")` |
| Course PATCH | `refresh_user_devices(user_id, "course_updated")` |
| Course DELETE when a row was deleted | `refresh_user_devices(user_id, "course_deleted")` |
| Todo POST | `refresh_user_devices(user_id, "todo_created")` |
| Todo PATCH, including completion via `status=done` | `refresh_user_devices(user_id, "todo_updated")` |
| Todo DELETE when a row was deleted | `refresh_user_devices(user_id, "todo_deleted")` |
| Activation bind, for Bearer/miniprogram and Cookie/console sessions | `refresh_user_devices(current_owner_id, "device_bound")` |
| Miniprogram or console unbind when identity unbind succeeded | `clear_unbound_device(retained_device_id, "device_unbound")` |

All triggers are awaited in the existing async handler flow. No detached or untracked task is created. The calls occur after the synchronous identity/activation store methods return, which is after their SQLite transaction contexts have committed.

### Failure Isolation

- Validation failures, missing update targets, no-op deletes, invalid bind codes, rejected unbind targets, and identity-store exceptions do not invoke Overview triggers.
- `_refresh_user_overview` catches ordinary Overview service exceptions and records exactly `overview refresh failed reason=<reason>` with tag `xiaoxin.overview`.
- `_clear_unbound_device_overview` catches ordinary Overview service exceptions and records exactly `overview clear failed reason=<reason>` with tag `xiaoxin.overview`.
- MQTT, weather, or Overview generation failures therefore cannot turn an already committed business write into an HTTP failure.
- `asyncio.CancelledError` is not swallowed because it is not an `Exception`; the handler directly awaits the trigger and does not leak a background task.

### TDD Evidence

Each write family was introduced with a failing focused test before its production call was added:

- Semester RED: expected `semester_updated`, observed no calls.
- Course RED: expected create/update/delete reasons, observed no calls.
- Todo RED: expected create/update/completion/delete reasons, observed no calls.
- Activation bind/rebind RED: expected `device_bound`, observed no calls.
- Miniprogram/console unbind RED: expected retained-device clear, observed no calls.
- Failure isolation RED: with the helper catch intentionally absent, refresh and clear service failures propagated as two `RuntimeError` failures. The minimal catch-and-log implementation made both tests GREEN.

Final focused result:

```text
8 passed, 107 deselected in 3.38s
```

### Verification

```text
python -m pytest tests/xiaoxin/test_control_handler.py -q
115 passed in 26.28s

python -m pytest tests/xiaoxin/test_overview_service.py tests/xiaoxin/test_control_runtime.py tests/xiaoxin/test_config_contract.py -q
61 passed in 12.62s

python -m py_compile core/api/xiaoxin_control_handler.py tests/xiaoxin/test_control_handler.py
exit 0

python -c "from core.api.xiaoxin_control_handler import XiaoxinControlHandler; print(XiaoxinControlHandler.__name__)"
XiaoxinControlHandler

git diff --check -- main/xiaozhi-server/core/api/xiaoxin_control_handler.py main/xiaozhi-server/tests/xiaoxin/test_control_handler.py
exit 0
```

### Scope And Concerns

- GET semester, course list/detail, todo list, curriculum Overview, student Overview, and device reads were verified to make zero refresh/clear calls.
- Todo completion intentionally uses `todo_updated`; `todo_completed` is not a Task 7 reason and was not invented.
- The legacy bind endpoints that always return `activation_required` remain unchanged and do not trigger Overview.
- The pre-existing `.superpowers/sdd/progress.md` working-tree modification was not edited or staged.
- No known functional concern remains. Confidence: high.
