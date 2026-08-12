from __future__ import annotations

import json
from datetime import datetime

import pytest

from core.business_runtime_factory import create_conversation_runtime
from core.conversation_runtime import TurnRequest
from core.museum.content_import import (
    audit_interaction_evidence,
    withdraw_revision,
)
from core.museum.store import MuseumStore


def _request(*, text: str, request_id: str, device_id: str = "guard-device", llm=None):
    return TurnRequest(
        request_id=request_id,
        transport_session_id=f"transport-{request_id}",
        visitor_session_id=None,
        device_id=device_id,
        user_text=text,
        history=(),
        occurred_at=datetime.now().astimezone(),
        llm=llm,
    )


def _runtime(tmp_path):
    return create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(tmp_path / "museum.db"),
                "seed_demo_content": True,
                "exhibit_context_mode": "explicit",
            }
        }
    )


class _JsonLLM:
    def __init__(self, payload):
        self._payload = payload

    def response_no_stream(self, *_args, **_kwargs):
        return json.dumps(self._payload, ensure_ascii=False)


def test_invalid_fact_id_is_rejected_and_recorded_by_the_answer_guard(tmp_path):
    runtime = _runtime(tmp_path)

    outcome = runtime.handle_turn(
        _request(
            text="战国水晶杯是什么材质？",
            request_id="invalid-fact-id",
            llm=_JsonLLM(
                {
                    "status": "grounded",
                    "fact_ids": ["fact-does-not-exist"],
                    "social_intent": "",
                    "answer": "它是王室专用的玉石器物。",
                }
            ),
        )
    )

    assert outcome.knowledge_status == "grounded"
    assert "一整块天然水晶" in outcome.spoken_text
    assert "王室专用" not in outcome.spoken_text
    trace = MuseumStore(tmp_path / "museum.db").get_interaction_trace(
        outcome.audit_id
    )
    assert trace["guard_result"] == "model_fact_ids_rejected"


@pytest.mark.parametrize(
    ("answer", "expected_guard", "forbidden"),
    (
        (
            "它由一整块天然水晶琢制而成。张三亲自设计了它。",
            "model_answer_unsupported_claim",
            "张三",
        ),
        (
            "它由一整块天然水晶琢制而成。它出土于北京故宫。",
            "model_answer_unsupported_claim",
            "北京故宫",
        ),
        (
            "它由一整块天然水晶琢制而成。它高99厘米。",
            "model_answer_extra_number",
            "99厘米",
        ),
        (
            "它由一整块天然水晶琢制而成。"
            + "这段补充说明不在馆方资料中。" * 30,
            "model_answer_too_long",
            "这段补充说明",
        ),
    ),
)
def test_model_added_facts_are_never_spoken_and_guard_reason_is_recorded(
    tmp_path,
    answer,
    expected_guard,
    forbidden,
):
    runtime = _runtime(tmp_path)

    outcome = runtime.handle_turn(
        _request(
            text="战国水晶杯是什么材质？",
            request_id=f"guard-{expected_guard}",
            llm=_JsonLLM(
                {
                    "status": "grounded",
                    "fact_ids": ["fact-crystal-cup-material"],
                    "social_intent": "",
                    "answer": answer,
                }
            ),
        )
    )

    assert outcome.knowledge_status == "grounded"
    assert "一整块天然水晶" in outcome.spoken_text
    assert forbidden not in outcome.spoken_text
    trace = MuseumStore(tmp_path / "museum.db").get_interaction_trace(
        outcome.audit_id
    )
    assert trace["guard_result"] == expected_guard


def test_non_social_question_cannot_be_rewritten_as_assistant_identity(tmp_path):
    runtime = _runtime(tmp_path)

    outcome = runtime.handle_turn(
        _request(
            text="战国水晶杯的馆长叫什么名字？",
            request_id="reject-conversational-mismatch",
            llm=_JsonLLM(
                {
                    "status": "conversational",
                    "fact_ids": [],
                    "social_intent": "identity",
                    "answer": "",
                }
            ),
        )
    )

    assert outcome.knowledge_status == "unsupported"
    assert "小芯" not in outcome.spoken_text
    assert outcome.audit_record["guard_result"] == (
        "model_conversational_intent_mismatch"
    )


def test_withdrawn_revision_is_hidden_from_new_answers_but_old_trace_remains(
    tmp_path,
):
    runtime = _runtime(tmp_path)
    store = MuseumStore(tmp_path / "museum.db")

    first = runtime.handle_turn(
        _request(
            text="战国水晶杯是什么材质？",
            request_id="before-withdrawal",
        )
    )
    old_trace = store.get_interaction_trace(first.audit_id)
    assert first.knowledge_status == "grounded"
    assert old_trace["grounding_status"] == "grounded"

    withdrawn = withdraw_revision(
        store,
        revision_id="warring-states-crystal-cup-r1",
        withdrawn_by="fixture-operator",
        withdrawn_at=datetime.now().astimezone(),
        reason="测试撤回",
    )

    second = runtime.handle_turn(
        _request(
            text="战国水晶杯是什么材质？",
            request_id="after-withdrawal",
        )
    )
    new_trace = store.get_interaction_trace(second.audit_id)
    historical = audit_interaction_evidence(
        store,
        request_id="before-withdrawal",
    )

    assert second.knowledge_status == "unsupported"
    assert withdrawn.status == "withdrawn"
    assert second.fact_ids == ()
    assert "一整块天然水晶" not in second.spoken_text
    assert new_trace["grounding_status"] == "unsupported"
    assert new_trace["unanswered_reason"] == "no_published_fact_match"
    assert json.loads(new_trace["evidence_json"])["fact_ids"] == []
    assert json.loads(old_trace["evidence_json"])["fact_ids"] == [
        "fact-crystal-cup-material"
    ]
    assert json.loads(old_trace["evidence_json"])["content_revision_id"] == (
        "warring-states-crystal-cup-r1"
    )
    assert historical.content_revision_id == "warring-states-crystal-cup-r1"
    assert historical.facts[0].fact_id == "fact-crystal-cup-material"
    assert "一整块天然水晶" in historical.facts[0].statement
    assert {source.source_id for source in historical.sources} == {
        "source-hangzhou-portal-2020",
        "source-people-daily-2026",
    }
