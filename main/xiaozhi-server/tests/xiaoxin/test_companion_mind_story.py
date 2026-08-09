from __future__ import annotations

import asyncio

from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionMind,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.reflection import (
    AdjustmentProposal,
    ChapterStatementProposal,
    ReflectionProposal,
)
from core.xiaoxin.companion.store import CompanionStore


def _run_due_work(mind, **kwargs):
    return asyncio.run(mind.run_due_work(**kwargs))


def _subject(stage: str = "sophomore") -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="golden-owner",
        pet_id="golden-pet",
        memory_subject_id="golden-subject",
        speaker_identity="confirmed",
        academic_stage=stage,
        persistence_allowed=True,
    )


def _sync_academic_stage(
    mind: CompanionMind,
    *,
    stage: str,
    effective_at: str,
) -> None:
    mind.apply_control(
        CompanionControlCommand(
            action="sync_academic_stage",
            subject=_subject(stage),
            payload={
                "now": effective_at,
                "effective_at": effective_at,
                "source_revision": 1,
            },
        )
    )


class GoldenReflectionModel:
    def __init__(self) -> None:
        self.adjustment_dimension: str | None = None
        self.adjustment_value: str | None = None

    def reflect(self, request):
        evidence_ids = tuple(item.evidence_id for item in request.evidence)
        relationship_evidence_ids = tuple(
            item.evidence_id
            for item in request.evidence
            if item.ownership_scope == "relationship"
        )
        adjustments = ()
        chapter_statements = ()
        if (
            request.job_kind == "session_consolidation"
            and self.adjustment_dimension is not None
            and self.adjustment_value is not None
        ):
            adjustments = (
                AdjustmentProposal(
                    dimension=self.adjustment_dimension,
                    value=self.adjustment_value,
                    scope="conversation",
                    evidence_ids=relationship_evidence_ids,
                    confidence=0.9,
                ),
            )
        if request.job_kind == "academic_stage_changed":
            chapter_statements = tuple(
                ChapterStatementProposal(
                    claim_scope=(
                        "user_fact"
                        if ownership_scope == "user"
                        else "shared_experience"
                    ),
                    evidence_ids=tuple(
                        item.evidence_id
                        for item in request.evidence
                        if item.ownership_scope == ownership_scope
                    ),
                )
                for ownership_scope in ("user", "relationship")
                if any(
                    item.ownership_scope == ownership_scope
                    for item in request.evidence
                )
            )
        proposal_evidence_ids = (
            evidence_ids
            if request.job_kind == "academic_stage_changed"
            else relationship_evidence_ids
        )
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="黄金故事中的行为只由安全依据形成。",
            evidence_ids=proposal_evidence_ids,
            adjustments=adjustments,
            chapter_statements=chapter_statements,
        )


class OfflineReflectionModel:
    def reflect(self, request):
        raise TimeoutError("reflection timeout")


def _commit_feedback(
    mind: CompanionMind,
    *,
    turn_id: str,
    occurred_at: str,
    signal: dict[str, object],
    stage: str = "sophomore",
):
    return _commit_signals(
        mind,
        turn_id=turn_id,
        occurred_at=occurred_at,
        signals=(signal,),
        stage=stage,
    )


def _eligible_short_reply_feedback(summary: str) -> dict[str, object]:
    return {
        "kind": "interaction_feedback",
        "ownership_scope": "relationship",
        "content": {
            "outcome": "short_reply_worked",
            "behavior_key": "response_length",
            "context_scope": "conversation",
            "direction": "decrease",
            "feedback_specificity": "behavior_and_context",
            "source_reliability": "first_party_observed",
        },
        "source_summary": summary,
        "attribution": "observed_interaction",
        "confidence": 1.0,
        "retention": "long_term",
        "prompt_eligible": True,
    }


def _commit_signals(
    mind: CompanionMind,
    *,
    turn_id: str,
    occurred_at: str,
    signals: tuple[dict[str, object], ...],
    stage: str = "sophomore",
):
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=turn_id,
            subject=_subject(stage),
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
            feedback_signals=signals,
        ),
    )


def _prepare(
    mind: CompanionMind,
    *,
    turn_id: str,
    occurred_at: str,
    stage: str = "sophomore",
):
    return mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=turn_id,
            subject=_subject(stage),
            request_digest=f"digest-{turn_id}",
            surface="voice",
            occurred_at=occurred_at,
        )
    )


def test_golden_story_a_sophomore_first_use_has_age_without_invented_history(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"golden-story",
    )

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="golden-a-first-use",
            subject=_subject(),
            request_digest="golden-a-first-use",
            surface="voice",
            occurred_at="2026-07-18T09:00:00+08:00",
        )
    )

    assert prepared.policy.xiaoxin_age == 2
    assert prepared.policy.relationship_stage == "first_meeting"
    assert prepared.used_evidence_ids == ()
    assert "invent_shared_history" in prepared.policy.prohibited_behaviors


def test_golden_story_b_boundaries_and_growth_adjustments_remain_reversible(
    tmp_path,
):
    model = GoldenReflectionModel()
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"golden-story",
        reflection_model=model,
    )
    bootstrap = _prepare(
        mind,
        turn_id="golden-b-bootstrap",
        occurred_at="2026-07-18T09:00:00+08:00",
    )
    mind.commit_turn(
        bootstrap,
        CompanionTurnOutcome(
            visible_response="你好。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    mind.apply_control(
        CompanionControlCommand(
            action="set_boundary",
            subject=_subject(),
            payload={
                "boundary_key": "question_frequency",
                "value": "never",
                "source_summary": "用户明确要求少追问。",
                "now": "2026-07-18T09:01:00+08:00",
                "idempotency_key": "golden-b-boundary",
            },
        )
    )
    after_boundary = _prepare(
        mind,
        turn_id="golden-b-after-boundary",
        occurred_at="2026-07-18T09:02:00+08:00",
    )
    assert after_boundary.policy.question_budget == 0

    model.adjustment_dimension = "response_length"
    model.adjustment_value = "short"
    committed = []
    for index, day in enumerate((18, 19, 20), start=1):
        outcome = _commit_feedback(
            mind,
            turn_id=f"golden-b-short-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            signal=_eligible_short_reply_feedback(
                "短回答后用户继续自然表达。"
            ),
        )
        committed.append(outcome)
        result = _run_due_work(
            mind,
            now=f"2026-07-{day:02d}T10:01:00+08:00"
        )
        assert result.succeeded == 1

    after_growth = _prepare(
        mind,
        turn_id="golden-b-after-growth",
        occurred_at="2026-07-20T11:00:00+08:00",
    )
    assert after_growth.policy.response_length == "short"

    correction = mind.apply_control(
        CompanionControlCommand(
            action="correct_evidence",
            subject=_subject(),
            payload={
                "evidence_id": committed[0].evidence_ids[0],
                "replacement_content": {
                    "outcome": "user_prefers_fuller_explanations"
                },
                "source_summary": "用户明确要求恢复详细解释。",
                "now": "2026-07-20T11:01:00+08:00",
                "idempotency_key": "golden-b-correction",
            },
        )
    )
    after_correction = _prepare(
        mind,
        turn_id="golden-b-after-correction",
        occurred_at="2026-07-20T11:02:00+08:00",
    )

    assert correction.deactivated == 1
    assert after_correction.policy.response_length == "standard"


def test_golden_story_c_reset_keeps_user_facts_and_retires_relationship_state(
    tmp_path,
):
    model = GoldenReflectionModel()
    model.adjustment_dimension = "response_length"
    model.adjustment_value = "short"
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"golden-story",
        reflection_model=model,
    )
    first = _commit_signals(
        mind,
        turn_id="golden-c-first",
        occurred_at="2026-07-18T10:00:00+08:00",
        signals=(
            {
                "kind": "profile_fact",
                "ownership_scope": "user",
                "content": {"fact_key": "preferred_name", "value": "小林"},
                "source_summary": "用户明确希望被称作小林。",
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "persistent",
                "prompt_eligible": True,
            },
            {
                "kind": "user_life_event",
                "ownership_scope": "user",
                "content": {"event": "完成了首次公开展示"},
                "source_summary": "用户明确表示自己完成了首次公开展示。",
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "persistent",
                "prompt_eligible": True,
            },
            _eligible_short_reply_feedback(
                "用户确认共同互动中的简短回应合适。"
            ),
        ),
    )
    assert first.evidence_ids
    assert _run_due_work(mind, now="2026-07-18T10:01:00+08:00").succeeded == 1

    for index, day in enumerate((19, 20), start=2):
        _commit_feedback(
            mind,
            turn_id=f"golden-c-shared-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            signal=_eligible_short_reply_feedback(
                "用户再次确认共同互动中的简短回应合适。"
            ),
        )
        assert _run_due_work(
            mind,
            now=f"2026-07-{day:02d}T10:01:00+08:00"
        ).succeeded == 1

    _sync_academic_stage(
        mind,
        stage="junior",
        effective_at="2026-09-01T08:59:00+08:00",
    )
    stage_change = _prepare(
        mind,
        turn_id="golden-c-stage-change",
        occurred_at="2026-09-01T09:00:00+08:00",
        stage="junior",
    )
    mind.commit_turn(
        stage_change,
        CompanionTurnOutcome(
            visible_response="新学期好。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    assert _run_due_work(mind, now="2026-09-01T09:01:00+08:00").succeeded == 1

    before_reset = mind.project(
        CompanionProjectionRequest(
            subject=_subject("junior"),
            surface="operator",
            now="2026-09-01T09:02:00+08:00",
        )
    )
    assert before_reset.payload["active_adjustments"]
    assert before_reset.payload["chapters"]

    reset = mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject("junior"),
            payload={
                "now": "2026-09-01T09:03:00+08:00",
                "idempotency_key": "golden-c-reset",
            },
        )
    )
    after_reset = mind.project(
        CompanionProjectionRequest(
            subject=_subject("junior"),
            surface="operator",
            now="2026-09-01T09:04:00+08:00",
        )
    )
    summaries = {
        item["source_summary"] for item in after_reset.payload["evidence"]
    }

    assert reset.deactivated >= 3
    assert after_reset.relationship_stage == "first_meeting"
    assert after_reset.payload["active_adjustments"] == ()
    assert after_reset.payload["chapters"] == ()
    assert "用户明确希望被称作小林。" in summaries
    assert "用户明确表示自己完成了首次公开展示。" in summaries
    assert not any("共同互动" in summary for summary in summaries)


def test_golden_story_d_offline_reflection_does_not_block_realtime_or_device_use(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"golden-story",
        reflection_model=OfflineReflectionModel(),
    )
    committed = _commit_feedback(
        mind,
        turn_id="golden-d-meaningful",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "meaningful_moment",
            "ownership_scope": "relationship",
            "content": {"outcome": "helpful"},
            "source_summary": "用户确认本轮帮助有效。",
            "attribution": "observed_interaction",
            "confidence": 1.0,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )

    failed_work = _run_due_work(mind, now="2026-07-18T10:01:00+08:00")
    next_turn = _prepare(
        mind,
        turn_id="golden-d-after-timeout",
        occurred_at="2026-07-18T10:01:01+08:00",
    )
    next_commit = mind.commit_turn(
        next_turn,
        CompanionTurnOutcome(
            visible_response="对话继续。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    hardware = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="hardware",
            now="2026-07-18T10:01:02+08:00",
        )
    )
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-18T10:01:02+08:00",
        )
    )

    assert committed.job_ids
    assert failed_work.retried == 1
    assert next_commit.status == "committed"
    assert hardware.payload["hardware_expression"]
    assert any(job["status"] == "retry" for job in operator.payload["jobs"])


def test_golden_story_e_forgetting_evidence_immediately_invalidates_derivations(
    tmp_path,
):
    model = GoldenReflectionModel()
    model.adjustment_dimension = "response_length"
    model.adjustment_value = "short"
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"golden-story",
        reflection_model=model,
    )
    committed = []
    for index, day in enumerate((18, 19, 20), start=1):
        outcome = _commit_feedback(
            mind,
            turn_id=f"golden-e-evidence-{index}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            signal=_eligible_short_reply_feedback(
                "用户确认这次共同互动中的简短回应合适。"
            ),
        )
        committed.append(outcome)
        assert _run_due_work(
            mind,
            now=f"2026-07-{day:02d}T10:01:00+08:00"
        ).succeeded == 1

    _sync_academic_stage(
        mind,
        stage="junior",
        effective_at="2026-09-01T08:59:00+08:00",
    )
    stage_change = _prepare(
        mind,
        turn_id="golden-e-stage-change",
        occurred_at="2026-09-01T09:00:00+08:00",
        stage="junior",
    )
    mind.commit_turn(
        stage_change,
        CompanionTurnOutcome(
            visible_response="新学期好。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    assert _run_due_work(mind, now="2026-09-01T09:01:00+08:00").succeeded == 1
    before_forget = mind.project(
        CompanionProjectionRequest(
            subject=_subject("junior"),
            surface="operator",
            now="2026-09-01T09:02:00+08:00",
        )
    )
    assert before_forget.payload["active_adjustments"]
    assert before_forget.payload["chapters"]

    forgotten = mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject("junior"),
            payload={
                "evidence_id": committed[0].evidence_ids[0],
                "now": "2026-09-01T09:03:00+08:00",
                "idempotency_key": "golden-e-forget",
            },
        )
    )
    immediately_after = mind.project(
        CompanionProjectionRequest(
            subject=_subject("junior"),
            surface="operator",
            now="2026-09-01T09:03:01+08:00",
        )
    )
    recomputed = _run_due_work(mind, now="2026-09-01T09:04:00+08:00")
    after_recompute = mind.project(
        CompanionProjectionRequest(
            subject=_subject("junior"),
            surface="operator",
            now="2026-09-01T09:04:01+08:00",
        )
    )

    assert forgotten.forgotten == 1
    assert forgotten.requeued == 1
    assert immediately_after.payload["active_adjustments"] == ()
    assert immediately_after.payload["chapters"] == ()
    assert recomputed.succeeded == 2
    assert after_recompute.payload["active_adjustments"] == ()
    assert len(after_recompute.payload["chapters"]) == 1
    assert after_recompute.payload["chapters"][0]["version"] == 2


def test_golden_story_f_rejected_initiative_disables_during_repair(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"golden-story",
    )
    committed = _commit_feedback(
        mind,
        turn_id="golden-f-evidence",
        occurred_at="2026-07-18T10:00:00+08:00",
        signal={
            "kind": "meaningful_moment",
            "ownership_scope": "relationship",
            "content": {"outcome": "followup_worthwhile"},
            "source_summary": "存在有依据的后续陪伴机会。",
            "attribution": "observed_interaction",
            "confidence": 1.0,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )
    decision = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-18T12:00:00+08:00",
        )
    )
    mind.apply_control(
        CompanionControlCommand(
            action="record_initiative_feedback",
            subject=_subject(),
            payload={
                "decision_id": decision.payload["decision_id"],
                "outcome": "rejected",
                "now": "2026-07-18T12:05:00+08:00",
                "idempotency_key": "golden-f-rejected",
            },
        )
    )
    next_day = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-19T12:00:00+08:00",
        )
    )

    assert decision.payload["eligible"] is True
    assert decision.payload["evidence_ids"] == committed.evidence_ids
    assert next_day.payload == {
        "eligible": False,
        "reason_code": "disabled",
    }
