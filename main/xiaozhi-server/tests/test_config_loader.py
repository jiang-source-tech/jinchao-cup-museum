from copy import deepcopy
from pathlib import Path

import pytest

from config.config_loader import (
    enforce_museum_data_boundary,
    read_config,
    reject_legacy_config_sections,
)
from core.api.ota_handler import OTAHandler
from core.websocket_server import WebSocketServer


def test_tracked_config_constructs_transports_and_rejects_legacy_data(tmp_path):
    config_path = Path(__file__).parents[1] / "config.example.yaml"
    config = read_config(config_path)

    websocket_server = WebSocketServer(config)
    ota_handler = OTAHandler(config)

    assert websocket_server.auth_enable is False
    assert websocket_server.auth is None
    assert ota_handler.auth_enable is False
    assert ota_handler.auth is None

    authenticated_config = deepcopy(config)
    authenticated_config["server"]["auth"]["enabled"] = True
    for factory in (WebSocketServer, OTAHandler):
        with pytest.raises(ValueError, match="server.auth_key"):
            factory(authenticated_config)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    archive_dir = data_dir / "archive"
    archive_dir.mkdir()
    legacy_database = archive_dir / "xiaoxin_control.db"
    legacy_database.touch()
    (data_dir / "museum.db").touch()

    with pytest.raises(RuntimeError, match="拒绝启动") as error:
        enforce_museum_data_boundary(tmp_path)

    assert "xiaoxin_control.db" in str(error.value)
    assert "museum.db" not in str(error.value)


def test_legacy_business_config_sections_are_rejected(tmp_path):
    config_path = tmp_path / "legacy-business.yaml"
    config_path.write_text(
        "xiaoxin_control: {}\nxiaoxin_runtime: {}\nvoiceprint: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        reject_legacy_config_sections(read_config(config_path), config_path)

    for section in ("xiaoxin_control", "xiaoxin_runtime", "voiceprint"):
        assert section in str(error.value)


def test_compose_uses_an_isolated_museum_data_mount():
    compose_path = Path(__file__).parents[1] / "docker-compose.yml"
    dockerignore_path = Path(__file__).parents[3] / ".dockerignore"
    compose = read_config(compose_path)

    assert compose["name"] == "jinchao-museum"
    service = compose["services"]["museum-server"]
    assert service["image"] == "jinchao-museum-server:${MUSEUM_IMAGE_TAG:-local}"
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": "${MUSEUM_DATA_DIR:-./museum-data}",
            "target": "/opt/jinchao-museum-server/data",
        }
    ]
    dockerignore = dockerignore_path.read_text(encoding="utf-8").splitlines()
    for excluded_path in (
        "main/xiaozhi-server/config.yaml",
        "main/xiaozhi-server/data/",
        "main/xiaozhi-server/mcp_server_settings.json",
        "main/xiaozhi-server/museum-data/",
    ):
        assert excluded_path in dockerignore
