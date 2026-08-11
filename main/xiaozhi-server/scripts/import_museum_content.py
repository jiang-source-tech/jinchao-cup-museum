from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.museum.content_import import (  # noqa: E402
    ContentImportResult,
    ContentPackageValidationError,
    ExhibitVersionHistory,
    InteractionEvidenceAudit,
    MuseumContentPackage,
    RevisionLifecycleResult,
    audit_interaction_evidence,
    import_draft_content,
    load_content_package,
    publish_revision,
    review_revision,
    rollback_revision,
    show_exhibit_versions,
    validate_content_package_for_store,
    withdraw_revision,
)
from core.museum.store import MuseumStore  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="校验、导入和管理博物馆展品内容版本"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="只校验 YAML/JSON 内容包"
    )
    _add_input_argument(validate_parser)
    validate_parser.add_argument(
        "--database",
        type=Path,
        help="可选；同时检查与现有 SQLite 内容的冲突",
    )
    validate_parser.add_argument("--json", action="store_true")

    import_parser = subparsers.add_parser(
        "import", help="完整校验后在单一事务中导入 draft"
    )
    _add_input_argument(import_parser)
    import_parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="目标 SQLite 数据库",
    )
    import_parser.add_argument("--json", action="store_true")

    review_parser = subparsers.add_parser(
        "review", help="审核一个 draft 内容版本"
    )
    _add_lifecycle_arguments(review_parser)

    publish_parser = subparsers.add_parser(
        "publish", help="发布 reviewed 内容版本并替代旧发布版本"
    )
    _add_lifecycle_arguments(publish_parser)

    withdraw_parser = subparsers.add_parser(
        "withdraw", help="撤回当前发布版本"
    )
    _add_lifecycle_arguments(withdraw_parser, require_reason=True)

    rollback_parser = subparsers.add_parser(
        "rollback", help="恢复一个已撤回版本"
    )
    _add_lifecycle_arguments(rollback_parser, require_reason=True)

    show_parser = subparsers.add_parser(
        "show", help="查看展品版本和生命周期事件"
    )
    _add_database_argument(show_parser)
    show_parser.add_argument("--exhibit-id", required=True)
    show_parser.add_argument("--json", action="store_true")

    audit_parser = subparsers.add_parser(
        "audit", help="按 request_id 复核历史回答依据"
    )
    _add_database_argument(audit_parser)
    audit_parser.add_argument("--request-id", required=True)
    audit_parser.add_argument("--json", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "validate":
        package = load_content_package(args.input)
        if args.database is not None:
            validate_content_package_for_store(MuseumStore(args.database), package)
        payload = _package_payload("validated", package, args.database)
    elif args.command == "import":
        package = load_content_package(args.input)
        result = import_draft_content(MuseumStore(args.database), package)
        payload = _result_payload("imported", result, args.database)
    else:
        store = MuseumStore(args.database)
        if args.command == "review":
            result = review_revision(
                store,
                revision_id=args.revision_id,
                reviewed_by=args.actor,
                reviewed_at=_operation_time(args),
            )
            payload = _lifecycle_payload("reviewed", result, args.database)
        elif args.command == "publish":
            result = publish_revision(
                store,
                revision_id=args.revision_id,
                published_by=args.actor,
                published_at=_operation_time(args),
            )
            payload = _lifecycle_payload("published", result, args.database)
        elif args.command == "withdraw":
            result = withdraw_revision(
                store,
                revision_id=args.revision_id,
                withdrawn_by=args.actor,
                withdrawn_at=_operation_time(args),
                reason=args.reason,
            )
            payload = _lifecycle_payload("withdrawn", result, args.database)
        elif args.command == "rollback":
            result = rollback_revision(
                store,
                revision_id=args.revision_id,
                rolled_back_by=args.actor,
                rolled_back_at=_operation_time(args),
                reason=args.reason,
            )
            payload = _lifecycle_payload("rolled_back", result, args.database)
        elif args.command == "show":
            history = show_exhibit_versions(store, exhibit_id=args.exhibit_id)
            payload = _history_payload(history, args.database)
        else:
            audit = audit_interaction_evidence(
                store,
                request_id=args.request_id,
            )
            payload = _audit_payload(audit, args.database)
    _print_payload(payload, as_json=args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ContentPackageValidationError as exc:
        _print_error(
            error_type="validation_error",
            message=str(exc),
            issues=exc.issues,
            as_json=args.json,
        )
        return 2
    except sqlite3.Error as exc:
        _print_error(
            error_type="database_error",
            message=str(exc),
            issues=(),
            as_json=args.json,
        )
        return 2


def _add_input_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="内容包路径（.yaml、.yml 或 .json）",
    )


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="目标 SQLite 数据库",
    )


def _add_lifecycle_arguments(
    parser: argparse.ArgumentParser,
    *,
    require_reason: bool = False,
) -> None:
    _add_database_argument(parser)
    parser.add_argument("--revision-id", required=True)
    parser.add_argument("--actor", required=True, help="执行人标识")
    parser.add_argument(
        "--occurred-at",
        type=_datetime_argument,
        help="带时区的 ISO 8601 时间；默认使用当前 UTC 时间",
    )
    if require_reason:
        parser.add_argument("--reason", required=True)
    parser.add_argument("--json", action="store_true")


def _datetime_argument(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("时间必须是 ISO 8601 格式") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("时间必须包含时区")
    return parsed


def _operation_time(args: argparse.Namespace) -> datetime:
    return args.occurred_at or datetime.now(timezone.utc)


def _package_payload(
    action: str,
    package: MuseumContentPackage,
    database: Path | None,
) -> dict:
    return {
        "action": action,
        "schema_version": package.schema_version,
        "museum_id": package.museum.id,
        "database": str(database) if database is not None else None,
        "exhibit_ids": [exhibit.id for exhibit in package.exhibits],
        "exhibit_count": len(package.exhibits),
        "revision_count": len(package.exhibits),
        "fact_count": sum(
            len(exhibit.revision.facts) for exhibit in package.exhibits
        ),
        "source_count": len(package.sources),
    }


def _result_payload(
    action: str,
    result: ContentImportResult,
    database: Path,
) -> dict:
    return {
        "action": action,
        "museum_id": result.museum_id,
        "database": str(database),
        "exhibit_ids": list(result.exhibit_ids),
        "exhibit_count": result.exhibit_count,
        "revision_count": result.revision_count,
        "fact_count": result.fact_count,
        "source_count": result.source_count,
    }


def _lifecycle_payload(
    action: str,
    result: RevisionLifecycleResult,
    database: Path,
) -> dict:
    return {
        "action": action,
        "database": str(database),
        "revision_id": result.revision_id,
        "exhibit_id": result.exhibit_id,
        "revision_number": result.revision_number,
        "status": result.status,
        "previous_published_revision_id": (
            result.previous_published_revision_id
        ),
    }


def _history_payload(history: ExhibitVersionHistory, database: Path) -> dict:
    return {
        "action": "show",
        "database": str(database),
        **asdict(history),
    }


def _audit_payload(audit: InteractionEvidenceAudit, database: Path) -> dict:
    return {
        "action": "audit",
        "database": str(database),
        **asdict(audit),
    }


def _print_payload(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    if "museum_id" in payload:
        print(
            f"{payload['action'].upper()}: museum={payload['museum_id']} "
            f"exhibits={payload['exhibit_count']} "
            f"revisions={payload['revision_count']} "
            f"facts={payload['fact_count']} sources={payload['source_count']}"
        )
    elif "revision_id" in payload and "status" in payload:
        print(
            f"{payload['action'].upper()}: revision={payload['revision_id']} "
            f"status={payload['status']} exhibit={payload['exhibit_id']}"
        )
    elif payload["action"] == "show":
        print(
            f"SHOW: exhibit={payload['exhibit_id']} "
            f"published={payload['current_published_revision_id'] or '-'}"
        )
        for revision in payload["revisions"]:
            print(
                f"revision={revision['revision_id']} "
                f"number={revision['revision_number']} "
                f"status={revision['status']} facts={revision['fact_count']} "
                f"sources={revision['source_count']} "
                f"added={','.join(revision['added_fact_ids']) or '-'} "
                f"removed={','.join(revision['removed_fact_ids']) or '-'}"
            )
    else:
        print(
            f"AUDIT: request={payload['request_id']} "
            f"revision={payload['content_revision_id'] or '-'} "
            f"facts={len(payload['facts'])} sources={len(payload['sources'])}"
        )
    print(f"database={payload['database']}")


def _print_error(
    *,
    error_type: str,
    message: str,
    issues: tuple[str, ...],
    as_json: bool,
) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": error_type,
                    "message": message,
                    "issues": list(issues),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
