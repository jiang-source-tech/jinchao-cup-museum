from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Barrier

import pytest

from core.xiaoxin.companion import (
    AcademicState,
    CompanionControlCommand,
    CompanionContractError,
    CompanionIdempotencyConflict,
    CompanionMind,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
    age_expression_for_stage,
    normalize_academic_stage,
    require_academic_migration_selection,
    resolve_academic_transition,
    xiaoxin_age_for_stage,
)
from core.xiaoxin.companion.store import CompanionStore
from core.xiaoxin.prompts import build_system_messages


@pytest.mark.parametrize(
    ("raw_grade", "stage", "age"),
    [
        ("大一", "freshman", 1),
        (" 大二 ", "sophomore", 2),
        ("大三年级", "junior", 3),
        ("大四本科", "senior", 4),
        ("freshman", "freshman", 1),
        ("sophomore", "sophomore", 2),
        ("junior", "junior", 3),
        ("senior", "senior", 4),
        ("", "unknown", None),
        (None, "unknown", None),
        ("研究生", "unknown", None),
        ("2024级", "unknown", None),
        ("我大二了", "unknown", None),
    ],
)
def test_grade_is_the_only_xiaoxin_age_fact_source(raw_grade, stage, age):
    normalized = normalize_academic_stage(raw_grade)

    assert normalized == stage
    assert xiaoxin_age_for_stage(normalized) == age


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (
            "freshman",
            {
                "voice_cadence": "start_then_explore",
                "question_preference": "exploratory",
                "problem_organization": "action_seed",
                "memory_use": "concrete_cue",
                "initiative_posture": "light_invitation",
                "hardware_cadence": "quick_single",
            },
        ),
        (
            "sophomore",
            {
                "voice_cadence": "receive_then_next_step",
                "question_preference": "clarifying",
                "problem_organization": "bounded_plan",
                "memory_use": "progress_continuity",
                "initiative_posture": "contextual_followup",
                "hardware_cadence": "steady_sequence",
            },
        ),
        (
            "junior",
            {
                "voice_cadence": "conclusion_then_tradeoffs",
                "question_preference": "tradeoff",
                "problem_organization": "option_tradeoff",
                "memory_use": "evidence_comparison",
                "initiative_posture": "decision_point",
                "hardware_cadence": "deliberate_sequence",
            },
        ),
        (
            "senior",
            {
                "voice_cadence": "judgment_risk_then_close",
                "question_preference": "judgment_check",
                "problem_organization": "principle_risk",
                "memory_use": "revisable_long_view",
                "initiative_posture": "restrained_acknowledgement",
                "hardware_cadence": "restrained_single",
            },
        ),
        (
            "unknown",
            {
                "voice_cadence": "age_neutral",
                "question_preference": "age_neutral",
                "problem_organization": "age_neutral",
                "memory_use": "age_neutral",
                "initiative_posture": "age_neutral",
                "hardware_cadence": "age_neutral",
            },
        ),
    ],
)
def test_academic_stage_maps_to_complete_six_dimension_age_expression(
    stage,
    expected,
):
    assert asdict(age_expression_for_stage(stage)) == expected


def test_sophomore_first_use_is_age_two_but_still_first_meeting(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"academic-stage-test",
    )

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-sophomore-first",
            subject=CompanionSubjectContext(
                owner_user_id="owner-1",
                pet_id="pet-1",
                memory_subject_id="subject-1",
                speaker_identity="confirmed",
                academic_stage="sophomore",
                persistence_allowed=True,
            ),
            request_digest="digest-sophomore-first",
            surface="voice",
            occurred_at="2026-07-18T13:00:00+08:00",
        )
    )

    assert prepared.policy.xiaoxin_age == 2
    assert prepared.policy.relationship_stage == "first_meeting"
    assert prepared.used_evidence_ids == ()
    assert prepared.policy.age_expression.problem_organization == "bounded_plan"
    assert prepared.policy.memory_reference_budget == 0
    assert "invent_shared_history" in prepared.policy.prohibited_behaviors


def test_unknown_grade_keeps_xiaoxin_age_null(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"academic-stage-test",
    )

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-unknown-grade",
            subject=CompanionSubjectContext(
                owner_user_id="owner-1",
                pet_id="pet-1",
                memory_subject_id="subject-1",
                speaker_identity="confirmed",
                academic_stage="unknown",
                persistence_allowed=True,
            ),
            request_digest="digest-unknown-grade",
            surface="voice",
            occurred_at="2026-07-18T13:01:00+08:00",
        )
    )

    assert prepared.policy.xiaoxin_age is None
    assert set(asdict(prepared.policy.age_expression).values()) == {"age_neutral"}


def test_age_expression_prompt_is_structured_and_cannot_expand_permissions(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"academic-stage-prompt-v2",
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-age-expression-prompt",
            subject=_subject("senior"),
            request_digest="digest-age-expression-prompt",
            surface="voice",
            occurred_at="2026-07-18T13:02:00+08:00",
        )
    )
    messages = build_system_messages(
        "persona",
        "",
        "",
        {"reply_mode": "free_chat", "intent": "chat"},
        None,
        companion_policy=prepared.policy,
    )
    system_prompt = messages[0]["content"]

    assert '"problem_organization": "principle_risk"' in system_prompt
    assert '"memory_use": "revisable_long_view"' in system_prompt
    assert '"hardware_cadence": "restrained_single"' in system_prompt
    assert "不得据此增加篇幅、问题、记忆、主动、工具、知识或硬件强度" in system_prompt
    assert "voice_guidance" not in system_prompt

    hardware = mind.project(
        CompanionProjectionRequest(
            subject=_subject("senior"),
            surface="hardware",
            now="2026-07-18T13:03:00+08:00",
        )
    )
    assert set(hardware.payload) == {"hardware_expression"}
    assert hardware.payload["hardware_expression"] == {
        "intensity": "neutral",
        "cadence": "restrained_single",
    }


def test_structured_grade_change_records_system_evidence_without_resetting_epoch(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"academic-stage-test")
    sophomore = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    first = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-stage-1",
            subject=sophomore,
            request_digest="digest-stage-1",
            surface="voice",
            occurred_at="2026-07-18T13:10:00+08:00",
        )
    )
    mind.commit_turn(
        first,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
    )
    first_epoch = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    assert first_epoch is not None
    junior = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="junior",
        persistence_allowed=True,
    )
    mind.apply_control(
        CompanionControlCommand(
            action="sync_academic_stage",
            subject=junior,
            payload={
                "now": "2026-07-18T13:19:00+08:00",
                "effective_at": "2026-07-18T13:19:00+08:00",
                "source_revision": 1,
            },
        )
    )

    second = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-stage-2",
            subject=junior,
            request_digest="digest-stage-2",
            surface="voice",
            occurred_at="2026-07-18T13:20:00+08:00",
        )
    )
    mind.commit_turn(
        second,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
    )

    assert second.relationship_epoch_id == first_epoch.epoch_id
    assert second.policy.xiaoxin_age == 3
    with store.connection() as connection:
        stage_evidence = connection.execute(
            """
            SELECT status, content_json
            FROM companion_evidence
            WHERE kind = 'system_event'
              AND source_ref = 'identity:student_profile'
            ORDER BY occurred_at
            """
        ).fetchall()
        jobs = connection.execute(
            """
            SELECT job_kind, status
            FROM consolidation_jobs
            WHERE job_kind = 'academic_stage_changed'
            """
        ).fetchall()
        epoch_count = connection.execute(
            """
            SELECT COUNT(*) FROM relationship_epochs WHERE pet_id = 'pet-1'
            """
        ).fetchone()[0]

    assert [row["status"] for row in stage_evidence] == [
        "superseded",
        "active",
    ]
    assert '"academic_stage":"sophomore"' in stage_evidence[0]["content_json"]
    assert '"academic_stage":"junior"' in stage_evidence[1]["content_json"]
    assert [tuple(row) for row in jobs] == [("academic_stage_changed", "pending")]
    assert epoch_count == 1


def _subject(stage: str) -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage=stage,
        persistence_allowed=True,
    )


def _commit_turn(
    mind: CompanionMind,
    *,
    turn_id: str,
    stage: str,
    occurred_at: str,
    delivery_status: str = "delivered",
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
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status=delivery_status,
            feedback_signals=(
                {
                    "kind": "meaningful_moment",
                    "ownership_scope": "relationship",
                    "content": {"event": "一起完成了第一次校园问答"},
                    "source_summary": "我们一起完成了第一次校园问答。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    return prepared


def _advance_to_sophomore(mind: CompanionMind) -> None:
    mind.apply_control(
        CompanionControlCommand(
            action="sync_academic_stage",
            subject=_subject("sophomore"),
            payload={
                "now": "2027-09-01T08:00:00+08:00",
                "source_revision": 1,
            },
        )
    )


def _apply_academic_update(
    mind: CompanionMind,
    *,
    stage: str,
    revision: int,
    at: str,
    status: str = "active",
    transition_kind: str | None = None,
    clear_stage: bool = False,
):
    return mind.apply_control(
        CompanionControlCommand(
            action="sync_academic_stage",
            subject=_subject(stage),
            payload={
                "now": at,
                "effective_at": at,
                "source_revision": revision,
                "academic_status": status,
                "transition_kind": transition_kind,
                "clear_stage": clear_stage,
            },
        )
    )


@pytest.mark.parametrize(
    (
        "initial_stage",
        "target_stage",
        "status",
        "transition_kind",
        "clear_stage",
        "expected_stage",
        "expected_kind",
        "growth_eligible",
    ),
    [
        ("freshman", "junior", "active", None, False, "junior", "skip_advance", True),
        ("sophomore", "sophomore", "active", None, False, "sophomore", "same_stage", False),
        ("senior", "senior", "active", "major_change", False, "senior", "major_change", False),
        ("freshman", "unknown", "leave", None, False, "freshman", "leave", False),
        ("junior", "sophomore", "active", None, False, "sophomore", "regression", False),
        ("sophomore", "senior", "active", "correction", False, "senior", "correction", False),
        ("senior", "unknown", "graduated", None, False, "senior", "graduation", False),
        ("junior", "unknown", "active", None, True, "unknown", "explicit_clear", False),
    ],
)
def test_nonstandard_academic_transition_matrix_is_traceable_without_fake_stages(
    tmp_path,
    initial_stage,
    target_stage,
    status,
    transition_kind,
    clear_stage,
    expected_stage,
    expected_kind,
    growth_eligible,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"academic-matrix")
    _commit_turn(
        mind,
        turn_id="turn-initial",
        stage=initial_stage,
        occurred_at="2026-09-01T08:00:00+08:00",
    )

    _apply_academic_update(
        mind,
        stage=target_stage,
        revision=1,
        at="2027-09-01T08:00:00+08:00",
        status=status,
        transition_kind=transition_kind,
        clear_stage=clear_stage,
    )

    state = store.get_academic_state(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
    )
    transitions = store.list_academic_transitions(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
    )
    assert state is not None
    assert state["academic_stage"] == expected_stage
    assert state["academic_status"] == status
    assert state["source_revision"] == 1
    assert transitions[-1]["transition_kind"] == expected_kind
    assert transitions[-1]["from_stage"] == initial_stage
    assert transitions[-1]["to_stage"] == expected_stage
    assert transitions[-1]["growth_eligible"] is growth_eligible
    assert {item["to_stage"] for item in transitions} <= {
        initial_stage,
        expected_stage,
    }
    with store.connection() as connection:
        moment_count = connection.execute(
            "SELECT COUNT(*) FROM companion_growth_moments"
        ).fetchone()[0]
    expected_moment_count = int(
        expected_kind in {"skip_advance", "regression", "graduation"}
    )
    assert moment_count == expected_moment_count


def test_academic_revision_is_idempotent_rejects_conflict_and_ignores_old_update(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"academic-revision")
    _commit_turn(
        mind,
        turn_id="turn-revision-initial",
        stage="freshman",
        occurred_at="2026-09-01T08:00:00+08:00",
    )
    first = _apply_academic_update(
        mind,
        stage="junior",
        revision=2,
        at="2027-09-01T08:00:00+08:00",
    )
    duplicate = _apply_academic_update(
        mind,
        stage="junior",
        revision=2,
        at="2027-09-01T08:00:00+08:00",
    )
    stale = _apply_academic_update(
        mind,
        stage="sophomore",
        revision=1,
        at="2027-01-01T08:00:00+08:00",
    )

    assert first.status == "applied"
    assert duplicate.status == "already_applied"
    assert stale.status == "already_applied"
    with pytest.raises(CompanionIdempotencyConflict, match="source revision"):
        _apply_academic_update(
            mind,
            stage="senior",
            revision=2,
            at="2027-09-01T08:00:00+08:00",
        )
    with pytest.raises(ValueError, match="source_revision"):
        mind.apply_control(
            CompanionControlCommand(
                action="sync_academic_stage",
                subject=_subject("senior"),
                payload={"now": "2028-09-01T08:00:00+08:00"},
            )
        )
    _commit_turn(
        mind,
        turn_id="turn-stale-context",
        stage="senior",
        occurred_at="2028-09-02T08:00:00+08:00",
    )
    transitions = store.list_academic_transitions(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
    )
    assert [(item["source_revision"], item["to_stage"]) for item in transitions] == [
        (0, "freshman"),
        (2, "junior"),
    ]
    state = store.get_academic_state(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
    )
    assert state is not None
    assert state["academic_stage"] == "junior"


def test_temporary_unknown_profile_preserves_confirmed_age_and_projection(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"academic-unknown")
    _commit_turn(
        mind,
        turn_id="turn-unknown-initial",
        stage="junior",
        occurred_at="2026-09-01T08:00:00+08:00",
    )
    _apply_academic_update(
        mind,
        stage="unknown",
        revision=1,
        at="2026-10-01T08:00:00+08:00",
    )

    projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject("unknown"),
            surface="miniprogram",
            now="2026-10-01T08:01:00+08:00",
        )
    )
    state = store.get_academic_state(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
    )
    assert state is not None
    assert state["academic_stage"] == "junior"
    assert projection.xiaoxin_age == 3


def test_leave_and_same_stage_resume_preserve_pet_temperament_and_epoch(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"academic-leave")
    _commit_turn(
        mind,
        turn_id="turn-leave-initial",
        stage="sophomore",
        occurred_at="2026-09-01T08:00:00+08:00",
    )
    epoch_before = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    temperament_before = store.get_birth_temperament(
        owner_user_id="owner-1", pet_id="pet-1"
    )
    _apply_academic_update(
        mind,
        stage="unknown",
        status="leave",
        revision=1,
        at="2027-01-01T08:00:00+08:00",
    )
    _apply_academic_update(
        mind,
        stage="sophomore",
        status="active",
        transition_kind="resume",
        revision=2,
        at="2027-09-01T08:00:00+08:00",
    )

    epoch_after = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    temperament_after = store.get_birth_temperament(
        owner_user_id="owner-1", pet_id="pet-1"
    )
    transitions = store.list_academic_transitions(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
    )
    assert epoch_before == epoch_after
    assert temperament_before == temperament_after
    assert [item["transition_kind"] for item in transitions] == [
        "initialized",
        "leave",
        "resume",
    ]
    assert all(not item["growth_eligible"] for item in transitions)


def test_profile_correction_invalidates_pending_growth_without_new_moment(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"academic-correction")
    _commit_turn(
        mind,
        turn_id="turn-correction-initial",
        stage="freshman",
        occurred_at="2026-09-01T08:00:00+08:00",
    )
    _apply_academic_update(
        mind,
        stage="sophomore",
        revision=1,
        at="2027-09-01T08:00:00+08:00",
    )
    transition = store.list_academic_transitions(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
    )[-1]
    epoch = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    assert epoch is not None
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO companion_chapters(
                chapter_id, pet_id, relationship_epoch_id, academic_stage,
                xiaoxin_age, period_start, period_end, safe_narrative,
                status, version, created_at
            ) VALUES (
                'wrong-chapter', 'pet-1', ?, 'sophomore', 2,
                '2027-09-01T08:00:00+08:00', NULL, '错误阶段章节',
                'active', 1, '2027-09-01T08:00:00+08:00'
            )
            """,
            (epoch.epoch_id,),
        )
        connection.execute(
            """
            INSERT INTO chapter_evidence(chapter_id, evidence_id, pet_id)
            VALUES ('wrong-chapter', ?, 'pet-1')
            """,
            (transition["evidence_id"],),
        )
        connection.commit()
    _apply_academic_update(
        mind,
        stage="junior",
        transition_kind="correction",
        revision=2,
        at="2027-09-02T08:00:00+08:00",
    )

    with store.connection() as connection:
        moments = connection.execute(
            """
            SELECT metadata.lifecycle_status
            FROM companion_growth_moments AS moment
            JOIN companion_growth_moment_metadata AS metadata
              ON metadata.moment_id = moment.moment_id
            """
        ).fetchall()
        correction_jobs = connection.execute(
            """
            SELECT job_id, status FROM consolidation_jobs
            WHERE json_extract(payload_json, '$.source_revision') = 2
            """
        ).fetchall()
        pending_jobs = connection.execute(
            """
            SELECT job_id FROM consolidation_jobs
            WHERE status = 'pending' AND job_kind = 'academic_stage_changed'
            """
        ).fetchall()
        wrong_chapter = connection.execute(
            "SELECT status FROM companion_chapters WHERE chapter_id = 'wrong-chapter'"
        ).fetchone()
    assert [row["lifecycle_status"] for row in moments] == ["invalidated"]
    assert correction_jobs == []
    assert pending_jobs == []
    assert wrong_chapter is not None
    assert wrong_chapter["status"] == "invalidated"


def test_transition_kind_cannot_lie_and_graduation_is_frozen_without_correction():
    freshman = AcademicState(
        stage="freshman",
        status="active",
        effective_at="2026-09-01T08:00:00+08:00",
        source_revision=1,
    )
    with pytest.raises(ValueError, match="conflicts with state change"):
        resolve_academic_transition(
            previous=freshman,
            stage="junior",
            status="active",
            effective_at="2027-09-01T08:00:00+08:00",
            source_revision=2,
            requested_kind="advance",
        )

    graduated = AcademicState(
        stage="senior",
        status="graduated",
        effective_at="2030-06-30T08:00:00+08:00",
        source_revision=8,
    )
    with pytest.raises(ValueError, match="requires correction or migration"):
        resolve_academic_transition(
            previous=graduated,
            stage="senior",
            status="active",
            effective_at="2030-07-01T08:00:00+08:00",
            source_revision=9,
        )

    correction = resolve_academic_transition(
        previous=graduated,
        stage="senior",
        status="active",
        effective_at="2030-07-01T08:00:00+08:00",
        source_revision=9,
        requested_kind="correction",
    )
    assert correction.kind == "correction"
    assert correction.growth_eligible is False


def test_account_migration_requires_verified_explicit_pet_and_no_profile_conflict():
    with pytest.raises(ValueError, match="explicit pet selection"):
        require_academic_migration_selection(
            candidate_pet_ids=("pet-a", "pet-b"),
            selected_pet_id=None,
            same_person_verified=True,
            has_academic_conflict=False,
        )
    with pytest.raises(PermissionError, match="same-person"):
        require_academic_migration_selection(
            candidate_pet_ids=("pet-a",),
            selected_pet_id="pet-a",
            same_person_verified=False,
            has_academic_conflict=False,
        )
    with pytest.raises(ValueError, match="profile conflict"):
        require_academic_migration_selection(
            candidate_pet_ids=("pet-a", "pet-b"),
            selected_pet_id="pet-a",
            same_person_verified=True,
            has_academic_conflict=True,
        )
    assert require_academic_migration_selection(
        candidate_pet_ids=("pet-a", "pet-b"),
        selected_pet_id="pet-b",
        same_person_verified=True,
        has_academic_conflict=False,
    ) == "pet-b"


def test_v16_migration_backfills_authoritative_state_from_active_stage_evidence(
    tmp_path,
):
    db_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(db_path)
    mind = CompanionMind(store=store, token_secret=b"academic-v16-backfill")
    _commit_turn(
        mind,
        turn_id="turn-v16-backfill",
        stage="senior",
        occurred_at="2026-09-01T08:00:00+08:00",
    )
    with store.connection() as connection:
        connection.execute("DELETE FROM companion_academic_states")
        connection.execute("DELETE FROM companion_academic_transitions")
        connection.execute("PRAGMA user_version = 15")
        connection.commit()

    migrated = CompanionStore(db_path)
    state = migrated.get_academic_state(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
    )
    transitions = migrated.list_academic_transitions(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
    )

    assert state is not None
    assert state["academic_stage"] == "senior"
    assert state["source_revision"] == 0
    assert len(transitions) == 1
    assert transitions[0]["source_kind"] == "legacy_backfill"


def test_freshman_to_sophomore_projects_one_traceable_growth_moment_to_all_surfaces(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"academic-growth-test")
    freshman = _commit_turn(
        mind,
        turn_id="turn-freshman",
        stage="freshman",
        occurred_at="2026-09-01T08:00:00+08:00",
    )
    _advance_to_sophomore(mind)

    projections = {
        surface: mind.project(
            CompanionProjectionRequest(
                subject=_subject("sophomore"),
                surface=surface,
                now="2027-09-01T08:01:00+08:00",
            )
        )
        for surface in ("voice", "miniprogram", "hardware")
    }
    moments = [projection.payload["growth_moment"] for projection in projections.values()]

    epoch = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    assert epoch is not None
    assert {projection.xiaoxin_age for projection in projections.values()} == {2}
    assert {moment["moment_id"] for moment in moments} == {moments[0]["moment_id"]}
    assert moments[0]["from_stage"] == "freshman"
    assert moments[0]["to_stage"] == "sophomore"
    assert moments[0]["xiaoxin_age"] == 2
    assert moments[0]["relationship_epoch_id"] == epoch.epoch_id
    assert moments[0]["evidence_id"]
    assert "大一" in moments[0]["safe_summary"]
    assert "大二" in moments[0]["safe_summary"]
    assert "2岁" in moments[0]["safe_summary"]
    assert len(moments[0]["safe_summary"].encode("utf-8")) <= 63


def test_growth_moment_is_expressed_once_and_delivery_failure_can_retry(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"academic-growth-test",
    )
    _commit_turn(
        mind,
        turn_id="turn-freshman",
        stage="freshman",
        occurred_at="2026-09-01T08:00:00+08:00",
    )
    _advance_to_sophomore(mind)

    failed = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-growth-failed",
            subject=_subject("sophomore"),
            request_digest="digest-growth-failed",
            surface="voice",
            occurred_at="2027-09-01T08:02:00+08:00",
        )
    )
    assert failed.growth_moment is not None
    mind.commit_turn(
        failed,
        CompanionTurnOutcome(
            visible_response="新学年见。",
            assistant_action="reply",
            delivery_status="delivery_failed",
        ),
    )

    retried = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-growth-retry",
            subject=_subject("sophomore"),
            request_digest="digest-growth-retry",
            surface="voice",
            occurred_at="2027-09-01T08:03:00+08:00",
        )
    )
    assert retried.growth_moment is not None
    assert retried.growth_moment["moment_id"] == failed.growth_moment["moment_id"]
    mind.commit_turn(
        retried,
        CompanionTurnOutcome(
            visible_response="从大一到大二，我也长到2岁了。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    later = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-growth-later",
            subject=_subject("sophomore"),
            request_digest="digest-growth-later",
            surface="voice",
            occurred_at="2027-09-01T08:04:00+08:00",
        )
    )
    assert later.growth_moment is None


def test_forgetting_stage_change_evidence_hides_and_prevents_claiming_growth_moment(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"academic-growth-test",
    )
    _commit_turn(
        mind,
        turn_id="turn-freshman",
        stage="freshman",
        occurred_at="2026-09-01T08:00:00+08:00",
    )
    _advance_to_sophomore(mind)
    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject("sophomore"),
            surface="miniprogram",
            now="2027-09-01T08:01:00+08:00",
        )
    )
    evidence_id = before.payload["growth_moment"]["evidence_id"]

    mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject("sophomore"),
            payload={
                "evidence_id": evidence_id,
                "now": "2027-09-01T08:02:00+08:00",
                "idempotency_key": "forget-growth-source",
            },
        )
    )

    after = mind.project(
        CompanionProjectionRequest(
            subject=_subject("sophomore"),
            surface="miniprogram",
            now="2027-09-01T08:03:00+08:00",
        )
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-growth-forget",
            subject=_subject("sophomore"),
            request_digest="digest-after-growth-forget",
            surface="voice",
            occurred_at="2027-09-01T08:03:00+08:00",
        )
    )

    assert "growth_moment" not in after.payload
    assert prepared.growth_moment is None


def test_growth_moment_has_a_dedicated_prompt_block():
    growth_moment = {
        "moment_id": "growth-1",
        "from_stage": "freshman",
        "to_stage": "sophomore",
        "xiaoxin_age": 2,
        "safe_summary": "从大一到大二，小芯现在2岁。",
        "occurred_at": "2027-09-01T08:00:00+08:00",
        "relationship_epoch_id": "epoch-1",
        "evidence_id": "evidence-1",
    }

    messages = build_system_messages(
        "persona",
        "普通记忆",
        "",
        {"reply_mode": "free_chat", "intent": "chat"},
        None,
        growth_moment=growth_moment,
    )
    system_prompt = messages[0]["content"]

    assert "<growth_moment>" in system_prompt
    assert "</growth_moment>" in system_prompt
    assert '"moment_id": "growth-1"' in system_prompt
    assert "本轮自然表达一次" in system_prompt


def test_growth_moment_claim_is_atomic_and_part_of_the_prepared_token(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"academic-growth-test",
    )
    _commit_turn(
        mind,
        turn_id="turn-freshman",
        stage="freshman",
        occurred_at="2026-09-01T08:00:00+08:00",
    )
    _advance_to_sophomore(mind)
    barrier = Barrier(2)

    def prepare(index: int):
        barrier.wait()
        return mind.prepare_turn(
            CompanionTurnRequest(
                turn_id=f"turn-growth-concurrent-{index}",
                subject=_subject("sophomore"),
                request_digest=f"digest-growth-concurrent-{index}",
                surface="voice",
                occurred_at="2027-09-01T08:02:00+08:00",
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        prepared = tuple(executor.map(prepare, (1, 2)))

    claimed = tuple(item for item in prepared if item.growth_moment is not None)
    assert len(claimed) == 1
    forged = replace(
        claimed[0],
        growth_moment={**claimed[0].growth_moment, "safe_summary": "伪造成长"},
    )
    with pytest.raises(CompanionContractError, match="prepared token"):
        mind.commit_turn(
            forged,
            CompanionTurnOutcome(
                visible_response="伪造",
                assistant_action="reply",
                delivery_status="delivered",
            ),
        )


def test_growth_windows_controls_and_claim_gates_do_not_replay(tmp_path, monkeypatch):
    academic_store = CompanionStore(tmp_path / "academic.db")
    academic_mind = CompanionMind(
        store=academic_store,
        token_secret=b"academic-window",
    )
    _commit_turn(
        academic_mind,
        turn_id="academic-window-bootstrap",
        stage="freshman",
        occurred_at="2026-01-01T08:00:00+08:00",
    )
    _apply_academic_update(
        academic_mind,
        stage="sophomore",
        revision=1,
        at="2026-02-01T08:00:00+08:00",
    )
    academic_expired = academic_mind.project(
        CompanionProjectionRequest(
            subject=_subject("sophomore"),
            surface="miniprogram",
            now="2026-03-04T08:00:00+08:00",
        )
    )
    assert "growth_moment" not in academic_expired.payload
    with academic_store.connection() as connection:
        academic_lifecycle = connection.execute(
            "SELECT lifecycle_status FROM companion_growth_moment_metadata"
        ).fetchone()[0]
    assert academic_lifecycle == "expired"

    graduation_store = CompanionStore(tmp_path / "graduation.db")
    graduation_mind = CompanionMind(
        store=graduation_store,
        token_secret=b"graduation-window",
    )
    _commit_turn(
        graduation_mind,
        turn_id="graduation-window-bootstrap",
        stage="senior",
        occurred_at="2026-01-01T08:00:00+08:00",
    )
    _apply_academic_update(
        graduation_mind,
        stage="senior",
        status="graduated",
        revision=1,
        at="2026-02-01T08:00:00+08:00",
    )
    active = graduation_mind.project(
        CompanionProjectionRequest(
            subject=_subject("senior"),
            surface="miniprogram",
            now="2026-05-01T08:00:00+08:00",
        )
    ).payload["growth_moment"]
    assert active["primary_kind"] == "graduation"
    assert active["mode"] == "boundary_only"
    assert active["xiaoxin_age"] == 4

    graduation_expired = graduation_mind.project(
        CompanionProjectionRequest(
            subject=_subject("senior"),
            surface="miniprogram",
            now="2026-05-03T08:00:00+08:00",
        )
    )
    assert "growth_moment" not in graduation_expired.payload

    control_store = CompanionStore(tmp_path / "controls.db")
    control_mind = CompanionMind(
        store=control_store,
        token_secret=b"growth-controls",
    )
    _commit_turn(
        control_mind,
        turn_id="growth-controls-bootstrap",
        stage="freshman",
        occurred_at="2026-01-01T08:00:00+08:00",
    )
    _apply_academic_update(
        control_mind,
        stage="sophomore",
        revision=1,
        at="2026-02-01T08:00:00+08:00",
    )
    reserved = control_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="growth-controls-reserved",
            subject=_subject("sophomore"),
            request_digest="digest-growth-controls-reserved",
            surface="voice",
            occurred_at="2026-02-01T08:01:00+08:00",
        )
    )
    assert reserved.growth_moment is not None
    disabled = control_mind.apply_control(
        CompanionControlCommand(
            action="set_growth_moments_enabled",
            subject=_subject("sophomore"),
            payload={
                "enabled": False,
                "now": "2026-02-01T08:02:00+08:00",
                "idempotency_key": "disable-growth-moments",
            },
        )
    )
    assert disabled.deactivated == 1
    _apply_academic_update(
        control_mind,
        stage="junior",
        revision=2,
        at="2026-03-01T08:00:00+08:00",
    )
    control_mind.commit_turn(
        reserved,
        CompanionTurnOutcome(
            visible_response="关闭后旧投影安全结束。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    control_mind.apply_control(
        CompanionControlCommand(
            action="set_growth_moments_enabled",
            subject=_subject("junior"),
            payload={
                "enabled": True,
                "now": "2026-03-01T08:01:00+08:00",
                "idempotency_key": "enable-growth-moments",
            },
        )
    )
    reopened = control_mind.project(
        CompanionProjectionRequest(
            subject=_subject("junior"),
            surface="miniprogram",
            now="2026-03-01T08:02:00+08:00",
        )
    )
    assert "growth_moment" not in reopened.payload
    _apply_academic_update(
        control_mind,
        stage="senior",
        revision=3,
        at="2026-04-01T08:00:00+08:00",
    )
    new_after_reopen = control_mind.project(
        CompanionProjectionRequest(
            subject=_subject("senior"),
            surface="miniprogram",
            now="2026-04-01T08:01:00+08:00",
        )
    )
    assert new_after_reopen.payload["growth_moment"]["to_stage"] == "senior"
    _story_device_action_does_not_claim_a_growth_moment(tmp_path)
    for posture in ("reunion_cautious", "repairing"):
        _story_nonsteady_relationship_posture_does_not_claim_growth_moment(
            tmp_path,
            monkeypatch,
            posture,
        )


def _story_device_action_does_not_claim_a_growth_moment(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"growth-device-gate",
    )
    _commit_turn(
        mind,
        turn_id="device-gate-bootstrap",
        stage="freshman",
        occurred_at="2026-01-01T08:00:00+08:00",
    )
    _apply_academic_update(
        mind,
        stage="sophomore",
        revision=1,
        at="2026-02-01T08:00:00+08:00",
    )
    device = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="device-gate-action",
            subject=_subject("sophomore"),
            request_digest="digest-device-gate-action",
            surface="voice",
            interaction_kind="device_action",
            occurred_at="2026-02-01T08:01:00+08:00",
        )
    )
    conversation = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="device-gate-conversation",
            subject=_subject("sophomore"),
            request_digest="digest-device-gate-conversation",
            surface="voice",
            interaction_kind="conversation",
            occurred_at="2026-02-01T08:02:00+08:00",
        )
    )
    assert device.growth_moment is None
    assert conversation.growth_moment is not None


def _story_nonsteady_relationship_posture_does_not_claim_growth_moment(
    tmp_path,
    monkeypatch,
    posture,
):
    import core.xiaoxin.companion.mind as mind_module

    mind = CompanionMind(
        store=CompanionStore(tmp_path / f"{posture}.db"),
        token_secret=b"growth-posture-gate",
    )
    _commit_turn(
        mind,
        turn_id=f"{posture}-bootstrap",
        stage="freshman",
        occurred_at="2026-01-01T08:00:00+08:00",
    )
    _apply_academic_update(
        mind,
        stage="sophomore",
        revision=1,
        at="2026-02-01T08:00:00+08:00",
    )
    original = mind_module.build_companion_policy

    def build_with_posture(inputs):
        return replace(original(inputs), relationship_posture=posture)

    with monkeypatch.context() as scoped:
        scoped.setattr(mind_module, "build_companion_policy", build_with_posture)
        prepared = mind.prepare_turn(
            CompanionTurnRequest(
                turn_id=f"{posture}-claim",
                subject=_subject("sophomore"),
                request_digest=f"digest-{posture}-claim",
                surface="voice",
                occurred_at="2026-02-01T08:01:00+08:00",
            )
        )
    assert prepared.policy.relationship_posture == posture
    assert prepared.growth_moment is None
