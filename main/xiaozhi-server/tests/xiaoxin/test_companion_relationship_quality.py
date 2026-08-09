from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionMind,
    CompanionObservation,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.store import SCHEMA_VERSION, CompanionStore
from core.xiaoxin.companion.policy import (
    CompanionPolicyInputs,
    RelationshipQualityMetrics,
    build_companion_policy,
)


def _subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-relationship-v2",
        pet_id="pet-relationship-v2",
        memory_subject_id="subject-relationship-v2",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


def _other_subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-relationship-v2",
        pet_id="pet-relationship-v2",
        memory_subject_id="subject-relationship-v2-other",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


def _commit_chat(
    mind: CompanionMind,
    *,
    turn_id: str,
    occurred_at: str,
    subject: CompanionSubjectContext | None = None,
    feedback_signals=(),
) -> None:
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=turn_id,
            subject=subject or _subject(),
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
            delivery_status="generated",
            feedback_signals=feedback_signals,
        ),
    )


def _seed_familiar_timeline(mind: CompanionMind) -> None:
    _commit_chat(
        mind,
        turn_id="long-rhythm-day-1",
        occurred_at="2026-07-01T09:00:00+08:00",
        feedback_signals=(
            {
                "kind": "explicit_preference",
                "ownership_scope": "user",
                "content": {"preference": "concise_plans"},
                "source_summary": "用户明确偏好简洁计划。",
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            },
            {
                "kind": "goal",
                "ownership_scope": "user",
                "content": {"goal": "finish_term_project"},
                "source_summary": "用户明确记录了学期项目目标。",
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            },
            {
                "kind": "accepted_help",
                "ownership_scope": "relationship",
                "content": {"outcome": "helpful"},
                "source_summary": "用户确认本次帮助有效。",
                "attribution": "observed_interaction",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            },
            {
                "kind": "interaction_feedback",
                "ownership_scope": "relationship",
                "content": {"outcome": "helpful"},
                "source_summary": "用户确认当前相处方式合适。",
                "attribution": "observed_interaction",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            },
        ),
    )
    for day in (7, 13, 15):
        _commit_chat(
            mind,
            turn_id=f"long-rhythm-day-{day}",
            occurred_at=f"2026-07-{day:02d}T09:00:00+08:00",
        )


def _quality_signals(index: int):
    return (
        {
            "kind": "goal",
            "ownership_scope": "user",
            "content": {"goal": f"long_term_goal_{index}"},
            "source_summary": f"用户记录了长期目标 {index}。",
            "attribution": "explicit_user_statement",
            "confidence": 1.0,
            "retention": "long_term",
            "prompt_eligible": True,
        },
        {
            "kind": "accepted_help",
            "ownership_scope": "relationship",
            "content": {"outcome": "helpful", "sequence": index},
            "source_summary": f"用户确认第 {index} 次帮助有效。",
            "attribution": "observed_interaction",
            "confidence": 1.0,
            "retention": "long_term",
            "prompt_eligible": True,
        },
        {
            "kind": "interaction_feedback",
            "ownership_scope": "relationship",
            "content": {"outcome": "helpful", "sequence": index},
            "source_summary": f"用户确认第 {index} 次相处方式合适。",
            "attribution": "observed_interaction",
            "confidence": 1.0,
            "retention": "long_term",
            "prompt_eligible": True,
        },
    )


def _prepare_policy(mind: CompanionMind, *, turn_id: str, occurred_at: str):
    return mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=turn_id,
            subject=_subject(),
            request_digest=f"digest-{turn_id}",
            surface="voice",
            occurred_at=occurred_at,
        )
    ).policy


def test_real_sqlite_timeline_reaches_familiar_only_after_long_rhythm(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-long-rhythm-v1",
    )
    _seed_familiar_timeline(mind)

    policy = _prepare_policy(
        mind,
        turn_id="long-rhythm-stage-check",
        occurred_at="2026-07-15T10:00:00+08:00",
    )

    assert policy.relationship_stage == "familiar"
    assert policy.relationship_posture == "steady"
    assert policy.version == "companion-policy-v6"

    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-15T10:01:00+08:00",
        )
    )
    progress = operator.payload["diagnostics"]["relationship_stage_progress"]
    assert progress["policy_version"] == "companion-policy-v6"
    assert progress["current_stage"] == "familiar"
    assert progress["next_stage"] == "attuned"
    assert set(progress["gap_reason_codes"]) == {
        "minimum_relationship_span",
        "minimum_active_days",
        "minimum_active_weeks",
        "minimum_active_months",
        "minimum_reliable_knowledge",
        "minimum_helpfulness_days",
        "minimum_attunement_days",
        "minimum_recent_helpfulness_days",
    }


def test_future_turns_and_stage_events_do_not_change_as_of_policy(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-as-of-replay-v1",
    )
    _seed_familiar_timeline(mind)
    assert _prepare_policy(
        mind,
        turn_id="future-stage-record",
        occurred_at="2026-07-15T10:00:00+08:00",
    ).relationship_stage == "familiar"

    replayed = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-01T10:00:00+08:00",
        )
    )

    assert replayed.relationship_stage == "first_meeting"
    assert replayed.payload["policy"]["relationship_posture"] == "steady"
    assert tuple(
        event["relationship_stage"]
        for event in replayed.payload["diagnostics"]["relationship_stage_events"]
    ) == ("first_meeting",)


def test_concurrent_and_out_of_order_stage_events_never_downgrade(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"relationship-stage-monotonic-v1",
    )
    _commit_chat(
        mind,
        turn_id="monotonic-stage-bootstrap",
        occurred_at="2026-07-01T09:00:00+08:00",
    )
    epoch = store.get_active_epoch(
        owner_user_id=_subject().owner_user_id,
        pet_id=_subject().pet_id,
    )
    assert epoch is not None

    common = {
        "owner_user_id": _subject().owner_user_id,
        "pet_id": _subject().pet_id,
        "memory_subject_id": _subject().memory_subject_id,
        "relationship_epoch_id": epoch.epoch_id,
        "quality": {
            "continuity": 12,
            "knowledge": 5,
            "helpfulness": 4,
            "attunement": 3,
        },
        "reason_codes": ("multi_day_continuity",),
        "policy_version": "companion-policy-v5",
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                store.record_relationship_stage_event,
                **common,
                relationship_stage="familiar",
                now="2026-07-03T09:00:00+08:00",
            ),
            executor.submit(
                store.record_relationship_stage_event,
                **common,
                relationship_stage="attuned",
                now="2026-07-02T09:00:00+08:00",
            ),
        )
        for future in futures:
            future.result()
    store.record_relationship_stage_event(
        **common,
        relationship_stage="first_meeting",
        now="2026-07-04T09:00:00+08:00",
    )

    with store.connection() as connection:
        stages = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT relationship_stage
                FROM relationship_stage_events
                WHERE pet_id = ? AND memory_subject_id = ?
                  AND relationship_epoch_id = ?
                ORDER BY rowid
                """,
                (_subject().pet_id, _subject().memory_subject_id, epoch.epoch_id),
            )
        )
    stage_order = {
        "first_meeting": 0,
        "familiar": 1,
        "attuned": 2,
        "long_term_companion": 3,
    }
    assert stages[-1] == "attuned"
    assert tuple(stage_order[stage] for stage in stages) == tuple(
        sorted(stage_order[stage] for stage in stages)
    )


def test_attuned_and_long_term_reunion_thresholds_survive_restart(tmp_path):
    start = datetime.fromisoformat("2026-01-01T09:00:00+08:00")
    cases = (
        ("attuned", 60, ("familiar", "attuned")),
        (
            "long_term_companion",
            120,
            ("familiar", "attuned", "long_term_companion"),
        ),
    )
    for expected_stage, absence_days, promotions in cases:
        database_dir = tmp_path / expected_stage
        database_dir.mkdir()
        database_path = database_dir / "xiaoxin_companion.db"
        store = CompanionStore(database_path)
        mind = CompanionMind(
            store=store,
            token_secret=f"relationship-{expected_stage}-reunion-v1".encode(),
        )
        _commit_chat(
            mind,
            turn_id=f"{expected_stage}-reunion-bootstrap",
            occurred_at=start.isoformat(),
        )
        epoch = store.get_active_epoch(
            owner_user_id=_subject().owner_user_id,
            pet_id=_subject().pet_id,
        )
        assert epoch is not None
        for index, stage in enumerate(promotions, start=1):
            store.record_relationship_stage_event(
                owner_user_id=_subject().owner_user_id,
                pet_id=_subject().pet_id,
                memory_subject_id=_subject().memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                relationship_stage=stage,
                quality={
                    "continuity": 0,
                    "knowledge": 0,
                    "helpfulness": 0,
                    "attunement": 0,
                },
                reason_codes=(),
                policy_version="companion-policy-v5",
                now=(start + timedelta(minutes=index)).isoformat(),
            )

        mind = CompanionMind(
            store=CompanionStore(database_path),
            token_secret=f"relationship-{expected_stage}-reunion-v1".encode(),
        )
        before_threshold = _prepare_policy(
            mind,
            turn_id=f"{expected_stage}-before-reunion-threshold",
            occurred_at=(start + timedelta(days=absence_days - 1)).isoformat(),
        )
        at_threshold = _prepare_policy(
            mind,
            turn_id=f"{expected_stage}-at-reunion-threshold",
            occurred_at=(start + timedelta(days=absence_days)).isoformat(),
        )

        assert before_threshold.relationship_stage == expected_stage
        assert before_threshold.relationship_posture == "steady"
        assert at_threshold.relationship_stage == expected_stage
        assert at_threshold.relationship_posture == "reunion_cautious"
        assert at_threshold.relationship_adjustment_gain == 0.5


def test_monthly_low_frequency_relationships_eventually_reach_long_term(tmp_path):
    start = datetime.fromisoformat("2026-01-01T09:00:00+08:00")
    for label, interval_days in (("twice-monthly", 15), ("monthly", 31)):
        database_dir = tmp_path / label
        database_dir.mkdir()
        mind = CompanionMind(
            store=CompanionStore(database_dir / "xiaoxin_companion.db"),
            token_secret=f"relationship-{label}-v1".encode(),
        )
        occurred_at = start
        for index in range(36):
            occurred_at = start + timedelta(days=index * interval_days)
            _commit_chat(
                mind,
                turn_id=f"{label}-{index}",
                occurred_at=occurred_at.isoformat(),
                feedback_signals=_quality_signals(index),
            )

        policy = _prepare_policy(
            mind,
            turn_id=f"{label}-stage-check",
            occurred_at=(occurred_at + timedelta(hours=1)).isoformat(),
        )

        assert policy.relationship_stage == "long_term_companion"


def test_three_day_volume_burst_cannot_unlock_relationship_stage(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-three-day-burst-v1",
    )
    start = datetime.fromisoformat("2026-01-01T09:00:00+08:00")
    for day in range(3):
        for turn in range(10):
            _commit_chat(
                mind,
                turn_id=f"burst-{day}-{turn}",
                occurred_at=(
                    start + timedelta(days=day, minutes=turn)
                ).isoformat(),
                feedback_signals=(
                    _quality_signals(day * 10 + turn) if turn == 0 else ()
                ),
            )

    policy = _prepare_policy(
        mind,
        turn_id="burst-after-one-year",
        occurred_at=(start + timedelta(days=366)).isoformat(),
    )

    assert policy.relationship_stage == "first_meeting"


def test_cross_instance_turn_replay_counts_one_interaction(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    secret = b"relationship-cross-instance-v1"
    first_mind = CompanionMind(
        store=CompanionStore(database_path),
        token_secret=secret,
    )
    prepared = first_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="cross-instance-turn",
            subject=_subject(),
            request_digest="digest-cross-instance-turn",
            surface="voice",
            occurred_at="2026-07-01T09:00:00+08:00",
        )
    )
    outcome = CompanionTurnOutcome(
        visible_response="收到。",
        assistant_action="reply",
        delivery_status="generated",
    )
    first = first_mind.commit_turn(prepared, outcome)
    second_mind = CompanionMind(
        store=CompanionStore(database_path),
        token_secret=secret,
    )
    replay = second_mind.commit_turn(prepared, outcome)

    with CompanionStore(database_path).connection() as connection:
        turn_count = connection.execute(
            "SELECT COUNT(*) FROM companion_turns WHERE turn_id = ?",
            (prepared.turn_id,),
        ).fetchone()[0]
    assert first.status == "committed"
    assert replay.status == "already_committed"
    assert turn_count == 1


def test_forgetting_all_knowledge_preserves_stage_but_removes_memory_budget(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-forget-knowledge-v1",
    )
    _seed_familiar_timeline(mind)
    assert _prepare_policy(
        mind,
        turn_id="forget-knowledge-stage-record",
        occurred_at="2026-07-15T10:00:00+08:00",
    ).relationship_stage == "familiar"
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-15T10:01:00+08:00",
        )
    )
    knowledge = tuple(
        item
        for item in operator.payload["evidence"]
        if item["kind"] in {"explicit_preference", "goal"}
    )
    assert len(knowledge) == 2
    for index, item in enumerate(knowledge):
        mind.apply_control(
            CompanionControlCommand(
                action="forget_evidence",
                subject=_subject(),
                payload={
                    "evidence_id": item["evidence_id"],
                    "now": f"2026-07-15T10:0{index + 2}:00+08:00",
                    "idempotency_key": f"forget-relationship-knowledge-{index}",
                },
            )
        )

    after = _prepare_policy(
        mind,
        turn_id="forget-knowledge-policy-check",
        occurred_at="2026-07-15T10:05:00+08:00",
    )

    assert after.relationship_stage == "familiar"
    assert after.memory_reference_budget == 0


def test_reunion_and_repair_postures_survive_restart_and_recover(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    mind = CompanionMind(
        store=CompanionStore(database_path),
        token_secret=b"relationship-posture-v1",
    )
    _seed_familiar_timeline(mind)
    assert _prepare_policy(
        mind,
        turn_id="posture-record-familiar",
        occurred_at="2026-07-15T10:00:00+08:00",
    ).relationship_stage == "familiar"

    mind = CompanionMind(
        store=CompanionStore(database_path),
        token_secret=b"relationship-posture-v1",
    )
    absent = _prepare_policy(
        mind,
        turn_id="posture-return-preview",
        occurred_at="2026-08-14T10:00:00+08:00",
    )
    assert absent.relationship_stage == "familiar"
    assert absent.relationship_posture == "reunion_cautious"
    assert absent.relationship_adjustment_gain == 0.5
    assert absent.memory_reference_budget <= 1
    assert absent.initiative_level == "disabled"

    _commit_chat(
        mind,
        turn_id="posture-return-day-1",
        occurred_at="2026-08-14T10:05:00+08:00",
    )
    store = CompanionStore(database_path)
    epoch = store.get_active_epoch(
        owner_user_id=_subject().owner_user_id,
        pet_id=_subject().pet_id,
    )
    assert epoch is not None
    store.record_relationship_stage_event(
        owner_user_id=_subject().owner_user_id,
        pet_id=_subject().pet_id,
        memory_subject_id=_subject().memory_subject_id,
        relationship_epoch_id=epoch.epoch_id,
        relationship_stage="attuned",
        quality={
            "continuity": 12,
            "knowledge": 5,
            "helpfulness": 4,
            "attunement": 3,
        },
        reason_codes=("multi_day_continuity",),
        policy_version="companion-policy-v5",
        now="2026-08-14T10:06:00+08:00",
    )
    mind = CompanionMind(
        store=CompanionStore(database_path),
        token_secret=b"relationship-posture-v1",
    )
    after_promotion = _prepare_policy(
        mind,
        turn_id="posture-after-return-promotion",
        occurred_at="2026-08-14T10:07:00+08:00",
    )
    assert after_promotion.relationship_stage == "attuned"
    assert after_promotion.relationship_posture == "reunion_cautious"
    assert after_promotion.relationship_adjustment_gain == 0.5

    _commit_chat(
        mind,
        turn_id="posture-return-day-2",
        occurred_at="2026-08-15T10:05:00+08:00",
    )
    second_return = _prepare_policy(
        mind,
        turn_id="posture-return-day-2-check",
        occurred_at="2026-08-15T11:00:00+08:00",
    )
    assert second_return.relationship_posture == "reunion_cautious"
    assert second_return.relationship_adjustment_gain == 0.75

    _commit_chat(
        mind,
        turn_id="posture-return-day-3",
        occurred_at="2026-08-16T10:05:00+08:00",
    )
    recovered = _prepare_policy(
        mind,
        turn_id="posture-return-recovered",
        occurred_at="2026-08-16T11:00:00+08:00",
    )
    assert recovered.relationship_stage == "attuned"
    assert recovered.relationship_posture == "steady"
    assert recovered.relationship_adjustment_gain == 1.0

    _observe(
        mind,
        kind="companion_feedback",
        source_ref="posture-negative-feedback",
        occurred_at="2026-08-17T09:00:00+08:00",
        payload={
            "feedback_id": "posture-negative-feedback",
            "signal": "too_personal",
            "interaction_ref": "posture-return-recovered",
        },
        summary="用户明确表示这次表达过于私人。",
    )
    mind = CompanionMind(
        store=CompanionStore(database_path),
        token_secret=b"relationship-posture-v1",
    )
    repairing = _prepare_policy(
        mind,
        turn_id="posture-repairing",
        occurred_at="2026-08-17T09:01:00+08:00",
    )
    assert repairing.relationship_stage == "attuned"
    assert repairing.relationship_posture == "repairing"
    assert repairing.relationship_adjustment_gain == 0.0
    assert repairing.memory_reference_budget == 0
    assert repairing.initiative_level == "disabled"

    _observe(
        mind,
        kind="companion_feedback",
        source_ref="posture-positive-repair",
        occurred_at="2026-08-17T09:05:00+08:00",
        payload={
            "feedback_id": "posture-positive-repair",
            "signal": "helpful",
            "interaction_ref": "posture-repairing",
        },
        summary="用户明确确认调整后的表达有效。",
    )
    repaired = _prepare_policy(
        mind,
        turn_id="posture-repaired",
        occurred_at="2026-08-17T09:06:00+08:00",
    )
    assert repaired.relationship_stage == "attuned"
    assert repaired.relationship_posture == "steady"
    assert repaired.relationship_adjustment_gain == 1.0

    _observe(
        mind,
        kind="companion_feedback",
        source_ref="posture-second-negative-feedback",
        occurred_at="2026-08-18T09:00:00+08:00",
        payload={
            "feedback_id": "posture-second-negative-feedback",
            "signal": "not_helpful",
            "interaction_ref": "posture-repaired",
        },
        summary="用户明确表示本次帮助没有效果。",
    )
    for day in (19, 20):
        _commit_chat(
            mind,
            turn_id=f"posture-healthy-day-{day}",
            occurred_at=f"2026-08-{day:02d}T09:00:00+08:00",
        )
    still_repairing = _prepare_policy(
        mind,
        turn_id="posture-two-healthy-days",
        occurred_at="2026-08-20T10:00:00+08:00",
    )
    assert still_repairing.relationship_posture == "repairing"

    _commit_chat(
        mind,
        turn_id="posture-healthy-day-21",
        occurred_at="2026-08-21T09:00:00+08:00",
    )
    mind = CompanionMind(
        store=CompanionStore(database_path),
        token_secret=b"relationship-posture-v1",
    )
    healthy_recovery = _prepare_policy(
        mind,
        turn_id="posture-three-healthy-days",
        occurred_at="2026-08-21T10:00:00+08:00",
    )
    assert healthy_recovery.relationship_stage == "attuned"
    assert healthy_recovery.relationship_posture == "steady"
    assert healthy_recovery.relationship_adjustment_gain == 1.0


def _observe(
    mind: CompanionMind,
    *,
    kind: str,
    source_ref: str,
    occurred_at: str,
    payload: dict[str, object],
    summary: str,
) -> None:
    mind.observe(
        CompanionObservation(
            idempotency_key=f"relationship-v2:{kind}:{source_ref}",
            subject=_subject(),
            kind=kind,
            source_kind="relationship_v2_story",
            source_ref=source_ref,
            occurred_at=occurred_at,
            payload=payload,
            safe_summary=summary,
        )
    )


def _quality_ready_initiative(mind: CompanionMind) -> str:
    _commit_chat(
        mind,
        turn_id="initiative-quality-day-1",
        occurred_at="2026-07-20T09:00:00+08:00",
    )
    _observe(
        mind,
        kind="boundary_set",
        source_ref="initiative_level",
        occurred_at="2026-07-20T09:05:00+08:00",
        payload={"boundary_key": "initiative_level", "value": "low"},
        summary="用户允许低频主动陪伴。",
    )
    _observe(
        mind,
        kind="todo_completed",
        source_ref="todo-initiative-quality",
        occurred_at="2026-07-21T09:00:00+08:00",
        payload={
            "todo_id": "todo-initiative-quality",
            "title": "完成高数复习",
            "due_at": "2026-07-21T08:30:00+08:00",
            "status": "done",
            "completion_source": "explicit_user_action",
        },
        summary="用户明确完成了高数复习待办。",
    )
    _commit_chat(
        mind,
        turn_id="initiative-quality-day-2",
        occurred_at="2026-07-21T10:00:00+08:00",
    )
    _commit_chat(
        mind,
        turn_id="initiative-quality-day-3",
        occurred_at="2026-07-22T10:00:00+08:00",
    )
    before_feedback = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="initiative-quality-before-feedback",
            subject=_subject(),
            request_digest="digest-initiative-quality-before-feedback",
            surface="voice",
            occurred_at="2026-07-22T10:05:00+08:00",
        )
    )
    assert before_feedback.policy.relationship_stage == "first_meeting"
    initiative = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-22T10:10:00+08:00",
        )
    )
    assert initiative.payload["eligible"] is True
    return str(initiative.payload["decision_id"])


def test_real_observations_drive_familiar_stage_with_explainable_quality_event(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-quality-v2",
    )
    _commit_chat(
        mind,
        turn_id="relationship-day-1",
        occurred_at="2026-07-20T09:00:00+08:00",
    )
    _observe(
        mind,
        kind="boundary_set",
        source_ref="question_frequency",
        occurred_at="2026-07-20T09:05:00+08:00",
        payload={"boundary_key": "question_frequency", "value": "less"},
        summary="用户明确希望少追问。",
    )
    _observe(
        mind,
        kind="todo_completed",
        source_ref="todo-relationship-v2",
        occurred_at="2026-07-21T09:00:00+08:00",
        payload={
            "todo_id": "todo-relationship-v2",
            "title": "完成英语复习",
            "due_at": "2026-07-21T08:30:00+08:00",
            "status": "done",
            "completion_source": "explicit_user_action",
        },
        summary="用户明确完成了之前的英语复习待办。",
    )
    _observe(
        mind,
        kind="companion_feedback",
        source_ref="feedback-relationship-v2",
        occurred_at="2026-07-21T09:05:00+08:00",
        payload={
            "feedback_id": "feedback-relationship-v2",
            "signal": "helpful",
            "interaction_ref": "relationship-day-1",
        },
        summary="用户明确表示这次陪伴有帮助。",
    )
    _commit_chat(
        mind,
        turn_id="relationship-day-2",
        occurred_at="2026-07-21T10:00:00+08:00",
    )
    _commit_chat(
        mind,
        turn_id="relationship-day-3",
        occurred_at="2026-07-22T10:00:00+08:00",
    )

    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-22T11:00:01+08:00",
        )
    )

    assert operator.relationship_stage == "first_meeting"
    events = operator.payload["diagnostics"]["relationship_stage_events"]
    assert events[-1]["previous_stage"] is None
    assert events[-1]["relationship_stage"] == "first_meeting"
    assert set(events[-1]["quality"]) == {
        "continuity",
        "knowledge",
        "helpfulness",
        "attunement",
    }
    assert set(events[-1]["reason_codes"]) == {"multi_day_continuity"}


def test_latest_explicit_feedback_reduces_and_then_restores_companion_behavior(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-feedback-v2",
    )
    _commit_chat(
        mind,
        turn_id="feedback-bootstrap",
        occurred_at="2026-07-20T09:00:00+08:00",
    )
    _observe(
        mind,
        kind="boundary_set",
        source_ref="response_length",
        occurred_at="2026-07-20T09:05:00+08:00",
        payload={"boundary_key": "response_length", "value": "short"},
        summary="用户明确希望回答简短。",
    )
    _observe(
        mind,
        kind="companion_feedback",
        source_ref="feedback-too-personal",
        occurred_at="2026-07-20T09:10:00+08:00",
        payload={
            "feedback_id": "feedback-too-personal",
            "signal": "too_personal",
            "interaction_ref": "feedback-bootstrap",
        },
        summary="用户明确表示这次追问过于私人。",
    )

    reduced = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="feedback-reduced",
            subject=_subject(),
            request_digest="digest-feedback-reduced",
            surface="voice",
            occurred_at="2026-07-20T09:11:00+08:00",
        )
    )

    assert reduced.policy.relationship_stage == "first_meeting"
    assert reduced.policy.memory_reference_budget == 0
    assert reduced.policy.initiative_level == "disabled"

    _observe(
        mind,
        kind="companion_feedback",
        source_ref="feedback-helpful-after-boundary",
        occurred_at="2026-07-20T09:12:00+08:00",
        payload={
            "feedback_id": "feedback-helpful-after-boundary",
            "signal": "helpful",
            "interaction_ref": "feedback-reduced",
        },
        summary="用户明确表示调整后的陪伴有帮助。",
    )
    restored = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="feedback-restored",
            subject=_subject(),
            request_digest="digest-feedback-restored",
            surface="voice",
            occurred_at="2026-07-20T09:13:00+08:00",
        )
    )

    assert restored.policy.memory_reference_budget == 1
    assert restored.policy.initiative_level == "low"


def test_many_cross_day_chats_without_quality_outcomes_do_not_upgrade_stage(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-idle-chat-v2",
    )
    for day in range(1, 31):
        _commit_chat(
            mind,
            turn_id=f"idle-chat-{day}",
            occurred_at=f"2026-06-{day:02d}T09:00:00+08:00",
        )

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="idle-chat-after-30-days",
            subject=_subject(),
            request_digest="digest-idle-chat-after-30-days",
            surface="voice",
            occurred_at="2026-07-01T09:00:00+08:00",
        )
    )

    assert prepared.policy.relationship_stage == "first_meeting"
    assert prepared.policy.version == "companion-policy-v6"


def test_one_high_quality_day_cannot_skip_required_relationship_time():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="sophomore",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                turn_count=100,
                distinct_interaction_days=1,
                reliable_fact_count=100,
                effective_feedback_count=100,
                completed_followup_count=100,
                accepted_help_count=100,
            ),
        )
    )

    assert policy.relationship_stage == "first_meeting"


def test_accepted_initiative_feedback_is_a_real_attunement_input(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-initiative-accepted-v2",
    )
    decision_id = _quality_ready_initiative(mind)

    mind.apply_control(
        CompanionControlCommand(
            action="record_initiative_feedback",
            subject=_subject(),
            payload={
                "decision_id": decision_id,
                "outcome": "accepted",
                "now": "2026-07-22T10:11:00+08:00",
                "idempotency_key": "relationship-initiative-accepted",
            },
        )
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="initiative-quality-after-accepted",
            subject=_subject(),
            request_digest="digest-initiative-quality-after-accepted",
            surface="voice",
            occurred_at="2026-07-22T10:12:00+08:00",
        )
    )

    assert prepared.policy.relationship_stage == "first_meeting"


def test_rejected_initiative_feedback_does_not_reward_stage_and_reduces_initiative(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-initiative-rejected-v2",
    )
    decision_id = _quality_ready_initiative(mind)

    mind.apply_control(
        CompanionControlCommand(
            action="record_initiative_feedback",
            subject=_subject(),
            payload={
                "decision_id": decision_id,
                "outcome": "rejected",
                "now": "2026-07-22T10:11:00+08:00",
                "idempotency_key": "relationship-initiative-rejected",
            },
        )
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="initiative-quality-after-rejected",
            subject=_subject(),
            request_digest="digest-initiative-quality-after-rejected",
            surface="voice",
            occurred_at="2026-07-22T10:12:00+08:00",
        )
    )

    assert prepared.policy.relationship_stage == "first_meeting"
    assert prepared.policy.initiative_level == "disabled"


def test_latest_negative_feedback_overrides_more_aggressive_derived_adjustments():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="sophomore",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                turn_count=3,
                distinct_interaction_days=2,
                reliable_fact_count=1,
                effective_feedback_count=1,
                accepted_help_count=1,
                negative_feedback_count=1,
            ),
            active_adjustments={
                "question_frequency": "often",
                "initiative_level": "medium",
                "memory_reference_depth": "deep",
                "closure_style": "relational",
            },
            short_term_state={"last_relationship_feedback": "too_personal"},
        )
    )

    assert policy.relationship_stage == "familiar"
    assert policy.memory_reference_budget == 0
    assert policy.initiative_level == "disabled"
    assert policy.closure_style == "concise"


def test_schema_v9_migrates_to_v10_relationship_stage_audit(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    with store.connection() as connection:
        connection.execute("DROP TABLE relationship_stage_events")
        connection.execute("PRAGMA user_version = 9")
        connection.commit()

    migrated_store = CompanionStore(database_path)
    mind = CompanionMind(
        store=migrated_store,
        token_secret=b"relationship-schema-v10",
    )
    _commit_chat(
        mind,
        turn_id="relationship-v10-bootstrap",
        occurred_at="2026-07-20T09:00:00+08:00",
    )
    mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="relationship-v10-event",
            subject=_subject(),
            request_digest="digest-relationship-v10-event",
            surface="voice",
            occurred_at="2026-07-20T09:01:00+08:00",
        )
    )

    with migrated_store.connection() as connection:
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        event_count = connection.execute(
            "SELECT COUNT(*) FROM relationship_stage_events"
        ).fetchone()[0]
        index_count = connection.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'index'
              AND name = 'idx_relationship_stage_events_subject'
            """
        ).fetchone()[0]

    assert schema_version == SCHEMA_VERSION
    assert event_count == 1
    assert index_count == 1


def test_relationship_reset_starts_a_fresh_stage_audit_epoch(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-stage-reset-v2",
    )
    _commit_chat(
        mind,
        turn_id="relationship-reset-bootstrap",
        occurred_at="2026-07-20T09:00:00+08:00",
    )
    mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="relationship-reset-old-event",
            subject=_subject(),
            request_digest="digest-relationship-reset-old-event",
            surface="voice",
            occurred_at="2026-07-20T09:01:00+08:00",
        )
    )
    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-20T09:02:00+08:00",
        )
    )
    old_epoch_id = before.payload["relationship_epoch_id"]

    mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={
                "now": "2026-07-20T09:03:00+08:00",
                "idempotency_key": "relationship-stage-reset",
            },
        )
    )
    mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="relationship-reset-new-event",
            subject=_subject(),
            request_digest="digest-relationship-reset-new-event",
            surface="voice",
            occurred_at="2026-07-20T09:04:00+08:00",
        )
    )
    after = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-20T09:05:00+08:00",
        )
    )

    events = after.payload["diagnostics"]["relationship_stage_events"]
    assert after.relationship_stage == "first_meeting"
    assert after.payload["relationship_epoch_id"] != old_epoch_id
    assert len(events) == 1
    assert events[0]["relationship_epoch_id"] == after.payload["relationship_epoch_id"]
    assert events[0]["previous_stage"] is None


def test_personal_memory_purge_deletes_relationship_stage_audit(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"relationship-stage-purge-v2",
    )
    _commit_chat(
        mind,
        turn_id="relationship-purge-bootstrap",
        occurred_at="2026-07-20T09:00:00+08:00",
    )
    mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="relationship-purge-event",
            subject=_subject(),
            request_digest="digest-relationship-purge-event",
            surface="voice",
            occurred_at="2026-07-20T09:01:00+08:00",
        )
    )

    mind.apply_control(
        CompanionControlCommand(
            action="purge_personal_memory",
            subject=_subject(),
            payload={
                "now": "2026-07-20T09:02:00+08:00",
                "idempotency_key": "relationship-stage-purge",
            },
        )
    )

    with store.connection() as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) FROM relationship_stage_events"
        ).fetchone()[0]
    assert event_count == 0


def test_relationship_quality_and_stage_events_are_isolated_between_speakers(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-stage-subject-isolation-v2",
    )
    _commit_chat(
        mind,
        turn_id="relationship-own-bootstrap",
        occurred_at="2026-07-20T09:00:00+08:00",
    )
    mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="relationship-own-event",
            subject=_subject(),
            request_digest="digest-relationship-own-event",
            surface="voice",
            occurred_at="2026-07-20T09:01:00+08:00",
        )
    )
    _commit_chat(
        mind,
        turn_id="relationship-other-bootstrap",
        occurred_at="2026-07-20T09:02:00+08:00",
        subject=_other_subject(),
    )

    own = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-20T09:03:00+08:00",
        )
    )
    other = mind.project(
        CompanionProjectionRequest(
            subject=_other_subject(),
            surface="operator",
            now="2026-07-20T09:03:00+08:00",
        )
    )

    own_events = own.payload["diagnostics"]["relationship_stage_events"]
    other_events = other.payload["diagnostics"]["relationship_stage_events"]
    assert {item["memory_subject_id"] for item in own_events} == {
        _subject().memory_subject_id
    }
    assert {item["memory_subject_id"] for item in other_events} == {
        _other_subject().memory_subject_id
    }


def test_miniprogram_gets_boundary_and_stage_but_not_internal_quality_audit(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"relationship-miniprogram-projection-v2",
    )
    _commit_chat(
        mind,
        turn_id="relationship-mini-bootstrap",
        occurred_at="2026-07-20T09:00:00+08:00",
    )
    _observe(
        mind,
        kind="boundary_set",
        source_ref="question_frequency",
        occurred_at="2026-07-20T09:01:00+08:00",
        payload={"boundary_key": "question_frequency", "value": "less"},
        summary="用户明确希望少追问。",
    )
    mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="relationship-mini-stage-event",
            subject=_subject(),
            request_digest="digest-relationship-mini-stage-event",
            surface="voice",
            occurred_at="2026-07-20T09:02:00+08:00",
        )
    )

    projected = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="miniprogram",
            now="2026-07-20T09:03:00+08:00",
        )
    )

    assert projected.relationship_stage == "first_meeting"
    assert tuple(item["label"] for item in projected.payload["explicit_settings"]) == (
        "用户明确希望少追问。",
    )
    assert all(
        item["source"] == "你明确设置"
        for item in projected.payload["explicit_settings"]
    )
    assert "diagnostics" not in projected.payload
    assert "relationship_stage_events" not in repr(projected.payload)


def test_stage_audit_failure_does_not_roll_back_a_committed_conversation(
    tmp_path,
    monkeypatch,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"relationship-stage-audit-failure-v2",
    )

    def fail_stage_audit(**kwargs):
        raise OSError("simulated stage audit failure")

    monkeypatch.setattr(store, "record_relationship_stage_event", fail_stage_audit)

    _commit_chat(
        mind,
        turn_id="relationship-stage-audit-failure",
        occurred_at="2026-07-20T09:00:00+08:00",
    )

    with store.connection() as connection:
        turn_count = connection.execute(
            "SELECT COUNT(*) FROM companion_turns"
        ).fetchone()[0]
    assert turn_count == 1
