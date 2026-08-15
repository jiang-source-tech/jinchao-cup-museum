from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.museum.embedding import DashScopeTextEmbedder  # noqa: E402
from core.museum.evidence_index import QdrantEvidenceIndex  # noqa: E402
from core.museum.evidence_retrieval import (  # noqa: E402
    EvidenceSearchRequest,
    EvidenceSearchService,
)
from core.museum.store import MuseumStore  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检索博物馆原文证据片段")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--exhibit-id", action="append", default=[])
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--model", default="text-embedding-v4")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        store = MuseumStore(args.database, read_only=True)
        embedder = DashScopeTextEmbedder(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            model=args.model,
            dimension=args.dimension,
            timeout_seconds=30,
        )
        index = QdrantEvidenceIndex(
            url=args.qdrant_url,
            collection_name=args.collection,
            dimension=args.dimension,
            timeout_seconds=30,
        )
        pack = EvidenceSearchService(
            store=store,
            embedder=embedder,
            index=index,
        ).search(
            EvidenceSearchRequest(
                question=args.question,
                exhibit_ids=tuple(args.exhibit_id),
                source_ids=tuple(args.source_id),
                limit=args.limit,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {"status": "succeeded", **pack.as_dict()},
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
