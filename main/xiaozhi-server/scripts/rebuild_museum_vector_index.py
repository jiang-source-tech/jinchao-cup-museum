from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from time import perf_counter


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config.settings import load_config  # noqa: E402
from core.museum.embedding import DashScopeTextEmbedder  # noqa: E402
from core.museum.knowledge_release import (  # noqa: E402
    build_index_text,
    prepare_index_records,
)
from core.museum.qdrant_index import QdrantFactIndex  # noqa: E402
from core.museum.store import MuseumStore  # noqa: E402


def rebuild_from_config(config: dict) -> dict[str, object]:
    runtime = config.get("business_runtime", {})
    retrieval = runtime.get("retrieval", {})
    database_path = SERVER_ROOT / str(
        runtime.get("database_path", "data/museum_demo.db")
    )
    dimension = int(retrieval.get("embedding_dimension", 1024))
    model = str(retrieval.get("embedding_model", "text-embedding-v4"))
    collection = str(retrieval.get("qdrant_collection", "museum_facts_v1"))
    qdrant_url = _expand_env_default(
        str(retrieval.get("qdrant_url", "http://qdrant:6333"))
    )
    store = MuseumStore(database_path)
    records = prepare_index_records(
        store.published_fact_index_records(),
        embedding_model=model,
        embedding_dimension=dimension,
    )
    texts = [build_index_text(record) for record in records]
    embedder = DashScopeTextEmbedder(
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        model=model,
        dimension=dimension,
        timeout_seconds=float(retrieval.get("embedding_timeout_seconds", 10)),
    )
    started = perf_counter()
    vectors: list[list[float]] = []
    for offset in range(0, len(texts), 10):
        vectors.extend(embedder.embed_many(texts[offset : offset + 10]))
    index = QdrantFactIndex(
        url=qdrant_url,
        collection_name=collection,
        dimension=dimension,
        timeout_seconds=float(retrieval.get("qdrant_timeout_seconds", 10)),
    )
    indexed = index.rebuild(records, vectors)
    counted = index.count()
    if indexed != counted:
        raise RuntimeError(
            f"Qdrant point count mismatch: indexed={indexed}, counted={counted}"
        )
    return {
        "database_path": str(database_path),
        "collection": collection,
        "embedding_model": model,
        "embedding_dimension": dimension,
        "published_fact_count": len(records),
        "indexed_point_count": counted,
        "duration_ms": round((perf_counter() - started) * 1000),
    }


def _expand_env_default(value: str) -> str:
    if value.startswith("${") and value.endswith("}"):
        expression = value[2:-1]
        name, separator, default = expression.partition(":-")
        if separator:
            return os.getenv(name, default)
        return os.getenv(name, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="重建当前已发布博物馆事实的 Qdrant 向量索引"
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = rebuild_from_config(load_config())
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
