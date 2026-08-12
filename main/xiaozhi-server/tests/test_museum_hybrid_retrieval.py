from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from core.conversation_runtime import TurnRequest
from core.museum.answering import GroundedAnswerService
from core.museum.embedding import DashScopeTextEmbedder
from core.museum.qdrant_index import DenseFactHit
from core.museum.retrieval import HybridEvidenceRetriever, RetrievalRequest
from core.museum.runtime import MuseumRuntime
from core.museum.store import DEMO_EXHIBIT_ID, MuseumStore


class FakeEmbedder:
    model = "fake-embedding"
    dimension = 3

    def embed(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class FailingEmbedder(FakeEmbedder):
    def embed(self, _text: str) -> list[float]:
        raise TimeoutError("embedding timeout")


class FakeIndex:
    collection_name = "test-museum-facts"

    def __init__(self, fact_ids: tuple[str, ...] | tuple[tuple[str, float], ...]):
        self._hits = tuple(
            item if isinstance(item, tuple) else (item, 1.0 - index * 0.01)
            for index, item in enumerate(fact_ids)
        )

    def search(self, **_kwargs) -> tuple[DenseFactHit, ...]:
        return tuple(
            DenseFactHit(
                fact_id=fact_id,
                score=score,
                payload={"fact_id": fact_id},
            )
            for fact_id, score in self._hits
        )


class InspectingIndex(FakeIndex):
    def __init__(self, fact_ids: tuple[str, ...]):
        super().__init__(fact_ids)
        self.last_fact_types = None

    def search(self, **kwargs) -> tuple[DenseFactHit, ...]:
        self.last_fact_types = kwargs["fact_types"]
        return super().search(**kwargs)


def _store(tmp_path) -> MuseumStore:
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    return store


def _retriever(store, *, embedder, fact_ids, mode="hybrid"):
    return HybridEvidenceRetriever(
        store=store,
        embedder=embedder,
        index=FakeIndex(fact_ids),
        mode=mode,
        circuit_failure_threshold=1,
        circuit_cooldown_seconds=60,
    )


def test_dense_recall_fills_a_real_understanding_gap(tmp_path, monkeypatch):
    store = _store(tmp_path)
    index = InspectingIndex(
        (
            ("fact-crystal-cup-era", 0.99),
            ("fact-crystal-cup-material", 0.85),
        )
    )
    retriever = HybridEvidenceRetriever(
        store=store,
        embedder=FakeEmbedder(),
        index=index,
        mode="hybrid",
    )

    answer = GroundedAnswerService(store, retriever).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="古人挑了哪一种透明矿物来琢它？",
    )

    assert answer.fine_intent == "material"
    assert answer.evidence is not None
    assert answer.evidence.fact_ids == ("fact-crystal-cup-material",)
    assert [
        candidate["fact_id"]
        for candidate in answer.retrieval_trace["lexical_candidates"]
    ] == ["fact-crystal-cup-material"]
    assert [
        candidate["fact_id"]
        for candidate in answer.retrieval_trace["dense_candidates"]
    ] == ["fact-crystal-cup-material"]
    assert index.last_fact_types == ("material",)

    appearance_index = InspectingIndex(
        (
            ("fact-crystal-cup-material", 0.99),
            ("fact-crystal-cup-appearance", 0.85),
            ("fact-crystal-cup-era", 0.84),
        )
    )
    appearance_answer = GroundedAnswerService(
        store,
        HybridEvidenceRetriever(
            store=store,
            embedder=FakeEmbedder(),
            index=appearance_index,
            mode="hybrid",
        ),
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它长什么样？",
    )
    assert appearance_answer.evidence is not None
    assert appearance_answer.evidence.fact_ids == (
        "fact-crystal-cup-appearance",
    )
    assert appearance_index.last_fact_types == ("appearance",)

    captured = {}

    def fake_embedding_call(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            output={
                "embeddings": [
                    {"text_index": 0, "embedding": [0.1, 0.2, 0.3]}
                ]
            },
        )

    monkeypatch.setattr("dashscope.TextEmbedding.call", fake_embedding_call)
    vector = DashScopeTextEmbedder(
        api_key="test-key",
        dimension=3,
        timeout_seconds=1.25,
    ).embed("测试")
    assert vector == [0.1, 0.2, 0.3]
    assert captured["request_timeout"] == 1.25
    assert "timeout" not in captured
    assert store.retrieve_evidence(
        exhibit_id=DEMO_EXHIBIT_ID,
        question="这东西原来戴在哪儿？",
        fact_types=(),
    ) is None


def test_sqlite_rejects_cross_exhibit_stale_and_unknown_dense_ids(tmp_path):
    store = _store(tmp_path)
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO exhibit(id, zone_id, name, aliases_json, status)
            VALUES ('other-exhibit', ?, '其他展品', '[]', 'active')
            """,
            ("hangzhou-history-demo-zone",),
        )
        connection.execute(
            """
            INSERT INTO content_revision(id, exhibit_id, revision_no, status)
            VALUES ('other-r1', 'other-exhibit', 1, 'published')
            """
        )
        connection.execute(
            """
            INSERT INTO exhibit_fact(
                id, revision_id, fact_type, statement, keywords_json, confidence
            ) VALUES ('fact-other', 'other-r1', 'material', '其他事实。', '[]', 'official')
            """
        )
        connection.execute(
            """
            INSERT INTO fact_source(fact_id, source_id)
            VALUES ('fact-other', 'source-hangzhou-portal-2020')
            """
        )
    retriever = _retriever(
        store,
        embedder=FakeEmbedder(),
        fact_ids=(
            "fact-other",
            "fact-withdrawn-or-old",
            "fact-crystal-cup-material",
        ),
    )

    result = retriever.retrieve(
        RetrievalRequest(
            exhibit_id=DEMO_EXHIBIT_ID,
            question="透明矿物是什么？",
            allow_dense_only=True,
        )
    )

    assert result.evidence is not None
    assert result.evidence.fact_ids == ("fact-crystal-cup-material",)
    assert set(result.diagnostics.rejected_fact_ids) == {
        "fact-other",
        "fact-withdrawn-or-old",
    }

    unknown = retriever.retrieve(
        RetrievalRequest(
            exhibit_id=DEMO_EXHIBIT_ID,
            question="馆长叫什么名字？",
            allow_dense_only=False,
        )
    )
    assert unknown.evidence is None


def test_dense_failure_falls_back_and_persists_retrieval_audit(tmp_path):
    store = _store(tmp_path)
    retriever = _retriever(
        store,
        embedder=FailingEmbedder(),
        fact_ids=(),
    )
    runtime = MuseumRuntime(store, retriever=retriever)
    outcome = runtime.handle_turn(
        TurnRequest(
            request_id="hybrid-fallback-audit",
            transport_session_id="transport-hybrid",
            visitor_session_id=None,
            device_id="hybrid-test-device",
            user_text="战国水晶杯是什么材质做的？",
            history=(),
            occurred_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            llm=None,
            metadata={"selected_exhibit_id": DEMO_EXHIBIT_ID},
        )
    )

    assert outcome.knowledge_status == "grounded"
    assert outcome.fact_ids == ("fact-crystal-cup-material",)
    trace = store.get_interaction_trace_by_request_id("hybrid-fallback-audit")
    retrieval_trace = json.loads(trace["retrieval_trace_json"])
    assert retrieval_trace["fallback_reason"] == "dense_error:TimeoutError"
    assert retrieval_trace["selected_fact_ids"] == [
        "fact-crystal-cup-material"
    ]
