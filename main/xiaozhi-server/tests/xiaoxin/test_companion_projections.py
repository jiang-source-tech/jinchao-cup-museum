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


def _subject(
    memory_subject_id: str = "subject-1",
    academic_stage: str = "sophomore",
) -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id=memory_subject_id,
        speaker_identity="confirmed",
        academic_stage=academic_stage,
        persistence_allowed=True,
    )


class _SubjectScopedDerivationModel:
    def __init__(self) -> None:
        self.calls = []

    def reflect(self, request):
        self.calls.append(request)
        if request.job_kind == "academic_stage_changed":
            statements = tuple(
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
                    item.ownership_scope == ownership_scope for item in request.evidence
                )
            )
            return ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="阶段章节整理完成。",
                evidence_ids=tuple(item.evidence_id for item in request.evidence),
                chapter_statements=statements,
            )
        relationship_ids = tuple(
            item.evidence_id
            for item in request.evidence
            if item.ownership_scope == "relationship"
        )
        adjustment_value = (
            "expanded"
            if any(item.source_summary.startswith("B ") for item in request.evidence)
            else "short"
        )
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="相处方式整理完成。",
            evidence_ids=relationship_ids,
            adjustments=(
                (
                    AdjustmentProposal(
                        dimension="response_length",
                        value=adjustment_value,
                        scope="all",
                        evidence_ids=relationship_ids,
                        confidence=0.9,
                    ),
                )
                if relationship_ids
                else ()
            ),
        )


def _commit_subject_turn(
    mind: CompanionMind,
    *,
    memory_subject_id: str,
    academic_stage: str,
    turn_id: str,
    occurred_at: str,
    feedback_signals=(),
):
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=turn_id,
            subject=_subject(memory_subject_id, academic_stage),
            request_digest=f"digest-{turn_id}",
            surface="voice",
            occurred_at=occurred_at,
        )
    )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=feedback_signals,
        ),
    )
    return prepared, committed


def test_surfaces_share_age_and_relationship_stage_with_privacy_specific_payloads(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"projection-story",
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-projection-bootstrap",
            subject=_subject(),
            request_digest="digest-projection-bootstrap",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="你好。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    projections = {
        surface: mind.project(
            CompanionProjectionRequest(
                subject=_subject(),
                surface=surface,
                now="2026-07-18T10:01:00+08:00",
            )
        )
        for surface in ("voice", "miniprogram", "hardware")
    }

    assert {item.xiaoxin_age for item in projections.values()} == {2}
    assert {item.relationship_stage for item in projections.values()} == {
        "first_meeting"
    }
    assert set(projections["hardware"].payload) == {"hardware_expression"}
    assert "evidence" not in projections["hardware"].payload
    assert "narrative" not in projections["hardware"].payload
    assert "companion_summary" in projections["miniprogram"].payload
    assert "policy" not in projections["miniprogram"].payload


def test_miniprogram_and_operator_return_safe_traceable_metadata(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"projection-story")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-projection-evidence",
            subject=_subject(),
            request_digest="digest-projection-evidence",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "meaningful_moment",
                    "ownership_scope": "relationship",
                    "content": {"private_detail": "不得投影"},
                    "source_summary": "本轮形成了安全摘要。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )

    mini = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="miniprogram",
            now="2026-07-18T10:01:00+08:00",
        )
    )
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-18T10:01:00+08:00",
        )
    )

    assert set(mini.payload) == {
        "companion_summary",
        "learned_behaviors",
        "explicit_settings",
        "growth_moments_enabled",
        "companion_preferences",
        "available_controls",
    }
    preferences = mini.payload["companion_preferences"]
    assert set(preferences) == {"proactive_companionship", "past_reference"}
    assert preferences["proactive_companionship"]["pace"] in {"quiet", "natural"}
    assert preferences["past_reference"]["mode"] in {
        "never",
        "occasional",
        "natural",
    }
    assert operator.payload["pet_id"] == "pet-1"
    assert operator.payload["memory_subject_id"] == "subject-1"
    assert operator.payload["relationship_epoch_id"]
    assert operator.payload["jobs"][0]["job_id"] == committed.job_ids[0]
    assert set(operator.payload["jobs"][0]) == {
        "job_id",
        "job_kind",
        "status",
        "attempt",
        "model",
        "prompt_version",
        "schema_version",
        "failure_reason",
    }
    assert "private_detail" not in repr(mini.payload)
    assert committed.evidence_ids[0] not in repr(mini.payload)
    assert "private_detail" not in repr(operator.payload)


def test_miniprogram_projects_saved_companion_preferences_without_internal_terms(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"miniprogram-preferences")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-miniprogram-preferences",
            subject=_subject(),
            request_digest="digest-miniprogram-preferences",
            surface="voice",
            occurred_at="2026-08-03T21:00:00+08:00",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我在。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    for dimension, value, label, request_id in (
        ("initiative_level", "medium", "自然地主动陪伴", "preference-proactive"),
        (
            "memory_reference_depth",
            "shallow",
            "偶尔联系以前聊过的事",
            "preference-memory",
        ),
    ):
        mind.apply_control(
            CompanionControlCommand(
                action="set_interaction_contract",
                subject=_subject(),
                payload={
                    "dimension": dimension,
                    "value": value,
                    "scope": "all",
                    "safe_label": label,
                    "safe_scope": "所有场景",
                    "now": "2026-08-03T21:01:00+08:00",
                    "idempotency_key": request_id,
                },
            )
        )

    projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="miniprogram",
            now="2026-08-03T21:02:00+08:00",
        )
    )
    preferences = projection.payload["companion_preferences"]

    assert preferences["proactive_companionship"] == {
        "enabled": True,
        "pace": "natural",
        "setting_id": preferences["proactive_companionship"]["setting_id"],
    }
    assert preferences["proactive_companionship"]["setting_id"]
    assert preferences["past_reference"]["mode"] == "occasional"
    assert preferences["past_reference"]["setting_id"]
    assert "initiative_level" not in repr(projection.payload)
    assert "memory_reference_depth" not in repr(projection.payload)


def test_operator_diagnostics_include_inactive_evidence_timeline_only_for_operator(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"operator-diagnostics-story",
    )
    prepared, committed = _commit_subject_turn(
        mind,
        memory_subject_id="subject-1",
        academic_stage="sophomore",
        turn_id="turn-operator-diagnostics",
        occurred_at="2026-07-18T10:00:00+08:00",
        feedback_signals=(
            {
                "kind": "meaningful_moment",
                "ownership_scope": "relationship",
                "content": {"private_detail": "must not be projected"},
                "source_summary": "A safe operator summary.",
                "attribution": "observed_interaction",
                "confidence": 0.9,
                "retention": "long_term",
                "prompt_eligible": True,
            },
        ),
    )
    mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject(),
            payload={
                "evidence_id": committed.evidence_ids[0],
                "now": "2026-07-18T10:02:00+08:00",
                "idempotency_key": "forget-operator-diagnostics",
            },
        )
    )

    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-18T10:03:00+08:00",
        )
    )
    mini = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="miniprogram",
            now="2026-07-18T10:03:00+08:00",
        )
    )

    timeline = operator.payload["diagnostics"]["evidence_timeline"]
    forgotten = next(
        item for item in timeline if item["evidence_id"] == committed.evidence_ids[0]
    )
    assert forgotten == {
        "evidence_id": committed.evidence_ids[0],
        "ownership_scope": "relationship",
        "relationship_epoch_id": operator.payload["relationship_epoch_id"],
        "kind": "meaningful_moment",
        "source_kind": "turn",
        "source_ref": "turn-operator-diagnostics",
        "source_summary": "A safe operator summary.",
        "attribution": "observed_interaction",
        "confidence": 0.9,
        "occurred_at": "2026-07-18T10:00:00+08:00",
        "retention": "long_term",
        "status": "forgotten",
        "prompt_eligible": False,
        "expires_at": None,
        "fact_key": None,
        "importance": 0.5,
        "sensitivity": "private",
        "valid_from": "2026-07-18T10:00:00+08:00",
        "valid_until": None,
        "is_current_epoch": True,
    }
    assert "private_detail" not in repr(operator.payload)
    assert "diagnostics" not in mini.payload


def test_derived_policy_chapters_and_jobs_do_not_cross_memory_subjects(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = _SubjectScopedDerivationModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"subject-projection-story",
        reflection_model=model,
    )

    _commit_subject_turn(
        mind,
        memory_subject_id="subject-b",
        academic_stage="sophomore",
        turn_id="turn-subject-b-private-fact",
        occurred_at="2026-07-17T10:00:00+08:00",
        feedback_signals=(
            {
                "kind": "user_life_event",
                "ownership_scope": "user",
                "content": {"event": "subject_b_private_goal"},
                "source_summary": "B 的私人目标不得进入 A 的章节。",
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            },
        ),
    )
    for day in (18, 19, 20):
        _commit_subject_turn(
            mind,
            memory_subject_id="subject-a",
            academic_stage="freshman",
            turn_id=f"turn-subject-a-meaningful-{day}",
            occurred_at=f"2026-07-{day}T10:00:00+08:00",
            feedback_signals=(
                {
                    "kind": "meaningful_moment",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "helpful"},
                    "source_summary": f"A 在 {day} 日确认互动有帮助。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
                {
                    "kind": "interaction_feedback",
                    "ownership_scope": "relationship",
                    "content": {
                        "outcome": "helpful",
                        "behavior_key": "response_length",
                        "context_scope": "all",
                        "direction": "decrease",
                        "feedback_specificity": "behavior_and_context",
                        "source_reliability": "first_party_observed",
                        "claim_context": "direct",
                        "temporal_scope": "behavior_pattern",
                    },
                    "source_summary": f"A 在 {day} 日确认较短回复更合适。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        )
    assert _run_due_work(mind, now="2026-07-20T10:01:00+08:00").succeeded == 4

    mind.apply_control(
        CompanionControlCommand(
            action="sync_academic_stage",
            subject=_subject("subject-a", "sophomore"),
            payload={
                "now": "2026-09-01T08:59:00+08:00",
                "effective_at": "2026-09-01T08:59:00+08:00",
                "source_revision": 1,
            },
        )
    )
    _commit_subject_turn(
        mind,
        memory_subject_id="subject-a",
        academic_stage="sophomore",
        turn_id="turn-subject-a-stage-change",
        occurred_at="2026-09-01T09:00:00+08:00",
    )
    assert _run_due_work(mind, now="2026-09-01T09:01:00+08:00").succeeded == 1

    chapter_request = next(
        call for call in model.calls if call.job_kind == "academic_stage_changed"
    )
    assert all(
        "B 的私人目标" not in item.source_summary for item in chapter_request.evidence
    )

    _commit_subject_turn(
        mind,
        memory_subject_id="subject-b",
        academic_stage="sophomore",
        turn_id="turn-subject-b-candidate-adjustment",
        occurred_at="2026-09-01T10:00:00+08:00",
        feedback_signals=(
            {
                "kind": "meaningful_moment",
                "ownership_scope": "relationship",
                "content": {"outcome": "helpful"},
                "source_summary": "B 确认本轮互动有帮助。",
                "attribution": "observed_interaction",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            },
        ),
    )
    assert _run_due_work(mind, now="2026-09-01T10:01:00+08:00").succeeded == 1

    _, pending_a = _commit_subject_turn(
        mind,
        memory_subject_id="subject-a",
        academic_stage="sophomore",
        turn_id="turn-subject-a-pending-job",
        occurred_at="2026-09-02T09:00:00+08:00",
        feedback_signals=(
            {
                "kind": "meaningful_moment",
                "ownership_scope": "relationship",
                "content": {"outcome": "helpful"},
                "source_summary": "A 的待整理互动。",
                "attribution": "observed_interaction",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            },
        ),
    )
    _, pending_b = _commit_subject_turn(
        mind,
        memory_subject_id="subject-b",
        academic_stage="sophomore",
        turn_id="turn-subject-b-pending-job",
        occurred_at="2026-09-02T09:01:00+08:00",
        feedback_signals=(
            {
                "kind": "meaningful_moment",
                "ownership_scope": "relationship",
                "content": {"outcome": "helpful"},
                "source_summary": "B 的待整理互动。",
                "attribution": "observed_interaction",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            },
        ),
    )

    mini_a = mind.project(
        CompanionProjectionRequest(
            subject=_subject("subject-a", "sophomore"),
            surface="miniprogram",
            now="2026-09-02T09:02:00+08:00",
        )
    )
    mini_b = mind.project(
        CompanionProjectionRequest(
            subject=_subject("subject-b", "sophomore"),
            surface="miniprogram",
            now="2026-09-02T09:02:00+08:00",
        )
    )
    operator_b = mind.project(
        CompanionProjectionRequest(
            subject=_subject("subject-b", "sophomore"),
            surface="operator",
            now="2026-09-02T09:02:00+08:00",
        )
    )
    operator_a = mind.project(
        CompanionProjectionRequest(
            subject=_subject("subject-a", "sophomore"),
            surface="operator",
            now="2026-09-02T09:02:00+08:00",
        )
    )

    assert mini_a.payload["learned_behaviors"]
    assert all(
        "evidence_ids" not in item for item in mini_a.payload["learned_behaviors"]
    )
    assert "policy" not in mini_a.payload
    assert mini_b.payload["learned_behaviors"] == ()
    assert operator_a.payload["policy"]["response_length"] == "short"
    assert operator_b.payload["policy"]["response_length"] == "standard"
    operator_job_ids = {item["job_id"] for item in operator_b.payload["jobs"]}
    assert pending_b.job_ids[0] in operator_job_ids
    assert pending_a.job_ids[0] not in operator_job_ids
    diagnostics_a = operator_a.payload["diagnostics"]
    assert diagnostics_a["lineage"]["adjustments"]
    assert diagnostics_a["lineage"]["chapters"]
    assert all(item["evidence_ids"] for item in diagnostics_a["lineage"]["adjustments"])
    assert all(item["evidence_ids"] for item in diagnostics_a["lineage"]["chapters"])
    assert diagnostics_a["health"]["evidence_by_status"]["active"] >= 1
    assert diagnostics_a["health"]["jobs_by_status"]["pending"] >= 1
    assert all(
        "B " not in item["source_summary"]
        for item in diagnostics_a["evidence_timeline"]
    )
