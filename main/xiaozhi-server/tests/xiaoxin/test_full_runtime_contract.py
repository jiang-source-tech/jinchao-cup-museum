from pathlib import Path
from types import SimpleNamespace

from core.xiaoxin.runtime import XiaoxinRuntime
from core.xiaoxin.types import XiaoxinConfig
from scripts import xiaoxin_smoke


PROJECT_DIR = Path(__file__).resolve().parents[2]


class FakeAdapter:
    def __init__(self, reply):
        self.reply = reply

    def complete_chat(self, messages, max_tokens=None, temperature=None):
        return self.reply


def test_campus_question_does_not_invent_when_fact_missing(tmp_path):
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=PROJECT_DIR / "data" / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        llm_adapter_factory=lambda llm: FakeAdapter(
            "这个我这里没有可靠资料，不能瞎编。"
        ),
    )

    result = runtime.handle_turn(
        "device_1",
        "实验中心电话是多少？",
        [],
        object(),
        "s1",
    )

    assert result.handled is True
    assert "没有可靠资料" in result.reply


def test_existing_tool_exit_stays_unhandled(tmp_path):
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=PROJECT_DIR / "data" / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        llm_adapter_factory=lambda llm: FakeAdapter("不应该调用"),
    )

    result = runtime.handle_turn("device_1", "拜拜", [], object(), "s1")

    assert result.handled is False
    assert result.bypass_reason == "existing_tool"


def test_smoke_script_keeps_real_knowledge_dir_and_injected_companion_db(tmp_path):
    runtime = xiaoxin_smoke.create_runtime(tmp_path)

    assert runtime.config.knowledge_dir == xiaoxin_smoke.ROOT / "data" / "xiaoxin_knowledge"
    assert runtime.config.companion_db_path == tmp_path / "xiaoxin_companion.db"


def test_smoke_script_uses_temporary_companion_db_and_cleans_up(
    monkeypatch, tmp_path
):
    events: dict[str, object] = {"closed": False}

    class FakeTemporaryDirectory:
        def __init__(self, prefix=None):
            events["prefix"] = prefix

        def __enter__(self):
            events["entered"] = tmp_path
            return str(tmp_path)

        def __exit__(self, exc_type, exc, tb):
            events["closed"] = True
            return False

    class FakeRuntime:
        def handle_turn(self, user_id, user_text, history, llm, session_id):
            return SimpleNamespace(
                handled=user_text != "拜拜",
                reply=f"reply:{user_text}",
                bypass_reason=None if user_text != "拜拜" else "existing_tool",
            )

    def fake_create_runtime(companion_dir):
        events["companion_dir"] = companion_dir
        return FakeRuntime()

    monkeypatch.setattr(
        xiaoxin_smoke.tempfile,
        "TemporaryDirectory",
        FakeTemporaryDirectory,
    )
    monkeypatch.setattr(xiaoxin_smoke, "create_runtime", fake_create_runtime)

    lines = xiaoxin_smoke.run_smoke()

    assert events["prefix"] == "xiaoxin_smoke_companion_"
    assert events["companion_dir"] == tmp_path
    assert events["closed"] is True
    assert xiaoxin_smoke.SMOKE_PROMPTS == (
        "你好",
        "北秀食堂营业时间？",
        "帮我联系老师要电话",
        "拜拜",
    )
    assert lines[0].startswith("你好 -> handled=True")
    assert lines[-1].endswith("bypass=existing_tool")
