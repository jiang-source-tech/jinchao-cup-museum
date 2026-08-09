import base64
import asyncio
import json
import os
import queue
import time
import traceback
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets

from config.logger import setup_logging
from core.providers.tts.base import TTSProviderBase
from core.providers.tts.dto.dto import ContentType, InterfaceType, SentenceType
from core.providers.llm.openai.credentials import resolve_api_key
from core.utils.tts import MarkdownCleaner

TAG = __name__
logger = setup_logging()


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 100, 50, int),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.interface_type = InterfaceType.DUAL_STREAM
        self.report_on_last = True

        self.api_key = resolve_api_key(config)

        self.model = config.get("model", "qwen3-tts-instruct-flash-realtime")
        self.voice = config.get("private_voice") or config.get("voice", "Cherry")
        self.mode = config.get("mode", "server_commit")
        self.language_type = config.get("language_type", "Auto")
        self.response_format = config.get("response_format", "pcm")
        self.sample_rate = int(config.get("sample_rate", 24000))
        volume = config.get("volume", "50")
        self.volume = int(volume) if volume else 50
        self.instructions = config.get("instructions", "")
        self.optimize_instructions = self._to_optional_bool(
            config.get("optimize_instructions")
        )

        self.ws_url = self._build_ws_url(config)
        self.header = {"Authorization": f"Bearer {self.api_key}"}
        self.ws = None
        self._monitor_task = None
        self.activate_session = False
        self.last_active_time = None
        self.tts_text = ""
        self._first_audio_sent = False
        self._last_subtitle_text = ""

    @staticmethod
    def _to_optional_bool(value):
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)

    def _build_ws_url(self, config):
        url = config.get("url") or config.get("ws_url")
        workspace_id = config.get("workspace_id")
        region = config.get("region", "cn-beijing")

        if not url:
            if workspace_id:
                url = (
                    f"wss://{workspace_id}.{region}.maas.aliyuncs.com"
                    "/api-ws/v1/realtime"
                )
            else:
                url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault("model", self.model)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    async def _send_event(self, event):
        event["event_id"] = f"event_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        await self.ws.send(json.dumps(event, ensure_ascii=False))
        self.last_active_time = time.time()

    def _build_session_config(self):
        session = {
            "mode": self.mode,
            "voice": self.voice,
            "language_type": self.language_type,
            "response_format": self.response_format,
            "sample_rate": self.sample_rate,
            "volume": self.volume,
        }
        if self.instructions:
            session["instructions"] = self.instructions
        if self.optimize_instructions is not None:
            session["optimize_instructions"] = self.optimize_instructions
        return session

    async def _ensure_connection(self):
        if self.ws:
            return self.ws

        self.ws = await websockets.connect(
            self.ws_url,
            additional_headers=self.header,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=10,
            max_size=10 * 1024 * 1024,
        )
        self.last_active_time = time.time()
        return self.ws

    def tts_text_priority_thread(self):
        while not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)

                if self.conn.client_abort:
                    try:
                        logger.bind(tag=TAG).info("Client abort received; finishing Qwen TTS session")
                        future = self._run_coroutine(self.finish_session(self.conn.sentence_id))
                        future.result(timeout=self.tts_timeout)
                    except Exception as e:
                        logger.bind(tag=TAG).warning(f"Failed to finish Qwen TTS session: {e}")
                    continue

                if message.sentence_id != self.conn.sentence_id:
                    continue

                if message.sentence_type == SentenceType.FIRST:
                    self.reset_stream_state()
                    self.tts_text = ""
                    self._first_audio_sent = False
                    self._last_subtitle_text = ""
                    self.before_stop_play_files.clear()
                    if not getattr(self.conn, "sentence_id", None):
                        self.conn.sentence_id = uuid.uuid4().hex
                    future = self._run_coroutine(self.start_session(self.conn.sentence_id))
                    future.result(timeout=self.tts_timeout)

                elif message.content_type == ContentType.TEXT:
                    if message.content_detail:
                        future = self._run_coroutine(
                            self.text_to_speak(message.content_detail, None)
                        )
                        future.result(timeout=self.tts_timeout)

                elif message.content_type == ContentType.FILE:
                    logger.bind(tag=TAG).info(
                        f"Add audio file to pending playback list: {message.content_file}"
                    )
                    if message.content_file and os.path.exists(message.content_file):
                        self._process_audio_file_stream(
                            message.content_file,
                            callback=lambda audio_data: self.handle_audio_file(
                                audio_data, message.content_detail
                            ),
                        )

                if message.sentence_type == SentenceType.LAST:
                    future = self._run_coroutine(self.finish_session(self.conn.sentence_id))
                    future.result(timeout=self.tts_timeout)

            except queue.Empty:
                continue
            except Exception as e:
                logger.bind(tag=TAG).error(
                    f"Failed to process Qwen TTS text: {str(e)}, type: {type(e).__name__}, stack: {traceback.format_exc()}"
                )

    def _run_coroutine(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.conn.loop)

    async def start_session(self, session_id):
        try:
            if self.activate_session:
                await self.close()

            self.activate_session = True
            await self._ensure_connection()

            if self._monitor_task is None or self._monitor_task.done():
                self._monitor_task = asyncio.create_task(
                    self._start_monitor_tts_response()
                )

            await self._send_event(
                {"type": "session.update", "session": self._build_session_config()}
            )
            logger.bind(tag=TAG).debug(f"Qwen TTS Realtime session started: {session_id}")
        except Exception as e:
            logger.bind(tag=TAG).error(f"Failed to start Qwen TTS Realtime session: {str(e)}")
            await self.close()
            raise

    async def text_to_speak(self, text, _):
        if self.ws is None:
            logger.bind(tag=TAG).warning("Qwen TTS WebSocket does not exist; skip text")
            return

        filtered_text = MarkdownCleaner.clean_markdown(text)
        if not filtered_text:
            return

        confirmed_texts, self._pending_prefix = self._match_stream_text(filtered_text)
        for txt in confirmed_texts:
            if not txt:
                continue
            self.tts_text += txt
            await self._send_event({"type": "input_text_buffer.append", "text": txt})

    async def finish_session(self, session_id):
        try:
            if self.ws:
                if self._pending_prefix:
                    self.tts_text += self._pending_prefix
                    await self._send_event(
                        {"type": "input_text_buffer.append", "text": self._pending_prefix}
                    )
                    self._pending_prefix = ""
                await self._send_event({"type": "session.finish"})
        except Exception as e:
            logger.bind(tag=TAG).error(f"Failed to finish Qwen TTS Realtime session: {str(e)}")
            await self.close()
            raise

    async def close(self):
        await super().close()
        self.activate_session = False
        await self._cancel_monitor_task()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
            self.last_active_time = None

    async def _cancel_monitor_task(self):
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.bind(tag=TAG).warning(f"Failed to cancel Qwen TTS monitor task: {e}")
        self._monitor_task = None

    async def _start_monitor_tts_response(self):
        try:
            while not self.conn.stop_event.is_set() and self.ws:
                try:
                    msg = await self.ws.recv()
                    self.last_active_time = time.time()
                    if not isinstance(msg, str):
                        continue

                    event = json.loads(msg)
                    event_type = event.get("type")

                    if event_type == "error":
                        logger.bind(tag=TAG).error(f"Qwen TTS Realtime error: {event}")
                        break
                    if event_type == "session.created":
                        logger.bind(tag=TAG).debug("Qwen TTS Realtime session created")
                    elif event_type == "session.updated":
                        logger.bind(tag=TAG).debug("Qwen TTS Realtime session updated")
                    elif event_type == "response.audio.delta":
                        audio = base64.b64decode(event.get("delta", ""))
                        if audio:
                            self._emit_first_audio_marker()
                            self._emit_subtitle_update()
                            self.opus_encoder.encode_pcm_to_opus_stream(
                                audio, False, callback=self.handle_opus
                            )
                    elif event_type == "response.done":
                        logger.bind(tag=TAG).debug("Qwen TTS Realtime response done")
                    elif event_type == "session.finished":
                        logger.bind(tag=TAG).debug("Qwen TTS Realtime session finished")
                        self.activate_session = False
                        self._emit_first_audio_marker()
                        self._emit_subtitle_update()
                        self._process_before_stop_play_files()
                        break
                except websockets.ConnectionClosed:
                    logger.bind(tag=TAG).warning("Qwen TTS Realtime WebSocket closed")
                    break
                except Exception as e:
                    logger.bind(tag=TAG).error(
                        f"Failed to process Qwen TTS Realtime response: {e}\n{traceback.format_exc()}"
                    )
                    break
        finally:
            self.activate_session = False
            self._monitor_task = None
            if self.ws:
                try:
                    await self.ws.close()
                except Exception:
                    pass
                self.ws = None

    def _emit_first_audio_marker(self):
        if self._first_audio_sent:
            return
        text = self.tts_text.strip() or None
        if text is None:
            return
        self._first_audio_sent = True
        self._last_subtitle_text = text
        self.tts_audio_queue.put(
            (SentenceType.FIRST, [], text, self.conn.sentence_id)
        )

    def _emit_subtitle_update(self):
        if not self._first_audio_sent:
            return
        text = self.tts_text.strip()
        if not text or text == self._last_subtitle_text:
            return
        self._last_subtitle_text = text
        self.tts_audio_queue.put(
            (SentenceType.UPDATE, [], text, self.conn.sentence_id)
        )

    def audio_to_opus_data_stream(self, audio_file_path, callback=None):
        from core.utils.util import audio_to_data_stream

        return audio_to_data_stream(
            audio_file_path,
            is_opus=True,
            callback=callback,
            sample_rate=self.conn.sample_rate,
            opus_encoder=None,
        )

    def wakeup_response_is_opus(self, conn) -> bool:
        return True

    def to_tts(self, text: str) -> list:
        logger.bind(tag=TAG).warning("Qwen TTS Realtime does not support non-stream to_tts")
        return []
