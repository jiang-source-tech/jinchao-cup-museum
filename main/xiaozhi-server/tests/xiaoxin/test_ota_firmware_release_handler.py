from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from core.api.ota_handler import OTAHandler
from core.http_server import SimpleHttpServer
from core.xiaoxin.firmware_release import FirmwareReleaseCatalog


FIRMWARE_MODEL = "esp32-s3-touch-lcd-1.46"
PARTITION_LAYOUT_ID = "xiaoxin-ota-16m-v1"


def _config() -> dict:
    return {
        "server": {
            "auth": {"enabled": False},
            "auth_key": "test-secret",
            "port": 8000,
            "http_port": 8003,
            "websocket": "ws://updates.example/xiaoxin/v1/",
            "timezone_offset": 8,
        },
        "firmware_cache_ttl": 30,
    }


def _ota_request(
    version: str = "1.1.0",
    *,
    ota_report: dict[str, str] | None = None,
) -> object:
    request = make_mocked_request(
        "POST",
        "/xiaoxin/ota/",
        headers={
            "Device-Id": "device-1",
            "Client-Id": "client-1",
            "Device-Model": FIRMWARE_MODEL,
            "Partition-Layout-Id": PARTITION_LAYOUT_ID,
        },
    )
    payload = {
        "application": {"version": version},
        "board": {"type": FIRMWARE_MODEL},
    }
    if ota_report is not None:
        payload["ota_report"] = ota_report
    request._read_bytes = json.dumps(payload).encode("utf-8")
    return request


def test_ota_handler_extends_legacy_firmware_object_for_release_offer(tmp_path):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"new-firmware")
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
        mandatory=True,
        min_current_version="1.0.0",
        state="published",
    )

    response = asyncio.run(
        OTAHandler(
            _config(),
            firmware_release_catalog=catalog,
        ).handle_post(_ota_request())
    )
    body = json.loads(response.text)

    assert response.status == 200
    assert body["firmware"] == {
        "version": "1.2.0",
        "url": f"https://updates.example/xiaoxin/ota/artifacts/{release.sha256}.bin",
        "schema_version": 1,
        "release_id": release.release_id,
        "sha256": release.sha256,
        "size_bytes": len(b"new-firmware"),
        "model": FIRMWARE_MODEL,
        "board_type": FIRMWARE_MODEL,
        "partition_layout_id": PARTITION_LAYOUT_ID,
        "channel": "stable",
        "mandatory": True,
        "min_current_version": "1.0.0",
    }
    observations = catalog.list_observations(device_id="device-1")
    assert [(item.event, item.result) for item in observations] == [
        ("checked", "received"),
        ("offer", "eligible"),
    ]
    assert observations[0].current_version == "1.1.0"
    assert observations[1].release_id == release.release_id
    assert observations[1].target_version == "1.2.0"
    assert observations[1].sha256 == release.sha256


def test_ota_handler_keeps_legacy_firmware_shape_when_no_release_matches(tmp_path):
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )

    response = asyncio.run(
        OTAHandler(_config(), firmware_release_catalog=catalog).handle_post(_ota_request())
    )
    body = json.loads(response.text)

    assert response.status == 200
    assert body["firmware"] == {"version": "1.1.0", "url": ""}


def test_ota_handler_never_offers_a_numeric_release_to_an_invalid_version_contract(
    tmp_path,
):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"new-firmware")
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        public_ota_url="https://updates.example/xiaoxin/ota/",
    )
    catalog.create_release_from_file(
        source,
        model=FIRMWARE_MODEL,
        board_type=FIRMWARE_MODEL,
        partition_layout_id=PARTITION_LAYOUT_ID,
        version="1.2.0",
        state="published",
    )

    response = asyncio.run(
        OTAHandler(_config(), firmware_release_catalog=catalog).handle_post(
            _ota_request("1.2.dev")
        )
    )
    body = json.loads(response.text)

    assert response.status == 200
    assert body["firmware"] == {"version": "1.2.dev", "url": ""}
    assert [
        (observation.event, observation.result)
        for observation in catalog.list_observations(device_id="device-1")
    ] == [("checked", "received"), ("offer", "rejected")]


def test_ota_handler_records_a_valid_ota_report_once_without_affecting_offer(
    tmp_path,
):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"new-firmware")
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
    report = {
        "release_id": release.release_id,
        "outcome": "committed",
        "running_version": "1.2.0",
        "running_partition": "ota_1",
        "sha256": release.sha256,
    }
    handler = OTAHandler(_config(), firmware_release_catalog=catalog)

    first = asyncio.run(handler.handle_post(_ota_request(ota_report=report)))
    second = asyncio.run(handler.handle_post(_ota_request(ota_report=report)))

    assert json.loads(first.text)["firmware"]["release_id"] == release.release_id
    assert json.loads(second.text)["firmware"]["release_id"] == release.release_id
    reports = [
        observation
        for observation in catalog.list_observations(device_id="device-1")
        if observation.event == "device_report"
    ]
    assert len(reports) == 1
    assert reports[0].release_id == release.release_id
    assert reports[0].result == "committed"
    assert reports[0].current_version == "1.2.0"
    assert reports[0].target_version == "1.2.0"
    assert reports[0].slot == "ota_1"
    assert reports[0].sha256 == release.sha256


def test_ota_handler_rejects_invalid_ota_report_without_changing_offer(tmp_path):
    source = tmp_path / "xiaoxin.bin"
    source.write_bytes(b"new-firmware")
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
    report = {
        "release_id": release.release_id,
        "outcome": "committed",
        "running_version": "1.2.0",
        "running_partition": "factory",
        "sha256": release.sha256,
    }

    response = asyncio.run(
        OTAHandler(_config(), firmware_release_catalog=catalog).handle_post(
            _ota_request(ota_report=report)
        )
    )
    body = json.loads(response.text)

    assert body["firmware"]["release_id"] == release.release_id
    assert not [
        observation
        for observation in catalog.list_observations(device_id="device-1")
        if observation.event == "device_report"
    ]


def test_http_server_streams_only_catalog_digest_artifacts(tmp_path):
    async def scenario():
        source = tmp_path / "xiaoxin.bin"
        source.write_bytes(b"digest-artifact")
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
        config = _config()
        server = SimpleHttpServer(config, firmware_release_catalog=catalog)
        test_server = TestServer(server.build_app())
        client = TestClient(test_server)
        await client.start_server()
        try:
            response = await client.get(
                f"/xiaoxin/ota/artifacts/{release.sha256}.bin"
            )
            return response.status, await response.read()
        finally:
            await client.close()

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body == b"digest-artifact"


def test_http_server_stops_serving_an_artifact_when_its_release_is_paused(tmp_path):
    async def scenario():
        source = tmp_path / "xiaoxin.bin"
        source.write_bytes(b"paused-artifact")
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
        catalog.set_release_state(release.release_id, "paused")
        server = SimpleHttpServer(_config(), firmware_release_catalog=catalog)
        test_server = TestServer(server.build_app())
        client = TestClient(test_server)
        await client.start_server()
        try:
            response = await client.get(
                f"/xiaoxin/ota/artifacts/{release.sha256}.bin"
            )
            return response.status
        finally:
            await client.close()

    assert asyncio.run(scenario()) == 404


def test_ota_handler_uses_legacy_filename_fallback_only_when_enabled(tmp_path):
    config = _config()
    config["server"]["vision_explain"] = (
        "https://updates.example/mcp/vision/explain"
    )
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        legacy_filename_fallback=True,
    )
    legacy_bin_dir = tmp_path / "legacy-bin"
    legacy_bin_dir.mkdir()
    (legacy_bin_dir / f"{FIRMWARE_MODEL}_1.2.0.bin").write_bytes(b"legacy-firmware")
    handler = OTAHandler(
        _config() | {"server": config["server"]},
        firmware_release_catalog=catalog,
    )
    handler.bin_dir = str(legacy_bin_dir)

    response = asyncio.run(handler.handle_post(_ota_request()))
    body = json.loads(response.text)

    assert body["firmware"] == {
        "version": "1.2.0",
        "url": f"https://updates.example/xiaoxin/ota/download/{FIRMWARE_MODEL}_1.2.0.bin",
    }


def test_legacy_filename_fallback_rejects_versions_outside_the_firmware_contract(
    tmp_path,
):
    config = _config()
    config["server"]["vision_explain"] = (
        "https://updates.example/mcp/vision/explain"
    )
    catalog = FirmwareReleaseCatalog(
        database_path=tmp_path / "releases.db",
        artifact_dir=tmp_path / "artifacts",
        legacy_filename_fallback=True,
    )
    legacy_bin_dir = tmp_path / "legacy-bin"
    legacy_bin_dir.mkdir()
    (legacy_bin_dir / f"{FIRMWARE_MODEL}_1.2.dev.bin").write_bytes(
        b"legacy-firmware"
    )
    handler = OTAHandler(
        _config() | {"server": config["server"]},
        firmware_release_catalog=catalog,
    )
    handler.bin_dir = str(legacy_bin_dir)

    response = asyncio.run(handler.handle_post(_ota_request()))
    body = json.loads(response.text)

    assert body["firmware"] == {"version": "1.1.0", "url": ""}
