from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionEvidence,
    CompanionMind,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.store import (
    CompanionIdempotencyConflict,
    CompanionStore,
)


def _subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


def _prepared(mind: CompanionMind):
    bootstrap = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-bootstrap",
            subject=_subject(),
            request_digest="digest-bootstrap",
            surface="voice",
            occurred_at="2026-07-18T09:59:00+08:00",
        )
    )
    mind.commit_turn(
        bootstrap,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
    )
    return mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-1",
            subject=_subject(),
            request_digest="digest-1",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )


def _evidence(
    evidence_id: str,
    *,
    ownership_scope: str,
    relationship_epoch_id: str | None,
) -> CompanionEvidence:
    return CompanionEvidence(
        evidence_id=evidence_id,
        pet_id="pet-1",
        memory_subject_id="subject-1",
        ownership_scope=ownership_scope,
        relationship_epoch_id=relationship_epoch_id,
        kind=(
            "explicit_preference" if ownership_scope == "user" else "meaningful_moment"
        ),
        content={"value": evidence_id},
        source_kind="turn",
        source_ref="turn-1",
        source_summary="安全摘要",
        attribution="explicit_user_statement",
        confidence=1.0,
        occurred_at="2026-07-18T10:00:00+08:00",
        retention="long_term",
        status="active",
        prompt_eligible=True,
    )


def _insert_active_adjustment(
    store: CompanionStore,
    *,
    epoch_id: str,
    adjustment_id: str,
    dimension: str,
    value: str,
    scope: str = "all",
) -> None:
    evidence_id = f"evidence-{adjustment_id}"
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO companion_evidence(
                evidence_id, pet_id, memory_subject_id, ownership_scope,
                relationship_epoch_id, kind, content_json, source_kind,
                source_ref, source_summary, attribution, confidence,
                occurred_at, retention, status, prompt_eligible, created_at
            ) VALUES (
                ?, 'pet-1', 'subject-1', 'relationship', ?,
                'interaction_feedback', '{}', 'test', ?,
                '来自相处中确认的表达偏好。', 'observed_interaction', 1.0,
                '2026-07-18T10:01:00+08:00', 'long_term', 'active', 1,
                '2026-07-18T10:01:00+08:00'
            )
            """,
            (evidence_id, epoch_id, f"test:{evidence_id}"),
        )
        connection.execute(
            """
            INSERT INTO companion_adjustments(
                adjustment_id, pet_id, relationship_epoch_id, dimension,
                value_json, scope, behavior_key, context_scope, direction,
                status, confidence, generated_by, created_at
            ) VALUES (?, 'pet-1', ?, ?, ?, ?, ?, ?, 'increase',
                      'active', 0.9, 'deterministic-test',
                      '2026-07-18T10:01:00+08:00')
            """,
            (
                adjustment_id,
                epoch_id,
                dimension,
                f'{{"value":"{value}"}}',
                scope,
                dimension,
                scope,
            ),
        )
        connection.execute(
            """
            INSERT INTO adjustment_evidence(adjustment_id, evidence_id, pet_id)
            VALUES (?, ?, 'pet-1')
            """,
            (adjustment_id, evidence_id),
        )
        connection.commit()


def test_reset_relationship_preserves_user_evidence_and_stops_old_relationship_memory(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    prepared = _prepared(mind)
    old_epoch_id = prepared.relationship_epoch_id
    store.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
        evidence=(
            _evidence(
                "user-preference",
                ownership_scope="user",
                relationship_epoch_id=None,
            ),
            _evidence(
                "shared-moment",
                ownership_scope="relationship",
                relationship_epoch_id=old_epoch_id,
            ),
        ),
    )

    result = mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={
                "now": "2026-07-18T11:00:00+08:00",
                "idempotency_key": "reset-1",
            },
        )
    )

    assert result.retained == 1
    assert result.deactivated == 1
    assert result.forgotten == 0

    with store.connection() as connection:
        old_epoch = connection.execute(
            """
            SELECT ended_at, end_reason
            FROM relationship_epochs
            WHERE epoch_id = ?
            """,
            (old_epoch_id,),
        ).fetchone()
        active_epochs = connection.execute(
            """
            SELECT epoch_id
            FROM relationship_epochs
            WHERE pet_id = 'pet-1' AND ended_at IS NULL
            """
        ).fetchall()
        user_evidence = connection.execute(
            """
            SELECT status, prompt_eligible
            FROM companion_evidence
            WHERE evidence_id = 'user-preference'
            """
        ).fetchone()

    assert old_epoch["ended_at"] == "2026-07-18T11:00:00+08:00"
    assert old_epoch["end_reason"] == "user_reset"
    assert len(active_epochs) == 1
    assert active_epochs[0]["epoch_id"] != old_epoch_id
    assert tuple(user_evidence) == ("active", 1)

    next_turn = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-2",
            subject=_subject(),
            request_digest="digest-2",
            surface="voice",
            occurred_at="2026-07-18T11:01:00+08:00",
        )
    )
    assert "user-preference" in next_turn.used_evidence_ids
    assert "shared-moment" not in next_turn.used_evidence_ids


def test_forget_evidence_immediately_removes_it_from_recall_and_requeues_work(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    prepared = _prepared(mind)
    store.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
        evidence=(
            _evidence(
                "user-preference",
                ownership_scope="user",
                relationship_epoch_id=None,
            ),
        ),
    )

    result = mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject(),
            payload={
                "evidence_id": "user-preference",
                "now": "2026-07-18T11:10:00+08:00",
                "idempotency_key": "forget-1",
            },
        )
    )

    assert result.forgotten == 1
    assert result.requeued == 1

    next_turn = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-2",
            subject=_subject(),
            request_digest="digest-2",
            surface="voice",
            occurred_at="2026-07-18T11:11:00+08:00",
        )
    )
    assert "user-preference" not in next_turn.used_evidence_ids
    with store.connection() as connection:
        evidence = connection.execute(
            """
            SELECT status, prompt_eligible
            FROM companion_evidence
            WHERE evidence_id = 'user-preference'
            """
        ).fetchone()
        jobs = connection.execute(
            """
            SELECT COUNT(*)
            FROM consolidation_jobs
            WHERE job_kind = 'recompute_after_forget'
            """
        ).fetchone()[0]
    assert tuple(evidence) == ("forgotten", 0)
    assert jobs == 1


def test_correct_evidence_never_leaves_old_and_new_values_active(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    prepared = _prepared(mind)
    store.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
        evidence=(
            replace(
                _evidence(
                    "preference-old",
                    ownership_scope="user",
                    relationship_epoch_id=None,
                ),
                fact_key="preference:response_style",
                importance=0.9,
                sensitivity="sensitive",
                valid_from="2026-07-18T10:00:00+08:00",
                valid_until="2027-07-18T10:00:00+08:00",
            ),
        ),
    )

    result = mind.apply_control(
        CompanionControlCommand(
            action="correct_evidence",
            subject=_subject(),
            payload={
                "evidence_id": "preference-old",
                "replacement_content": {"value": "detailed_answers"},
                "source_summary": "用户明确纠正为希望详细回答。",
                "now": "2026-07-18T11:20:00+08:00",
                "idempotency_key": "correct-1",
            },
        )
    )

    assert result.deactivated == 1
    assert result.requeued == 1
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT evidence_id, status, prompt_eligible, content_json
            FROM companion_evidence
            WHERE evidence_id = 'preference-old'
               OR source_ref = 'control:correct-1'
            ORDER BY evidence_id
            """
        ).fetchall()
        relations = connection.execute(
            """
            SELECT COUNT(*)
            FROM evidence_relations
            WHERE source_evidence_id = 'preference-old'
              AND relation_kind = 'superseded_by'
            """
        ).fetchone()[0]
        correction_idempotency_key = connection.execute(
            """
            SELECT idempotency_key
            FROM companion_observations
            WHERE kind = 'memory_corrected'
            """
        ).fetchone()[0]

    assert len(rows) == 2
    states = {
        row["evidence_id"]: (row["status"], row["prompt_eligible"]) for row in rows
    }
    assert states["preference-old"] == ("superseded", 0)
    replacement_id = next(
        evidence_id for evidence_id in states if evidence_id != "preference-old"
    )
    assert states[replacement_id] == ("active", 1)
    assert relations == 1
    assert correction_idempotency_key == "memory-corrected:correct-1"
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-18T11:21:00+08:00",
        )
    )
    correction = operator.payload["diagnostics"]["observations"][0]
    assert correction["kind"] == "memory_corrected"
    assert correction["source_kind"] == "memory_control"
    assert correction["source_ref"] == "preference-old"
    assert correction["evidence_ids"] == (replacement_id,)
    replacement = next(
        item
        for item in operator.payload["diagnostics"]["evidence_timeline"]
        if item["evidence_id"] == replacement_id
    )
    assert replacement["fact_key"] == "preference:response_style"
    assert replacement["importance"] == 0.9
    assert replacement["sensitivity"] == "sensitive"
    assert replacement["valid_from"] == "2026-07-18T10:00:00+08:00"
    assert replacement["valid_until"] == "2027-07-18T10:00:00+08:00"


def test_memory_correction_rolls_back_when_observation_audit_fails(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"correction-atomicity")
    prepared = _prepared(mind)
    store.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
        evidence=(
            replace(
                _evidence(
                    "preference-old",
                    ownership_scope="user",
                    relationship_epoch_id=None,
                ),
                fact_key="preference:response_style",
                importance=0.9,
                sensitivity="sensitive",
                valid_from="2026-07-18T10:00:00+08:00",
                valid_until="2027-07-18T10:00:00+08:00",
            ),
        ),
    )
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_memory_correction_observation
            BEFORE INSERT ON companion_observations
            WHEN NEW.kind = 'memory_corrected'
            BEGIN
                SELECT RAISE(ABORT, 'simulated correction audit failure');
            END
            """
        )

    with pytest.raises(Exception, match="simulated correction audit failure"):
        mind.apply_control(
            CompanionControlCommand(
                action="correct_evidence",
                subject=_subject(),
                payload={
                    "evidence_id": "preference-old",
                    "replacement_content": {"value": "detailed_answers"},
                    "source_summary": "用户明确纠正为希望详细回答。",
                    "now": "2026-07-18T11:20:00+08:00",
                    "idempotency_key": "correct-atomicity",
                },
            )
        )

    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-18T11:21:00+08:00",
        )
    )
    timeline = operator.payload["diagnostics"]["evidence_timeline"]
    by_id = {item["evidence_id"]: item for item in timeline}
    assert by_id["preference-old"]["status"] == "active"
    assert all(item["source_kind"] != "control" for item in timeline)
    assert operator.payload["diagnostics"]["observations"] == ()
    assert all(
        item["job_kind"] != "recompute_after_correction"
        for item in operator.payload["jobs"]
    )


def test_explicit_boundary_is_immediate_and_can_be_revoked(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    _prepared(mind)

    set_result = mind.apply_control(
        CompanionControlCommand(
            action="set_boundary",
            subject=_subject(),
            payload={
                "boundary_key": "question_frequency",
                "value": "never",
                "source_summary": "用户明确要求不要追问。",
                "now": "2026-07-18T11:30:00+08:00",
                "idempotency_key": "boundary-set-1",
            },
        )
    )
    assert set_result.retained == 1

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-boundary",
            subject=_subject(),
            request_digest="digest-boundary",
            surface="voice",
            occurred_at="2026-07-18T11:31:00+08:00",
        )
    )
    with store.connection() as connection:
        boundary = connection.execute(
            """
            SELECT evidence_id
            FROM companion_evidence
            WHERE kind = 'explicit_boundary' AND status = 'active'
            """
        ).fetchone()
    assert boundary["evidence_id"] in prepared.used_evidence_ids

    revoke_result = mind.apply_control(
        CompanionControlCommand(
            action="revoke_boundary",
            subject=_subject(),
            payload={
                "evidence_id": boundary["evidence_id"],
                "now": "2026-07-18T11:32:00+08:00",
                "idempotency_key": "boundary-revoke-1",
            },
        )
    )
    assert revoke_result.deactivated == 1

    after_revoke = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-revoke",
            subject=_subject(),
            request_digest="digest-after-revoke",
            surface="voice",
            occurred_at="2026-07-18T11:33:00+08:00",
        )
    )
    assert boundary["evidence_id"] not in after_revoke.used_evidence_ids


def test_controls_reject_unknown_speakers_and_cross_owner_access(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    _prepared(mind)
    unknown = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-unknown",
        speaker_identity="unknown",
        academic_stage="unknown",
        persistence_allowed=False,
    )
    other_owner = CompanionSubjectContext(
        owner_user_id="owner-2",
        pet_id="pet-1",
        memory_subject_id="subject-2",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )

    for subject in (unknown, other_owner):
        with pytest.raises(PermissionError):
            mind.apply_control(
                CompanionControlCommand(
                    action="reset_relationship",
                    subject=subject,
                    payload={
                        "now": "2026-07-18T11:40:00+08:00",
                        "idempotency_key": f"reset-{subject.owner_user_id}",
                    },
                )
            )


def test_concurrent_relationship_reset_is_idempotent_and_leaves_one_active_epoch(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    _prepared(mind)
    barrier = Barrier(2)

    def reset(_: int):
        barrier.wait()
        return mind.apply_control(
            CompanionControlCommand(
                action="reset_relationship",
                subject=_subject(),
                payload={
                    "now": "2026-07-18T12:00:00+08:00",
                    "idempotency_key": "concurrent-reset-1",
                },
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reset, (1, 2)))

    assert results[0] == results[1]
    with store.connection() as connection:
        active_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM relationship_epochs
            WHERE pet_id = 'pet-1' AND ended_at IS NULL
            """
        ).fetchone()[0]
        epoch_count = connection.execute(
            """
            SELECT COUNT(*) FROM relationship_epochs WHERE pet_id = 'pet-1'
            """
        ).fetchone()[0]
        control_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM memory_controls
            WHERE idempotency_key = 'concurrent-reset-1'
            """
        ).fetchone()[0]

    assert active_count == 1
    assert epoch_count == 2
    assert control_count == 1


def test_purge_clears_companion_content_but_keeps_pet_ownership(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    prepared = _prepared(mind)
    old_epoch_id = prepared.relationship_epoch_id
    store.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
        evidence=(
            _evidence(
                "user-fact",
                ownership_scope="user",
                relationship_epoch_id=None,
            ),
            _evidence(
                "relationship-fact",
                ownership_scope="relationship",
                relationship_epoch_id=old_epoch_id,
            ),
        ),
    )

    result = mind.apply_control(
        CompanionControlCommand(
            action="purge_personal_memory",
            subject=_subject(),
            payload={
                "now": "2026-07-18T12:10:00+08:00",
                "idempotency_key": "purge-1",
            },
        )
    )

    assert result.forgotten == 3
    with store.connection() as connection:
        evidence = connection.execute(
            """
            SELECT status, prompt_eligible, content_json, source_summary
            FROM companion_evidence
            WHERE pet_id = 'pet-1'
            ORDER BY evidence_id
            """
        ).fetchall()
        pet = connection.execute(
            """
            SELECT owner_user_id FROM companion_pets WHERE pet_id = 'pet-1'
            """
        ).fetchone()
        active_epochs = connection.execute(
            """
            SELECT epoch_id FROM relationship_epochs
            WHERE pet_id = 'pet-1' AND ended_at IS NULL
            """
        ).fetchall()
        control = connection.execute(
            """
            SELECT action, payload_json
            FROM memory_controls
            WHERE idempotency_key = 'purge-1'
            """
        ).fetchone()

    assert all(tuple(row) == ("forgotten", 0, "{}", "purged") for row in evidence)
    assert pet["owner_user_id"] == "owner-1"
    assert len(active_epochs) == 1
    assert active_epochs[0]["epoch_id"] != old_epoch_id
    assert control["action"] == "purge_personal_memory"
    assert "user-fact" not in control["payload_json"]


def test_purge_clears_every_subject_owned_by_the_personal_pet(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    _prepared(mind)
    other_subject = replace(_subject(), memory_subject_id="subject-2")
    other_prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-subject-2",
            subject=other_subject,
            request_digest="digest-subject-2",
            surface="voice",
            occurred_at="2026-07-18T10:05:00+08:00",
        )
    )
    store.commit_turn(
        other_prepared,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
        evidence=(
            replace(
                _evidence(
                    "subject-2-private-fact",
                    ownership_scope="user",
                    relationship_epoch_id=None,
                ),
                memory_subject_id="subject-2",
                source_ref="turn-subject-2",
            ),
        ),
    )

    mind.apply_control(
        CompanionControlCommand(
            action="purge_personal_memory",
            subject=_subject(),
            payload={
                "now": "2026-07-18T12:10:00+08:00",
                "idempotency_key": "purge-all-subjects",
            },
        )
    )

    after_purge = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-subject-2-after-purge",
            subject=other_subject,
            request_digest="digest-subject-2-after-purge",
            surface="voice",
            occurred_at="2026-07-18T12:11:00+08:00",
        )
    )
    with store.connection() as connection:
        evidence = connection.execute(
            """
            SELECT status, prompt_eligible, content_json, source_summary
            FROM companion_evidence
            WHERE evidence_id = 'subject-2-private-fact'
            """
        ).fetchone()

    assert tuple(evidence) == ("forgotten", 0, "{}", "purged")
    assert "subject-2-private-fact" not in after_purge.used_evidence_ids


def test_control_idempotency_key_reuse_with_different_command_is_rejected(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    _prepared(mind)
    mind.apply_control(
        CompanionControlCommand(
            action="set_boundary",
            subject=_subject(),
            payload={
                "boundary_key": "question_frequency",
                "value": "never",
                "source_summary": "用户明确要求不要追问。",
                "now": "2026-07-18T11:30:00+08:00",
                "idempotency_key": "shared-control-key",
            },
        )
    )

    with pytest.raises(CompanionIdempotencyConflict):
        mind.apply_control(
            CompanionControlCommand(
                action="set_boundary",
                subject=_subject(),
                payload={
                    "boundary_key": "question_frequency",
                    "value": "sometimes",
                    "source_summary": "用户改变了追问边界。",
                    "now": "2026-07-18T11:30:00+08:00",
                    "idempotency_key": "shared-control-key",
                },
            )
        )
    with pytest.raises(CompanionIdempotencyConflict):
        mind.apply_control(
            CompanionControlCommand(
                action="set_boundary",
                subject=replace(_subject(), memory_subject_id="subject-2"),
                payload={
                    "boundary_key": "question_frequency",
                    "value": "never",
                    "source_summary": "用户明确要求不要追问。",
                    "now": "2026-07-18T11:30:00+08:00",
                    "idempotency_key": "shared-control-key",
                },
            )
        )
    with pytest.raises(CompanionIdempotencyConflict):
        mind.apply_control(
            CompanionControlCommand(
                action="reset_relationship",
                subject=_subject(),
                payload={
                    "now": "2026-07-18T11:31:00+08:00",
                    "idempotency_key": "shared-control-key",
                },
            )
        )


def test_control_rejects_invalid_timestamp_without_writing(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    _prepared(mind)

    with pytest.raises(ValueError, match="ISO-8601"):
        mind.apply_control(
            CompanionControlCommand(
                action="reset_relationship",
                subject=_subject(),
                payload={
                    "now": "not-a-time",
                    "idempotency_key": "invalid-time-reset",
                },
            )
        )

    with store.connection() as connection:
        active_epochs = connection.execute(
            """
            SELECT COUNT(*) FROM relationship_epochs
            WHERE pet_id = 'pet-1' AND ended_at IS NULL
            """
        ).fetchone()[0]
        controls = connection.execute(
            """
            SELECT COUNT(*) FROM memory_controls
            WHERE idempotency_key = 'invalid-time-reset'
            """
        ).fetchone()[0]

    assert active_epochs == 1
    assert controls == 0


def test_reset_cancels_pending_jobs_from_the_ended_epoch(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    prepared = _prepared(mind)
    old_epoch_id = prepared.relationship_epoch_id
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO consolidation_jobs(
                job_id, pet_id, relationship_epoch_id, job_kind,
                idempotency_key, payload_json, status, due_at,
                schema_version, created_at, updated_at
            ) VALUES (
                'old-epoch-job', 'pet-1', ?, 'academic_stage_changed',
                'old-epoch-job-key', '{}', 'pending',
                '2026-07-18T11:00:00+08:00', 'test-v1',
                '2026-07-18T10:00:00+08:00', '2026-07-18T10:00:00+08:00'
            )
            """,
            (old_epoch_id,),
        )
        connection.commit()

    mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={
                "now": "2026-07-18T11:00:00+08:00",
                "idempotency_key": "reset-cancels-old-jobs",
            },
        )
    )

    with store.connection() as connection:
        job = connection.execute(
            """
            SELECT status FROM consolidation_jobs WHERE job_id = 'old-epoch-job'
            """
        ).fetchone()

    assert job["status"] == "cancelled"


def test_forget_theme_uses_generic_content_tags_without_theme_specific_schema(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"relationship-test-secret")
    prepared = _prepared(mind)
    store.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
        evidence=(
            replace(
                _evidence(
                    "theme-c",
                    ownership_scope="user",
                    relationship_epoch_id=None,
                ),
                content={"theme": "c_language", "value": "short_answers"},
            ),
            replace(
                _evidence(
                    "theme-roommate",
                    ownership_scope="user",
                    relationship_epoch_id=None,
                ),
                content={
                    "theme": "roommate_relationship",
                    "value": "short_answers",
                },
            ),
        ),
    )

    result = mind.apply_control(
        CompanionControlCommand(
            action="forget_theme",
            subject=_subject(),
            payload={
                "theme": "c_language",
                "now": "2026-07-18T12:20:00+08:00",
                "idempotency_key": "forget-theme-1",
            },
        )
    )

    assert result.forgotten == 1
    with store.connection() as connection:
        states = {
            row["evidence_id"]: (row["status"], row["prompt_eligible"])
            for row in connection.execute(
                """
                SELECT evidence_id, status, prompt_eligible
                FROM companion_evidence
                WHERE evidence_id IN ('theme-c', 'theme-roommate')
                """
            )
        }
    assert states["theme-c"] == ("forgotten", 0)
    assert states["theme-roommate"] == ("active", 1)


def test_current_turn_correction_is_ephemeral_and_contract_follows_reset_purge_matrix(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"slice12-contract-story")
    prepared = _prepared(mind)
    _insert_active_adjustment(
        store,
        epoch_id=prepared.relationship_epoch_id,
        adjustment_id="adjustment-replaced-by-contract",
        dimension="response_length",
        value="short",
    )
    _insert_active_adjustment(
        store,
        epoch_id=prepared.relationship_epoch_id,
        adjustment_id="adjustment-miniprogram-scope",
        dimension="response_length",
        value="standard",
        scope="miniprogram",
    )
    mind.apply_control(
        CompanionControlCommand(
            action="set_interaction_contract",
            subject=_subject(),
            payload={
                "dimension": "response_length",
                "value": "expanded",
                "scope": "voice",
                "safe_label": "需要时多展开一些",
                "safe_scope": "所有场景",
                "now": "2026-07-18T10:02:00+08:00",
                "idempotency_key": "contract-expanded-1",
            },
        )
    )
    after_contract = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="miniprogram",
            now="2026-07-18T10:02:01+08:00",
        )
    )
    assert tuple(
        item["adjustment_id"] for item in after_contract.payload["learned_behaviors"]
    ) == ("adjustment-miniprogram-scope",)

    corrected = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-corrected-once",
            subject=_subject(),
            request_digest="digest-corrected-once",
            surface="voice",
            occurred_at="2026-07-18T10:03:00+08:00",
            current_turn_corrections=("concise",),
        )
    )
    next_turn = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-correction",
            subject=_subject(),
            request_digest="digest-after-correction",
            surface="voice",
            occurred_at="2026-07-18T10:04:00+08:00",
        )
    )
    assert corrected.policy.response_length == "short"
    assert next_turn.policy.response_length == "expanded"

    mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={
                "now": "2026-07-18T10:05:00+08:00",
                "idempotency_key": "contract-reset-1",
            },
        )
    )
    after_reset = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-contract-reset",
            subject=_subject(),
            request_digest="digest-after-contract-reset",
            surface="voice",
            occurred_at="2026-07-18T10:06:00+08:00",
        )
    )
    assert after_reset.policy.response_length == "expanded"

    mind.apply_control(
        CompanionControlCommand(
            action="purge_personal_memory",
            subject=_subject(),
            payload={
                "now": "2026-07-18T10:07:00+08:00",
                "idempotency_key": "contract-purge-1",
            },
        )
    )
    projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="miniprogram",
            now="2026-07-18T10:08:00+08:00",
        )
    )
    assert projection.payload["explicit_settings"] == ()


def test_adjustment_controls_only_revoke_target_then_restore_all_implicit_expression(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"slice12-adjustment-story")
    prepared = _prepared(mind)
    _insert_active_adjustment(
        store,
        epoch_id=prepared.relationship_epoch_id,
        adjustment_id="adjustment-response",
        dimension="response_length",
        value="expanded",
    )
    _insert_active_adjustment(
        store,
        epoch_id=prepared.relationship_epoch_id,
        adjustment_id="adjustment-humor",
        dimension="humor_level",
        value="medium",
    )

    revoked = mind.apply_control(
        CompanionControlCommand(
            action="revoke_adjustment",
            subject=_subject(),
            payload={
                "adjustment_id": "adjustment-response",
                "now": "2026-07-18T10:03:00+08:00",
                "idempotency_key": "revoke-response-only",
            },
        )
    )
    after_single = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="miniprogram",
            now="2026-07-18T10:04:00+08:00",
        )
    )
    assert revoked.deactivated == 1
    assert tuple(
        item["adjustment_id"] for item in after_single.payload["learned_behaviors"]
    ) == ("adjustment-humor",)

    restored = mind.apply_control(
        CompanionControlCommand(
            action="restore_default_expression",
            subject=_subject(),
            payload={
                "now": "2026-07-18T10:05:00+08:00",
                "idempotency_key": "restore-expression-default",
            },
        )
    )
    after_restore = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="miniprogram",
            now="2026-07-18T10:06:00+08:00",
        )
    )
    assert restored.deactivated == 1
    assert after_restore.payload["learned_behaviors"] == ()
