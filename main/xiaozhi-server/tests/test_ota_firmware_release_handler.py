from core.api.ota_handler import OTAHandler


def _config(websocket: str) -> dict:
    return {
        "server": {
            "auth": {"enabled": False},
            "port": 8000,
            "http_port": 8003,
            "websocket": websocket,
            "timezone_offset": 8,
        },
    }


def test_ota_handler_defaults_to_museum_websocket_route():
    placeholder = "ws://你的ip或者域名:端口号/museum/v1/"

    assert OTAHandler(_config(placeholder))._get_websocket_url(
        "192.0.2.10", 8000
    ) == "ws://192.0.2.10:8000/museum/v1/"
    assert OTAHandler(_config(""))._get_websocket_url(
        "192.0.2.10", 8000
    ) == "ws://192.0.2.10:8000/museum/v1/"
