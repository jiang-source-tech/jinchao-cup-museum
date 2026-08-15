from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.museum.source_ingestion import (  # noqa: E402
    SourceManifestError,
    load_source_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验博物馆原始资料 manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    manifest = load_source_manifest(args.manifest)
    payload = {
        "status": "valid",
        "schema_version": manifest.schema_version,
        "dataset_id": manifest.dataset_id,
        "museum_id": manifest.museum_id,
        "source_count": len(manifest.sources),
        "source_ids": [entry.id for entry in manifest.sources],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"资料清单有效：{manifest.dataset_id}")
        print(f"来源数量：{len(manifest.sources)}")
        for entry in manifest.sources:
            print(f"- {entry.id}: {entry.title}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except SourceManifestError as exc:
        payload = {
            "status": "invalid",
            "error_type": "manifest_validation_error",
            "message": str(exc),
            "issues": list(exc.issues),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
