from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config.settings import load_config  # noqa: E402
from core.museum.knowledge_release import (  # noqa: E402
    build_knowledge_release_manifest,
    verify_knowledge_release_payloads,
)
from core.museum.qdrant_index import QdrantFactIndex  # noqa: E402
from core.museum.store import MuseumStore  # noqa: E402


IndexFactory = Callable[..., QdrantFactIndex]


def verify_from_config(
    config: dict[str, Any],
    *,
    qdrant_url: str | None = None,
    index_factory: IndexFactory = QdrantFactIndex,
) -> dict[str, Any]:
    runtime = config.get("business_runtime", {})
    retrieval = runtime.get("retrieval", {})
    configured_path = Path(
        str(runtime.get("database_path", "data/museum_demo.db"))
    )
    database_path = (
        configured_path
        if configured_path.is_absolute()
        else SERVER_ROOT / configured_path
    )
    dimension = int(retrieval.get("embedding_dimension", 1024))
    model = str(retrieval.get("embedding_model", "text-embedding-v4"))
    collection = str(retrieval.get("qdrant_collection", "museum_facts_v1"))
    store = MuseumStore(database_path)
    manifest = build_knowledge_release_manifest(
        store.published_fact_index_records(),
        embedding_model=model,
        embedding_dimension=dimension,
        collection=collection,
    )
    result: dict[str, Any] = {
        "ok": True,
        "mode": "manifest_only",
        "database_path": str(database_path),
        "manifest": manifest,
        "qdrant": {
            "checked": False,
            "reason": "qdrant_url_not_provided",
        },
    }
    if qdrant_url is None:
        return result

    index = index_factory(
        url=qdrant_url,
        collection_name=collection,
        dimension=dimension,
        timeout_seconds=float(retrieval.get("qdrant_timeout_seconds", 10)),
    )
    verification = verify_knowledge_release_payloads(
        manifest,
        index.all_payloads(),
    )
    result.update(
        {
            "ok": verification["ok"],
            "mode": "qdrant_verified",
            "qdrant": {
                "checked": True,
                "url": qdrant_url,
                "collection_alias": collection,
                **verification,
            },
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成确定性知识发布清单，并按需校验 Qdrant alias 中的点"
    )
    parser.add_argument(
        "--qdrant-url",
        help="提供后校验 Qdrant；省略时仅生成和核对发布清单",
    )
    parser.add_argument("--output", type=Path, help="可选 JSON 输出文件")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = verify_from_config(
        load_config(),
        qdrant_url=args.qdrant_url,
    )
    rendered = json.dumps(
        result,
        ensure_ascii=False,
        indent=2 if args.pretty else None,
    )
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
