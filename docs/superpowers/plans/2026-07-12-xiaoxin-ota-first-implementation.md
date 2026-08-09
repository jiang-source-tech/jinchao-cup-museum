# Xiaoxin OTA-First Implementation Plan

## Goal

After one controlled USB bootstrap migration, a Wi-Fi-connected Xiaoxin device automatically discovers an eligible newer firmware, downloads it into the inactive OTA slot, verifies it, reboots, proves basic health, and either commits or rolls back. Operators publish immutable release artifacts; they do not rename files in place or manually flash subsequent versions.

## Non-negotiable constraints

- The currently deployed factory-only partition layout cannot be migrated remotely. Existing devices need one USB bootstrap; do not promise otherwise.
- Preserve `nvs` at `0x9000` during bootstrap. Do not use `erase_flash`.
- Preserve the legacy OTA JSON keys `firmware.version` and `firmware.url`; old firmware must continue to parse responses.
- `docs/requirements/requirements.yaml` is user-owned dirty work and must not be edited by implementation slices.
- The target board is Waveshare ESP32-S3 Touch LCD 1.46. The new layout must retain the `assets` partition label used by the firmware.

## Public contracts

### Server offer

`POST /xiaoxin/ota/` keeps the legacy shape and, when a release is eligible, adds these fields under `firmware`:

```json
{
  "version": "1.2.3",
  "url": "https://.../xiaoxin/ota/artifacts/<sha256>.bin",
  "schema_version": 1,
  "release_id": "rel_...",
  "sha256": "<64 lowercase hex>",
  "size_bytes": 5684960,
  "model": "...",
  "board_type": "waveshare-esp32-s3-touch-lcd-1.46",
  "partition_layout_id": "xiaoxin-ota-16m-v1",
  "channel": "canary",
  "mandatory": false,
  "min_current_version": "..."
}
```

No eligible release means the old behavior: current version and an empty URL.

### Device lifecycle

```text
CHECK -> ELIGIBLE | DEFERRED -> DOWNLOAD -> VERIFY -> SET_BOOT -> REBOOT
  -> HEALTH_PENDING -> COMMITTED | ROLLBACK
```

Device validation rejects an empty or malformed SHA-256, wrong model/board/layout, unexpected size, downgrade, unavailable OTA slot, and failed digest. A pending app becomes valid only after its local health gate; it must not be committed merely because an OTA HTTP check happened.

### Rollout

Publish draft -> canary allowlist -> bounded percentage -> stable. A paused or revoked release cannot be offered. Automatic installation happens only while device activity and power/network policy permit it; the first implementation may retain startup checks but must expose reasons for deferral and failure.

## Ordered implementation slices

### 1. Firmware bootstrap partition layout

Owner: firmware partition slice.

- Add `partitions/xiaoxin-ota-16m.csv`: NVS 24 KiB, PHY, `otadata`, `ota_0` 6 MiB, `ota_1` 6 MiB, `assets` 3.875 MiB.
- Point tracked target configuration at the new layout and add a static test that rejects a factory-only target or an assets-less OTA table.
- Adjust reset semantics so no code assumes a `factory` partition exists.
- Build-time gate: current app must fit both slots with recorded headroom; current app is 5,684,960 bytes, leaving 606,496 bytes per 6 MiB slot.

Acceptance: generated partition table and flasher args show `ota_0`, `ota_1`, `otadata`, and `assets`; no existing NVS erase is required by the bootstrap flashing procedure.

### 2. Server release catalog and immutable artifacts

Owner: server release slice.

- Add a deep `FirmwareReleaseCatalog` module behind `select_offer`, `open_artifact`, and `record_observation`.
- Import a bin atomically, calculate SHA-256/size, store it by digest, and create a release with model, board, layout, channel, and rollout metadata.
- Let `OTAHandler` use the catalog first, retaining an explicitly configured legacy filename fallback during migration.
- Provide a CLI publisher rather than an unauthenticated HTTP upload endpoint.
- Add catalog and handler integration tests, including legacy compatibility, immutable storage, mismatch rejection, pause/revoke, and deterministic canary selection.

Acceptance: a published canary artifact yields a compatible extended offer and digest URL; an unpublished, paused, revoked, incompatible, or corrupt artifact does not.

### 3. Firmware offer parsing and artifact verification

Owner: firmware update slice.

- Parse optional extended metadata while accepting legacy offers only in explicitly configured compatibility mode.
- Stream SHA-256 during download and verify declared size, image descriptor, model/board/layout before setting the boot partition.
- Return precise failure reasons; do not call `esp_ota_set_boot_partition` after any validation failure.
- Make HTTPS the release configuration; insecure HTTP remains a deliberate development-only compatibility setting, not a production default.

Acceptance: fake/host tests cover valid artifact, wrong hash, wrong size, wrong board/layout, downgrade, missing slot, and failed write.

### 4. Safe automatic installation and rollback health gate

Owner: firmware update slice after slice 3.

- Separate update eligibility from installation.
- Gate automatic installation on idle audio/TTS state, network availability, and target-board power policy; persist deferred reason and retry time.
- Move `MarkCurrentVersionValid` behind a local health window. On health failure, call rollback and report the release outcome.
- Add an eventual periodic check with jitter for devices that never reboot.

Acceptance: no unsafe-time install; success transitions to committed only after health; failure returns to last valid slot.

### 5. Hardware flight and production rollout

Owner: coordinator plus real device.

- Back up device identity/configuration and USB-flash the bootstrap image, partition table, `ota_data_initial.bin`, app to `ota_0`, and assets at `0xc20000`; never use stale manual offsets or `erase_flash`.
- Publish a newer canary release and prove `ota_0 -> ota_1`; run a bad-hash or health-failure case and prove rollback; publish another release to prove `ota_1 -> ota_0`.
- Only then expand rollout and run notification/TTS/Overview/two-device isolation regression on the OTA-updated build.

Acceptance: every device outcome is recorded by release ID, device ID, old/new version, slot, hash, timestamps, logs, and captured UI evidence.

## Verification order

1. Focused server catalog/handler tests.
2. Focused firmware static and host-model tests.
3. Firmware full build using the active target configuration and partition-table validation.
4. Server full Xiaoxin test suite.
5. Cross-repository review.
6. Physical USB bootstrap once, then real Wi-Fi OTA success and rollback trials.
