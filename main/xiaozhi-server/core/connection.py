import copy
import json
import logging
import uuid
import time
import queue
import asyncio
import threading
import traceback
import websockets
from datetime import datetime

from typing import Dict, Any
from collections import deque
from core.utils.modules_initialize import (
    initialize_tts,
    initialize_asr,
)
from core.providers.tts.default import DefaultTTS
from concurrent.futures import ThreadPoolExecutor
from core.utils.dialogue import Message, Dialogue
from core.providers.asr.dto.dto import InterfaceType
from core.handle.textHandle import handleTextMessage
from core.handle.sendAudioHandle import send_tts_message
from core.auth import AuthenticationError
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType
from config.logger import setup_logging, build_module_string, create_connection_logger
from core.utils.prompt_manager import PromptManager
from core.utils.util import get_system_error_response
from core.business_runtime_factory import create_conversation_runtime
from core.conversation_runtime import TurnOutcome, TurnRequest
from core.reliable_tts import TtsAckResult, TtsAttemptError


TAG = __name__

TTS_PHASE_READY_WAIT = "READY_WAIT"
TTS_PHASE_STREAMING = "STREAMING"
TTS_PHASE_DONE_WAIT = "DONE_WAIT"
TTS_PHASE_TERMINAL = "TERMINAL"

class TTSException(RuntimeError):
    pass


class ConnectionHandler:
    def __init__(
        self,
        config: Dict[str, Any],
        _vad,
        _asr,
        _llm,
        server=None,
    ):
        self.config = copy.deepcopy(config)
        self.session_id = str(uuid.uuid4())
        self.logger = setup_logging()
        self.server = server  # 保存server实例的引用
        self.websocket: websockets.ServerConnection | None = None
        self.headers = None
        self.device_id = None
        self.client_ip = None
        self.prompt = None
        self.welcome_msg = None
        self.max_output_size = 0
        self.audio_format = "opus"
        self.sample_rate = 24000  # 默认采样率，从客户端 hello 消息中动态更新

        # 客户端状态相关
        self.client_abort = False
        self.client_is_speaking = False
        self.client_listen_mode = "auto"

        # 线程任务相关
        self.loop = None  # 在 handle_connection 中获取运行中的事件循环
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=5)

        self.vad = None
        self.asr = None
        self.tts = None
        self._asr = _asr
        self._vad = _vad
        self.llm = _llm
        self.conversation_runtime = None
        self.visitor_session_id = None
        self.last_museum_state = None

        # vad相关变量
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.client_voice_window = deque(maxlen=5)
        self.first_activity_time = 0.0  # 记录首次活动的时间（毫秒）
        self.last_activity_time = 0.0  # 统一的活动时间戳（毫秒）
        self.vad_last_voice_time = 0.0  # 记录用户最后一次说话的时间（毫秒）
        self.client_voice_stop = False
        self.last_is_voice = False

        # asr相关变量
        # 因为实际部署时可能会用到公共的本地ASR，不能把变量暴露给公共ASR
        # 所以涉及到ASR的变量，需要在这里定义，属于connection的私有变量
        self.asr_audio = []
        self.asr_audio_queue = queue.Queue()
        self.audio_frames_received = 0
        self.audio_frames_queued = 0
        self.audio_frames_dropped_before_asr_ready = 0

        # llm相关变量
        self.dialogue = Dialogue()

        # tts相关变量
        self.sentence_id = None
        self.tts_ack_waiters: dict[tuple[str, str], asyncio.Future[TtsAckResult]] = {}
        self.tts_ack_completed: dict[tuple[str, str], tuple[TtsAckResult, float]] = {}
        self._tts_ack_wait_subscribers: dict[asyncio.Future[TtsAckResult], int] = {}
        self._tts_ack_active_phases: dict[str, str] = {}
        self._tts_attempt_phases: dict[str, str] = {}
        self._tts_attempt_phase_updated_at: dict[str, float] = {}
        self._tts_terminal_results: dict[str, TtsAckResult] = {}
        self._tts_attempt_terminal_waiters: dict[
            str, asyncio.Future[TtsAckResult]
        ] = {}
        self.tts_ack_completed_ttl_seconds = 30.0
        # 处理TTS响应没有文本返回
        self.tts_MessageText = ""

        # iot相关变量
        self.iot_descriptors = {}

        self.cmd_exit = self.config["exit_commands"]

        # 是否在聊天结束后关闭连接
        self.close_after_chat = False

        self.timeout_seconds = (
            int(self.config.get("close_connection_no_voice_time", 120)) + 60
        )  # 在原来第一道关闭的基础上加60秒，进行二道关闭
        self.timeout_task = None

        # {"mcp":true} 表示启用MCP功能
        self.features = None
        self.client_hello_event = asyncio.Event()
        self.device_time_snapshot = None

        # 标记连接是否来自MQTT
        self.conn_from_mqtt_gateway = False

        # 初始化提示词管理器
        self.prompt_manager = PromptManager(self.config, self.logger)

    async def handle_connection(self, ws: websockets.ServerConnection):
        try:
            # 获取运行中的事件循环（必须在异步上下文中）
            self.loop = asyncio.get_running_loop()

            # 获取并验证headers
            self.headers = dict(ws.request.headers)
            real_ip = self.headers.get("x-real-ip") or self.headers.get(
                "x-forwarded-for"
            )
            if real_ip:
                self.client_ip = real_ip.split(",")[0].strip()
            else:
                self.client_ip = ws.remote_address[0]
            self.logger.bind(tag=TAG).info(
                f"{self.client_ip} conn - Headers: {self.headers}"
            )

            self.device_id = self.headers.get("device-id", None)

            # 认证通过,继续处理
            self.websocket = ws

            # 检查是否来自MQTT连接
            request_path = ws.request.path
            self.conn_from_mqtt_gateway = request_path.endswith("?from=mqtt_gateway")
            if self.conn_from_mqtt_gateway:
                self.logger.bind(tag=TAG).info("连接来自:MQTT网关")

            # 初始化活动时间戳
            self.first_activity_time = time.time() * 1000
            self.last_activity_time = time.time() * 1000

            # 启动超时检查任务
            self.timeout_task = asyncio.create_task(self._check_timeout())

            # Each connection negotiates its own output format. Do not mutate
            # the shared config object when the device sends its input params.
            self.welcome_msg = copy.deepcopy(self.config["xiaozhi"])
            self.welcome_msg["session_id"] = self.session_id

            # 从配置中读取采样率
            self.sample_rate = self.welcome_msg["audio_params"]["sample_rate"]
            self.logger.bind(tag=TAG).info(f"配置输出音频采样率为: {self.sample_rate}")

            # 在后台初始化配置和组件（完全不阻塞主循环）
            asyncio.create_task(self._background_initialize())

            try:
                async for message in self.websocket:
                    await self._route_message(message)
            except websockets.exceptions.ConnectionClosed:
                self.logger.bind(tag=TAG).info("客户端断开连接")

        except AuthenticationError as e:
            self.logger.bind(tag=TAG).error(f"Authentication failed: {str(e)}")
            return
        except Exception as e:
            stack_trace = traceback.format_exc()
            self.logger.bind(tag=TAG).error(f"Connection error: {str(e)}-{stack_trace}")
            return
        finally:
            try:
                sentence_ids = [
                    sentence_id
                    for sentence_id, phase in self._tts_attempt_phases.items()
                    if phase != TTS_PHASE_TERMINAL
                ]
                for sentence_id in sentence_ids:
                    self._terminalize_tts_attempt_failure(
                        sentence_id, "connection_closed_before_done"
                    )
                await self._save_and_close(ws)
            except Exception as final_error:
                self.logger.bind(tag=TAG).error(f"最终清理时出错: {final_error}")
                # 确保即使保存记忆失败，也要关闭连接
                try:
                    await self.close(ws)
                except Exception as close_error:
                    self.logger.bind(tag=TAG).error(
                        f"强制关闭连接时出错: {close_error}"
                    )

    async def _save_and_close(self, ws):
        """Close transport resources for the temporary museum session."""
        try:
            await self.close(ws)
        except Exception as close_error:
            self.logger.bind(tag=TAG).error(f"关闭连接失败: {close_error}")

    async def _route_message(self, message):
        """消息路由"""
        if isinstance(message, str):
            await handleTextMessage(self, message)
        elif isinstance(message, bytes):
            self.audio_frames_received += 1
            if self.vad is None or self.asr is None:
                self.audio_frames_dropped_before_asr_ready += 1
                if (
                    self.audio_frames_dropped_before_asr_ready <= 3
                    or self.audio_frames_dropped_before_asr_ready % 50 == 0
                ):
                    self.logger.bind(tag=TAG).warning(
                        "audio frame dropped before vad/asr ready: "
                        f"frame={self.audio_frames_received} "
                        f"bytes={len(message)} vad_ready={self.vad is not None} "
                        f"asr_ready={self.asr is not None}"
                    )
                return

            # 处理来自MQTT网关的音频包
            if self.conn_from_mqtt_gateway and len(message) >= 16:
                handled = await self._process_mqtt_audio_message(message)
                if handled:
                    return

            # 不需要头部处理或没有头部时，直接处理原始消息
            self.asr_audio_queue.put(message)
            self.audio_frames_queued += 1
            if self.audio_frames_queued <= 3 or self.audio_frames_queued % 100 == 0:
                self.logger.bind(tag=TAG).info(
                    "audio frame received: "
                    f"frame={self.audio_frames_received} queued={self.audio_frames_queued} "
                    f"bytes={len(message)} queue_size={self.asr_audio_queue.qsize()} "
                    f"listen_mode={self.client_listen_mode}"
                )

    async def _process_mqtt_audio_message(self, message):
        """
        处理来自MQTT网关的音频消息，解析16字节头部并提取音频数据

        Args:
            message: 包含头部的音频消息

        Returns:
            bool: 是否成功处理了消息
        """
        try:
            # 提取头部信息
            timestamp = int.from_bytes(message[8:12], "big")
            audio_length = int.from_bytes(message[12:16], "big")

            # 提取音频数据
            if audio_length > 0 and len(message) >= 16 + audio_length:
                # 有指定长度，提取精确的音频数据
                audio_data = message[16 : 16 + audio_length]
                # 基于时间戳进行排序处理
                self._process_websocket_audio(audio_data, timestamp)
                return True
            elif len(message) > 16:
                # 没有指定长度或长度无效，去掉头部后处理剩余数据
                audio_data = message[16:]
                self.asr_audio_queue.put(audio_data)
                return True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"解析WebSocket音频包失败: {e}")

        # 处理失败，返回False表示需要继续处理
        return False

    def _process_websocket_audio(self, audio_data, timestamp):
        """处理WebSocket格式的音频包"""
        # 初始化时间戳序列管理
        if not hasattr(self, "audio_timestamp_buffer"):
            self.audio_timestamp_buffer = {}
            self.last_processed_timestamp = 0
            self.max_timestamp_buffer_size = 20

        # 如果时间戳是递增的，直接处理
        if timestamp >= self.last_processed_timestamp:
            self.asr_audio_queue.put(audio_data)
            self.last_processed_timestamp = timestamp

            # 处理缓冲区中的后续包
            processed_any = True
            while processed_any:
                processed_any = False
                for ts in sorted(self.audio_timestamp_buffer.keys()):
                    if ts > self.last_processed_timestamp:
                        buffered_audio = self.audio_timestamp_buffer.pop(ts)
                        self.asr_audio_queue.put(buffered_audio)
                        self.last_processed_timestamp = ts
                        processed_any = True
                        break
        else:
            # 乱序包，暂存
            if len(self.audio_timestamp_buffer) < self.max_timestamp_buffer_size:
                self.audio_timestamp_buffer[timestamp] = audio_data
            else:
                self.asr_audio_queue.put(audio_data)

    async def handle_restart(self, message):
        """处理服务器重启请求"""
        try:

            self.logger.bind(tag=TAG).info("收到服务器重启指令，准备执行...")

            # 发送确认响应
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "success",
                        "message": "服务器重启中...",
                        "content": {"action": "restart"},
                    }
                )
            )

            # 异步执行重启操作
            def restart_server():
                """实际执行重启的方法"""
                time.sleep(1)
                self.logger.bind(tag=TAG).info("执行服务器重启...")
                subprocess.Popen(
                    [sys.executable, "app.py"],
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    start_new_session=True,
                )
                os._exit(0)

            # 使用线程执行重启避免阻塞事件循环
            threading.Thread(target=restart_server, daemon=True).start()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"重启失败: {str(e)}")
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "error",
                        "message": f"Restart failed: {str(e)}",
                        "content": {"action": "restart"},
                    }
                )
            )

    def _initialize_components(self):
        try:
            if self.tts is None:
                self.tts = self._initialize_tts()
            # 打开语音合成通道
            asyncio.run_coroutine_threadsafe(
                self.tts.open_audio_channels(self), self.loop
            )
            self.selected_module_str = build_module_string(
                self.config.get("selected_module", {})
            )
            self.logger = create_connection_logger(self.selected_module_str)

            """初始化组件"""
            if self.config.get("prompt") is not None:
                user_prompt = self.config["prompt"]
                # 使用快速提示词进行初始化
                prompt = self.prompt_manager.get_quick_prompt(user_prompt)
                self.change_system_prompt(prompt)
                self.logger.bind(tag=TAG).info(
                    f"快速初始化组件: prompt成功 {prompt[:50]}..."
                )

            """初始化本地组件"""
            if self.vad is None:
                self.vad = self._vad
            if self.asr is None:
                self.asr = self._initialize_asr()

            # 打开语音识别通道
            asyncio.run_coroutine_threadsafe(
                self.asr.open_audio_channels(self), self.loop
            )

            self._init_conversation_runtime()
            """更新系统提示词"""
            self._init_prompt_enhancement()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"实例化组件失败: {e}")

    def _init_prompt_enhancement(self):

        # 更新上下文信息
        self.prompt_manager.update_context_info(self, self.client_ip)
        enhanced_prompt = self.prompt_manager.build_enhanced_prompt(
            self.config["prompt"],
            self.device_id,
            self.client_ip,
            emoji_enabled=(self.features or {}).get("emoji", True),
        )
        if enhanced_prompt:
            self.change_system_prompt(enhanced_prompt)
            self.logger.bind(tag=TAG).debug("系统提示词已增强更新")

    def _init_conversation_runtime(self):
        self.conversation_runtime = create_conversation_runtime(self.config)

    def _try_business_turn(self, query: str, current_sentence_id: str) -> bool:
        if not query:
            return False
        if self.conversation_runtime is None:
            self._init_conversation_runtime()

        history = self.dialogue.get_llm_dialogue()
        if (
            history
            and history[-1].get("role") == "user"
            and history[-1].get("content") == query
        ):
            history = history[:-1]

        request = TurnRequest(
            request_id=current_sentence_id,
            transport_session_id=self.session_id,
            visitor_session_id=self.visitor_session_id,
            device_id=self.device_id,
            user_text=query,
            history=tuple(history[-8:]),
            occurred_at=datetime.now().astimezone(),
            llm=self.llm,
            metadata={
                "device_time_snapshot": self.device_time_snapshot,
            },
        )
        try:
            self._publish_retrieving_state(current_sentence_id)
            outcome = self.conversation_runtime.handle_turn(request)
        except Exception as exc:
            logging.getLogger(__name__).exception("Business runtime failed")
            self.logger.bind(tag=TAG).error(f"Business runtime failed: {exc}")
            outcome = TurnOutcome(
                handled=True,
                spoken_text=get_system_error_response(self.config),
                error_code="runtime_failure",
            )

        if not outcome.handled:
            return False
        visitor_session_id = outcome.audit_record.get("visitor_session_id")
        if visitor_session_id:
            self.visitor_session_id = str(visitor_session_id)
        if outcome.museum_state:
            self.last_museum_state = dict(outcome.museum_state)
            self._publish_museum_state(outcome.museum_state)
        if outcome.output_committed:
            return True

        reply = outcome.spoken_text or get_system_error_response(self.config)
        self.tts.store_tts_text(current_sentence_id, reply)
        self.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=current_sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT,
                content_detail=reply,
            )
        )
        self.dialogue.put(Message(role="assistant", content=reply))
        return True

    async def send_initial_museum_state(self) -> None:
        if self.conversation_runtime is None:
            self._init_conversation_runtime()
        open_session = getattr(self.conversation_runtime, "open_session", None)
        if not callable(open_session):
            return
        request = TurnRequest(
            request_id=f"hello-{uuid.uuid4().hex}",
            transport_session_id=self.session_id,
            visitor_session_id=self.visitor_session_id,
            device_id=self.device_id,
            user_text="",
            history=(),
            occurred_at=datetime.now().astimezone(),
            llm=self.llm,
            metadata={"device_time_snapshot": self.device_time_snapshot},
        )
        outcome = await asyncio.to_thread(open_session, request)
        visitor_session_id = outcome.audit_record.get("visitor_session_id")
        if visitor_session_id:
            self.visitor_session_id = str(visitor_session_id)
        if outcome.museum_state:
            self.last_museum_state = dict(outcome.museum_state)
            await self.send_business_event(outcome.museum_state)

    async def send_business_event(self, payload: Dict[str, Any]) -> None:
        if self.websocket is None:
            raise RuntimeError("websocket is not connected")
        await self.websocket.send(json.dumps(payload, ensure_ascii=False))

    def _publish_museum_state(self, state) -> None:
        if not state or self.loop is None or self.websocket is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            self.send_business_event(dict(state)),
            self.loop,
        )
        try:
            future.result(timeout=3)
        except Exception as exc:
            self.logger.bind(tag=TAG).warning(f"museum_state send failed: {exc}")

    def _publish_retrieving_state(self, request_id: str) -> None:
        if not self.last_museum_state:
            return
        state = copy.deepcopy(self.last_museum_state)
        state["request_id"] = request_id
        grounding = state.setdefault("grounding", {})
        grounding["status"] = "retrieving"
        grounding["source_count"] = 0
        self._publish_museum_state(state)

    def _initialize_tts(self):
        """初始化TTS"""
        tts = None
        tts = initialize_tts(self.config)

        if tts is None:
            tts = DefaultTTS(self.config, delete_audio_file=True)

        return tts

    def _initialize_asr(self):
        """初始化ASR"""
        if (
            self._asr is not None
            and hasattr(self._asr, "interface_type")
            and self._asr.interface_type == InterfaceType.LOCAL
        ):
            # 如果公共ASR是本地服务，则直接返回
            # 因为本地一个实例ASR，可以被多个连接共享
            asr = self._asr
        else:
            # 如果公共ASR是远程服务，则初始化一个新实例
            # 因为远程ASR，涉及到websocket连接和接收线程，需要每个连接一个实例
            asr = initialize_asr(self.config)

        return asr

    async def _background_initialize(self):
        """Initialize local connection components without blocking the event loop."""
        try:
            self.executor.submit(self._initialize_components)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Background initialization failed: {e}")

    def change_system_prompt(self, prompt):
        self.prompt = prompt
        # 更新系统prompt至上下文
        self.dialogue.update_system_message(self.prompt)

    def chat(self, query, depth=0, sentence_id=None):
        if not query:
            return False
        current_sentence_id = sentence_id or str(uuid.uuid4().hex)
        self.sentence_id = current_sentence_id
        self.logger.bind(tag=TAG).info(f"博物馆运行时收到用户消息: {query}")
        self.dialogue.put(Message(role="user", content=query))
        self.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=current_sentence_id,
                sentence_type=SentenceType.FIRST,
                content_type=ContentType.ACTION,
            )
        )
        handled = self._try_business_turn(query, current_sentence_id)
        self.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=current_sentence_id,
                sentence_type=SentenceType.LAST,
                content_type=ContentType.ACTION,
            )
        )
        return handled

    def clearSpeakStatus(self):
        self.client_is_speaking = False
        self.logger.bind(tag=TAG).debug(f"清除服务端讲话状态")

    def supports_tts_ready_ack(self) -> bool:
        return (self.features or {}).get("tts_ready_ack") is True

    def supports_tts_done_ack(self) -> bool:
        return (self.features or {}).get("tts_done_ack") is True

    def supports_tts_preroll_buffer(self) -> bool:
        return (self.features or {}).get("tts_preroll_buffer") is True

    def _tts_ack_key(self, state: str, sentence_id: str):
        return (state, sentence_id)

    def _prune_tts_completed_acks(self) -> None:
        stale_before = time.monotonic() - self.tts_ack_completed_ttl_seconds
        for key, (_, completed_at) in list(self.tts_ack_completed.items()):
            _, sentence_id = key
            if self._tts_attempt_phases.get(sentence_id) != TTS_PHASE_TERMINAL:
                continue
            if completed_at < stale_before:
                self.tts_ack_completed.pop(key, None)

                future = self.tts_ack_waiters.get(key)
                if (
                    future is not None
                    and future.done()
                    and future not in self._tts_ack_wait_subscribers
                ):
                    self.tts_ack_waiters.pop(key, None)
                    state, sentence_id = key
                    if self._tts_ack_active_phases.get(sentence_id) == state:
                        self._tts_ack_active_phases.pop(sentence_id, None)

        for sentence_id, updated_at in list(self._tts_attempt_phase_updated_at.items()):
            if (
                self._tts_attempt_phases.get(sentence_id) != TTS_PHASE_TERMINAL
                or updated_at >= stale_before
            ):
                continue
            for state in ("ready", "done"):
                self._retire_tts_ack_phase(sentence_id, state)
            self._tts_attempt_phases.pop(sentence_id, None)
            self._tts_attempt_phase_updated_at.pop(sentence_id, None)
            self._tts_terminal_results.pop(sentence_id, None)
            self._tts_attempt_terminal_waiters.pop(sentence_id, None)

    def _set_tts_attempt_phase(self, sentence_id: str, phase: str) -> None:
        self._tts_attempt_phases[sentence_id] = phase
        self._tts_attempt_phase_updated_at[sentence_id] = time.monotonic()

    def _terminalize_tts_attempt_failure(
        self, sentence_id: str, reason: str
    ) -> TtsAckResult:
        existing = self._tts_terminal_results.get(sentence_id)
        if self._tts_attempt_phases.get(sentence_id) == TTS_PHASE_TERMINAL:
            return existing or TtsAckResult(
                "error", sentence_id, reason or "attempt_failed"
            )
        result = TtsAckResult("error", sentence_id, reason or "attempt_failed")
        self._tts_terminal_results[sentence_id] = result
        self._set_tts_attempt_phase(sentence_id, TTS_PHASE_TERMINAL)
        terminal_waiter = self._tts_attempt_terminal_waiters.get(sentence_id)
        if terminal_waiter is not None and not terminal_waiter.done():
            terminal_waiter.set_result(result)
        state = self._tts_ack_active_phases.get(sentence_id)
        if state in {"ready", "done"}:
            key = self._tts_ack_key(state, sentence_id)
            future = self.tts_ack_waiters.get(key)
            self.tts_ack_completed[key] = (result, time.monotonic())
            if future is not None and not future.done():
                future.set_result(result)
        return result

    def begin_tts_terminal_wait(
        self, sentence_id: str
    ) -> asyncio.Future[TtsAckResult]:
        self._prune_tts_completed_acks()
        future = self._tts_attempt_terminal_waiters.get(sentence_id)
        if future is None or future.cancelled():
            future = asyncio.get_running_loop().create_future()
            self._tts_attempt_terminal_waiters[sentence_id] = future
        terminal = self._tts_terminal_results.get(sentence_id)
        if terminal is not None and not future.done():
            future.set_result(terminal)
        return future

    async def wait_for_tts_terminal(self, sentence_id: str) -> TtsAckResult:
        future = self.begin_tts_terminal_wait(sentence_id)
        return await asyncio.shield(future)

    def _retire_tts_ack_phase(self, sentence_id: str, state: str) -> None:
        key = self._tts_ack_key(state, sentence_id)
        self.tts_ack_waiters.pop(key, None)
        self.tts_ack_completed.pop(key, None)
        if self._tts_ack_active_phases.get(sentence_id) == state:
            self._tts_ack_active_phases.pop(sentence_id, None)

    def begin_tts_ack_wait(
        self, state: str, sentence_id: str
    ) -> asyncio.Future[TtsAckResult]:
        self._prune_tts_completed_acks()
        if state not in {"ready", "done"}:
            raise ValueError(f"unsupported tts ack state: {state}")

        phase = self._tts_attempt_phases.get(sentence_id)
        if phase == TTS_PHASE_TERMINAL:
            future = asyncio.get_running_loop().create_future()
            result = self._tts_terminal_results.get(sentence_id)
            if result is None:
                result = TtsAckResult("error", sentence_id, "attempt_terminal")
            future.set_result(result)
            return future

        target_phase = TTS_PHASE_READY_WAIT if state == "ready" else TTS_PHASE_DONE_WAIT
        if state == "ready":
            if phase is None:
                self._set_tts_attempt_phase(sentence_id, target_phase)
            elif phase != TTS_PHASE_READY_WAIT:
                future = asyncio.get_running_loop().create_future()
                future.set_result(
                    TtsAckResult("error", sentence_id, "invalid_ack_phase")
                )
                return future
        else:
            if phase in {None, TTS_PHASE_STREAMING}:
                self._set_tts_attempt_phase(sentence_id, target_phase)
            elif phase != TTS_PHASE_DONE_WAIT:
                future = asyncio.get_running_loop().create_future()
                future.set_result(
                    TtsAckResult("error", sentence_id, "invalid_ack_phase")
                )
                return future

        active_state = self._tts_ack_active_phases.get(sentence_id)
        if active_state is not None and active_state != state:
            self._retire_tts_ack_phase(sentence_id, active_state)
        self._tts_ack_active_phases[sentence_id] = state

        key = self._tts_ack_key(state, sentence_id)
        future = self.tts_ack_waiters.get(key)
        completed = self.tts_ack_completed.get(key)
        if future is None or (future.done() and completed is None):
            future = asyncio.get_running_loop().create_future()
            if completed is not None:
                future.set_result(completed[0])
            self.tts_ack_waiters[key] = future
        return future

    def mark_tts_streaming(self, sentence_id: str) -> bool:
        self._prune_tts_completed_acks()
        if self._tts_attempt_phases.get(sentence_id) != TTS_PHASE_READY_WAIT:
            return False
        self._retire_tts_ack_phase(sentence_id, "ready")
        self._set_tts_attempt_phase(sentence_id, TTS_PHASE_STREAMING)
        return True

    def resolve_tts_ack(self, state: str, sentence_id: str) -> bool:
        self._prune_tts_completed_acks()
        expected_phase = {
            "ready": TTS_PHASE_READY_WAIT,
            "done": TTS_PHASE_DONE_WAIT,
        }.get(state)
        if expected_phase is None:
            return False
        if self._tts_attempt_phases.get(sentence_id) != expected_phase:
            return False

        active_state = self._tts_ack_active_phases.get(sentence_id)
        if active_state is not None and active_state != state:
            return False

        result = TtsAckResult(state=state, sentence_id=sentence_id)
        key = self._tts_ack_key(state, sentence_id)
        future = self.tts_ack_waiters.get(key)
        self.tts_ack_completed[key] = (result, time.monotonic())
        self._tts_attempt_phase_updated_at[sentence_id] = time.monotonic()
        if state == "done":
            self._tts_terminal_results[sentence_id] = result
            self._set_tts_attempt_phase(sentence_id, TTS_PHASE_TERMINAL)
            terminal_waiter = self._tts_attempt_terminal_waiters.get(sentence_id)
            if terminal_waiter is not None and not terminal_waiter.done():
                terminal_waiter.set_result(result)
        if future is None or future.done():
            return True
        future.set_result(result)
        return True

    def resolve_tts_error(self, sentence_id: str, reason: str) -> bool:
        self._prune_tts_completed_acks()
        phase = self._tts_attempt_phases.get(sentence_id)
        if phase not in {
            TTS_PHASE_READY_WAIT,
            TTS_PHASE_STREAMING,
            TTS_PHASE_DONE_WAIT,
        }:
            return False

        state = {
            TTS_PHASE_READY_WAIT: "ready",
            TTS_PHASE_DONE_WAIT: "done",
        }.get(phase)
        result = TtsAckResult("error", sentence_id, reason)
        self._tts_terminal_results[sentence_id] = result
        self._set_tts_attempt_phase(sentence_id, TTS_PHASE_TERMINAL)
        terminal_waiter = self._tts_attempt_terminal_waiters.get(sentence_id)
        if terminal_waiter is not None and not terminal_waiter.done():
            terminal_waiter.set_result(result)

        if state is None:
            return True

        key = self._tts_ack_key(state, sentence_id)
        future = self.tts_ack_waiters.get(key)
        if future is None or future.done():
            self.tts_ack_completed[key] = (result, time.monotonic())
            return True

        self.tts_ack_completed[key] = (result, time.monotonic())
        future.set_result(result)
        return True

    async def wait_for_tts_ack(
        self, state: str, sentence_id: str, timeout_ms: int
    ) -> TtsAckResult | None:
        self._prune_tts_completed_acks()
        key = self._tts_ack_key(state, sentence_id)
        future = self.tts_ack_waiters.get(key) or self.begin_tts_ack_wait(
            state, sentence_id
        )
        self._tts_ack_wait_subscribers[future] = (
            self._tts_ack_wait_subscribers.get(future, 0) + 1
        )
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout_ms / 1000)
        except asyncio.TimeoutError:
            return None
        finally:
            subscriber_count = self._tts_ack_wait_subscribers[future] - 1
            if subscriber_count > 0:
                self._tts_ack_wait_subscribers[future] = subscriber_count
            else:
                self._tts_ack_wait_subscribers.pop(future, None)
                if self.tts_ack_waiters.get(key) is future:
                    self.tts_ack_waiters.pop(key, None)
                    self.tts_ack_completed.pop(key, None)
                    if self._tts_ack_active_phases.get(sentence_id) == state:
                        self._tts_ack_active_phases.pop(sentence_id, None)

    async def close(self, ws=None):
        """资源清理方法"""
        try:
            # 清理 VAD 连接资源
            if (
                hasattr(self, "vad")
                and self.vad
                and hasattr(self.vad, "release_conn_resources")
            ):
                self.vad.release_conn_resources(self)

            # 清理音频缓冲区
            if hasattr(self, "audio_buffer"):
                self.audio_buffer.clear()

            # 取消超时任务
            if self.timeout_task and not self.timeout_task.done():
                self.timeout_task.cancel()
                try:
                    await self.timeout_task
                except asyncio.CancelledError:
                    pass
                self.timeout_task = None

            # 触发停止事件
            if self.stop_event:
                self.stop_event.set()

            # 清空任务队列
            self.clear_queues()

            # 关闭WebSocket连接
            try:
                if ws:
                    # 安全地检查WebSocket状态并关闭
                    try:
                        if hasattr(ws, "closed") and not ws.closed:
                            await ws.close()
                        elif hasattr(ws, "state") and ws.state.name != "CLOSED":
                            await ws.close()
                        else:
                            # 如果没有closed属性，直接尝试关闭
                            await ws.close()
                    except Exception:
                        # 如果关闭失败，忽略错误
                        pass
                elif self.websocket:
                    try:
                        if (
                            hasattr(self.websocket, "closed")
                            and not self.websocket.closed
                        ):
                            await self.websocket.close()
                        elif (
                            hasattr(self.websocket, "state")
                            and self.websocket.state.name != "CLOSED"
                        ):
                            await self.websocket.close()
                        else:
                            # 如果没有closed属性，直接尝试关闭
                            await self.websocket.close()
                    except Exception:
                        # 如果关闭失败，忽略错误
                        pass
            except Exception as ws_error:
                self.logger.bind(tag=TAG).error(f"关闭WebSocket连接时出错: {ws_error}")

            if self.tts:
                await self.tts.close()
            if self.asr:
                await self.asr.close()

            # 最后关闭线程池（避免阻塞）
            if self.executor:
                try:
                    self.executor.shutdown(wait=False)
                except Exception as executor_error:
                    self.logger.bind(tag=TAG).error(
                        f"关闭线程池时出错: {executor_error}"
                    )
                self.executor = None
            self.logger.bind(tag=TAG).info("连接资源已释放")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"关闭连接时出错: {e}")
        finally:
            # 确保停止事件被设置
            if self.stop_event:
                self.stop_event.set()

    def clear_queues(self):
        """清空所有任务队列"""
        if self.tts:
            self.logger.bind(tag=TAG).debug(
                f"开始清理: TTS队列大小={self.tts.tts_text_queue.qsize()}, 音频队列大小={self.tts.tts_audio_queue.qsize()}"
            )

            # 使用非阻塞方式清空队列
            for q in [
                self.tts.tts_text_queue,
                self.tts.tts_audio_queue,
            ]:
                if not q:
                    continue
                while True:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break

            # 重置音频流控器（取消后台任务并清空队列）
            if hasattr(self, "audio_rate_controller") and self.audio_rate_controller:
                self.audio_rate_controller.reset()
                self.logger.bind(tag=TAG).debug("已重置音频流控器")

            self.logger.bind(tag=TAG).debug(
                f"清理结束: TTS队列大小={self.tts.tts_text_queue.qsize()}, 音频队列大小={self.tts.tts_audio_queue.qsize()}"
            )

    def reset_audio_states(self):
        """
        重置所有音频相关状态(VAD + ASR)
        """
        # Reset VAD states
        self.client_audio_buffer.clear()
        self.client_have_voice = False
        self.client_voice_stop = False
        self.client_voice_window.clear()
        self.last_is_voice = False
        self.vad_last_voice_time = 0.0

        # Clear ASR buffers
        self.asr_audio.clear()

        self.logger.bind(tag=TAG).debug("All audio states reset.")

    async def _check_timeout(self):
        """检查连接超时"""
        try:
            while not self.stop_event.is_set():
                last_activity_time = self.last_activity_time
                # 检查是否超时（只有在时间戳已初始化的情况下）
                if last_activity_time > 0.0:
                    current_time = time.time() * 1000
                    if current_time - last_activity_time > self.timeout_seconds * 1000:
                        if not self.stop_event.is_set():
                            self.logger.bind(tag=TAG).info("连接超时，准备关闭")
                            # 设置停止事件，防止重复处理
                            self.stop_event.set()
                            # 使用 try-except 包装关闭操作，确保不会因为异常而阻塞
                            try:
                                await self.close(self.websocket)
                            except Exception as close_error:
                                self.logger.bind(tag=TAG).error(
                                    f"超时关闭连接时出错: {close_error}"
                                )
                        break
                # 每10秒检查一次，避免过于频繁
                await asyncio.sleep(10)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"超时检查任务出错: {e}")
        finally:
            self.logger.bind(tag=TAG).info("超时检查任务已退出")
