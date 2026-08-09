from types import SimpleNamespace

from core.providers.tts.qwen_realtime import TTSProvider
from core.providers.tts.dto.dto import SentenceType


def test_qwen_realtime_session_config_includes_configured_volume():
    provider = TTSProvider(
        {
            "api_key": "test-key",
            "voice": "Stella",
            "volume": 100,
        },
        delete_audio_file=True,
    )

    assert provider._build_session_config()["volume"] == 100


def test_qwen_realtime_updates_subtitle_after_audio_has_started():
    provider = TTSProvider(
        {
            "api_key": "test-key",
            "voice": "Stella",
        },
        delete_audio_file=True,
    )
    provider.conn = SimpleNamespace(sentence_id="sentence-1")
    prefix = "今天杭州天气晴朗，气温25到32度，东南风1级。未来七天"
    full_text = prefix + "适合出去玩。"

    provider.tts_text = prefix
    provider._emit_first_audio_marker()
    provider.tts_text = full_text
    provider._emit_subtitle_update()

    first_type, _, first_text, first_sentence_id = (
        provider.tts_audio_queue.get_nowait()
    )
    update_type, _, update_text, update_sentence_id = (
        provider.tts_audio_queue.get_nowait()
    )

    assert first_type is SentenceType.FIRST
    assert first_text == prefix
    assert first_sentence_id == "sentence-1"
    assert update_type is SentenceType.UPDATE
    assert update_text == full_text
    assert update_sentence_id == "sentence-1"

    provider._emit_subtitle_update()
    assert provider.tts_audio_queue.empty()
