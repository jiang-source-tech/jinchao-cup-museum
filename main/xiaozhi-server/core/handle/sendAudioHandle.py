import json
import time
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils import textUtils
from core.utils.util import audio_to_data
from core.providers.tts.dto.dto import SentenceType
from core.utils.audioRateController import AudioRateController

TAG = __name__
# 音频帧时长（毫秒）
AUDIO_FRAME_DURATION = 60
# 预缓冲包数量。首批音频先积累到这个数量，避免设备在首个
# Qwen 音频 delta 到达后立即起播，随后被生成抖动打断。
PRE_BUFFER_COUNT = 5


async def sendAudioMessage(
    conn: "ConnectionHandler", sentenceType, audios, text, sentence_id=None
):
    # 跳过旧句子残留音频
    if sentence_id is not None and sentence_id != conn.sentence_id:
        return

    if conn.tts.tts_audio_first_sentence:
        conn.logger.bind(tag=TAG).info(f"发送第一段语音: {text}")
        conn.tts.tts_audio_first_sentence = False

    if sentenceType == SentenceType.FIRST:
        # 首帧字幕是控制帧，不属于音频流。先发送它，再触碰流控器；
        # 失效的旧流控器可能在 sendAudio() 中被重置，否则会丢字幕而保留音频。
        await send_tts_message(
            conn, "sentence_start", text, sentence_id=sentence_id
        )
    elif sentenceType == SentenceType.UPDATE:
        await send_tts_message(
            conn, "sentence_update", text, sentence_id=sentence_id
        )

    await sendAudio(conn, audios)
    # 发送句子开始消息
    if sentenceType not in (SentenceType.MIDDLE, SentenceType.UPDATE):
        conn.logger.bind(tag=TAG).info(f"发送音频消息: {sentenceType}, {text}")

    # 发送结束消息（如果是最后一个文本）
    # 通话需要维持speaking状态
    if sentenceType == SentenceType.LAST:
        await send_tts_message(conn, "stop", None, sentence_id=sentence_id)
        if conn.close_after_chat:
            await conn.close()


async def _wait_for_audio_completion(conn: "ConnectionHandler"):
    """
    等待音频队列清空并等待预缓冲包播放完成

    Args:
        conn: 连接对象
    """
    if hasattr(conn, "audio_rate_controller") and conn.audio_rate_controller:
        rate_controller = conn.audio_rate_controller
        flow_control = getattr(conn, "audio_flow_control", None)
        if flow_control is not None:
            await _flush_initial_prebuffer(conn, flow_control)
        conn.logger.bind(tag=TAG).debug(
            f"等待音频发送完成，队列中还有 {len(rate_controller.queue)} 个包"
        )
        await rate_controller.queue_empty_event.wait()

        # 等待预缓冲包播放完成
        # 前N个包直接发送，增加2个网络抖动包，需要额外等待它们在客户端播放完成
        frame_duration_ms = rate_controller.frame_duration
        pre_buffer_playback_time = (PRE_BUFFER_COUNT + 2) * frame_duration_ms / 1000.0
        await asyncio.sleep(pre_buffer_playback_time)

        conn.logger.bind(tag=TAG).debug("音频发送完成")


async def _send_to_mqtt_gateway(
    conn: "ConnectionHandler", opus_packet, timestamp, sequence
):
    """
    发送带16字节头部的opus数据包给mqtt_gateway
    Args:
        conn: 连接对象
        opus_packet: opus数据包
        timestamp: 时间戳
        sequence: 序列号
    """
    # 为opus数据包添加16字节头部
    header = bytearray(16)
    header[0] = 1  # type
    header[2:4] = len(opus_packet).to_bytes(2, "big")  # payload length
    header[4:8] = sequence.to_bytes(4, "big")  # sequence
    header[8:12] = timestamp.to_bytes(4, "big")  # 时间戳
    header[12:16] = len(opus_packet).to_bytes(4, "big")  # opus长度

    # 发送包含头部的完整数据包
    complete_packet = bytes(header) + opus_packet
    await conn.websocket.send(complete_packet)


async def sendAudio(
    conn: "ConnectionHandler", audios, frame_duration=AUDIO_FRAME_DURATION
):
    """
    发送音频包，使用 AudioRateController 进行精确的流量控制

    Args:
        conn: 连接对象
        audios: 单个opus包(bytes) 或 opus包列表
        frame_duration: 帧时长（毫秒），默认使用全局常量AUDIO_FRAME_DURATION
    """
    if audios is None or len(audios) == 0:
        return

    send_delay = conn.config.get("tts_audio_send_delay", -1) / 1000.0
    is_single_packet = isinstance(audios, bytes)

    # 初始化或获取 RateController
    rate_controller, flow_control = _get_or_create_rate_controller(
        conn, frame_duration, is_single_packet
    )

    # 统一转换为列表处理
    audio_list = [audios] if is_single_packet else audios

    # 发送音频包
    await _send_audio_with_rate_control(
        conn, audio_list, rate_controller, flow_control, send_delay
    )


def _get_or_create_rate_controller(
    conn: "ConnectionHandler", frame_duration, is_single_packet
):
    """
    获取或创建 RateController 和 flow_control

    Args:
        conn: 连接对象
        frame_duration: 帧时长
        is_single_packet: 是否单包模式（True: TTS流式单包, False: 批量包）

    Returns:
        (rate_controller, flow_control)
    """
    # 检查是否需要重置控制器
    need_reset = False

    if not hasattr(conn, "audio_rate_controller"):
        # 控制器不存在，需要创建
        need_reset = True
    else:
        rate_controller = conn.audio_rate_controller

        # 后台发送任务已停止, 则需要重置
        if (
            not rate_controller.pending_send_task
            or rate_controller.pending_send_task.done()
        ):
            need_reset = True
        # 当sentence_id 变化，需要重置
        elif (
            getattr(conn, "audio_flow_control", {}).get("sentence_id")
            != conn.sentence_id
        ):
            need_reset = True

    if need_reset:
        # 创建或获取 rate_controller
        if not hasattr(conn, "audio_rate_controller"):
            conn.audio_rate_controller = AudioRateController(frame_duration)
        else:
            conn.audio_rate_controller.reset()

        # 初始化 flow_control
        conn.audio_flow_control = {
            "packet_count": 0,
            "sequence": 0,
            "sentence_id": conn.sentence_id,
            "prebuffer": [],
        }

        # 启动后台发送循环
        _start_background_sender(
            conn, conn.audio_rate_controller, conn.audio_flow_control
        )

    return conn.audio_rate_controller, conn.audio_flow_control


def _start_background_sender(conn: "ConnectionHandler", rate_controller, flow_control):
    """
    启动后台发送循环任务

    Args:
        conn: 连接对象
        rate_controller: 速率控制器
        flow_control: 流控状态
    """

    async def send_callback(packet):
        # 检查是否应该中止
        if conn.client_abort:
            raise asyncio.CancelledError("客户端已中止")

        if flow_control.get("sentence_id") != conn.sentence_id:
            raise asyncio.CancelledError("tts sentence ownership changed")

        conn.last_activity_time = time.time() * 1000
        await _do_send_audio(conn, packet, flow_control)

    # 使用 start_sending 启动后台循环
    rate_controller.start_sending(send_callback)


async def _send_audio_with_rate_control(
    conn: "ConnectionHandler", audio_list, rate_controller, flow_control, send_delay
):
    """
    使用 rate_controller 发送音频包

    Args:
        conn: 连接对象
        audio_list: 音频包列表
        rate_controller: 速率控制器
        flow_control: 流控状态
        send_delay: 固定延迟（秒），-1表示使用动态流控
    """
    for packet in audio_list:
        if conn.client_abort:
            return

        conn.last_activity_time = time.time() * 1000

        # 预缓冲：先收集前N个包，达到阈值后一次性释放。
        if flow_control["packet_count"] < PRE_BUFFER_COUNT:
            flow_control.setdefault("prebuffer", []).append(packet)
            if len(flow_control["prebuffer"]) >= PRE_BUFFER_COUNT:
                await _flush_initial_prebuffer(conn, flow_control)
        elif send_delay > 0:
            # 固定延迟模式
            await asyncio.sleep(send_delay)
            await _do_send_audio(conn, packet, flow_control)
        else:
            # 动态流控模式：仅添加到队列，由后台循环负责发送
            rate_controller.add_audio(packet)


async def _flush_initial_prebuffer(conn: "ConnectionHandler", flow_control):
    """发送尚未达到阈值的首批音频，通常发生在短句结束时。"""
    prebuffer = flow_control.setdefault("prebuffer", [])
    while prebuffer:
        if conn.client_abort:
            prebuffer.clear()
            return
        packet = prebuffer.pop(0)
        await _do_send_audio(conn, packet, flow_control)


async def _do_send_audio(conn: "ConnectionHandler", opus_packet, flow_control):
    """
    执行实际的音频发送
    """
    packet_index = flow_control.get("packet_count", 0)
    sequence = flow_control.get("sequence", 0)
    captured_sentence_id = flow_control.get("sentence_id")
    if captured_sentence_id is not None and captured_sentence_id != conn.sentence_id:
        return False

    if conn.conn_from_mqtt_gateway:
        # 计算时间戳（基于播放位置）
        start_time = time.time()
        timestamp = int(start_time * 1000) % (2**32)
        await _send_to_mqtt_gateway(conn, opus_packet, timestamp, sequence)
    else:
        # 直接发送opus数据包
        await conn.websocket.send(opus_packet)

    # 更新流控状态
    flow_control["packet_count"] = packet_index + 1
    flow_control["sequence"] = sequence + 1
    return True


async def send_tts_message(
    conn: "ConnectionHandler", state, text=None, sentence_id=None
):
    """Send TTS state message."""
    if text is None and state == "sentence_start":
        return
    message = {"type": "tts", "state": state, "session_id": conn.session_id}
    message_sentence_id = sentence_id if sentence_id is not None else conn.sentence_id
    if message_sentence_id is not None:
        message["sentence_id"] = message_sentence_id
    if text is not None:
        message["text"] = textUtils.check_emoji(text)

    # TTS playback finished
    if state == "stop":
        stop_sentence_id = sentence_id if sentence_id is not None else conn.sentence_id
        if stop_sentence_id != conn.sentence_id:
            return

        done_ack_supported = (
            stop_sentence_id is not None and conn.supports_tts_done_ack()
        )
        tts_notify = conn.config.get("enable_stop_tts_notify", False)
        if tts_notify:
            stop_tts_notify_voice = conn.config.get(
                "stop_tts_notify_voice", "config/assets/tts_notify.mp3"
            )
            audios = await audio_to_data(
                stop_tts_notify_voice,
                is_opus=True,
                sample_rate=conn.sample_rate,
            )
            await sendAudio(conn, audios)
        # Release a short sentence's partial initial prebuffer before waiting
        # for the rate-controlled queue to drain.
        flow_control = getattr(conn, "audio_flow_control", None)
        if flow_control is not None:
            await _flush_initial_prebuffer(conn, flow_control)
        # Wait for all audio packets to finish sending.
        await _wait_for_audio_completion(conn)
        done_wait_started_at = time.monotonic()
        if done_ack_supported:
            conn.begin_tts_ack_wait("done", stop_sentence_id)
        await conn.websocket.send(json.dumps(message))
        timeout_ms = int(conn.config.get("tts_done_ack_timeout_ms", 10000))
        result = None
        if done_ack_supported:
            result = await conn.wait_for_tts_ack("done", stop_sentence_id, timeout_ms)
            conn.logger.bind(tag=TAG).info(
                "sentence_id={} tts_state=terminal done_wait_ms={}".format(
                    stop_sentence_id,
                    int((time.monotonic() - done_wait_started_at) * 1000),
                )
            )
            if result is None:
                conn._terminalize_tts_attempt_failure(
                    stop_sentence_id, "done_timeout"
                )

        if hasattr(conn, "audio_rate_controller") and conn.audio_rate_controller:
            conn.audio_rate_controller.stop_sending()
        conn.clearSpeakStatus()
        return

    await conn.websocket.send(json.dumps(message))


async def send_stt_message(
    conn: "ConnectionHandler", text, sentence_id: str | None = None
):
    """发送 STT 状态消息"""
    end_prompt_str = conn.config.get("end_prompt", {}).get("prompt")
    if end_prompt_str and end_prompt_str == text:
        await send_tts_message(conn, "start", sentence_id=sentence_id)
        return

    # 兼容少量传输层包装，只提取最终要显示和发送给设备的文本。
    display_text = text
    try:
        # 某些传输层可能把文本包装在 content 字段中；其余字段不属于
        # 博物馆业务上下文，不写入连接状态。
        if text.strip().startswith("{") and text.strip().endswith("}"):
            parsed_data = json.loads(text)
            if isinstance(parsed_data, dict) and "content" in parsed_data:
                display_text = parsed_data["content"]
    except (json.JSONDecodeError, TypeError):
        # 如果不是JSON格式，直接使用原始文本
        display_text = text
    stt_text = textUtils.get_string_no_punctuation_or_emoji(display_text)
    await conn.websocket.send(
        json.dumps({"type": "stt", "text": stt_text, "session_id": conn.session_id})
    )
    await send_tts_message(conn, "start", sentence_id=sentence_id)
    # 发送start消息后客户端状态会处于说话中状态，同步服务端状态
    conn.client_is_speaking = True


async def send_display_message(conn: "ConnectionHandler", text):
    """发送纯显示消息"""
    message = {"type": "stt", "text": text, "session_id": conn.session_id}
    await conn.websocket.send(json.dumps(message))
