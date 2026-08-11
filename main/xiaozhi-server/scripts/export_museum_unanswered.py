from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.museum.store import MuseumStore  # noqa: E402


_CSV_FIELDS = (
    "request_id",
    "original_question",
    "resolution_status",
    "exhibit_id",
    "unanswered_reason",
    "recorded_unanswered_reason",
    "coarse_intent",
    "fine_intent",
    "occurrence_count",
    "last_occurred_at",
    "fact_candidate_ids",
    "guard_result",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="导出博物馆问答未命中问题并复核代表请求"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export", help="按问题、展品和原因聚合未命中记录"
    )
    _add_database_argument(export_parser)
    export_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="导出文件路径",
    )
    export_parser.add_argument(
        "--format",
        choices=("json", "csv"),
        required=True,
        help="导出格式",
    )

    audit_parser = subparsers.add_parser(
        "audit", help="按代表 request_id 导出完整 interaction_trace"
    )
    _add_database_argument(audit_parser)
    audit_parser.add_argument("--request-id", required=True)
    audit_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="审计 JSON 文件路径",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if not args.database.is_file():
        _print_error(
            error="database_not_found",
            message=f"数据库不存在：{args.database}",
        )
        return 2

    store = MuseumStore(args.database)
    if args.command == "export":
        issues = store.list_unanswered_issues()
        if args.format == "json":
            payload = {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "database": str(args.database),
                "issue_count": len(issues),
                "issues": [asdict(issue) for issue in issues],
            }
            _write_json(args.output, payload)
        else:
            _write_csv(args.output, [asdict(issue) for issue in issues])
        _print_success(
            action="export",
            output=args.output,
            record_count=len(issues),
        )
        return 0

    audit = store.get_interaction_audit_by_request_id(args.request_id)
    if audit is None:
        _print_error(
            error="request_not_found",
            message=f"未找到 request_id：{args.request_id}",
        )
        return 3
    _write_json(
        args.output,
        {
            "schema_version": 1,
            "database": str(args.database),
            "audit": audit,
        },
    )
    _print_success(action="audit", output=args.output, record_count=1)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (OSError, sqlite3.Error) as exc:
        _print_error(error="export_failed", message=str(exc))
        return 2


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="现有 SQLite 数据库路径",
    )


def _write_json(output: Path, payload: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_csv(output: Path, rows: list[dict]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            serialized = dict(row)
            serialized["fact_candidate_ids"] = json.dumps(
                serialized["fact_candidate_ids"],
                ensure_ascii=False,
            )
            writer.writerow(serialized)


def _print_success(*, action: str, output: Path, record_count: int) -> None:
    print(
        json.dumps(
            {
                "status": "ok",
                "action": action,
                "output": str(output),
                "record_count": record_count,
            },
            ensure_ascii=False,
        )
    )


def _print_error(*, error: str, message: str) -> None:
    print(
        json.dumps(
            {
                "status": "error",
                "error": error,
                "message": message,
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
