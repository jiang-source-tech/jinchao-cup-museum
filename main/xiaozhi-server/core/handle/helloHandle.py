import time
import json
import uuid
import random
import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils.dialogue import Message
from core.utils.util import audio_to_data
from core.providers.tts.dto.dto import SentenceType
from core.utils.wakeup_word import WakeupWordsConfig
from core.handle.sendAudioHandle import sendAudioMessage, send_tts_message
from core.utils.util import remove_punctuation_and_length, opus_datas_to_wav_bytes
from core.providers.tools.device_mcp import MCPClient, send_mcp_initialize_message

TAG = __name__

WAKEUP_CONFIG = {
    "refresh_time": 10,
    "responses": [
        "我一直都在呢，您请说。",
        "在的呢，请随时吩咐我。",
        "来啦来啦，请告诉我吧。",
        "您请说，我正听着。",
        "请您讲话，我准备好了。",
        "请您说出指令吧。",
        "我认真听着呢，请讲。",
        "请问您需要什么帮助？",
        "我在这里，等候您的指令。",
    ],
}

# 创建全局的唤醒词配置管理器
wakeup_words_config = WakeupWordsConfig()

# 用于防止并发调用wakeupWordsResponse的锁
_wakeup_response_lock = asyncio.Lock()


async def handleHelloMessage(conn: "ConnectionHandler", msg_json):
    """处理hello消息"""
    _store_device_time_snapshot(conn, msg_json)
    boot_id, reset_reason = _store_device_status(conn, msg_json)
    audio_params = msg_json.get("audio_params")
    if isinstance(audio_params, dict):
        # The device hello describes the upstream microphone stream. The
        # welcome message describes the server's downstream playback stream.
        # They may legitimately use different sample rates (this device uses
        # 16 kHz input and 24 kHz speaker output), so never echo the complete
        # client object back to the device.
        conn.client_audio_params = dict(audio_params)
        client_format = audio_params.get("format")
        conn.logger.bind(tag=TAG).debug(
            f"客户端音频参数: format={client_format}, "
            f"sample_rate={audio_params.get('sample_rate')}"
        )
        if isinstance(client_format, str) and client_format:
            conn.audio_format = client_format
            server_audio_params = conn.welcome_msg.setdefault("audio_params", {})
            server_audio_params["format"] = client_format
    features = msg_json.get("features")
    if features:
        conn.logger.bind(tag=TAG).debug(f"客户端特性: {features}")
        conn.features = features
        if features.get("mcp"):
            conn.logger.bind(tag=TAG).debug("客户端支持MCP")
            conn.mcp_client = MCPClient()
            # 发送初始化
            asyncio.create_task(send_mcp_initialize_message(conn))

    await conn.websocket.send(json.dumps(conn.welcome_msg))
    hello_event = getattr(conn, "client_hello_event", None)
    if hello_event is not None:
        hello_event.set()
    runtime = getattr(conn, "xiaoxin_control_runtime", None)
    note_device_boot = getattr(runtime, "note_device_boot", None)
    device_id = getattr(conn, "device_id", None)
    if device_id and boot_id and reset_reason and callable(note_device_boot):
        try:
            note_device_boot(
                device_id,
                boot_id=boot_id,
                reset_reason=reset_reason,
            )
        except Exception:
            conn.logger.bind(tag=TAG).exception(
                "Failed to record Xiaoxin boot checkin"
            )


def _store_device_status(
    conn: "ConnectionHandler", msg_json: dict[str, Any]
) -> tuple[str | None, str | None]:
    device_status = msg_json.get("device_status")
    if not isinstance(device_status, dict):
        return None, None

    boot_id = device_status.get("boot_id")
    if isinstance(boot_id, str):
        boot_id = boot_id.strip() or None
    else:
        boot_id = None

    reset_reason = device_status.get("reset_reason")
    if isinstance(reset_reason, str):
        reset_reason = reset_reason.strip().lower() or None
    else:
        reset_reason = None

    runtime = getattr(conn, "xiaoxin_control_runtime", None)
    registry = getattr(runtime, "registry", None)
    update_telemetry = getattr(registry, "update_device_telemetry", None)
    device_id = getattr(conn, "device_id", None)
    if not device_id or not callable(update_telemetry):
        return boot_id, reset_reason

    update_telemetry(
        device_id,
        battery_level=device_status.get("battery_level"),
        battery_percent=device_status.get("battery_percent"),
        firmware_version=device_status.get("firmware_version"),
    )
    return boot_id, reset_reason


def _store_device_time_snapshot(conn: "ConnectionHandler", msg_json: dict[str, Any]) -> None:
    device_time = msg_json.get("device_time")
    if not isinstance(device_time, dict):
        return

    wall_time_ms = device_time.get("wall_time_ms")
    if wall_time_ms is not None and not isinstance(wall_time_ms, (int, float)):
        wall_time_ms = None

    conn.device_time_snapshot = {
        "wall_time_ms": wall_time_ms,
        "sync_status": str(device_time.get("sync_status") or "unknown"),
        "timezone": str(device_time.get("timezone") or ""),
        "source": str(device_time.get("source") or ""),
        "received_at_ms": int(time.time() * 1000),
    }


async def checkWakeupWords(conn: "ConnectionHandler", text):
    enable_wakeup_words_response_cache = conn.config[
        "enable_wakeup_words_response_cache"
    ]

    # 等待tts初始化，最多等待3秒
    start_time = time.time()
    while time.time() - start_time < 3:
        if conn.tts:
            break
        await asyncio.sleep(0.1)
    else:
        return False

    if not enable_wakeup_words_response_cache:
        return False

    _, filtered_text = remove_punctuation_and_length(text)
    if filtered_text not in conn.config.get("wakeup_words"):
        return False

    # 将唤醒词回复视为新会话，提前生成 sentence_id 以便 tts:start 和 ready ACK 对齐
    conn.sentence_id = str(uuid.uuid4().hex)
    ready_ack_supported = (
        hasattr(conn, "supports_tts_ready_ack") and conn.supports_tts_ready_ack()
    )
    if ready_ack_supported:
        conn.begin_tts_ack_wait("ready", conn.sentence_id)

    conn.just_woken_up = True
    await send_tts_message(conn, "start")

    # 获取当前音色
    voice = getattr(conn.tts, "voice", "default")
    if not voice:
        voice = "default"

    # 获取唤醒词回复配置
    response = wakeup_words_config.get_wakeup_response(voice)
    if not response or not response.get("file_path"):
        response = {
            "voice": "default",
            "file_path": "config/assets/wakeup_words_short.wav",
            "time": 0,
            "text": "我在这里哦！",
        }

    # 获取音频数据
    wakeup_response_is_opus = getattr(conn, "audio_format", "opus") != "pcm"
    if hasattr(conn.tts, "wakeup_response_is_opus"):
        wakeup_response_is_opus = conn.tts.wakeup_response_is_opus(conn)

    opus_packets = await audio_to_data(
        response.get("file_path"),
        is_opus=wakeup_response_is_opus,
        use_cache=False,
        sample_rate=conn.sample_rate,
    )
    # 播放唤醒词回复
    conn.client_abort = False

    if ready_ack_supported:
        timeout_ms = int(conn.config.get("tts_ready_ack_timeout_ms", 700))
        await conn.wait_for_tts_ack("ready", conn.sentence_id, timeout_ms)
    else:
        delay_ms = int(conn.config.get("wakeup_response_start_delay_ms", 300))
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

    conn.logger.bind(tag=TAG).info(f"播放唤醒词回复: {response.get('text')}")
    await sendAudioMessage(conn, SentenceType.FIRST, opus_packets, response.get("text"))
    await sendAudioMessage(conn, SentenceType.LAST, [], None)

    # 补充对话
    conn.dialogue.put(Message(role="assistant", content=response.get("text")))

    # 检查是否需要更新唤醒词回复
    if time.time() - response.get("time", 0) > WAKEUP_CONFIG["refresh_time"]:
        if not _wakeup_response_lock.locked():
            asyncio.create_task(wakeupWordsResponse(conn))
    return True


async def wakeupWordsResponse(conn: "ConnectionHandler"):
    if not conn.tts:
        return

    try:
        # 尝试获取锁，如果获取不到就返回
        if not await _wakeup_response_lock.acquire():
            return

        # 从预定义回复列表中随机选择一个回复
        result = random.choice(WAKEUP_CONFIG["responses"])
        if not result or len(result) == 0:
            return

        # 生成TTS音频
        tts_result = await asyncio.to_thread(conn.tts.to_tts, result)
        if not tts_result:
            return

        # 获取当前音色
        voice = getattr(conn.tts, "voice", "default")

        # 使用链接的sample_rate
        wav_bytes = opus_datas_to_wav_bytes(tts_result, sample_rate=conn.sample_rate)
        file_path = wakeup_words_config.generate_file_path(voice)
        with open(file_path, "wb") as f:
            f.write(wav_bytes)
        # 更新配置
        wakeup_words_config.update_wakeup_response(voice, file_path, result)
    finally:
        # 确保在任何情况下都释放锁
        if _wakeup_response_lock.locked():
            _wakeup_response_lock.release()
