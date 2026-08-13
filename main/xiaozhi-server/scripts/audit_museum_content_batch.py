from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.museum.content_import import (  # noqa: E402
    ContentPackageValidationError,
    audit_content_batch,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="批量预检博物馆内容包，不写入生产数据库"
    )
    parser.add_argument(
        "--input",
        type=Path,
        action="append",
        default=[],
        help="内容包路径；可重复传入",
    )
    parser.add_argument(
        "--directory",
        type=Path,
        help="扫描目录下的 .yaml、.yml 和 .json 内容包",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    paths = list(args.input)
    if args.directory is not None:
        for pattern in ("*.yaml", "*.yml", "*.json"):
            paths.extend(sorted(args.directory.glob(pattern)))
    audit = audit_content_batch(dict.fromkeys(paths))
    payload = asdict(audit)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(
            f"CONTENT BATCH: ok={audit.ok} packages={audit.package_count} "
            f"museums={len(audit.museum_ids)} exhibits={len(audit.exhibit_ids)} "
            f"facts={len(audit.fact_ids)} sources={len(audit.source_ids)}"
        )
        print(
            f"schema_versions={audit.schema_version_counts} "
            f"unique_aliases={audit.unique_alias_count} "
            f"ambiguous_aliases={audit.ambiguous_alias_count}"
        )
        for issue in audit.issues:
            print(f"- {issue}", file=sys.stderr)
    return 0 if audit.ok else 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ContentPackageValidationError as exc:
        payload = {"ok": False, "issues": list(exc.issues)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
