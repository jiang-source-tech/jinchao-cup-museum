from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from core.xiaoxin.companion import (
    CompanionIdempotencyConflict,
    CompanionMind,
    CompanionObservation,
    CompanionProjectionRequest,
    CompanionSubjectContext,
)
from core.xiaoxin.companion.store import CompanionStore
from core.xiaoxin.companion.observation_ingress import CompanionObservationIngress


def _subject(
    *,
    owner_user_id: str = "owner-1",
    pet_id: str = "pet-1",
    memory_subject_id: str = "subject-1",
    speaker_identity: str = "confirmed",
) -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id=owner_user_id,
        pet_id=pet_id,
        memory_subject_id=memory_subject_id,
        speaker_identity=speaker_identity,
        academic_stage="sophomore",
        persistence_allowed=speaker_identity == "confirmed",
    )


def _todo_observation(
    subject: CompanionSubjectContext,
    *,
    title: str = "完成 C 语言作业",
) -> CompanionObservation:
    return CompanionObservation(
        idempotency_key="todo-created:todo-1:2026-07-20T10:00:00+08:00",
        subject=subject,
        kind="todo_created",
        source_kind="miniprogram_todo",
        source_ref="todo-1",
        occurred_at="2026-07-20T10:00:00+08:00",
        payload={
            "todo_id": "todo-1",
            "title": title,
            "due_at": "2026-07-21T20:00:00+08:00",
            "status": "pending",
        },
        safe_summary="用户创建了一项未来待办。",
    )


def test_todo_created_observation_becomes_traceable_future_event_evidence(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"observation-test-secret",
    )
    subject = _subject()

    observed = mind.observe(_todo_observation(subject))

    projection = mind.project(
        CompanionProjectionRequest(
            subject=subject,
            surface="operator",
            now="2026-07-20T10:01:00+08:00",
        )
    )

    assert observed.status == "recorded"
    assert len(observed.evidence_ids) == 1
    evidence = projection.payload["evidence"]
    assert tuple(item["kind"] for item in evidence) == ("future_event",)
    diagnostics = projection.payload["diagnostics"]
    assert diagnostics["health"]["observations"] == 1
    assert diagnostics["observations"] == (
        {
            "observation_id": observed.observation_id,
            "kind": "todo_created",
            "source_kind": "miniprogram_todo",
            "source_ref": "todo-1",
            "safe_summary": "用户创建了一项未来待办。",
            "occurred_at": "2026-07-20T10:00:00+08:00",
            "status": "recorded",
            "evidence_ids": observed.evidence_ids,
        },
    )


def test_course_lifecycle_observations_replace_and_cancel_the_same_fact(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"course-observation-secret")
    subject = _subject()
    base_payload = {
        "course_id": "course-1",
        "title": "线性代数",
        "classroom": "一教 201",
        "teacher": "刘老师",
        "weekday": 2,
        "start_section": 3,
        "end_section": 4,
        "week_range": "1-18",
        "starts_at": "10:10",
        "ends_at": "11:45",
    }

    created = mind.observe(
        CompanionObservation(
            idempotency_key="course-created:course-1:v1",
            subject=subject,
            kind="course_created",
            source_kind="miniprogram_course",
            source_ref="course-1",
            occurred_at="2026-07-20T10:00:00+08:00",
            payload=base_payload,
            safe_summary="用户创建了一门课程。",
        )
    )
    updated = mind.observe(
        CompanionObservation(
            idempotency_key="course-updated:course-1:v2",
            subject=subject,
            kind="course_updated",
            source_kind="miniprogram_course",
            source_ref="course-1",
            occurred_at="2026-07-20T10:01:00+08:00",
            payload={**base_payload, "classroom": "一教 202"},
            safe_summary="用户更新了一门课程。",
        )
    )
    deleted = mind.observe(
        CompanionObservation(
            idempotency_key="course-deleted:course-1:v3",
            subject=subject,
            kind="course_deleted",
            source_kind="miniprogram_course",
            source_ref="course-1",
            occurred_at="2026-07-20T10:02:00+08:00",
            payload={**base_payload, "classroom": "一教 202"},
            safe_summary="用户删除了一门课程。",
        )
    )

    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT kind, fact_key, status, prompt_eligible,
                   json_extract(content_json, '$.classroom')
            FROM companion_evidence
            WHERE fact_key = 'course:course-1'
            ORDER BY occurred_at
            """
        ).fetchall()
        lineage_count = connection.execute(
            "SELECT COUNT(*) FROM observation_evidence"
        ).fetchone()[0]

    assert created.status == updated.status == deleted.status == "recorded"
    assert [tuple(row) for row in rows] == [
        ("future_event", "course:course-1", "superseded", 0, "一教 201"),
        ("future_event", "course:course-1", "superseded", 0, "一教 202"),
        (
            "future_event_cancelled",
            "course:course-1",
            "active",
            0,
            "一教 202",
        ),
    ]
    assert lineage_count == 3


@pytest.mark.parametrize(
    ("kind", "source_ref", "payload", "evidence_kind", "fact_key", "scope"),
    (
        (
            "goal_set",
            "goal-1",
            {"goal_id": "goal-1", "title": "通过英语六级", "status": "active"},
            "goal",
            "goal:goal-1",
            "user",
        ),
        (
            "future_event_set",
            "event-1",
            {
                "event_id": "event-1",
                "title": "参加英语六级考试",
                "scheduled_at": "2026-12-12T09:00:00+08:00",
                "status": "planned",
            },
            "future_event",
            "event:event-1",
            "user",
        ),
        (
            "boundary_set",
            "initiative_frequency",
            {
                "boundary_key": "initiative_frequency",
                "value": "low",
            },
            "boundary",
            "boundary:initiative_frequency",
            "user",
        ),
        (
            "companion_feedback",
            "feedback-1",
            {
                "feedback_id": "feedback-1",
                "signal": "helpful",
                "interaction_ref": "turn-1",
            },
            "accepted_help",
            "companion_feedback:feedback-1",
            "relationship",
        ),
    ),
)
def test_explicit_portrait_observations_create_deterministic_evidence(
    tmp_path, kind, source_ref, payload, evidence_kind, fact_key, scope
):
    store = CompanionStore(tmp_path / f"{kind}.db")
    mind = CompanionMind(store=store, token_secret=b"portrait-observation")

    result = mind.observe(
        CompanionObservation(
            idempotency_key=f"{kind}:{source_ref}:v1",
            subject=_subject(),
            kind=kind,
            source_kind="miniprogram_companion",
            source_ref=source_ref,
            occurred_at="2026-07-20T11:00:00+08:00",
            payload=payload,
            safe_summary="用户明确更新了一项陪伴资料。",
        )
    )

    with store.connection() as connection:
        evidence = connection.execute(
            """
            SELECT kind, fact_key, ownership_scope, status, prompt_eligible
            FROM companion_evidence WHERE evidence_id = ?
            """,
            (result.evidence_ids[0],),
        ).fetchone()
    assert tuple(evidence) == (evidence_kind, fact_key, scope, "active", 1)


def test_observation_replay_is_duplicate_and_conflicting_content_is_rejected(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"observation-test-secret",
    )
    subject = _subject()
    original = _todo_observation(subject)

    first = mind.observe(original)
    replay = mind.observe(original)

    assert replay.status == "duplicate"
    assert replay.observation_id == first.observation_id
    assert replay.evidence_ids == first.evidence_ids

    with pytest.raises(CompanionIdempotencyConflict):
        mind.observe(_todo_observation(subject, title="悄悄替换后的任务"))


def test_observation_private_writes_require_confirmed_subject_and_owner_match(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"observation-test-secret",
    )
    unknown = _subject(speaker_identity="unknown")

    skipped = mind.observe(_todo_observation(unknown))

    assert skipped.status == "not_persisted"
    confirmed = _subject()
    recorded = mind.observe(_todo_observation(confirmed))
    projection = mind.project(
        CompanionProjectionRequest(
            subject=confirmed,
            surface="operator",
            now="2026-07-20T10:01:00+08:00",
        )
    )
    assert recorded.status == "recorded"
    assert len(projection.payload["diagnostics"]["observations"]) == 1

    wrong_owner = _subject(owner_user_id="owner-2")
    conflicting_owner_observation = replace(
        _todo_observation(wrong_owner),
        idempotency_key="todo-created:todo-2",
        source_ref="todo-2",
        payload={
            "todo_id": "todo-2",
            "title": "越权事项",
            "due_at": "2026-07-21T20:00:00+08:00",
            "status": "pending",
        },
    )
    with pytest.raises(PermissionError, match="owner"):
        mind.observe(conflicting_owner_observation)


def test_concurrent_observations_remain_isolated_between_memory_subjects(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"subject-isolation")
    subject_a = _subject(memory_subject_id="subject-a")
    subject_b = _subject(memory_subject_id="subject-b")
    observation_a = replace(
        _todo_observation(subject_a),
        idempotency_key="todo-created:todo-a",
        source_ref="todo-a",
        payload={
            "todo_id": "todo-a",
            "title": "A 的待办",
            "due_at": "2026-07-21T20:00:00+08:00",
            "status": "pending",
        },
    )
    observation_b = replace(
        _todo_observation(subject_b),
        idempotency_key="todo-created:todo-b",
        source_ref="todo-b",
        payload={
            "todo_id": "todo-b",
            "title": "B 的待办",
            "due_at": "2026-07-22T20:00:00+08:00",
            "status": "pending",
        },
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(mind.observe, (observation_a, observation_b))
        )

    projections = tuple(
        mind.project(
            CompanionProjectionRequest(
                subject=subject,
                surface="operator",
                now="2026-07-20T10:01:00+08:00",
            )
        )
        for subject in (subject_a, subject_b)
    )
    assert all(result.status == "recorded" for result in results)
    assert tuple(
        projection.payload["diagnostics"]["observations"][0]["source_ref"]
        for projection in projections
    ) == ("todo-a", "todo-b")
    assert tuple(
        projection.payload["diagnostics"]["evidence_timeline"][0][
            "source_ref"
        ]
        for projection in projections
    ) == ("todo-a", "todo-b")


def test_todo_update_supersedes_previous_fact_and_preserves_lineage(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"observation-test-secret",
    )
    subject = _subject()
    created = mind.observe(_todo_observation(subject))
    updated_observation = replace(
        _todo_observation(subject),
        idempotency_key="todo-updated:todo-1:2026-07-20T11:00:00+08:00",
        kind="todo_updated",
        occurred_at="2026-07-20T11:00:00+08:00",
        payload={
            "todo_id": "todo-1",
            "title": "完成数据结构作业",
            "due_at": "2026-07-22T20:00:00+08:00",
            "status": "pending",
        },
        safe_summary="用户更新了一项未来待办。",
    )

    updated = mind.observe(updated_observation)
    projection = mind.project(
        CompanionProjectionRequest(
            subject=subject,
            surface="operator",
            now="2026-07-20T11:01:00+08:00",
        )
    )

    assert updated.status == "recorded"
    timeline = projection.payload["diagnostics"]["evidence_timeline"]
    assert tuple(item["status"] for item in timeline) == (
        "active",
        "superseded",
    )
    assert {item["fact_key"] for item in timeline} == {"todo:todo-1"}
    assert projection.payload["diagnostics"]["relations"] == (
        {
            "relation_id": projection.payload["diagnostics"]["relations"][0][
                "relation_id"
            ],
            "relation_kind": "superseded_by",
            "source_evidence_id": created.evidence_ids[0],
            "target_evidence_id": updated.evidence_ids[0],
            "created_at": projection.payload["diagnostics"]["relations"][0][
                "created_at"
            ],
        },
    )


def test_reminder_delivery_is_audited_without_claiming_user_completion(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"observation-test-secret",
    )
    subject = _subject()
    mind.observe(_todo_observation(subject))
    delivered = mind.observe(
        CompanionObservation(
            idempotency_key="reminder-delivered:delivery-1",
            subject=subject,
            kind="reminder_delivered",
            source_kind="todo_reminder_delivery",
            source_ref="delivery-1",
            occurred_at="2026-07-21T20:00:01+08:00",
            payload={
                "todo_id": "todo-1",
                "delivery_id": "delivery-1",
                "delivery_status": "delivered",
            },
            safe_summary="一项待办提醒已送达设备。",
        )
    )
    projection = mind.project(
        CompanionProjectionRequest(
            subject=subject,
            surface="operator",
            now="2026-07-21T20:01:00+08:00",
        )
    )

    assert delivered.status == "recorded"
    assert delivered.evidence_ids == ()
    assert tuple(
        item["kind"]
        for item in projection.payload["diagnostics"]["observations"]
    ) == ("reminder_delivered", "todo_created")
    assert all(
        item["kind"] not in {"followup_completed", "accepted_help"}
        for item in projection.payload["diagnostics"]["evidence_timeline"]
    )


def test_reminder_delivery_failure_is_audited_without_user_outcome_evidence(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"reminder-failure")
    subject = _subject()

    result = mind.observe(
        CompanionObservation(
            idempotency_key="reminder-delivery-failed:delivery-1",
            subject=subject,
            kind="reminder_delivery_failed",
            source_kind="todo_reminder_delivery",
            source_ref="delivery-1",
            occurred_at="2026-07-20T20:01:00+08:00",
            payload={
                "todo_id": "todo-1",
                "delivery_id": "delivery-1",
                "delivery_status": "failed",
                "failure_reason": "expired",
            },
            safe_summary="一项待办提醒投递失败。",
        )
    )

    projection = mind.project(
        CompanionProjectionRequest(
            subject=subject,
            surface="operator",
            now="2026-07-20T20:02:00+08:00",
        )
    )

    assert result.status == "recorded"
    assert result.evidence_ids == ()
    assert projection.payload["evidence"] == ()
    assert projection.payload["diagnostics"]["observations"][0]["kind"] == (
        "reminder_delivery_failed"
    )


def test_explicit_todo_completion_creates_relationship_evidence(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"observation-test-secret",
    )
    subject = _subject()
    created = mind.observe(_todo_observation(subject))
    completed_observation = replace(
        _todo_observation(subject),
        idempotency_key="todo-completed:todo-1:2026-07-21T19:00:00+08:00",
        kind="todo_completed",
        occurred_at="2026-07-21T19:00:00+08:00",
        payload={
            "todo_id": "todo-1",
            "title": "完成 C 语言作业",
            "due_at": "2026-07-21T20:00:00+08:00",
            "status": "done",
            "completion_source": "explicit_user_action",
        },
        safe_summary="用户明确完成了一项待办。",
    )

    completed = mind.observe(completed_observation)
    projection = mind.project(
        CompanionProjectionRequest(
            subject=subject,
            surface="operator",
            now="2026-07-21T19:01:00+08:00",
        )
    )

    timeline = projection.payload["diagnostics"]["evidence_timeline"]
    assert completed.status == "recorded"
    assert tuple((item["kind"], item["status"]) for item in timeline) == (
        ("followup_completed", "active"),
        ("future_event", "superseded"),
    )
    assert projection.payload["diagnostics"]["relations"][0][
        "source_evidence_id"
    ] == created.evidence_ids[0]
    assert projection.payload["diagnostics"]["relations"][0][
        "target_evidence_id"
    ] == completed.evidence_ids[0]


def test_observation_ingress_resolves_authenticated_user_to_confirmed_subject(tmp_path):
    subject_record = SimpleNamespace(
        id="subject-1",
        kind="user_speaker",
        merged_into_subject_id=None,
    )

    class IdentityStore:
        def get_personal_pet_for_user(self, user_id):
            assert user_id == "owner-1"
            return SimpleNamespace(id="pet-1")

        def list_memory_subjects_for_user(self, user_id):
            assert user_id == "owner-1"
            return [subject_record]

        def get_student_profile_for_user(self, user_id):
            assert user_id == "owner-1"
            return {"grade": "大二"}

    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"observation-test-secret",
    )
    ingress = CompanionObservationIngress(IdentityStore(), mind)

    result = ingress.observe_user_event(
        user_id="owner-1",
        idempotency_key="todo-created:todo-1",
        kind="todo_created",
        source_kind="miniprogram_todo",
        source_ref="todo-1",
        occurred_at="2026-07-20T10:00:00+08:00",
        payload={
            "todo_id": "todo-1",
            "title": "完成 C 语言作业",
            "due_at": "2026-07-21T20:00:00+08:00",
            "status": "pending",
        },
        safe_summary="用户创建了一项未来待办。",
    )

    assert result is not None
    projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-20T10:01:00+08:00",
        )
    )
    assert projection.xiaoxin_age == 2
    assert len(projection.payload["diagnostics"]["observations"]) == 1


def test_observation_ingress_defers_and_backfills_ambiguous_subject_events(tmp_path):
    class IdentityStore:
        def __init__(self):
            self.subject_ids = ["subject-1", "subject-2"]

        def get_personal_pet_for_user(self, user_id):
            return SimpleNamespace(id="pet-1")

        def list_memory_subjects_for_user(self, user_id):
            return [
                SimpleNamespace(
                    id=subject_id,
                    kind="user_speaker",
                    merged_into_subject_id=None,
                )
                for subject_id in self.subject_ids
            ]

        def get_student_profile_for_user(self, user_id):
            return {"grade": "大二"}

    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"observation-test-secret",
    )
    identity_store = IdentityStore()
    ingress = CompanionObservationIngress(identity_store, mind)

    result = ingress.observe_user_event(
        user_id="owner-1",
        idempotency_key="todo-created:todo-1",
        kind="todo_created",
        source_kind="miniprogram_todo",
        source_ref="todo-1",
        occurred_at="2026-07-20T10:00:00+08:00",
        payload={
            "todo_id": "todo-1",
            "title": "完成 C 语言作业",
            "due_at": "2026-07-21T20:00:00+08:00",
            "status": "pending",
        },
        safe_summary="用户创建了一项未来待办。",
    )

    assert result is not None
    assert result.status == "deferred"
    replay = ingress.observe_user_event(
        user_id="owner-1",
        idempotency_key="todo-created:todo-1",
        kind="todo_created",
        source_kind="miniprogram_todo",
        source_ref="todo-1",
        occurred_at="2026-07-20T10:00:00+08:00",
        payload={
            "todo_id": "todo-1",
            "title": "完成 C 语言作业",
            "due_at": "2026-07-21T20:00:00+08:00",
            "status": "pending",
        },
        safe_summary="用户创建了一项未来待办。",
    )
    assert replay is not None
    assert replay.status == "duplicate"
    with pytest.raises(CompanionIdempotencyConflict):
        ingress.observe_user_event(
            user_id="owner-1",
            idempotency_key="todo-created:todo-1",
            kind="todo_created",
            source_kind="miniprogram_todo",
            source_ref="todo-1",
            occurred_at="2026-07-20T10:00:00+08:00",
            payload={
                "todo_id": "todo-1",
                "title": "冲突内容",
                "due_at": "2026-07-21T20:00:00+08:00",
                "status": "pending",
            },
            safe_summary="用户创建了一项未来待办。",
        )
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-20T10:01:00+08:00",
        )
    )
    pending = operator.payload["diagnostics"]["pending_observations"]
    assert pending == (
        {
            "observation_id": result.observation_id,
            "kind": "todo_created",
            "source_kind": "miniprogram_todo",
            "source_ref": "todo-1",
            "safe_summary": "用户创建了一项未来待办。",
            "occurred_at": "2026-07-20T10:00:00+08:00",
            "queued_reason": "ambiguous_subject",
            "status": "pending",
            "attempt_count": 0,
            "last_error_code": None,
            "expires_at": pending[0]["expires_at"],
        },
    )
    assert str(pending[0]["expires_at"]).endswith("+00:00")
    assert operator.payload["diagnostics"]["health"][
        "pending_observations_by_status"
    ] == {"pending": 1}
    assert operator.payload["diagnostics"]["health"]["pending_observations"] == 1
    expired_operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2027-07-20T10:01:00+08:00",
        )
    )
    assert expired_operator.payload["diagnostics"]["pending_observations"][0][
        "status"
    ] == "expired"
    assert expired_operator.payload["diagnostics"]["health"][
        "pending_observations_by_status"
    ] == {"expired": 1}
    assert expired_operator.payload["diagnostics"]["health"][
        "pending_observations"
    ] == 0
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM pending_companion_observations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_observations"
        ).fetchone()[0] == 0

    identity_store.subject_ids = ["subject-1"]
    backfilled = ingress.flush_pending_for_user("owner-1")

    assert len(backfilled) == 1
    assert backfilled[0].status == "recorded"
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM pending_companion_observations"
        ).fetchone()[0] == 0
        observation = connection.execute(
            """
            SELECT memory_subject_id, kind FROM companion_observations
            """
        ).fetchone()
    assert tuple(observation) == ("subject-1", "todo_created")


def test_invalid_pending_observation_isolated_and_visible_after_bounded_retries(
    tmp_path,
):
    class IdentityStore:
        def __init__(self):
            self.subject_ids = ["subject-1", "subject-2"]

        def get_personal_pet_for_user(self, user_id):
            return SimpleNamespace(id="pet-1")

        def list_memory_subjects_for_user(self, user_id):
            return [
                SimpleNamespace(
                    id=subject_id,
                    kind="user_speaker",
                    merged_into_subject_id=None,
                )
                for subject_id in self.subject_ids
            ]

        def get_student_profile_for_user(self, user_id):
            return {"grade": "大二"}

    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"pending-failure")
    identity_store = IdentityStore()
    ingress = CompanionObservationIngress(identity_store, mind)
    deferred = ingress.observe_user_event(
        user_id="owner-1",
        idempotency_key="invalid-todo-created:todo-1",
        kind="todo_created",
        source_kind="miniprogram_todo",
        source_ref="todo-1",
        occurred_at="2026-07-20T10:00:00+08:00",
        payload={
            "todo_id": "todo-1",
            "due_at": "2026-07-21T20:00:00+08:00",
            "status": "pending",
        },
        safe_summary="用户创建了一项未来待办。",
    )
    assert deferred is not None and deferred.status == "deferred"
    identity_store.subject_ids = ["subject-1"]

    for _ in range(3):
        assert ingress.flush_pending_for_user("owner-1") == ()

    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-20T10:01:00+08:00",
        )
    )
    pending = operator.payload["diagnostics"]["pending_observations"]
    assert len(pending) == 1
    assert pending[0]["status"] == "failed"
    assert pending[0]["attempt_count"] == 3
    assert pending[0]["last_error_code"] == "CompanionContractError"
    assert "payload" not in pending[0]
    assert operator.payload["diagnostics"]["health"][
        "pending_observations_by_status"
    ] == {"failed": 1}


def test_todo_deletion_closes_future_fact_without_prompting_it(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"observation-test-secret",
    )
    subject = _subject()
    mind.observe(_todo_observation(subject))
    deleted_observation = replace(
        _todo_observation(subject),
        idempotency_key="todo-deleted:todo-1:2026-07-20T12:00:00+08:00",
        kind="todo_deleted",
        occurred_at="2026-07-20T12:00:00+08:00",
        payload={
            "todo_id": "todo-1",
            "title": "完成 C 语言作业",
            "due_at": "2026-07-21T20:00:00+08:00",
            "status": "deleted",
        },
        safe_summary="用户删除了一项待办。",
    )

    deleted = mind.observe(deleted_observation)
    projection = mind.project(
        CompanionProjectionRequest(
            subject=subject,
            surface="operator",
            now="2026-07-20T12:01:00+08:00",
        )
    )

    timeline = projection.payload["diagnostics"]["evidence_timeline"]
    assert deleted.status == "recorded"
    assert tuple((item["kind"], item["status"]) for item in timeline) == (
        ("future_event_cancelled", "active"),
        ("future_event", "superseded"),
    )
    assert timeline[0]["prompt_eligible"] is False


def test_deleting_completed_todo_does_not_erase_explicit_completion(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"observation-test-secret",
    )
    subject = _subject()
    mind.observe(_todo_observation(subject))
    completed = replace(
        _todo_observation(subject),
        idempotency_key="todo-completed:todo-1",
        kind="todo_completed",
        occurred_at="2026-07-21T19:00:00+08:00",
        payload={
            "todo_id": "todo-1",
            "title": "完成 C 语言作业",
            "due_at": "2026-07-21T20:00:00+08:00",
            "status": "done",
            "completion_source": "explicit_user_action",
        },
        safe_summary="用户明确完成了一项待办。",
    )
    mind.observe(completed)
    deleted = replace(
        completed,
        idempotency_key="todo-deleted:todo-1",
        kind="todo_deleted",
        occurred_at="2026-07-21T19:10:00+08:00",
        payload={
            **completed.payload,
            "status": "deleted",
            "previous_status": "done",
        },
        safe_summary="用户删除了一项已完成待办。",
    )

    deletion_result = mind.observe(deleted)
    projection = mind.project(
        CompanionProjectionRequest(
            subject=subject,
            surface="operator",
            now="2026-07-21T19:11:00+08:00",
        )
    )

    assert deletion_result.evidence_ids == ()
    timeline = projection.payload["diagnostics"]["evidence_timeline"]
    assert tuple((item["kind"], item["status"]) for item in timeline) == (
        ("followup_completed", "active"),
        ("future_event", "superseded"),
    )
