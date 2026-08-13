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
from core.museum.knowledge_release import (  # noqa: E402
    build_index_text,
    build_knowledge_release_manifest,
    prepare_index_records,
    verify_knowledge_release_payloads,
)
from core.museum.qdrant_index import QdrantFactIndex  # noqa: E402
from core.museum.store import MuseumStore  # noqa: E402


ISOLATED_COLLECTION = "museum_facts_stage3_isolated_v1"


def build_isolated_vector_index(
    *,
    database_path: Path,
    qdrant_url: str,
    api_key: str,
    collection: str = ISOLATED_COLLECTION,
    model: str = "text-embedding-v4",
    dimension: int = 1024,
    batch_size: int = 10,
    embedder_factory: Callable[..., Any] = DashScopeTextEmbedder,
    index_factory: Callable[..., Any] = QdrantFactIndex,
) -> dict[str, Any]:
    if batch_size < 1 or batch_size > 10:
        raise ValueError("batch_size must be between 1 and 10")
    if not database_path.is_file():
        raise FileNotFoundError(f"isolated database does not exist: {database_path}")

    store = MuseumStore(database_path)
    records = prepare_index_records(
        store.published_fact_index_records(),
        embedding_model=model,
        embedding_dimension=dimension,
    )
    if not records:
        raise RuntimeError("isolated database has no published facts")
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
    indexed = index.create_physical_collection(records, vectors)
    manifest = build_knowledge_release_manifest(
        records,
        embedding_model=model,
        embedding_dimension=dimension,
        collection=collection,
    )
    verification = verify_knowledge_release_payloads(manifest, index.all_payloads())
    if not verification["ok"]:
        raise RuntimeError("isolated Qdrant payload verification failed")

    return {
        "database_path": str(database_path),
        "qdrant_url": qdrant_url,
        "collection": collection,
        "embedding_model": model,
        "embedding_dimension": dimension,
        "embedding_batch_count": (len(texts) + batch_size - 1) // batch_size,
        "embedded_text_count": len(texts),
        "embedded_character_count": sum(len(text) for text in texts),
        "embedding_usage": dict(sorted(usage_totals.items())),
        "request_ids": request_ids,
        "indexed_point_count": indexed,
        "release_id": manifest["release_id"],
        "payload_verification": verification,
        "duration_ms": round((perf_counter() - started) * 1000),
        "alias_switched": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="为阶段 3 隔离 SQLite 建立不切换 alias 的 Qdrant 向量索引"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--collection", default=ISOLATED_COLLECTION)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = build_isolated_vector_index(
        database_path=args.database,
        qdrant_url=args.qdrant_url,
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        collection=args.collection,
        batch_size=args.batch_size,
    )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
