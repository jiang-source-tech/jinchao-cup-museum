from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Any, Iterable

from qdrant_client import QdrantClient
from qdrant_client.http import models


POINT_NAMESPACE = uuid.UUID("49b137b3-40bf-4ae3-a3ad-1aa9fb59fb47")


@dataclass(frozen=True)
class DenseFactHit:
    fact_id: str
    score: float
    payload: dict[str, Any]


class QdrantFactIndex:
    def __init__(
        self,
        *,
        url: str,
        collection_name: str,
        dimension: int,
        timeout_seconds: float = 2.0,
        client: QdrantClient | None = None,
    ):
        self.collection_name = collection_name
        self.dimension = dimension
        self._client = (
            client
            if client is not None
            else QdrantClient(url=url, timeout=timeout_seconds)
        )

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

    def rebuild(
        self,
        records: Iterable[dict[str, Any]],
        vectors: Iterable[list[float]],
    ) -> int:
        records = tuple(records)
        vectors = tuple(vectors)
        if len(records) != len(vectors):
            raise ValueError("record and vector counts differ")
        build_name = (
            f"{self.collection_name}__build_"
            f"{uuid.uuid4().hex}"
        )
        self._client.create_collection(
            collection_name=build_name,
            vectors_config=models.VectorParams(
                size=self.dimension,
                distance=models.Distance.COSINE,
            ),
            on_disk_payload=True,
        )
        try:
            indexed = self._upsert(records, vectors, build_name)
            counted = int(
                self._client.count(
                    collection_name=build_name,
                    exact=True,
                ).count
            )
            if counted != indexed:
                raise RuntimeError(
                    f"Qdrant build count mismatch: indexed={indexed}, counted={counted}"
                )
            previous_build = self._switch_alias(build_name)
            self._delete_stale_builds(
                current_build=build_name,
                previous_build=previous_build,
            )
            return indexed
        except Exception:
            if self._client.collection_exists(build_name):
                self._client.delete_collection(build_name)
            raise

    def create_physical_collection(
        self,
        records: Iterable[dict[str, Any]],
        vectors: Iterable[list[float]],
    ) -> int:
        records = tuple(records)
        vectors = tuple(vectors)
        if len(records) != len(vectors):
            raise ValueError("record and vector counts differ")
        if self._client.collection_exists(self.collection_name):
            raise FileExistsError(
                f"refusing to overwrite existing Qdrant collection: "
                f"{self.collection_name}"
            )
        aliases_before = tuple(
            sorted(
                (alias.alias_name, alias.collection_name)
                for alias in self._client.get_aliases().aliases
            )
        )
        self._client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=self.dimension,
                distance=models.Distance.COSINE,
            ),
            on_disk_payload=True,
        )
        try:
            indexed = self._upsert(records, vectors, self.collection_name)
            counted = int(
                self._client.count(
                    collection_name=self.collection_name,
                    exact=True,
                ).count
            )
            if counted != indexed:
                raise RuntimeError(
                    f"Qdrant build count mismatch: indexed={indexed}, counted={counted}"
                )
            aliases_after = tuple(
                sorted(
                    (alias.alias_name, alias.collection_name)
                    for alias in self._client.get_aliases().aliases
                )
            )
            if aliases_after != aliases_before:
                raise RuntimeError("isolated Qdrant build unexpectedly changed aliases")
            return indexed
        except Exception:
            if self._client.collection_exists(self.collection_name):
                self._client.delete_collection(self.collection_name)
            raise

    def count(self) -> int:
        return int(
            self._client.count(
                collection_name=self.collection_name,
                exact=True,
            ).count
        )

    def all_payloads(self, *, page_size: int = 256) -> tuple[dict[str, Any], ...]:
        payloads: list[dict[str, Any]] = []
        offset = None
        while True:
            points, next_offset = self._client.scroll(
                collection_name=self.collection_name,
                limit=page_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            payloads.extend(dict(point.payload or {}) for point in points)
            if next_offset is None:
                return tuple(payloads)
            offset = next_offset

    def _switch_alias(self, build_name: str) -> str | None:
        aliases = self._client.get_aliases().aliases
        current_alias = next(
            (
                alias
                for alias in aliases
                if alias.alias_name == self.collection_name
            ),
            None,
        )
        operations = []
        if current_alias is not None:
            operations.append(
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=self.collection_name)
                )
            )
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=build_name,
                    alias_name=self.collection_name,
                )
            )
        )
        self._client.update_collection_aliases(operations)
        return (
            str(current_alias.collection_name)
            if current_alias is not None
            else None
        )

    def _upsert(
        self,
        records: tuple[dict[str, Any], ...],
        vectors: tuple[list[float], ...],
        collection_name: str,
    ) -> int:
        indexed = 0
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
                    collection_name=collection_name,
                    points=points,
                    wait=True,
                )
                indexed += len(points)
        return indexed

    def _delete_stale_builds(
        self,
        *,
        current_build: str,
        previous_build: str | None,
    ) -> None:
        prefix = f"{self.collection_name}__build_"
        retained = {current_build}
        if previous_build:
            retained.add(previous_build)
        for collection in self._client.get_collections().collections:
            name = collection.name
            if name.startswith(prefix) and name not in retained:
                self._client.delete_collection(name)
