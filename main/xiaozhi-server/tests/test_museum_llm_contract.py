from __future__ import annotations

import json
from datetime import datetime

from core.business_runtime_factory import create_conversation_runtime
from core.conversation_runtime import TurnRequest
from core.museum.store import MuseumStore


class _JsonLlm:
    model_name = "deepseek-v4-flash"

    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    def response_no_stream(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        return self.response


def _request(
    *,
    llm,
    request_id: str,
    text: str = "战国水晶杯是什么材质？",
) -> TurnRequest:
    return TurnRequest(
        request_id=request_id,
        transport_session_id=f"transport-{request_id}",
        visitor_session_id=None,
        device_id=f"device-{request_id}",
        user_text=text,
        history=(),
        occurred_at=datetime.now().astimezone(),
        llm=llm,
    )


def test_llm_json_contract_and_audit_metadata_are_persisted(tmp_path):
    database = tmp_path / "museum.db"
    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(database),
                "seed_demo_content": True,
                "exhibit_context_mode": "explicit",
            }
        }
    )
    llm = _JsonLlm(
        json.dumps(
            {
                "status": "grounded",
                "fact_ids": ["fact-crystal-cup-material"],
                "social_intent": "",
                "answer": "这件杯子由一整块天然水晶琢制而成。馆方资料确认它使用的是天然水晶。",
            },
            ensure_ascii=False,
        )
    )

    outcome = runtime.handle_turn(
        _request(llm=llm, request_id="llm-contract-grounded")
    )

    assert llm.calls[0]["kwargs"]["response_format"] == {"type": "json_object"}
    assert outcome.audit_record["llm_invoked"] is True
    assert outcome.audit_record["llm_model"] == "deepseek-v4-flash"
    assert outcome.audit_record["llm_prompt_version"] == "museum-grounded-router-v1"
    assert outcome.audit_record["llm_result"] == "parsed"
    response_summary = json.loads(outcome.audit_record["llm_response_summary"])
    assert response_summary["status"] == "grounded"
    assert response_summary["fact_ids"] == ["fact-crystal-cup-material"]
    assert len(response_summary["sha256"]) == 64
    assert "天然水晶" not in outcome.audit_record["llm_response_summary"]

    trace = MuseumStore(database).get_interaction_trace_by_request_id(
        "llm-contract-grounded"
    )
    assert trace is not None
    assert trace["llm_invoked"] == 1
    assert trace["llm_model"] == "deepseek-v4-flash"
    assert trace["llm_prompt_version"] == "museum-grounded-router-v1"
    assert trace["llm_result"] == "parsed"
    assert trace["llm_response_summary"] == outcome.audit_record[
        "llm_response_summary"
    ]


def test_detailed_overview_accepts_five_grounded_facts_from_llm(tmp_path):
    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(tmp_path / "museum.db"),
                "seed_demo_content": True,
                "exhibit_context_mode": "explicit",
            }
        }
    )
    llm = _JsonLlm(
        json.dumps(
            {
                "status": "grounded",
                "fact_ids": [
                    "fact-crystal-cup-era",
                    "fact-crystal-cup-material",
                    "fact-crystal-cup-appearance",
                    "fact-crystal-cup-excavation",
                    "fact-crystal-cup-dimensions",
                ],
                "social_intent": "",
                "answer": (
                    "这件水晶杯经鉴定为战国中晚期遗物，已有两千多年历史。"
                    "它由一整块天然水晶琢制而成。"
                    "它器口微敞、杯壁斜直、圈足外撇，外形很像现代常见的玻璃杯。"
                    "它于1990年在杭州半山镇石塘村的战国墓葬中出土。"
                    "它高15.4厘米，口径7.8厘米，底径5.4厘米。"
                ),
            },
            ensure_ascii=False,
        )
    )

    outcome = runtime.handle_turn(
        _request(
            llm=llm,
            request_id="llm-contract-detailed-overview",
            text="请详细介绍一下战国水晶杯",
        )
    )

    assert len(outcome.fact_ids) == 5
    assert outcome.audit_record["guard_result"] == "model_answer_accepted"
    assert "最多5个" in llm.calls[0]["system_prompt"]


def test_llm_rejection_or_invalid_response_cannot_override_retrieved_evidence(
    tmp_path,
):
    database = tmp_path / "museum.db"
    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(database),
                "seed_demo_content": True,
                "exhibit_context_mode": "explicit",
            }
        }
    )
    llm = _JsonLlm("not-json")

    outcome = runtime.handle_turn(
        _request(llm=llm, request_id="llm-contract-invalid")
    )

    assert outcome.knowledge_status == "grounded"
    assert outcome.fact_ids == ("fact-crystal-cup-material",)
    assert outcome.audit_record["llm_result"] == "invalid_response"
    assert outcome.audit_record["guard_result"] == "model_response_invalid_fallback"
    summary = json.loads(outcome.audit_record["llm_response_summary"])
    assert summary["parse_status"] == "invalid_response"
    assert summary["chars"] == len("not-json")

    unsupported_llm = _JsonLlm(
        json.dumps(
            {
                "status": "unsupported",
                "fact_ids": [],
                "social_intent": "",
                "answer": "",
            },
            ensure_ascii=False,
        )
    )
    unsupported_outcome = runtime.handle_turn(
        _request(llm=unsupported_llm, request_id="llm-contract-false-unsupported")
    )

    assert unsupported_outcome.knowledge_status == "grounded"
    assert unsupported_outcome.fact_ids == ("fact-crystal-cup-material",)
    assert unsupported_outcome.audit_record["llm_result"] == "parsed"
    assert (
        unsupported_outcome.audit_record["guard_result"]
        == "model_unsupported_grounded_fallback"
    )


def test_llm_contract_rejects_wrong_field_shapes(tmp_path):
    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(tmp_path / "museum.db"),
                "seed_demo_content": True,
                "exhibit_context_mode": "explicit",
            }
        }
    )
    llm = _JsonLlm(
        json.dumps(
            {
                "status": "grounded",
                "fact_ids": "fact-crystal-cup-material",
                "answer": "这不是合法的结构化响应。",
            },
            ensure_ascii=False,
        )
    )

    outcome = runtime.handle_turn(
        _request(llm=llm, request_id="llm-contract-wrong-shape")
    )

    assert outcome.knowledge_status == "grounded"
    assert outcome.audit_record["llm_result"] == "invalid_response"
    assert outcome.audit_record["guard_result"] == "model_response_invalid_fallback"


def test_grounded_llm_response_cannot_mix_in_a_social_intent(tmp_path):
    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(tmp_path / "museum.db"),
                "seed_demo_content": True,
                "exhibit_context_mode": "explicit",
            }
        }
    )
    llm = _JsonLlm(
        json.dumps(
            {
                "status": "grounded",
                "fact_ids": ["fact-crystal-cup-material"],
                "social_intent": "greeting",
                "answer": "这件杯子由一整块天然水晶琢制而成。馆方资料确认它使用的是天然水晶。",
            },
            ensure_ascii=False,
        )
    )

    outcome = runtime.handle_turn(
        _request(llm=llm, request_id="llm-contract-mixed-intent")
    )

    assert outcome.knowledge_status == "grounded"
    assert outcome.audit_record["llm_result"] == "invalid_response"
    assert outcome.audit_record["guard_result"] == "model_response_invalid_fallback"
