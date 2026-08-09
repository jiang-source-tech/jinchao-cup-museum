from typing import Any, Dict, TYPE_CHECKING

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__


class TtsTextMessageHandler(TextMessageHandler):
    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.TTS

    async def handle(self, conn: "ConnectionHandler", msg_json: Dict[str, Any]) -> None:
        state = msg_json.get("state")
        if state not in {"ready", "done", "error"}:
            conn.logger.bind(tag=TAG).debug(f"Ignoring device tts state: {state}")
            return

        if msg_json.get("session_id") != conn.session_id:
            conn.logger.bind(tag=TAG).warning(
                f"Ignoring tts {state} ack from stale session"
            )
            return

        sentence_id = msg_json.get("sentence_id")
        if not isinstance(sentence_id, str) or not sentence_id:
            conn.logger.bind(tag=TAG).warning(
                f"Ignoring tts {state} ack with invalid sentence_id"
            )
            return

        if state == "error":
            reason = msg_json.get("reason")
            if not isinstance(reason, str) or not reason:
                reason = "unknown_device_error"
            resolved = conn.resolve_tts_error(sentence_id, reason)
            if resolved and hasattr(conn, "mark_xiaoxin_control_tts_failed"):
                conn.mark_xiaoxin_control_tts_failed(sentence_id, reason)
        else:
            resolved = conn.resolve_tts_ack(state, sentence_id)

        if not resolved:
            conn.logger.bind(tag=TAG).debug(
                f"Ignoring unmatched tts {state} ack for sentence_id={sentence_id}"
            )
