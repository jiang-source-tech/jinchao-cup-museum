from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config.settings import load_config  # noqa: E402
from core.museum.observability import summarize_interaction_traces  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读汇总博物馆交互审计指标")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)
    config = load_config()
    runtime = config.get("business_runtime", {})
    database = args.database or Path(
        str(runtime.get("database_path", "data/museum_demo.db"))
    )
    if not database.is_absolute():
        database = SERVER_ROOT / database
    since = None if args.all else datetime.now().astimezone() - timedelta(hours=args.hours)
    try:
        result = summarize_interaction_traces(database, since=since)
    except Exception as exc:
        result = {
            "ok": False,
            "error": type(exc).__name__,
            "detail": "指标汇总失败，详细异常仅保留在受控运行环境中。",
        }
    else:
        result["ok"] = True
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(payload + "\n", encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
