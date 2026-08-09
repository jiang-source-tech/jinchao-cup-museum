from typing import Any, Dict

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType


class ServerTextMessageHandler(TextMessageHandler):
    """Server control messages are disabled after removing manager services."""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.SERVER

    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        return
