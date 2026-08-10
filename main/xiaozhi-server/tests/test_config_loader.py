from copy import deepcopy
from pathlib import Path

import pytest

from config.config_loader import read_config, warn_if_legacy_data_present
from core.api.ota_handler import OTAHandler
from core.websocket_server import WebSocketServer


def test_tracked_config_constructs_transports_and_warns_about_legacy_data(
    tmp_path,
    capsys,
):
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
    legacy_database = data_dir / "xiaoxin_control.db"
    legacy_database.touch()
    (data_dir / "museum.db").touch()

    found = warn_if_legacy_data_present(tmp_path)

    assert found == (legacy_database,)
    warning = capsys.readouterr().err
    assert "CRITICAL" in warning
    assert "xiaoxin_control.db" in warning
    assert "museum.db" not in warning
