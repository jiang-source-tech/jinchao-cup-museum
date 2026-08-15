from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.museum.source_ingestion import (  # noqa: E402
    SourceManifestError,
    ingest_source_manifest,
)
from core.museum.store import MuseumStore  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="摄取博物馆原始资料")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--root-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    run_id = args.run_id.strip() or f"ingest-{uuid.uuid4().hex[:12]}"
    report = ingest_source_manifest(
        args.manifest,
        store=MuseumStore(args.database),
        run_id=run_id,
        root_dir=args.root_dir,
    )
    payload = {
        "status": "succeeded" if report.ok else "failed",
        "run_id": report.run_id,
        "source_ids": list(report.source_ids),
        "segment_ids": list(report.segment_ids),
        "source_count": len(report.source_ids),
        "segment_count": len(report.segment_ids),
        "errors": list(report.errors),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"资料摄取{'完成' if report.ok else '失败'}：{report.run_id}")
        print(f"来源数量：{len(report.source_ids)}")
        print(f"片段数量：{len(report.segment_ids)}")
        for error in report.errors:
            print(f"错误：{error}", file=sys.stderr)
    return 0 if report.ok else 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except SourceManifestError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "error_type": "manifest_validation_error",
                        "message": str(exc),
                        "issues": list(exc.issues),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
