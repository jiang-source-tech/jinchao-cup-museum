import json
from datetime import datetime

from core.business_runtime_factory import create_conversation_runtime
from core.conversation_runtime import TurnRequest
from core.museum.answering import GroundedAnswerService
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
            "exhibit_context_mode": "explicit",
            **runtime_overrides,
        }
    }
    return create_conversation_runtime(config)


def test_published_fact_produces_grounded_answer_and_trace(tmp_path):
    runtime = _runtime(tmp_path)

    outcome = runtime.handle_turn(
        _request(text="战国水晶杯是什么材质做的？")
    )

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
    assert trace["coarse_intent"] == "exhibit_knowledge"
    assert trace["fine_intent"] == "material"
    assert trace["intent_confidence"] > 0.7
    assert trace["guard_result"] == "published_facts_only"
    assert json.loads(trace["stage_latency_json"])["total_ms"] >= 0

    overview = runtime.handle_turn(_request(text="水晶杯有什么特点？"))
    assert overview.audit_record["knowledge_status"] == "grounded"
    assert "玻璃杯" in overview.spoken_text

    class HallucinatingLLM:
        def response_no_stream(self, *_args):
            return "这件水晶杯是王室祭祀专用器物。它由一位著名工匠亲手制作。"

    guarded = runtime.handle_turn(
        _request(text="战国水晶杯是什么材质做的？", llm=HallucinatingLLM())
    )
    assert "王室" not in guarded.spoken_text
    assert "一整块天然水晶" in guarded.spoken_text

    class SemanticFactSelector:
        def response_no_stream(self, _system_prompt, _user_prompt, **_kwargs):
            return json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": ["fact-crystal-cup-material"],
                    "social_intent": "",
                    "answer": "它由一整块天然水晶琢制而成。",
                },
                ensure_ascii=False,
            )

    natural_question = runtime.handle_turn(
        _request(
            text="你好，古人选了哪种矿石来琢它？",
            llm=SemanticFactSelector(),
        )
    )
    assert natural_question.knowledge_status == "grounded"
    assert natural_question.fact_ids == ("fact-crystal-cup-material",)
    assert "一整块天然水晶" in natural_question.spoken_text


def test_unsupported_question_returns_explicit_fallback(tmp_path):
    runtime = _runtime(tmp_path)

    outcome = runtime.handle_turn(
        _request(text="战国水晶杯在战国时卖多少钱？")
    )

    assert outcome.handled is True
    assert "不能替它补一个答案" in outcome.spoken_text
    assert outcome.audit_record["knowledge_status"] == "unsupported"
    assert outcome.audit_record["fact_ids"] == []
    assert outcome.display_state["grounding"]["status"] == "unsupported"

    store = MuseumStore(tmp_path / "museum.db")
    with store.connection() as connection:
        connection.execute("DELETE FROM fact_source")
    no_sourced_facts = runtime.handle_turn(
        _request(text="战国水晶杯是什么材质做的？")
    )
    assert no_sourced_facts.audit_record["knowledge_status"] == "unsupported"
    assert "不能替它补一个答案" in no_sourced_facts.spoken_text


def test_llm_cannot_use_an_era_fact_to_answer_a_price_question(tmp_path):
    class WrongFactSelector:
        def response_no_stream(self, _system_prompt, _user_prompt, **_kwargs):
            return json.dumps(
                {
                    "status": "grounded",
                    "fact_ids": ["fact-crystal-cup-era"],
                    "social_intent": "",
                    "answer": "它卖了很多钱。",
                },
                ensure_ascii=False,
            )

    runtime = _runtime(tmp_path)
    outcome = runtime.handle_turn(
        _request(
            text="战国水晶杯在战国时期卖了多少钱？",
            llm=WrongFactSelector(),
        )
    )

    assert outcome.knowledge_status == "unsupported"
    assert outcome.fact_ids == ()
    assert outcome.audit_record["fine_intent"] == "price"


def test_comparison_does_not_reuse_a_single_exhibit_fact(tmp_path):
    runtime = _runtime(tmp_path)
    outcome = runtime.handle_turn(
        _request(text="水晶杯和其他展品有什么区别？")
    )

    assert outcome.knowledge_status == "unsupported"
    assert outcome.fact_ids == ()
    assert outcome.audit_record["coarse_intent"] == "comparison"


def test_explicit_reference_establishes_context_then_inherits_and_switches(tmp_path):
    runtime = _runtime(tmp_path)
    store = MuseumStore(tmp_path / "museum.db")
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO exhibit(id, zone_id, name, aliases_json, image_uri, status)
            VALUES (?, ?, ?, ?, NULL, 'active')
            """,
            (
                "sword-demo",
                "hangzhou-history-demo-zone",
                "越王勾践剑",
                '["勾践剑"]',
            ),
        )
        connection.execute(
            """
            INSERT INTO source_document(
                id, museum_id, title, source_type, locator, rights_note
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "source-sword-demo",
                "hangzhou-museum-demo",
                "越王勾践剑演示资料",
                "demo",
                "test",
                "test only",
            ),
        )
        connection.execute(
            """
            INSERT INTO content_revision(
                id, exhibit_id, revision_no, status,
                reviewed_by, reviewed_at, published_at
            ) VALUES (?, ?, 1, 'published', ?, ?, ?)
            """,
            (
                "sword-demo-r1",
                "sword-demo",
                "test-reviewer",
                "2026-08-11T00:00:00+00:00",
                "2026-08-11T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO exhibit_fact(
                id, revision_id, fact_type, statement, keywords_json, confidence
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "fact-sword-material",
                "sword-demo-r1",
                "material",
                "越王勾践剑的主要材质是青铜。",
                '["材质", "材料", "青铜"]',
                "reviewed-demo",
            ),
        )
        connection.execute(
            "INSERT INTO fact_source(fact_id, source_id) VALUES (?, ?)",
            ("fact-sword-material", "source-sword-demo"),
        )

    first = runtime.handle_turn(
        _request(text="战国水晶杯是什么材质？")
    )
    follow_up = runtime.handle_turn(_request(text="它是怎么做出来的？"))
    switched = runtime.handle_turn(
        _request(text="越王勾践剑是什么材质？")
    )

    assert first.display_state["context"]["source"] == "explicit_mention"
    assert follow_up.display_state["context"]["source"] == "inherited_session"
    assert "水晶硬度高" in follow_up.spoken_text
    assert switched.display_state["context"]["exhibit_id"] == "sword-demo"
    assert switched.display_state["context"]["source"] == "explicit_mention"
    assert switched.fact_ids == ("fact-sword-material",)
    assert "青铜" in switched.spoken_text


def test_natural_follow_up_variants_keep_the_established_exhibit(tmp_path):
    runtime = _runtime(tmp_path)

    first = runtime.handle_turn(
        _request(text="战国水晶杯是什么材质？")
    )
    follow_ups = (
        ("这个杯子是怎么做出来的？", "fact-crystal-cup-craft-limit"),
        ("你能讲讲它的历史吗？", "fact-crystal-cup-era"),
        ("它的制作工艺复杂吗？", "fact-crystal-cup-craft-limit"),
    )

    assert first.knowledge_status == "grounded"
    for question, expected_fact_id in follow_ups:
        outcome = runtime.handle_turn(_request(text=question))
        assert outcome.knowledge_status == "grounded"
        assert outcome.fact_ids == (expected_fact_id,)
        assert outcome.display_state["context"]["exhibit_id"] == "warring-states-crystal-cup"
        assert outcome.display_state["context"]["source"] == "inherited_session"


def test_explicit_mode_never_uses_an_existing_device_placement(tmp_path):
    runtime = _runtime(tmp_path)
    store = MuseumStore(tmp_path / "museum.db")
    store.ensure_demo_placement("demo-device", datetime.now().astimezone())

    outcome = runtime.handle_turn(_request(text="它是什么材质做的？"))

    assert outcome.knowledge_status == "missing_context"
    assert outcome.error_code == "exhibit_reference_missing"
    assert outcome.display_state["context"]["exhibit_id"] == ""


def test_unlisted_exhibit_switch_does_not_answer_from_previous_exhibit(tmp_path):
    runtime = _runtime(tmp_path)

    first = runtime.handle_turn(
        _request(text="战国水晶杯是什么材质？")
    )
    switched = runtime.handle_turn(
        _request(text="换成越王勾践剑，它是什么材质？")
    )

    assert first.knowledge_status == "grounded"
    assert switched.knowledge_status == "missing_context"
    assert switched.error_code == "exhibit_reference_missing"
    assert switched.audit_record["resolution_status"] == "not_found"
    assert "没有收录" in switched.spoken_text
    assert switched.display_state["context"]["exhibit_id"] == ""


def test_identity_question_gets_a_normal_conversational_reply(tmp_path):
    class LLMThatMustNotRun:
        def response_no_stream(self, *_args, **_kwargs):
            raise AssertionError("common identity reply should not require the LLM")

    runtime = _runtime(tmp_path)

    outcome = runtime.handle_turn(
        _request(text="你好，你是谁？", llm=LLMThatMustNotRun())
    )

    assert outcome.handled is True
    assert outcome.knowledge_status == "conversational"
    assert "小芯" in outcome.spoken_text
    assert "金潮杯博物馆" in outcome.spoken_text
    assert "不能替它补一个答案" not in outcome.spoken_text
    assert outcome.fact_ids == ()
    assert outcome.display_state["grounding"]["status"] == "ready"
    trace = MuseumStore(tmp_path / "museum.db").get_interaction_trace(
        outcome.audit_id
    )
    assert trace["grounding_status"] == "conversational"
    assert trace["guard_result"] == "conversational_scope"
    assert GroundedAnswerService.answer_conversational("你好这个杯子多大") is None

    unassigned_runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(tmp_path / "museum.db"),
                "exhibit_context_mode": "explicit",
                "auto_assign_unknown_devices": False,
            }
        }
    )

    unassigned_outcome = unassigned_runtime.handle_turn(
        _request(text="你好，你是谁？", device_id="unplaced-device")
    )

    assert unassigned_outcome.handled is True
    assert unassigned_outcome.error_code is None
    assert unassigned_outcome.knowledge_status == "conversational"
    assert "金潮杯博物馆" in unassigned_outcome.spoken_text
    assert unassigned_outcome.display_state["context"]["source"] == "unassigned"
    assert unassigned_outcome.display_state["grounding"]["status"] == "ready"
    trace = MuseumStore(tmp_path / "museum.db").get_interaction_trace(
        unassigned_outcome.audit_id
    )
    assert trace["grounding_status"] == "conversational"


def test_missing_current_exhibit_is_handled_without_legacy_or_llm_fallback(tmp_path):
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
        }
    )

    outcome = runtime.handle_turn(
        _request(
            text="给我介绍一下",
            device_id="unplaced-device",
            llm=LLMThatMustNotRun(),
        )
    )

    assert outcome.handled is True
    assert outcome.error_code == "exhibit_reference_missing"
    assert outcome.audit_record["knowledge_status"] == "missing_context"
