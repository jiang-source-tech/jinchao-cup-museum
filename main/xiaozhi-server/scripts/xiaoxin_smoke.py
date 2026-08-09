from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.xiaoxin.runtime import XiaoxinRuntime
from core.xiaoxin.types import XiaoxinConfig

SMOKE_PROMPTS = (
    "你好",
    "北秀食堂营业时间？",
    "帮我联系老师要电话",
    "拜拜",
)


class FakeAdapter:
    def complete_chat(self, messages, max_tokens=None, temperature=None):
        user = messages[-1]["content"]
        if user == "你好":
            return "你好呀，我是数字学姐小芯，有什么想问的尽管说。"
        if "北秀食堂" in user:
            return "北秀食堂我这里有一些资料，但营业时间没有可靠依据，数字学姐不能乱说。"
        if "联系老师" in user or "电话" in user:
            return "老师个人电话不能直接提供，建议你通过学院办公室、课程群或企业微信联系。"
        return "收到啦，数字学姐先记下。"


def create_runtime(companion_dir: Path) -> XiaoxinRuntime:
    return XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=ROOT / "data" / "xiaoxin_knowledge",
            companion_db_path=companion_dir / "xiaoxin_companion.db",
        ),
        llm_adapter_factory=lambda llm: FakeAdapter(),
    )


def format_result(text: str, result) -> str:
    return (
        f"{text} -> handled={result.handled}, "
        f"reply={result.reply}, bypass={result.bypass_reason}"
    )


def run_smoke() -> list[str]:
    lines: list[str] = []
    with tempfile.TemporaryDirectory(prefix="xiaoxin_smoke_companion_") as companion_dir:
        runtime = create_runtime(Path(companion_dir))
        for text in SMOKE_PROMPTS:
            result = runtime.handle_turn(
                "smoke_device",
                text,
                [],
                object(),
                "smoke_session",
            )
            lines.append(format_result(text, result))
    return lines


def main():
    for line in run_smoke():
        print(line)


if __name__ == "__main__":
    main()
