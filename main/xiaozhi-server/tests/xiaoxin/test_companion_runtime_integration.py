from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from inspect import getsource
import sqlite3

import pytest

from core.xiaoxin.companion import (
    CompanionCommitResult,
    CompanionControlCommand,
    CompanionControlResult,
    CompanionEvidence,
    CompanionExpressionStyle,
    CompanionMind,
    CompanionPolicy,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
    MEMORY_INTERPRETATION_RESULT_VERSION,
    MemoryInterpretationResult,
    MemoryInterpreter,
    PreparedCompanionTurn,
    TurnBehaviorPlan,
)
from core.xiaoxin.companion.store import CompanionStore
from core.xiaoxin.runtime import XiaoxinRuntime
from core.xiaoxin.turn_analysis import companion_context
import core.xiaoxin.prompts as prompts
from core.xiaoxin.types import XiaoxinConfig


class RecordingCompanionMind:
    def __init__(
        self,
        events: list[str],
        *,
        prompt_context: tuple[str, ...] = ("用户明确偏好简短回答。",),
        uses_semantic_user_facts: bool = False,
    ) -> None:
        self.events = events
        self.prepare_calls = []
        self.commit_calls = []
        self.recall_calls = []
        self.prompt_context = prompt_context
        self.uses_semantic_user_facts = uses_semantic_user_facts

    def prepare_turn(self, request):
        self.events.append("prepare")
        self.prepare_calls.append(request)
        memory_budget = 0 if request.interaction_kind == "general_qa" else 1
        prompt_context = () if memory_budget == 0 else self.prompt_context
        used_evidence_ids = () if memory_budget == 0 else ("evidence-1",)
        return PreparedCompanionTurn(
            turn_id=request.turn_id,
            owner_user_id=request.subject.owner_user_id,
            pet_id=request.subject.pet_id,
            memory_subject_id=request.subject.memory_subject_id,
            relationship_epoch_id="epoch-1",
            request_digest=request.request_digest,
            occurred_at=request.occurred_at,
            prepared_token="opaque-test-token",
            policy=CompanionPolicy(
                xiaoxin_age=2,
                relationship_stage="first_meeting",
                response_length="standard",
                question_budget=1,
                memory_reference_budget=memory_budget,
                initiative_level="low",
                emotional_posture="warm",
                closure_style="concise",
            ),
            persistence_allowed=True,
            prompt_context=prompt_context,
            used_evidence_ids=used_evidence_ids,
            academic_stage=request.subject.academic_stage,
            surface=request.surface,
            interaction_kind=request.interaction_kind,
        )

    def _recall_companion_memory(self, prepared, **kwargs):
        self.recall_calls.append(kwargs)
        return prepared, {
            "memories": prepared.prompt_context,
            "reason_code": "test_recall",
        }

    def commit_turn(self, prepared, outcome):
        self.events.append("commit")
        self.commit_calls.append((prepared, outcome))
        return CompanionCommitResult(
            turn_id=prepared.turn_id,
            status="committed",
            evidence_ids=("evidence-2",),
        )


class RecordingAdapter:
    def __init__(self, events: list[str], reply: str) -> None:
        self.events = events
        self.reply = reply
        self.calls = []

    def complete_chat(self, messages, max_tokens=None, temperature=None):
        self.events.append("llm")
        self.calls.append(messages)
        return self.reply


class SequenceAdapter:
    def __init__(self, events: list[str], replies: tuple[str, ...]) -> None:
        self.events = events
        self.replies = replies
        self.calls = []

    def complete_chat(self, messages, max_tokens=None, temperature=None):
        self.events.append("llm")
        self.calls.append(messages)
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[index]


class RaisingAdapter:
    def complete_chat(self, messages, max_tokens=None, temperature=None):
        raise RuntimeError("llm offline")


class EmptySemanticModel:
    def interpret(self, request):
        return MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION
        )


class NativeMemoryToolLLM:
    model_name = "native-memory-tool-test"

    def __init__(self) -> None:
        self.function_calls = 0
        self.final_calls = []

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        self.function_calls += 1
        yield None, [
            {
                "id": "runtime-recall-1",
                "function": {
                    "name": "recall_companion_memory",
                    "arguments": '{"query":"上次让我紧张的那个考试"}',
                },
            }
        ]

    def response(self, session_id, dialogue, **kwargs):
        self.final_calls.append(dialogue)
        yield "你之前提到，六级考试会让你紧张。"


class NativeNoToolMemoryLLM:
    model_name = "native-no-tool-memory-test"
    supports_native_function_calls = True

    def __init__(self) -> None:
        self.calls = []

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        self.calls.append(dialogue)
        yield "你的专属测试代号是蓝杉4729。", None

    def response(self, session_id, dialogue, **kwargs):
        raise AssertionError("native provider should receive the bounded tool request")


class NativeMemoryDenialLLM:
    model_name = "native-memory-denial-test"
    supports_native_function_calls = True

    def __init__(self, *, recover_on_retry: bool, denies_memory: bool = True) -> None:
        self.recover_on_retry = recover_on_retry
        self.denies_memory = denies_memory
        self.calls = []

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        self.calls.append(dialogue)
        if not self.denies_memory:
            yield "因为", None
            return
        yield "小芯这次真的没有之前的记忆啦，系统里没存下这些信息。", None

    def response(self, session_id, dialogue, **kwargs):
        self.calls.append(dialogue)
        if not self.denies_memory:
            yield "如果"
            return
        retry_prompt = str(dialogue[-1].get("content") or "")
        if self.recover_on_retry and "上一条错误地否认了已召回记忆" in retry_prompt:
            yield "你现在主要在准备嵌入式课程设计，习惯先抓关键路径。"
            return
        yield "小脑袋里空空如也，我确实不记得了。"


class FailingCommitCompanionMind(RecordingCompanionMind):
    def commit_turn(self, prepared, outcome):
        self.events.append("commit")
        self.commit_calls.append((prepared, outcome))
        raise OSError("sqlite unavailable")


class ControlCompanionMind(RecordingCompanionMind):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.control_calls = []

    def apply_control(self, command):
        self.events.append("control")
        self.control_calls.append(command)
        return CompanionControlResult(
            action=command.action,
            status="applied",
            retained=3,
            deactivated=4,
            requeued=1,
        )


def test_v2_runtime_prepares_before_llm_and_commits_after_visible_reply(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(events)
    adapter = RecordingAdapter(events, "我在，慢慢说。")
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: adapter,
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    result = runtime.handle_turn(
        user_id="device-1",
        user_text="今天有点累，这次别追问，简短一点。",
        history=[],
        llm=object(),
        session_id="session-1",
        turn_id="turn-runtime-v2-1",
        companion_subject_context=subject,
    )

    assert result.reply == "我在，慢慢说。"
    assert events == ["prepare", "llm", "commit"]
    assert mind.prepare_calls[0].source_text == "今天有点累，这次别追问，简短一点。"
    assert mind.prepare_calls[0].current_turn_corrections == (
        "no_follow_up",
        "concise",
    )
    assert mind.commit_calls[0][1].visible_response == result.reply


def test_explicit_low_mood_contract_survives_a_new_runtime(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    subject = CompanionSubjectContext(
        owner_user_id="owner-contract",
        pet_id="pet-contract",
        memory_subject_id="subject-contract",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        companion_mind=CompanionMind(
            store=CompanionStore(database_path),
            token_secret=b"runtime-contract",
        ),
        llm_adapter_factory=lambda llm: RecordingAdapter(
            [], "我先接住你。现在只做一件最小的事。"
        ),
    )

    runtime.handle_turn(
        user_id="device-contract",
        user_text=(
            "以后我低落时请少说一点，不要连续追问："
            "先用一句话接住我，然后只给一个最小行动。"
        ),
        history=[],
        llm=object(),
        session_id="session-contract",
        turn_id="turn-contract",
        companion_subject_context=subject,
    )

    with CompanionStore(database_path).connection() as connection:
        contracts = tuple(
            connection.execute(
                "SELECT dimension, scope FROM companion_interaction_contracts "
                "WHERE status = 'active' ORDER BY dimension"
            )
        )
    restarted = CompanionMind(
        store=CompanionStore(database_path),
        token_secret=b"runtime-contract-restarted",
    )
    low_mood = restarted.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-restart-low",
            subject=subject,
            request_digest="digest-after-restart-low",
            surface="voice",
            occurred_at="2026-07-28T20:00:00+08:00",
            context="user_low_mood",
        )
    )
    ordinary = restarted.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-restart-ordinary",
            subject=subject,
            request_digest="digest-after-restart-ordinary",
            surface="voice",
            occurred_at="2026-07-28T20:01:00+08:00",
        )
    )

    assert [(row["dimension"], row["scope"]) for row in contracts] == [
        ("question_frequency", "user_low_mood"),
        ("response_length", "user_low_mood"),
    ]
    assert low_mood.policy.response_length == "short"
    assert low_mood.policy.question_budget == 0
    assert ordinary.policy.question_budget > 0


def test_v2_runtime_passes_current_query_and_origin_hint_to_companion_retrieval(
    tmp_path,
):
    events: list[str] = []
    mind = RecordingCompanionMind(events)
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: RecordingAdapter(
            events, "\u4f60\u6765\u81ea\u6b66\u6c49\u3002"
        ),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    runtime.handle_turn(
        user_id="device-1",
        user_text="\u6211\u6765\u81ea\u54ea\u91cc\uff1f",
        history=[],
        llm=object(),
        session_id="session-origin-recall",
        turn_id="turn-origin-recall",
        companion_subject_context=subject,
    )

    request = mind.prepare_calls[0]
    assert request.interaction_kind == "explicit_recall"
    assert request.retrieval_query == "\u6211\u6765\u81ea\u54ea\u91cc\uff1f"
    assert request.retrieval_hints == {"fact_keys": ("origin",)}


def test_task_start_scenarios_recall_the_general_strategy_before_generation(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(events, uses_semantic_user_facts=True)
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: RecordingAdapter(events, "先找一个轻量起点。"),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-task-start",
        pet_id="pet-task-start",
        memory_subject_id="subject-task-start",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )

    runtime.handle_turn(
        user_id="device-task-start",
        user_text="按你现在对我的了解，我碰到陌生复杂任务时通常怎么开始？",
        history=[],
        llm=object(),
        session_id="session-task-start-recall",
        turn_id="turn-task-start-recall",
        companion_subject_context=subject,
    )
    runtime.handle_turn(
        user_id="device-task-start",
        user_text="陌生传感器资料很多，你按适合我的方式陪我开头。",
        history=[],
        llm=object(),
        session_id="session-task-start-apply",
        turn_id="turn-task-start-apply",
        companion_subject_context=subject,
    )

    assert mind.prepare_calls[0].interaction_kind == "explicit_recall"
    assert mind.prepare_calls[1].interaction_kind == "conversation"
    assert len(mind.recall_calls) == 2
    assert all(
        call["fact_keys"] == ("preference:task_start_strategy",)
        and call["kinds"] == ("preference",)
        for call in mind.recall_calls
    )


def test_task_start_strategy_recall_uses_the_previous_user_turn(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(events, uses_semantic_user_facts=True)
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: RecordingAdapter(events, "先找一个轻量起点。"),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-task-context",
        pet_id="pet-task-context",
        memory_subject_id="subject-task-context",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )
    current_text = "你按你了解我的方式，陪我把第一步开始起来吧。"

    runtime.handle_turn(
        user_id="device-task-context",
        user_text=current_text,
        history=[
            {
                "role": "user",
                "content": "今晚要接一个陌生传感器，资料很多，脑子有点乱。",
            },
            {"role": "assistant", "content": "我们慢慢来。"},
            {"role": "user", "content": current_text},
        ],
        llm=object(),
        session_id="session-task-context",
        turn_id="turn-task-context",
        companion_subject_context=subject,
    )

    assert len(mind.recall_calls) == 1
    assert mind.recall_calls[0]["fact_keys"] == (
        "preference:task_start_strategy",
    )
    assert "陌生传感器" in mind.recall_calls[0]["query"]
    assert current_text in mind.recall_calls[0]["query"]


def test_cross_session_continuation_prefers_durable_semantic_memory(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(events, uses_semantic_user_facts=True)
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: RecordingAdapter(events, "听起来进展不错。"),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-durable-continuity",
        pet_id="pet-durable-continuity",
        memory_subject_id="subject-durable-continuity",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )

    runtime.handle_turn(
        user_id="device-durable-continuity",
        user_text="之前那个课程设计我这两天又往前推了一点。",
        history=[],
        llm=object(),
        session_id="session-durable-continuity",
        turn_id="turn-durable-continuity",
        companion_subject_context=subject,
    )

    assert len(mind.recall_calls) == 1
    assert mind.recall_calls[0]["kinds"] == ()
    assert mind.recall_calls[0]["fact_keys"] == ()


@pytest.mark.parametrize(
    ("user_text", "expected_kind", "expected_outcome"),
    (
        ("你刚才的回答很有帮助。", "accepted_help", "helpful"),
        ("你刚才问得太私人了。", "interaction_feedback", "too_personal"),
    ),
)
def test_v2_runtime_commits_explicit_voice_relationship_feedback(
    tmp_path,
    user_text,
    expected_kind,
    expected_outcome,
):
    events: list[str] = []
    mind = RecordingCompanionMind(events)
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: RecordingAdapter(
            events, "我会按你的反馈调整。"
        ),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    runtime.handle_turn(
        user_id="device-1",
        user_text=user_text,
        history=[],
        llm=object(),
        session_id="session-relationship-feedback",
        turn_id=f"turn-relationship-feedback-{expected_outcome}",
        companion_subject_context=subject,
    )

    signals = tuple(
        item
        for item in mind.commit_calls[0][1].feedback_signals
        if item["kind"] != "assistant_action"
    )
    assert len(signals) == 1
    assert signals[0]["kind"] == expected_kind
    assert signals[0]["content"] == {"outcome": expected_outcome}
    assert signals[0]["attribution"] == "explicit_user_feedback"


def test_v2_prompt_uses_structured_policy_and_safe_evidence_only(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(events)
    adapter = RecordingAdapter(events, "收到。")
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: adapter,
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    runtime.handle_turn(
        user_id="device-1",
        user_text="继续说吧。",
        history=[],
        llm=object(),
        session_id="session-1",
        turn_id="turn-runtime-v2-prompt",
        companion_subject_context=subject,
        trusted_student_profile={
            "college": "信息与电气工程学院",
            "major": "电子信息工程",
            "class_name": "2501",
            "grade": "大二",
            "student_no": "32500000",
        },
    )

    system_prompt = adapter.calls[0][0]["content"]
    assert "<companion_policy>" in system_prompt
    assert '"relationship_stage": "first_meeting"' in system_prompt
    assert "用户明确偏好简短回答。" in system_prompt
    assert '<student_profile source="miniprogram">' in system_prompt
    assert '"学院": "信息与电气工程学院"' in system_prompt
    assert '"专业": "电子信息工程"' in system_prompt
    assert '"班级": "2501"' in system_prompt
    assert '"年级": "大二"' in system_prompt
    assert "32500000" not in system_prompt
    assert "<relationship>" not in system_prompt
    assert "relationship_level" not in system_prompt
    assert "relationship_state" not in getsource(prompts)
    assert '"reason_codes"' not in system_prompt


def test_v2_prompt_projects_expression_style_without_birth_temperament_metadata(
    tmp_path,
):
    events: list[str] = []
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    store.ensure_subject(
        owner_user_id="owner-style-prompt",
        pet_id="pet-vector-1",
        started_at="2026-07-25T09:00:00+08:00",
    )
    mind = CompanionMind(
        store=store,
        token_secret=b"runtime-expression-style-secret",
    )
    adapter = RecordingAdapter(events, "收到。")
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: adapter,
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-style-prompt",
        pet_id="pet-vector-1",
        memory_subject_id="subject-style-prompt",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    runtime.handle_turn(
        user_id="device-style-prompt",
        user_text="继续说吧。",
        history=[],
        llm=object(),
        session_id="session-style-prompt",
        turn_id="turn-style-prompt",
        companion_subject_context=subject,
    )

    system_prompt = adapter.calls[0][0]["content"]
    assert '"expression_style": {' in system_prompt
    assert '"exploration_orientation": "balanced"' in system_prompt
    assert '"expression_energy": "natural"' in system_prompt
    assert '"thought_organization": "structured"' in system_prompt
    assert '"humor_level": "none"' in system_prompt
    assert '"initiative_bias": "reserved"' in system_prompt
    assert "<expression_guidance>" in system_prompt
    assert "先说结论，再用简洁顺序组织要点" in system_prompt
    assert "只完成用户当前需要，不额外开启新任务或新话题" in system_prompt
    for forbidden in (
        "pet_id",
        "generator_version",
        "generated_at",
        "source_kind",
        "playfulness",
        "companion_initiative",
        "BirthTemperament",
        "xiaoxin-temperament-v1",
    ):
        assert forbidden not in system_prompt


def test_expression_guidance_preserves_personality_difference_without_new_permissions(
):
    calm_policy = CompanionPolicy(
        xiaoxin_age=1,
        relationship_stage="first_meeting",
        response_length="short",
        question_budget=0,
        memory_reference_budget=0,
        initiative_level="disabled",
        emotional_posture="warm",
        closure_style="concise",
        expression_style=CompanionExpressionStyle(
            exploration_orientation="focused",
            expression_energy="calm",
            thought_organization="intuitive",
            humor_level="none",
            initiative_bias="reserved",
        ),
    )
    lively_policy = replace(
        calm_policy,
        expression_style=CompanionExpressionStyle(
            exploration_orientation="exploratory",
            expression_energy="lively",
            thought_organization="structured",
            humor_level="medium",
            initiative_bias="proactive",
        ),
    )

    calm_prompt = prompts.build_system_messages(
        prompts.PERSONA, "", "", {}, None, companion_policy=calm_policy
    )[0]["content"]
    lively_prompt = prompts.build_system_messages(
        prompts.PERSONA, "", "", {}, None, companion_policy=lively_policy
    )[0]["content"]

    assert calm_prompt != lively_prompt
    assert "以陈述句为主" in calm_prompt
    assert "句子节奏可以更轻快" in lively_prompt
    assert "机械对称模板" in calm_prompt
    assert "先说结论" in lively_prompt
    assert "问题预算是上限，不是必须用完" in calm_prompt
    assert "只有缺少继续回答所必需的信息时才提问" in calm_prompt
    assert "固定口头禅、统一开场" in lively_prompt
    assert "我把这句话在小脑袋里转一圈" not in lively_prompt
    assert "不得增加问题预算" in lively_prompt
    assert '"question_budget": 0' in lively_prompt
    assert '"initiative_level": "disabled"' in lively_prompt


def test_expression_guidance_lets_low_mood_override_lively_temperament():
    policy = CompanionPolicy(
        xiaoxin_age=1,
        relationship_stage="familiar",
        response_length="short",
        question_budget=0,
        memory_reference_budget=0,
        initiative_level="disabled",
        emotional_posture="supportive",
        closure_style="concise",
        expression_style=CompanionExpressionStyle(
            exploration_orientation="exploratory",
            expression_energy="lively",
            thought_organization="balanced",
            humor_level="none",
            initiative_bias="timely",
        ),
        reason_codes=(
            "serious_context_humor_suppression",
            "low_mood_support",
            "low_mood_question_stop",
        ),
    )

    prompt = prompts.build_system_messages(
        prompts.PERSONA, "", "", {}, None, companion_policy=policy
    )[0]["content"]

    assert "当前场景优先于固有气质" in prompt
    assert "收住活泼、探索和玩笑" in prompt
    assert "语气可以更有活力" not in prompt


@pytest.mark.parametrize(
    ("user_text", "expected_semantic_kinds"),
    (
        ("你还记得我做编程题时的习惯吗？", ("preference", "interest")),
        ("小芯，我复习电路时通常先做什么来着？", ()),
        (
            "我今晚想推进机器人竞赛，但有点不知道从哪开始。"
            "按照你记得的我的做事习惯，给我一个具体的三步安排，别只复述记忆。",
            ("preference", "interest", "goal"),
        ),
        (
            "我今晚要练校园乐队键盘。按照你记得的我的做事习惯，"
            "给我一个可以边练边调整的具体方法，别只复述记忆。",
            ("preference", "interest"),
        ),
    ),
)
def test_natural_recall_is_classified_as_explicit_recall(
    tmp_path, user_text, expected_semantic_kinds
):
    events: list[str] = []
    mind = RecordingCompanionMind(events)
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: RecordingAdapter(events, "记得。"),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )

    runtime.handle_turn(
        user_id="device-1",
        user_text=user_text,
        history=[],
        llm=object(),
        session_id="session-natural-recall",
        turn_id="turn-natural-recall",
        companion_subject_context=subject,
    )

    assert mind.prepare_calls[0].interaction_kind == "explicit_recall"
    if expected_semantic_kinds:
        assert XiaoxinRuntime._companion_retrieval_hints(
            user_text,
            interaction_kind="explicit_recall",
            semantic_user_facts=True,
        ) == {"kinds": expected_semantic_kinds}


@pytest.mark.parametrize(
    ("initial_text", "followup_text", "expected_memory_fragments"),
    (
        (
            "今天电路分析课的小测我没发挥好，看到分数那一下挺挫败的。"
            "我打算晚上把错题重新做一遍，先不逃避。",
            "小芯，我刚从图书馆回来，那几道错题我已经重新做完了。",
            ("电路分析课的小测我没发挥好", "打算晚上把错题重新做一遍"),
        ),
        (
            "今天数字电路实验时我把面包板上的线接反了，排查半天才发现，"
            "那一下挺挫败的。我准备把线重新接好，再测一遍，不想就这么算了。",
            "小芯，我刚从实验室出来，刚才说的那块面包板我已经重新接好了，"
            "第二次测试也通过了。",
            ("面包板上的线接反了", "不想就这么算了"),
        ),
    ),
)
def test_runtime_recalls_recent_context_for_cross_session_followup(
    tmp_path, initial_text, followup_text, expected_memory_fragments
):
    database_path = tmp_path / "cross-session-continuity.db"
    subject = CompanionSubjectContext(
        owner_user_id="owner-continuity",
        pet_id="pet-continuity",
        memory_subject_id="subject-continuity",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )
    first_adapter = RecordingAdapter(
        [], "这一下确实挺挫败的。晚上愿意回去重做，已经是在认真接住自己了。"
    )
    first_runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        companion_mind=CompanionMind(
            store=CompanionStore(database_path),
            token_secret=b"continuity-first",
            memory_interpreter=MemoryInterpreter(EmptySemanticModel()),
            memory_interpreter_mode="candidate",
        ),
        llm_adapter_factory=lambda llm: first_adapter,
    )

    first_runtime.handle_turn(
        user_id="device-continuity",
        user_text=initial_text,
        history=[],
        llm=object(),
        session_id="session-before-restart",
        turn_id="turn-before-restart",
        companion_subject_context=subject,
    )

    second_adapter = RecordingAdapter(
        [], "你把那几道错题重新做完了，这次没有躲开那份挫败。先歇一会儿吧。"
    )
    second_runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        companion_mind=CompanionMind(
            store=CompanionStore(database_path),
            token_secret=b"continuity-after-restart",
            memory_interpreter=MemoryInterpreter(EmptySemanticModel()),
            memory_interpreter_mode="candidate",
        ),
        llm_adapter_factory=lambda llm: second_adapter,
    )

    result = second_runtime.handle_turn(
        user_id="device-continuity",
        user_text=followup_text,
        history=[],
        llm=object(),
        session_id="session-after-restart",
        turn_id="turn-after-restart",
        companion_subject_context=subject,
    )

    system_prompt = second_adapter.calls[0][0]["content"]
    for fragment in expected_memory_fragments:
        assert fragment in system_prompt
    assert "用户正在用指代明确续接先前对话" in system_prompt
    assert "先自然点明 <memory> 中" in system_prompt
    assert result.memory_result["commit_status"] == "committed"
    with sqlite3.connect(database_path) as connection:
        audit = connection.execute(
            """
            SELECT selected_evidence_ids_json
            FROM companion_retrieval_audits
            WHERE turn_id = ?
            """,
            ("turn-after-restart",),
        ).fetchone()
    assert audit is not None
    assert audit[0] != "[]"


@pytest.mark.parametrize(
    "user_text",
    (
        "戴维南定理是什么来着？",
        "电路分析时通常先做什么？",
    ),
)
def test_general_knowledge_questions_stay_general_qa(user_text):
    assert (
        XiaoxinRuntime._companion_interaction_kind(
            {"intent": "knowledge_qa", "reply_mode": "knowledge_grounded"},
            user_text,
        )
        == "general_qa"
    )


def test_multi_fact_recall_hints_cover_profile_activity_and_preference():
    assert XiaoxinRuntime._companion_interaction_kind(
        {"intent": "unknown", "reply_mode": "free_chat"},
        "我最近在做的事情和做事习惯分别是什么？",
    ) == "explicit_recall"

    hints = XiaoxinRuntime._companion_retrieval_hints(
        "我的测试代号、最近在做的事情和做事习惯是什么？",
        interaction_kind="explicit_recall",
        semantic_user_facts=True,
    )

    assert hints == {
        "kinds": ("profile", "preference", "interest", "goal")
    }
    assert XiaoxinRuntime._minimum_explicit_recall_budget(hints) == 3

    activity_and_habit_hints = XiaoxinRuntime._companion_retrieval_hints(
        "我最近在做的事情和做事习惯分别是什么？",
        interaction_kind="explicit_recall",
        semantic_user_facts=True,
    )
    assert activity_and_habit_hints == {
        "kinds": ("preference", "interest", "goal")
    }
    assert (
        XiaoxinRuntime._minimum_explicit_recall_budget(activity_and_habit_hints) == 2
    )

    production_failure_text = (
        "我准备继续做手头的事情。你按对我的了解，提醒我最近在推进什么，"
        "以及我通常喜欢怎么开始；不确定就直说，别猜。"
    )
    assert (
        XiaoxinRuntime._companion_interaction_kind(
            {"intent": "unknown", "reply_mode": "free_chat"},
            production_failure_text,
        )
        == "explicit_recall"
    )
    production_failure_hints = XiaoxinRuntime._companion_retrieval_hints(
        production_failure_text,
        interaction_kind="explicit_recall",
        semantic_user_facts=True,
    )
    assert production_failure_hints == {"kinds": ("preference", "interest", "goal")}
    assert XiaoxinRuntime._minimum_explicit_recall_budget(production_failure_hints) == 2

    primary_focus_text = (
        "现在缓过来一些了。结合我这学期的主线，给我一个很小的下一步。"
    )
    assert XiaoxinRuntime._companion_interaction_kind(
        {"intent": "unknown", "reply_mode": "free_chat"},
        primary_focus_text,
    ) == "explicit_recall"
    assert XiaoxinRuntime._companion_retrieval_hints(
        primary_focus_text,
        interaction_kind="explicit_recall",
        semantic_user_facts=True,
    ) == {"fact_keys": ("goal:current_primary_focus",)}


def test_low_mood_context_catches_natural_brain_messy_phrase():
    assert (
        companion_context(
            "先别急着给一堆办法，我脑子还是乱的。你陪我把现在的感受理清一点。"
        )
        == "user_low_mood"
    )


def test_runtime_rejects_unsupported_claimed_memory_without_calling_llm(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(
        events,
        prompt_context=(
            '{"fact":"正在准备数字钢琴考级","kind":"goal"}',
            '{"fact":"喜欢边尝试边调整","kind":"preference"}',
        ),
        uses_semantic_user_facts=True,
    )
    adapter = RecordingAdapter(events, "按这个情况给你安排。")
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "unsupported-memory-premise.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: adapter,
    )

    for index, user_text in enumerate(
        (
            "你之前说我在准备嵌入式课程设计、喜欢先列计划再行动。"
            "照这个情况给我排练习计划。",
            "你记得我在准备嵌入式课程设计、喜欢先列计划吧。"
            "照这个情况给我排练习计划。",
            "你记得我在准备嵌入式课程设计、通常喜欢怎么开始吗？",
        )
    ):
        result = runtime.handle_turn(
            user_id="device-b",
            user_text=user_text,
            history=[],
            llm=object(),
            session_id="session-b",
            turn_id=f"turn-b-unsupported-premise-{index}",
            companion_subject_context=CompanionSubjectContext(
                owner_user_id="owner-b",
                pet_id="pet-b",
                memory_subject_id="subject-b",
                speaker_identity="confirmed",
                academic_stage="sophomore",
                persistence_allowed=True,
            ),
        )

        assert result.reply == (
            "我这里没有可靠记录能确认这个前提，不能把它当成既有记忆来安排。"
        )

    question_result = runtime.handle_turn(
        user_id="device-b",
        user_text="你记得我是否在准备考研吗？",
        history=[],
        llm=object(),
        session_id="session-b",
        turn_id="turn-b-memory-question",
        companion_subject_context=CompanionSubjectContext(
            owner_user_id="owner-b",
            pet_id="pet-b",
            memory_subject_id="subject-b",
            speaker_identity="confirmed",
            academic_stage="sophomore",
            persistence_allowed=True,
        ),
    )

    assert question_result.reply == "按这个情况给你安排。"
    assert len(adapter.calls) == 1
    assert events == [
        "prepare",
        "commit",
        "prepare",
        "commit",
        "prepare",
        "commit",
        "prepare",
        "llm",
        "commit",
    ]


def test_v2_runtime_does_not_commit_when_llm_generation_fails(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(events)
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: RaisingAdapter(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    with pytest.raises(RuntimeError, match="llm offline"):
        runtime.handle_turn(
            user_id="device-1",
            user_text="今天怎么样？",
            history=[],
            llm=object(),
            session_id="session-1",
            turn_id="turn-runtime-v2-llm-failure",
            companion_subject_context=subject,
        )

    assert events == ["prepare"]
    assert mind.commit_calls == []


def test_v2_runtime_returns_reply_and_reports_memory_commit_failure(tmp_path):
    events: list[str] = []
    mind = FailingCommitCompanionMind(events)
    adapter = RecordingAdapter(events, "先把今天照顾好就行。")
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: adapter,
    )

    result = runtime.handle_turn(
        user_id="device-1",
        user_text="我今天有点累。",
        history=[],
        llm=object(),
        session_id="session-1",
        turn_id="turn-runtime-v2-commit-failure",
        companion_subject_context=CompanionSubjectContext(
            owner_user_id="owner-1",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            speaker_identity="confirmed",
            academic_stage="sophomore",
            persistence_allowed=True,
        ),
    )

    assert result.reply == "先把今天照顾好就行。"
    assert result.memory_result["memory_action"] == "memory_commit_failed"
    assert result.memory_result["commit_status"] == "failed"
    assert "记住" not in result.reply


def test_v2_local_rule_retry_is_idempotent_and_does_not_write_legacy_memory(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    legacy_memory_dir = tmp_path / "legacy-memory"
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: RaisingAdapter(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    kwargs = {
        "user_id": "device-1",
        "user_text": "帮我联系老师要电话",
        "history": [],
        "llm": object(),
        "session_id": "session-1",
        "turn_id": "turn-runtime-v2-local-idempotent",
        "companion_subject_context": subject,
    }

    first = runtime.handle_turn(**kwargs)
    second = runtime.handle_turn(**kwargs)

    assert first.model == second.model == "local-rule"
    assert first.memory_result["commit_status"] == "committed"
    assert second.memory_result["commit_status"] == "already_committed"
    with sqlite3.connect(database_path) as connection:
        turn_count = connection.execute(
            "SELECT COUNT(*) FROM companion_turns WHERE turn_id = ?",
            (kwargs["turn_id"],),
        ).fetchone()[0]
    assert turn_count == 1
    assert not legacy_memory_dir.exists()


def test_v2_llm_reply_retry_uses_the_same_idempotent_turn_id(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    adapter = RecordingAdapter([], "我在，先说最压着你的那一点。")
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: adapter,
    )
    kwargs = {
        "user_id": "device-1",
        "user_text": "最近压力有点大。",
        "history": [],
        "llm": object(),
        "session_id": "session-1",
        "turn_id": "turn-runtime-v2-llm-idempotent",
        "companion_subject_context": CompanionSubjectContext(
            owner_user_id="owner-1",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            speaker_identity="confirmed",
            academic_stage="sophomore",
            persistence_allowed=True,
        ),
    }

    first = runtime.handle_turn(**kwargs)
    second = runtime.handle_turn(**kwargs)

    assert first.memory_result["commit_status"] == "committed"
    assert second.memory_result["commit_status"] == "already_committed"
    with sqlite3.connect(database_path) as connection:
        turn_count = connection.execute(
            "SELECT COUNT(*) FROM companion_turns WHERE turn_id = ?",
            (kwargs["turn_id"],),
        ).fetchone()[0]
    assert turn_count == 1


def test_v2_runtime_without_resolved_subject_performs_zero_private_writes(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    adapter = RecordingAdapter([], "你好，我可以先不使用私人记忆陪你聊。")
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: adapter,
    )

    result = runtime.handle_turn(
        user_id="device-unknown",
        user_text="你好。",
        history=[],
        llm=object(),
        session_id="session-unknown",
        turn_id="turn-runtime-v2-unknown",
    )

    assert result.memory_result["commit_status"] == "not_persisted"
    system_prompt = adapter.calls[0][0]["content"]
    assert '"initiative_level": "disabled"' in system_prompt
    assert '"memory_reference_budget": 0' in system_prompt
    with sqlite3.connect(database_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("companion_pets", "companion_turns", "companion_evidence")
        }
    assert counts == {
        "companion_pets": 0,
        "companion_turns": 0,
        "companion_evidence": 0,
    }


def test_v2_runtime_does_not_take_over_existing_tool_routes(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(events)
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: RaisingAdapter(),
    )

    result = runtime.handle_turn(
        user_id="device-1",
        user_text="拜拜",
        history=[],
        llm=object(),
        session_id="session-1",
        turn_id="turn-runtime-v2-existing-tool",
    )

    assert result.handled is False
    assert result.bypass_reason == "existing_tool"
    assert events == []


def test_v2_relationship_reset_requires_miniprogram_handoff_without_llm(tmp_path):
    events: list[str] = []
    mind = ControlCompanionMind(events)
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: RaisingAdapter(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    result = runtime.handle_turn(
        user_id="device-1",
        user_text="重置关系",
        history=[],
        llm=object(),
        session_id="session-1",
        turn_id="turn-runtime-v2-reset",
        companion_subject_context=subject,
    )

    assert events == []
    assert mind.control_calls == []
    assert result.model == "local-rule"
    assert "小程序" in result.reply
    assert result.memory_result["memory_action"] == "reset_relationship"
    assert result.memory_result["commit_status"] == "handoff_required"
    assert result.memory_result["executable"] is False
    assert result.relationship is None


def test_v2_commit_does_not_reanalyze_raw_user_text(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(events)
    adapter = RecordingAdapter(events, "收到。")
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: adapter,
    )

    result = runtime.handle_turn(
        user_id="device-1",
        user_text="今天还行。",
        history=[],
        llm=object(),
        session_id="session-1",
        turn_id="turn-runtime-v2-no-reanalysis",
        companion_subject_context=CompanionSubjectContext(
            owner_user_id="owner-1",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            speaker_identity="confirmed",
            academic_stage="sophomore",
            persistence_allowed=True,
        ),
    )

    assert result.memory_result["commit_status"] == "committed"
    assert events == ["prepare", "llm", "commit"]


def test_real_v2_runtime_queues_background_work_for_successful_reply(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        companion_mind=CompanionMind(
            store=store,
            token_secret=b"runtime-real-worker-test",
        ),
        llm_adapter_factory=lambda llm: RecordingAdapter([], "我在。"),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    result = runtime.handle_turn(
        user_id="device-1",
        user_text="今天还行。",
        history=[],
        llm=object(),
        session_id="session-1",
        turn_id="turn-runtime-real-worker",
        companion_subject_context=subject,
    )

    assert result.memory_result["commit_status"] == "committed"
    assert len(result.memory_result["evidence_ids"]) == 1
    assert len(result.memory_result["job_ids"]) == 1
    with store.connection() as connection:
        evidence = connection.execute(
            """
            SELECT kind, content_json
            FROM companion_evidence
            WHERE kind = 'assistant_action'
            """
        ).fetchone()
        job = connection.execute(
            "SELECT status, job_kind FROM consolidation_jobs"
        ).fetchone()
    assert evidence["kind"] == "assistant_action"
    assert "今天还行" not in evidence["content_json"]
    assert tuple(job) == ("pending", "session_consolidation")


def test_real_v2_runtime_keeps_explicit_preferred_name_across_restart_and_reset(
    tmp_path,
):
    database_path = tmp_path / "xiaoxin_companion.db"
    knowledge_dir = Path(__file__).resolve().parents[2] / "data" / "xiaoxin_knowledge"
    subject = CompanionSubjectContext(
        owner_user_id="owner-name",
        pet_id="pet-name",
        memory_subject_id="subject-name",
        speaker_identity="confirmed",
        academic_stage="unknown",
        persistence_allowed=True,
    )

    first_adapter = RecordingAdapter([], "很高兴认识你，小林。")
    first_runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: first_adapter,
    )
    introduced = first_runtime.handle_turn(
        user_id="device-name",
        user_text="我叫小林，我是大三。",
        history=[],
        llm=object(),
        session_id="session-name-1",
        turn_id="turn-name-introduction",
        companion_subject_context=subject,
    )

    assert introduced.relationship["xiaoxin_age"] is None
    assert len(introduced.memory_result["evidence_ids"]) == 2

    recall_adapter = RecordingAdapter([], "你希望我叫你小林。")
    restarted = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: recall_adapter,
    )
    restarted.handle_turn(
        user_id="device-name",
        user_text="我叫什么？",
        history=[],
        llm=object(),
        session_id="session-name-2",
        turn_id="turn-name-recall-before-reset",
        companion_subject_context=subject,
    )
    before_reset_prompt = recall_adapter.calls[0][0]["content"]

    handoff = restarted.handle_turn(
        user_id="device-name",
        user_text="重置关系",
        history=[],
        llm=object(),
        session_id="session-name-2",
        turn_id="turn-name-reset",
        companion_subject_context=subject,
    )
    assert handoff.memory_result["commit_status"] == "handoff_required"
    reset = restarted.companion_mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=subject,
            payload={
                "now": restarted._current_shanghai_time().isoformat(),
                "idempotency_key": "runtime-test-name-reset",
            },
        )
    )

    after_reset_adapter = RecordingAdapter([], "我仍然会叫你小林。")
    after_reset = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: after_reset_adapter,
    )
    recalled = after_reset.handle_turn(
        user_id="device-name",
        user_text="还记得怎么称呼我吗？",
        history=[],
        llm=object(),
        session_id="session-name-3",
        turn_id="turn-name-recall-after-reset",
        companion_subject_context=subject,
    )
    after_reset_prompt = after_reset_adapter.calls[0][0]["content"]

    assert "用户明确希望被称作小林。" in before_reset_prompt
    assert reset.retained >= 1
    assert "用户明确希望被称作小林。" in after_reset_prompt
    assert recalled.relationship == {
        "relationship_stage": "first_meeting",
        "xiaoxin_age": None,
    }


def test_real_v2_runtime_preferred_name_redeclaration_supersedes_old_name(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    knowledge_dir = Path(__file__).resolve().parents[2] / "data" / "xiaoxin_knowledge"
    subject = CompanionSubjectContext(
        owner_user_id="owner-name-correction",
        pet_id="pet-name-correction",
        memory_subject_id="subject-name-correction",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: RecordingAdapter([], "收到。"),
    )

    runtime.handle_turn(
        user_id="device-name-correction",
        user_text="我叫小王。",
        history=[],
        llm=object(),
        session_id="session-name-correction-1",
        turn_id="turn-name-old",
        companion_subject_context=subject,
    )
    runtime.handle_turn(
        user_id="device-name-correction",
        user_text="以后叫我小林。",
        history=[],
        llm=object(),
        session_id="session-name-correction-2",
        turn_id="turn-name-new",
        companion_subject_context=subject,
    )

    recall_adapter = RecordingAdapter([], "我会叫你小林。")
    restarted = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: recall_adapter,
    )
    restarted.handle_turn(
        user_id="device-name-correction",
        user_text="你应该怎么称呼我？",
        history=[],
        llm=object(),
        session_id="session-name-correction-3",
        turn_id="turn-name-correction-recall",
        companion_subject_context=subject,
    )

    prompt = recall_adapter.calls[0][0]["content"]
    assert "用户明确希望被称作小林。" in prompt
    assert "用户明确希望被称作小王。" not in prompt


def test_real_v2_runtime_preferred_name_withdrawal_stops_old_name_recall(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    knowledge_dir = Path(__file__).resolve().parents[2] / "data" / "xiaoxin_knowledge"
    subject = CompanionSubjectContext(
        owner_user_id="owner-name-withdrawal",
        pet_id="pet-name-withdrawal",
        memory_subject_id="subject-name-withdrawal",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: RecordingAdapter([], "收到。"),
    )
    runtime.handle_turn(
        user_id="device-name-withdrawal",
        user_text="我叫小王。",
        history=[],
        llm=object(),
        session_id="session-name-withdrawal-1",
        turn_id="turn-name-withdrawal-old",
        companion_subject_context=subject,
    )

    withdrawn = runtime.handle_turn(
        user_id="device-name-withdrawal",
        user_text="别再这样称呼我。",
        history=[],
        llm=object(),
        session_id="session-name-withdrawal-2",
        turn_id="turn-name-withdrawal",
        companion_subject_context=subject,
    )

    assert len(withdrawn.memory_result["evidence_ids"]) == 2

    recall_adapter = RecordingAdapter([], "我不会再用之前的称呼。")
    restarted = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: recall_adapter,
    )
    restarted.handle_turn(
        user_id="device-name-withdrawal",
        user_text="你现在应该怎么称呼我？",
        history=[],
        llm=object(),
        session_id="session-name-withdrawal-3",
        turn_id="turn-name-withdrawal-recall",
        companion_subject_context=subject,
    )

    prompt = recall_adapter.calls[0][0]["content"]
    assert "用户明确要求不再使用之前的称呼。" in prompt
    assert "用户明确希望被称作小王。" not in prompt


def test_real_v2_runtime_keeps_explicit_user_growth_fact_across_restart_and_reset(
    tmp_path,
):
    database_path = tmp_path / "xiaoxin_companion.db"
    knowledge_dir = Path(__file__).resolve().parents[2] / "data" / "xiaoxin_knowledge"
    subject = CompanionSubjectContext(
        owner_user_id="owner-growth-fact",
        pet_id="pet-growth-fact",
        memory_subject_id="subject-growth-fact",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: RecordingAdapter([], "这件事值得记住。"),
    )

    committed = runtime.handle_turn(
        user_id="device-growth-fact",
        user_text="我终于完成了自己定下的目标。",
        history=[],
        llm=object(),
        session_id="session-growth-fact-1",
        turn_id="turn-growth-fact",
        companion_subject_context=subject,
    )

    assert len(committed.memory_result["evidence_ids"]) == 2

    before_reset_adapter = RecordingAdapter([], "你完成了自己定下的目标。")
    restarted = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: before_reset_adapter,
    )
    restarted.handle_turn(
        user_id="device-growth-fact",
        user_text="我最近完成了什么？",
        history=[],
        llm=object(),
        session_id="session-growth-fact-2",
        turn_id="turn-growth-fact-recall-before-reset",
        companion_subject_context=subject,
    )
    before_reset_prompt = before_reset_adapter.calls[0][0]["content"]

    handoff = restarted.handle_turn(
        user_id="device-growth-fact",
        user_text="重置关系",
        history=[],
        llm=object(),
        session_id="session-growth-fact-2",
        turn_id="turn-growth-fact-reset",
        companion_subject_context=subject,
    )
    assert handoff.memory_result["commit_status"] == "handoff_required"
    restarted.companion_mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=subject,
            payload={
                "now": restarted._current_shanghai_time().isoformat(),
                "idempotency_key": "runtime-test-growth-reset",
            },
        )
    )

    after_reset_adapter = RecordingAdapter([], "你完成了自己定下的目标。")
    after_reset = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: after_reset_adapter,
    )
    after_reset.handle_turn(
        user_id="device-growth-fact",
        user_text="关系重置后还记得我的成长吗？",
        history=[],
        llm=object(),
        session_id="session-growth-fact-3",
        turn_id="turn-growth-fact-recall-after-reset",
        companion_subject_context=subject,
    )
    after_reset_prompt = after_reset_adapter.calls[0][0]["content"]

    expected = "用户明确表示自己终于完成了自己定下的目标。"
    assert expected in before_reset_prompt
    assert expected in after_reset_prompt


def test_real_v2_runtime_persists_explicit_origin_and_preference_as_user_evidence(
    tmp_path,
):
    database_path = tmp_path / "xiaoxin_companion.db"
    knowledge_dir = Path(__file__).resolve().parents[2] / "data" / "xiaoxin_knowledge"
    subject = CompanionSubjectContext(
        owner_user_id="owner-explicit-facts",
        pet_id="pet-explicit-facts",
        memory_subject_id="subject-explicit-facts",
        speaker_identity="confirmed",
        academic_stage="unknown",
        persistence_allowed=True,
    )
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: RecordingAdapter([], "我记下了。"),
    )

    committed = runtime.handle_turn(
        user_id="device-explicit-facts",
        user_text="我来自武汉，平时喜欢简短回答。",
        history=[],
        llm=object(),
        session_id="session-explicit-facts-1",
        turn_id="turn-explicit-facts",
        companion_subject_context=subject,
    )

    assert committed.relationship["xiaoxin_age"] is None
    assert len(committed.memory_result["evidence_ids"]) == 3

    recall_adapter = RecordingAdapter([], "你来自武汉，也喜欢简短回答。")
    restarted = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: recall_adapter,
    )
    restarted.handle_turn(
        user_id="device-explicit-facts",
        user_text="你记得我明确说过什么吗？",
        history=[],
        llm=object(),
        session_id="session-explicit-facts-2",
        turn_id="turn-explicit-facts-recall",
        companion_subject_context=subject,
    )

    prompt = recall_adapter.calls[0][0]["content"]
    assert "用户明确表示平时喜欢简短回答。" in prompt

    handoff = restarted.handle_turn(
        user_id="device-explicit-facts",
        user_text="重置关系",
        history=[],
        llm=object(),
        session_id="session-explicit-facts-2",
        turn_id="turn-explicit-facts-reset",
        companion_subject_context=subject,
    )
    assert handoff.memory_result["commit_status"] == "handoff_required"
    restarted.companion_mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=subject,
            payload={
                "now": restarted._current_shanghai_time().isoformat(),
                "idempotency_key": "runtime-test-explicit-facts-reset",
            },
        )
    )
    projection = restarted.companion_mind.project(
        CompanionProjectionRequest(
            subject=subject,
            surface="operator",
            now=restarted._current_shanghai_time().isoformat(),
        )
    )
    summaries = {item["source_summary"] for item in projection.payload["evidence"]}
    assert summaries >= {
        "用户明确表示自己来自武汉。",
        "用户明确表示平时喜欢简短回答。",
    }


def test_real_v2_runtime_does_not_persist_non_factual_first_person_claims(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    knowledge_dir = Path(__file__).resolve().parents[2] / "data" / "xiaoxin_knowledge"
    subject = CompanionSubjectContext(
        owner_user_id="owner-reported-facts",
        pet_id="pet-reported-facts",
        memory_subject_id="subject-reported-facts",
        speaker_identity="confirmed",
        academic_stage="unknown",
        persistence_allowed=True,
    )
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: RecordingAdapter(
            [], "我不会把转述当成你的事实。"
        ),
    )

    direct = runtime.handle_turn(
        user_id="device-reported-facts",
        user_text="我叫小林。",
        history=[],
        llm=object(),
        session_id="session-reported-facts-1",
        turn_id="turn-reported-facts-direct",
        companion_subject_context=subject,
    )
    reported = runtime.handle_turn(
        user_id="device-reported-facts",
        user_text=(
            "老师称呼我为小王，朋友聊到、我来自上海，"
            "朋友聊到，别叫我小林，朋友聊到\n平时喜欢简短回答，"
            "朋友说我完成了项目，但我喜欢详细回答，"
            "老师表示我来自武汉，但我叫小王，"
            "朋友聊到我来自武汉，但我叫小王，"
            "朋友聊到我完成了项目，但我喜欢详细回答，"
            "朋友聊到我来自武汉，但我完成了项目，"
            "朋友聊到，别叫我小王，以后叫我小王，"
            "朋友跟我说过我完成了项目。"
        ),
        history=[],
        llm=object(),
        session_id="session-reported-facts-2",
        turn_id="turn-reported-facts-third-party",
        companion_subject_context=subject,
    )
    imagined = runtime.handle_turn(
        user_id="device-reported-facts",
        user_text=(
            "我梦到我终于完成了项目，我以为我通过了考试，"
            "我希望未来我解决了这个问题，我梦到别叫我小林，"
            "我梦到别叫我小王，以后叫我小王。"
        ),
        history=[],
        llm=object(),
        session_id="session-reported-facts-2",
        turn_id="turn-reported-facts-non-factual",
        companion_subject_context=subject,
    )

    assert len(direct.memory_result["evidence_ids"]) == 2
    assert len(reported.memory_result["evidence_ids"]) == 1
    assert len(imagined.memory_result["evidence_ids"]) == 1

    recall_adapter = RecordingAdapter([], "你明确希望我叫你小林。")
    restarted = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=knowledge_dir,
            companion_db_path=database_path,
        ),
        llm_adapter_factory=lambda llm: recall_adapter,
    )
    restarted.handle_turn(
        user_id="device-reported-facts",
        user_text="你记得我明确说过什么吗？",
        history=[],
        llm=object(),
        session_id="session-reported-facts-3",
        turn_id="turn-reported-facts-recall",
        companion_subject_context=subject,
    )

    prompt = recall_adapter.calls[0][0]["content"]
    assert "用户明确希望被称作小林。" in prompt
    assert "用户明确希望被称作小王。" not in prompt
    assert "用户明确表示自己来自上海。" not in prompt
    assert "用户明确表示自己终于完成了项目。" not in prompt
    assert "用户明确表示自己通过了考试。" not in prompt
    assert "用户明确表示自己解决了这个问题。" not in prompt


def test_v2_knowledge_route_requests_general_qa_zero_memory_policy(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(events)
    adapter = RecordingAdapter(events, "我这里只能按可靠校园资料回答。")
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "xiaoxin_companion.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: adapter,
    )

    runtime.handle_turn(
        user_id="device-1",
        user_text="北秀食堂在哪里？",
        history=[],
        llm=object(),
        session_id="session-1",
        turn_id="turn-runtime-v2-general-qa",
        companion_subject_context=CompanionSubjectContext(
            owner_user_id="owner-1",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            speaker_identity="confirmed",
            academic_stage="sophomore",
            persistence_allowed=True,
        ),
    )

    assert mind.prepare_calls[0].interaction_kind == "general_qa"
    system_prompt = adapter.calls[0][0]["content"]
    assert '"memory_reference_budget": 0' in system_prompt
    assert "用户明确偏好简短回答。" not in system_prompt


def test_runtime_native_memory_tool_completes_natural_recall_end_to_end(tmp_path):
    database_path = tmp_path / "semantic-runtime-recall.db"
    store = CompanionStore(database_path)
    subject = CompanionSubjectContext(
        owner_user_id="owner-semantic-runtime",
        pet_id="pet-semantic-runtime",
        memory_subject_id="subject-semantic-runtime",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    seed_mind = CompanionMind(store=store, token_secret=b"runtime-semantic-seed")
    seed = seed_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-runtime-semantic-seed",
            subject=subject,
            request_digest="digest-runtime-semantic-seed",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
        )
    )
    seed_mind.commit_turn(
        seed,
        CompanionTurnOutcome(
            visible_response="我记住了。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "goal",
                    "ownership_scope": "user",
                    "content": {
                        "fact_key": "goal:english_cet6",
                        "canonical_value": "六级考试让我紧张",
                    },
                    "source_summary": "用户明确表示六级考试会让自己紧张。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "persistent",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    semantic_mind = CompanionMind(
        store=store,
        token_secret=b"runtime-semantic-tool",
        memory_interpreter=MemoryInterpreter(EmptySemanticModel()),
        memory_interpreter_mode="candidate",
    )
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        companion_mind=semantic_mind,
    )
    llm = NativeMemoryToolLLM()

    result = runtime.handle_turn(
        user_id="device-semantic-runtime",
        user_text="上次让我紧张的那个考试是什么？",
        history=[],
        llm=llm,
        session_id="session-semantic-runtime",
        turn_id="turn-runtime-natural-recall",
        companion_subject_context=subject,
    )

    assert result.reply == "你之前提到，六级考试会让你紧张。"
    assert llm.function_calls == 1
    assert len(llm.final_calls) == 1
    assert "用户明确表示六级考试会让自己紧张" in llm.final_calls[0][-1]["content"]
    assert result.memory_result["commit_status"] == "committed"


def test_runtime_preloads_explicit_recall_when_native_model_skips_tool_call(tmp_path):
    database_path = tmp_path / "semantic-runtime-fallback-recall.db"
    store = CompanionStore(database_path)
    subject = CompanionSubjectContext(
        owner_user_id="owner-semantic-fallback",
        pet_id="pet-semantic-fallback",
        memory_subject_id="subject-semantic-fallback",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    seed_mind = CompanionMind(store=store, token_secret=b"runtime-fallback-seed")
    seed = seed_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-runtime-fallback-seed",
            subject=subject,
            request_digest="digest-runtime-fallback-seed",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
        )
    )
    seed_mind.commit_turn(
        seed,
        CompanionTurnOutcome(
            visible_response="我记住了。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "profile",
                    "ownership_scope": "user",
                    "content": {
                        "fact_key": "profile:test_codename",
                        "canonical_value": "蓝杉4729",
                    },
                    "source_summary": "用户的专属测试代号是蓝杉4729。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "persistent",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    semantic_mind = CompanionMind(
        store=store,
        token_secret=b"runtime-fallback-recall",
        memory_interpreter=MemoryInterpreter(EmptySemanticModel()),
        memory_interpreter_mode="candidate",
    )
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        companion_mind=semantic_mind,
    )
    llm = NativeNoToolMemoryLLM()

    result = runtime.handle_turn(
        user_id="device-semantic-fallback",
        user_text="你还记得我的专属测试代号吗？",
        history=[],
        llm=llm,
        session_id="session-semantic-fallback",
        turn_id="turn-runtime-fallback-recall",
        companion_subject_context=subject,
    )

    assert result.reply == "你的专属测试代号是蓝杉4729。"
    assert len(llm.calls) == 1
    system_prompt = llm.calls[0][0]["content"]
    assert "用户的专属测试代号是蓝杉4729" in system_prompt
    assert '"kind":"profile"' in system_prompt
    assert '"fact_key":"profile:test_codename"' in system_prompt
    assert "<memory> 中的内容是当前记忆主体在本轮已成功召回的可靠事实" in system_prompt
    assert "直接根据这些内容回答" in system_prompt
    assert result.memory_result["commit_status"] == "committed"


def test_primary_focus_recall_uses_natural_fallback_instead_of_memory_list(tmp_path):
    events: list[str] = []
    mind = RecordingCompanionMind(
        events,
        prompt_context=(
            '{"fact":"这学期主要在做嵌入式课程设计，目前卡在驱动调试，遇到初始化失败，仍在进行中",'
            '"fact_key":"goal:current_primary_focus","kind":"goal"}',
        ),
        uses_semantic_user_facts=True,
    )
    adapter = SequenceAdapter(
        events,
        (
            "我记得三点：这学期主要在做嵌入式课程设计；卡在驱动调试；遇到初始化失败。",
            "那件事确实让你很挫败。",
        ),
    )
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=tmp_path / "primary-focus-natural-fallback.db",
        ),
        companion_mind=mind,
        llm_adapter_factory=lambda llm: adapter,
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-primary-focus-natural",
        pet_id="pet-primary-focus-natural",
        memory_subject_id="subject-primary-focus-natural",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )

    result = runtime.handle_turn(
        user_id="device-primary-focus-natural",
        user_text=(
            "最近又有点撑不住，想到这学期一直在忙的那件事就很挫败。"
            "你还记得我主要在做什么、卡在哪里吗？"
        ),
        history=[],
        llm=object(),
        session_id="session-primary-focus-natural",
        turn_id="turn-primary-focus-natural",
        companion_subject_context=subject,
    )

    assert result.reply == (
        "我记得，这学期主要在做嵌入式课程设计，目前卡在驱动调试，"
        "遇到初始化失败，仍在进行中。"
    )
    assert "我记得三点" not in result.reply
    assert len(adapter.calls) == 2


@pytest.mark.parametrize(
    ("recover_on_retry", "denies_memory", "expected_reply"),
    (
        (
            True,
            True,
            "你现在主要在准备嵌入式课程设计，习惯先抓关键路径。",
        ),
        (
            False,
            True,
            "我记得，先抓关键路径；也记得准备嵌入式课程设计。",
        ),
        (
            False,
            False,
            "我记得，先抓关键路径；也记得准备嵌入式课程设计。",
        ),
    ),
)
def test_explicit_recall_never_discards_successfully_recalled_facts(
    tmp_path,
    recover_on_retry,
    denies_memory,
    expected_reply,
):
    database_path = tmp_path / "explicit-recall-denial.db"
    store = CompanionStore(database_path)
    subject = CompanionSubjectContext(
        owner_user_id="owner-explicit-recall-denial",
        pet_id="pet-explicit-recall-denial",
        memory_subject_id="subject-explicit-recall-denial",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )
    seed_mind = CompanionMind(store=store, token_secret=b"recall-denial-seed")
    seed = seed_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-denial-seed",
            subject=subject,
            request_digest="digest-recall-denial-seed",
            surface="voice",
            occurred_at="2026-08-31T19:00:00+08:00",
        )
    )
    store.commit_turn(
        seed,
        CompanionTurnOutcome(
            visible_response="我会认真记下。",
            assistant_action="reply",
            delivery_status="generated",
        ),
        evidence=(
            CompanionEvidence(
                evidence_id="evidence-recall-denial-goal",
                pet_id=subject.pet_id,
                memory_subject_id=subject.memory_subject_id,
                ownership_scope="user",
                relationship_epoch_id=None,
                kind="goal",
                content={"canonical_value": "准备嵌入式课程设计"},
                source_kind="test",
                source_ref="turn-recall-denial-seed",
                source_summary="准备嵌入式课程设计",
                attribution="explicit_user_statement",
                confidence=1.0,
                occurred_at="2026-08-31T19:00:00+08:00",
                retention="long_term",
                status="active",
                prompt_eligible=True,
                fact_key="goal:embedded_course_design",
                sensitivity="low",
            ),
            CompanionEvidence(
                evidence_id="evidence-recall-denial-preference",
                pet_id=subject.pet_id,
                memory_subject_id=subject.memory_subject_id,
                ownership_scope="user",
                relationship_epoch_id=None,
                kind="preference",
                content={"canonical_value": "先抓关键路径"},
                source_kind="test",
                source_ref="turn-recall-denial-seed",
                source_summary="先抓关键路径",
                attribution="explicit_user_statement",
                confidence=1.0,
                occurred_at="2026-08-31T19:00:01+08:00",
                retention="long_term",
                status="active",
                prompt_eligible=True,
                fact_key="preference:working_style",
                sensitivity="low",
            ),
        ),
    )
    runtime = XiaoxinRuntime(
        XiaoxinConfig(
            enabled=True,
            knowledge_dir=Path(__file__).resolve().parents[2]
            / "data"
            / "xiaoxin_knowledge",
            companion_db_path=database_path,
        ),
        time_provider=lambda: datetime.fromisoformat(
            "2026-10-30T19:00:00+08:00"
        ),
        companion_mind=CompanionMind(
            store=store,
            token_secret=b"recall-denial-runtime",
            memory_interpreter=MemoryInterpreter(EmptySemanticModel()),
            memory_interpreter_mode="candidate",
        ),
    )
    llm = NativeMemoryDenialLLM(
        recover_on_retry=recover_on_retry,
        denies_memory=denies_memory,
    )

    result = runtime.handle_turn(
        user_id="device-explicit-recall-denial",
        user_text=(
            "过了这么久，你记得我现在主要在准备什么、通常喜欢怎么开始吗？"
            "不确定就直说。"
        ),
        history=[],
        llm=llm,
        session_id="session-explicit-recall-denial",
        turn_id=f"turn-explicit-recall-denial-{recover_on_retry}",
        companion_subject_context=subject,
    )

    assert result.reply == expected_reply
    assert len(llm.calls) == 2


def test_turn_behavior_plan_prompt_is_active_only_and_remains_non_template():
    plan = TurnBehaviorPlan(
        primary_move="practical_support",
        information_order="stepwise",
        question_mode="needed_only",
        support_move="next_step",
        closure_intent="leave_space",
        initiative_hook="none",
        salient_traits=("thought_organization", "initiative_bias"),
    )
    common = {
        "persona": "你是小芯。",
        "memory_context": "",
        "relationship_context": "",
        "route": {"reply_mode": "free_chat", "intent": "chat"},
        "knowledge_context": None,
    }

    active_prompt = prompts.build_system_messages(
        **common,
        turn_behavior_plan=plan,
    )[0]["content"]
    inactive_prompt = prompts.build_system_messages(
        **common,
        turn_behavior_plan=None,
    )[0]["content"]

    assert "<turn_behavior_plan>" in active_prompt
    assert "不是话术模板" in active_prompt
    assert "问题预算" in active_prompt
    assert "固定开场" in active_prompt
    assert "<turn_behavior_plan>" not in inactive_prompt
