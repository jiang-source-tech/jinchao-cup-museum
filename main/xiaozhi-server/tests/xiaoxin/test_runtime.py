from pathlib import Path
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from core.xiaoxin.response_guard import is_fragmented_reply
from core.xiaoxin.runtime import (
    DEFAULT_CONVERSATIONAL_FALLBACK,
    DEFAULT_MESSAGE_DRAFTING_FALLBACK,
    XiaoxinRuntime,
)
from core.xiaoxin.types import XiaoxinConfig


class FakeAdapter:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete_chat(self, messages, max_tokens=None, temperature=None):
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return self.replies.pop(0)


def make_runtime(tmp_path, replies, time_provider=None):
    adapter = FakeAdapter(replies)
    cfg = XiaoxinConfig(
        enabled=True,
        knowledge_dir=Path(__file__).resolve().parents[2]
        / "data"
        / "xiaoxin_knowledge",
        companion_db_path=tmp_path / "xiaoxin_companion.db",
        max_tokens=300,
        free_chat_temperature=0.8,
        knowledge_temperature=0.35,
        boundary_temperature=0.5,
    )
    runtime = XiaoxinRuntime(
        cfg,
        llm_adapter_factory=lambda llm: adapter,
        time_provider=time_provider,
    )
    return runtime, adapter


def test_existing_tool_turn_is_unhandled(tmp_path):
    runtime, adapter = make_runtime(tmp_path, [])

    result = runtime.handle_turn(
        "device_1",
        "\u62dc\u62dc",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is False
    assert result.bypass_reason == "existing_tool"
    assert adapter.calls == []


def test_hard_boundary_uses_local_template_without_llm(tmp_path):
    runtime, adapter = make_runtime(tmp_path, [])

    result = runtime.handle_turn(
        "device_1",
        "\u5e2e\u6211\u8054\u7cfb\u8001\u5e08\u8981\u7535\u8bdd",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    assert "\u4e0d\u80fd" in result.reply
    assert result.route["reply_mode"] == "hard_template"
    assert adapter.calls == []


def test_hard_boundary_rule_does_not_create_legacy_memory_files(tmp_path):
    runtime, adapter = make_runtime(tmp_path, [])

    result = runtime.handle_turn(
        "device_1",
        "我叫小明，帮我联系老师要电话",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    assert result.route["reply_mode"] == "hard_template"
    assert result.memory_result["memory_action"] == "skipped"
    assert list(tmp_path.glob("*.json")) == []
    assert list(tmp_path.glob("*.jsonl")) == []
    assert adapter.calls == []


def test_runtime_system_prompt_keeps_senior_sister_persona_sync_rules(tmp_path):
    runtime, adapter = make_runtime(tmp_path, ["我在呢，慢慢说。"])

    result = runtime.handle_turn(
        "device_1",
        "我感觉别人都比我强，有点焦虑",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    system_prompt = adapter.calls[0]["messages"][0]["content"]
    assert "数字学姐" in system_prompt
    assert "数字学长" not in system_prompt
    assert "消息、短信、邮件、申请文本" in system_prompt
    for required in (
        "安静陪伴",
        "罗杰斯式情绪陪伴",
        "电子宠物身体感",
        "克制的亲近感",
        "不把记忆列表背给用户",
        "不急着派任务",
    ):
        assert required in system_prompt
    assert "不要把屏幕亮起、表情变化或动画写成会被语音念出的动作旁白" in system_prompt
    assert "我屏幕亮了一下" not in system_prompt


def test_free_chat_calls_llm_and_anonymous_subject_stays_private(tmp_path):
    runtime, adapter = make_runtime(
        tmp_path, ["\u6211\u5728\u5462\uff0c\u6162\u6162\u8bf4\u3002"]
    )

    result = runtime.handle_turn(
        "device_1",
        "\u6211\u53eb\u5c0f\u660e\uff0c\u662f\u81ea\u52a8\u5316\u4e13\u4e1a\u65b0\u751f",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    assert result.reply == "\u6211\u5728\u5462\uff0c\u6162\u6162\u8bf4\u3002"
    assert result.memory_result["memory_action"] == "skipped"
    assert adapter.calls[0]["temperature"] == 0.8


def test_runtime_does_not_duplicate_current_user_turn_when_history_already_has_it(
    tmp_path,
):
    runtime, adapter = make_runtime(tmp_path, ["\u597d\u7684"])
    history = [{"role": "user", "content": "\u4f60\u597d"}]

    result = runtime.handle_turn(
        "device_1",
        "\u4f60\u597d",
        history,
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    user_messages = [
        message
        for message in adapter.calls[0]["messages"]
        if message.get("role") == "user"
    ]
    assert user_messages == [{"role": "user", "content": "\u4f60\u597d"}]


def test_runtime_normalizes_xiaoxin_asr_name_before_llm_and_memory(tmp_path):
    runtime, adapter = make_runtime(tmp_path, ["\u6211\u662f\u5c0f\u82af\u3002"])

    result = runtime.handle_turn(
        "device_1",
        "\u4f60\u53eb\u5c0f\u65b0\u5417\uff1f",
        [{"role": "user", "content": "\u4f60\u53eb\u5c0f\u65b0\u5417\uff1f"}],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    user_messages = [
        message
        for message in adapter.calls[0]["messages"]
        if message.get("role") == "user"
    ]
    assert user_messages == [
        {"role": "user", "content": "\u4f60\u53eb\u5c0f\u82af\u5417\uff1f"}
    ]


def test_runtime_injects_current_shanghai_time_into_system_prompt(tmp_path):
    fixed_now = datetime(2026, 7, 4, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    runtime, adapter = make_runtime(
        tmp_path,
        ["\u73b0\u5728\u662f\u4e0a\u5348\u3002"],
        time_provider=lambda: fixed_now,
    )

    result = runtime.handle_turn(
        "device_1",
        "\u6211\u4eec\u804a\u4f1a\u513f\u5427",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    system_prompt = adapter.calls[0]["messages"][0]["content"]
    assert "Asia/Shanghai" in system_prompt
    assert "2026-07-04 09:40:00" in system_prompt
    assert "\u5fc5\u987b\u4ee5\u8fd9\u4e2a\u65f6\u95f4\u4e3a\u51c6" in system_prompt


def test_runtime_injects_device_sntp_snapshot_into_system_prompt(tmp_path):
    fixed_now = datetime(2026, 7, 4, 9, 40, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    device_now = datetime(2026, 7, 4, 9, 40, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    runtime, adapter = make_runtime(
        tmp_path,
        ["\u6211\u5728\u5462\u3002"],
        time_provider=lambda: fixed_now,
    )

    result = runtime.handle_turn(
        "device_1",
        "\u6211\u4eec\u804a\u4f1a\u513f\u5427",
        [],
        llm=object(),
        session_id="s1",
        device_time_snapshot={
            "wall_time_ms": int(device_now.timestamp() * 1000),
            "sync_status": "synced",
            "timezone": "Asia/Shanghai",
            "source": "sntp",
            "received_at_ms": int(fixed_now.timestamp() * 1000),
        },
    )

    assert result.handled is True
    system_prompt = adapter.calls[0]["messages"][0]["content"]
    assert "\u8bbe\u5907SNTP\u72b6\u6001\uff1asynced" in system_prompt
    assert "\u8bbe\u5907\u65f6\u95f4\uff1a2026-07-04 09:40:40" in system_prompt
    assert (
        "\u670d\u52a1\u7aef\u65f6\u95f4\u4ecd\u662f\u6700\u7ec8\u51c6\u7ef3"
        in system_prompt
    )


def test_current_time_question_uses_local_rule_before_tool_bypass(tmp_path):
    fixed_now = datetime(2026, 7, 4, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    runtime, adapter = make_runtime(
        tmp_path,
        [],
        time_provider=lambda: fixed_now,
    )

    result = runtime.handle_turn(
        "device_1",
        "\u5c0f\u82af\uff0c\u73b0\u5728\u51e0\u70b9\u4e86\uff1f",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    assert result.route["reply_mode"] == "local_time"
    assert result.route["intent"] == "current_time"
    assert "\u4e0a\u53489\u70b940\u5206" in result.reply
    assert adapter.calls == []


def test_current_date_and_weekday_question_uses_local_rule(tmp_path):
    fixed_now = datetime(2026, 7, 4, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    runtime, adapter = make_runtime(
        tmp_path,
        [],
        time_provider=lambda: fixed_now,
    )

    result = runtime.handle_turn(
        "device_1",
        "\u5c0f\u82af\uff0c\u4eca\u5929\u51e0\u53f7\u661f\u671f\u51e0\uff1f",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    assert result.route["reply_mode"] == "local_time"
    assert result.route["intent"] == "current_date"
    assert "2026\u5e747\u67084\u65e5" in result.reply
    assert "\u661f\u671f\u516d" in result.reply
    assert adapter.calls == []


def test_self_intro_with_real_campus_question_stays_grounded_and_retries(tmp_path):
    runtime, adapter = make_runtime(
        tmp_path,
        [
            "\u5317\u79c0\u98df\u5802\u8425\u4e1a\u65f6\u95f4\u662f\u65e9\u4e0a\u4e03\u70b9\u5230\u665a\u4e0a\u4e5d\u70b9\u3002",
            "\u5317\u79c0\u98df\u5802\u6211\u8fd9\u91cc\u6709\u4e00\u4e9b\u4fe1\u606f\uff0c\u4f46\u8425\u4e1a\u65f6\u95f4\u6ca1\u6709\u53ef\u9760\u8d44\u6599\u3002",
        ],
    )

    result = runtime.handle_turn(
        "device_1",
        "\u6211\u662f\u81ea\u52a8\u5316\u4e13\u4e1a\u65b0\u751f\uff0c\u5317\u79c0\u98df\u5802\u8425\u4e1a\u65f6\u95f4\u51e0\u70b9\uff1f",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    assert result.route["reply_mode"] == "knowledge_grounded"
    assert "\u6ca1\u6709\u53ef\u9760\u8d44\u6599" in result.reply
    assert len(adapter.calls) == 2
    assert adapter.calls[0]["temperature"] == 0.35
    assert adapter.calls[1]["temperature"] == 0.5


def test_knowledge_reply_retries_when_scope_is_violated(tmp_path):
    runtime, adapter = make_runtime(
        tmp_path,
        [
            "\u5317\u79c0\u98df\u5802\u8425\u4e1a\u65f6\u95f4\u662f\u65e9\u4e0a\u4e03\u70b9\u5230\u665a\u4e0a\u4e5d\u70b9\u3002",
            "\u5317\u79c0\u98df\u5802\u6211\u8fd9\u91cc\u6709\u4e00\u4e9b\u4fe1\u606f\uff0c\u4f46\u8425\u4e1a\u65f6\u95f4\u6ca1\u6709\u53ef\u9760\u8d44\u6599\u3002",
        ],
    )

    result = runtime.handle_turn(
        "device_1",
        "\u5317\u79c0\u98df\u5802\u8425\u4e1a\u65f6\u95f4\uff1f",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    assert "\u6ca1\u6709\u53ef\u9760\u8d44\u6599" in result.reply
    assert len(adapter.calls) == 2


def test_free_chat_retry_failure_uses_conversational_fallback(tmp_path, caplog):
    runtime, adapter = make_runtime(tmp_path, ["\u56e0\u4e3a", "\u5982\u679c"])

    with caplog.at_level(logging.WARNING, logger="core.xiaoxin.runtime"):
        result = runtime.handle_turn(
            "device_1",
            "\u6211\u6709\u70b9\u7d27\u5f20",
            [],
            llm=object(),
            session_id="s1",
        )

    assert result.handled is True
    assert result.route["reply_mode"] == "free_chat"
    assert result.reply == DEFAULT_CONVERSATIONAL_FALLBACK
    assert "再跟我说一遍" not in result.reply
    assert is_fragmented_reply(result.reply) is False
    assert len(adapter.calls) == 2
    rejection_logs = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Xiaoxin reply rejected ")
    ]
    assert len(rejection_logs) == 2
    assert '"phase": "initial"' in rejection_logs[0]
    assert '"phase": "repair"' in rejection_logs[1]
    assert '"fragmented_reply"' in rejection_logs[0]
    assert "\u56e0\u4e3a" not in caplog.text
    assert "\u5982\u679c" not in caplog.text


def test_message_drafting_retry_failure_uses_drafting_fallback(tmp_path):
    runtime, adapter = make_runtime(tmp_path, ["\u56e0\u4e3a", "\u5982\u679c"])

    result = runtime.handle_turn(
        "device_1",
        "\u5e2e\u6211\u5199\u4e00\u6bb5\u7ed9\u8f85\u5bfc\u5458\u8be2\u95ee\u60c5\u51b5\u7684\u6d88\u606f",
        [],
        llm=object(),
        session_id="s1",
    )

    assert result.handled is True
    assert result.route["reply_mode"] == "message_drafting"
    assert result.reply == DEFAULT_MESSAGE_DRAFTING_FALLBACK
    assert len(adapter.calls) == 2
