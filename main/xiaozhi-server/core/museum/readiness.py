from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from core.museum.knowledge_release import (
    build_knowledge_release_manifest,
    verify_knowledge_release_payloads,
)
from core.museum.qdrant_index import QdrantFactIndex
from core.museum.store import MuseumStore


IndexFactory = Callable[..., QdrantFactIndex]


def check_museum_readiness(
    config: dict[str, Any],
    *,
    server_root: str | Path,
    index_factory: IndexFactory = QdrantFactIndex,
) -> dict[str, Any]:
    runtime = config.get("business_runtime", {})
    retrieval = runtime.get("retrieval", {})
    configured_path = Path(
        str(runtime.get("database_path", "data/museum_demo.db"))
    )
    root = Path(server_root)
    database_path = (
        configured_path if configured_path.is_absolute() else root / configured_path
    )
    checks: list[dict[str, Any]] = []
    if not database_path.exists():
        return _result(
            checks=[_check("database_exists", False, str(database_path))],
            mode=str(retrieval.get("mode", "rules")),
        )

    try:
        with sqlite3.connect(database_path) as connection:
            integrity = str(
                connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
        checks.append(_check("database_integrity", integrity == "ok", integrity))
        store = MuseumStore(database_path, read_only=True)
        records = store.published_fact_index_records()
    except Exception as exc:
        checks.append(_check("database_readable", False, type(exc).__name__))
        return _result(checks=checks, mode=str(retrieval.get("mode", "rules")))

    published_count = len(records)
    checks.append(
        _check(
            "published_facts_available",
            published_count > 0,
            str(published_count),
        )
    )
    mode = str(retrieval.get("mode", "rules")).strip().lower()
    if mode == "rules":
        return _result(checks=checks, mode=mode)

    dimension = int(retrieval.get("embedding_dimension", 1024))
    model = str(retrieval.get("embedding_model", "text-embedding-v4"))
    collection = str(retrieval.get("qdrant_collection", "museum_facts_v1"))
    qdrant_url = _expand_env_default(
        str(retrieval.get("qdrant_url", "http://qdrant:6333"))
    )
    manifest = build_knowledge_release_manifest(
        records,
        embedding_model=model,
        embedding_dimension=dimension,
        collection=collection,
    )
    try:
        index = index_factory(
            url=qdrant_url,
            collection_name=collection,
            dimension=dimension,
            timeout_seconds=float(retrieval.get("qdrant_timeout_seconds", 2)),
        )
        verification = verify_knowledge_release_payloads(
            manifest,
            index.all_payloads(),
        )
        checks.append(
            _check(
                "qdrant_release_matches",
                bool(verification["ok"]),
                verification,
            )
        )
    except Exception as exc:
        checks.append(_check("qdrant_reachable", False, type(exc).__name__))
    result = _result(checks=checks, mode=mode)
    result["release_id"] = manifest["release_id"]
    result["published_fact_count"] = published_count
    return result


def _result(*, checks: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    return {
        "ready": all(bool(check["ok"]) for check in checks),
        "mode": mode,
        "checks": checks,
    }


def _check(name: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _expand_env_default(value: str) -> str:
    import os

    if value.startswith("${") and value.endswith("}"):
        expression = value[2:-1]
        name, separator, default = expression.partition(":-")
        if separator:
            return os.getenv(name, default)
        return os.getenv(name, value)
    return value
