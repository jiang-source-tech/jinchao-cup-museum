from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.firmware_release import FirmwareCheck, FirmwareReleaseCatalog


FIRMWARE_MODEL = "esp32-s3-touch-lcd-1.46"
PARTITION_LAYOUT_ID = "xiaoxin-ota-16m-v1"


def test_publish_firmware_cli_creates_an_offer_when_explicitly_published(tmp_path):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"cli-firmware")
    database_path = tmp_path / "releases.db"
    artifact_dir = tmp_path / "artifacts"
    server_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/publish_firmware.py",
            "--source",
            str(source),
            "--database",
            str(database_path),
            "--artifact-dir",
            str(artifact_dir),
            "--public-ota-url",
            "https://updates.example/xiaoxin/ota/",
            "--model",
            FIRMWARE_MODEL,
            "--version",
            "1.2.0",
            "--board-type",
            FIRMWARE_MODEL,
            "--partition-layout-id",
            PARTITION_LAYOUT_ID,
            "--publish",
        ],
        cwd=server_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    published = json.loads(result.stdout)
    assert published["state"] == "published"
    catalog = FirmwareReleaseCatalog(
        database_path=database_path,
        artifact_dir=artifact_dir,
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )
    offer = catalog.select_offer(
        FirmwareCheck(
            device_id="device-1",
            model=FIRMWARE_MODEL,
            board_type=FIRMWARE_MODEL,
            partition_layout_id=PARTITION_LAYOUT_ID,
            current_version="1.1.0",
        )
    )
    assert offer is not None
    assert offer.release_id == published["release_id"]


def test_publish_firmware_cli_rejects_a_published_release_without_target_fields(
    tmp_path,
):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"cli-firmware")
    server_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/publish_firmware.py",
            "--source",
            str(source),
            "--database",
            str(tmp_path / "releases.db"),
            "--artifact-dir",
            str(tmp_path / "artifacts"),
            "--public-ota-url",
            "https://updates.example/xiaoxin/ota/",
            "--model",
            FIRMWARE_MODEL,
            "--version",
            "1.2.0",
            "--publish",
        ],
        cwd=server_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--board-type and --partition-layout-id are required with --publish" in result.stderr


def test_publish_firmware_cli_can_pause_an_existing_release(tmp_path):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"cli-firmware")
    database_path = tmp_path / "releases.db"
    artifact_dir = tmp_path / "artifacts"
    server_root = Path(__file__).resolve().parents[1]

    created = subprocess.run(
        [
            sys.executable,
            "scripts/publish_firmware.py",
            "--source",
            str(source),
            "--database",
            str(database_path),
            "--artifact-dir",
            str(artifact_dir),
            "--public-ota-url",
            "https://updates.example/xiaoxin/ota/",
            "--model",
            FIRMWARE_MODEL,
            "--version",
            "1.2.0",
            "--board-type",
            FIRMWARE_MODEL,
            "--partition-layout-id",
            PARTITION_LAYOUT_ID,
            "--publish",
        ],
        cwd=server_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    release_id = json.loads(created.stdout)["release_id"]

    paused = subprocess.run(
        [
            sys.executable,
            "scripts/publish_firmware.py",
            "--operation",
            "set-state",
            "--release-id",
            release_id,
            "--state",
            "paused",
            "--database",
            str(database_path),
            "--artifact-dir",
            str(artifact_dir),
        ],
        cwd=server_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert paused.returncode == 0, paused.stderr
    assert json.loads(paused.stdout)["state"] == "paused"
    catalog = FirmwareReleaseCatalog(
        database_path=database_path,
        artifact_dir=artifact_dir,
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )
    assert (
        catalog.select_offer(
            FirmwareCheck(
                device_id="device-1",
                model=FIRMWARE_MODEL,
                board_type=FIRMWARE_MODEL,
                partition_layout_id=PARTITION_LAYOUT_ID,
                current_version="1.1.0",
            )
        )
        is None
    )


def test_publish_firmware_cli_lists_credential_free_release_observations(tmp_path):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"cli-firmware")
    database_path = tmp_path / "releases.db"
    artifact_dir = tmp_path / "artifacts"
    catalog = FirmwareReleaseCatalog(
        database_path=database_path,
        artifact_dir=artifact_dir,
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
    catalog.record_observation(
        release_id=release.release_id,
        device_id="device-1",
        event="health_confirmed",
        current_version="1.1.0",
        target_version="1.2.0",
        sha256=release.sha256,
        slot="ota_1",
        result="committed",
        reason="health_gate_passed",
    )
    server_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/publish_firmware.py",
            "--operation",
            "observations",
            "--release-id",
            release.release_id,
            "--database",
            str(database_path),
            "--artifact-dir",
            str(artifact_dir),
        ],
        cwd=server_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    observations = json.loads(result.stdout)
    assert len(observations) == 1
    assert {key: value for key, value in observations[0].items() if key != "observed_at"} == {
        "current_version": "1.1.0",
        "device_id": "device-1",
        "event": "health_confirmed",
        "reason": "health_gate_passed",
        "release_id": release.release_id,
        "result": "committed",
        "sha256": release.sha256,
        "slot": "ota_1",
        "target_version": "1.2.0",
    }
    assert observations[0]["observed_at"]
    assert "password" not in observations[0]
