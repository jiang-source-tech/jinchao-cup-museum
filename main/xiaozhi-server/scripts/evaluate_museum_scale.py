from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import tempfile
from time import perf_counter
from typing import Any, Iterable
import uuid


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.http import models  # noqa: E402

from core.museum.content_import import (  # noqa: E402
    import_draft_content,
    parse_content_package,
    publish_revision,
    review_revision,
)
from core.museum.qdrant_index import DenseFactHit  # noqa: E402
from core.museum.retrieval import (  # noqa: E402
    HybridEvidenceRetriever,
    RetrievalRequest,
)
from core.museum.store import MuseumStore  # noqa: E402


FACT_TYPES = ("material", "era", "craft", "usage")
FACTS_PER_EXHIBIT = 20
POINT_NAMESPACE = uuid.UUID("f7b439f0-b57d-4c91-9d7c-b5330c4bad85")
TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*|[\u4e00-\u9fff]", re.IGNORECASE)


@dataclass(frozen=True)
class DeterministicHashEmbedder:
    """A repeatable, network-free embedder for load testing only."""

    model: str = "deterministic-hash-v1"
    dimension: int = 128

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = TOKEN_PATTERN.findall(text.lower())
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            weight = 8.0 if token.startswith("scale-key-") else 1.0
            vector[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            return [value / norm for value in vector]
        return vector

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class EmbeddedQdrantFactIndex:
    """Qdrant local mode adapter with the production retriever interface."""

    def __init__(self, *, dimension: int):
        self.collection_name = f"museum_scale_{uuid.uuid4().hex}"
        self.dimension = dimension
        self._client = QdrantClient(location=":memory:")
        self._client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=dimension,
                distance=models.Distance.COSINE,
            ),
        )

    def rebuild(
        self,
        records: Iterable[dict[str, Any]],
        vectors: Iterable[list[float]],
    ) -> int:
        records = tuple(records)
        vectors = tuple(vectors)
        if len(records) != len(vectors):
            raise ValueError("record and vector counts differ")
        for offset in range(0, len(records), 100):
            points = [
                models.PointStruct(
                    id=str(uuid.uuid5(POINT_NAMESPACE, str(record["fact_id"]))),
                    vector=vector,
                    payload=record,
                )
                for record, vector in zip(
                    records[offset : offset + 100],
                    vectors[offset : offset + 100],
                    strict=True,
                )
            ]
            if points:
                self._client.upsert(
                    collection_name=self.collection_name,
                    points=points,
                    wait=True,
                )
        return self.count()

    def search(
        self,
        *,
        vector: list[float],
        exhibit_id: str,
        fact_types: tuple[str, ...],
        limit: int,
    ) -> tuple[DenseFactHit, ...]:
        conditions: list[models.Condition] = [
            models.FieldCondition(
                key="exhibit_id",
                match=models.MatchValue(value=exhibit_id),
            )
        ]
        if fact_types:
            conditions.append(
                models.FieldCondition(
                    key="fact_type",
                    match=models.MatchAny(any=list(fact_types)),
                )
            )
        response = self._client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=models.Filter(must=conditions),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return tuple(
            DenseFactHit(
                fact_id=str((point.payload or {}).get("fact_id", "")),
                score=float(point.score),
                payload=dict(point.payload or {}),
            )
            for point in response.points
            if (point.payload or {}).get("fact_id")
        )

    def count(self) -> int:
        return int(
            self._client.count(
                collection_name=self.collection_name,
                exact=True,
            ).count
        )

    def close(self) -> None:
        self._client.close()


def evaluate_scale(
    *,
    fact_count: int = 1000,
    query_count: int = 200,
    embedding_dimension: int = 128,
) -> dict[str, Any]:
    if fact_count < 1:
        raise ValueError("fact_count must be positive")
    if query_count < 1:
        raise ValueError("query_count must be positive")
    if embedding_dimension < 8:
        raise ValueError("embedding_dimension must be at least 8")

    started = perf_counter()
    temporary_path = ""
    cleanup_performed = False
    result: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(prefix="museum-scale-eval-") as directory:
        workspace = Path(directory)
        temporary_path = str(workspace)
        database_path = workspace / "scale-evaluation.db"
        store = MuseumStore(database_path)

        package_started = perf_counter()
        package = parse_content_package(_scale_package(fact_count))
        imported = import_draft_content(store, package)
        import_ms = _elapsed_ms(package_started)

        publish_started = perf_counter()
        occurred_at = datetime(2026, 8, 12, tzinfo=timezone.utc)
        for revision_id in imported.revision_ids:
            review_revision(
                store,
                revision_id=revision_id,
                reviewed_by="scale-evaluator",
                reviewed_at=occurred_at,
            )
            publish_revision(
                store,
                revision_id=revision_id,
                published_by="scale-evaluator",
                published_at=occurred_at,
            )
        publish_ms = _elapsed_ms(publish_started)

        export_started = perf_counter()
        records = [dict(record) for record in store.published_fact_index_records()]
        export_ms = _elapsed_ms(export_started)
        if len(records) != fact_count:
            raise RuntimeError(
                f"published fact count mismatch: expected={fact_count}, actual={len(records)}"
            )

        embedder = DeterministicHashEmbedder(dimension=embedding_dimension)
        index = EmbeddedQdrantFactIndex(dimension=embedding_dimension)
        try:
            index_started = perf_counter()
            vectors = embedder.embed_many([_index_text(record) for record in records])
            indexed_count = index.rebuild(records, vectors)
            index_ms = _elapsed_ms(index_started)
            if indexed_count != fact_count:
                raise RuntimeError(
                    f"vector count mismatch: expected={fact_count}, actual={indexed_count}"
                )

            retriever = HybridEvidenceRetriever(
                store=store,
                embedder=embedder,
                index=index,
                mode="hybrid",
                dense_score_threshold=0.05,
            )
            cases = _sample_cases(records, min(query_count, fact_count))
            query_result = _run_queries(retriever, cases)
        finally:
            index.close()

        result = {
            "dataset": {
                "synthetic": True,
                "exhibit_count": imported.exhibit_count,
                "fact_count": imported.fact_count,
                "source_count": imported.source_count,
                "query_count": len(cases),
            },
            "isolation": {
                "temporary_workspace": temporary_path,
                "database_was_temporary": database_path.parent == workspace,
                "production_database_touched": False,
                "qdrant_backend": "embedded-memory",
                "external_embedding_calls": 0,
            },
            "index": {
                "embedding_model": embedder.model,
                "embedding_dimension": embedder.dimension,
                "indexed_point_count": indexed_count,
            },
            "recall_at_3": query_result["recall_at_3"],
            "latency_ms": query_result["latency_ms"],
            "setup_ms": {
                "import": import_ms,
                "review_and_publish": publish_ms,
                "export_index_records": export_ms,
                "embed_and_index": index_ms,
                "total_before_cleanup": _elapsed_ms(started),
            },
            "limitations": [
                "Synthetic facts validate scale, isolation, publication, filtering, and latency only.",
                "Deterministic hash vectors do not measure text-embedding-v4 semantic quality.",
                "Embedded Qdrant local mode does not reproduce production network or server load.",
            ],
            "sample_failures": query_result["failures"][:10],
        }
    cleanup_performed = not Path(temporary_path).exists()
    if result is None:
        raise RuntimeError("scale evaluation did not produce a result")
    result["isolation"]["cleanup_performed"] = cleanup_performed
    return result


def _scale_package(fact_count: int) -> dict[str, Any]:
    exhibit_count = math.ceil(fact_count / FACTS_PER_EXHIBIT)
    exhibits = []
    next_fact = 0
    for exhibit_number in range(exhibit_count):
        exhibit_id = f"scale-exhibit-{exhibit_number:05d}"
        facts = []
        for local_fact_number in range(FACTS_PER_EXHIBIT):
            if next_fact >= fact_count:
                break
            fact_type = FACT_TYPES[local_fact_number % len(FACT_TYPES)]
            keyword = f"scale-key-{next_fact:06d}"
            facts.append(
                {
                    "id": f"scale-fact-{next_fact:06d}",
                    "type": fact_type,
                    "statement": (
                        f"Synthetic {fact_type} evidence for {keyword}; "
                        "this statement is for isolated scale evaluation only."
                    ),
                    "keywords": [keyword, f"scale-{fact_type}"],
                    "confidence": "synthetic-evaluation",
                    "sources": ["scale-source"],
                }
            )
            next_fact += 1
        exhibits.append(
            {
                "id": exhibit_id,
                "zone_id": "scale-zone",
                "name": f"Scale Exhibit {exhibit_number:05d}",
                "aliases": [f"Scale Alias {exhibit_number:05d}"],
                "status": "active",
                "revision": {
                    "id": f"{exhibit_id}-r1",
                    "number": 1,
                    "status": "draft",
                    "facts": facts,
                },
            }
        )
    return {
        "schema_version": 1,
        "museum": {
            "id": "scale-evaluation-museum",
            "name": "Isolated Scale Evaluation Museum",
            "status": "active",
        },
        "zones": [
            {"id": "scale-zone", "name": "Scale Evaluation Zone", "sort_order": 1}
        ],
        "sources": [
            {
                "id": "scale-source",
                "title": "Synthetic scale evaluation source",
                "source_type": "synthetic_evaluation",
                "locator": "scale-evaluation://generated",
                "rights_note": "Generated locally; never publish as museum content.",
            }
        ],
        "exhibits": exhibits,
    }


def _index_text(record: dict[str, Any]) -> str:
    return "\n".join(
        (
            str(record["exhibit_name"]),
            " ".join(str(value) for value in record.get("aliases", [])),
            str(record["fact_type"]),
            str(record["statement"]),
            " ".join(str(value) for value in record.get("keywords", [])),
        )
    )


def _sample_cases(
    records: list[dict[str, Any]],
    query_count: int,
) -> list[dict[str, str]]:
    if query_count == len(records):
        selected = records
    else:
        selected = [
            records[min(len(records) - 1, index * len(records) // query_count)]
            for index in range(query_count)
        ]
    return [
        {
            "exhibit_id": str(record["exhibit_id"]),
            "fact_id": str(record["fact_id"]),
            "fact_type": str(record["fact_type"]),
            "keyword": str(record["keywords"][0]),
        }
        for record in selected
    ]


def _run_queries(
    retriever: HybridEvidenceRetriever,
    cases: list[dict[str, str]],
) -> dict[str, Any]:
    hits = {"lexical": 0, "dense": 0, "hybrid": 0, "selected": 0}
    latencies: list[float] = []
    failures: list[dict[str, Any]] = []
    for case in cases:
        started = perf_counter()
        result = retriever.retrieve(
            RetrievalRequest(
                exhibit_id=case["exhibit_id"],
                question=f"Find the evidence tagged {case['keyword']}",
                limit=3,
                fact_types=(case["fact_type"],),
                query_terms=(case["keyword"],),
                allow_dense_only=True,
                dense_fact_types=(case["fact_type"],),
            )
        )
        latencies.append((perf_counter() - started) * 1000)
        branches = {
            "lexical": [item.fact_id for item in result.diagnostics.lexical_candidates],
            "dense": [item.fact_id for item in result.diagnostics.dense_candidates],
            "hybrid": [item.fact_id for item in result.diagnostics.fused_candidates],
            "selected": list(result.evidence.fact_ids) if result.evidence else [],
        }
        missed = []
        for name, fact_ids in branches.items():
            matched = case["fact_id"] in fact_ids[:3]
            hits[name] += int(matched)
            if not matched:
                missed.append(name)
        if missed:
            failures.append({**case, "missed_branches": missed, "actual": branches})
    return {
        "recall_at_3": {
            name: round(count / len(cases), 4) for name, count in hits.items()
        },
        "latency_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "failures": failures,
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run an isolated thousand-fact museum retrieval scale evaluation."
    )
    parser.add_argument("--facts", type=int, default=1000)
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--dimension", type=int, default=128)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = evaluate_scale(
        fact_count=args.facts,
        query_count=args.queries,
        embedding_dimension=args.dimension,
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
