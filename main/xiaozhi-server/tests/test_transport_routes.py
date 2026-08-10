import asyncio
from types import SimpleNamespace

from aiohttp import web

from core.http_server import SimpleHttpServer
from core.transport_paths import (
    MUSEUM_WEBSOCKET_PATH,
    process_museum_websocket_request,
)


class _Handler:
    async def handle_get(self, request):
        return web.Response()

    async def handle_post(self, request):
        return web.Response()

    async def handle_options(self, request):
        return web.Response()

    async def handle_artifact_download(self, request):
        return web.Response()


def test_http_server_registers_only_museum_ota_routes():
    server = SimpleHttpServer.__new__(SimpleHttpServer)
    server.ota_handler = _Handler()
    server.vision_handler = _Handler()

    routes = {
        (route.method, route.resource.canonical)
        for route in server.build_app().router.routes()
    }

    assert ("GET", "/museum/ota/") in routes
    assert ("POST", "/museum/ota/") in routes
    assert ("GET", "/museum/ota/artifacts/{sha256}.bin") in routes
    assert all("/xiaoxin/" not in path for _, path in routes)
    assert all("/xiaozhi/" not in path for _, path in routes)
    assert all("/activate" not in path for _, path in routes)
    assert all("/download/" not in path for _, path in routes)


def test_websocket_handshake_accepts_only_the_museum_path():
    class Connection:
        def respond(self, status, body):
            return status, body

    connection = Connection()
    accepted = asyncio.run(
        process_museum_websocket_request(
            connection,
            SimpleNamespace(
                path=f"{MUSEUM_WEBSOCKET_PATH}?device-id=device-1",
                headers={"connection": "upgrade"},
            ),
        )
    )
    rejected = asyncio.run(
        process_museum_websocket_request(
            connection,
            SimpleNamespace(
                path="/xiaoxin/v1/?device-id=device-1",
                headers={"connection": "upgrade"},
            ),
        )
    )

    assert accepted is None
    assert rejected == (404, "Not Found\n")
