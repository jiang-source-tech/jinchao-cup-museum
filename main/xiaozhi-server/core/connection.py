import os
import sys
import copy
import json
import re
import logging
import uuid
import time
import queue
import asyncio
import threading
import traceback
import subprocess
import websockets
from dataclasses import dataclass, replace
from datetime import datetime

from core.utils.util import extract_json_from_string
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
from core.providers.tools.unified_tool_handler import UnifiedToolHandler
from plugins_func.loadplugins import auto_import_modules
from plugins_func.register import Action, ActionResponse
from core.auth import AuthenticationError
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType
from config.logger import setup_logging, build_module_string, create_connection_logger
from core.utils.prompt_manager import PromptManager
from core.utils.voiceprint_provider import VoiceprintProvider
from core.utils.util import get_system_error_response
from core.xiaoxin.runtime import normalize_xiaoxin_user_text
from core.xiaoxin.companion import build_companion_subject_context
from core.xiaoxin.compliance import Capability
from core.xiaoxin.semantic_router import is_existing_tool_turn
from core.xiaoxin.tts_delivery import TtsAckResult, TtsAttemptError
from core.utils import textUtils


TAG = __name__

TTS_PHASE_READY_WAIT = "READY_WAIT"
TTS_PHASE_STREAMING = "STREAMING"
TTS_PHASE_DONE_WAIT = "DONE_WAIT"
TTS_PHASE_TERMINAL = "TERMINAL"

auto_import_modules("plugins_func.functions")


class TTSException(RuntimeError):
    pass


@dataclass(frozen=True)
class ControlTextChatResult:
    event_id: str
    sentence_id: str
    submitted_at: str
    assistant_text: str | None
    tts_outcome: str
    tts_reason: str | None = None


# direct_answer 虚拟工具定义
# 不是真实工具，是路由机制：将"调不调工具"的二选一变为"调哪个"的多选，防止小模型误触发真实工具
DIRECT_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "direct_answer",
        "description": "当用户的请求不匹配其他任何工具时，可用此选项直接回复。将回复内容写在response参数里。",
        "parameters": {
            "type": "object",
            "properties": {
                "response": {
                    "type": "string",
                    "description": "你回复用户的完整内容",
                },
            },
            "required": ["response"],
        },
    },
}


class ConnectionHandler:
    def __init__(
        self,
        config: Dict[str, Any],
        _vad,
        _asr,
        _llm,
        _memory,
        _intent,
        server=None,
    ):
        self.config = copy.deepcopy(config)
        self.session_id = str(uuid.uuid4())
        self.logger = setup_logging()
        self.server = server  # 保存server实例的引用
        self.xiaoxin_control_runtime = getattr(server, "xiaoxin_runtime", None)
        self.xiaoxin_control_tts_deliveries = {}
        self.need_bind = False  # 是否需要绑定设备
        self.bind_completed_event = asyncio.Event()
        self.bind_code = None  # 绑定设备的验证码
        self.last_bind_prompt_time = 0  # 上次播放绑定提示的时间戳(秒)
        self.bind_prompt_interval = 60  # 绑定提示播放间隔(秒)


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
        self.memory = _memory
        self.intent = _intent
        self.xiaoxin_runtime = None
        self.companion_subject_context = None

        # 为每个连接单独管理声纹识别
        self.voiceprint_provider = None

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
        self.current_speaker = None  # 存储当前说话人

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
        self.func_handler = None

        self.cmd_exit = self.config["exit_commands"]

        # 是否在聊天结束后关闭连接
        self.close_after_chat = False
        self.load_function_plugin = False
        self.intent_type = "nointent"

        self.timeout_seconds = (
            int(self.config.get("close_connection_no_voice_time", 120)) + 60
        )  # 在原来第一道关闭的基础上加60秒，进行二道关闭
        self.timeout_task = None

        # {"mcp":true} 表示启用MCP功能
        self.features = None
        self.client_hello_event = asyncio.Event()
        self.device_time_snapshot = None
        self.control_text_chat_busy = False
        self.control_text_chat_sentence_id = None

        # 标记连接是否来自MQTT
        self.conn_from_mqtt_gateway = False

        # 初始化提示词管理器
        self.prompt_manager = PromptManager(self.config, self.logger)

        # 初始化通话状态
        self.calling = False
        # 标记当前是否为来电接听模式
        self.incoming_call = None

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
            if self.xiaoxin_control_runtime and self.device_id:
                note_device_seen = getattr(
                    self.xiaoxin_control_runtime, "note_device_seen", None
                )
                if callable(note_device_seen):
                    note_device_seen(self.device_id)
                transport = (
                    "mqtt_gateway" if self.conn_from_mqtt_gateway else "websocket"
                )
                self.xiaoxin_control_runtime.registry.register_connection(
                    self.device_id, self, transport
                )

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
                active_sentence_ids = [
                    sentence_id
                    for sentence_id, phase in self._tts_attempt_phases.items()
                    if phase != TTS_PHASE_TERMINAL
                    and sentence_id not in self.xiaoxin_control_tts_deliveries
                ]
                sentence_ids = [
                    *self.xiaoxin_control_tts_deliveries,
                    *active_sentence_ids,
                ]
                for sentence_id in sentence_ids:
                    self.mark_xiaoxin_control_tts_failed(
                        sentence_id, "connection_closed_before_done"
                    )
                if self.xiaoxin_control_runtime and self.device_id:
                    self.xiaoxin_control_runtime.registry.unregister_connection(
                        self.device_id, self
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
        """保存记忆并关闭连接"""
        try:
            if self.memory and self._device_capability_allowed(
                Capability.COMPANION_MEMORY_WRITE
            ):
                # 使用线程池异步保存记忆
                def save_memory_task():
                    try:
                        # 创建新事件循环（避免与主循环冲突）
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            self.memory.save_memory(
                                self.dialogue.dialogue, self.session_id
                            )
                        )
                    except Exception as e:
                        self.logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
                    finally:
                        try:
                            loop.close()
                        except Exception:
                            pass

                # 启动线程保存记忆，不等待完成
                threading.Thread(target=save_memory_task, daemon=True).start()
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
        finally:
            # 立即关闭连接，不等待记忆保存完成
            try:
                await self.close(ws)
            except Exception as close_error:
                self.logger.bind(tag=TAG).error(
                    f"保存记忆后关闭连接失败: {close_error}"
                )

    async def _discard_message_with_bind_prompt(self):
        """丢弃消息并检查是否需要播放绑定提示"""
        current_time = time.time()
        # 检查是否需要播放绑定提示
        if current_time - self.last_bind_prompt_time >= self.bind_prompt_interval:
            self.last_bind_prompt_time = current_time
            # 复用现有的绑定提示逻辑
            from core.handle.receiveAudioHandle import check_bind_device

            asyncio.create_task(check_bind_device(self))

    async def _route_message(self, message):
        """消息路由"""
        # 检查是否已经获取到真实的绑定状态
        if not self.bind_completed_event.is_set():
            # 还没有获取到真实状态，等待直到获取到真实状态或超时
            try:
                await asyncio.wait_for(self.bind_completed_event.wait(), timeout=1)
            except asyncio.TimeoutError:
                # 超时仍未获取到真实状态，丢弃消息
                await self._discard_message_with_bind_prompt()
                return

        # 已经获取到真实状态，检查是否需要绑定
        if self.need_bind:
            # 需要绑定，丢弃消息
            await self._discard_message_with_bind_prompt()
            return

        # 不需要绑定，继续处理消息

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
            if self.need_bind:
                self.bind_completed_event.set()
                return
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

            # 初始化声纹识别
            self._initialize_voiceprint()
            # 打开语音识别通道
            asyncio.run_coroutine_threadsafe(
                self.asr.open_audio_channels(self), self.loop
            )

            """加载记忆"""
            self._initialize_memory()
            """加载意图识别"""
            self._initialize_intent()
            self._init_xiaoxin_runtime()
            """更新系统提示词"""
            self._init_prompt_enhancement()
            """注入工具调用few-shot示例（仅function_call模式）"""
            self._inject_tool_call_fewshot()

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

    def _init_xiaoxin_runtime(self):
        runtime_cfg = self.config.get("xiaoxin_runtime", {})
        if not runtime_cfg.get("enabled", False):
            self.xiaoxin_runtime = None
            return

        from config.config_loader import get_project_dir
        from core.xiaoxin.runtime import XiaoxinRuntime
        from core.xiaoxin.types import XiaoxinConfig

        cfg = XiaoxinConfig.from_dict(runtime_cfg, get_project_dir())
        companion_mind = getattr(
            self.xiaoxin_control_runtime,
            "companion_mind",
            None,
        )
        if companion_mind is None:
            from core.xiaoxin.companion import CompanionMind

            companion_mind = CompanionMind()
            self.logger.bind(tag=TAG).warning(
                "Xiaoxin control runtime has no CompanionMind; "
                "private companion persistence is disabled for this connection"
            )
        self.xiaoxin_runtime = XiaoxinRuntime(
            cfg,
            companion_mind=companion_mind,
        )

    def _try_xiaoxin_turn(self, query: str, current_sentence_id: str) -> bool:
        if self.xiaoxin_runtime is None or not query:
            return False
        if is_existing_tool_turn(query):
            return False

        history = self.dialogue.get_llm_dialogue()
        if (
            history
            and history[-1].get("role") == "user"
            and history[-1].get("content") == query
        ):
            history = history[:-1]
        history = history[-8:]
        self.companion_subject_context = None
        trusted_student_profile = None
        owner_user_id = None
        if (
            self.xiaoxin_control_runtime is not None
            and getattr(self.xiaoxin_control_runtime, "identity_resolver", None)
            is not None
        ):
            try:
                identity = (
                    self.xiaoxin_control_runtime.identity_resolver.resolve_turn_subject(
                        self.device_id or self.session_id,
                        self.current_speaker,
                        self.session_id,
                    )
                )
                personal_memory_allowed = (
                    identity.subject_kind == "user_speaker"
                    and identity.owner_user_id is not None
                )
                owner_user_id = identity.owner_user_id
                identity_store = getattr(
                    self.xiaoxin_control_runtime,
                    "identity_store",
                    None,
                )
                if identity_store is not None and personal_memory_allowed:
                    pet = identity_store.get_personal_pet_for_user(
                        identity.owner_user_id
                    )
                    profile = identity_store.get_student_profile_for_user(
                        identity.owner_user_id
                    )
                    if isinstance(profile, dict):
                        trusted_student_profile = profile
                    if pet is not None:
                        self.companion_subject_context = (
                            build_companion_subject_context(
                                owner_user_id=identity.owner_user_id,
                                pet_id=pet.id,
                                memory_subject_id=identity.memory_subject_id,
                                subject_kind=identity.subject_kind,
                                raw_grade=(profile or {}).get("grade"),
                            )
                        )
            except Exception as exc:
                logging.getLogger(__name__).exception(
                    "Xiaoxin identity resolution failed"
                )
                self.logger.bind(tag=TAG).error(
                    f"Xiaoxin identity resolution failed: {exc}"
                )
        compliance_service = getattr(
            self.xiaoxin_control_runtime,
            "compliance_service",
            None,
        )
        if compliance_service is not None:
            if not owner_user_id:
                return self._complete_compliance_denied_turn(current_sentence_id)
            chat_decision = compliance_service.require_capability(
                owner_user_id,
                Capability.COMPANION_CHAT,
            )
            if not chat_decision.allowed:
                return self._complete_compliance_denied_turn(
                    current_sentence_id,
                    decision=chat_decision,
                )
            memory_decision = compliance_service.require_capability(
                owner_user_id,
                Capability.COMPANION_MEMORY_READ,
            )
            if (
                self.companion_subject_context is not None
                and not memory_decision.allowed
            ):
                self.companion_subject_context = replace(
                    self.companion_subject_context,
                    persistence_allowed=False,
                )
        try:
            result = self.xiaoxin_runtime.handle_turn(
                user_id=self.device_id or self.session_id,
                user_text=query,
                history=history,
                llm=self.llm,
                session_id=self.session_id,
                speaker=self.current_speaker,
                device_time_snapshot=self.device_time_snapshot,
                turn_id=current_sentence_id,
                companion_subject_context=self.companion_subject_context,
                trusted_student_profile=trusted_student_profile,
            )
        except Exception as exc:
            logging.getLogger(__name__).exception("Xiaoxin runtime failed")
            self.logger.bind(tag=TAG).error(f"Xiaoxin runtime failed: {exc}")
            reply = get_system_error_response(self.config)
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
        if not result.handled:
            return False

        reply = result.reply or get_system_error_response(self.config)
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

    def _complete_compliance_denied_turn(
        self,
        current_sentence_id: str,
        *,
        decision=None,
    ) -> bool:
        if decision is not None and decision.reason == "service_tool_only":
            reply = (
                "陪伴服务当前暂未开放。我仍可以帮你查询天气、课程和待办，"
                "或执行设备控制。"
            )
        elif decision is not None and not decision.status.required_actions:
            reply = (
                "当前账号只开放工具能力。我仍可以帮你查询天气、课程和待办，"
                "或执行设备控制。"
            )
        else:
            reply = (
                "当前账号尚未完成陪伴服务设置。我仍可以帮你查询天气、课程和待办，"
                "或执行设备控制；请先在小程序合规中心完成设置。"
            )
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

    def _device_capability_allowed(self, capability: Capability) -> bool:
        control_runtime = self.xiaoxin_control_runtime
        compliance_service = getattr(control_runtime, "compliance_service", None)
        if compliance_service is None:
            return True
        identity_store = getattr(control_runtime, "identity_store", None)
        device = (
            identity_store.get_device_by_device_id(self.device_id)
            if identity_store is not None and self.device_id
            else None
        )
        owner_user_id = getattr(device, "owner_user_id", None)
        if not owner_user_id:
            return False
        return compliance_service.require_capability(
            owner_user_id,
            capability,
        ).allowed

    def _inject_tool_call_fewshot(self):
        """注入工具调用 few-shot 示例到对话历史。
        结构：正样本（工具调用示例）放在动态 system 之前，可命中前缀缓存；
        负样本（直接回答示例）放在动态 system 之后、紧挨真实用户消息，
        确保模型在处理用户消息前最后看到的是"不调工具"的行为模式。
        """
        if self.intent_type != "function_call":
            return
        if not hasattr(self, "func_handler") or self.func_handler is None:
            return

        tools = self.func_handler.get_functions()
        if not tools:
            return

        tool_names = {t.get("function", {}).get("name") for t in tools}

        # === few-shot 示例（is_temporary）===
        # 展示 direct_answer 携带 response 参数的用法，一次调用完成回复

        # 示例1：direct_answer（回复内容写在 response 参数里，无需递归）
        da_tc_id = "fewshot_da_001"
        self.dialogue.put(
            Message(role="user", content="给我讲个故事吧", is_temporary=True)
        )
        self.dialogue.put(
            Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": da_tc_id,
                        "function": {
                            "arguments": '{"response": "好呀，你想听什么类型的呀？童话、冒险还是搞笑的？选一个我给你开讲~"}',
                            "name": "direct_answer",
                        },
                        "type": "function",
                        "index": 0,
                    }
                ],
                is_temporary=True,
            )
        )
        self.dialogue.put(
            Message(
                role="tool",
                tool_call_id=da_tc_id,
                content="已直接回复",
                is_temporary=True,
            )
        )

        # 示例2：真实工具调用（handle_exit_intent）
        if "handle_exit_intent" in tool_names:
            tc_id = "fewshot_exit_001"
            self.dialogue.put(Message(role="user", content="拜拜", is_temporary=True))
            self.dialogue.put(
                Message(
                    role="assistant",
                    tool_calls=[
                        {
                            "id": tc_id,
                            "function": {
                                "arguments": '{"say_goodbye": "再见，下次再聊~"}',
                                "name": "handle_exit_intent",
                            },
                            "type": "function",
                            "index": 0,
                        }
                    ],
                    is_temporary=True,
                )
            )
            self.dialogue.put(
                Message(
                    role="tool",
                    tool_call_id=tc_id,
                    content="退出意图已处理",
                    is_temporary=True,
                )
            )
            self.dialogue.put(
                Message(
                    role="assistant",
                    content="再见，下次再聊~",
                    is_temporary=True,
                )
            )

        self.logger.bind(tag=TAG).debug("已注入工具调用 few-shot 示例")

    def _initialize_tts(self):
        """初始化TTS"""
        tts = None
        if not self.need_bind:
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

    def _initialize_voiceprint(self):
        """为当前连接初始化声纹识别"""
        try:
            voiceprint_config = self.config.get("voiceprint", {})
            if voiceprint_config:
                identity_store = getattr(self.xiaoxin_control_runtime, "identity_store", None)

                def speaker_resolver(device_id):
                    if identity_store is None or not device_id:
                        return []
                    device = identity_store.get_device_by_device_id(device_id)
                    if device is None or not device.owner_user_id:
                        return []
                    return [
                        (speaker.speaker_key, speaker.display_name)
                        for speaker in identity_store.list_speakers_for_device(
                            device.owner_user_id, device_id
                        )
                        if speaker.status != "archived"
                        and speaker.speaker_key.startswith("xiaoxin_")
                    ]

                voiceprint_provider = VoiceprintProvider(
                    voiceprint_config,
                    speaker_resolver=speaker_resolver,
                )
                if voiceprint_provider is not None and voiceprint_provider.enabled:
                    self.voiceprint_provider = voiceprint_provider
                    self.logger.bind(tag=TAG).info("声纹识别功能已在连接时动态启用")
                else:
                    self.logger.bind(tag=TAG).warning("声纹识别功能启用但配置不完整")
            else:
                self.logger.bind(tag=TAG).info("声纹识别功能未启用")
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"声纹识别初始化失败: {str(e)}")

    async def _background_initialize(self):
        """Initialize local connection components without blocking the event loop."""
        try:
            self.bind_completed_event.set()
            self.executor.submit(self._initialize_components)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Background initialization failed: {e}")

    def _initialize_memory(self):
        if self.memory is None:
            return
        """初始化记忆模块"""
        self.memory.init_memory(
            role_id=self.device_id,
            llm=self.llm,
            summary_memory=self.config.get("summaryMemory", None),
            save_to_file=True,
        )

        # 获取记忆总结配置
        memory_config = self.config["Memory"]
        memory_type = self.config["Memory"][self.config["selected_module"]["Memory"]][
            "type"
        ]
        # 如果使用 nomen 或 mem_report_only，直接返回
        if memory_type == "nomem" or memory_type == "mem_report_only":
            return
        # 使用 mem_local_short 模式
        elif memory_type == "mem_local_short":
            memory_llm_name = memory_config[self.config["selected_module"]["Memory"]][
                "llm"
            ]
            if memory_llm_name and memory_llm_name in self.config["LLM"]:
                # 如果配置了专用LLM，则创建独立的LLM实例
                from core.utils import llm as llm_utils

                memory_llm_config = self.config["LLM"][memory_llm_name]
                memory_llm_type = memory_llm_config.get("type", memory_llm_name)
                memory_llm = llm_utils.create_instance(
                    memory_llm_type, memory_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"为记忆总结创建了专用LLM: {memory_llm_name}, 类型: {memory_llm_type}"
                )
                self.memory.set_llm(memory_llm)
            else:
                # 否则使用主LLM
                self.memory.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM作为意图识别模型")

    def _initialize_intent(self):
        if self.intent is None:
            return
        self.intent_type = self.config["Intent"][
            self.config["selected_module"]["Intent"]
        ]["type"]
        if self.intent_type == "function_call" or self.intent_type == "intent_llm":
            self.load_function_plugin = True
        """初始化意图识别模块"""
        # 获取意图识别配置
        intent_config = self.config["Intent"]
        intent_type = self.config["Intent"][self.config["selected_module"]["Intent"]][
            "type"
        ]

        # 如果使用 nointent，直接返回
        if intent_type == "nointent":
            return
        # 使用 intent_llm 模式
        elif intent_type == "intent_llm":
            intent_llm_name = intent_config[self.config["selected_module"]["Intent"]][
                "llm"
            ]

            if intent_llm_name and intent_llm_name in self.config["LLM"]:
                # 如果配置了专用LLM，则创建独立的LLM实例
                from core.utils import llm as llm_utils

                intent_llm_config = self.config["LLM"][intent_llm_name]
                intent_llm_type = intent_llm_config.get("type", intent_llm_name)
                intent_llm = llm_utils.create_instance(
                    intent_llm_type, intent_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"为意图识别创建了专用LLM: {intent_llm_name}, 类型: {intent_llm_type}"
                )
                self.intent.set_llm(intent_llm)
            else:
                # 否则使用主LLM
                self.intent.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM作为意图识别模型")

        """加载统一工具处理器"""
        self.func_handler = UnifiedToolHandler(self)

        # 异步初始化工具处理器
        if hasattr(self, "loop") and self.loop:
            asyncio.run_coroutine_threadsafe(self.func_handler._initialize(), self.loop)

    def change_system_prompt(self, prompt):
        self.prompt = prompt
        # 更新系统prompt至上下文
        self.dialogue.update_system_message(self.prompt)

    def chat(self, query, depth=0, sentence_id=None):
        # 保存当前任务的sentence_id到局部变量，避免被新任务覆盖
        current_sentence_id = None

        if query is not None and self.xiaoxin_runtime is not None:
            query = normalize_xiaoxin_user_text(query)

        if query is not None:
            self.logger.bind(tag=TAG).info(f"大模型收到用户消息: {query}")

        # 为最顶层时新建会话ID和发送FIRST请求
        if depth == 0:
            current_sentence_id = sentence_id or str(uuid.uuid4().hex)
            self.sentence_id = current_sentence_id  # 更新共享属性
            self.dialogue.put(Message(role="user", content=query))
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.FIRST,
                    content_type=ContentType.ACTION,
                )
            )
        else:
            # 递归调用时，使用当前的sentence_id
            current_sentence_id = self.sentence_id

        if self._try_xiaoxin_turn(query, current_sentence_id):
            if depth == 0:
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=current_sentence_id,
                        sentence_type=SentenceType.LAST,
                        content_type=ContentType.ACTION,
                    )
                )
            return True

        # 设置最大递归深度，避免无限循环，可根据实际需求调整
        MAX_DEPTH = 5
        force_final_answer = False  # 标记是否强制最终回答

        if depth >= MAX_DEPTH:
            self.logger.bind(tag=TAG).debug(
                f"已达到最大工具调用深度 {MAX_DEPTH}，将强制基于现有信息回答"
            )
            force_final_answer = True
            # 添加系统指令，要求 LLM 基于现有信息回答
            self.dialogue.put(
                Message(
                    role="user",
                    content="[系统提示] 已达到最大工具调用次数限制，请你基于目前已经获取的所有信息，直接给出最终答案。不要再尝试调用任何工具。",
                )
            )

        # Define intent functions
        functions = None
        # 达到最大深度时，禁用工具调用，强制 LLM 直接回答
        if (
            self.intent_type == "function_call"
            and hasattr(self, "func_handler")
            and not force_final_answer
        ):
            functions = list(self.func_handler.get_functions())
            # 仅在第一层调用时注入 direct_answer 虚拟工具
            # 递归调用（depth>0）不注入，避免模型在生成文本回复时再次调 direct_answer 导致循环
            if functions is not None and depth == 0:
                functions.append(DIRECT_ANSWER_TOOL)

        response_message = []

        try:
            # 使用带记忆的对话
            memory_str = None
            # 仅当query非空（代表用户询问）时查询记忆
            if (
                self.memory is not None
                and query
                and not is_existing_tool_turn(query)
                and self._device_capability_allowed(
                    Capability.COMPANION_MEMORY_READ
                )
            ):
                future = asyncio.run_coroutine_threadsafe(
                    self.memory.query_memory(query), self.loop
                )
                memory_str = future.result()

            if self.intent_type == "function_call" and functions is not None:
                # 使用支持functions的streaming接口
                llm_responses = self.llm.response_with_functions(
                    self.session_id,
                    self.dialogue.get_llm_dialogue_with_memory(
                        memory_str, self.config.get("voiceprint", {})
                    ),
                    functions=functions,
                )
            else:
                llm_responses = self.llm.response(
                    self.session_id,
                    self.dialogue.get_llm_dialogue_with_memory(
                        memory_str, self.config.get("voiceprint", {})
                    ),
                )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"LLM 处理出错 {query}: {e}")
            return None

        # 处理流式响应
        tool_call_flag = False
        # 支持多个并行工具调用 - 使用列表存储
        tool_calls_list = []  # 格式: [{"id": "", "name": "", "arguments": ""}]
        content_arguments = ""
        emotion_flag = True
        try:
            for response in llm_responses:
                if self.client_abort:
                    break
                if self.intent_type == "function_call" and functions is not None:
                    content, tools_call = response
                    if "content" in response:
                        content = response["content"]
                        tools_call = None
                    if content is not None and len(content) > 0:
                        content_arguments += content

                    if not tool_call_flag and content_arguments.startswith(
                        "<tool_call>"
                    ):
                        # print("content_arguments", content_arguments)
                        tool_call_flag = True

                    if tools_call is not None and len(tools_call) > 0:
                        tool_call_flag = True
                        self._merge_tool_calls(tool_calls_list, tools_call)

                    # 流式提取 direct_answer 的 response 参数，实时送 TTS
                    # 使用安全缓冲区，防止 JSON 闭合符号泄漏到 TTS
                    _DA_STREAM_BUFFER = 5
                    for tc in tool_calls_list:
                        if tc["name"] == "direct_answer" and tc.get("arguments"):
                            da_text = self._extract_direct_answer_response(
                                tc["arguments"]
                            )
                            sent_len = tc.get("_da_sent", 0)
                            if da_text and len(da_text) > sent_len:
                                safe_end = max(
                                    sent_len, len(da_text) - _DA_STREAM_BUFFER
                                )
                                if safe_end > sent_len:
                                    new_part = da_text[sent_len:safe_end]
                                    # 清理 delta 中可能泄漏的 JSON 闭合垃圾
                                    new_part = self._clean_response_garbage(new_part)
                                    if new_part:
                                        tc["_da_sent"] = safe_end
                                        self.tts.tts_text_queue.put(
                                            TTSMessageDTO(
                                                sentence_id=current_sentence_id,
                                                sentence_type=SentenceType.MIDDLE,
                                                content_type=ContentType.TEXT,
                                                content_detail=new_part,
                                            )
                                        )
                else:
                    content = response

                # 在llm回复中获取情绪表情，一轮对话只在开头获取一次
                if emotion_flag and content is not None and content.strip():
                    if (self.features or {}).get("emoji", True):
                        asyncio.run_coroutine_threadsafe(
                            textUtils.get_emotion(self, content),
                            self.loop,
                        )
                    emotion_flag = False

                if content is not None and len(content) > 0:
                    if not tool_call_flag:
                        response_message.append(content)
                        self.tts.tts_text_queue.put(
                            TTSMessageDTO(
                                sentence_id=current_sentence_id,
                                sentence_type=SentenceType.MIDDLE,
                                content_type=ContentType.TEXT,
                                content_detail=content,
                            )
                        )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"LLM stream processing error: {e}")
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.MIDDLE,
                    content_type=ContentType.TEXT,
                    content_detail=get_system_error_response(self.config),
                )
            )
            if depth == 0:
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=current_sentence_id,
                        sentence_type=SentenceType.LAST,
                        content_type=ContentType.ACTION,
                    )
                )
            return
        # 处理function call
        if tool_call_flag:
            bHasError = False
            # 处理基于文本的工具调用格式
            if len(tool_calls_list) == 0 and content_arguments:
                a = extract_json_from_string(content_arguments)
                if a is not None:
                    try:
                        content_arguments_json = json.loads(a)
                        tool_calls_list.append(
                            {
                                "id": str(uuid.uuid4().hex),
                                "name": content_arguments_json["name"],
                                "arguments": json.dumps(
                                    content_arguments_json["arguments"],
                                    ensure_ascii=False,
                                ),
                            }
                        )
                    except Exception as e:
                        bHasError = True
                        response_message.append(a)
                else:
                    bHasError = True
                    response_message.append(content_arguments)
                if bHasError:
                    self.logger.bind(tag=TAG).error(
                        f"function call error: {content_arguments}"
                    )

            if not bHasError and len(tool_calls_list) > 0:
                # 处理 direct_answer 虚拟工具
                direct_answer_calls = [
                    tc for tc in tool_calls_list if tc["name"] == "direct_answer"
                ]
                real_tool_calls = [
                    tc for tc in tool_calls_list if tc["name"] != "direct_answer"
                ]

                if direct_answer_calls:
                    self.logger.bind(tag=TAG).debug(
                        f"模型选择 direct_answer，流式已播报，写入对话历史"
                    )
                    for tc in direct_answer_calls:
                        da_response = self._extract_direct_answer_response(
                            tc.get("arguments", "{}")
                        )
                        if da_response:
                            # 刷新流式缓冲区中未发送的部分
                            sent_len = tc.get("_da_sent", 0)
                            remaining = da_response[sent_len:]
                            if remaining:
                                remaining = self._clean_response_garbage(remaining)
                                if remaining:
                                    self.tts.tts_text_queue.put(
                                        TTSMessageDTO(
                                            sentence_id=current_sentence_id,
                                            sentence_type=SentenceType.MIDDLE,
                                            content_type=ContentType.TEXT,
                                            content_detail=remaining,
                                        )
                                    )
                            # 写入对话历史
                            da_response = self._clean_response_garbage(da_response)
                            self.tts.store_tts_text(current_sentence_id, da_response)
                            self.dialogue.put(
                                Message(role="assistant", content=da_response)
                            )

                    if not real_tool_calls:
                        if depth == 0:
                            self.tts.tts_text_queue.put(
                                TTSMessageDTO(
                                    sentence_id=current_sentence_id,
                                    sentence_type=SentenceType.LAST,
                                    content_type=ContentType.ACTION,
                                )
                            )
                        return

                    tool_calls_list = real_tool_calls

            if not bHasError and len(tool_calls_list) > 0:
                self.logger.bind(tag=TAG).debug(
                    f"检测到 {len(tool_calls_list)} 个工具调用"
                )

                # LLM 流式阶段已播报过的文本
                streamed_text = ""
                if len(response_message) > 0:
                    streamed_text = "".join(response_message)
                    self.tts.store_tts_text(current_sentence_id, streamed_text)
                    self.dialogue.put(Message(role="assistant", content=streamed_text))
                response_message.clear()

                # 收集所有工具调用的 Future
                futures_with_data = []
                for tool_call_data in tool_calls_list:
                    self.logger.bind(tag=TAG).debug(
                        f"function_name={tool_call_data['name']}, function_id={tool_call_data['id']}, function_arguments={tool_call_data['arguments']}"
                    )

                    future = asyncio.run_coroutine_threadsafe(
                        self.func_handler.handle_llm_function_call(
                            self, tool_call_data
                        ),
                        self.loop,
                    )
                    futures_with_data.append((future, tool_call_data))

                tool_call_timeout = int(self.config.get("tool_call_timeout", 30))
                tool_results = []

                for future, tool_call_data in futures_with_data:
                    try:
                        result = future.result(timeout=tool_call_timeout)
                        tool_results.append((result, tool_call_data))
                    except Exception as e:
                        self.logger.bind(tag=TAG).error(
                            f"Tool call timed out or failed: {tool_call_data['name']}, error: {e}"
                        )
                        tool_results.append(
                            (
                                ActionResponse(
                                    action=Action.ERROR,
                                    result="哎呀，网络遇到点问题，请稍后再试一下！",
                                ),
                                tool_call_data,
                            )
                        )

                if tool_results:
                    self._handle_function_result(
                        tool_results, depth=depth, streamed_text=streamed_text
                    )

        # 存储对话内容
        if len(response_message) > 0:
            text_buff = "".join(response_message)
            self.tts.store_tts_text(current_sentence_id, text_buff)
            self.dialogue.put(Message(role="assistant", content=text_buff))

        if depth == 0:
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )
            # 使用lambda延迟计算，只有在DEBUG级别时才执行get_llm_dialogue()
            self.logger.bind(tag=TAG).debug(
                lambda: json.dumps(
                    self.dialogue.get_llm_dialogue(), indent=4, ensure_ascii=False
                )
            )

        return True

    def _handle_function_result(self, tool_results, depth, streamed_text=""):
        need_llm_tools = []
        record_tools = []

        for result, tool_call_data in tool_results:
            if result.action in [
                Action.RESPONSE,
                Action.NOTFOUND,
                Action.ERROR,
            ]:
                text = result.response if result.response else result.result
                if streamed_text and text in streamed_text:
                    self.logger.bind(tag=TAG).debug(
                        f"Skipping duplicate TTS for tool {tool_call_data['name']}, already streamed"
                    )
                else:
                    self.tts.tts_one_sentence(
                        self, ContentType.TEXT, content_detail=text
                    )
                    self.tts.store_tts_text(self.sentence_id, text)
                self.dialogue.put(Message(role="assistant", content=text))
            elif result.action == Action.REQLLM:
                need_llm_tools.append((result, tool_call_data))
            elif result.action == Action.RECORD:
                record_tools.append((result, tool_call_data))
            else:
                pass

        # Action.RECORD：写入完整工具调用链（assistant(tool_calls) → tool(result) → assistant(response)）
        # 模型从历史中学到工具调用模式，不额外调用LLM
        if record_tools:
            # 构造 assistant 消息（含 tool_calls），记录"模型调用了哪些工具"
            all_tool_calls = [
                {
                    "id": tool_call_data["id"],
                    "function": {
                        "arguments": (
                            "{}"
                            if tool_call_data["arguments"] == ""
                            else tool_call_data["arguments"]
                        ),
                        "name": tool_call_data["name"],
                    },
                    "type": "function",
                    "index": idx,
                }
                for idx, (_, tool_call_data) in enumerate(record_tools)
            ]
            self.dialogue.put(Message(role="assistant", tool_calls=all_tool_calls))

            # 写入每条工具的执行结果，记录"工具返回了什么"
            for result, tool_call_data in record_tools:
                text = result.result or ""
                self.dialogue.put(
                    Message(
                        role="tool",
                        tool_call_id=(
                            str(uuid.uuid4())
                            if tool_call_data["id"] is None
                            else tool_call_data["id"]
                        ),
                        content=text,
                    )
                )

            # 用固定文本作为最终回复，补全标准三段式，保证下一条消息是 user 而非接 tool
            response_parts = []
            for result, _ in record_tools:
                resp = result.response or result.result
                if resp:
                    response_parts.append(resp)
            if response_parts:
                self.dialogue.put(
                    Message(role="assistant", content="，".join(response_parts))
                )

        if need_llm_tools:
            all_tool_calls = [
                {
                    "id": tool_call_data["id"],
                    "function": {
                        "arguments": (
                            "{}"
                            if tool_call_data["arguments"] == ""
                            else tool_call_data["arguments"]
                        ),
                        "name": tool_call_data["name"],
                    },
                    "type": "function",
                    "index": idx,
                }
                for idx, (_, tool_call_data) in enumerate(need_llm_tools)
            ]
            self.dialogue.put(Message(role="assistant", tool_calls=all_tool_calls))

            for result, tool_call_data in need_llm_tools:
                text = result.result
                if text is not None and len(text) > 0:
                    self.dialogue.put(
                        Message(
                            role="tool",
                            tool_call_id=(
                                str(uuid.uuid4())
                                if tool_call_data["id"] is None
                                else tool_call_data["id"]
                            ),
                            content=text,
                        )
                    )

            self.chat(None, depth=depth + 1)

    def clearSpeakStatus(self):
        self.client_is_speaking = False
        self.logger.bind(tag=TAG).debug(f"清除服务端讲话状态")

    def supports_tts_ready_ack(self) -> bool:
        return (self.features or {}).get("tts_ready_ack") is True

    def supports_tts_done_ack(self) -> bool:
        return (self.features or {}).get("tts_done_ack") is True

    def supports_tts_preroll_buffer(self) -> bool:
        return (self.features or {}).get("tts_preroll_buffer") is True

    def supports_reliable_notification_tts(self) -> bool:
        return (
            self.supports_tts_ready_ack()
            and self.supports_tts_done_ack()
            and self.supports_tts_preroll_buffer()
        )

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

            # 清理工具处理器资源
            if hasattr(self, "func_handler") and self.func_handler:
                try:
                    await self.func_handler.cleanup()
                except Exception as cleanup_error:
                    self.logger.bind(tag=TAG).error(
                        f"清理工具处理器时出错: {cleanup_error}"
                    )

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

    def chat_and_close(self, text):
        """Chat with the user and then close the connection"""
        try:
            # Use the existing chat method
            self.chat(text)

            # After chat is complete, close the connection
            self.close_after_chat = True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Chat and close error: {str(e)}")

    async def submit_control_text_chat(
        self,
        text: str,
        *,
        speaker: str | None = None,
        simulated_as_of: datetime | None = None,
        await_tts_terminal: bool = False,
        evaluation_run_id: str | None = None,
        evaluation_case_id: str | None = None,
    ) -> ControlTextChatResult:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("text is empty")
        if len(clean_text) > 500:
            raise ValueError("text is too long")
        if self.control_text_chat_busy:
            raise RuntimeError("text chat busy")

        self.control_text_chat_busy = True
        sentence_id = uuid.uuid4().hex
        submitted_at = datetime.now().astimezone().isoformat()
        original_time_provider = None
        try:
            if simulated_as_of is not None:
                if self.xiaoxin_runtime is None:
                    raise RuntimeError("xiaoxin runtime is not available")
                original_time_provider = self.xiaoxin_runtime.time_provider
                self.xiaoxin_runtime.time_provider = lambda: simulated_as_of
            if speaker is not None:
                self.current_speaker = speaker
            try:
                await asyncio.wait_for(self.client_hello_event.wait(), timeout=8)
            except asyncio.TimeoutError as exc:
                self.logger.bind(tag=TAG).warning(
                    "client hello was not ready before control text chat"
                )
                raise RuntimeError("client hello is not ready") from exc
            reliable_mode = self.supports_reliable_notification_tts()
            if isinstance(self.features, dict) and self.features.get("mcp"):
                mcp_client = getattr(self, "mcp_client", None)
                if mcp_client is None:
                    raise RuntimeError("device MCP client is not initialized")
                try:
                    await mcp_client.wait_until_ready(timeout_seconds=8)
                except asyncio.TimeoutError as exc:
                    self.logger.bind(tag=TAG).warning(
                        "device MCP tools were not ready before control text chat"
                    )
                    raise RuntimeError("device MCP tools are not ready") from exc
            if reliable_mode:
                await self._wait_until_tts_ready(timeout_seconds=5)
                await self._quiesce_audio_for_reliable_tts()
            self.reset_audio_states()
            self.client_abort = False
            self.control_text_chat_sentence_id = sentence_id
            if reliable_mode:
                await self._start_reliable_tts(sentence_id)
            await asyncio.to_thread(
                self.chat,
                clean_text,
                sentence_id=sentence_id,
            )
            tts_outcome = "not_waited"
            tts_reason = None
            if await_tts_terminal:
                if not reliable_mode:
                    tts_outcome = "unsupported"
                    tts_reason = "device_does_not_support_reliable_tts"
                else:
                    terminal = await self.wait_for_tts_terminal(sentence_id)
                    if (
                        terminal.state == "error"
                        and terminal.reason == "done_timeout"
                    ):
                        tts_outcome = "timeout"
                        tts_reason = "done_timeout"
                    else:
                        tts_outcome = terminal.state
                        tts_reason = terminal.reason
            get_tts_text = getattr(self.tts, "get_tts_text", None)
            assistant_text = (
                get_tts_text(sentence_id) if callable(get_tts_text) else None
            )
            result = ControlTextChatResult(
                event_id=sentence_id,
                sentence_id=sentence_id,
                submitted_at=submitted_at,
                assistant_text=assistant_text,
                tts_outcome=tts_outcome,
                tts_reason=tts_reason,
            )
            if evaluation_run_id is not None and evaluation_case_id is not None:
                self.logger.bind(tag="xiaoxin.evaluation").info(
                    json.dumps(
                        {
                            "event": "xiaoxin_evaluation_chat",
                            "evaluation_run_id": evaluation_run_id,
                            "case_id": evaluation_case_id,
                            "event_id": result.event_id,
                            "sentence_id": result.sentence_id,
                            "device_id": self.device_id,
                            "pet_id": getattr(
                                self.companion_subject_context, "pet_id", None
                            ),
                            "memory_subject_id": getattr(
                                self.companion_subject_context,
                                "memory_subject_id",
                                None,
                            ),
                            "tts_outcome": result.tts_outcome,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            return result
        except BaseException:
            if self.control_text_chat_sentence_id == sentence_id:
                self.control_text_chat_sentence_id = None
            raise
        finally:
            if original_time_provider is not None:
                self.xiaoxin_runtime.time_provider = original_time_provider
            self.control_text_chat_busy = False

    async def send_xiaoxin_event(self, payload: Dict[str, Any]) -> None:
        if self.websocket is None:
            raise RuntimeError("websocket is not connected")
        self.logger.bind(tag="xiaoxin.overview_sync").info(
            "websocket send_xiaoxin_event "
            f"type={payload.get('type')} device_id={payload.get('device_id')}"
        )
        await self.websocket.send(json.dumps(payload, ensure_ascii=False))

    async def speak_from_control_console(
        self, text: str, delivery_id: str, sentence_id: str
    ) -> None:
        if not text:
            raise TTSException("control console TTS text is empty")
        if not sentence_id:
            raise TTSException("control console TTS sentence_id is empty")

        await self._wait_until_tts_ready(timeout_seconds=5)

        reliable_mode = self.supports_reliable_notification_tts()
        if reliable_mode:
            await self._quiesce_audio_for_reliable_tts()

        self.sentence_id = sentence_id
        self.xiaoxin_control_tts_deliveries[sentence_id] = delivery_id
        failure_reason = "attempt_failed"
        try:
            if reliable_mode:
                failure_reason = "ready_handshake_failed"
                await self._start_reliable_tts(
                    sentence_id,
                    delivery_id=delivery_id,
                )
            else:
                failure_reason = "start_send_failed"
                await send_tts_message(self, "start", sentence_id=sentence_id)
                self.client_is_speaking = True
                delay_ms = int(self.config.get("wakeup_response_start_delay_ms", 300))
                if delay_ms > 0:
                    failure_reason = "start_delay_failed"
                    await asyncio.sleep(delay_ms / 1000)

            first = TTSMessageDTO(
                sentence_id=sentence_id,
                sentence_type=SentenceType.FIRST,
                content_type=ContentType.ACTION,
            )
            middle = TTSMessageDTO(
                sentence_id=sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT,
                content_detail=text,
            )
            last = TTSMessageDTO(
                sentence_id=sentence_id,
                sentence_type=SentenceType.LAST,
                content_type=ContentType.ACTION,
            )
            terminal_result = self._tts_terminal_results.get(sentence_id)
            if terminal_result is not None:
                raise TtsAttemptError(
                    sentence_id,
                    terminal_result.reason or "device_error",
                )
            failure_reason = "store_tts_text_failed"
            self.tts.store_tts_text(sentence_id, text)
            failure_reason = "tts_queue_publish_failed"
            self.tts.tts_text_queue.put(first)
            self.tts.tts_text_queue.put(middle)
            self.tts.tts_text_queue.put(last)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                outcome_reason = "attempt_cancelled"
            elif isinstance(exc, TtsAttemptError):
                outcome_reason = exc.reason
            else:
                outcome_reason = failure_reason
            self.mark_xiaoxin_control_tts_failed(sentence_id, outcome_reason)
            if self._tts_ack_active_phases.get(sentence_id) == "ready":
                self._retire_tts_ack_phase(sentence_id, "ready")
            if self.sentence_id == sentence_id:
                self.client_is_speaking = False
            raise

    async def _start_reliable_tts(
        self,
        sentence_id: str,
        *,
        delivery_id: str | None = None,
    ) -> None:
        timeout_ms = int(self.config.get("tts_ready_ack_timeout_ms", 700))
        retry_delays_ms = list(
            self.config.get("tts_ready_start_retry_delays_ms", [300, 600, 1200])
        )[:3]
        ready_started_at = time.monotonic()
        for send_index in range(len(retry_delays_ms) + 1):
            self.begin_tts_ack_wait("ready", sentence_id)
            await send_tts_message(self, "start", sentence_id=sentence_id)
            self.client_is_speaking = True
            result = await self.wait_for_tts_ack("ready", sentence_id, timeout_ms)
            if (
                result is not None
                and result.successful
                and result.state == "ready"
                and result.sentence_id == sentence_id
            ):
                if not self.mark_tts_streaming(sentence_id):
                    raise TtsAttemptError(sentence_id, "invalid_ready_phase")
                self.logger.bind(tag="xiaoxin.tts").info(
                    "delivery_id={} sentence_id={} tts_state=streaming "
                    "start_to_ready_ms={}".format(
                        delivery_id,
                        sentence_id,
                        int((time.monotonic() - ready_started_at) * 1000),
                    )
                )
                return
            if (
                result is not None
                and result.state == "error"
                and result.sentence_id == sentence_id
            ):
                raise TtsAttemptError(sentence_id, result.reason or "device_error")
            if send_index < len(retry_delays_ms):
                self.logger.bind(tag="xiaoxin.tts").warning(
                    "tts_state=preparing sentence_id={} ready_retry={} "
                    "failure_reason=ready_timeout".format(
                        sentence_id,
                        send_index + 1,
                    )
                )
                await asyncio.sleep(retry_delays_ms[send_index] / 1000)
        raise TtsAttemptError(sentence_id, "ready_timeout")

    async def _quiesce_audio_for_reliable_tts(self) -> None:
        old_sentence_id = self.sentence_id
        self.client_abort = True

        rate_controller = getattr(self, "audio_rate_controller", None)
        if rate_controller is not None:
            pending_task = getattr(rate_controller, "pending_send_task", None)
            rate_controller.stop_sending()
            if pending_task is not None and not pending_task.done():
                try:
                    await pending_task
                except asyncio.CancelledError:
                    pass
            rate_controller.reset()

        for queue_name in ("tts_text_queue", "tts_audio_queue"):
            pending_queue = getattr(self.tts, queue_name, None)
            if pending_queue is None:
                continue
            while True:
                try:
                    pending_queue.get_nowait()
                except queue.Empty:
                    break

        if old_sentence_id:
            clear_tts_text = getattr(self.tts, "clear_tts_text", None)
            if callable(clear_tts_text):
                clear_tts_text(old_sentence_id)
        reset_stream_state = getattr(self.tts, "reset_stream_state", None)
        if callable(reset_stream_state):
            reset_stream_state()
        if hasattr(self.tts, "tts_audio_first_sentence"):
            self.tts.tts_audio_first_sentence = True
        self.audio_flow_control = {}
        self.client_abort = False

    async def _wait_until_tts_ready(self, timeout_seconds: float) -> None:
        deadline = time.time() + timeout_seconds
        while self.tts is None and time.time() < deadline:
            await asyncio.sleep(0.05)
        if self.tts is None:
            raise TTSException("tts is not ready")

    def _control_delivery_for_sentence(self, sentence_id: str) -> str | None:
        return self.xiaoxin_control_tts_deliveries.get(sentence_id)

    def mark_xiaoxin_control_tts_done(self, sentence_id: str) -> None:
        delivery_id = self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
        if delivery_id and self.xiaoxin_control_runtime:
            self.xiaoxin_control_runtime.dispatcher.mark_tts_done(
                delivery_id, sentence_id
            )
            observe_tts_done = getattr(
                self.xiaoxin_control_runtime,
                "observe_todo_reminder_tts_done",
                None,
            )
            if callable(observe_tts_done):
                observe_tts_done(delivery_id, sentence_id)

    def mark_xiaoxin_control_tts_failed(self, sentence_id: str, reason: str) -> None:
        terminal_result = self._terminalize_tts_attempt_failure(sentence_id, reason)
        if (
            terminal_result.state == "done"
            and terminal_result.sentence_id == sentence_id
        ):
            self.mark_xiaoxin_control_tts_done(sentence_id)
            return
        delivery_id = self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
        if self.sentence_id == sentence_id:
            self.client_abort = True
            self.client_is_speaking = False
        if delivery_id and self.xiaoxin_control_runtime:
            self.xiaoxin_control_runtime.dispatcher.mark_tts_attempt_failed(
                delivery_id, sentence_id, reason
            )

    def mark_xiaoxin_control_tts_legacy_unverified(self, sentence_id: str) -> None:
        delivery_id = self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
        if delivery_id and self.xiaoxin_control_runtime:
            self.xiaoxin_control_runtime.dispatcher.mark_tts_legacy_unverified(
                delivery_id, sentence_id
            )

    async def _check_timeout(self):
        """检查连接超时"""
        try:
            while not self.stop_event.is_set():
                last_activity_time = self.last_activity_time
                if self.need_bind:
                    last_activity_time = self.first_activity_time

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

    @staticmethod
    def _extract_direct_answer_response(arguments_str):
        """从 direct_answer 的参数中提取 response 值。
        优先使用 json.loads 标准解析，流式阶段 fallback 到字符串提取。
        """
        if not arguments_str:
            return ""
        # 优先尝试标准 JSON 解析（适用于完整且格式正确的 JSON）
        try:
            data = json.loads(arguments_str)
            if isinstance(data, dict) and "response" in data:
                return data["response"]
        except (json.JSONDecodeError, TypeError):
            pass
        # Fallback：流式阶段 JSON 可能不完整，使用字符串提取
        marker = '"response": "'
        idx = arguments_str.find(marker)
        if idx < 0:
            marker = '"response":"'
            idx = arguments_str.find(marker)
        if idx < 0:
            return ""
        start = idx + len(marker)
        raw = arguments_str[start:]
        # 去掉末尾的 JSON 闭合符号（如果已完整）
        if raw.endswith('"}'):
            raw = raw[:-2]
        elif raw.endswith('"'):
            raw = raw[:-1]
        # 处理 JSON 转义
        raw = raw.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        return raw

    @staticmethod
    def _clean_response_garbage(text):
        """清理 response 中可能泄漏的 JSON 闭合符号。
        模型有时会在 response 内容中生成 JSON 闭合字符（如 ）"}} 或 '})，
        这些不是故事内容的一部分，需要去除。
        """
        if not text:
            return text
        # 清理独立一行的 JSON 闭合垃圾（如 ）"}}  '}}  "}}  }}  } ）
        _garbage_chars = frozenset("\")'}）")
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if (
                stripped
                and len(stripped) <= 8
                and all(c in _garbage_chars for c in stripped)
            ):
                continue
            cleaned.append(line)
        result = "\n".join(cleaned)
        # 清理末尾残留的 JSON 闭合符号
        result = re.sub(r'["\'}\]]+$', "", result.rstrip()).rstrip()
        return result

    def _merge_tool_calls(self, tool_calls_list, tools_call):
        """合并工具调用列表

        Args:
            tool_calls_list: 已收集的工具调用列表
            tools_call: 新的工具调用
        """
        for tool_call in tools_call:
            tool_index = getattr(tool_call, "index", None)
            if tool_index is None:
                if tool_call.function.name:
                    # 有 function_name，说明是新的工具调用
                    tool_index = len(tool_calls_list)
                else:
                    tool_index = len(tool_calls_list) - 1 if tool_calls_list else 0

            # 确保列表有足够的位置
            if tool_index >= len(tool_calls_list):
                tool_calls_list.append({"id": "", "name": "", "arguments": ""})

            # 更新工具调用信息
            if tool_call.id:
                tool_calls_list[tool_index]["id"] = tool_call.id
            if tool_call.function.name:
                tool_calls_list[tool_index]["name"] = tool_call.function.name
            if tool_call.function.arguments:
                tool_calls_list[tool_index]["arguments"] += tool_call.function.arguments
