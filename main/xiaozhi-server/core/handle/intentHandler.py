from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.handle.helloHandle import checkWakeupWords
from core.handle.sendAudioHandle import send_stt_message
from core.utils.util import remove_punctuation_and_length


async def handle_user_intent(conn: "ConnectionHandler", text: str) -> bool:
    """Handle transport-level voice commands before the museum runtime."""
    _, filtered_text = remove_punctuation_and_length(text)
    if filtered_text in conn.cmd_exit:
        conn.logger.bind(tag=__name__).info("收到设备退出指令")
        await send_stt_message(conn, text)
        await conn.close()
        return True
    return await checkWakeupWords(conn, filtered_text)
