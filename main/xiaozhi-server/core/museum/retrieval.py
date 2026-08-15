from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
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
    "craft": ("craft",),
    "research_limit": ("research_limit",),
    "appearance": ("appearance",),
    "usage": ("usage",),
    "overview": ("history", "era", "material", "appearance", "excavation"),
    "history": ("history",),
}

_SEMANTIC_INTENT_PROTOTYPES = {
    "material": "展品由什么材料、原料或物质制成",
    "dimensions": "展品的尺寸、高度、大小、口径或长宽",
    "excavation": "展品在哪里出土、发现地点或考古地点",
    "era": "展品属于哪个年代、距今多久或哪个历史时期",
    "craft": "展品如何制作、加工工艺、制作方法或工艺过程",
    "research_limit": "展品有哪些研究争议、未解问题、不同观点或尚无定论之处",
    "appearance": "展品的外形、样子、颜色和看起来的特征",
    "usage": "展品过去的用途、作用、用来做什么或怎么使用",
    "price": "展品的价格、售价、市场价值或卖了多少钱",
    "history": "展品在馆方登记的名称、公开名称或历史记录",
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
    semantic_fallback: bool = False
    rule_intent: str = ""
    semantic_validation: bool = False


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
    semantic_fallback: bool = False
    semantic_intent: str = ""
    semantic_candidate_intent: str = ""
    semantic_candidate_score: float = 0.0
    semantic_confidence: float = 0.0
    semantic_margin: float = 0.0
    semantic_override: bool = False
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
    supports_semantic_fallback = True

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
        self._semantic_prototype_lock = Lock()
        self._semantic_prototype_vectors = None

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
                semantic_intent = ""
                if request.semantic_fallback or request.semantic_validation:
                    (
                        semantic_intent,
                        candidate_intent,
                        candidate_score,
                        semantic_margin,
                    ) = self._classify_semantic_intent(vector)
                    diagnostics.semantic_intent = semantic_intent
                    diagnostics.semantic_candidate_intent = candidate_intent
                    diagnostics.semantic_candidate_score = candidate_score
                    diagnostics.semantic_confidence = candidate_score
                    diagnostics.semantic_margin = semantic_margin
                search_fact_types = request.dense_fact_types
                semantic_disagrees = bool(
                    request.semantic_validation
                    and request.rule_intent
                    and request.rule_intent != "unknown"
                    and semantic_intent
                    and semantic_intent != request.rule_intent
                )
                if semantic_disagrees:
                    search_fact_types = _DENSE_FACT_TYPES_BY_INTENT.get(
                        semantic_intent,
                        search_fact_types,
                    )
                elif request.semantic_fallback and semantic_intent:
                    search_fact_types = _DENSE_FACT_TYPES_BY_INTENT.get(
                        semantic_intent,
                        (),
                    )
                dense_started = perf_counter()
                dense = self._index.search(
                    vector=vector,
                    exhibit_id=request.exhibit_id,
                    fact_types=search_fact_types,
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
        if request.semantic_fallback:
            diagnostics.semantic_fallback = True
            if (
                diagnostics.semantic_candidate_intent
                and not diagnostics.semantic_intent
            ):
                valid_dense = ()
            else:
                valid_dense = _select_semantic_fallback_hits(
                    valid_dense,
                    relaxed=bool(diagnostics.semantic_intent),
                )
            if valid_dense and not diagnostics.semantic_intent:
                diagnostics.semantic_confidence = valid_dense[0].score
                diagnostics.semantic_margin = (
                    valid_dense[0].score - valid_dense[1].score
                    if len(valid_dense) > 1
                    else valid_dense[0].score
                )
        semantic_disagrees = bool(
            request.semantic_validation
            and request.rule_intent
            and request.rule_intent != "unknown"
            and diagnostics.semantic_intent
            and diagnostics.semantic_intent != request.rule_intent
        )
        if request.dense_fact_types and not semantic_disagrees:
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
        if semantic_disagrees and _is_high_confidence_dense_match(valid_dense):
            diagnostics.semantic_override = True
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
        if request.semantic_fallback:
            chosen = [
                candidate.fact_id
                for candidate in diagnostics.dense_candidates[:1]
            ]
        elif diagnostics.semantic_override:
            chosen = [
                candidate.fact_id
                for candidate in diagnostics.dense_candidates[:1]
            ]
        evidence = self._store.hydrate_published_facts(
            exhibit_id=request.exhibit_id,
            fact_ids=tuple(chosen),
            limit=request.limit,
        )
        selected = list(evidence.fact_ids) if evidence else []
        if (
            request.semantic_fallback
            and not diagnostics.semantic_intent
            and evidence
            and evidence.facts
        ):
            diagnostics.semantic_intent = _intent_for_fact_type(
                evidence.facts[0].fact_type
            )
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

    def _classify_semantic_intent(self, vector):
        embed_many = getattr(self._embedder, "embed_many", None)
        if not callable(embed_many):
            return "", "", 0.0, 0.0
        try:
            with self._semantic_prototype_lock:
                if self._semantic_prototype_vectors is None:
                    prototype_vectors = embed_many(
                        list(_SEMANTIC_INTENT_PROTOTYPES.values())
                    )
                    if len(prototype_vectors) != len(
                        _SEMANTIC_INTENT_PROTOTYPES
                    ):
                        return "", "", 0.0, 0.0
                    self._semantic_prototype_vectors = dict(
                        zip(
                            _SEMANTIC_INTENT_PROTOTYPES,
                            prototype_vectors,
                        )
                    )
            ranked = sorted(
                (
                    (
                        intent,
                        _cosine_similarity(
                            vector,
                            prototype_vector,
                        ),
                    )
                    for intent, prototype_vector in self._semantic_prototype_vectors.items()
                ),
                key=lambda item: (-item[1], item[0]),
            )
        except Exception:
            return "", "", 0.0, 0.0
        if not ranked:
            return "", "", 0.0, 0.0
        top_intent, top_score = ranked[0]
        margin = top_score - ranked[1][1] if len(ranked) > 1 else top_score
        accepted = top_score >= 0.45 and margin >= 0.015
        return (
            top_intent if accepted else "",
            top_intent,
            top_score,
            margin,
        )

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


_SEMANTIC_FALLBACK_MIN_SCORE = 0.72
_SEMANTIC_FALLBACK_MIN_MARGIN = 0.08


def _select_semantic_fallback_hits(hits, *, relaxed: bool = False):
    if not hits or hits[0].score < _SEMANTIC_FALLBACK_MIN_SCORE:
        return ()
    if relaxed:
        return hits
    if len(hits) > 1 and (
        hits[0].score - hits[1].score < _SEMANTIC_FALLBACK_MIN_MARGIN
    ):
        return ()
    return hits


def _is_high_confidence_dense_match(hits) -> bool:
    if not hits or hits[0].score < _SEMANTIC_FALLBACK_MIN_SCORE:
        return False
    return len(hits) == 1 or (
        hits[0].score - hits[1].score >= _SEMANTIC_FALLBACK_MIN_MARGIN
    )


def _cosine_similarity(left, right) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _intent_for_fact_type(fact_type: str) -> str:
    if fact_type in {
        "material",
        "dimensions",
        "excavation",
        "era",
        "craft",
        "research_limit",
        "appearance",
        "usage",
        "price",
        "history",
    }:
        return fact_type
    return ""


def dense_fact_types_for_intent(
    fine_intent: str,
    fact_types: tuple[str, ...],
    query_terms: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if fine_intent == "appearance" and any(
        term in {"透明", "透亮", "通透"} for term in query_terms
    ):
        return ("appearance", "material")
    default_types = _DENSE_FACT_TYPES_BY_INTENT.get(fine_intent, fact_types)
    if any(fact_type not in default_types for fact_type in fact_types):
        return fact_types
    return default_types


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
