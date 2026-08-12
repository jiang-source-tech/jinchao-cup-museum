from __future__ import annotations

from dataclasses import asdict, dataclass, field
from threading import Lock
from time import monotonic, perf_counter
from typing import Protocol

from core.museum.contracts import EvidenceSnapshot
from core.museum.embedding import TextEmbedder
from core.museum.qdrant_index import QdrantFactIndex
from core.museum.store import MuseumStore


VALID_RETRIEVAL_MODES = {"rules", "shadow", "hybrid"}
_DENSE_FACT_TYPES_BY_INTENT = {
    "material": ("material",),
    "dimensions": ("dimensions",),
    "excavation": ("excavation",),
    "era": ("era",),
    "craft": ("craft", "research_limit"),
    "appearance": ("appearance",),
    "overview": ("era", "material", "appearance", "excavation"),
}


@dataclass(frozen=True)
class RetrievalRequest:
    exhibit_id: str
    question: str
    limit: int = 3
    fact_types: tuple[str, ...] = ()
    query_terms: tuple[str, ...] = ()
    overview: bool = False
    allow_dense_only: bool = False
    dense_fact_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankedFactCandidate:
    fact_id: str
    rank: int
    score: float


@dataclass
class RetrievalDiagnostics:
    mode: str
    lexical_candidates: list[RankedFactCandidate] = field(default_factory=list)
    dense_candidates: list[RankedFactCandidate] = field(default_factory=list)
    fused_candidates: list[RankedFactCandidate] = field(default_factory=list)
    selected_fact_ids: list[str] = field(default_factory=list)
    rejected_fact_ids: list[str] = field(default_factory=list)
    fallback_reason: str = ""
    embedding_model: str = ""
    embedding_dimension: int = 0
    collection: str = ""
    index_version: str = "facts-v1"
    stage_latency_ms: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalResult:
    evidence: EvidenceSnapshot | None
    diagnostics: RetrievalDiagnostics


class EvidenceRetriever(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


class SqliteEvidenceRetriever:
    def __init__(self, store: MuseumStore, *, candidate_limit: int = 12):
        self._store = store
        self._candidate_limit = candidate_limit

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        started = perf_counter()
        candidates = self._store.lexical_fact_candidates(
            exhibit_id=request.exhibit_id,
            question=request.question,
            limit=max(self._candidate_limit, request.limit),
            fact_types=request.fact_types,
            query_terms=request.query_terms,
            overview=request.overview,
        )
        diagnostics = RetrievalDiagnostics(
            mode="rules",
            lexical_candidates=[
                RankedFactCandidate(fact_id=fact_id, rank=index, score=score)
                for index, (fact_id, score) in enumerate(candidates, start=1)
            ],
        )
        evidence = self._store.hydrate_published_facts(
            exhibit_id=request.exhibit_id,
            fact_ids=tuple(fact_id for fact_id, _ in candidates),
            limit=request.limit,
        )
        diagnostics.selected_fact_ids = list(evidence.fact_ids) if evidence else []
        diagnostics.stage_latency_ms["lexical"] = _duration_ms(started)
        return RetrievalResult(evidence=evidence, diagnostics=diagnostics)


class HybridEvidenceRetriever:
    def __init__(
        self,
        *,
        store: MuseumStore,
        embedder: TextEmbedder,
        index: QdrantFactIndex,
        mode: str,
        lexical_limit: int = 12,
        dense_limit: int = 12,
        dense_score_threshold: float = 0.5,
        rrf_k: int = 60,
        circuit_failure_threshold: int = 3,
        circuit_cooldown_seconds: float = 30.0,
    ):
        if mode not in VALID_RETRIEVAL_MODES - {"rules"}:
            raise ValueError("hybrid retriever mode must be shadow or hybrid")
        self._store = store
        self._embedder = embedder
        self._index = index
        self._mode = mode
        self._lexical_limit = lexical_limit
        self._dense_limit = dense_limit
        self._dense_score_threshold = dense_score_threshold
        self._rrf_k = rrf_k
        self._failure_threshold = max(1, circuit_failure_threshold)
        self._cooldown_seconds = max(0.0, circuit_cooldown_seconds)
        self._failure_count = 0
        self._open_until = 0.0
        self._circuit_lock = Lock()

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        total_started = perf_counter()
        lexical_started = perf_counter()
        lexical = self._store.lexical_fact_candidates(
            exhibit_id=request.exhibit_id,
            question=request.question,
            limit=max(self._lexical_limit, request.limit),
            fact_types=request.fact_types,
            query_terms=request.query_terms,
            overview=request.overview,
        )
        diagnostics = RetrievalDiagnostics(
            mode=self._mode,
            lexical_candidates=[
                RankedFactCandidate(fact_id=fact_id, rank=index, score=score)
                for index, (fact_id, score) in enumerate(lexical, start=1)
            ],
            embedding_model=self._embedder.model,
            embedding_dimension=self._embedder.dimension,
            collection=self._index.collection_name,
        )
        diagnostics.stage_latency_ms["lexical"] = _duration_ms(lexical_started)

        dense = ()
        if self._circuit_is_open():
            diagnostics.fallback_reason = "dense_circuit_open"
        else:
            try:
                embedding_started = perf_counter()
                vector = self._embedder.embed(
                    " ".join(
                        part
                        for part in (
                            request.question,
                            " ".join(request.query_terms),
                        )
                        if part
                    )
                )
                diagnostics.stage_latency_ms["embedding"] = _duration_ms(
                    embedding_started
                )
                dense_started = perf_counter()
                dense = self._index.search(
                    vector=vector,
                    exhibit_id=request.exhibit_id,
                    fact_types=(),
                    limit=max(self._dense_limit, request.limit),
                )
                diagnostics.stage_latency_ms["dense"] = _duration_ms(dense_started)
                self._record_dense_success()
            except Exception as exc:
                diagnostics.fallback_reason = (
                    f"dense_error:{type(exc).__name__}"
                )
                self._record_dense_failure()

        raw_dense_ids = tuple(hit.fact_id for hit in dense)
        valid_dense_ids = set(
            self._store.valid_published_fact_ids(
                exhibit_id=request.exhibit_id,
                fact_ids=raw_dense_ids,
            )
        )
        valid_dense = tuple(
            hit
            for hit in dense
            if hit.fact_id in valid_dense_ids
            and hit.score >= self._dense_score_threshold
        )
        if request.dense_fact_types:
            allowed_dense_ids = set(
                self._store.valid_published_fact_ids_by_type(
                    exhibit_id=request.exhibit_id,
                    fact_ids=tuple(hit.fact_id for hit in valid_dense),
                    fact_types=request.dense_fact_types,
                )
            )
            valid_dense = tuple(
                hit for hit in valid_dense if hit.fact_id in allowed_dense_ids
            )
        diagnostics.dense_candidates = [
            RankedFactCandidate(
                fact_id=hit.fact_id,
                rank=index,
                score=hit.score,
            )
            for index, hit in enumerate(valid_dense, start=1)
        ]
        fused = _rrf(
            diagnostics.lexical_candidates,
            diagnostics.dense_candidates,
            k=self._rrf_k,
        )
        fused_ids = tuple(candidate.fact_id for candidate in fused)
        valid_fused_ids = set(
            self._store.valid_published_fact_ids(
                exhibit_id=request.exhibit_id,
                fact_ids=fused_ids,
            )
        )
        diagnostics.fused_candidates = [
            candidate for candidate in fused if candidate.fact_id in valid_fused_ids
        ]

        chosen = (
            [candidate.fact_id for candidate in diagnostics.fused_candidates]
            if (
                self._mode == "hybrid"
                and not diagnostics.fallback_reason
                and (
                    bool(diagnostics.lexical_candidates)
                    or request.allow_dense_only
                )
            )
            else [candidate.fact_id for candidate in diagnostics.lexical_candidates]
        )
        evidence = self._store.hydrate_published_facts(
            exhibit_id=request.exhibit_id,
            fact_ids=tuple(chosen),
            limit=request.limit,
        )
        selected = list(evidence.fact_ids) if evidence else []
        all_candidate_ids = tuple(dict.fromkeys(
            [candidate.fact_id for candidate in diagnostics.lexical_candidates]
            + list(raw_dense_ids)
        ))
        valid_ids = set(self._store.valid_published_fact_ids(
            exhibit_id=request.exhibit_id,
            fact_ids=all_candidate_ids,
        ))
        diagnostics.selected_fact_ids = selected
        diagnostics.rejected_fact_ids = [
            fact_id for fact_id in all_candidate_ids if fact_id not in valid_ids
        ]
        diagnostics.stage_latency_ms["total"] = _duration_ms(total_started)
        return RetrievalResult(evidence=evidence, diagnostics=diagnostics)

    def _circuit_is_open(self) -> bool:
        with self._circuit_lock:
            if self._open_until <= monotonic():
                self._open_until = 0.0
                return False
            return True

    def _record_dense_success(self) -> None:
        with self._circuit_lock:
            self._failure_count = 0
            self._open_until = 0.0

    def _record_dense_failure(self) -> None:
        with self._circuit_lock:
            self._failure_count += 1
            if self._failure_count >= self._failure_threshold:
                self._open_until = monotonic() + self._cooldown_seconds


def _rrf(
    lexical: list[RankedFactCandidate],
    dense: list[RankedFactCandidate],
    *,
    k: int,
) -> list[RankedFactCandidate]:
    scores: dict[str, float] = {}
    for branch in (lexical, dense):
        for candidate in branch:
            scores[candidate.fact_id] = scores.get(candidate.fact_id, 0.0) + (
                1.0 / (k + candidate.rank)
            )
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [
        RankedFactCandidate(fact_id=fact_id, rank=rank, score=score)
        for rank, (fact_id, score) in enumerate(ordered, start=1)
    ]


def dense_fact_types_for_intent(
    fine_intent: str,
    fact_types: tuple[str, ...],
    query_terms: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if fine_intent == "appearance" and any(
        term in {"透明", "透亮", "通透"} for term in query_terms
    ):
        return ("appearance", "material")
    return _DENSE_FACT_TYPES_BY_INTENT.get(fine_intent, fact_types)


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
