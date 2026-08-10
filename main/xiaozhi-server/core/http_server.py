import asyncio

from aiohttp import web

from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.firmware_release import FirmwareReleaseCatalog

TAG = __name__


class SimpleHttpServer:
    def __init__(
        self,
        config: dict,
        firmware_release_catalog: FirmwareReleaseCatalog | None = None,
    ):
        self.config = config
        self.logger = setup_logging()
        if firmware_release_catalog is None:
            self.ota_handler = OTAHandler(config)
        else:
            self.ota_handler = OTAHandler(
                config,
                firmware_release_catalog,
            )
        self.vision_handler = VisionHandler(config)

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket")

        if websocket_config and "://" in websocket_config:
            return websocket_config
        return f"ws://{local_ip}:{port}/museum/v1/"

    def build_app(self) -> web.Application:
        app = web.Application()
        ota_routes = [
            web.get("/museum/ota/", self.ota_handler.handle_get),
            web.post("/museum/ota/", self.ota_handler.handle_post),
            web.options("/museum/ota/", self.ota_handler.handle_options),
            web.get(
                "/museum/ota/artifacts/{sha256}.bin",
                self.ota_handler.handle_artifact_download,
            ),
            web.options(
                "/museum/ota/artifacts/{sha256}.bin",
                self.ota_handler.handle_options,
            ),
        ]
        app.add_routes(ota_routes)

        app.add_routes(
            [
                web.get("/mcp/vision/explain", self.vision_handler.handle_get),
                web.post("/mcp/vision/explain", self.vision_handler.handle_post),
                web.options("/mcp/vision/explain", self.vision_handler.handle_options),
            ]
        )

        return app

    async def start(self):
        try:
            server_config = self.config["server"]
            host = server_config.get("ip", "0.0.0.0")
            port = int(server_config.get("http_port", 8003))

            if port:
                app = self.build_app()
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()

                while True:
                    await asyncio.sleep(3600)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP服务启动失败: {e}")
            import traceback

            self.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
            raise
