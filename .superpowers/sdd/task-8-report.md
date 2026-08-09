## Task 8 Report - Device Public-IP Observation And Weather Location APIs

### Status

Implemented Task 8 server behavior only: device credential verification, trusted public-IP observation from OTA and heartbeat requests, authenticated device location heartbeat, and authenticated miniprogram weather-location GET/PATCH APIs. Task 9 diagnostics/manual-resync behavior was not added.

### Implemented Contracts

- `DoorbellCredentialStore.verify_password(username, device_id, password)` queries by username, device ID, and active status, then compares the stored opaque password with `secrets.compare_digest()`.
- A password is unusable for a different device ID, and disabled credentials are rejected.
- OTA and heartbeat IP extraction trusts `X-Forwarded-For`/`X-Real-IP` only when the direct peer belongs to `xiaoxin_control.overview_mqtt.trusted_proxy_cidrs`.
- Trusted XFF chains are processed from the direct-proxy side toward the client; trusted proxy hops are stripped and the nearest non-trusted client address is selected.
- RFC1918/CGNAT/ULA private addresses, loopback, link-local, multicast, reserved, unspecified, and invalid addresses are not observed.
- OTA calls `observe_device_ip(device_id, public_ip, "ota")` without exposing the IP in its response. Observation/provider failures are logged and do not change the OTA response.
- `POST /api/xiaoxin/device/location-heartbeat` authenticates exactly from `Device-Id`, `Device-Username`, and `Authorization: Bearer <opaque password>`. It does not read an IP or tenant from the request body.
- `GET/PATCH /api/miniprogram/weather-location` requires a valid miniprogram session and an owned bound device. Caller-supplied foreign device IDs are rejected.
- Manual PATCH requires validated non-empty province and city values, stores manual mode, fetches/caches weather through the existing Overview service, and refreshes the device Overview.
- Automatic PATCH restores the latest persisted automatic candidate through the existing store semantics and refreshes the device Overview. If no candidate has yet been observed, subsequent authenticated OTA/heartbeat observation supplies it.
- Weather-location responses use the miniprogram contract's `weatherLocation` whitelist and do not expose raw IPs, IP HMACs, MQTT credentials, openids, or provider error text.

### TDD Evidence

The required families were executed in order.

1. Credential RED: two tests failed with `AttributeError` because `verify_password` did not exist. GREEN: focused credential tests passed, followed by the full credential file (`8 passed`).
2. Trusted IP/OTA RED: trusted-XFF, non-blocking observation failure, and manual-mode preservation tests failed because OTA made no observation call. GREEN: all five new OTA observation tests passed.
3. Heartbeat RED: three tests failed with `AttributeError` because `handle_location_heartbeat` did not exist. GREEN: all three heartbeat tests passed.
4. Student GET/PATCH RED: four tests failed with `AttributeError` because the weather-location handlers did not exist. GREEN: all four weather-location tests passed.

Final required focused result:

```text
10 passed, 133 deselected in 3.09s
```

### Verification

```text
python -m pytest tests/xiaoxin/test_doorbell_credentials.py tests/xiaoxin/test_ota_activation_handler.py tests/xiaoxin/test_control_handler.py -q
143 passed in 31.17s

python -m pytest tests/xiaoxin/test_overview_service.py tests/xiaoxin/test_control_runtime.py tests/xiaoxin/test_overview_store.py -q
75 passed in 14.26s

python -m compileall -q core/api/ota_handler.py core/api/xiaoxin_control_handler.py core/xiaoxin/doorbell_credentials.py tests/xiaoxin/test_ota_activation_handler.py tests/xiaoxin/test_control_handler.py tests/xiaoxin/test_doorbell_credentials.py
exit 0

python -c "from core.api.ota_handler import OTAHandler; from core.api.xiaoxin_control_handler import XiaoxinControlHandler; from core.xiaoxin.doorbell_credentials import DoorbellCredentialStore; print('imports-ok')"
imports-ok

git diff --check
no whitespace errors; Git emitted only the repository's CRLF conversion warnings
```

### Scope And Concerns

- The trusted proxy configuration interface is `xiaoxin_control.overview_mqtt.trusted_proxy_cidrs`, accepting a string or list of CIDR/single-IP values. Any invalid entry emits a fixed diagnostic and disables the entire trusted-proxy set for that request.
- The task brief explicitly uses documentation address `203.0.113.10` as the public test peer. Python's `ipaddress` does not mark it `is_reserved`, so the implementation accepts it while still rejecting the explicit private/reserved classes listed above. Production deployments should naturally observe globally routed client addresses.
- The existing overview store deliberately keeps the manual city as a temporary fallback when switching to automatic mode before any automatic candidate exists; the next authenticated OTA/heartbeat observation replaces it under automatic mode.
- IP extraction is intentionally duplicated privately in the two handlers because Task 8 constrained production edits to the listed handler files. A later task may extract it to a shared utility without changing behavior.
- The pre-existing `.superpowers/sdd/progress.md` modification was not edited or staged.
- No Task 9 diagnostics fields or manual Overview resync endpoint were implemented.
- Confidence: high.

## Review Follow-up - Five Important Findings

### OTA Observation Authentication And Ordering

- RED proved that unauthenticated OTA, wrong device passwords, missing client IDs, and blank client IDs could produce IP-observation side effects.
- OTA now validates the required client ID first and only observes after `Device-Username` plus `Authorization: Bearer <active opaque device password>` passes `DoorbellCredentialStore.verify_password()` for the same `Device-Id`.
- Initial OTA/activation responses remain compatible: requests without an existing credential still receive the normal response but skip location observation. Observation exceptions remain isolated from successful OTA responses.

### Trusted Proxy Configuration And Header Ambiguity

- `config.yaml` now explicitly declares `trusted_proxy_cidrs: []`; the exact configuration contract test locks the default.
- Invalid CIDRs log only `invalid trusted proxy CIDR ignored` under `xiaoxin.network`. The invalid value, forwarded header, IP address, and credentials are never logged by this diagnostic.
- A mixed valid/invalid list fails closed as a whole; it cannot partially enable proxy trust.
- XFF and X-Real-IP are read through `headers.getall()`. More than one field instance is ambiguous and rejected, while one field may still contain a normal comma-separated proxy chain.

### Global-Unicast Filtering

- The handler filter is now an allowlist based on `ipaddress.is_global`, with explicit rejection of multicast, unspecified, reserved, and IPv6 site-local addresses.
- The only non-global exception is the brief's exact documentation peer `203.0.113.10`.
- Tests reject `198.18.0.1`, `192.0.0.1`, and `2001:db8::1`, while accepting real IPv4 and IPv6 global-unicast examples.

### Manual City Validation Before Persistence

- `OpenMeteoWeatherProvider.validate_city(province, city)` performs only strict CN geocoding and reuses the existing province-match rules. It never fetches a forecast.
- Manual PATCH validates the city before `set_manual_location()`. Invalid/cross-province locations return a sanitized 400 with no location write, no snapshot, and no refresh.
- Geocoding timeouts and transient external failures return a sanitized retryable 503 without exposing provider bodies.
- After successful geocoding, the normal weather fetch/cache/Overview refresh runs. A transient forecast failure therefore leaves a valid manual location accepted and produces the existing unavailable-weather projection rather than misclassifying the city.

### Review Follow-up Verification

```text
API credential/OTA/handler group: 157 passed in 34.68s
Config/provider/service/runtime/store/broker/doorbell regressions: 118 passed in 15.94s
Compileall: exit 0
Import smoke: imports-ok True
git diff --check: no whitespace errors; CRLF conversion warnings only
```

The pre-existing `.superpowers/sdd/progress.md` modification remains excluded, and Task 9 remains outside this commit.

## Second Review Follow-up - Logging, Ownership TOCTOU, And Error Classes

### Credential-Safe OTA Logging

- RED captured the full request-header representation in OTA debug logs, including the device MQTT username, bearer password, Authorization field, and unrelated complete headers.
- OTA now logs only fixed safe request metadata (`method` and `path`) before parsing. Complete headers and raw request bodies are no longer logged.
- The existing device ID and client ID operational messages remain, but no message or logger extra contains the opaque credential, Authorization value, full header collection, or observed IP/header combination.

### Manual-Location Ownership TOCTOU

- Deterministic REDs paused geocoding and forecast awaits while device ownership changed from student A to student B. The old handler returned 200 and could leave A's manual city on B's device.
- `OverviewSyncService.set_manual_location_for_user()` now serializes the device operation, re-reads the bound owner after geocoding, saves the prior location, writes the candidate manual location, and uses the existing locked refresh path.
- If ownership changes during weather refresh, the existing final-owner check discards the snapshot before publish. The service then restores the exact prior location (or deletes the newly created row) and returns an ownership conflict to the handler.
- The handler returns sanitized 404 `device not found` for both pre-write and mid-refresh ownership conflicts. The normal unchanged-owner path remains 200.
- `XiaoxinOverviewStore.restore_location()` is covered for exact replacement and deletion rollback.

### Location Validation Versus Provider Failure

- Added `LocationValidationError` as a `ProviderDataError` subclass so existing provider/daily callers remain compatible.
- Strict CN no-match and province-mismatch cases raise `LocationValidationError` and map to sanitized 400 `invalid weather location`.
- Malformed geocoding JSON/schema remains `ProviderDataError`; malformed provider data, timeout, and network failures map to sanitized retryable 503 and never persist a manual location.

### Cross-Date Regression Test Repair

- The fixed-date same-city retry test began failing after the host date crossed from July 10 to July 11 because its store used the real clock and correctly considered the July 10 cache expired.
- The test now injects its existing fixed `now` into the Overview store clock. Production cache expiry behavior is unchanged.

### Second Review Verification

```text
Credential/OTA/control API group: 161 passed in 63.17s
Provider/config/service/runtime/store/broker/doorbell regressions: 121 passed in 42.57s
Compileall: exit 0
Import smoke: imports-ok True True
git diff --check: no whitespace errors; CRLF conversion warnings only
```

The pre-existing `.superpowers/sdd/progress.md` modification remains excluded. Task 9 was not implemented.

## Third Review Follow-up - Unified Location Mutations And CAS Rollback

### Deterministic Concurrency REDs

- Automatic PATCH switched a manual row to automatic, blocked in forecast, then ownership moved from A to B. The old path returned 200 and left A's automatic strategy on B's device.
- Manual PATCH blocked in forecast while authenticated IP observation resolved a newer automatic candidate. The unconditional rollback restored an old row and erased the newer candidate.

### Versioned Store Contract

- `device_weather_locations` now has `location_revision INTEGER NOT NULL DEFAULT 1`.
- Existing databases are migrated through `PRAGMA table_info` plus `ALTER TABLE`; existing rows start at revision 1.
- Manual writes, automatic current/candidate writes, and mode switches increment the revision monotonically. Store methods return the resulting revision in the location row.
- `restore_location()` now requires `expected_revision`. Restore or delete executes only when the current row still has that exact revision. A successful restore increments the revision again; a stale rollback returns false and preserves the newer write.

### Single Device-Location Mutation Lock

- Manual owner-aware changes, automatic owner-aware mode switches, and `observe_device_ip()` location writes now all execute through `OverviewSyncService._serialize_device(device_id)`.
- IP provider lookup remains outside the lock. Before writing, observation rechecks that the device is currently bound; the store transaction re-reads the current mode before applying current/candidate fields.
- Manual and automatic handlers call owner-aware service methods. They recheck ownership inside the device lock, use `_refresh_device_locked()` without re-entering the lock, and conditionally roll back on owner mismatch.
- Observation uses the same locked refresh path and conditionally rolls back if ownership changes during refresh.
- The automatic transfer race now returns sanitized 404 and restores the prior manual strategy without publishing A data. The manual/observation race preserves the newer Jiangsu/Nanjing candidate and publishes only the current B-owned snapshot.

### Third Review Verification

```text
Credential/OTA/control API group: 163 passed in 35.23s
Store/service/provider/runtime/config/doorbell/broker regressions: 124 passed in 16.57s
Compileall: exit 0
Import smoke: imports-ok True True
git diff --check: no whitespace errors; CRLF conversion warnings only
```

Production search confirms that location mutation methods are called only inside `OverviewSyncService`; the HTTP handler no longer writes location state directly. The pre-existing `.superpowers/sdd/progress.md` modification remains excluded, and Task 9 remains out of scope.
