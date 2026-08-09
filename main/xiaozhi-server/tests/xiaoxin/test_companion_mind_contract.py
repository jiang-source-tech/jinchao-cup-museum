from __future__ import annotations

from dataclasses import replace
from inspect import Parameter, iscoroutinefunction, signature
from typing import cast

import pytest

from core.xiaoxin.companion import (
    CompanionContractError,
    CompanionControlCommand,
    CompanionEvidence,
    CompanionExpressionStyle,
    CompanionMind,
    CompanionPolicy,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
    CompanionWorkResult,
)
from core.xiaoxin.companion.store import CompanionStore


def test_prepare_turn_projects_persisted_birth_temperament_into_policy(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    store.ensure_subject(
        owner_user_id="owner-style",
        pet_id="pet-vector-1",
        started_at="2026-07-25T09:00:00+08:00",
    )
    mind = CompanionMind(store=store, token_secret=b"style-contract-secret")

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-style",
            subject=CompanionSubjectContext(
                owner_user_id="owner-style",
                pet_id="pet-vector-1",
                memory_subject_id="subject-style",
                speaker_identity="confirmed",
                academic_stage="sophomore",
                persistence_allowed=True,
            ),
            request_digest="digest-style",
            surface="voice",
            occurred_at="2026-07-25T09:01:00+08:00",
        )
    )

    assert prepared.policy.expression_style == CompanionExpressionStyle(
        exploration_orientation="balanced",
        expression_energy="natural",
        thought_organization="structured",
        humor_level="none",
        initiative_bias="reserved",
    )


def test_project_projects_persisted_birth_temperament_into_policy(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    store.ensure_subject(
        owner_user_id="owner-style-project",
        pet_id="pet-vector-1",
        started_at="2026-07-25T09:00:00+08:00",
    )
    mind = CompanionMind(store=store, token_secret=b"style-contract-secret")
    subject = CompanionSubjectContext(
        owner_user_id="owner-style-project",
        pet_id="pet-vector-1",
        memory_subject_id="subject-style-project",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    projection = mind.project(
        CompanionProjectionRequest(
            subject=subject,
            surface="operator",
            now="2026-07-25T09:01:00+08:00",
        )
    )

    assert projection.payload["policy"]["expression_style"] == {
        "exploration_orientation": "balanced",
        "expression_energy": "natural",
        "thought_organization": "structured",
        "humor_level": "none",
        "initiative_bias": "reserved",
    }


def test_companion_package_exposes_one_deep_module_interface():
    from core.xiaoxin import companion

    assert companion.CompanionMind is not None
    assert companion.CompanionTurnRequest is not None
    assert companion.PreparedCompanionTurn is not None
    assert companion.CompanionTurnOutcome is not None
    assert companion.CompanionControlCommand is not None
    assert companion.CompanionProjectionRequest is not None
    assert companion.CompanionSubjectContext is not None
    assert "CompanionStore" not in companion.__all__


def test_unknown_speaker_prepares_a_private_memory_free_turn():
    mind = CompanionMind()
    request = CompanionTurnRequest(
        turn_id="turn-unknown-1",
        subject=CompanionSubjectContext(
            owner_user_id="owner-1",
            pet_id="pet-1",
            memory_subject_id="subject-unknown",
            speaker_identity="unknown",
            academic_stage="sophomore",
            persistence_allowed=False,
        ),
        request_digest="digest-1",
        surface="voice",
        occurred_at="2026-07-18T10:00:00+08:00",
    )

    prepared = mind.prepare_turn(request)

    assert prepared.used_evidence_ids == ()
    assert prepared.prompt_context == ()
    assert prepared.policy.xiaoxin_age is None
    assert prepared.policy.relationship_stage == "first_meeting"
    assert prepared.policy.question_budget == 0
    assert prepared.policy.memory_reference_budget == 0


def test_commit_rejects_a_prepared_token_and_digest_mismatch():
    mind = CompanionMind(token_secret=b"contract-test-secret")
    request = CompanionTurnRequest(
        turn_id="turn-unknown-2",
        subject=CompanionSubjectContext(
            owner_user_id="owner-1",
            pet_id="pet-1",
            memory_subject_id="subject-unknown",
            speaker_identity="unknown",
            academic_stage="unknown",
            persistence_allowed=False,
        ),
        request_digest="digest-original",
        surface="voice",
        occurred_at="2026-07-18T10:01:00+08:00",
    )
    prepared = mind.prepare_turn(request)
    tampered = replace(prepared, request_digest="digest-tampered")

    with pytest.raises(CompanionContractError, match="prepared token"):
        mind.commit_turn(
            tampered,
            CompanionTurnOutcome(
                visible_response="你好",
                assistant_action="reply",
                delivery_status="delivered",
            ),
        )


def test_commit_rejects_tampered_policy_and_used_evidence_audit():
    mind = CompanionMind(token_secret=b"contract-test-secret")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-unknown-audit-tamper",
            subject=CompanionSubjectContext(
                owner_user_id="owner-1",
                pet_id="pet-1",
                memory_subject_id="subject-unknown",
                speaker_identity="unknown",
                academic_stage="unknown",
                persistence_allowed=False,
            ),
            request_digest="digest-audit-original",
            surface="voice",
            occurred_at="2026-07-18T10:01:30+08:00",
            interaction_kind="explicit_recall",
        )
    )
    outcome = CompanionTurnOutcome(
        visible_response="你好",
        assistant_action="reply",
        delivery_status="delivered",
    )
    tampered_values = (
        replace(
            prepared,
            policy=replace(prepared.policy, version="forged-policy-v9"),
        ),
        replace(prepared, used_evidence_ids=("forged-evidence",)),
        replace(prepared, prompt_context=("forged prompt context",)),
        replace(prepared, interaction_kind="general_qa"),
        replace(prepared, surface="hardware"),
        replace(prepared, persistence_allowed=True),
        replace(
            prepared,
            policy=replace(
                prepared.policy,
                hardware_expression={"intensity": "forged"},
            ),
        ),
        replace(
            prepared,
            policy=replace(
                prepared.policy,
                expression_style=replace(
                    prepared.policy.expression_style,
                    expression_energy="lively",
                ),
            ),
        ),
    )

    for tampered in tampered_values:
        with pytest.raises(CompanionContractError, match="prepared token"):
            mind.commit_turn(tampered, outcome)


def test_unknown_speaker_commit_performs_zero_private_writes():
    mind = CompanionMind(token_secret=b"contract-test-secret")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-unknown-3",
            subject=CompanionSubjectContext(
                owner_user_id="owner-1",
                pet_id="pet-1",
                memory_subject_id="subject-unknown",
                speaker_identity="unknown",
                academic_stage="junior",
                persistence_allowed=False,
            ),
            request_digest="digest-3",
            surface="voice",
            occurred_at="2026-07-18T10:02:00+08:00",
        )
    )

    result = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
    )

    assert result.status == "not_persisted"
    assert result.evidence_ids == ()
    assert result.job_ids == ()


def test_relationship_evidence_requires_a_relationship_epoch():
    with pytest.raises(ValueError, match="relationship_epoch_id"):
        CompanionEvidence(
            evidence_id="evidence-1",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            ownership_scope="relationship",
            relationship_epoch_id=None,
            kind="meaningful_moment",
            content={"theme": "roommate_relationship"},
            source_kind="turn",
            source_ref="turn-1",
            source_summary="用户说明这次沟通有帮助。",
            attribution="explicit_user_statement",
            confidence=1.0,
            occurred_at="2026-07-18T10:03:00+08:00",
            retention="long_term",
            status="active",
            prompt_eligible=True,
        )


@pytest.mark.parametrize("status", ["forgotten", "superseded", "expired"])
def test_inactive_evidence_cannot_be_prompt_eligible(status: str):
    with pytest.raises(ValueError, match="prompt_eligible"):
        CompanionEvidence(
            evidence_id=f"evidence-{status}",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            ownership_scope="user",
            relationship_epoch_id=None,
            kind="explicit_preference",
            content={"theme": "c_language", "preference": "short_answers"},
            source_kind="turn",
            source_ref="turn-2",
            source_summary="用户明确要求简短回答。",
            attribution="explicit_user_statement",
            confidence=1.0,
            occurred_at="2026-07-18T10:04:00+08:00",
            retention="long_term",
            status=status,
            prompt_eligible=True,
        )


def test_external_contracts_reject_malformed_boundary_values():
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )

    invalid_factories = [
        lambda: CompanionTurnRequest(
            turn_id="",
            subject=subject,
            request_digest="digest",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        ),
        lambda: CompanionTurnRequest(
            turn_id="turn-1",
            subject=subject,
            request_digest="digest",
            surface=cast(object, "screen"),
            occurred_at="2026-07-18T10:00:00+08:00",
        ),
        lambda: CompanionTurnRequest(
            turn_id="turn-1",
            subject=subject,
            request_digest="digest",
            surface="voice",
            occurred_at="2026-07-18T10:00:00",
        ),
        lambda: CompanionPolicy(
            xiaoxin_age=5,
            relationship_stage="first_meeting",
            response_length="standard",
            question_budget=0,
            memory_reference_budget=0,
            initiative_level="disabled",
            emotional_posture="neutral",
            closure_style="concise",
        ),
        lambda: CompanionControlCommand(
            action=cast(object, "set_relationship_stage"),
            subject=subject,
        ),
        lambda: CompanionProjectionRequest(
            subject=subject,
            surface=cast(object, "filesystem"),
            now="2026-07-18T10:00:00+08:00",
        ),
        lambda: CompanionWorkResult(claimed=-1),
        lambda: CompanionEvidence(
            evidence_id="evidence-naive-time",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            ownership_scope="user",
            relationship_epoch_id=None,
            kind="profile_fact",
            content={"value": "example"},
            source_kind="turn",
            source_ref="turn-1",
            source_summary="安全摘要",
            attribution="explicit_user_statement",
            confidence=1.0,
            occurred_at="2026-07-18T10:00:00",
            retention="long_term",
            status="active",
            prompt_eligible=True,
        ),
        lambda: CompanionEvidence(
            evidence_id="evidence-invalid-status",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            ownership_scope="user",
            relationship_epoch_id=None,
            kind="profile_fact",
            content={"value": "example"},
            source_kind="turn",
            source_ref="turn-1",
            source_summary="安全摘要",
            attribution="explicit_user_statement",
            confidence=1.0,
            occurred_at="2026-07-18T10:00:00+08:00",
            retention="long_term",
            status="trusted",
            prompt_eligible=True,
        ),
    ]

    for factory in invalid_factories:
        with pytest.raises(ValueError):
            factory()


def test_companion_mind_exposes_only_the_six_planned_operations():
    public_methods = {
        name
        for name in dir(CompanionMind)
        if not name.startswith("_") and callable(getattr(CompanionMind, name))
    }
    assert public_methods == {
        "prepare_turn",
        "commit_turn",
        "observe",
        "apply_control",
        "project",
        "run_due_work",
    }
    assert list(signature(CompanionMind.prepare_turn).parameters) == [
        "self",
        "request",
    ]
    assert list(signature(CompanionMind.commit_turn).parameters) == [
        "self",
        "prepared",
        "outcome",
    ]
    due_parameters = signature(CompanionMind.run_due_work).parameters
    assert due_parameters["now"].kind is Parameter.KEYWORD_ONLY
    assert due_parameters["limit"].kind is Parameter.KEYWORD_ONLY
    assert due_parameters["limit"].default == 20
    assert iscoroutinefunction(CompanionMind.run_due_work)


def test_different_life_themes_use_the_same_evidence_contract():
    evidence = [
        CompanionEvidence(
            evidence_id=f"evidence-{theme}",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            ownership_scope="user",
            relationship_epoch_id=None,
            kind="explicit_preference",
            content={"theme": theme, "preference": "short_answers"},
            source_kind="turn",
            source_ref=f"turn-{theme}",
            source_summary="用户明确表达了回答偏好。",
            attribution="explicit_user_statement",
            confidence=1.0,
            occurred_at="2026-07-18T10:05:00+08:00",
            retention="long_term",
            status="active",
            prompt_eligible=True,
        )
        for theme in ("c_language", "roommate_relationship")
    ]

    assert {item.kind for item in evidence} == {"explicit_preference"}
    assert {item.content["theme"] for item in evidence} == {
        "c_language",
        "roommate_relationship",
    }


def test_confirmed_subject_commits_through_the_injected_sqlite_store(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"contract-test-secret")
    request = CompanionTurnRequest(
        turn_id="turn-confirmed-1",
        subject=CompanionSubjectContext(
            owner_user_id="owner-1",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            speaker_identity="confirmed",
            academic_stage="freshman",
            persistence_allowed=True,
        ),
        request_digest="digest-confirmed-1",
        surface="voice",
        occurred_at="2026-07-18T10:06:00+08:00",
    )

    prepared = mind.prepare_turn(request)
    result = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
    )

    assert result.status == "committed"
    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT memory_subject_id, occurred_at, relationship_epoch_id,
                   policy_version
            FROM companion_turns
            WHERE turn_id = 'turn-confirmed-1' AND pet_id = 'pet-1'
            """
        ).fetchone()
    assert row["memory_subject_id"] == "subject-1"
    assert row["occurred_at"] == "2026-07-18T10:06:00+08:00"
    assert row["relationship_epoch_id"]
    assert row["policy_version"] == "companion-policy-v6"


def test_prepare_turn_does_not_create_private_state_before_visible_reply(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"contract-test-secret")

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-prepare-only",
            subject=CompanionSubjectContext(
                owner_user_id="owner-1",
                pet_id="pet-1",
                memory_subject_id="subject-1",
                speaker_identity="confirmed",
                academic_stage="sophomore",
                persistence_allowed=True,
            ),
            request_digest="digest-prepare-only",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )

    assert prepared.policy.expression_style == CompanionExpressionStyle(
        exploration_orientation="balanced",
        expression_energy="natural",
        thought_organization="balanced",
        humor_level="low",
        initiative_bias="reserved",
    )
    with store.connection() as connection:
        counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "companion_pets",
                "relationship_epochs",
                "companion_evidence",
                "consolidation_jobs",
                "companion_birth_temperaments",
            )
        )

    assert counts == (0, 0, 0, 0, 0)


def test_mind_commit_persists_validated_turn_signals_and_job_idempotently(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"contract-test-secret")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-with-signal",
            subject=CompanionSubjectContext(
                owner_user_id="owner-1",
                pet_id="pet-1",
                memory_subject_id="subject-1",
                speaker_identity="confirmed",
                academic_stage="sophomore",
                persistence_allowed=True,
            ),
            request_digest="digest-with-signal",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    outcome = CompanionTurnOutcome(
        visible_response="记住了。",
        assistant_action="acknowledge_preference",
        delivery_status="delivered",
        feedback_signals=(
            {
                "kind": "explicit_preference",
                "ownership_scope": "user",
                "content": {"response_length": "short"},
                "source_summary": "用户明确偏好简短回答。",
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            },
        ),
    )

    first = mind.commit_turn(prepared, outcome)
    retry = mind.commit_turn(prepared, outcome)

    assert first.status == "committed"
    assert retry.status == "already_committed"
    assert first.evidence_ids == retry.evidence_ids
    assert first.job_ids == retry.job_ids
    assert len(first.evidence_ids) == 1
    assert len(first.job_ids) == 1
    with store.connection() as connection:
        evidence = connection.execute(
            """
            SELECT ownership_scope, relationship_epoch_id, kind, status
            FROM companion_evidence
            WHERE evidence_id = ?
            """,
            (first.evidence_ids[0],),
        ).fetchone()
        job = connection.execute(
            """
            SELECT job_kind, status, relationship_epoch_id
            FROM consolidation_jobs
            WHERE job_id = ?
            """,
            (first.job_ids[0],),
        ).fetchone()

    assert tuple(evidence) == ("user", None, "explicit_preference", "active")
    assert job["job_kind"] == "session_consolidation"
    assert job["status"] == "pending"
    assert job["relationship_epoch_id"]
