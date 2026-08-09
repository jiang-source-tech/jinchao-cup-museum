# Xiaoxin Bound Device Target Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the Xiaoxin control console to select any owned bound device as an event target, even when the device is not currently listening or connected over WebSocket.

**Architecture:** Keep backend delivery semantics unchanged: `/api/xiaoxin/devices` remains the source of owned bound devices, and `/api/xiaoxin/events` continues to submit to the dispatcher. Change only the console target-device UI so device availability affects status text and delivery results, not whether the target can be selected.

**Tech Stack:** Static HTML/JavaScript control console, Python pytest static regression tests.

## Global Constraints

- Do not broaden backend authorization; only the current account's bound devices can be sent to.
- Do not make overview sync available for offline devices; it still requires a live WebSocket connection.
- Preserve the dispatcher state machine: connected sends directly, wakeable uses MQTT doorbell wake, offline records delivery failure.

---

### Task 1: Target Device Selection

**Files:**
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_control_console_static.py`
- Modify: `main/xiaozhi-server/core/api/static/xiaoxin_control.html`

**Interfaces:**
- Consumes: `state.devices` from `/api/xiaoxin/devices`, with `owner_user_id`, `bind_status`, `state`, `doorbell_state`, `display_name`, and `device_id`.
- Produces: `targetDevices()` returning devices with `owner_user_id` and `bind_status === "bound"` for the event target dropdown.

- [x] **Step 1: Write the failing test**

```python
def test_control_console_target_devices_use_owned_bound_devices_not_runtime_state():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert "function targetDevices()" in html
    assert 'device.owner_user_id && device.bind_status === "bound"' in html
    assert "const selectableDevices = targetDevices();" in html
    assert "const selectableDevices = enabledDevices();" not in html
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/xiaoxin/test_control_console_static.py::test_control_console_target_devices_use_owned_bound_devices_not_runtime_state -q`

Expected: FAIL because `targetDevices()` does not exist and the console still calls `enabledDevices()`.

- [x] **Step 3: Write minimal implementation**

```javascript
function targetDevices() {
  return state.devices.filter((device) => device.owner_user_id && device.bind_status === "bound");
}

const selectableDevices = targetDevices();
```

Also remove the `offline` disabled attribute from the device radio input so a bound offline target can still populate the event form.

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/xiaoxin/test_control_console_static.py::test_control_console_target_devices_use_owned_bound_devices_not_runtime_state -q`

Expected: PASS.

- [x] **Step 5: Run focused Xiaoxin control tests**

Run: `python -m pytest tests/xiaoxin/test_control_console_static.py tests/xiaoxin/test_control_handler.py::test_post_event_returns_created_delivery_for_owned_bound_device tests/xiaoxin/test_control_handler.py::test_logged_in_user_can_wake_owned_bound_device -q`

Expected: PASS.
