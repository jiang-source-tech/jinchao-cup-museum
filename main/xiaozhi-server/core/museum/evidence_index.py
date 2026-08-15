from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from qdrant_client import QdrantClient
from qdrant_client.http import models


POINT_NAMESPACE = uuid.UUID("8a8b88a7-3cb5-4a10-bf12-3ab57f75e2cb")
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DenseEvidenceHit:
    segment_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class EvidenceIndexValidation:
    collection: str
    point_count: int
    expected_count: int
    dimension: int

    @property
    def ok(self) -> bool:
        return self.point_count == self.expected_count


class QdrantEvidenceIndex:
    """Versioned Qdrant adapter for source-segment embeddings."""

    def __init__(
        self,
        *,
        url: str,
        collection_name: str,
        dimension: int,
        timeout_seconds: float = 2.0,
        rebuild_lock_timeout_seconds: float = 3600.0,
        rebuild_lock_empty_grace_seconds: float = 2.0,
        client: QdrantClient | None = None,
    ):
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        if rebuild_lock_timeout_seconds <= 0:
            raise ValueError("rebuild lock timeout must be positive")
        if rebuild_lock_empty_grace_seconds < 0:
            raise ValueError("rebuild lock empty grace must not be negative")
        self.collection_name = collection_name
        self.dimension = dimension
        self._rebuild_lock_timeout_seconds = rebuild_lock_timeout_seconds
        self._rebuild_lock_empty_grace_seconds = rebuild_lock_empty_grace_seconds
        self._client = (
            client
            if client is not None
            else QdrantClient(url=url, timeout=timeout_seconds)
        )

    def search(
        self,
        *,
        vector: list[float],
        exhibit_ids: tuple[str, ...] = (),
        source_ids: tuple[str, ...] = (),
        source_levels: tuple[str, ...] = (),
        limit: int = 8,
    ) -> tuple[DenseEvidenceHit, ...]:
        if len(vector) != self.dimension:
            raise ValueError("query vector dimension mismatch")
        if limit <= 0:
            return ()
        conditions: list[models.Condition] = []
        if exhibit_ids:
            conditions.append(
                models.FieldCondition(
                    key="exhibit_ids",
                    match=models.MatchAny(any=list(exhibit_ids)),
                )
            )
        if source_ids:
            conditions.append(
                models.FieldCondition(
                    key="source_id",
                    match=models.MatchAny(any=list(source_ids)),
                )
            )
        if source_levels:
            conditions.append(
                models.FieldCondition(
                    key="source_level",
                    match=models.MatchAny(any=list(source_levels)),
                )
            )
        query_filter = models.Filter(must=conditions) if conditions else None
        response = self._client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return tuple(
            DenseEvidenceHit(
                segment_id=str((point.payload or {}).get("segment_id", "")),
                score=float(point.score),
                payload=dict(point.payload or {}),
            )
            for point in response.points
            if (point.payload or {}).get("segment_id")
        )

    def rebuild(
        self,
        records: Iterable[dict[str, Any]],
        vectors: Iterable[list[float]],
    ) -> int:
        records_tuple = tuple(records)
        vectors_tuple = tuple(vectors)
        self._validate_inputs(records_tuple, vectors_tuple)
        build_name = f"{self.collection_name}__build_{uuid.uuid4().hex}"
        lock_name = f"{self.collection_name}__rebuild_lock"
        lock_owner = self._acquire_rebuild_lock(lock_name)
        build_created = False
        try:
            previous_target = self._alias_target()
            self._create_collection(build_name)
            build_created = True
            indexed = self._upsert(
                records_tuple,
                vectors_tuple,
                build_name,
                lock_name=lock_name,
                lock_owner=lock_owner,
            )
            self._assert_count(build_name, indexed)
            self._assert_rebuild_lock_owner(lock_name, lock_owner)
            self._switch_alias(build_name, expected_target=previous_target)
            self._prune_published_builds()
            return indexed
        except Exception:
            try:
                alias_target = self._alias_target()
            except Exception:
                alias_target = None
            if build_created and alias_target != build_name:
                if self._client.collection_exists(build_name):
                    self._client.delete_collection(build_name)
            raise
        finally:
            self._release_rebuild_lock(lock_name, lock_owner)

    def create_physical_collection(
        self,
        records: Iterable[dict[str, Any]],
        vectors: Iterable[list[float]],
    ) -> int:
        records_tuple = tuple(records)
        vectors_tuple = tuple(vectors)
        self._validate_inputs(records_tuple, vectors_tuple)
        if self._client.collection_exists(self.collection_name):
            raise FileExistsError(
                f"refusing to overwrite existing Qdrant collection: {self.collection_name}"
            )
        aliases_before = tuple(
            sorted(
                (alias.alias_name, alias.collection_name)
                for alias in self._client.get_aliases().aliases
            )
        )
        self._create_collection(self.collection_name)
        try:
            indexed = self._upsert(
                records_tuple,
                vectors_tuple,
                self.collection_name,
            )
            self._assert_count(self.collection_name, indexed)
            aliases_after = tuple(
                sorted(
                    (alias.alias_name, alias.collection_name)
                    for alias in self._client.get_aliases().aliases
                )
            )
            if aliases_after != aliases_before:
                raise RuntimeError("isolated evidence build unexpectedly changed aliases")
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

    def validate(self, expected_count: int) -> EvidenceIndexValidation:
        result = EvidenceIndexValidation(
            collection=self.collection_name,
            point_count=self.count(),
            expected_count=expected_count,
            dimension=self.dimension,
        )
        if not result.ok:
            raise RuntimeError(
                "evidence index count mismatch: "
                f"actual={result.point_count}, expected={result.expected_count}"
            )
        return result

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

    def _validate_inputs(
        self,
        records: tuple[dict[str, Any], ...],
        vectors: tuple[list[float], ...],
    ) -> None:
        if len(records) != len(vectors):
            raise ValueError("record and vector counts differ")
        segment_ids = [str(record.get("segment_id", "")) for record in records]
        if any(not segment_id for segment_id in segment_ids):
            raise ValueError("every evidence record requires segment_id")
        if len(set(segment_ids)) != len(segment_ids):
            raise ValueError("segment IDs must be unique")
        if any(len(vector) != self.dimension for vector in vectors):
            raise ValueError("evidence vector dimension mismatch")

    def _create_collection(self, collection_name: str) -> None:
        self._client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=self.dimension,
                distance=models.Distance.COSINE,
            ),
            on_disk_payload=True,
        )

    def _assert_count(self, collection_name: str, expected: int) -> None:
        counted = int(
            self._client.count(collection_name=collection_name, exact=True).count
        )
        if counted != expected:
            raise RuntimeError(
                f"evidence build count mismatch: indexed={expected}, counted={counted}"
            )

    def _upsert(
        self,
        records: tuple[dict[str, Any], ...],
        vectors: tuple[list[float], ...],
        collection_name: str,
        *,
        lock_name: str = "",
        lock_owner: str = "",
    ) -> int:
        indexed = 0
        for offset in range(0, len(records), 100):
            if lock_name:
                self._refresh_rebuild_lock(lock_name, lock_owner)
            points = [
                models.PointStruct(
                    id=str(uuid.uuid5(POINT_NAMESPACE, str(record["segment_id"]))),
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

    def _switch_alias(
        self,
        build_name: str,
        *,
        expected_target: str | None,
    ) -> None:
        aliases = self._client.get_aliases().aliases
        current_alias = next(
            (
                alias
                for alias in aliases
                if alias.alias_name == self.collection_name
            ),
            None,
        )
        current_target = (
            str(current_alias.collection_name) if current_alias is not None else None
        )
        if current_target != expected_target:
            raise RuntimeError(
                "evidence alias changed during rebuild; refusing stale switch"
            )
        history_prefix = f"{self.collection_name}__history_"
        history_sequence = max(
            (
                _history_alias_sequence(alias.alias_name, history_prefix)
                for alias in aliases
                if alias.alias_name.startswith(history_prefix)
            ),
            default=0,
        ) + 1
        history_alias = (
            f"{history_prefix}{history_sequence:020d}_{uuid.uuid4().hex}"
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
                    alias_name=history_alias,
                )
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

    def _prune_published_builds(self) -> None:
        history_prefix = f"{self.collection_name}__history_"
        try:
            aliases = tuple(self._client.get_aliases().aliases)
        except Exception:
            LOGGER.warning(
                "failed to inspect Qdrant evidence index history",
                exc_info=True,
            )
            return

        current_target = next(
            (
                str(alias.collection_name)
                for alias in aliases
                if alias.alias_name == self.collection_name
            ),
            "",
        )
        published = sorted(
            (
                (alias.alias_name, str(alias.collection_name))
                for alias in aliases
                if alias.alias_name.startswith(history_prefix)
            ),
            key=lambda item: (
                _history_alias_sequence(item[0], history_prefix),
                item[0],
            ),
            reverse=True,
        )
        retained_targets = {current_target}
        for _, target in published:
            if target not in retained_targets:
                retained_targets.add(target)
                break

        for alias_name, target in published:
            if target in retained_targets:
                continue
            try:
                self._delete_published_build(
                    history_alias=alias_name,
                    collection_name=target,
                )
            except Exception:
                LOGGER.warning(
                    "failed to prune stale Qdrant evidence collection %s",
                    target,
                    exc_info=True,
                )

    def _delete_published_build(
        self,
        *,
        history_alias: str,
        collection_name: str,
    ) -> None:
        aliases = tuple(self._client.get_aliases().aliases)
        references = {
            alias.alias_name
            for alias in aliases
            if str(alias.collection_name) == collection_name
        }
        if history_alias not in references or references != {history_alias}:
            return
        if self._client.collection_exists(collection_name):
            self._client.delete_collection(collection_name)

    def _alias_target(self) -> str | None:
        current_alias = next(
            (
                alias
                for alias in self._client.get_aliases().aliases
                if alias.alias_name == self.collection_name
            ),
            None,
        )
        return str(current_alias.collection_name) if current_alias is not None else None

    def _acquire_rebuild_lock(self, lock_name: str) -> str:
        owner = uuid.uuid4().hex
        for _attempt in range(4):
            try:
                self._create_collection(lock_name)
            except Exception as exc:
                if not self._client.collection_exists(lock_name):
                    raise
                metadata = self._rebuild_lock_metadata(lock_name)
                if metadata is None and self._rebuild_lock_empty_grace_seconds:
                    time.sleep(self._rebuild_lock_empty_grace_seconds)
                    metadata = self._rebuild_lock_metadata(lock_name)
                if metadata is not None:
                    active_owner, heartbeat_at = metadata
                    age_seconds = max(0.0, time.time() - heartbeat_at)
                    if age_seconds <= self._rebuild_lock_timeout_seconds:
                        raise RuntimeError(
                            "evidence rebuild already in progress: "
                            f"{self.collection_name} (owner={active_owner})"
                        ) from exc
                LOGGER.warning(
                    "recovering stale Qdrant evidence rebuild lock %s",
                    lock_name,
                )
                try:
                    self._client.delete_collection(lock_name)
                except Exception as cleanup_exc:
                    if self._client.collection_exists(lock_name):
                        raise RuntimeError(
                            "failed to recover stale evidence rebuild lock: "
                            f"{self.collection_name}"
                        ) from cleanup_exc
                continue

            try:
                self._write_rebuild_lock(lock_name, owner)
                self._assert_rebuild_lock_owner(lock_name, owner)
                return owner
            except Exception:
                try:
                    if self._client.collection_exists(lock_name):
                        self._client.delete_collection(lock_name)
                except Exception:
                    LOGGER.error(
                        "failed to clean an uninitialized evidence rebuild lock %s",
                        lock_name,
                        exc_info=True,
                    )
                raise
        raise RuntimeError(
            f"failed to acquire evidence rebuild lock: {self.collection_name}"
        )

    def _write_rebuild_lock(self, lock_name: str, owner: str) -> None:
        self._client.upsert(
            collection_name=lock_name,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid5(POINT_NAMESPACE, f"lock:{lock_name}")),
                    vector=[0.0] * self.dimension,
                    payload={
                        "kind": "evidence_rebuild_lock",
                        "owner": owner,
                        "heartbeat_at": time.time(),
                    },
                )
            ],
            wait=True,
        )

    def _rebuild_lock_metadata(self, lock_name: str) -> tuple[str, float] | None:
        if not self._client.collection_exists(lock_name):
            return None
        points, _next_offset = self._client.scroll(
            collection_name=lock_name,
            limit=2,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = dict(point.payload or {})
            if payload.get("kind") != "evidence_rebuild_lock":
                continue
            owner = str(payload.get("owner", ""))
            try:
                heartbeat_at = float(payload.get("heartbeat_at"))
            except (TypeError, ValueError):
                continue
            if owner:
                return owner, heartbeat_at
        return None

    def _assert_rebuild_lock_owner(self, lock_name: str, owner: str) -> None:
        metadata = self._rebuild_lock_metadata(lock_name)
        if metadata is None or metadata[0] != owner:
            raise RuntimeError(
                f"evidence rebuild lock ownership lost: {self.collection_name}"
            )

    def _refresh_rebuild_lock(self, lock_name: str, owner: str) -> None:
        self._assert_rebuild_lock_owner(lock_name, owner)
        self._write_rebuild_lock(lock_name, owner)

    def _release_rebuild_lock(self, lock_name: str, owner: str) -> None:
        for _attempt in range(3):
            try:
                if not self._client.collection_exists(lock_name):
                    return
                metadata = self._rebuild_lock_metadata(lock_name)
                if metadata is None or metadata[0] != owner:
                    LOGGER.error(
                        "refusing to release evidence rebuild lock owned by another process: %s",
                        lock_name,
                    )
                    return
                self._client.delete_collection(lock_name)
                if not self._client.collection_exists(lock_name):
                    return
            except Exception:
                LOGGER.warning(
                    "failed to release Qdrant evidence rebuild lock %s",
                    lock_name,
                    exc_info=True,
                )
        LOGGER.error(
            "Qdrant evidence rebuild published but retained lock %s; "
            "a later rebuild can recover it after the lease expires",
            lock_name,
        )


def _history_alias_sequence(alias_name: str, prefix: str) -> int:
    raw_sequence = alias_name[len(prefix) :].split("_", 1)[0]
    try:
        return int(raw_sequence)
    except ValueError:
        return 0
