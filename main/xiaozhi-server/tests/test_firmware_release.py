from __future__ import annotations

import os

import pytest

from core.firmware_release import (
    FirmwareCheck,
    FirmwareReleaseCatalog,
    FirmwareReleaseError,
)


FIRMWARE_MODEL = "esp32-s3-touch-lcd-1.46"
PARTITION_LAYOUT_ID = "xiaoxin-ota-16m-v1"


def _check(
    device_id: str = "device-1",
    *,
    version: str = "1.1.0",
    channel: str = "stable",
) -> FirmwareCheck:
    return FirmwareCheck(
        device_id=device_id,
        model=FIRMWARE_MODEL,
        board_type=FIRMWARE_MODEL,
        partition_layout_id=PARTITION_LAYOUT_ID,
        current_version=version,
        channel=channel,
    )


def test_published_release_selects_an_immutable_digest_artifact(tmp_path):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"firmware-v1")
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )

    release = catalog.create_release_from_file(
        source,
        model=FIRMWARE_MODEL,
        board_type=FIRMWARE_MODEL,
        partition_layout_id=PARTITION_LAYOUT_ID,
        version="1.2.0",
        channel="stable",
        mandatory=True,
        min_current_version="1.0.0",
        state="published",
    )
    source.write_bytes(b"mutated-after-publish")

    offer = catalog.select_offer(_check())

    assert offer is not None
    assert offer.release_id == release.release_id
    assert offer.version == "1.2.0"
    assert offer.url.endswith(f"/artifacts/{release.sha256}.bin")
    assert offer.size_bytes == len(b"firmware-v1")
    assert catalog.open_artifact(release.sha256).read_bytes() == b"firmware-v1"


def test_draft_release_artifact_is_not_available_for_public_download(tmp_path):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"draft-firmware")
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )

    release = catalog.create_release_from_file(
        source,
        model=FIRMWARE_MODEL,
        version="1.2.0",
    )

    assert catalog.open_artifact(release.sha256) is None


@pytest.mark.parametrize("missing_field", ("model", "board_type", "partition_layout_id"))
def test_published_release_requires_a_complete_compiled_firmware_target(
    tmp_path,
    missing_field,
):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"firmware-target")
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )
    target = {
        "model": FIRMWARE_MODEL,
        "board_type": FIRMWARE_MODEL,
        "partition_layout_id": PARTITION_LAYOUT_ID,
    }
    target[missing_field] = ""

    with pytest.raises(FirmwareReleaseError, match="published release requires"):
        catalog.create_release_from_file(
            source,
            version="1.2.0",
            state="published",
            **target,
        )


def test_canary_release_applies_allowlist_and_deterministic_percentage_rollout(
    tmp_path,
):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"canary-firmware")
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )
    release = catalog.create_release_from_file(
        source,
        release_id="rel-canary",
        model=FIRMWARE_MODEL,
        board_type=FIRMWARE_MODEL,
        partition_layout_id=PARTITION_LAYOUT_ID,
        version="1.2.0",
        channel="canary",
        state="published",
        allowlisted_device_ids=("canary-1",),
    )

    assert catalog.select_offer(_check("canary-1", channel="canary")) is not None
    assert catalog.select_offer(_check("device-2", channel="canary")) is None

    catalog.set_rollout_percentage(release.release_id, 10)

    first_offer = catalog.select_offer(_check("device-2", channel="canary"))
    assert first_offer is not None
    assert first_offer.release_id == release.release_id
    assert catalog.select_offer(_check("device-2", channel="canary")) == first_offer
    assert catalog.select_offer(_check("stable-1", channel="canary")) is None


def test_paused_or_revoked_release_is_immediately_unavailable(tmp_path):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"release-to-stop")
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )
    release = catalog.create_release_from_file(
        source,
        model=FIRMWARE_MODEL,
        board_type=FIRMWARE_MODEL,
        partition_layout_id=PARTITION_LAYOUT_ID,
        version="1.2.0",
        state="published",
    )

    assert catalog.select_offer(_check()) is not None
    assert catalog.open_artifact(release.sha256) is not None

    catalog.set_release_state(release.release_id, "paused")

    assert catalog.select_offer(_check()) is None
    assert catalog.open_artifact(release.sha256) is None

    catalog.set_release_state(release.release_id, "published")
    assert catalog.select_offer(_check()) is not None

    catalog.set_release_state(release.release_id, "revoked")

    assert catalog.select_offer(_check()) is None
    assert catalog.open_artifact(release.sha256) is None
    with pytest.raises(FirmwareReleaseError, match="revoked"):
        catalog.set_release_state(release.release_id, "published")


def test_corrupt_published_artifact_is_never_offered_or_downloaded(tmp_path):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"intact-firmware")
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )
    release = catalog.create_release_from_file(
        source,
        model=FIRMWARE_MODEL,
        board_type=FIRMWARE_MODEL,
        partition_layout_id=PARTITION_LAYOUT_ID,
        version="1.2.0",
        state="published",
    )
    artifact = catalog.open_artifact(release.sha256)
    assert artifact is not None
    os.chmod(artifact, 0o644)
    artifact.write_bytes(b"tampered-firmware")

    assert catalog.select_offer(_check()) is None
    assert catalog.open_artifact(release.sha256) is None


def test_target_version_cannot_be_reused_for_a_different_immutable_artifact(tmp_path):
    first = tmp_path / "first.bin"
    first.write_bytes(b"first-firmware")
    second = tmp_path / "second.bin"
    second.write_bytes(b"second-firmware")
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )
    catalog.create_release_from_file(
        first,
        model=FIRMWARE_MODEL,
        board_type=FIRMWARE_MODEL,
        partition_layout_id=PARTITION_LAYOUT_ID,
        version="1.2.0",
        state="published",
    )

    with pytest.raises(FirmwareReleaseError, match="target version already exists"):
        catalog.create_release_from_file(
            second,
            model=FIRMWARE_MODEL,
            board_type=FIRMWARE_MODEL,
            partition_layout_id=PARTITION_LAYOUT_ID,
            version="1.2.0",
            state="draft",
        )


def test_observation_idempotency_key_deduplicates_an_exact_device_report(tmp_path):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"audited-firmware")
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )
    release = catalog.create_release_from_file(
        source,
        model=FIRMWARE_MODEL,
        board_type=FIRMWARE_MODEL,
        partition_layout_id=PARTITION_LAYOUT_ID,
        version="1.2.0",
        state="published",
    )
    facts = {
        "release_id": release.release_id,
        "device_id": "device-1",
        "event": "device_report",
        "current_version": "1.2.0",
        "target_version": "1.2.0",
        "sha256": release.sha256,
        "slot": "ota_1",
        "result": "committed",
        "reason": "ota_report",
        "idempotency_key": "a" * 64,
    }

    first = catalog.record_observation(**facts)
    duplicate = catalog.record_observation(**facts)

    assert duplicate == first
    assert catalog.list_observations(device_id="device-1") == [first]
    with pytest.raises(FirmwareReleaseError, match="idempotency key conflicts"):
        catalog.record_observation(**(facts | {"result": "failed"}))


def test_catalog_rejects_http_except_when_explicit_development_flag_is_enabled(tmp_path):
    with pytest.raises(FirmwareReleaseError, match="HTTPS"):
        FirmwareReleaseCatalog(
            database_path=tmp_path / "releases.db",
            artifact_dir=tmp_path / "artifacts",
            public_ota_url="http://updates.example/xiaoxin/ota/",
        )

    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="http://updates.example/xiaoxin/ota/",
        allow_insecure_http=True,
    )
    assert catalog.public_ota_url == "http://updates.example/xiaoxin/ota"


@pytest.mark.parametrize(
    "unsafe_version",
    ("1.2", "1.2.dev", "1.2.3.4", "01.2.3", "2147483648.0.0"),
)
def test_catalog_rejects_versions_that_the_firmware_numeric_parser_cannot_read(
    tmp_path,
    unsafe_version,
):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"firmware-version-contract")
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )

    with pytest.raises(FirmwareReleaseError, match="version is invalid"):
        catalog.create_release_from_file(
            source,
            model=FIRMWARE_MODEL,
            board_type=FIRMWARE_MODEL,
            partition_layout_id=PARTITION_LAYOUT_ID,
            version=unsafe_version,
            state="published",
        )

    with pytest.raises(FirmwareReleaseError, match="version is invalid"):
        catalog.create_release_from_file(
            source,
            model=FIRMWARE_MODEL,
            board_type=FIRMWARE_MODEL,
            partition_layout_id=PARTITION_LAYOUT_ID,
            version="1.2.3",
            min_current_version=unsafe_version,
            state="published",
        )
