from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config.settings import load_config  # noqa: E402
from core.business_runtime_factory import create_conversation_runtime  # noqa: E402
from core.museum.canary import run_canary  # noqa: E402
from scripts.museum_text_chat import initialize_chat_llm  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行生产 RAG 固定文本 canary")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--require-llm", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--maximum-duration-ms", type=int, default=3000)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config()
        llm, llm_mode = initialize_chat_llm(
            config,
            disabled=args.no_llm,
            required=args.require_llm,
        )
        result = run_canary(
            create_conversation_runtime(config),
            llm=llm,
            run_id=args.run_id,
            maximum_duration_ms=args.maximum_duration_ms,
        )
        result["llm_mode"] = llm_mode
    except Exception as exc:
        result = {
            "passed": False,
            "error": type(exc).__name__,
            "detail": "Canary 执行失败，详细异常仅保留在受控运行环境中。",
        }
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(payload + "\n", encoding="utf-8")
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
