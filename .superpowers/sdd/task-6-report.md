# Task 6 Report: Runtime Wiring And Daily Refresh Loop

## Status

Implemented the Task 6 server runtime wiring and refresh loop. The default configuration remains disabled, so this commit does not turn on production Overview publication.

## TDD Evidence

### RED

- Command: `python -m pytest tests/xiaoxin/test_control_runtime.py tests/xiaoxin/test_config_contract.py -x -vv`
- Result: failed at `test_runtime_exposes_overview_service_and_store` with `AttributeError: 'XiaoxinControlRuntime' object has no attribute 'overview_store'`.
- The failure was the expected missing runtime wiring, not a collection, fixture, or syntax error.

### GREEN

- Command: `python -m pytest tests/xiaoxin/test_control_runtime.py tests/xiaoxin/test_config_contract.py -q`
- Result: `33 passed in 4.25s`.

## Implemented Behavior

- `create_xiaoxin_control_runtime()` now explicitly constructs and exposes `overview_store` and `overview_service`.
- Relative Overview database paths resolve from the project directory; absolute test/deployment paths remain unchanged.
- The runtime constructs the Open-Meteo weather provider and PConline IP-location provider and injects them into the service.
- Empty `ip_hmac_secret` becomes `None`, never a built-in fallback key. `observe_device_ip()` therefore returns `overview_ip_hmac_unconfigured` before any automatic IP persistence.
- `overview_mqtt.enabled` is gated by both the Overview flag and the parent `xiaoxin_control.enabled` flag.
- The publisher remains detached while Overview is disabled or the runtime is stopped. The committed default is `enabled: false`.
- On enabled start, the runtime registers exactly one connect listener and one PUBACK listener, attaches the doorbell MQTT publisher, and starts at most one Overview task.
- PUBACK is forwarded to `OverviewSyncService.handle_publish_ack()`.
- The connect listener only signals an `asyncio.Event` with `call_soon_threadsafe`; it never awaits or drains on the Paho callback path.
- Stop clears loop/wakeup references before canceling, cancels and awaits the Overview task, detaches the publisher, and tolerates late connect callbacks.
- Every tick independently attempts pending snapshot drain and scans due weather retries.
- Successful weather retries populate the shared city/date cache and refresh all currently bound devices using that exact country/province/city.
- Weather retry delays are exactly 600, 1800, and 7200 seconds. After the initial failure and three failed retry attempts, `next_attempt_at` is persisted as `NULL`.
- The daily gate runs at most once per server-local date after the configured 00:05 threshold.
- The daily pass refreshes all bound devices so course/todo date-derived cards advance even when no weather location is configured.
- Weather locations are grouped by country/province/city. Existing valid city/date cache entries are reused; missing entries are fetched by the service and failures enter the finite retry schedule.

## Configuration Contract

`main/xiaozhi-server/config.yaml` now declares exactly:

```yaml
overview_mqtt:
  enabled: false
  db: data/xiaoxin_overview.db
  ip_hmac_secret: ""
  retry_tick_seconds: 1
  daily_refresh_hour: 0
  daily_refresh_minute: 5
```

No deployment secret was committed and production publication was not enabled.

## Verification

- Focused runtime/config: `33 passed in 4.25s`.
- Overview service/store/doorbell client regressions: `56 passed in 8.77s`.
- Combined import-smoke, runtime/config, service/store/client verification: `92 passed in 15.37s`.
- `python -m py_compile core/xiaoxin/control_runtime.py tests/xiaoxin/test_control_runtime.py tests/xiaoxin/test_config_contract.py`: exit 0.
- Direct import printed `(600, 1800, 7200)`: exit 0.
- `git diff --check`: no whitespace errors; only existing Git CRLF conversion warnings were emitted.

## Scope Check

- No CRUD, bind/unbind, course/todo trigger wiring was added; that remains Task 7.
- No weather-location API or heartbeat endpoint was added; that remains Task 8.
- No diagnostics/manual-resync API was added; that remains Task 9.
- No store schema, provider implementation, or doorbell client implementation was changed. Review follow-up added only a retry-state read interface and an explicit service publish-session lifecycle reset.
- The pre-existing `.superpowers/sdd/progress.md` working-tree modification is not part of this task and is excluded from the commit.

## Concerns

- The phrase “third failed retry” is implemented literally: the initial failed fetch schedules 600 seconds; retry failures schedule 1800 and 7200 seconds; failure of the third retry persists `next_attempt_at=NULL`. This is the only interpretation that uses all three required delay values.
- The runtime uses the server process local timezone, as required by the local-date rule. Deployments must keep the host timezone configured correctly for the intended campus day boundary.

## Review Follow-up: Daily Commit, City Backoff, And MQTT Session Reset

### Daily marker RED -> GREEN

- RED: a failed daily refresh wrote `_overview_last_daily_refresh_date` before work started, so the next tick on the same date skipped the refresh. Cancellation also left the date committed.
- GREEN: the marker is assigned only in the successful `else` path after `_run_daily_overview_refresh()` returns. Exceptions leave the marker unset; `CancelledError` propagates and leaves it unset.
- Focused evidence: `2 passed` for same-day retry after exception and cancellation without marker commit.

### Shared-city weather RED -> GREEN

- RED: after the first device in a city failed weather fetch, the second device immediately called the provider again. CRUD refreshes also ignored persisted future/exhausted retry state.
- Store added `get_weather_retry_state(country/province/city/date/provider)` as a read-only lookup over the existing retry row; no schema changed.
- Service now checks retry state after cache lookup. Future or exhausted failures produce the canonical unavailable card without another provider request. `manual_resync` remains the explicit override; a new date or city naturally uses a different cache key.
- Runtime still performs the first city request and persists the exact 600/1800/7200 schedule. The remaining devices then project unavailable weather without another external request.
- Retry success writes the shared weather cache, clears the failure state through the existing `put_daily_weather()` upsert, and refreshes every matching bound device, including the first device in the group.
- Focused evidence: store state, exhausted CRUD suppression, and two-device same-city failure/retry tests all pass.

### MQTT publish-session RED -> GREEN

- RED: an old in-flight MID `1` was retired when a new MQTT session reused MID `1`; the legitimate new PUBACK was consumed as stale and the new revision remained pending.
- `OverviewSyncService.reset_publish_session()` clears only in-memory MID mappings, retired MIDs, and early-ACK windows. Persistent pending snapshots and ACK deadlines remain untouched.
- Runtime shutdown order is now: cancel/await Overview loop, stop/join the old doorbell MQTT client, then reset publish-session memory. The next start attaches the publisher only after the old session has been reset.
- Focused evidence: lifecycle order is locked as `stop -> reset`, and a start/stop/start MID-reuse integration test marks the new revision published.

### Review Follow-up Verification

- Runtime/config: `38 passed in 5.47s`.
- Overview service/store/doorbell client: `58 passed in 8.77s`.
- The pre-existing `.superpowers/sdd/progress.md` modification remains excluded.

## Second Review Follow-up: Queued PUBACK Session Isolation

### Deterministic RED

- Doorbell RED queued an old Paho client's PUBACK into a fake running-loop queue, stopped that client, started a new Paho client that reused MID `1`, and queued its PUBACK. The client exposed no source-session generation, so the queued callbacks could not be distinguished.
- Service RED published revision 1 as MID `1`, retained an old ACK callback without executing it, reset and began a new session, then published revision 2 as MID `1`. The service had no `begin_publish_session()` epoch and therefore could not reject the delayed old ACK.

### GREEN

- Every successfully created Paho client receives a monotonically increasing generation before callbacks can run.
- `_on_publish(client, ...)` reads the generation from that exact callback source client and queues `(mid, generation)`. It never reads the mutable current generation when the queued listener eventually executes.
- Publish ACK listeners are session-aware across Doorbell, runtime, service, and tests.
- `OverviewSyncService.begin_publish_session(generation)` resets volatile MID/retired/early-ACK state only when the generation changes. Repeated start with the same generation is a no-op.
- `handle_publish_ack(mid, generation)` returns immediately when the generation does not equal the active publish session, without consuming retired MIDs or touching current MID mappings.
- Runtime start order is now: register listeners, start the Doorbell client, pass its exact active generation to the service, attach the publisher, then start the Overview loop. Thus no Overview publish occurs before the service knows the Paho session epoch.
- Runtime stop still joins/stops the old client before resetting service publish-session memory.

### Result

- The delayed old-generation MID `1` ACK leaves revision 2 pending.
- The new-generation MID `1` ACK then marks revision 2 published.
- Focused runtime + Doorbell + Overview service: `68 passed in 11.00s`.
- Task 4 listener exception/closed-loop isolation and Task 5 early-ACK, ACK deadline, retired-MID, and owner-isolation behavior remain covered.

## Final Review Follow-up: Lazy Reconnect Before MID Mapping

### RED

- Runtime startup used a Paho client whose initial `connect()` failed, leaving no active generation.
- The first Overview refresh called legacy `publish_overview()`, which lazily created a new Paho session and returned MID `1` before the service had begun that generation.
- The queued connect callback then began the new generation and cleared the just-created MID mapping. Generation-correct connect plus PUBACK still left the snapshot pending.
- Doorbell contract REDs also confirmed the absence of an explicit session ensure operation and a publish operation that refuses to rebuild or cross generations.

### GREEN

- Doorbell now exposes `ensure_publish_session() -> generation | None` and `publish_overview_in_session(expected_generation, ...) -> mid | None`.
- `publish_overview_in_session` never creates a client. It checks the expected generation both before and after the Paho publish call; a session change returns `None` and uses the existing publish-failure backoff.
- Legacy `publish_overview()` remains compatible by delegating to ensure + publish-in-session.
- Runtime attaches a private session-aware publisher wrapper to `OverviewSyncService`. Its strict order is: ensure an active session, synchronously call `begin_publish_session(generation)`, then publish only in that generation.
- Therefore early ACK and normal MID mapping always occur after the service epoch is established. A connect callback for the same generation is idempotent and cannot clear mappings.

### Verification

- Startup failure/lazy recovery plus Doorbell session API focused cases: `3 passed`.
- Runtime/config + Doorbell + Overview service/store regressions: `101 passed in 15.24s`.
- Disabled behavior, queued-old-ACK isolation, early ACK, ACK deadline, start-stop-start MID reuse, daily refresh, and weather backoff remain covered.
