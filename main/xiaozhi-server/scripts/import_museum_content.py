from __future__ import annotations

import argparse
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
    MuseumContentPackage,
    import_draft_content,
    load_content_package,
    validate_content_package_for_store,
)
from core.museum.store import MuseumStore  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="校验或导入博物馆展品内容包"
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
    return parser


def run(args: argparse.Namespace) -> int:
    package = load_content_package(args.input)
    if args.command == "validate":
        if args.database is not None:
            validate_content_package_for_store(MuseumStore(args.database), package)
        payload = _package_payload("validated", package, args.database)
    else:
        result = import_draft_content(MuseumStore(args.database), package)
        payload = _result_payload("imported", result, args.database)
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


def _print_payload(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    print(
        f"{payload['action'].upper()}: museum={payload['museum_id']} "
        f"exhibits={payload['exhibit_count']} "
        f"revisions={payload['revision_count']} "
        f"facts={payload['fact_count']} sources={payload['source_count']}"
    )
    if payload.get("database"):
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
