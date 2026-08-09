from typing import Any, Dict

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType


class XiaoxinAckMessageHandler(TextMessageHandler):
    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.XIAOXIN_ACK

    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        runtime = getattr(getattr(conn, "server", None), "xiaoxin_runtime", None)
        if runtime is None:
            logger = getattr(conn, "logger", None)
            if logger is not None:
                logger.bind(tag=__name__).warning("xiaoxin_ack ignored without runtime")
            return

        await runtime.dispatcher.handle_ack(conn.device_id, msg_json, conn)
