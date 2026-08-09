from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionMind,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.reflection import AdjustmentProposal, ReflectionProposal
from core.xiaoxin.companion.store import CompanionStore


def _run_due_work(mind, **kwargs):
    return asyncio.run(mind.run_due_work(**kwargs))


class RecordingReflectionModel:
    def __init__(self, proposal: ReflectionProposal | None = None) -> None:
        self.calls = []
        self.proposal = proposal or ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="本轮无需形成长期相处调整。",
        )

    def reflect(self, request):
        self.calls.append(request)
        return self.proposal


def _subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


_BEHAVIOR_KEYS = {
    "response_length": "response_length",
    "question_frequency": "follow_up_question",
    "initiative_level": "proactive_initiative",
    "memory_reference_depth": "memory_reference",
    "emotional_posture": "emotional_posture",
    "humor_level": "humor",
    "closure_style": "conversation_closure",
    "hardware_expression_intensity": "hardware_expression",
}

_DIRECTIONS = {
    ("response_length", "short"): "decrease",
    ("response_length", "expanded"): "increase",
    ("question_frequency", "never"): "decrease",
    ("question_frequency", "less"): "decrease",
    ("question_frequency", "often"): "increase",
    ("initiative_level", "disabled"): "decrease",
    ("initiative_level", "low"): "decrease",
    ("initiative_level", "medium"): "increase",
    ("memory_reference_depth", "never"): "decrease",
    ("memory_reference_depth", "shallow"): "decrease",
    ("memory_reference_depth", "moderate"): "increase",
    ("memory_reference_depth", "deep"): "increase",
    ("emotional_posture", "neutral"): "decrease",
    ("emotional_posture", "supportive"): "increase",
    ("emotional_posture", "attuned"): "increase",
    ("humor_level", "none"): "decrease",
    ("humor_level", "low"): "decrease",
    ("humor_level", "medium"): "increase",
    ("closure_style", "concise"): "decrease",
    ("closure_style", "warm"): "increase",
    ("closure_style", "relational"): "increase",
    ("closure_style", "familiar"): "increase",
    ("hardware_expression_intensity", "low"): "decrease",
    ("hardware_expression_intensity", "neutral"): "decrease",
    ("hardware_expression_intensity", "medium"): "increase",
    ("hardware_expression_intensity", "high"): "increase",
}


def _eligible_adjustment_content(
    *,
    outcome: str,
    dimension: str,
    value: str,
    scope: str = "conversation",
) -> dict[str, object]:
    return {
        "outcome": outcome,
        "behavior_key": _BEHAVIOR_KEYS[dimension],
        "context_scope": scope,
        "direction": _DIRECTIONS[(dimension, value)],
        "feedback_specificity": "behavior_and_context",
        "source_reliability": "first_party_observed",
        "claim_context": "direct",
        "temporal_scope": "behavior_pattern",
    }


def _commit_signal(
    mind: CompanionMind,
    *,
    turn_id: str,
    occurred_at: str,
    signal: dict[str, object],
):
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=turn_id,
            subject=_subject(),
            request_digest=f"digest-{turn_id}",
            surface="voice",
            occurred_at=occurred_at,
        )
    )
    return mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(signal,),
        ),
    )


def _consolidate_adjustment(
    mind: CompanionMind,
    model: RecordingReflectionModel,
    *,
    turn_id: str,
    occurred_at: str,
    dimension: str,
    value: str,
    scope: str = "conversation",
):
    committed = _commit_signal(
        mind,
        turn_id=turn_id,
        occurred_at=occurred_at,
        signal={
            "kind": "interaction_feedback",
            "ownership_scope": "relationship",
            "content": _eligible_adjustment_content(
                outcome="interaction_pattern_observed",
                dimension=dimension,
                value=value,
                scope=scope,
            ),
            "source_summary": "本轮出现了可核对的互动结果。",
            "attribution": "observed_interaction",
            "confidence": 0.9,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="本轮形成了一次可撤销的相处调整信号。",
        evidence_ids=committed.evidence_ids,
        adjustments=(
            AdjustmentProposal(
                dimension=dimension,
                value=value,
                scope=scope,
                evidence_ids=committed.evidence_ids,
                confidence=0.9,
            ),
        ),
    )
    completed_at = (
        datetime.fromisoformat(occurred_at) + timedelta(minutes=1)
    ).isoformat()
    result = _run_due_work(mind, now=completed_at)
    assert result.succeeded == 1
    return committed


def test_non_meaningful_session_does_not_call_model_or_create_capsule(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    _commit_signal(
        mind,
        turn_id="turn-ordinary",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "assistant_action",
            "ownership_scope": "relationship",
            "content": {"reply_mode": "general_qa"},
            "source_summary": "本轮完成普通事实问答。",
            "attribution": "observed_interaction",
            "confidence": 1.0,
            "retention": "short_term",
            "prompt_eligible": False,
        },
    )

    result = _run_due_work(mind, now="2026-07-18T10:01:00+08:00")

    assert result.succeeded == 1
    assert model.calls == []
    with store.connection() as connection:
        capsule_count = connection.execute(
            "SELECT COUNT(*) FROM session_capsules"
        ).fetchone()[0]
    assert capsule_count == 0


@pytest.mark.parametrize(
    "reply_mode",
    ("time", "weather", "general_qa", "tool_call", "unresolved_chitchat"),
)
def test_ordinary_interaction_modes_never_create_capsules(tmp_path, reply_mode):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    _commit_signal(
        mind,
        turn_id=f"turn-ordinary-{reply_mode}",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "assistant_action",
            "ownership_scope": "relationship",
            "content": {"reply_mode": reply_mode},
            "source_summary": "本轮没有形成有意义互动结果。",
            "attribution": "observed_interaction",
            "confidence": 1.0,
            "retention": "short_term",
            "prompt_eligible": False,
        },
    )

    assert _run_due_work(mind, now="2026-07-18T10:01:00+08:00").succeeded == 1
    assert model.calls == []
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM session_capsules"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("kind", "content"),
    (
        ("explicit_boundary", {"boundary_key": "question_frequency"}),
        ("interaction_feedback", {"outcome": "helpful"}),
        ("followup_completed", {"status": "completed"}),
        ("accepted_help", {"outcome": "accepted"}),
    ),
)
def test_meaningful_outcome_kinds_can_create_capsules(tmp_path, kind, content):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    committed = _commit_signal(
        mind,
        turn_id=f"turn-meaningful-{kind}",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": kind,
            "ownership_scope": "relationship",
            "content": content,
            "source_summary": "本轮形成了可核对的互动结果。",
            "attribution": "observed_interaction",
            "confidence": 1.0,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="本轮形成了可核对的互动结果。",
        evidence_ids=committed.evidence_ids,
    )

    assert _run_due_work(mind, now="2026-07-18T10:01:00+08:00").succeeded == 1
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM session_capsules"
        ).fetchone()[0] == 1


def test_meaningful_session_creates_capsule_citing_current_epoch_evidence(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    committed = _commit_signal(
        mind,
        turn_id="turn-helpful",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "meaningful_moment",
            "ownership_scope": "relationship",
            "content": {"outcome": "accepted_help"},
            "source_summary": "用户确认这次帮助解决了问题。",
            "attribution": "observed_interaction",
            "confidence": 1.0,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="本轮帮助得到用户明确接受。",
        evidence_ids=committed.evidence_ids,
    )

    result = _run_due_work(mind, now="2026-07-18T10:01:00+08:00")

    assert result.succeeded == 1
    assert len(model.calls) == 1
    with store.connection() as connection:
        capsule = connection.execute(
            """
            SELECT relationship_epoch_id, safe_summary, interaction_outcome,
                   status, expires_at
            FROM session_capsules
            """
        ).fetchone()
        linked_ids = tuple(
            row[0]
            for row in connection.execute(
                "SELECT evidence_id FROM capsule_evidence ORDER BY evidence_id"
            )
        )
        active_epoch_id = connection.execute(
            """
            SELECT epoch_id FROM relationship_epochs
            WHERE pet_id = 'pet-1' AND ended_at IS NULL
            """
        ).fetchone()[0]

    assert capsule["relationship_epoch_id"] == active_epoch_id
    assert capsule["safe_summary"] == "本轮帮助得到用户明确接受。"
    assert capsule["interaction_outcome"] == "accepted_help"
    assert capsule["status"] == "active"
    assert capsule["expires_at"] == "2026-10-16T10:01:00+08:00"
    assert linked_ids == committed.evidence_ids


def test_single_implicit_signal_creates_only_candidate_adjustment(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    committed = _commit_signal(
        mind,
        turn_id="turn-less-questions-1",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "interaction_feedback",
            "ownership_scope": "relationship",
            "content": _eligible_adjustment_content(
                outcome="user_relaxed_when_questions_reduced",
                dimension="question_frequency",
                value="never",
            ),
            "source_summary": "减少追问后用户继续了对话。",
            "attribution": "observed_interaction",
            "confidence": 0.8,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="少追问时本轮互动更顺畅。",
        evidence_ids=committed.evidence_ids,
        adjustments=(
            AdjustmentProposal(
                dimension="question_frequency",
                value="never",
                scope="conversation",
                evidence_ids=committed.evidence_ids,
                confidence=0.8,
            ),
        ),
    )

    result = _run_due_work(mind, now="2026-07-18T10:01:00+08:00")

    assert result.succeeded == 1
    with store.connection() as connection:
        adjustment = connection.execute(
            """
            SELECT dimension, value_json, scope, status, confidence,
                   generated_by, valid_until
            FROM companion_adjustments
            """
        ).fetchone()
        linked_ids = tuple(
            row[0]
            for row in connection.execute(
                "SELECT evidence_id FROM adjustment_evidence ORDER BY evidence_id"
            )
        )
        signals_json = connection.execute(
            "SELECT adjustment_signals_json FROM session_capsules"
        ).fetchone()[0]

    assert tuple(adjustment) == (
        "question_frequency",
        '{"value":"never"}',
        "conversation",
        "candidate",
        0.8,
        "RecordingReflectionModel",
        "2026-08-17T10:01:00+08:00",
    )
    assert linked_ids == committed.evidence_ids
    assert signals_json == '["question_frequency"]'
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-candidate-adjustment",
            subject=_subject(),
            request_digest="digest-after-candidate-adjustment",
            surface="voice",
            occurred_at="2026-07-18T10:02:00+08:00",
        )
    )
    assert prepared.policy.question_budget > 0


def test_repeated_cross_date_evidence_promotes_candidate_to_trial_then_active(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    statuses = []

    for index, day in enumerate((18, 19, 20), start=1):
        committed = _commit_signal(
            mind,
            turn_id=f"turn-no-questions-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            signal={
                "kind": "interaction_feedback",
                "ownership_scope": "relationship",
                "content": _eligible_adjustment_content(
                    outcome="conversation_continued_without_questions",
                    dimension="question_frequency",
                    value="never",
                ),
                "source_summary": "不追问时用户主动继续表达。",
                "attribution": "observed_interaction",
                "confidence": 0.9,
                "retention": "long_term",
                "prompt_eligible": True,
            },
        )
        model.proposal = ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="不追问时用户更愿意继续表达。",
            evidence_ids=committed.evidence_ids,
            adjustments=(
                AdjustmentProposal(
                    dimension="question_frequency",
                    value="never",
                    scope="conversation",
                    evidence_ids=committed.evidence_ids,
                    confidence=0.9,
                ),
            ),
        )
        result = _run_due_work(mind, now=f"2026-07-{day:02d}T10:01:00+08:00")
        assert result.succeeded == 1
        with store.connection() as connection:
            rows = connection.execute(
                """
                SELECT status, valid_until
                FROM companion_adjustments
                ORDER BY created_at, adjustment_id
                """
            ).fetchall()
        statuses.append(tuple(rows[-1]))

    assert statuses == [
        ("candidate", "2026-08-17T10:01:00+08:00"),
        ("trial", "2026-09-17T10:01:00+08:00"),
        ("active", None),
    ]
    with store.connection() as connection:
        adjustment_count = connection.execute(
            "SELECT COUNT(*) FROM companion_adjustments"
        ).fetchone()[0]
        linked_count = connection.execute(
            "SELECT COUNT(*) FROM adjustment_evidence"
        ).fetchone()[0]
    assert adjustment_count == 1
    assert linked_count == 3

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-active-adjustment",
            subject=_subject(),
            request_digest="digest-after-active-adjustment",
            surface="voice",
            occurred_at="2026-07-20T11:00:00+08:00",
        )
    )
    assert prepared.policy.question_budget == 0


@pytest.mark.parametrize(
    ("content_overrides", "attribution", "expected_status", "qualification"),
    (
        ({}, "observed_interaction", "candidate", "eligible"),
        (
            {"feedback_specificity": "generic"},
            "observed_interaction",
            "candidate",
            "clue_only",
        ),
        (
            {"source_reliability": "model_inference"},
            "model_inference",
            None,
            None,
        ),
    ),
)
def test_adjustment_evidence_qualification_matrix_is_deterministic(
    tmp_path,
    content_overrides,
    attribution,
    expected_status,
    qualification,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    content = _eligible_adjustment_content(
        outcome="conversation_continued_without_questions",
        dimension="question_frequency",
        value="less",
    )
    content.update(content_overrides)
    committed = _commit_signal(
        mind,
        turn_id=f"turn-qualification-{qualification or 'rejected'}",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "interaction_feedback",
            "ownership_scope": "relationship",
            "content": content,
            "source_summary": "本轮提供一个资格矩阵样本。",
            "attribution": attribution,
            "confidence": 0.99,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="本轮形成一个待门禁判定的相处信号。",
        evidence_ids=committed.evidence_ids,
        adjustments=(
            AdjustmentProposal(
                dimension="question_frequency",
                value="less",
                scope="conversation",
                evidence_ids=committed.evidence_ids,
                confidence=0.99,
            ),
        ),
    )

    assert _run_due_work(mind, now="2026-07-18T10:01:00+08:00").succeeded == 1

    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT adjustment_id, behavior_key, context_scope, direction, status
            FROM companion_adjustments
            """
        ).fetchone()
        lineage = connection.execute(
            """
            SELECT qualification, reason_code, qualifying_local_date,
                   contributes_date
            FROM adjustment_evidence_qualification
            """
        ).fetchone()
        capsule_count = connection.execute(
            "SELECT COUNT(*) FROM session_capsules"
        ).fetchone()[0]

    assert capsule_count == 1
    if expected_status is None:
        assert row is None
        assert lineage is None
        return
    assert tuple(row)[1:] == (
        "follow_up_question",
        "conversation",
        "decrease",
        expected_status,
    )
    assert lineage["qualification"] == qualification
    if qualification == "eligible":
        assert lineage["reason_code"] == "specific_first_party_feedback"
        assert lineage["qualifying_local_date"] == "2026-07-18"
        assert lineage["contributes_date"] == 1
    else:
        assert lineage["reason_code"] == "generic_feedback_clue_only"
        assert lineage["qualifying_local_date"] is None
        assert lineage["contributes_date"] == 0


def test_shanghai_dates_drive_promotion_and_lineage(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    statuses = []
    for index, occurred_at in enumerate(
        (
            "2026-07-18T16:30:00+00:00",
            "2026-07-19T23:30:00+08:00",
            "2026-07-20T10:00:00+08:00",
            "2026-07-21T10:00:00+08:00",
        ),
        start=1,
    ):
        _consolidate_adjustment(
            mind,
            model,
            turn_id=f"turn-shanghai-date-{index}",
            occurred_at=occurred_at,
            dimension="question_frequency",
            value="less",
        )
        with store.connection() as connection:
            statuses.append(
                connection.execute(
                    "SELECT status FROM companion_adjustments"
                ).fetchone()[0]
            )

    assert statuses == ["candidate", "candidate", "trial", "active"]
    with store.connection() as connection:
        lineage = tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT qualifying_local_date, contributes_date
                FROM adjustment_evidence_qualification
                WHERE qualification = 'eligible'
                ORDER BY evidence_id
                """
            )
        )
        voting_dates = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT qualifying_local_date
                FROM adjustment_evidence_qualification
                WHERE contributes_date = 1
                ORDER BY qualifying_local_date
                """
            )
        )

    assert sorted(lineage) == [
        ("2026-07-19", 0),
        ("2026-07-19", 1),
        ("2026-07-20", 1),
        ("2026-07-21", 1),
    ]
    assert voting_dates == ("2026-07-19", "2026-07-20", "2026-07-21")

    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T10:02:00+08:00",
        )
    )
    adjustment = operator.payload["diagnostics"]["lineage"]["adjustments"][0]
    contributing_lineage = tuple(
        item
        for item in adjustment["qualification_lineage"]
        if item["contributes_date"]
    )
    assert tuple(
        item["qualifying_local_date"] for item in contributing_lineage
    ) == ("2026-07-19", "2026-07-20", "2026-07-21")
    assert len({item["evidence_id"] for item in contributing_lineage}) == 3
    assert {
        item["reason_code"] for item in contributing_lineage
    } == {"specific_first_party_feedback"}


def test_store_rechecks_unknown_speaker_before_creating_adjustment(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    committed = _commit_signal(
        mind,
        turn_id="turn-speaker-recheck",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "interaction_feedback",
            "ownership_scope": "relationship",
            "content": _eligible_adjustment_content(
                outcome="conversation_continued_without_questions",
                dimension="question_frequency",
                value="less",
            ),
            "source_summary": "本轮原本具有完整的结构化反馈字段。",
            "attribution": "observed_interaction",
            "confidence": 0.9,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    with store.connection() as connection:
        connection.execute(
            """
            UPDATE companion_evidence
            SET speaker_identity = 'unknown'
            WHERE evidence_id = ?
            """,
            committed.evidence_ids,
        )
        connection.commit()
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="该信号必须在持久化边界重新验明主体。",
        evidence_ids=committed.evidence_ids,
        adjustments=(
            AdjustmentProposal(
                dimension="question_frequency",
                value="less",
                scope="conversation",
                evidence_ids=committed.evidence_ids,
                confidence=0.9,
            ),
        ),
    )

    assert _run_due_work(mind, now="2026-07-18T10:01:00+08:00").succeeded == 1
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_adjustments"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM session_capsules"
        ).fetchone()[0] == 1


def test_forgetting_clue_only_evidence_preserves_independent_active_adjustment(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    clue_content = _eligible_adjustment_content(
        outcome="helpful",
        dimension="response_length",
        value="short",
    )
    clue_content["feedback_specificity"] = "generic"
    clue = _commit_signal(
        mind,
        turn_id="turn-clue-only-before-active",
        occurred_at="2026-07-17T10:00:00+08:00",
        signal={
            "kind": "interaction_feedback",
            "ownership_scope": "relationship",
            "content": clue_content,
            "source_summary": "这只是一次泛化线索。",
            "attribution": "observed_interaction",
            "confidence": 0.99,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="该线索只能建立候选，不能贡献日期。",
        evidence_ids=clue.evidence_ids,
        adjustments=(
            AdjustmentProposal(
                dimension="response_length",
                value="short",
                scope="conversation",
                evidence_ids=clue.evidence_ids,
                confidence=0.99,
            ),
        ),
    )
    assert _run_due_work(mind, now="2026-07-17T10:01:00+08:00").succeeded == 1
    for index, day in enumerate((18, 19, 20), start=1):
        _consolidate_adjustment(
            mind,
            model,
            turn_id=f"turn-qualified-after-clue-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            dimension="response_length",
            value="short",
        )

    result = mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject(),
            payload={
                "evidence_id": clue.evidence_ids[0],
                "now": "2026-07-20T10:02:00+08:00",
                "idempotency_key": "forget-nondecisive-clue",
            },
        )
    )

    assert result.forgotten == 1
    with store.connection() as connection:
        adjustment = connection.execute(
            """
            SELECT status
            FROM companion_adjustments
            WHERE behavior_key = 'response_length'
            """
        ).fetchone()
        qualifying_dates = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT qualifying_local_date
                FROM adjustment_evidence_qualification
                WHERE contributes_date = 1
                ORDER BY qualifying_local_date
                """
            )
        )

    assert adjustment["status"] == "active"
    assert qualifying_dates == ("2026-07-18", "2026-07-19", "2026-07-20")


def test_expired_candidate_cannot_reuse_old_qualified_date(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    _consolidate_adjustment(
        mind,
        model,
        turn_id="turn-expired-lineage-1",
        occurred_at="2026-07-01T10:00:00+08:00",
        dimension="humor_level",
        value="low",
    )
    assert _run_due_work(mind, now="2026-07-31T10:01:00+08:00").claimed == 0
    _consolidate_adjustment(
        mind,
        model,
        turn_id="turn-expired-lineage-2",
        occurred_at="2026-08-01T10:00:00+08:00",
        dimension="humor_level",
        value="low",
    )

    with store.connection() as connection:
        adjustments = tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT adjustment_id, status
                FROM companion_adjustments
                ORDER BY created_at, adjustment_id
                """
            )
        )
        votes = tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT adjustment_id, qualifying_local_date
                FROM adjustment_evidence_qualification
                WHERE contributes_date = 1
                ORDER BY qualifying_local_date
                """
            )
        )

    assert [status for _, status in adjustments] == ["expired", "candidate"]
    assert len({adjustment_id for adjustment_id, _ in votes}) == 2
    assert [local_date for _, local_date in votes] == [
        "2026-07-01",
        "2026-08-01",
    ]


def test_repeated_same_day_evidence_remains_candidate(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    for index in (1, 2):
        _consolidate_adjustment(
            mind,
            model,
            turn_id=f"turn-same-day-{index}",
            occurred_at="2026-07-18T10:00:00+08:00",
            dimension="closure_style",
            value="warm",
        )

    with store.connection() as connection:
        adjustment = tuple(
            connection.execute(
                "SELECT status, valid_until FROM companion_adjustments"
            ).fetchone()
        )
        linked_count = connection.execute(
            "SELECT COUNT(*) FROM adjustment_evidence"
        ).fetchone()[0]

    assert adjustment == ("candidate", "2026-08-17T10:01:00+08:00")
    assert linked_count == 2


def test_active_response_length_adjustment_changes_policy_within_age_limit(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    for index, day in enumerate((18, 19, 20), start=1):
        _consolidate_adjustment(
            mind,
            model,
            turn_id=f"turn-short-response-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            dimension="response_length",
            value="short",
        )

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-short-response-active",
            subject=_subject(),
            request_digest="digest-after-short-response-active",
            surface="voice",
            occurred_at="2026-07-20T11:00:00+08:00",
        )
    )

    assert prepared.policy.xiaoxin_age == 2
    assert prepared.policy.response_length == "short"


def test_explicit_boundary_applies_immediately_without_adjustment(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
    )
    bootstrap = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-before-boundary",
            subject=_subject(),
            request_digest="digest-before-boundary",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    mind.commit_turn(
        bootstrap,
        CompanionTurnOutcome(
            visible_response="你好。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    result = mind.apply_control(
        CompanionControlCommand(
            action="set_boundary",
            subject=_subject(),
            payload={
                "boundary_key": "question_frequency",
                "value": "never",
                "source_summary": "用户明确要求不要追问。",
                "now": "2026-07-18T10:01:00+08:00",
                "idempotency_key": "boundary-no-questions",
            },
        )
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-boundary",
            subject=_subject(),
            request_digest="digest-after-boundary",
            surface="voice",
            occurred_at="2026-07-18T10:02:00+08:00",
        )
    )

    assert result.status == "applied"
    assert prepared.policy.question_budget == 0
    with store.connection() as connection:
        adjustment_count = connection.execute(
            "SELECT COUNT(*) FROM companion_adjustments"
        ).fetchone()[0]
    assert adjustment_count == 0


def test_forgetting_evidence_invalidates_capsule_and_revokes_adjustment(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    committed = _commit_signal(
        mind,
        turn_id="turn-forget-derived",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "interaction_feedback",
            "ownership_scope": "relationship",
            "content": _eligible_adjustment_content(
                outcome="helpful",
                dimension="closure_style",
                value="warm",
            ),
            "source_summary": "用户确认本轮帮助有效。",
            "attribution": "observed_interaction",
            "confidence": 0.9,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="本轮帮助得到正向反馈。",
        evidence_ids=committed.evidence_ids,
        adjustments=(
            AdjustmentProposal(
                dimension="closure_style",
                value="warm",
                scope="conversation",
                evidence_ids=committed.evidence_ids,
                confidence=0.9,
            ),
        ),
    )
    assert _run_due_work(mind, now="2026-07-18T10:01:00+08:00").succeeded == 1

    result = mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject(),
            payload={
                "evidence_id": committed.evidence_ids[0],
                "now": "2026-07-18T10:02:00+08:00",
                "idempotency_key": "forget-derived-evidence",
            },
        )
    )

    assert result.forgotten == 1
    assert result.requeued == 1
    with store.connection() as connection:
        evidence_status = connection.execute(
            "SELECT status FROM companion_evidence WHERE evidence_id = ?",
            committed.evidence_ids,
        ).fetchone()[0]
        capsule_status = connection.execute(
            "SELECT status FROM session_capsules"
        ).fetchone()[0]
        adjustment_status = connection.execute(
            "SELECT status FROM companion_adjustments"
        ).fetchone()[0]
        recompute_status = connection.execute(
            """
            SELECT status FROM consolidation_jobs
            WHERE job_kind = 'recompute_after_forget'
            """
        ).fetchone()[0]

    assert evidence_status == "forgotten"
    assert capsule_status == "invalidated"
    assert adjustment_status == "revoked"
    assert recompute_status == "pending"

    calls_before_recompute = len(model.calls)
    recompute = _run_due_work(mind, now="2026-07-18T10:03:00+08:00")
    assert recompute.succeeded == 1
    assert len(model.calls) == calls_before_recompute
    with store.connection() as connection:
        recompute_status = connection.execute(
            """
            SELECT status FROM consolidation_jobs
            WHERE job_kind = 'recompute_after_forget'
            """
        ).fetchone()[0]
    assert recompute_status == "succeeded"


def test_forgetting_theme_invalidates_all_derived_objects_using_its_evidence(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    committed = _commit_signal(
        mind,
        turn_id="turn-forget-theme-derived",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "interaction_feedback",
            "ownership_scope": "relationship",
            "content": {
                **_eligible_adjustment_content(
                    outcome="helpful",
                    dimension="response_length",
                    value="short",
                ),
                "theme": "项目复盘",
            },
            "source_summary": "用户确认本轮复盘有帮助。",
            "attribution": "observed_interaction",
            "confidence": 0.9,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="本轮复盘得到正向反馈。",
        evidence_ids=committed.evidence_ids,
        adjustments=(
            AdjustmentProposal(
                dimension="response_length",
                value="short",
                scope="conversation",
                evidence_ids=committed.evidence_ids,
                confidence=0.9,
            ),
        ),
    )
    assert _run_due_work(mind, now="2026-07-18T10:01:00+08:00").succeeded == 1

    result = mind.apply_control(
        CompanionControlCommand(
            action="forget_theme",
            subject=_subject(),
            payload={
                "theme": "项目复盘",
                "now": "2026-07-18T10:02:00+08:00",
                "idempotency_key": "forget-derived-theme",
            },
        )
    )

    assert result.forgotten == 1
    with store.connection() as connection:
        capsule_status = connection.execute(
            "SELECT status FROM session_capsules"
        ).fetchone()[0]
        adjustment_status = connection.execute(
            "SELECT status FROM companion_adjustments"
        ).fetchone()[0]
    assert capsule_status == "invalidated"
    assert adjustment_status == "revoked"


def test_worker_expires_candidate_after_30_days_and_capsule_after_90_days(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    committed = _commit_signal(
        mind,
        turn_id="turn-expiring-derived",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "interaction_feedback",
            "ownership_scope": "relationship",
            "content": _eligible_adjustment_content(
                outcome="helpful",
                dimension="humor_level",
                value="low",
            ),
            "source_summary": "用户给出一次正向互动反馈。",
            "attribution": "observed_interaction",
            "confidence": 0.8,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="本轮收到一次正向反馈。",
        evidence_ids=committed.evidence_ids,
        adjustments=(
            AdjustmentProposal(
                dimension="humor_level",
                value="low",
                scope="conversation",
                evidence_ids=committed.evidence_ids,
                confidence=0.8,
            ),
        ),
    )
    assert _run_due_work(mind, now="2026-07-18T10:01:00+08:00").succeeded == 1

    assert _run_due_work(mind, now="2026-08-17T10:01:00+08:00").claimed == 0
    with store.connection() as connection:
        statuses_after_30 = (
            connection.execute(
                "SELECT status FROM companion_adjustments"
            ).fetchone()[0],
            connection.execute("SELECT status FROM session_capsules").fetchone()[0],
        )

    assert statuses_after_30 == ("expired", "active")

    assert _run_due_work(mind, now="2026-10-16T10:01:00+08:00").claimed == 0
    with store.connection() as connection:
        capsule_status = connection.execute(
            "SELECT status FROM session_capsules"
        ).fetchone()[0]
    assert capsule_status == "expired"


def test_expired_evidence_invalidates_capsule_and_revokes_adjustment(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    committed = _commit_signal(
        mind,
        turn_id="turn-expiring-evidence",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "interaction_feedback",
            "ownership_scope": "relationship",
            "content": _eligible_adjustment_content(
                outcome="temporary_signal",
                dimension="closure_style",
                value="warm",
            ),
            "source_summary": "这是一个有明确截止时间的互动信号。",
            "attribution": "observed_interaction",
            "confidence": 0.8,
            "retention": "short_term",
            "prompt_eligible": True,
            "expires_at": "2026-07-19T10:00:00+08:00",
        },
    )
    model.proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="本轮形成一个临时互动信号。",
        evidence_ids=committed.evidence_ids,
        adjustments=(
            AdjustmentProposal(
                dimension="closure_style",
                value="warm",
                scope="conversation",
                evidence_ids=committed.evidence_ids,
                confidence=0.8,
            ),
        ),
    )
    assert _run_due_work(mind, now="2026-07-18T10:01:00+08:00").succeeded == 1

    assert _run_due_work(mind, now="2026-07-19T10:00:00+08:00").claimed == 0
    with store.connection() as connection:
        states = (
            connection.execute(
                "SELECT status FROM companion_evidence WHERE evidence_id = ?",
                committed.evidence_ids,
            ).fetchone()[0],
            connection.execute("SELECT status FROM session_capsules").fetchone()[0],
            connection.execute(
                "SELECT status FROM companion_adjustments"
            ).fetchone()[0],
        )
    assert states == ("expired", "invalidated", "revoked")


def test_worker_expires_trial_after_60_days_without_revalidation(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    _consolidate_adjustment(
        mind,
        model,
        turn_id="turn-trial-expiry-1",
        occurred_at="2026-07-18T10:00:00+08:00",
        dimension="humor_level",
        value="low",
    )
    _consolidate_adjustment(
        mind,
        model,
        turn_id="turn-trial-expiry-2",
        occurred_at="2026-07-19T10:00:00+08:00",
        dimension="humor_level",
        value="low",
    )
    with store.connection() as connection:
        before = tuple(
            connection.execute(
                "SELECT status, valid_until FROM companion_adjustments"
            ).fetchone()
        )
    assert before == ("trial", "2026-09-17T10:01:00+08:00")

    assert _run_due_work(mind, now="2026-09-17T10:01:00+08:00").claimed == 0
    with store.connection() as connection:
        status = connection.execute(
            "SELECT status FROM companion_adjustments"
        ).fetchone()[0]
    assert status == "expired"


def test_relationship_reset_revokes_adjustment_and_removes_it_from_policy(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    for index, day in enumerate((18, 19, 20), start=1):
        _consolidate_adjustment(
            mind,
            model,
            turn_id=f"turn-reset-adjustment-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            dimension="question_frequency",
            value="never",
        )
    before_reset = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-before-reset-adjustment",
            subject=_subject(),
            request_digest="digest-before-reset-adjustment",
            surface="voice",
            occurred_at="2026-07-20T11:00:00+08:00",
        )
    )
    assert before_reset.policy.question_budget == 0

    result = mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={
                "now": "2026-07-21T10:00:00+08:00",
                "idempotency_key": "reset-derived-adjustment",
            },
        )
    )
    after_reset = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-reset-adjustment",
            subject=_subject(),
            request_digest="digest-after-reset-adjustment",
            surface="voice",
            occurred_at="2026-07-21T10:01:00+08:00",
        )
    )

    assert result.status == "applied"
    assert after_reset.policy.question_budget > 0
    with store.connection() as connection:
        adjustment_status = connection.execute(
            "SELECT status FROM companion_adjustments"
        ).fetchone()[0]
        capsule_statuses = {
            row[0] for row in connection.execute("SELECT status FROM session_capsules")
        }
    assert adjustment_status == "revoked"
    assert capsule_statuses == {"inactive"}


def test_single_contrary_evidence_keeps_active_adjustment_until_three_days(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    for index, day in enumerate((18, 19, 20), start=1):
        _consolidate_adjustment(
            mind,
            model,
            turn_id=f"turn-contrary-base-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            dimension="question_frequency",
            value="never",
        )
    _consolidate_adjustment(
        mind,
        model,
        turn_id="turn-contrary-signal",
        occurred_at="2026-07-21T10:00:00+08:00",
        dimension="question_frequency",
        value="often",
    )

    with store.connection() as connection:
        states = {
            row["value_json"]: row["status"]
            for row in connection.execute(
                """
                SELECT value_json, status
                FROM companion_adjustments
                WHERE dimension = 'question_frequency'
                """
            )
        }
    assert states == {
        '{"value":"never"}': "active",
        '{"value":"often"}': "candidate",
    }

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-contrary-signal",
            subject=_subject(),
            request_digest="digest-after-contrary-signal",
            surface="voice",
            occurred_at="2026-07-21T11:00:00+08:00",
        )
    )
    assert prepared.policy.question_budget == 0


def test_contrary_adjustment_replaces_old_active_only_after_three_days(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    for index, day in enumerate((18, 19, 20), start=1):
        _consolidate_adjustment(
            mind,
            model,
            turn_id=f"turn-replace-base-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            dimension="question_frequency",
            value="never",
        )
    for index, day in enumerate((21, 22, 23), start=1):
        _consolidate_adjustment(
            mind,
            model,
            turn_id=f"turn-replace-contrary-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            dimension="question_frequency",
            value="often",
        )

    with store.connection() as connection:
        states = {
            row["value_json"]: row["status"]
            for row in connection.execute(
                """
                SELECT value_json, status
                FROM companion_adjustments
                WHERE dimension = 'question_frequency'
                """
            )
        }
    assert states == {
        '{"value":"never"}': "superseded",
        '{"value":"often"}': "active",
    }

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-contrary-replacement",
            subject=_subject(),
            request_digest="digest-after-contrary-replacement",
            surface="voice",
            occurred_at="2026-07-23T11:00:00+08:00",
        )
    )
    assert prepared.policy.question_budget > 0


def test_original_direction_confirmation_supersedes_weak_contrary_challenge(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    for index, day in enumerate((18, 19, 20), start=1):
        _consolidate_adjustment(
            mind,
            model,
            turn_id=f"turn-confirm-base-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            dimension="question_frequency",
            value="never",
        )
    _consolidate_adjustment(
        mind,
        model,
        turn_id="turn-confirm-contrary",
        occurred_at="2026-07-21T10:00:00+08:00",
        dimension="question_frequency",
        value="often",
    )
    _consolidate_adjustment(
        mind,
        model,
        turn_id="turn-confirm-original",
        occurred_at="2026-07-22T10:00:00+08:00",
        dimension="question_frequency",
        value="never",
    )

    with store.connection() as connection:
        states = {
            row["value_json"]: row["status"]
            for row in connection.execute(
                """
                SELECT value_json, status
                FROM companion_adjustments
                WHERE dimension = 'question_frequency'
                """
            )
        }
    assert states == {
        '{"value":"never"}': "active",
        '{"value":"often"}': "superseded",
    }


def test_forget_rebuilds_new_adjustment_from_remaining_qualified_days(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    for index, day in enumerate((18, 19, 20), start=1):
        _consolidate_adjustment(
            mind,
            model,
            turn_id=f"turn-forget-rebuild-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            dimension="response_length",
            value="short",
        )
    with store.connection() as connection:
        original = connection.execute(
            """
            SELECT adjustment_id
            FROM companion_adjustments
            WHERE dimension = 'response_length' AND status = 'active'
            """
        ).fetchone()["adjustment_id"]
        forgotten_evidence_id = connection.execute(
            """
            SELECT qualification.evidence_id
            FROM adjustment_evidence_qualification AS qualification
            WHERE qualification.adjustment_id = ?
              AND qualification.contributes_date = 1
            ORDER BY qualification.qualifying_local_date
            LIMIT 1
            """,
            (original,),
        ).fetchone()["evidence_id"]

    result = mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject(),
            payload={
                "evidence_id": forgotten_evidence_id,
                "now": "2026-07-20T10:02:00+08:00",
                "idempotency_key": "forget-rebuild-adjustment",
            },
        )
    )
    assert result.requeued == 1
    assert _run_due_work(mind, now="2026-07-20T10:03:00+08:00").succeeded == 1

    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT adjustment_id, status
            FROM companion_adjustments
            WHERE dimension = 'response_length'
            ORDER BY created_at, adjustment_id
            """
        ).fetchall()
        rebuilt_votes = connection.execute(
            """
            SELECT COUNT(*)
            FROM adjustment_evidence_qualification
            WHERE adjustment_id = ? AND contributes_date = 1
            """,
            (rows[1]["adjustment_id"],),
        ).fetchone()[0]
    assert rows[0]["adjustment_id"] == original
    assert rows[0]["status"] == "revoked"
    assert rows[1]["adjustment_id"] != original
    assert rows[1]["status"] == "trial"
    assert rebuilt_votes == 2


def test_user_correction_revokes_derived_adjustment_and_invalidates_capsule(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )
    committed = _consolidate_adjustment(
        mind,
        model,
        turn_id="turn-correct-derived",
        occurred_at="2026-07-18T10:00:00+08:00",
        dimension="response_length",
        value="short",
    )

    result = mind.apply_control(
        CompanionControlCommand(
            action="correct_evidence",
            subject=_subject(),
            payload={
                "evidence_id": committed.evidence_ids[0],
                "replacement_content": {
                    "outcome": "user_prefers_fuller_explanations"
                },
                "source_summary": "用户澄清自己希望解释更完整。",
                "now": "2026-07-18T10:02:00+08:00",
                "idempotency_key": "correct-derived-adjustment",
            },
        )
    )

    assert result.status == "applied"
    assert result.deactivated == 1
    assert result.requeued == 1
    with store.connection() as connection:
        old_status = connection.execute(
            "SELECT status FROM companion_evidence WHERE evidence_id = ?",
            committed.evidence_ids,
        ).fetchone()[0]
        capsule_status = connection.execute(
            "SELECT status FROM session_capsules"
        ).fetchone()[0]
        adjustment_status = connection.execute(
            "SELECT status FROM companion_adjustments"
        ).fetchone()[0]

    assert old_status == "superseded"
    assert capsule_status == "invalidated"
    assert adjustment_status == "revoked"
    assert _run_due_work(mind, now="2026-07-18T10:03:00+08:00").succeeded == 1
    with store.connection() as connection:
        adjustment_states = tuple(
            row[0]
            for row in connection.execute(
                "SELECT status FROM companion_adjustments ORDER BY created_at"
            )
        )
    assert adjustment_states == ("revoked",)


def test_unrelated_topics_use_the_same_capsule_and_adjustment_path(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = RecordingReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"capsule-adjustment-story",
        reflection_model=model,
    )

    for turn_id, occurred_at, theme in (
        ("turn-programming-feedback", "2026-07-18T10:00:00+08:00", "C语言反馈"),
        ("turn-roommate-feedback", "2026-07-19T10:00:00+08:00", "宿舍相处反馈"),
    ):
        committed = _commit_signal(
            mind,
            turn_id=turn_id,
            occurred_at=occurred_at,
            signal={
                "kind": "interaction_feedback",
                "ownership_scope": "relationship",
                "content": {
                    **_eligible_adjustment_content(
                        outcome="shorter_reply_was_helpful",
                        dimension="response_length",
                        value="short",
                    ),
                    "theme": theme,
                },
                "source_summary": "用户确认较短回答更合适。",
                "attribution": "observed_interaction",
                "confidence": 0.9,
                "retention": "long_term",
                "prompt_eligible": True,
            },
        )
        model.proposal = ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="较短回答在本轮得到正向结果。",
            evidence_ids=committed.evidence_ids,
            adjustments=(
                AdjustmentProposal(
                    dimension="response_length",
                    value="short",
                    scope="conversation",
                    evidence_ids=committed.evidence_ids,
                    confidence=0.9,
                ),
            ),
        )
        assert _run_due_work(
            mind,
            now=occurred_at.replace("T10:00:00", "T10:01:00")
        ).succeeded == 1

    with store.connection() as connection:
        capsule_count = connection.execute(
            "SELECT COUNT(*) FROM session_capsules"
        ).fetchone()[0]
        adjustment = tuple(
            connection.execute(
                "SELECT dimension, value_json, status FROM companion_adjustments"
            ).fetchone()
        )
        schema_columns = {
            row["name"]
            for table in ("session_capsules", "companion_adjustments")
            for row in connection.execute(f"PRAGMA table_info({table})")
        }

    assert capsule_count == 2
    assert adjustment == (
        "response_length",
        '{"value":"short"}',
        "trial",
    )
    assert "theme" not in schema_columns
    assert "topic" not in schema_columns
