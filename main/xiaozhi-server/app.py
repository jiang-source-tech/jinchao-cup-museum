import asyncio
import signal
import sys
import uuid

from aioconsole import ainput

from config.logger import setup_logging
from config.settings import load_config
from core.http_server import SimpleHttpServer
from core.utils.gc_manager import get_gc_manager
from core.utils.util import (
    check_ffmpeg_installed,
    get_local_ip,
    validate_mcp_endpoint,
)
from core.websocket_server import WebSocketServer
from core.xiaoxin.control_runtime import create_xiaoxin_control_runtime

TAG = __name__
logger = setup_logging()


async def wait_for_exit() -> None:
    """
    阻塞直到收到 Ctrl-C / SIGTERM。
    - Unix: 使用 add_signal_handler
    - Windows: 依赖 KeyboardInterrupt
    """
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
    else:
        try:
            await asyncio.Future()
        except KeyboardInterrupt:
            pass


async def monitor_stdin():
    """监控标准输入，消费回车键"""
    while True:
        await ainput()


async def main():
    check_ffmpeg_installed()
    config = load_config()
    xiaoxin_runtime = None
    stdin_task = None
    ws_task = None
    ota_task = None
    gc_manager = None

    # auth_key用于jwt认证，比如视觉分析接口的jwt认证、ota接口的token生成与websocket认证
    auth_key = config["server"].get("auth_key", "")
    if not auth_key or len(auth_key) == 0 or "你" in auth_key:
        auth_key = str(uuid.uuid4().hex)

    config["server"]["auth_key"] = auth_key

    try:
        stdin_task = asyncio.create_task(monitor_stdin())

        gc_manager = get_gc_manager(interval_seconds=300)
        await gc_manager.start()

        xiaoxin_runtime = create_xiaoxin_control_runtime(config)
        await xiaoxin_runtime.start()

        ws_server = WebSocketServer(config, xiaoxin_runtime=xiaoxin_runtime)
        ws_task = asyncio.create_task(ws_server.start())

        ota_server = SimpleHttpServer(config, xiaoxin_runtime=xiaoxin_runtime)
        ota_task = asyncio.create_task(ota_server.start())

        port = int(config["server"].get("http_port", 8003))
        logger.bind(tag=TAG).info(
            "OTA接口是\t\thttp://{}:{}/xiaoxin/ota/",
            get_local_ip(),
            port,
        )
        logger.bind(tag=TAG).info(
            "视觉分析接口是\thttp://{}:{}/mcp/vision/explain",
            get_local_ip(),
            port,
        )
        mcp_endpoint = config.get("mcp_endpoint", None)
        if mcp_endpoint is not None and "你" not in mcp_endpoint:
            if validate_mcp_endpoint(mcp_endpoint):
                logger.bind(tag=TAG).info("mcp接入点是\t{}", mcp_endpoint)
                config["mcp_endpoint"] = mcp_endpoint.replace("/mcp/", "/call/")
            else:
                logger.bind(tag=TAG).error("mcp接入点不符合规范")
                config["mcp_endpoint"] = "你的接入点 websocket地址"

        websocket_port = 8000
        server_config = config.get("server", {})
        if isinstance(server_config, dict):
            websocket_port = int(server_config.get("port", 8000))

        logger.bind(tag=TAG).info(
            "Websocket地址是\tws://{}:{}/xiaoxin/v1/",
            get_local_ip(),
            websocket_port,
        )
        logger.bind(tag=TAG).info(
            "=======上面的地址是websocket协议地址，请勿用浏览器访问======="
        )
        logger.bind(tag=TAG).info(
            "如想测试websocket请启动digital-human模块，打开浏览器交互测试"
        )
        logger.bind(tag=TAG).info(
            "=============================================================\n"
        )

        await wait_for_exit()
    except asyncio.CancelledError:
        print("任务被取消，清理资源中...")
    finally:
        if gc_manager is not None:
            await gc_manager.stop()
        if xiaoxin_runtime is not None:
            await xiaoxin_runtime.stop()

        for task in (stdin_task, ws_task, ota_task):
            if task is not None:
                task.cancel()

        tasks = [task for task in (stdin_task, ws_task, ota_task) if task is not None]
        if tasks:
            await asyncio.wait(
                tasks,
                timeout=3.0,
                return_when=asyncio.ALL_COMPLETED,
            )
        print("服务器已关闭，程序退出。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("手动中断，程序终止。")
