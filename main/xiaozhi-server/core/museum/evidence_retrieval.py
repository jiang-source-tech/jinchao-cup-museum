from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Protocol
import uuid

from core.museum.contracts import (
    EvidenceClaim,
    EvidenceItem,
    EvidencePack,
)
from core.museum.embedding import TextEmbedder
from core.museum.evidence_index import DenseEvidenceHit
from core.museum.evidence_store import (
    EvidenceStore,
    RankedSourceSegment,
)
from core.museum.store import MuseumStore


class EvidenceIndex(Protocol):
    collection_name: str

    def search(
        self,
        *,
        vector: list[float],
        exhibit_ids: tuple[str, ...] = (),
        source_ids: tuple[str, ...] = (),
        source_levels: tuple[str, ...] = (),
        limit: int = 8,
    ) -> tuple[DenseEvidenceHit, ...]: ...


@dataclass(frozen=True)
class EvidenceSearchRequest:
    question: str
    exhibit_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    fact_types: tuple[str, ...] = ()
    source_levels: tuple[str, ...] = (
        "primary_public_source",
        "secondary_public_source",
        "demo_curated",
    )
    limit: int = 8
    query_id: str = ""


class EvidenceSearchService:
    """Hybrid source-segment retrieval kept separate from legacy fact retrieval."""

    def __init__(
        self,
        *,
        store: MuseumStore,
        embedder: TextEmbedder,
        index: EvidenceIndex,
        lexical_limit: int = 12,
        dense_limit: int = 12,
        dense_score_threshold: float = 0.5,
        rrf_k: int = 60,
    ):
        if lexical_limit <= 0 or dense_limit <= 0 or rrf_k <= 0:
            raise ValueError("retrieval limits and rrf_k must be positive")
        if not -1.0 <= dense_score_threshold <= 1.0:
            raise ValueError("dense_score_threshold must be between -1 and 1")
        self._evidence_store = EvidenceStore(store)
        self._store = store
        self._embedder = embedder
        self._index = index
        self._lexical_limit = lexical_limit
        self._dense_limit = dense_limit
        self._dense_score_threshold = dense_score_threshold
        self._rrf_k = rrf_k

    def search(self, request: EvidenceSearchRequest) -> EvidencePack:
        if request.limit <= 0:
            raise ValueError("request.limit must be positive")
        query_id = request.query_id or f"query-{uuid.uuid4().hex}"
        started = perf_counter()
        lexical_started = perf_counter()
        lexical = self._evidence_store.lexical_segment_candidates(
            question=request.question,
            exhibit_ids=request.exhibit_ids,
            source_ids=request.source_ids,
            source_levels=request.source_levels,
            limit=max(self._lexical_limit, request.limit),
        )
        lexical_ms = _duration_ms(lexical_started)
        trace: dict[str, object] = {
            "mode": "evidence_hybrid",
            "query_id": query_id,
            "lexical_candidates": [asdict(candidate) for candidate in lexical],
            "dense_candidates": [],
            "fused_candidates": [],
            "fallback_reason": "",
            "collection": str(getattr(self._index, "collection_name", "")),
            "dense_score_threshold": self._dense_score_threshold,
        }

        dense: tuple[DenseEvidenceHit, ...] = ()
        dense_started = perf_counter()
        try:
            vector = self._embedder.embed(request.question)
            dense = self._index.search(
                vector=vector,
                exhibit_ids=request.exhibit_ids,
                source_ids=request.source_ids,
                source_levels=request.source_levels,
                limit=max(self._dense_limit, request.limit),
            )
            trace["dense_ms"] = _duration_ms(dense_started)
        except Exception as exc:
            trace["fallback_reason"] = f"dense_error:{type(exc).__name__}"
            trace["dense_ms"] = _duration_ms(dense_started)

        raw_dense_count = len(dense)
        dense_records = self._evidence_store.segment_index_records(
            [hit.segment_id for hit in dense]
        )
        valid_dense_records = {
            str(record["segment_id"]): record
            for record in dense_records
        }
        dense = tuple(
            hit
            for hit in dense
            if hit.segment_id in valid_dense_records
            and (
                not request.source_levels
                or str(valid_dense_records[hit.segment_id].get("source_level", ""))
                in request.source_levels
            )
            and (
                not request.source_ids
                or str(valid_dense_records[hit.segment_id].get("source_id", ""))
                in request.source_ids
            )
            and (
                not request.exhibit_ids
                or set(
                    valid_dense_records[hit.segment_id].get("exhibit_ids", ())
                ).intersection(request.exhibit_ids)
            )
        )
        dense = tuple(
            hit for hit in dense if hit.score >= self._dense_score_threshold
        )
        if raw_dense_count and not dense and not trace["fallback_reason"]:
            trace["fallback_reason"] = "dense_candidates_filtered"
        trace["dense_candidates"] = [
            {
                "segment_id": hit.segment_id,
                "score": hit.score,
                "rank": rank,
            }
            for rank, hit in enumerate(dense, start=1)
        ]

        fused = _rrf(
            lexical,
            tuple(
                RankedSourceSegment(
                    segment_id=hit.segment_id,
                    score=hit.score,
                    source_id=str(hit.payload.get("source_id", "")),
                )
                for hit in dense
            ),
            k=self._rrf_k,
        )
        selected = list(fused[: request.limit])
        conflict_groups = self._evidence_store.conflict_groups(
            request.exhibit_ids
        )
        selected_ids = {candidate.segment_id for candidate in selected}
        missing_conflict_ids = tuple(
            dict.fromkeys(
                evidence_id
                for group in conflict_groups
                if selected_ids.intersection(group)
                for evidence_id in group
                if evidence_id not in selected_ids
            )
        )
        if missing_conflict_ids:
            conflict_records = self._evidence_store.segment_index_records(
                missing_conflict_ids
            )
            for record in conflict_records:
                source_level = str(record.get("source_level", ""))
                source_id = str(record.get("source_id", ""))
                record_exhibit_ids = tuple(record.get("exhibit_ids", ()))
                if request.source_levels and source_level not in request.source_levels:
                    continue
                if request.source_ids and source_id not in request.source_ids:
                    continue
                if request.exhibit_ids and not set(record_exhibit_ids).intersection(
                    request.exhibit_ids
                ):
                    continue
                segment_id = str(record["segment_id"])
                selected.append(
                    RankedSourceSegment(
                        segment_id=segment_id,
                        score=0.0,
                        source_id=source_id,
                    )
                )
                selected_ids.add(segment_id)
        selected_tuple = tuple(selected)
        trace["fused_candidates"] = [asdict(candidate) for candidate in fused]
        trace["selected_segment_ids"] = [
            candidate.segment_id for candidate in selected_tuple
        ]
        trace["lexical_ms"] = lexical_ms
        trace["total_ms"] = _duration_ms(started)

        records = self._evidence_store.segment_index_records(
            [candidate.segment_id for candidate in selected_tuple]
        )
        record_by_id = {str(record["segment_id"]): record for record in records}
        items = tuple(
            EvidenceItem(
                id=candidate.segment_id,
                kind="segment",
                text=str(record_by_id[candidate.segment_id]["text"]),
                source_id=str(record_by_id[candidate.segment_id]["source_id"]),
                segment_id=candidate.segment_id,
                source_title=str(record_by_id[candidate.segment_id]["source_title"]),
                locator=str(record_by_id[candidate.segment_id]["locator"]),
                score=candidate.score,
                rank=rank,
                source_level=str(record_by_id[candidate.segment_id]["source_level"]),
                content_version=int(record_by_id[candidate.segment_id]["content_version"]),
                exhibit_ids=tuple(record_by_id[candidate.segment_id]["exhibit_ids"]),
            )
            for rank, candidate in enumerate(selected_tuple, start=1)
            if candidate.segment_id in record_by_id
        )
        item_ids = {item.id for item in items}
        visible_conflict_groups: list[tuple[str, ...]] = []
        for group in conflict_groups:
            visible_group = tuple(
                evidence_id for evidence_id in group if evidence_id in item_ids
            )
            if visible_group:
                visible_conflict_groups.append(visible_group)
        claims = _published_claims(
            self._store,
            request.exhibit_ids,
            request.fact_types,
        )
        return EvidencePack(
            query_id=query_id,
            exhibit_ids=request.exhibit_ids,
            items=items,
            claims=claims,
            index_version=str(getattr(self._index, "collection_name", "")),
            retrieval_trace=trace,
            conflict_groups=tuple(visible_conflict_groups),
        )


def _published_claims(
    store: MuseumStore,
    exhibit_ids: tuple[str, ...],
    fact_types: tuple[str, ...],
) -> tuple[EvidenceClaim, ...]:
    claims: list[EvidenceClaim] = []
    snapshots = []
    fact_ids: list[str] = []
    for exhibit_id in exhibit_ids:
        snapshot = store.published_evidence(exhibit_id)
        if snapshot is None:
            continue
        snapshots.append((exhibit_id, snapshot))
        fact_ids.extend(fact.id for fact in snapshot.facts)
    support_map = EvidenceStore(store).claim_support_segment_ids(fact_ids)
    for exhibit_id, snapshot in snapshots:
        for fact in snapshot.facts:
            if fact_types and fact.fact_type not in fact_types:
                continue
            claims.append(
                EvidenceClaim(
                    id=fact.id,
                    exhibit_id=exhibit_id,
                    fact_type=fact.fact_type,
                    statement=fact.statement,
                    source_ids=fact.source_ids,
                    supporting_evidence_ids=support_map.get(fact.id, ()),
                )
            )
    return tuple(claims)


def _rrf(
    lexical: tuple[RankedSourceSegment, ...],
    dense: tuple[RankedSourceSegment, ...],
    *,
    k: int,
) -> tuple[RankedSourceSegment, ...]:
    scores: dict[str, float] = {}
    source_ids: dict[str, str] = {}
    for rank, candidate in enumerate(lexical, start=1):
        scores[candidate.segment_id] = scores.get(candidate.segment_id, 0.0) + 1 / (
            k + rank
        )
        source_ids.setdefault(candidate.segment_id, candidate.source_id)
    for rank, candidate in enumerate(dense, start=1):
        scores[candidate.segment_id] = scores.get(candidate.segment_id, 0.0) + 1 / (
            k + rank
        )
        source_ids.setdefault(candidate.segment_id, candidate.source_id)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        RankedSourceSegment(
            segment_id=segment_id,
            score=score,
            source_id=source_ids.get(segment_id, ""),
        )
        for segment_id, score in ordered
    )


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
