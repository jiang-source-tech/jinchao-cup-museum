from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Callable


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.museum.embedding import DashScopeTextEmbedder  # noqa: E402
from core.museum.evidence_index import QdrantEvidenceIndex  # noqa: E402
from core.museum.evidence_store import EvidenceStore  # noqa: E402
from core.museum.store import MuseumStore  # noqa: E402


def build_index_text(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("source_title", "")),
        str(record.get("section", "")),
        " ".join(str(value) for value in record.get("exhibit_ids", ())),
        str(record.get("text", "")),
    ]
    return "\n".join(part for part in parts if part)


def build_museum_evidence_index(
    *,
    database_path: Path,
    qdrant_url: str,
    api_key: str,
    collection: str,
    model: str = "text-embedding-v4",
    dimension: int = 1024,
    batch_size: int = 10,
    switch_alias: bool = True,
    embedder_factory: Callable[..., Any] = DashScopeTextEmbedder,
    index_factory: Callable[..., Any] = QdrantEvidenceIndex,
) -> dict[str, Any]:
    if batch_size < 1 or batch_size > 10:
        raise ValueError("batch_size must be between 1 and 10")
    if not database_path.is_file():
        raise FileNotFoundError(f"database does not exist: {database_path}")

    store = MuseumStore(database_path, read_only=True)
    records = EvidenceStore(store).published_segment_index_records()
    if not records:
        raise RuntimeError("database has no published source segments")
    texts = [build_index_text(record) for record in records]
    embedder = embedder_factory(
        api_key=api_key,
        model=model,
        dimension=dimension,
        timeout_seconds=30,
    )

    started = perf_counter()
    vectors: list[list[float]] = []
    request_ids: list[str] = []
    usage_totals: dict[str, int | float] = {}
    for offset in range(0, len(texts), batch_size):
        result = embedder.embed_many_with_usage(texts[offset : offset + batch_size])
        vectors.extend([list(vector) for vector in result.vectors])
        if result.request_id:
            request_ids.append(result.request_id)
        for key, value in result.usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage_totals[key] = usage_totals.get(key, 0) + value

    index = index_factory(
        url=qdrant_url,
        collection_name=collection,
        dimension=dimension,
        timeout_seconds=30,
    )
    indexed = (
        index.rebuild(records, vectors)
        if switch_alias
        else index.create_physical_collection(records, vectors)
    )
    return {
        "database_path": str(database_path),
        "qdrant_url": qdrant_url,
        "collection": collection,
        "embedding_model": model,
        "embedding_dimension": dimension,
        "embedded_segment_count": len(texts),
        "embedded_character_count": sum(len(text) for text in texts),
        "embedding_batch_count": (len(texts) + batch_size - 1) // batch_size,
        "embedding_usage": dict(sorted(usage_totals.items())),
        "request_ids": request_ids,
        "indexed_point_count": indexed,
        "alias_switched": switch_alias,
        "duration_ms": round((perf_counter() - started) * 1000),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建立博物馆原文片段向量索引")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--model", default="text-embedding-v4")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--no-switch-alias", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_museum_evidence_index(
            database_path=args.database,
            qdrant_url=args.qdrant_url,
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            collection=args.collection,
            model=args.model,
            dimension=args.dimension,
            batch_size=args.batch_size,
            switch_alias=not args.no_switch_alias,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {"status": "succeeded", **result},
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
