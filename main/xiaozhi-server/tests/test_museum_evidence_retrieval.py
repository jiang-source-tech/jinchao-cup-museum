from __future__ import annotations

import json
from pathlib import Path

from core.museum.answering import GroundedAnswerService
from core.museum.evidence_index import DenseEvidenceHit
from core.museum.evidence_retrieval import (
    EvidenceSearchRequest,
    EvidenceSearchService,
)
from core.museum.source_ingestion import ingest_source_manifest
from core.museum.llm_contract import build_museum_llm_prompts
from core.museum.query_understanding import understand_question
from core.museum.store import DEMO_EXHIBIT_ID, DEMO_MUSEUM_ID, MuseumStore


class FakeEmbedder:
    def embed(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class FakeIndex:
    collection_name = "evidence-retrieval-test"

    def __init__(self, hits):
        self.hits = tuple(hits)

    def search(self, **_kwargs):
        return self.hits


def _setup(tmp_path: Path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "source.md").write_text(
        (
            "# 材质\n\n这件展品由天然水晶制成。\n\n"
            "# 冲突夹具\n\n另一份演示记录声称这件展品由玉石制成。\n"
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "dataset_id": "evidence-retrieval-test",
        "museum": {"id": DEMO_MUSEUM_ID, "name": "杭州博物馆（演示数据）"},
        "sources": [
            {
                "id": "retrieval-source",
                "title": "检索测试资料",
                "source_type": "markdown",
                "path": "source.md",
                "rights_note": "自动化测试夹具。",
                "exhibit_ids": [DEMO_EXHIBIT_ID],
            }
        ],
    }
    manifest_path = dataset / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    report = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="retrieval-ingest",
    )
    assert report.ok
    return store, report


def test_evidence_search_returns_traceable_segments_and_claims(tmp_path: Path):
    store, report = _setup(tmp_path)
    index = FakeIndex(
        (
            DenseEvidenceHit(
                segment_id=report.segment_ids[0],
                score=0.98,
                payload={
                    "segment_id": report.segment_ids[0],
                    "source_id": "retrieval-source",
                },
            ),
            DenseEvidenceHit(
                segment_id="unknown-segment",
                score=0.97,
                payload={"segment_id": "unknown-segment"},
            ),
        )
    )
    service = EvidenceSearchService(
        store=store,
        embedder=FakeEmbedder(),
        index=index,
    )
    pack = service.search(
        EvidenceSearchRequest(
            question="这件展品是什么材质？",
            exhibit_ids=(DEMO_EXHIBIT_ID,),
            limit=3,
            query_id="query-001",
        )
    )
    assert pack.query_id == "query-001"
    assert pack.items
    assert pack.items[0].segment_id == report.segment_ids[0]
    assert pack.items[0].source_title == "检索测试资料"
    assert pack.items[0].locator
    assert pack.source_ids == ("retrieval-source",)
    assert any(claim.fact_type == "material" for claim in pack.claims)
    assert "unknown-segment" not in pack.evidence_ids
    assert pack.retrieval_trace["collection"] == "evidence-retrieval-test"

    wrong_exhibit_pack = service.search(
        EvidenceSearchRequest(
            question="这件展品是什么材质？",
            exhibit_ids=("missing-exhibit",),
            limit=3,
        )
    )
    assert wrong_exhibit_pack.items == ()

    strict_service = EvidenceSearchService(
        store=store,
        embedder=FakeEmbedder(),
        index=index,
        dense_score_threshold=0.99,
    )
    strict_pack = strict_service.search(
        EvidenceSearchRequest(
            question="量子纠缠",
            exhibit_ids=(DEMO_EXHIBIT_ID,),
            limit=3,
        )
    )
    assert strict_pack.items == ()
    assert strict_pack.retrieval_trace["fallback_reason"] == (
        "dense_candidates_filtered"
    )


def test_evidence_backed_overview_uses_guided_claim_fallback(tmp_path: Path):
    store, report = _setup(tmp_path)
    supporting_segment = report.segment_ids[0]
    with store.connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO knowledge_claim_support(fact_id, segment_id) "
            "VALUES (?, ?)",
            ("fact-crystal-cup-material", supporting_segment),
        )
    service = EvidenceSearchService(
        store=store,
        embedder=FakeEmbedder(),
        index=FakeIndex(
            (
                DenseEvidenceHit(
                    segment_id=supporting_segment,
                    score=0.98,
                    payload={
                        "segment_id": supporting_segment,
                        "source_id": "retrieval-source",
                    },
                ),
            )
        ),
    )

    answer = GroundedAnswerService(
        store,
        evidence_search=service,
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="介绍一下战国水晶杯",
    )

    assert answer.knowledge_status == "grounded"
    assert answer.spoken_text.startswith("现在看到的是战国水晶杯")
    assert "一整块天然水晶" in answer.spoken_text
    assert answer.cited_evidence_ids == (supporting_segment,)
    assert answer.retrieval_trace["answer_depth"] == "guided"
    assert answer.retrieval_trace["retrieval_limit"] == 10


def test_evidence_search_falls_back_to_lexical_when_dense_fails(tmp_path: Path):
    store, report = _setup(tmp_path)

    class FailingIndex:
        collection_name = "evidence-retrieval-failing"

        def search(self, **_kwargs):
            raise TimeoutError("qdrant timeout")

    service = EvidenceSearchService(
        store=store,
        embedder=FakeEmbedder(),
        index=FailingIndex(),
    )
    pack = service.search(
        EvidenceSearchRequest(
            question="天然水晶",
            exhibit_ids=(DEMO_EXHIBIT_ID,),
            limit=3,
        )
    )
    assert pack.items
    assert report.segment_ids[0] in pack.evidence_ids
    assert pack.retrieval_trace["fallback_reason"] == "dense_error:TimeoutError"


def test_default_visitor_search_excludes_synthetic_sources(tmp_path: Path):
    store, report = _setup(tmp_path)
    with store.connection() as connection:
        connection.execute(
            "UPDATE source_document SET source_level = 'synthetic_demo' "
            "WHERE id = 'retrieval-source'"
        )
    service = EvidenceSearchService(
        store=store,
        embedder=FakeEmbedder(),
        index=FakeIndex(
            (
                DenseEvidenceHit(
                    segment_id=report.segment_ids[0],
                    score=0.99,
                    payload={"segment_id": report.segment_ids[0]},
                ),
            )
        ),
    )

    visitor_pack = service.search(
        EvidenceSearchRequest(
            question="天然水晶",
            exhibit_ids=(DEMO_EXHIBIT_ID,),
        )
    )
    admin_pack = service.search(
        EvidenceSearchRequest(
            question="天然水晶",
            exhibit_ids=(DEMO_EXHIBIT_ID,),
            source_levels=("synthetic_demo",),
        )
    )

    assert visitor_pack.items == ()
    assert report.segment_ids[0] in admin_pack.evidence_ids


def test_conflict_retrieval_keeps_both_sides_and_blocks_silent_fallback(
    tmp_path: Path,
):
    store, report = _setup(tmp_path)
    supporting_segment, conflicting_segment = report.segment_ids[:2]
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO knowledge_claim_support(fact_id, segment_id) VALUES (?, ?)",
            ("fact-crystal-cup-material", supporting_segment),
        )
        connection.execute(
            """
            INSERT INTO knowledge_claim_conflict(fact_id, segment_id, reason)
            VALUES (?, ?, ?)
            """,
            (
                "fact-crystal-cup-material",
                conflicting_segment,
                "test fixture disagreement",
            ),
        )
    service = EvidenceSearchService(
        store=store,
        embedder=FakeEmbedder(),
        index=FakeIndex(
            (
                DenseEvidenceHit(
                    segment_id=supporting_segment,
                    score=0.99,
                    payload={"segment_id": supporting_segment},
                ),
            )
        ),
    )
    pack = service.search(
        EvidenceSearchRequest(
            question="天然水晶材质",
            exhibit_ids=(DEMO_EXHIBIT_ID,),
        )
    )

    assert set(pack.evidence_ids) == {supporting_segment, conflicting_segment}
    assert any(
        set(group) == {supporting_segment, conflicting_segment}
        for group in pack.conflict_groups
    )
    _system_prompt, user_prompt = build_museum_llm_prompts(
        exhibit_name="战国水晶杯",
        question="它是什么材质？",
        candidates=pack,
        history=(),
        understanding=understand_question("它是什么材质？"),
    )
    assert "冲突证据组" in user_prompt
    assert supporting_segment in user_prompt
    assert conflicting_segment in user_prompt

    answer = GroundedAnswerService(
        store,
        evidence_search=service,
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它是什么材质？",
        llm=None,
    )
    assert answer.knowledge_status == "unsupported"
    assert answer.cited_evidence_ids == ()

    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO source_document(
                id, museum_id, title, source_type, locator, rights_note,
                publisher, published_date, accessed_at, language, checksum,
                source_level, rights_status, original_path, parser_version,
                metadata_json
            )
            SELECT ?, museum_id, title, source_type, locator, rights_note,
                   publisher, published_date, accessed_at, language, checksum,
                   'synthetic_demo', rights_status, original_path,
                   parser_version, metadata_json
            FROM source_document
            WHERE id = 'retrieval-source'
            """,
            ("hidden-conflict-source",),
        )
        connection.execute(
            "UPDATE source_segment SET source_id = ? WHERE id = ?",
            ("hidden-conflict-source", conflicting_segment),
        )
    hidden_conflict_pack = service.search(
        EvidenceSearchRequest(
            question="天然水晶材质",
            exhibit_ids=(DEMO_EXHIBIT_ID,),
        )
    )
    assert hidden_conflict_pack.conflict_groups == ((supporting_segment,),)
    hidden_conflict_answer = GroundedAnswerService(
        store,
        evidence_search=service,
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它是什么材质？",
        llm=None,
    )
    assert hidden_conflict_answer.knowledge_status == "unsupported"
    assert hidden_conflict_answer.cited_evidence_ids == ()
