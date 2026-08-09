import json
from datetime import datetime

from core.business_runtime_factory import create_conversation_runtime
from core.conversation_runtime import TurnRequest
from core.museum.store import MuseumStore


def _request(*, text, device_id="demo-device", llm=None):
    return TurnRequest(
        request_id="request-1",
        transport_session_id="transport-1",
        visitor_session_id=None,
        device_id=device_id,
        user_text=text,
        history=(),
        occurred_at=datetime.now().astimezone(),
        llm=llm,
    )


def _runtime(tmp_path, **runtime_overrides):
    config = {
        "business_runtime": {
            "type": "museum",
            "database_path": str(tmp_path / "museum.db"),
            "demo_device_id": "demo-device",
            **runtime_overrides,
        }
    }
    return create_conversation_runtime(
        config,
        legacy_turn_handler=lambda *_: False,
    )


def test_published_fact_produces_grounded_answer_and_trace(tmp_path):
    runtime = _runtime(tmp_path)

    outcome = runtime.handle_turn(_request(text="它是什么材质做的？"))

    assert outcome.handled is True
    assert "一整块天然水晶" in outcome.spoken_text
    assert outcome.audit_record["knowledge_status"] == "grounded"
    assert outcome.audit_record["fact_ids"] == ["fact-crystal-cup-material"]
    assert outcome.audit_record["source_ids"]
    trace = MuseumStore(tmp_path / "museum.db").get_interaction_trace(
        outcome.audit_record["trace_id"]
    )
    evidence = json.loads(trace["evidence_json"])
    assert evidence["fact_ids"] == ["fact-crystal-cup-material"]
    assert trace["grounding_status"] == "grounded"
    assert trace["guard_result"] == "published_facts_only"
    assert json.loads(trace["stage_latency_json"])["total_ms"] >= 0

    overview = runtime.handle_turn(_request(text="水晶杯有什么特点？"))
    assert overview.audit_record["knowledge_status"] == "grounded"
    assert "玻璃杯" in overview.spoken_text


def test_unsupported_question_returns_explicit_fallback(tmp_path):
    runtime = _runtime(tmp_path)

    outcome = runtime.handle_turn(_request(text="这个杯子在战国时卖多少钱？"))

    assert outcome.handled is True
    assert "不能替它补一个答案" in outcome.spoken_text
    assert outcome.audit_record["knowledge_status"] == "unsupported"
    assert outcome.audit_record["fact_ids"] == []
    assert outcome.display_state["grounding"]["status"] == "unsupported"

    store = MuseumStore(tmp_path / "museum.db")
    with store.connection() as connection:
        connection.execute("DELETE FROM fact_source")
    no_sourced_facts = runtime.handle_turn(_request(text="它是什么材质做的？"))
    assert no_sourced_facts.audit_record["knowledge_status"] == "unsupported"
    assert "不能替它补一个答案" in no_sourced_facts.spoken_text


def test_missing_current_exhibit_is_handled_without_legacy_or_llm_fallback(tmp_path):
    legacy_calls = []

    class LLMThatMustNotRun:
        def response(self, *_args, **_kwargs):
            raise AssertionError("museum missing-context handling invoked the LLM")

    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(tmp_path / "museum.db"),
                "auto_assign_unknown_devices": False,
            }
        },
        legacy_turn_handler=lambda *args: legacy_calls.append(args) or True,
    )

    outcome = runtime.handle_turn(
        _request(text="给我介绍一下", device_id="unplaced-device", llm=LLMThatMustNotRun())
    )

    assert outcome.handled is True
    assert outcome.error_code == "current_exhibit_missing"
    assert outcome.audit_record["knowledge_status"] == "missing_context"
    assert legacy_calls == []
