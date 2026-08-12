from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
import os
from typing import Any

from core.conversation_runtime import ConversationRuntime
from core.museum.runtime import MuseumRuntime
from core.museum.store import MuseumStore
from core.museum.embedding import DashScopeTextEmbedder
from core.museum.qdrant_index import QdrantFactIndex
from core.museum.retrieval import (
    HybridEvidenceRetriever,
    SqliteEvidenceRetriever,
    VALID_RETRIEVAL_MODES,
)


def create_conversation_runtime(
    config: Mapping[str, Any],
) -> ConversationRuntime:
    runtime_config = config.get("business_runtime", {})
    if not isinstance(runtime_config, Mapping):
        raise ValueError("business_runtime must be a mapping")

    runtime_type = str(runtime_config.get("type", "museum")).strip().lower()
    if runtime_type != "museum":
        raise ValueError("only business_runtime.type=museum is supported")
    exhibit_context_mode = str(
        runtime_config.get("exhibit_context_mode", "explicit")
    ).strip().lower()
    if exhibit_context_mode not in {"explicit", "demo_placement"}:
        raise ValueError(
            "business_runtime.exhibit_context_mode must be explicit or demo_placement"
        )
    database_path = Path(
        str(runtime_config.get("database_path", "data/museum_demo.db"))
    )
    session_idle_ttl_minutes = int(
        runtime_config.get("session_idle_ttl_minutes", 5)
    )
    session_max_ttl_minutes = int(
        runtime_config.get("session_max_ttl_minutes", 30)
    )
    store = MuseumStore(
        database_path,
        session_idle_ttl_minutes=session_idle_ttl_minutes,
        session_max_ttl_minutes=session_max_ttl_minutes,
    )
    if bool(runtime_config.get("seed_demo_content", False)):
        store.seed_demo_content()
    retrieval_config = runtime_config.get("retrieval", {})
    if not isinstance(retrieval_config, Mapping):
        raise ValueError("business_runtime.retrieval must be a mapping")
    retrieval_mode = str(retrieval_config.get("mode", "rules")).strip().lower()
    if retrieval_mode not in VALID_RETRIEVAL_MODES:
        raise ValueError(
            "business_runtime.retrieval.mode must be rules, shadow or hybrid"
        )
    lexical_limit = int(retrieval_config.get("lexical_limit", 12))
    if retrieval_mode == "rules":
        retriever = SqliteEvidenceRetriever(
            store,
            candidate_limit=lexical_limit,
        )
    else:
        dimension = int(retrieval_config.get("embedding_dimension", 1024))
        qdrant_url = _expand_env_default(
            str(
                retrieval_config.get(
                    "qdrant_url",
                    os.getenv("MUSEUM_QDRANT_URL", "http://qdrant:6333"),
                )
            )
        )
        embedder = DashScopeTextEmbedder(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            model=str(
                retrieval_config.get("embedding_model", "text-embedding-v4")
            ),
            dimension=dimension,
            timeout_seconds=float(
                retrieval_config.get("embedding_timeout_seconds", 3)
            ),
        )
        index = QdrantFactIndex(
            url=qdrant_url,
            collection_name=str(
                retrieval_config.get("qdrant_collection", "museum_facts_v1")
            ),
            dimension=dimension,
            timeout_seconds=float(
                retrieval_config.get("qdrant_timeout_seconds", 2)
            ),
        )
        retriever = HybridEvidenceRetriever(
            store=store,
            embedder=embedder,
            index=index,
            mode=retrieval_mode,
            lexical_limit=lexical_limit,
            dense_limit=int(retrieval_config.get("dense_limit", 12)),
            dense_score_threshold=float(
                retrieval_config.get("dense_score_threshold", 0.5)
            ),
            rrf_k=int(retrieval_config.get("rrf_k", 60)),
            circuit_failure_threshold=int(
                retrieval_config.get("circuit_failure_threshold", 3)
            ),
            circuit_cooldown_seconds=float(
                retrieval_config.get("circuit_cooldown_seconds", 30)
            ),
        )
    demo_device_id = str(runtime_config.get("demo_device_id", "")).strip()
    if demo_device_id and exhibit_context_mode == "demo_placement":
        store.ensure_demo_placement(
            demo_device_id,
            datetime.now().astimezone(),
        )
    return MuseumRuntime(
        store,
        auto_assign_unknown_devices=bool(
            runtime_config.get("auto_assign_unknown_devices", False)
        ),
        exhibit_context_mode=exhibit_context_mode,
        retriever=retriever,
    )


def _expand_env_default(value: str) -> str:
    if value.startswith("${") and value.endswith("}"):
        expression = value[2:-1]
        name, separator, default = expression.partition(":-")
        if separator:
            return os.getenv(name, default)
        return os.getenv(name, value)
    return value
