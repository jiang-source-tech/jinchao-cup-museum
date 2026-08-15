from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.business_runtime_factory import create_conversation_runtime
from core.conversation_runtime import TurnRequest
from core.museum.answering import GroundedAnswerService
from core.museum.contracts import EvidenceItem, EvidencePack
from core.museum.evidence_index import DenseEvidenceHit
from core.museum.evidence_retrieval import (
    EvidenceSearchService,
)
from core.museum.runtime import MuseumRuntime
from core.museum.query_understanding import QuestionUnderstanding
from core.museum.source_ingestion import ingest_source_manifest
from core.museum.store import DEMO_EXHIBIT_ID, MuseumStore


class _Embedder:
    model = "fake"
    dimension = 3

    def embed(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class _Index:
    collection_name = "evidence-answering-test"

    def __init__(self, segment_id: str):
        self.segment_id = segment_id

    def search(self, **_kwargs):
        return (
            DenseEvidenceHit(
                segment_id=self.segment_id,
                score=0.98,
                payload={"segment_id": self.segment_id, "source_id": "answer-source"},
            ),
        )


class _JsonLlm:
    model_name = "fake-evidence-model"

    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict[str, str]] = []

    def response_no_stream(self, system_prompt, user_prompt, **_kwargs):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        return self.response


class _StaticSearch:
    def __init__(self, pack: EvidencePack):
        self.pack = pack

    def search(self, _request):
        return self.pack


def _setup(tmp_path: Path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "source.md").write_text(
        "# 材质\n\n这件展品由天然水晶制成。\n",
        encoding="utf-8",
    )
    manifest_path = dataset / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "evidence-answering-test",
                "museum": {
                    "id": "hangzhou-museum-demo",
                    "name": "杭州博物馆（演示数据）",
                },
                "sources": [
                    {
                        "id": "answer-source",
                        "title": "演示资料",
                        "source_type": "markdown",
                        "path": "source.md",
                        "rights_note": "自动化测试夹具。",
                        "exhibit_ids": [DEMO_EXHIBIT_ID],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    report = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="answering-ingest",
    )
    assert report.ok
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO knowledge_claim_support(fact_id, segment_id) VALUES (?, ?)",
            ("fact-crystal-cup-material", report.segment_ids[0]),
        )
    search = EvidenceSearchService(
        store=store,
        embedder=_Embedder(),
        index=_Index(report.segment_ids[0]),
    )
    return store, search, report.segment_ids[0]


def test_evidence_answer_requires_valid_claim_citations(tmp_path: Path):
    store, search, segment_id = _setup(tmp_path)
    llm = _JsonLlm(
        json.dumps(
            {
                "status": "grounded",
                "fact_ids": [],
                "evidence_ids": [segment_id],
                "claims": [
                    {
                        "text": "这件展品由天然水晶制成。",
                        "evidence_ids": [segment_id],
                    }
                ],
                "social_intent": "",
                "answer": "这件展品由天然水晶制成。",
            },
            ensure_ascii=False,
        )
    )
    answer = GroundedAnswerService(
        store,
        evidence_search=search,
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="这件展品是什么材质？",
        llm=llm,
        query_id="answer-query-001",
    )

    assert answer.knowledge_status == "grounded"
    assert answer.evidence_pack is not None
    assert answer.evidence_pack.evidence_ids == (segment_id,)
    assert answer.cited_evidence_ids == (segment_id,)
    assert answer.guard_result == "model_answer_accepted"
    assert answer.llm_prompt_version == "museum-evidence-router-v1"
    assert "EVIDENCE" in llm.calls[0]["user"]

    invalid_llm = _JsonLlm(
        json.dumps(
            {
                "status": "grounded",
                "fact_ids": [],
                "evidence_ids": ["not-in-pack"],
                "claims": [
                    {
                        "text": "这件展品由天然水晶制成。",
                        "evidence_ids": ["not-in-pack"],
                    }
                ],
                "social_intent": "",
                "answer": "这件展品由天然水晶制成。",
            },
            ensure_ascii=False,
        )
    )
    guarded = GroundedAnswerService(
        store,
        evidence_search=search,
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="这件展品是什么材质？",
        llm=invalid_llm,
    )
    assert "天然水晶" in guarded.spoken_text
    assert guarded.guard_result == "model_evidence_ids_rejected"

    negated_llm = _JsonLlm(
        json.dumps(
            {
                "status": "grounded",
                "fact_ids": [],
                "evidence_ids": [segment_id],
                "claims": [
                    {
                        "text": "这件展品不是由天然水晶制成。",
                        "evidence_ids": [segment_id],
                    }
                ],
                "social_intent": "",
                "answer": "这件展品不是由天然水晶制成。",
            },
            ensure_ascii=False,
        )
    )
    negated = GroundedAnswerService(
        store,
        evidence_search=search,
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="这件展品是什么材质？",
        llm=negated_llm,
    )
    assert "由天然水晶制成" in negated.spoken_text
    assert "不是由天然水晶制成" not in negated.spoken_text
    assert negated.guard_result == "model_claim_negation_mismatch"


def test_evidence_answer_honors_unsupported_and_rejects_dense_only_fallback(
    tmp_path: Path,
):
    store, search, segment_id = _setup(tmp_path)
    unsupported_llm = _JsonLlm(
        json.dumps(
            {
                "status": "unsupported",
                "fact_ids": [],
                "evidence_ids": [],
                "claims": [],
                "social_intent": "",
                "answer": "",
            },
            ensure_ascii=False,
        )
    )
    unsupported = GroundedAnswerService(
        store,
        evidence_search=search,
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="这件展品是什么材质？",
        llm=unsupported_llm,
    )

    assert unsupported.knowledge_status == "unsupported"
    assert unsupported.cited_evidence_ids == ()
    assert unsupported.guard_result == "model_unsupported"

    dense_only_pack = EvidencePack(
        query_id="dense-only",
        exhibit_ids=(DEMO_EXHIBIT_ID,),
        items=(
            EvidenceItem(
                id=segment_id,
                kind="segment",
                text="这件展品由天然水晶制成。",
                source_id="answer-source",
                segment_id=segment_id,
                source_title="演示资料",
                locator="source.md#section=material",
                score=0.9,
                rank=1,
                source_level="demo_curated",
                content_version=1,
                exhibit_ids=(DEMO_EXHIBIT_ID,),
            ),
        ),
        retrieval_trace={"lexical_candidates": [], "dense_candidates": []},
    )
    dense_only = GroundedAnswerService(
        store,
        evidence_search=_StaticSearch(dense_only_pack),
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它会发光吗？",
        llm=None,
        understanding=QuestionUnderstanding(
            coarse_intent="exhibit_knowledge",
            fine_intent="unknown",
            confidence=0.5,
            source="test",
        ),
    )

    assert dense_only.knowledge_status == "unsupported"
    assert dense_only.cited_evidence_ids == ()

    donor_question = GroundedAnswerService(
        store,
        evidence_search=search,
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="战国水晶杯是谁捐赠的？",
        llm=None,
    )
    assert donor_question.knowledge_status == "unsupported"
    assert donor_question.cited_evidence_ids == ()

    for question in (
        "这件展品的天然材料有多高？",
        "它的水晶工艺是谁发明的？",
    ):
        unrelated = GroundedAnswerService(
            store,
            evidence_search=search,
        ).answer(
            exhibit_id=DEMO_EXHIBIT_ID,
            exhibit_name="战国水晶杯",
            question=question,
            llm=None,
        )
        assert unrelated.knowledge_status == "unsupported"
        assert unrelated.cited_evidence_ids == ()


def test_claim_guard_binds_negation_and_measurements_to_their_objects(
    tmp_path: Path,
):
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    segment_id = "relation-evidence"
    pack = EvidencePack(
        query_id="relation-guard",
        exhibit_ids=(DEMO_EXHIBIT_ID,),
        items=(
            EvidenceItem(
                id=segment_id,
                kind="segment",
                text="这件器物不是玉器而是水晶器，高12厘米，宽8厘米。",
                source_id="relation-source",
                segment_id=segment_id,
                source_title="关系校验资料",
                locator="relation.md#section=record",
                score=0.9,
                rank=1,
                source_level="demo_curated",
                content_version=1,
                exhibit_ids=(DEMO_EXHIBIT_ID,),
            ),
        ),
        retrieval_trace={
            "lexical_candidates": [{"segment_id": segment_id, "score": 10.0}]
        },
    )
    service = GroundedAnswerService(
        store,
        evidence_search=_StaticSearch(pack),
    )
    swapped_material = service.answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它是什么材质？",
        llm=_JsonLlm(
            json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": [],
                    "evidence_ids": [segment_id],
                    "claims": [
                        {
                            "text": "这件器物不是水晶器而是玉器。",
                            "evidence_ids": [segment_id],
                        }
                    ],
                    "social_intent": "",
                    "answer": "这件器物不是水晶器而是玉器。",
                },
                ensure_ascii=False,
            )
        ),
    )
    swapped_dimensions = service.answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它有多高多宽？",
        llm=_JsonLlm(
            json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": [],
                    "evidence_ids": [segment_id],
                    "claims": [
                        {
                            "text": "它高8厘米，宽12厘米。",
                            "evidence_ids": [segment_id],
                        }
                    ],
                    "social_intent": "",
                    "answer": "它高8厘米，宽12厘米。",
                },
                ensure_ascii=False,
            )
        ),
    )

    assert swapped_material.guard_result == "model_claim_negation_mismatch"
    assert swapped_dimensions.guard_result == (
        "model_claim_measurement_relation_mismatch"
    )

    answer_only_swap = service.answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它有多高多宽？",
        llm=_JsonLlm(
            json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": [],
                    "evidence_ids": [segment_id],
                    "claims": [
                        {
                            "text": "这件器物不是玉器而是水晶器，高12厘米，宽8厘米。",
                            "evidence_ids": [segment_id],
                        }
                    ],
                    "social_intent": "",
                    "answer": "它高8厘米，宽12厘米。",
                },
                ensure_ascii=False,
            )
        ),
    )
    assert answer_only_swap.guard_result == (
        "model_answer_measurement_relation_mismatch"
    )

    parallel_pack = EvidencePack(
        query_id="parallel-relation-guard",
        exhibit_ids=(DEMO_EXHIBIT_ID,),
        items=(
            EvidenceItem(
                id="parallel-relation-evidence",
                kind="segment",
                text="该器物高和宽分别为12厘米和8厘米。",
                source_id="parallel-relation-source",
                segment_id="parallel-relation-evidence",
                source_title="并列尺寸资料",
                locator="parallel.md#section=dimensions",
                score=0.9,
                rank=1,
                source_level="demo_curated",
                content_version=1,
                exhibit_ids=(DEMO_EXHIBIT_ID,),
            ),
        ),
        retrieval_trace={
            "lexical_candidates": [
                {"segment_id": "parallel-relation-evidence", "score": 10.0}
            ]
        },
    )
    parallel_swap = GroundedAnswerService(
        store,
        evidence_search=_StaticSearch(parallel_pack),
    ).answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它有多高多宽？",
        llm=_JsonLlm(
            json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": [],
                    "evidence_ids": ["parallel-relation-evidence"],
                    "claims": [
                        {
                            "text": "该器物高和宽分别为8厘米和12厘米。",
                            "evidence_ids": ["parallel-relation-evidence"],
                        }
                    ],
                    "social_intent": "",
                    "answer": "该器物高和宽分别为8厘米和12厘米。",
                },
                ensure_ascii=False,
            )
        ),
    )
    assert parallel_swap.guard_result == (
        "model_claim_measurement_relation_mismatch"
    )


def test_claim_guard_covers_answer_facts_chinese_numbers_and_plain_negation(
    tmp_path: Path,
):
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    segment_id = "claim-coverage-evidence"
    pack = EvidencePack(
        query_id="claim-coverage-guard",
        exhibit_ids=(DEMO_EXHIBIT_ID,),
        items=(
            EvidenceItem(
                id=segment_id,
                kind="segment",
                text=(
                    "这件展品由天然水晶制成，已有两千多年历史。"
                    "并无证据表明这项工艺由某位工匠发明。"
                    "这件展品无纹饰。"
                ),
                source_id="claim-coverage-source",
                segment_id=segment_id,
                source_title="声明覆盖测试资料",
                locator="coverage.md#section=record",
                score=0.9,
                rank=1,
                source_level="demo_curated",
                content_version=1,
                exhibit_ids=(DEMO_EXHIBIT_ID,),
            ),
        ),
        retrieval_trace={
            "lexical_candidates": [{"segment_id": segment_id, "score": 10.0}]
        },
    )
    service = GroundedAnswerService(store, evidence_search=_StaticSearch(pack))

    unclaimed_fact = service.answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="请介绍它。",
        llm=_JsonLlm(
            json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": [],
                    "evidence_ids": [segment_id],
                    "claims": [
                        {
                            "text": "这件展品由天然水晶制成。",
                            "evidence_ids": [segment_id],
                        }
                    ],
                    "social_intent": "",
                    "answer": "这件展品由天然水晶制成。它已有两千多年历史。",
                },
                ensure_ascii=False,
            )
        ),
    )
    changed_chinese_number = service.answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它有多少年历史？",
        llm=_JsonLlm(
            json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": [],
                    "evidence_ids": [segment_id],
                    "claims": [
                        {
                            "text": "这件展品已有三千多年历史。",
                            "evidence_ids": [segment_id],
                        }
                    ],
                    "social_intent": "",
                    "answer": "这件展品已有三千多年历史。",
                },
                ensure_ascii=False,
            )
        ),
    )
    removed_negation = service.answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="这项工艺是谁发明的？",
        llm=_JsonLlm(
            json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": [],
                    "evidence_ids": [segment_id],
                    "claims": [
                        {
                            "text": "证据表明这项工艺由某位工匠发明。",
                            "evidence_ids": [segment_id],
                        }
                    ],
                    "social_intent": "",
                    "answer": "证据表明这项工艺由某位工匠发明。",
                },
                ensure_ascii=False,
            )
        ),
    )
    plain_negation = service.answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它有纹饰吗？",
        llm=_JsonLlm(
            json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": [],
                    "evidence_ids": [segment_id],
                    "claims": [
                        {
                            "text": "这件展品有纹饰。",
                            "evidence_ids": [segment_id],
                        }
                    ],
                    "social_intent": "",
                    "answer": "这件展品有纹饰。",
                },
                ensure_ascii=False,
            )
        ),
    )
    changed_material = service.answer(
        exhibit_id=DEMO_EXHIBIT_ID,
        exhibit_name="战国水晶杯",
        question="它是什么材质？",
        llm=_JsonLlm(
            json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": [],
                    "evidence_ids": [segment_id],
                    "claims": [
                        {
                            "text": "这件展品由天然水晶制成。",
                            "evidence_ids": [segment_id],
                        }
                    ],
                    "social_intent": "",
                    "answer": "这件展品由玉石制成。",
                },
                ensure_ascii=False,
            )
        ),
    )

    assert unclaimed_fact.guard_result == "model_answer_claim_number_mismatch"
    assert changed_chinese_number.guard_result == "model_claim_extra_number"
    assert removed_negation.guard_result == "model_claim_negation_mismatch"
    assert plain_negation.guard_result == "model_claim_negation_mismatch"
    assert changed_material.guard_result == "model_answer_claim_coverage_rejected"


def test_conflict_guard_requires_explicit_disclosure_and_both_unique_facts(
    tmp_path: Path,
):
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    crystal_id = "conflict-crystal"
    jade_id = "conflict-jade"
    pack = EvidencePack(
        query_id="conflict-answer-guard",
        exhibit_ids=(DEMO_EXHIBIT_ID,),
        items=(
            EvidenceItem(
                id=crystal_id,
                kind="segment",
                text="一份资料称这件展品由天然水晶制成。",
                source_id="conflict-source-crystal",
                segment_id=crystal_id,
                source_title="水晶说资料",
                locator="crystal.md#section=material",
                score=0.9,
                rank=1,
                source_level="demo_curated",
                content_version=1,
                exhibit_ids=(DEMO_EXHIBIT_ID,),
            ),
            EvidenceItem(
                id=jade_id,
                kind="segment",
                text="另一份资料称这件展品由玉石制成。",
                source_id="conflict-source-jade",
                segment_id=jade_id,
                source_title="玉石说资料",
                locator="jade.md#section=material",
                score=0.8,
                rank=2,
                source_level="demo_curated",
                content_version=1,
                exhibit_ids=(DEMO_EXHIBIT_ID,),
            ),
        ),
        conflict_groups=((crystal_id, jade_id),),
        retrieval_trace={
            "lexical_candidates": [
                {"segment_id": crystal_id, "score": 10.0},
                {"segment_id": jade_id, "score": 9.0},
            ]
        },
    )
    service = GroundedAnswerService(store, evidence_search=_StaticSearch(pack))

    def answer_with(text: str):
        return service.answer(
            exhibit_id=DEMO_EXHIBIT_ID,
            exhibit_name="战国水晶杯",
            question="它是什么材质？",
            llm=_JsonLlm(
                json.dumps(
                    {
                        "status": "conflicting",
                        "fact_ids": [],
                        "evidence_ids": [crystal_id, jade_id],
                        "claims": [
                            {
                                "text": "一份资料称这件展品由天然水晶制成。",
                                "evidence_ids": [crystal_id],
                            },
                            {
                                "text": "另一份资料称这件展品由玉石制成。",
                                "evidence_ids": [jade_id],
                            },
                        ],
                        "social_intent": "",
                        "answer": text,
                    },
                    ensure_ascii=False,
                )
            ),
        )

    implicit = answer_with("另一份资料称这件展品由天然水晶制成。")
    collapsed = answer_with(
        "资料存在冲突。一份资料称这件展品由天然水晶制成。"
        "另一份资料也称这件展品由天然水晶制成。"
    )
    valid = answer_with(
        "资料存在冲突。一份资料称这件展品由天然水晶制成。"
        "另一份资料称这件展品由玉石制成。"
    )

    assert implicit.guard_result == "model_conflict_not_disclosed"
    assert collapsed.guard_result == "model_conflict_claim_omitted"
    assert valid.guard_result == "model_conflict_answer_accepted"


def test_runtime_persists_segment_evidence_and_returns_evidence_ids(tmp_path: Path):
    store, search, segment_id = _setup(tmp_path)
    runtime = MuseumRuntime(store, evidence_search=search)
    outcome = runtime.handle_turn(
        TurnRequest(
            request_id="evidence-runtime-001",
            transport_session_id="transport-evidence-runtime-001",
            visitor_session_id=None,
            device_id="evidence-demo-device",
            user_text="战国水晶杯是什么材质？",
            history=(),
            occurred_at=datetime.now().astimezone(),
            llm=None,
        )
    )

    assert outcome.evidence_ids == (segment_id,)
    assert outcome.audit_record["evidence_ids"] == [segment_id]
    trace = store.get_interaction_trace_by_request_id("evidence-runtime-001")
    assert trace is not None
    evidence = json.loads(trace["evidence_json"])
    assert evidence["kind"] == "segments"
    assert evidence["evidence_ids"] == [segment_id]
    assert evidence["source_ids"] == ["answer-source"]


def test_runtime_distinguishes_cited_evidence_from_retrieval_candidates(
    tmp_path: Path,
):
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    material_id = "candidate-material"
    era_id = "candidate-era"
    pack = EvidencePack(
        query_id="citation-audit",
        exhibit_ids=(DEMO_EXHIBIT_ID,),
        items=(
            EvidenceItem(
                id=material_id,
                kind="segment",
                text="这件展品由天然水晶制成。",
                source_id="source-material",
                segment_id=material_id,
                source_title="材质资料",
                locator="material.md#section=material",
                score=0.9,
                rank=1,
                source_level="demo_curated",
                content_version=2,
                exhibit_ids=(DEMO_EXHIBIT_ID,),
            ),
            EvidenceItem(
                id=era_id,
                kind="segment",
                text="这件展品属于战国时期。",
                source_id="source-era",
                segment_id=era_id,
                source_title="年代资料",
                locator="era.md#section=era",
                score=0.8,
                rank=2,
                source_level="demo_curated",
                content_version=2,
                exhibit_ids=(DEMO_EXHIBIT_ID,),
            ),
        ),
        retrieval_trace={
            "lexical_candidates": [
                {"segment_id": material_id, "score": 10.0},
                {"segment_id": era_id, "score": 2.0},
            ]
        },
    )
    llm = _JsonLlm(
        json.dumps(
            {
                "status": "grounded",
                "fact_ids": [],
                "evidence_ids": [material_id],
                "claims": [
                    {
                        "text": "这件展品由天然水晶制成。",
                        "evidence_ids": [material_id],
                    }
                ],
                "social_intent": "",
                "answer": "这件展品由天然水晶制成。",
            },
            ensure_ascii=False,
        )
    )
    runtime = MuseumRuntime(store, evidence_search=_StaticSearch(pack))
    outcome = runtime.handle_turn(
        TurnRequest(
            request_id="citation-audit-001",
            transport_session_id="citation-audit-transport",
            visitor_session_id=None,
            device_id="citation-audit-device",
            user_text="战国水晶杯是什么材质？",
            history=(),
            occurred_at=datetime.now().astimezone(),
            llm=llm,
        )
    )

    assert outcome.evidence_ids == (material_id,)
    assert outcome.source_ids == ("source-material",)
    assert outcome.audit_record["candidate_evidence_ids"] == [material_id, era_id]
    trace = store.get_interaction_trace_by_request_id("citation-audit-001")
    evidence = json.loads(trace["evidence_json"])
    assert evidence["evidence_ids"] == [material_id]
    assert evidence["candidate_evidence_ids"] == [material_id, era_id]
    assert evidence["source_ids"] == ["source-material"]
    assert evidence["answer_claims"] == [
        {
            "text": "这件展品由天然水晶制成。",
            "evidence_ids": [material_id],
        }
    ]


def test_factory_can_select_evidence_segments_backend_with_lexical_fallback(
    tmp_path: Path,
):
    database = tmp_path / "factory-evidence.db"
    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(database),
                "seed_demo_content": True,
                "exhibit_context_mode": "explicit",
                "retrieval": {
                    "backend": "evidence_segments",
                    "lexical_limit": 8,
                },
            }
        }
    )
    dataset = tmp_path / "factory-dataset"
    dataset.mkdir()
    (dataset / "source.md").write_text(
        "# 材质\n\n这件展品由天然水晶制成。\n",
        encoding="utf-8",
    )
    manifest = dataset / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "factory-evidence-test",
                "museum": {
                    "id": "hangzhou-museum-demo",
                    "name": "杭州博物馆（演示数据）",
                },
                "sources": [
                    {
                        "id": "factory-source",
                        "title": "工厂后端演示资料",
                        "source_type": "markdown",
                        "path": "source.md",
                        "rights_note": "自动化测试夹具。",
                        "exhibit_ids": [DEMO_EXHIBIT_ID],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = ingest_source_manifest(
        manifest,
        store=MuseumStore(database),
        run_id="factory-ingest",
    )
    assert report.ok
    with MuseumStore(database).connection() as connection:
        connection.execute(
            "INSERT INTO knowledge_claim_support(fact_id, segment_id) VALUES (?, ?)",
            ("fact-crystal-cup-material", report.segment_ids[0]),
        )
    outcome = runtime.handle_turn(
        TurnRequest(
            request_id="factory-evidence-001",
            transport_session_id="factory-transport-001",
            visitor_session_id=None,
            device_id="factory-evidence-device",
            user_text="战国水晶杯是什么材质？",
            history=(),
            occurred_at=datetime.now().astimezone(),
            llm=None,
        )
    )
    assert outcome.knowledge_status == "grounded"
    assert outcome.evidence_ids == report.segment_ids
    assert outcome.audit_record["retrieval_trace"]["backend"] == "evidence_segments"
