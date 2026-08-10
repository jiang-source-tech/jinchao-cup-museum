import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.handle.abortHandle import handleAbortMessage
from core.handle.intentHandler import handle_user_intent
from core.handle.sendAudioHandle import send_stt_message


async def handleAudioMessage(conn: "ConnectionHandler", audio):
    have_voice = conn.vad.is_vad(conn, audio)
    if getattr(conn, "just_woken_up", False):
        have_voice = False
        if not hasattr(conn, "vad_resume_task") or conn.vad_resume_task.done():
            conn.vad_resume_task = asyncio.create_task(resume_vad_detection(conn))
        return
    await no_voice_close_connect(conn, have_voice)
    await conn.asr.receive_audio(conn, audio, have_voice)


async def resume_vad_detection(conn: "ConnectionHandler"):
    await asyncio.sleep(2)
    conn.just_woken_up = False


def _extract_recognized_text(text: str) -> str:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        return data["content"]
    return text


async def startToChat(conn: "ConnectionHandler", text: str):
    actual_text = _extract_recognized_text(text).strip()
    if not actual_text:
        return

    sentence_id = uuid.uuid4().hex
    conn.sentence_id = sentence_id

    if conn.client_is_speaking and conn.client_listen_mode != "manual":
        await handleAbortMessage(conn)

    if await handle_user_intent(conn, actual_text):
        return

    await send_stt_message(conn, actual_text, sentence_id=sentence_id)
    conn.client_abort = False
    conn.executor.submit(conn.chat, actual_text, 0, sentence_id)


async def no_voice_close_connect(conn: "ConnectionHandler", have_voice: bool):
    if have_voice:
        conn.last_activity_time = time.time() * 1000
        return
    if conn.last_activity_time <= 0:
        return
    idle_ms = time.time() * 1000 - conn.last_activity_time
    timeout_ms = int(conn.config.get("close_connection_no_voice_time", 120)) * 1000
    if idle_ms > timeout_ms and not conn.close_after_chat:
        conn.close_after_chat = True
        await conn.close()
