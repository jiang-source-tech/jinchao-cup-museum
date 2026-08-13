from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config.settings import load_config  # noqa: E402
from core.museum.readiness import check_museum_readiness  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查博物馆数据库和向量发布一致性")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = check_museum_readiness(load_config(), server_root=SERVER_ROOT)
    except Exception as exc:
        result = {
            "ready": False,
            "error": type(exc).__name__,
            "detail": "Readiness 检查失败，详细异常仅保留在受控运行环境中。",
        }
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(payload + "\n", encoding="utf-8")
    return 0 if result.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
