from __future__ import annotations

import asyncio

import pytest

from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionMind,
    CompanionObservation,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.store import CompanionIdempotencyConflict, CompanionStore
from core.xiaoxin.companion.reflection import ReflectionProposal
from core.xiaoxin.companion.initiative import (
    InitiativeDeliveryEligibility,
    InitiativeDeliveryResult,
)
from core.xiaoxin.companion.temperament import temperament_dimensions_for_pet


def _subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


def _set_initiative_contract(
    mind: CompanionMind,
    level: str,
    *,
    now: str,
    idempotency_key: str,
) -> None:
    mind.apply_control(
        CompanionControlCommand(
            action="set_interaction_contract",
            subject=_subject(),
            payload={
                "dimension": "initiative_level",
                "value": level,
                "scope": "all",
                "safe_label": level,
                "safe_scope": "all",
                "now": now,
                "idempotency_key": idempotency_key,
            },
        )
    )


def _seed_connection_need(database_path, *, initiative_level: str):
    store = CompanionStore(database_path)
    mind = CompanionMind(
        store=store,
        connection_bid_delays_minutes={
            "reserved": 4,
            "timely": 4,
            "proactive": 4,
        },
    )
    bootstrap = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=f"turn-bootstrap-{initiative_level}",
            subject=_subject(),
            request_digest=f"digest-bootstrap-{initiative_level}",
            surface="voice",
            occurred_at="2026-08-03T09:59:00+08:00",
        )
    )
    mind.commit_turn(
        bootstrap,
        CompanionTurnOutcome(
            visible_response="ok",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    _set_initiative_contract(
        mind,
        initiative_level,
        now="2026-08-03T09:59:30+08:00",
        idempotency_key=f"initiative-{initiative_level}-seed",
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=f"turn-connection-{initiative_level}",
            subject=_subject(),
            request_digest=f"digest-connection-{initiative_level}",
            surface="voice",
            occurred_at="2026-08-03T10:00:00+08:00",
            source_text="I am here.",
            conversation_digest=f"conversation-{initiative_level}",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="I am here too.",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    epoch = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    assert epoch is not None
    return store, mind, epoch


def _seed_decision(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"initiative-story")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-seed-initiative",
            subject=_subject(),
            request_digest="digest-seed-initiative",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "meaningful_moment",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "followup_worthwhile"},
                    "source_summary": "存在有依据的后续陪伴机会。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-18T12:00:00+08:00",
        )
    )
    return store, mind, projection.payload["decision_id"]


def test_initiative_requires_evidence_and_enforces_daily_low_priority_limit(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"initiative-story")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-initiative-evidence",
            subject=_subject(),
            request_digest="digest-initiative-evidence",
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
                    "content": {"outcome": "followup_worthwhile"},
                    "source_summary": "用户完成了之前提到的一项重要事项。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )

    first = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-18T12:00:00+08:00",
        )
    )
    second = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-18T13:00:00+08:00",
        )
    )

    assert first.payload["eligible"] is True
    assert first.payload["reason_code"] == "evidence_backed_followup"
    assert first.payload["evidence_ids"] == committed.evidence_ids
    assert second.payload == {
        "eligible": False,
        "reason_code": "daily_limit",
    }


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    (
        ({"initiative_enabled": False}, "disabled"),
        ({"quiet_hours_active": True}, "quiet_hours"),
        ({"device_available": False}, "device_unavailable"),
        ({"higher_priority_pending": True}, "higher_priority_notification"),
    ),
)
def test_initiative_hard_filters_run_before_decision_persistence(
    tmp_path, overrides, reason_code
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"initiative-story")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-filter-evidence",
            subject=_subject(),
            request_digest="digest-filter-evidence",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "meaningful_moment",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "followup_worthwhile"},
                    "source_summary": "存在可跟进的明确结果。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )

    projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-18T12:00:00+08:00",
            **overrides,
        )
    )

    assert projection.payload == {"eligible": False, "reason_code": reason_code}
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM initiative_decisions"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "outcome",
    ("ignored", "accepted", "rejected", "delivery_failed"),
)
def test_initiative_delivery_outcomes_create_feedback_evidence(tmp_path, outcome):
    store, mind, decision_id = _seed_decision(tmp_path)

    result = mind.apply_control(
        CompanionControlCommand(
            action="record_initiative_feedback",
            subject=_subject(),
            payload={
                "decision_id": decision_id,
                "outcome": outcome,
                "now": "2026-07-18T12:05:00+08:00",
                "idempotency_key": f"initiative-feedback-{outcome}",
            },
        )
    )

    assert result.status == "applied"
    with store.connection() as connection:
        delivery_status = connection.execute(
            "SELECT delivery_status FROM initiative_decisions"
        ).fetchone()[0]
        feedback = connection.execute(
            """
            SELECT evidence.evidence_id, evidence.kind,
                   json_extract(evidence.content_json, '$.outcome'),
                   json_extract(evidence.content_json, '$.reason_code'),
                   evidence.attribution,
                   observation.kind, observation.source_ref,
                   json_extract(observation.payload_json, '$.outcome')
            FROM companion_evidence AS evidence
            JOIN observation_evidence AS lineage
              ON lineage.evidence_id = evidence.evidence_id
            JOIN companion_observations AS observation
              ON observation.observation_id = lineage.observation_id
            WHERE evidence.kind = 'initiative_feedback'
            """
        ).fetchone()
    assert delivery_status == outcome
    assert tuple(feedback)[1:] == (
        "initiative_feedback",
        outcome,
        "evidence_backed_followup",
        (
            "observed_delivery_outcome"
            if outcome == "delivery_failed"
            else "observed_user_feedback"
        ),
        "initiative_feedback",
        decision_id,
        outcome,
    )


def test_initiative_feedback_replay_is_idempotent_and_conflicts_are_rejected(
    tmp_path,
):
    store, mind, decision_id = _seed_decision(tmp_path)
    command = CompanionControlCommand(
        action="record_initiative_feedback",
        subject=_subject(),
        payload={
            "decision_id": decision_id,
            "outcome": "accepted",
            "now": "2026-07-18T12:05:00+08:00",
            "idempotency_key": "initiative-feedback-replay",
        },
    )

    first = mind.apply_control(command)
    replay = mind.apply_control(command)
    with pytest.raises(CompanionIdempotencyConflict):
        mind.apply_control(
            CompanionControlCommand(
                action="record_initiative_feedback",
                subject=_subject(),
                payload={
                    "decision_id": decision_id,
                    "outcome": "rejected",
                    "now": "2026-07-18T12:06:00+08:00",
                    "idempotency_key": "initiative-feedback-replay",
                },
            )
        )

    assert replay == first
    with store.connection() as connection:
        delivery_status = connection.execute(
            "SELECT delivery_status FROM initiative_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()[0]
        feedback_rows = connection.execute(
            """
            SELECT json_extract(content_json, '$.outcome')
            FROM companion_evidence
            WHERE kind = 'initiative_feedback'
            """
        ).fetchall()
    assert delivery_status == "accepted"
    assert [row[0] for row in feedback_rows] == ["accepted"]
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_observations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM observation_evidence"
        ).fetchone()[0] == 1


def test_initiative_feedback_rolls_back_when_observation_audit_cannot_be_written(
    tmp_path,
):
    store, mind, decision_id = _seed_decision(tmp_path)
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_initiative_feedback_observation
            BEFORE INSERT ON companion_observations
            WHEN NEW.kind = 'initiative_feedback'
            BEGIN
                SELECT RAISE(ABORT, 'simulated observation failure');
            END
            """
        )

    with pytest.raises(Exception, match="simulated observation failure"):
        mind.apply_control(
            CompanionControlCommand(
                action="record_initiative_feedback",
                subject=_subject(),
                payload={
                    "decision_id": decision_id,
                    "outcome": "accepted",
                    "now": "2026-07-18T12:05:00+08:00",
                    "idempotency_key": "initiative-feedback-atomicity",
                },
            )
        )

    with store.connection() as connection:
        assert connection.execute(
            """
            SELECT delivery_status FROM initiative_decisions
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()[0] == "pending"
        assert connection.execute(
            """
            SELECT COUNT(*) FROM companion_evidence
            WHERE kind = 'initiative_feedback'
            """
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM memory_controls
            WHERE action = 'record_initiative_feedback'
            """
        ).fetchone()[0] == 0


def test_initiative_terminal_feedback_cannot_be_overwritten_with_a_new_key(tmp_path):
    store, mind, decision_id = _seed_decision(tmp_path)
    mind.apply_control(
        CompanionControlCommand(
            action="record_initiative_feedback",
            subject=_subject(),
            payload={
                "decision_id": decision_id,
                "outcome": "accepted",
                "now": "2026-07-18T12:05:00+08:00",
                "idempotency_key": "initiative-feedback-terminal-accepted",
            },
        )
    )

    with pytest.raises(CompanionIdempotencyConflict):
        mind.apply_control(
            CompanionControlCommand(
                action="record_initiative_feedback",
                subject=_subject(),
                payload={
                    "decision_id": decision_id,
                    "outcome": "delivery_failed",
                    "now": "2026-07-18T12:06:00+08:00",
                    "idempotency_key": "initiative-feedback-terminal-failed",
                },
            )
        )

    with store.connection() as connection:
        delivery_status = connection.execute(
            "SELECT delivery_status FROM initiative_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()[0]
        outcomes = [
            row[0]
            for row in connection.execute(
                """
                SELECT json_extract(content_json, '$.outcome')
                FROM companion_evidence
                WHERE kind = 'initiative_feedback'
                """
            )
        ]
    assert delivery_status == "accepted"
    assert outcomes == ["accepted"]


def test_initiative_feedback_cannot_be_written_into_another_subject(tmp_path):
    store, mind, decision_id = _seed_decision(tmp_path)
    other_subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-2",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    with pytest.raises(PermissionError):
        mind.apply_control(
            CompanionControlCommand(
                action="record_initiative_feedback",
                subject=other_subject,
                payload={
                    "decision_id": decision_id,
                    "outcome": "accepted",
                    "now": "2026-07-18T12:05:00+08:00",
                    "idempotency_key": "cross-subject-initiative-feedback",
                },
            )
        )

    with store.connection() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM companion_evidence
            WHERE kind = 'initiative_feedback'
            """
        ).fetchone()[0] == 0


def test_rejected_initiative_suppresses_same_reason_during_cooldown(tmp_path):
    _, mind, decision_id = _seed_decision(tmp_path)
    mind.apply_control(
        CompanionControlCommand(
            action="record_initiative_feedback",
            subject=_subject(),
            payload={
                "decision_id": decision_id,
                "outcome": "rejected",
                "now": "2026-07-18T12:05:00+08:00",
                "idempotency_key": "initiative-feedback-rejected-cooldown",
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

    assert next_day.payload == {
        "eligible": False,
        "reason_code": "disabled",
    }


@pytest.mark.parametrize("action", ("forget_evidence", "reset_relationship"))
def test_forgotten_or_old_epoch_evidence_cannot_trigger_initiative(tmp_path, action):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"initiative-story")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=f"turn-stale-initiative-{action}",
            subject=_subject(),
            request_digest=f"digest-stale-initiative-{action}",
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
                    "content": {"outcome": "followup_worthwhile"},
                    "source_summary": "这条依据随后被用户撤回。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    payload = {
        "now": "2026-07-18T10:01:00+08:00",
        "idempotency_key": f"stale-initiative-{action}",
    }
    if action == "forget_evidence":
        payload["evidence_id"] = committed.evidence_ids[0]
    mind.apply_control(
        CompanionControlCommand(
            action=action,
            subject=_subject(),
            payload=payload,
        )
    )

    projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-19T12:00:00+08:00",
        )
    )

    assert projection.payload == {"eligible": False, "reason_code": "no_evidence"}


def test_store_rejects_initiative_for_ended_epoch_even_with_caller_supplied_evidence(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"initiative-story")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-stale-store-initiative",
            subject=_subject(),
            request_digest="digest-stale-store-initiative",
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
                    "content": {"outcome": "followup_worthwhile"},
                    "source_summary": "这条旧关系依据不得再次触发主动陪伴。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    with store.connection() as connection:
        old_epoch_id = connection.execute(
            "SELECT relationship_epoch_id FROM companion_evidence WHERE evidence_id = ?",
            (committed.evidence_ids[0],),
        ).fetchone()[0]
    mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={
                "now": "2026-07-18T10:01:00+08:00",
                "idempotency_key": "reset-before-direct-initiative",
            },
        )
    )

    decision = store.decide_initiative(
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=old_epoch_id,
        evidence_ids=committed.evidence_ids,
        content_brief="不得发送的旧关系主动陪伴。",
        hardware_expression={"intensity": "low"},
        now="2026-07-19T12:00:00+08:00",
    )

    assert decision == {"eligible": False, "reason_code": "no_evidence"}
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM initiative_decisions"
        ).fetchone()[0] == 0


def test_reset_cancels_pending_initiative_before_delivery_claim(tmp_path):
    store, mind, decision_id = _seed_decision(tmp_path)
    mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={
                "now": "2026-07-18T12:01:00+08:00",
                "idempotency_key": "reset-pending-initiative",
            },
        )
    )

    delivery_projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-18T12:02:00+08:00",
            initiative_decision_id=decision_id,
        )
    )

    assert delivery_projection.payload == {
        "eligible": False,
        "reason_code": "stale_decision",
    }
    with store.connection() as connection:
        status = connection.execute(
            "SELECT delivery_status FROM initiative_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()[0]
    assert status == "invalidated"


def test_forgotten_initiative_evidence_is_rechecked_at_delivery_claim(tmp_path):
    store, mind, decision_id = _seed_decision(tmp_path)
    with store.connection() as connection:
        evidence_id = connection.execute(
            """
            SELECT json_extract(evidence_ids_json, '$[0]')
            FROM initiative_decisions WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()[0]
    mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject(),
            payload={
                "evidence_id": evidence_id,
                "now": "2026-07-18T12:01:00+08:00",
                "idempotency_key": "forget-pending-initiative-evidence",
            },
        )
    )

    delivery_projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-18T12:02:00+08:00",
            initiative_decision_id=decision_id,
        )
    )

    assert delivery_projection.payload == {
        "eligible": False,
        "reason_code": "stale_decision",
    }
    with store.connection() as connection:
        status = connection.execute(
            "SELECT delivery_status FROM initiative_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()[0]
    assert status == "invalidated"


def test_wrong_subject_cannot_invalidate_pending_initiative_claim(tmp_path):
    store, mind, decision_id = _seed_decision(tmp_path)
    other_subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-2",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    with pytest.raises(PermissionError):
        mind.project(
            CompanionProjectionRequest(
                subject=other_subject,
                surface="initiative",
                now="2026-07-18T12:01:00+08:00",
                initiative_decision_id=decision_id,
            )
        )

    with store.connection() as connection:
        status = connection.execute(
            "SELECT delivery_status FROM initiative_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()[0]
    assert status == "pending"

    claimed = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="initiative",
            now="2026-07-18T12:02:00+08:00",
            initiative_decision_id=decision_id,
        )
    )
    assert claimed.payload["eligible"] is True


class _RecordingInitiativeComposer:
    def __init__(self, store: CompanionStore) -> None:
        self.store = store
        self.calls = []

    async def compose(self, opportunity):
        with self.store.connection() as connection:
            status = connection.execute(
                """
                SELECT status FROM initiative_opportunities
                WHERE opportunity_id = ?
                """,
                (opportunity.opportunity_id,),
            ).fetchone()[0]
        self.calls.append((opportunity, status))
        return f"想和你一起庆祝：{opportunity.safe_brief}"


class _EmptyReflectionModel:
    def reflect(self, request):
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="无需生成额外派生对象。",
        )


class _FailOnceInitiativeComposer(_RecordingInitiativeComposer):
    async def compose(self, opportunity):
        if not self.calls:
            with self.store.connection() as connection:
                status = connection.execute(
                    "SELECT status FROM initiative_opportunities"
                ).fetchone()[0]
            self.calls.append((opportunity, status))
            raise TimeoutError("composer timeout")
        return await super().compose(opportunity)


class _BoundaryChangingComposer(_RecordingInitiativeComposer):
    mind = None

    async def compose(self, opportunity):
        assert self.mind is not None
        self.mind.observe(
            CompanionObservation(
                idempotency_key="disabled-during-composition",
                subject=_subject(),
                kind="boundary_set",
                source_kind="miniprogram_companion",
                source_ref="initiative_level",
                occurred_at="2026-07-21T10:01:00+08:00",
                payload={
                    "boundary_key": "initiative_level",
                    "value": "disabled",
                },
                safe_summary="用户在生成期间关闭了主动陪伴。",
            )
        )
        return await super().compose(opportunity)


class _RecordingInitiativeDeliveryPort:
    def __init__(
        self,
        *,
        eligibility_reason: str = "eligible",
        eligibility_retry_at: str | None = None,
        delivery_status: str = "delivered",
    ) -> None:
        self.eligibility_reason = eligibility_reason
        self.eligibility_retry_at = eligibility_retry_at
        self.delivery_status = delivery_status
        self.checked = []
        self.deliveries = []

    async def check_eligibility(self, opportunity, *, now):
        self.checked.append((opportunity, now))
        return InitiativeDeliveryEligibility(
            eligible=self.eligibility_reason == "eligible",
            reason_code=self.eligibility_reason,
            hardware_expression={"mode": "celebration", "intensity": "low"},
            retry_at=self.eligibility_retry_at,
        )

    async def deliver(self, request):
        self.deliveries.append(request)
        return InitiativeDeliveryResult(
            status=self.delivery_status,
            delivery_id="delivery-initiative-1",
            failure_reason=(
                "device_offline"
                if self.delivery_status == "delivery_failed"
                else None
            ),
        )


class _ConcurrentInitiativeDeliveryPort(_RecordingInitiativeDeliveryPort):
    def __init__(self) -> None:
        super().__init__()
        self._checks = 0
        self._both_checked = asyncio.Event()

    async def check_eligibility(self, opportunity, *, now):
        self._checks += 1
        if self._checks == 2:
            self._both_checked.set()
        await self._both_checked.wait()
        return await super().check_eligibility(opportunity, now=now)


def _pet_id_for_initiative_bias(bias: str) -> str:
    for index in range(1000):
        pet_id = f"pet-{bias}-{index}"
        if temperament_dimensions_for_pet(pet_id)["companion_initiative"] == bias:
            return pet_id
    raise AssertionError(f"could not find deterministic pet for {bias}")


def _observe_completed_goal(
    mind: CompanionMind,
    *,
    sensitivity: str = "private",
    goal_id: str = "goal-1",
    occurred_at: str = "2026-07-21T10:00:00+08:00",
):
    result = mind.observe(
        CompanionObservation(
            idempotency_key=f"goal-completed:{goal_id}:v1",
            subject=_subject(),
            kind="goal_completed",
            source_kind="miniprogram_companion",
            source_ref=goal_id,
            occurred_at=occurred_at,
            payload={
                "goal_id": goal_id,
                "title": "完成课程项目",
                "status": "completed",
            },
            safe_summary="用户完成了课程项目目标。",
        )
    )
    if sensitivity != "private":
        with mind._store.connection() as connection:
            connection.execute(
                "UPDATE companion_evidence SET sensitivity = ? WHERE evidence_id = ?",
                (sensitivity, result.evidence_ids[0]),
            )
            connection.commit()
    return result


def test_helpful_turn_creates_short_term_followup_opportunity(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"initiative-helpful")
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-helpful-followup",
            subject=_subject(),
            request_digest="digest-helpful-followup",
            surface="voice",
            interaction_kind="conversation",
            occurred_at="2026-07-21T10:00:00+08:00",
        )
    )

    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="鏀跺埌銆?",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "accepted_help",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "helpful"},
                    "source_summary": "鐢ㄦ埛鍒氭墠琛ㄧず杩欎釜寤鸿鏈夊府鍔┿€?",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )

    with store.connection() as connection:
        opportunity = connection.execute(
            """
            SELECT opportunity_kind, reason_code, due_at
            FROM initiative_opportunities
            """
        ).fetchone()

    assert tuple(opportunity) == (
        "followup",
        "helpful_response_followup",
        "2026-07-21T14:00:00+08:00",
    )


def test_completed_goal_creates_a_traceable_initiative_opportunity(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"initiative-opportunity")

    observed = _observe_completed_goal(mind)

    with store.connection() as connection:
        opportunity = connection.execute(
            """
            SELECT opportunity_kind, reason_code, evidence_ids_json, status,
                   due_at, safe_brief
            FROM initiative_opportunities
            """
        ).fetchone()
    assert tuple(opportunity[:2]) == ("celebration", "goal_completed")
    assert opportunity[2] == f'["{observed.evidence_ids[0]}"]'
    assert tuple(opportunity[3:]) == (
        "scheduled",
        "2026-07-21T10:00:00+08:00",
        "用户完成了课程项目目标。",
    )


def test_supported_sources_create_only_the_closed_opportunity_kinds(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store)
    observations = (
        CompanionObservation(
            idempotency_key="goal-set:initiative-types",
            subject=_subject(),
            kind="goal_set",
            source_kind="miniprogram_companion",
            source_ref="goal-types",
            occurred_at="2026-07-21T08:00:00+08:00",
            payload={
                "goal_id": "goal-types",
                "title": "完成项目",
                "status": "active",
                "target_at": "2026-07-28T20:00:00+08:00",
            },
            safe_summary="用户设置了一项目标。",
        ),
        CompanionObservation(
            idempotency_key="future-event:initiative-types",
            subject=_subject(),
            kind="future_event_set",
            source_kind="miniprogram_companion",
            source_ref="event-types",
            occurred_at="2026-07-21T08:01:00+08:00",
            payload={
                "event_id": "event-types",
                "title": "参加答辩",
                "scheduled_at": "2026-07-25T14:00:00+08:00",
                "status": "planned",
            },
            safe_summary="用户记录了一项未来事件。",
        ),
        CompanionObservation(
            idempotency_key="checkin-frequency:initiative-types",
            subject=_subject(),
            kind="boundary_set",
            source_kind="miniprogram_companion",
            source_ref="initiative_frequency",
            occurred_at="2026-07-21T08:02:00+08:00",
            payload={"boundary_key": "initiative_frequency", "value": "low"},
            safe_summary="用户设置了低频主动 check-in。",
        ),
        CompanionObservation(
            idempotency_key="todo-created:initiative-types",
            subject=_subject(),
            kind="todo_created",
            source_kind="miniprogram_todo",
            source_ref="todo-types",
            occurred_at="2026-07-21T08:03:00+08:00",
            payload={
                "todo_id": "todo-types",
                "title": "提交报告",
                "due_at": "2026-07-25T20:00:00+08:00",
                "status": "pending",
            },
            safe_summary="用户创建了一项未来待办。",
        ),
        CompanionObservation(
            idempotency_key="reminder-result:initiative-types",
            subject=_subject(),
            kind="reminder_tts_completed",
            source_kind="todo_reminder_delivery",
            source_ref="delivery-types",
            occurred_at="2026-07-21T08:04:00+08:00",
            payload={"delivery_id": "delivery-types", "todo_id": "todo-types"},
            safe_summary="一项待办提醒已完成语音播放。",
        ),
    )
    for observation in observations:
        mind.observe(observation)
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-followup-opportunity-types",
            subject=_subject(),
            request_digest="digest-followup-opportunity-types",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "meaningful_moment",
                    "ownership_scope": "relationship",
                    "content": {
                        "outcome": "followup_worthwhile",
                        "followup_time": "next_day",
                        "topic": "上台讲得怎么样",
                    },
                    "source_summary": "存在一项值得后续跟进的互动。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )

    with store.connection() as connection:
        opportunities = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT opportunity_kind, safe_brief FROM initiative_opportunities"
            )
        }
    assert set(opportunities) == {
        "followup",
        "reminder_result",
        "goal_progress",
        "future_event",
        "checkin",
    }
    assert opportunities["followup"] == "现在按约定直接问用户：上台讲得怎么样？"


@pytest.mark.asyncio
async def test_due_opportunity_is_claimed_before_composition_and_delivered_once(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        token_secret=b"initiative-scheduler",
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    _observe_completed_goal(mind)

    first = await mind.run_due_work(now="2026-07-21T10:01:00+08:00", limit=20)
    second = await mind.run_due_work(now="2026-07-21T10:02:00+08:00", limit=20)

    assert first == type(first)(claimed=1, succeeded=1)
    assert second == type(second)()
    assert len(composer.calls) == 1
    assert composer.calls[0][1] == "claimed"
    assert len(delivery_port.deliveries) == 1
    with store.connection() as connection:
        opportunity = connection.execute(
            """
            SELECT status, decision_id, delivery_id, outcome_code
            FROM initiative_opportunities
            """
        ).fetchone()
        decision = connection.execute(
            """
            SELECT delivery_status, content_brief
            FROM initiative_decisions WHERE decision_id = ?
            """,
            (opportunity[1],),
        ).fetchone()
    assert tuple(opportunity) == (
        "delivered",
        delivery_port.deliveries[0].decision_id,
        "delivery-initiative-1",
        "delivered",
    )
    assert tuple(decision) == (
        "delivered",
        "想和你一起庆祝：用户完成了课程项目目标。",
    )


@pytest.mark.asyncio
async def test_reflection_backlog_does_not_consume_initiative_scan_budget(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        reflection_model=_EmptyReflectionModel(),
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    _observe_completed_goal(mind)
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-reflection-and-initiative",
            subject=_subject(),
            request_digest="digest-reflection-and-initiative",
            surface="voice",
            occurred_at="2026-07-21T10:00:30+08:00",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "interaction_feedback",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "not_helpful"},
                    "source_summary": "用户明确表示这次没有帮助。",
                    "attribution": "explicit_user_feedback",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )

    result = await mind.run_due_work(
        now="2026-07-21T10:01:00+08:00",
        limit=1,
    )

    assert result.claimed == 2
    assert result.succeeded == 2
    assert len(delivery_port.deliveries) == 1


@pytest.mark.asyncio
async def test_two_schedulers_cannot_claim_or_deliver_the_same_opportunity_twice(
    tmp_path,
):
    database_path = tmp_path / "xiaoxin_companion.db"
    producer = CompanionMind(store=CompanionStore(database_path))
    _observe_completed_goal(producer)
    delivery_port = _ConcurrentInitiativeDeliveryPort()
    first_store = CompanionStore(database_path)
    second_store = CompanionStore(database_path)
    first = CompanionMind(
        store=first_store,
        initiative_composer=_RecordingInitiativeComposer(first_store),
        initiative_delivery_port=delivery_port,
    )
    second = CompanionMind(
        store=second_store,
        initiative_composer=_RecordingInitiativeComposer(second_store),
        initiative_delivery_port=delivery_port,
    )

    await asyncio.gather(
        first.run_due_work(now="2026-07-21T10:01:00+08:00"),
        second.run_due_work(now="2026-07-21T10:01:00+08:00"),
    )

    assert len(delivery_port.deliveries) == 1
    with first_store.connection() as connection:
        opportunity = connection.execute(
            "SELECT status, attempt FROM initiative_opportunities"
        ).fetchone()
        decision_count = connection.execute(
            "SELECT COUNT(*) FROM initiative_decisions"
        ).fetchone()[0]
    assert tuple(opportunity) == ("delivered", 1)
    assert decision_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("eligibility_reason", "sensitivity", "expected_reason"),
    (
        ("disabled", "private", "disabled"),
        ("eligible", "sensitive", "sensitive_evidence"),
    ),
)
async def test_blocked_opportunity_never_composes_or_delivers(
    tmp_path,
    eligibility_reason,
    sensitivity,
    expected_reason,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort(
        eligibility_reason=eligibility_reason
    )
    mind = CompanionMind(
        store=store,
        token_secret=b"initiative-filter",
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    _observe_completed_goal(mind, sensitivity=sensitivity)

    result = await mind.run_due_work(now="2026-07-21T10:01:00+08:00", limit=20)

    assert result == type(result)(claimed=1, succeeded=1)
    assert composer.calls == []
    assert delivery_port.deliveries == []
    with store.connection() as connection:
        row = connection.execute(
            "SELECT status, outcome_code FROM initiative_opportunities"
        ).fetchone()
    assert tuple(row) == ("blocked", expected_reason)


@pytest.mark.asyncio
async def test_transient_eligibility_defers_then_delivers_the_same_opportunity(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort(
        eligibility_reason="quiet_hours",
        eligibility_retry_at="2026-07-21T10:30:00+08:00",
    )
    mind = CompanionMind(
        store=store,
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    _observe_completed_goal(mind)

    deferred = await mind.run_due_work(now="2026-07-21T10:01:00+08:00")
    early = await mind.run_due_work(now="2026-07-21T10:29:59+08:00")
    delivery_port.eligibility_reason = "eligible"
    delivery_port.eligibility_retry_at = None
    recovered = await mind.run_due_work(now="2026-07-21T10:30:00+08:00")

    assert deferred == type(deferred)(claimed=1, retried=1)
    assert early == type(early)()
    assert recovered == type(recovered)(claimed=1, succeeded=1)
    assert len(composer.calls) == 1
    assert len(delivery_port.deliveries) == 1
    with store.connection() as connection:
        opportunity = connection.execute(
            """
            SELECT status, attempt, next_attempt_at
            FROM initiative_opportunities
            """
        ).fetchone()
        decision_count = connection.execute(
            "SELECT COUNT(*) FROM initiative_decisions"
        ).fetchone()[0]
    assert tuple(opportunity) == ("delivered", 1, None)
    assert decision_count == 1


@pytest.mark.asyncio
async def test_expired_future_event_is_blocked_but_recent_reminder_result_can_send(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    mind.observe(
        CompanionObservation(
            idempotency_key="todo-expired-reminder-result",
            subject=_subject(),
            kind="todo_created",
            source_kind="miniprogram_todo",
            source_ref="todo-expired",
            occurred_at="2026-07-21T08:00:00+08:00",
            payload={
                "todo_id": "todo-expired",
                "title": "提交材料",
                "due_at": "2026-07-21T10:00:00+08:00",
                "status": "pending",
            },
            safe_summary="用户创建了一项十点到期的待办。",
        )
    )
    mind.observe(
        CompanionObservation(
            idempotency_key="tts-completed-expired-reminder-result",
            subject=_subject(),
            kind="reminder_tts_completed",
            source_kind="todo_reminder_delivery",
            source_ref="delivery-expired",
            occurred_at="2026-07-21T10:00:00+08:00",
            payload={
                "delivery_id": "delivery-expired",
                "todo_id": "todo-expired",
            },
            safe_summary="待办提醒已完成语音播放。",
        )
    )

    result = await mind.run_due_work(now="2026-07-21T10:16:00+08:00")

    assert result.claimed == 2
    assert len(delivery_port.deliveries) == 1
    assert delivery_port.deliveries[0].opportunity_kind == "reminder_result"
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT opportunity_kind, status, outcome_code
            FROM initiative_opportunities ORDER BY due_at
            """
        ).fetchall()
    assert tuple(map(tuple, rows)) == (
        ("future_event", "blocked", "no_evidence"),
        ("reminder_result", "delivered", "delivered"),
    )


@pytest.mark.asyncio
async def test_delivery_completion_is_not_recorded_as_user_acceptance(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        initiative_composer=_RecordingInitiativeComposer(store),
        initiative_delivery_port=_RecordingInitiativeDeliveryPort(),
    )
    _observe_completed_goal(mind)

    await mind.run_due_work(now="2026-07-21T10:01:00+08:00")

    with store.connection() as connection:
        decision_status = connection.execute(
            "SELECT delivery_status FROM initiative_decisions"
        ).fetchone()[0]
        feedback_count = connection.execute(
            "SELECT COUNT(*) FROM companion_evidence WHERE kind = 'initiative_feedback'"
        ).fetchone()[0]
    assert decision_status == "delivered"
    assert feedback_count == 0


@pytest.mark.asyncio
async def test_delivery_failure_records_system_outcome_without_user_rejection(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        initiative_composer=_RecordingInitiativeComposer(store),
        initiative_delivery_port=_RecordingInitiativeDeliveryPort(
            delivery_status="delivery_failed"
        ),
    )
    _observe_completed_goal(mind)

    result = await mind.run_due_work(now="2026-07-21T10:01:00+08:00")

    assert result.failed == 1
    with store.connection() as connection:
        feedback = connection.execute(
            """
            SELECT json_extract(content_json, '$.outcome'), attribution
            FROM companion_evidence WHERE kind = 'initiative_feedback'
            """
        ).fetchone()
        opportunity = connection.execute(
            "SELECT status, outcome_code FROM initiative_opportunities"
        ).fetchone()
    assert tuple(feedback) == ("delivery_failed", "observed_delivery_outcome")
    assert tuple(opportunity) == ("delivery_failed", "device_offline")


@pytest.mark.asyncio
async def test_composition_failure_retries_after_backoff_without_duplicate_delivery(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _FailOnceInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    _observe_completed_goal(mind)

    failed = await mind.run_due_work(now="2026-07-21T10:01:00+08:00")
    early = await mind.run_due_work(now="2026-07-21T10:01:29+08:00")
    recovered = await mind.run_due_work(now="2026-07-21T10:01:30+08:00")

    assert (failed.retried, early.claimed, recovered.succeeded) == (1, 0, 1)
    assert len(delivery_port.deliveries) == 1
    with store.connection() as connection:
        row = connection.execute(
            "SELECT status, attempt FROM initiative_opportunities"
        ).fetchone()
        decision_count = connection.execute(
            "SELECT COUNT(*) FROM initiative_decisions"
        ).fetchone()[0]
    assert tuple(row) == ("delivered", 2)
    assert decision_count == 1


@pytest.mark.asyncio
async def test_relationship_reset_invalidates_scheduled_opportunity(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    _observe_completed_goal(mind)
    mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={
                "now": "2026-07-21T10:00:30+08:00",
                "idempotency_key": "reset-scheduled-opportunity",
            },
        )
    )

    result = await mind.run_due_work(now="2026-07-21T10:01:00+08:00")

    assert result.claimed == 0
    assert composer.calls == []
    assert delivery_port.deliveries == []
    with store.connection() as connection:
        row = connection.execute(
            "SELECT status, outcome_code FROM initiative_opportunities"
        ).fetchone()
    assert tuple(row) == ("invalidated", "relationship_reset")


@pytest.mark.asyncio
async def test_explicit_disabled_boundary_blocks_before_composition(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    mind.observe(
        CompanionObservation(
            idempotency_key="initiative-level-disabled:v1",
            subject=_subject(),
            kind="boundary_set",
            source_kind="miniprogram_companion",
            source_ref="initiative_level",
            occurred_at="2026-07-21T09:00:00+08:00",
            payload={"boundary_key": "initiative_level", "value": "disabled"},
            safe_summary="用户关闭了主动陪伴。",
        )
    )
    _observe_completed_goal(mind)

    result = await mind.run_due_work(now="2026-07-21T10:01:00+08:00")

    assert result.succeeded == 1
    assert composer.calls == []
    assert delivery_port.deliveries == []
    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT status, outcome_code FROM initiative_opportunities
            WHERE opportunity_kind = 'celebration'
            """
        ).fetchone()
    assert tuple(row) == ("blocked", "disabled")


@pytest.mark.asyncio
async def test_eligibility_is_rechecked_after_composition_before_delivery(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _BoundaryChangingComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    composer.mind = mind
    _observe_completed_goal(mind)

    result = await mind.run_due_work(now="2026-07-21T10:01:00+08:00")

    assert result.succeeded == 1
    assert delivery_port.deliveries == []
    with store.connection() as connection:
        opportunity = connection.execute(
            "SELECT status, outcome_code FROM initiative_opportunities"
        ).fetchone()
        decision = connection.execute(
            "SELECT delivery_status FROM initiative_decisions"
        ).fetchone()[0]
    assert tuple(opportunity) == ("blocked", "disabled")
    assert decision == "invalidated"


@pytest.mark.asyncio
async def test_two_unanswered_deliveries_create_frequency_backoff_not_rejection(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    for day in (21, 22):
        _observe_completed_goal(
            mind,
            goal_id=f"goal-{day}",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
        )
        await mind.run_due_work(now=f"2026-07-{day:02d}T10:01:00+08:00")
    _observe_completed_goal(
        mind,
        goal_id="goal-23",
        occurred_at="2026-07-23T10:00:00+08:00",
    )

    third = await mind.run_due_work(now="2026-07-23T10:01:00+08:00")

    assert third.claimed == 1
    assert len(delivery_port.deliveries) == 2
    with store.connection() as connection:
        statuses = connection.execute(
            """
            SELECT status, outcome_code FROM initiative_opportunities
            ORDER BY due_at
            """
        ).fetchall()
        rejected = connection.execute(
            """
            SELECT COUNT(*) FROM initiative_decisions
            WHERE delivery_status = 'rejected'
            """
        ).fetchone()[0]
    assert tuple(map(tuple, statuses)) == (
        ("delivered", "delivered"),
        ("delivered", "delivered"),
        ("blocked", "unanswered_backoff"),
    )
    assert rejected == 0


def test_forgetting_evidence_invalidates_and_scrubs_scheduled_opportunity(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store)
    observed = _observe_completed_goal(mind)

    mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject(),
            payload={
                "evidence_id": observed.evidence_ids[0],
                "now": "2026-07-21T10:00:30+08:00",
                "idempotency_key": "forget-goal-opportunity",
            },
        )
    )

    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT status, evidence_ids_json, safe_brief, outcome_code
            FROM initiative_opportunities
            """
        ).fetchone()
    assert tuple(row) == (
        "invalidated",
        "[]",
        "forgotten",
        "evidence_forgotten",
    )


@pytest.mark.asyncio
async def test_connection_bid_materializes_after_due_time_and_delivers_once(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
        connection_bid_delays_minutes={
            "reserved": 2,
            "timely": 2,
            "proactive": 2,
        },
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-connection-bid",
            subject=_subject(),
            request_digest="digest-connection-bid",
            surface="voice",
            occurred_at="2026-08-03T10:00:00+08:00",
            source_text="今天就是想来和你说说话。",
            conversation_digest="conversation-connection-bid",
        )
    )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我在，慢慢说。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    early = await mind.run_due_work(now="2026-08-03T10:02:29+08:00", limit=20)
    due = await mind.run_due_work(now="2026-08-03T10:02:30+08:00", limit=20)
    repeated = await mind.run_due_work(now="2026-08-03T10:03:00+08:00", limit=20)

    assert early == type(early)()
    assert due == type(due)(claimed=1, succeeded=1)
    assert repeated == type(repeated)()
    assert len(delivery_port.deliveries) == 1
    assert delivery_port.deliveries[0].opportunity_kind == "connection_bid"
    assert len(delivery_port.checked) == 1
    epoch = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    assert epoch is not None
    need = store.load_connection_need(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=epoch.epoch_id,
    )
    assert need is not None
    checked_opportunity, checked_at = delivery_port.checked[0]
    assert checked_at == "2026-08-03T10:02:30+08:00"
    assert checked_opportunity.initiative_bias == need["initiative_bias"]
    assert checked_opportunity.relationship_stage == need["relationship_stage"]
    assert checked_opportunity.connection_need_strength == "light"
    assert need["source_evidence_id"] in committed.evidence_ids
    assert need["pending_decision_id"] == delivery_port.deliveries[0].decision_id
    with store.connection() as connection:
        opportunity = connection.execute(
            """
            SELECT opportunity_kind, reason_code, status, evidence_ids_json
            FROM initiative_opportunities
            WHERE opportunity_kind = 'connection_bid'
            """
        ).fetchone()
    assert tuple(opportunity[:3]) == (
        "connection_bid",
        "relationship_connection_due",
        "delivered",
    )
    assert need["source_evidence_id"] in opportunity["evidence_ids_json"]

    response = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-connection-response",
            subject=_subject(),
            request_digest="digest-connection-response",
            surface="voice",
            occurred_at="2026-08-03T10:03:30+08:00",
            source_text="我来了，刚才正好在收拾东西。",
            conversation_digest="conversation-connection-response",
        )
    )
    response_commit = mind.commit_turn(
        response,
        CompanionTurnOutcome(
            visible_response="你回来就好，我们慢慢聊。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    responded_need = store.load_connection_need(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=epoch.epoch_id,
    )
    assert responded_need is not None
    assert responded_need["pending_decision_id"] is None
    assert responded_need["ignored_streak"] == 0
    assert responded_need["source_evidence_id"] in response_commit.evidence_ids
    with store.connection() as connection:
        response_state = connection.execute(
            """
            SELECT decision.delivery_status, opportunity.status,
                   opportunity.outcome_code
            FROM initiative_decisions AS decision
            JOIN initiative_opportunities AS opportunity
              ON opportunity.decision_id = decision.decision_id
            WHERE decision.decision_id = ?
            """,
            (delivery_port.deliveries[0].decision_id,),
        ).fetchone()
        response_event = connection.execute(
            """
            SELECT kind, json_extract(content_json, '$.decision_id')
            FROM companion_evidence
            WHERE kind = 'connection_responded'
            """
        ).fetchone()
    assert tuple(response_state) == (
        "connection_responded",
        "invalidated",
        "connection_responded",
    )
    assert tuple(response_event) == (
        "connection_responded",
        delivery_port.deliveries[0].decision_id,
    )


@pytest.mark.asyncio
async def test_connection_delivery_failure_clears_pending_and_backs_off(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    delivery_port = _RecordingInitiativeDeliveryPort(
        delivery_status="delivery_failed"
    )
    mind = CompanionMind(
        store=store,
        initiative_composer=_RecordingInitiativeComposer(store),
        initiative_delivery_port=delivery_port,
        connection_bid_delays_minutes={
            "reserved": 2,
            "timely": 2,
            "proactive": 2,
        },
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-connection-failure",
            subject=_subject(),
            request_digest="digest-connection-failure",
            surface="voice",
            occurred_at="2026-08-03T10:00:00+08:00",
            source_text="今天想和你说说话。",
            conversation_digest="conversation-connection-failure",
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

    failed = await mind.run_due_work(now="2026-08-03T10:02:30+08:00")
    early = await mind.run_due_work(now="2026-08-03T10:04:59+08:00")

    epoch = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    assert epoch is not None
    need = store.load_connection_need(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=epoch.epoch_id,
    )
    assert failed == type(failed)(claimed=1, failed=1)
    assert early == type(early)()
    assert need is not None
    assert need["pending_decision_id"] is None
    assert need["ignored_streak"] == 0
    assert need["next_eligible_at"] == "2026-08-03T10:05:00+08:00"
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT status, outcome_code
            FROM initiative_opportunities
            WHERE opportunity_kind = 'connection_bid'
            """
        ).fetchall()
    assert tuple(map(tuple, rows)) == (("delivery_failed", "device_offline"),)


@pytest.mark.asyncio
async def test_connection_ignore_backoff_differs_by_temperament(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=_RecordingInitiativeComposer(store),
        initiative_delivery_port=delivery_port,
        connection_bid_delays_minutes={
            "reserved": 2,
            "timely": 2,
            "proactive": 2,
        },
        connection_feedback_window_minutes=2,
    )
    subjects = []
    for bias in ("reserved", "proactive"):
        subject = CompanionSubjectContext(
            owner_user_id=f"owner-{bias}",
            pet_id=_pet_id_for_initiative_bias(bias),
            memory_subject_id=f"subject-{bias}",
            speaker_identity="confirmed",
            academic_stage="freshman",
            persistence_allowed=True,
        )
        subjects.append((bias, subject))
        prepared = mind.prepare_turn(
            CompanionTurnRequest(
                turn_id=f"turn-ignore-{bias}",
                subject=subject,
                request_digest=f"digest-ignore-{bias}",
                surface="voice",
                occurred_at="2026-08-03T10:00:00+08:00",
                source_text="今天先来和你说说话。",
                conversation_digest=f"conversation-ignore-{bias}",
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

    delivered = await mind.run_due_work(now="2026-08-03T10:02:30+08:00")
    boundary = await mind.run_due_work(now="2026-08-03T10:04:30+08:00")
    for _, subject in subjects:
        epoch = store.get_active_epoch(
            owner_user_id=subject.owner_user_id,
            pet_id=subject.pet_id,
        )
        assert epoch is not None
        boundary_need = store.load_connection_need(
            owner_user_id=subject.owner_user_id,
            pet_id=subject.pet_id,
            memory_subject_id=subject.memory_subject_id,
            relationship_epoch_id=epoch.epoch_id,
        )
        assert boundary_need is not None
        assert boundary_need["pending_decision_id"] is not None
    expired = await mind.run_due_work(now="2026-08-03T10:04:31+08:00")

    assert delivered == type(delivered)(claimed=2, succeeded=2)
    assert boundary == type(boundary)()
    assert expired == type(expired)()
    needs = {}
    for bias, subject in subjects:
        epoch = store.get_active_epoch(
            owner_user_id=subject.owner_user_id,
            pet_id=subject.pet_id,
        )
        assert epoch is not None
        needs[bias] = store.load_connection_need(
            owner_user_id=subject.owner_user_id,
            pet_id=subject.pet_id,
            memory_subject_id=subject.memory_subject_id,
            relationship_epoch_id=epoch.epoch_id,
        )
    assert needs["reserved"] is not None
    assert needs["proactive"] is not None
    assert needs["reserved"]["ignored_streak"] == 1
    assert needs["proactive"]["ignored_streak"] == 1
    assert needs["reserved"]["pending_decision_id"] is None
    assert needs["proactive"]["pending_decision_id"] is None
    assert needs["reserved"]["next_eligible_at"] == "2026-08-03T10:10:46+08:00"
    assert needs["proactive"]["next_eligible_at"] == "2026-08-03T10:08:16+08:00"
    with store.connection() as connection:
        outcomes = connection.execute(
            """
            SELECT delivery_status, COUNT(*)
            FROM initiative_decisions GROUP BY delivery_status
            """
        ).fetchall()
    assert tuple(map(tuple, outcomes)) == (("ignored", 2),)


@pytest.mark.asyncio
async def test_connection_explicit_rejection_enters_cooldown(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=_RecordingInitiativeComposer(store),
        initiative_delivery_port=delivery_port,
        connection_bid_delays_minutes={
            "reserved": 2,
            "timely": 2,
            "proactive": 2,
        },
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-rejection-seed",
            subject=_subject(),
            request_digest="digest-rejection-seed",
            surface="voice",
            occurred_at="2026-08-03T10:00:00+08:00",
            source_text="先陪我聊一会儿。",
            conversation_digest="conversation-rejection-seed",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="好，我在这里。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    await mind.run_due_work(now="2026-08-03T10:02:30+08:00")

    rejection = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-connection-rejected",
            subject=_subject(),
            request_digest="digest-connection-rejected",
            surface="voice",
            occurred_at="2026-08-03T10:03:00+08:00",
            source_text="别老主动找我。",
            conversation_digest="conversation-connection-rejected",
        )
    )
    mind.commit_turn(
        rejection,
        CompanionTurnOutcome(
            visible_response="知道了，我会给你更多空间。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "interaction_feedback",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "too_proactive"},
                    "source_summary": "用户明确表示主动陪伴过多。",
                    "attribution": "explicit_user_feedback",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": False,
                },
            ),
        ),
    )

    epoch = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    assert epoch is not None
    need = store.load_connection_need(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=epoch.epoch_id,
    )
    assert need is not None
    assert need["pending_decision_id"] is None
    assert need["cooldown_until"] == "2026-08-10T10:03:00+08:00"
    assert need["next_eligible_at"] == "2026-08-10T10:03:00+08:00"
    with store.connection() as connection:
        state = connection.execute(
            """
            SELECT decision.delivery_status, opportunity.status,
                   opportunity.outcome_code
            FROM initiative_decisions AS decision
            JOIN initiative_opportunities AS opportunity
              ON opportunity.decision_id = decision.decision_id
            WHERE opportunity.opportunity_kind = 'connection_bid'
            """
        ).fetchone()
        event = connection.execute(
            """
            SELECT kind, json_extract(content_json, '$.outcome')
            FROM companion_evidence WHERE kind = 'connection_rejected'
            """
        ).fetchone()
    assert tuple(state) == ("rejected", "invalidated", "rejected")
    assert tuple(event) == ("connection_rejected", "rejected")


def test_connection_need_timing_uses_temperament_and_keeps_owner_isolation(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        connection_bid_delays_minutes={
            "reserved": 4,
            "timely": 3,
            "proactive": 2,
        },
    )
    subjects = []
    for bias in ("reserved", "proactive"):
        pet_id = _pet_id_for_initiative_bias(bias)
        subject = CompanionSubjectContext(
            owner_user_id=f"owner-{bias}",
            pet_id=pet_id,
            memory_subject_id=f"subject-{bias}",
            speaker_identity="confirmed",
            academic_stage="freshman",
            persistence_allowed=True,
        )
        subjects.append((bias, subject))
        prepared = mind.prepare_turn(
            CompanionTurnRequest(
                turn_id=f"turn-{bias}",
                subject=subject,
                request_digest=f"digest-{bias}",
                surface="voice",
                occurred_at="2026-08-03T10:00:00+08:00",
                source_text=f"这是 {bias} 小芯自己的对话。",
                conversation_digest=f"conversation-{bias}",
            )
        )
        mind.commit_turn(
            prepared,
            CompanionTurnOutcome(
                visible_response="我收到了。",
                assistant_action="reply",
                delivery_status="generated",
            ),
        )

    needs = {}
    for bias, subject in subjects:
        epoch = store.get_active_epoch(
            owner_user_id=subject.owner_user_id,
            pet_id=subject.pet_id,
        )
        assert epoch is not None
        needs[bias] = store.load_connection_need(
            owner_user_id=subject.owner_user_id,
            pet_id=subject.pet_id,
            memory_subject_id=subject.memory_subject_id,
            relationship_epoch_id=epoch.epoch_id,
        )

    assert needs["reserved"] is not None
    assert needs["proactive"] is not None
    assert needs["reserved"]["initiative_bias"] == "reserved"
    assert needs["proactive"]["initiative_bias"] == "proactive"
    assert needs["reserved"]["threshold_seconds"] == 300
    assert needs["proactive"]["threshold_seconds"] == 150
    with pytest.raises(PermissionError):
        reserved_subject = subjects[0][1]
        proactive_subject = subjects[1][1]
        proactive_epoch = store.get_active_epoch(
            owner_user_id=proactive_subject.owner_user_id,
            pet_id=proactive_subject.pet_id,
        )
        assert proactive_epoch is not None
        store.load_connection_need(
            owner_user_id=reserved_subject.owner_user_id,
            pet_id=proactive_subject.pet_id,
            memory_subject_id=proactive_subject.memory_subject_id,
            relationship_epoch_id=proactive_epoch.epoch_id,
        )


def test_natural_initiative_halves_connection_cadence(tmp_path):
    low_store, _, low_epoch = _seed_connection_need(
        tmp_path / "low" / "xiaoxin_companion.db",
        initiative_level="low",
    )
    medium_store, _, medium_epoch = _seed_connection_need(
        tmp_path / "medium" / "xiaoxin_companion.db",
        initiative_level="medium",
    )

    low_need = low_store.load_connection_need(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=low_epoch.epoch_id,
    )
    medium_need = medium_store.load_connection_need(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=medium_epoch.epoch_id,
    )

    assert low_need is not None
    assert medium_need is not None
    assert low_need["initiative_level"] == "low"
    assert medium_need["initiative_level"] == "medium"
    assert low_need["threshold_seconds"] == 300
    assert medium_need["threshold_seconds"] == 150


def test_initiative_contract_reschedules_existing_need_without_bypassing_cooldown(
    tmp_path,
):
    store, mind, epoch = _seed_connection_need(
        tmp_path / "xiaoxin_companion.db",
        initiative_level="low",
    )
    with store.connection() as connection:
        connection.execute(
            """
            UPDATE companion_relationship_needs
            SET cooldown_until = '2026-08-10T10:00:00+08:00',
                next_eligible_at = '2026-08-10T10:00:00+08:00'
            WHERE owner_user_id = 'owner-1' AND pet_id = 'pet-1'
              AND memory_subject_id = 'subject-1'
            """
        )
        connection.commit()

    _set_initiative_contract(
        mind,
        "medium",
        now="2026-08-03T10:01:00+08:00",
        idempotency_key="initiative-medium-reschedule",
    )

    need = store.load_connection_need(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=epoch.epoch_id,
    )
    assert need is not None
    assert need["initiative_level"] == "medium"
    assert need["threshold_seconds"] == 150
    assert need["cooldown_until"] == "2026-08-10T10:00:00+08:00"
    assert need["next_eligible_at"] == "2026-08-10T10:00:00+08:00"


@pytest.mark.asyncio
async def test_disabled_interaction_contract_blocks_queued_initiative(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
    )
    _observe_completed_goal(mind)

    _set_initiative_contract(
        mind,
        "disabled",
        now="2026-07-21T10:00:30+08:00",
        idempotency_key="initiative-disabled-contract",
    )
    result = await mind.run_due_work(now="2026-07-21T10:01:00+08:00")

    assert result.claimed == 0
    assert composer.calls == []
    assert delivery_port.deliveries == []
    with store.connection() as connection:
        opportunity = connection.execute(
            """
            SELECT status, outcome_code FROM initiative_opportunities
            WHERE opportunity_kind = 'celebration'
            """
        ).fetchone()
    assert tuple(opportunity) == ("blocked", "disabled")



def test_boot_checkin_is_idempotent_and_expires_as_unobserved(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO companion_pets(pet_id, owner_user_id, created_at)
            VALUES ('pet-boot', 'owner-boot', '2026-08-04T08:00:00+08:00')
            """
        )
        connection.execute(
            """
            INSERT INTO relationship_epochs(
                epoch_id, pet_id, started_at, start_reason
            ) VALUES (
                'epoch-boot', 'pet-boot',
                '2026-08-04T08:00:00+08:00', 'first_use'
            )
            """
        )
        connection.commit()

    first = store.create_boot_checkin(
        boot_event_id="boot-1",
        device_id="device-boot",
        owner_user_id="owner-boot",
        pet_id="pet-boot",
        memory_subject_id="subject-boot",
        relationship_epoch_id="epoch-boot",
        boot_reason="manual_power_on",
        occurred_at="2026-08-04T10:00:00+08:00",
        due_at="2026-08-04T10:01:00+08:00",
        now="2026-08-04T10:00:00+08:00",
    )
    duplicate = store.create_boot_checkin(
        boot_event_id="boot-1",
        device_id="device-boot",
        owner_user_id="owner-boot",
        pet_id="pet-boot",
        memory_subject_id="subject-boot",
        relationship_epoch_id="epoch-boot",
        boot_reason="hello_connection",
        occurred_at="2026-08-04T10:00:01+08:00",
        due_at="2026-08-04T10:01:01+08:00",
        now="2026-08-04T10:00:01+08:00",
    )
    second_boot = store.create_boot_checkin(
        boot_event_id="boot-2",
        device_id="device-boot",
        owner_user_id="owner-boot",
        pet_id="pet-boot",
        memory_subject_id="subject-boot",
        relationship_epoch_id="epoch-boot",
        boot_reason="hello_connection",
        occurred_at="2026-08-04T10:00:02+08:00",
        due_at="2026-08-05T10:01:02+08:00",
        now="2026-08-04T10:00:02+08:00",
    )
    new_boot = store.create_boot_checkin(
        boot_event_id="boot-3",
        device_id="device-boot",
        owner_user_id="owner-boot",
        pet_id="pet-boot",
        memory_subject_id="subject-boot",
        relationship_epoch_id="epoch-boot",
        boot_reason="ota_request",
        occurred_at="2026-08-04T20:00:00+08:00",
        due_at="2026-08-04T20:01:00+08:00",
        now="2026-08-04T20:00:00+08:00",
    )

    assert first is not None
    assert duplicate == first
    assert second_boot is not None
    assert second_boot != first
    assert new_boot is not None
    assert new_boot != first
    due = store.list_due_initiative_opportunities(
        now="2026-08-04T10:01:00+08:00",
        limit=10,
    )
    assert len(due) == 1
    claimed = store.claim_initiative_opportunity(
        opportunity_id=first,
        hardware_expression={},
        now="2026-08-04T10:01:00+08:00",
    )
    assert claimed is not None
    store.begin_initiative_delivery(
        opportunity=claimed,
        content="hello",
        now="2026-08-04T10:01:00+08:00",
    )
    store.finish_initiative_delivery(
        opportunity=claimed,
        result=InitiativeDeliveryResult(
            status="delivered",
            delivery_id="delivery-boot-1",
        ),
        now="2026-08-04T10:01:00+08:00",
    )

    assert (
        store.expire_boot_checkins(
            now="2026-08-04T10:32:00+08:00",
            feedback_window_seconds=1800,
            limit=10,
        )
        == 1
    )
    with store.connection() as connection:
        expired = connection.execute(
            """
            SELECT opportunity.status, opportunity.next_attempt_at,
                   opportunity.attempt, decision.delivery_status, event.status
            FROM initiative_opportunities AS opportunity
            JOIN initiative_decisions AS decision
              ON decision.decision_id = opportunity.decision_id
            JOIN companion_device_boot_events AS event
              ON event.opportunity_id = opportunity.opportunity_id
            WHERE opportunity.opportunity_id = ?
            """,
            (first,),
        ).fetchone()
        event_count = connection.execute(
            "SELECT COUNT(*) FROM companion_device_boot_events"
        ).fetchone()[0]
    assert tuple(expired) == (
        "invalidated",
        None,
        1,
        "unobserved",
        "unobserved",
    )
    assert store.list_due_initiative_opportunities(
        now="2026-08-04T14:32:00+08:00",
        limit=10,
    ) == ()
    assert event_count == 3


@pytest.mark.asyncio
async def test_stale_boot_checkin_is_suppressed_before_delivery(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO companion_pets(pet_id, owner_user_id, created_at)
            VALUES ('pet-stale-boot', 'owner-stale-boot',
                    '2026-08-04T08:00:00+08:00')
            """
        )
        connection.execute(
            """
            INSERT INTO relationship_epochs(
                epoch_id, pet_id, started_at, start_reason
            ) VALUES (
                'epoch-stale-boot', 'pet-stale-boot',
                '2026-08-04T08:00:00+08:00', 'first_use'
            )
            """
        )
        connection.commit()
    opportunity_id = store.create_boot_checkin(
        boot_event_id="boot-stale-1",
        device_id="device-stale-boot",
        owner_user_id="owner-stale-boot",
        pet_id="pet-stale-boot",
        memory_subject_id="subject-stale-boot",
        relationship_epoch_id="epoch-stale-boot",
        boot_reason="manual_power_on",
        occurred_at="2026-08-04T10:00:00+08:00",
        due_at="2026-08-04T10:01:30+08:00",
        now="2026-08-04T10:00:00+08:00",
    )
    composer = _RecordingInitiativeComposer(store)
    delivery_port = _RecordingInitiativeDeliveryPort()
    mind = CompanionMind(
        store=store,
        initiative_composer=composer,
        initiative_delivery_port=delivery_port,
        boot_checkin_delivery_window_seconds=600,
    )

    result = await mind.run_due_work(now="2026-08-04T10:10:01+08:00")

    assert result == type(result)()
    assert composer.calls == []
    assert delivery_port.checked == []
    assert delivery_port.deliveries == []
    with store.connection() as connection:
        opportunity = connection.execute(
            """
            SELECT status, outcome_code, lease_until, next_attempt_at
            FROM initiative_opportunities
            WHERE opportunity_id = ?
            """,
            (opportunity_id,),
        ).fetchone()
        event = connection.execute(
            """
            SELECT status FROM companion_device_boot_events
            WHERE boot_event_id = 'boot-stale-1'
            """
        ).fetchone()
    assert tuple(opportunity) == (
        "invalidated",
        "boot_checkin_stale",
        None,
        None,
    )
    assert tuple(event) == ("suppressed",)


def test_boot_checkin_ignores_ordinary_daily_limit(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO companion_pets(pet_id, owner_user_id, created_at)
            VALUES ('pet-boot-limit', 'owner-boot-limit', '2026-08-04T08:00:00+08:00')
            """
        )
        connection.execute(
            """
            INSERT INTO relationship_epochs(
                epoch_id, pet_id, started_at, start_reason
            ) VALUES (
                'epoch-boot-limit', 'pet-boot-limit',
                '2026-08-04T08:00:00+08:00', 'first_use'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO initiative_decisions(
                decision_id, pet_id, relationship_epoch_id, reason_code,
                evidence_ids_json, priority, cooldown_until, content_brief,
                hardware_expression_json, delivery_status, created_at
            ) VALUES (
                'ordinary-low-priority', 'pet-boot-limit', 'epoch-boot-limit',
                'ordinary_followup', '[]', 'low', NULL, 'ordinary', '{}',
                'delivered', '2026-08-04T10:00:00+08:00'
            )
            """
        )
        connection.commit()

    opportunity_id = store.create_boot_checkin(
        boot_event_id="boot-limit-1",
        device_id="device-boot-limit",
        owner_user_id="owner-boot-limit",
        pet_id="pet-boot-limit",
        memory_subject_id="subject-boot-limit",
        relationship_epoch_id="epoch-boot-limit",
        boot_reason="manual_power_on",
        occurred_at="2026-08-04T10:00:00+08:00",
        due_at="2026-08-04T10:01:00+08:00",
        now="2026-08-04T10:00:00+08:00",
    )
    assert opportunity_id is not None

    claimed = store.claim_initiative_opportunity(
        opportunity_id=opportunity_id,
        hardware_expression={},
        now="2026-08-04T10:01:00+08:00",
    )

    assert claimed is not None


@pytest.mark.asyncio
async def test_boot_checkin_delivery_failure_retries_then_reaches_terminal_failure(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO companion_pets(pet_id, owner_user_id, created_at)
            VALUES ('pet-boot-delivery', 'owner-boot-delivery', '2026-08-04T08:00:00+08:00')
            """
        )
        connection.execute(
            """
            INSERT INTO relationship_epochs(
                epoch_id, pet_id, started_at, start_reason
            ) VALUES (
                'epoch-boot-delivery', 'pet-boot-delivery',
                '2026-08-04T08:00:00+08:00', 'first_use'
            )
            """
        )
        connection.commit()
    store.create_boot_checkin(
        boot_event_id="boot-delivery-1",
        device_id="device-boot-delivery",
        owner_user_id="owner-boot-delivery",
        pet_id="pet-boot-delivery",
        memory_subject_id="subject-boot-delivery",
        relationship_epoch_id="epoch-boot-delivery",
        boot_reason="manual_power_on",
        occurred_at="2026-08-04T10:00:00+08:00",
        due_at="2026-08-04T10:01:00+08:00",
        now="2026-08-04T10:00:00+08:00",
    )
    delivery_port = _RecordingInitiativeDeliveryPort(
        delivery_status="delivery_failed"
    )
    mind = CompanionMind(
        store=store,
        initiative_composer=_RecordingInitiativeComposer(store),
        initiative_delivery_port=delivery_port,
        boot_checkin_delivery_window_seconds=7200,
    )

    first = await mind.run_due_work(now="2026-08-04T10:01:00+08:00")
    before_second = await mind.run_due_work(now="2026-08-04T10:15:59+08:00")
    second = await mind.run_due_work(now="2026-08-04T10:16:00+08:00")
    before_third = await mind.run_due_work(now="2026-08-04T11:15:59+08:00")
    third = await mind.run_due_work(now="2026-08-04T11:16:00+08:00")

    assert first == type(first)(claimed=1, retried=1)
    assert before_second == type(before_second)()
    assert second == type(second)(claimed=1, retried=1)
    assert before_third == type(before_third)()
    assert third == type(third)(claimed=1, failed=1)
    with store.connection() as connection:
        opportunity = connection.execute(
            "SELECT status, attempt, next_attempt_at FROM initiative_opportunities"
        ).fetchone()
        event = connection.execute(
            "SELECT status FROM companion_device_boot_events"
        ).fetchone()
        decision = connection.execute(
            "SELECT delivery_status FROM initiative_decisions"
        ).fetchone()
        feedback_count = connection.execute(
            "SELECT COUNT(*) FROM companion_evidence WHERE kind = 'initiative_feedback'"
        ).fetchone()[0]
    assert tuple(opportunity) == ("delivery_failed", 3, None)
    assert tuple(event) == ("delivery_failed",)
    assert tuple(decision) == ("delivery_failed",)
    assert feedback_count == 0


def test_connection_bid_materialization_requires_active_presence_lease(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store, _, epoch = _seed_connection_need(
        database_path,
        initiative_level="low",
    )
    now = "2026-08-03T10:05:00+08:00"
    with store.connection() as connection:
        connection.execute(
            """
            UPDATE companion_relationship_needs
            SET next_eligible_at = ?, cooldown_until = NULL,
                pending_decision_id = NULL
            WHERE owner_user_id = 'owner-1' AND pet_id = 'pet-1'
              AND memory_subject_id = 'subject-1'
              AND relationship_epoch_id = ? AND need_kind = 'connection'
            """,
            (now, epoch.epoch_id),
        )
        connection.execute(
            """
            UPDATE companion_presence_leases
            SET status = 'closed', updated_at = ?
            WHERE owner_user_id = 'owner-1' AND pet_id = 'pet-1'
              AND memory_subject_id = 'subject-1'
              AND relationship_epoch_id = ?
            """,
            (now, epoch.epoch_id),
        )
        connection.commit()

    assert store.materialize_due_connection_bids(now=now, limit=10) == 0

    with store.connection() as connection:
        connection.execute(
            """
            UPDATE companion_presence_leases
            SET status = 'active', expires_at = ?, updated_at = ?
            WHERE owner_user_id = 'owner-1' AND pet_id = 'pet-1'
              AND memory_subject_id = 'subject-1'
              AND relationship_epoch_id = ?
            """,
            ("2026-08-03T11:00:00+08:00", now, epoch.epoch_id),
        )
        connection.commit()

    assert store.materialize_due_connection_bids(now=now, limit=10) == 1
