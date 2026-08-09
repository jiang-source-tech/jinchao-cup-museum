from __future__ import annotations

from dataclasses import replace

from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionMind,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionVAEvent,
)
from core.xiaoxin.companion.store import CompanionStore
from core.xiaoxin.companion.va import (
    AROUSAL_HALF_LIFE_SECONDS,
    BASELINE_AROUSAL,
    BASELINE_VALENCE,
    EVENT_SPECS,
    SNAPSHOT_TTL_SECONDS,
    VALENCE_HALF_LIFE_SECONDS,
    apply_event,
    baseline,
    decay,
    semantic_projection,
)


def _subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-va",
        pet_id="pet-va",
        memory_subject_id="subject-va",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


def _mind(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    store.ensure_subject(
        owner_user_id="owner-va",
        pet_id="pet-va",
        started_at="2026-07-25T08:00:00+08:00",
    )
    return store, CompanionMind(store=store, token_secret=b"va-story")


def _event(
    store: CompanionStore,
    event_id: str,
    kind: str,
    occurred_at: str,
    *,
    relationship_epoch_id: str | None = None,
    received_at: str | None = None,
) -> CompanionVAEvent:
    epoch = store.get_active_epoch(owner_user_id="owner-va", pet_id="pet-va")
    assert epoch is not None
    return CompanionVAEvent(
        event_id=event_id,
        subject=_subject(),
        relationship_epoch_id=relationship_epoch_id or epoch.epoch_id,
        kind=kind,
        occurred_at=occurred_at,
        received_at=received_at or occurred_at,
        source_kind="turn_analysis",
        source_ref=f"turn:{event_id}",
    )


def _contains_raw_coordinates(value: object) -> bool:
    if isinstance(value, dict):
        if {"valence", "arousal"} & set(value):
            return True
        return any(_contains_raw_coordinates(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_raw_coordinates(item) for item in value)
    return False


def test_va_model_covers_events_decay_expiry_and_safety_projection(tmp_path):
    initial = baseline(
        now="2026-07-25T09:00:00+08:00",
        age=2,
        relationship_stage="attuned",
    )
    assert (initial.valence, initial.arousal) == (
        BASELINE_VALENCE,
        BASELINE_AROUSAL,
    )
    for kind, (target_v, target_a, _, _) in EVENT_SPECS.items():
        state = apply_event(
            initial,
            kind=kind,
            occurred_at="2026-07-25T09:01:00+08:00",
            age=2,
            relationship_stage="attuned",
        )
        assert -1000 <= state.valence <= 1000
        assert -1000 <= state.arousal <= 1000
        assert abs(target_v - state.valence) < abs(target_v - initial.valence)
        if kind not in {"user_distress", "negative_feedback"}:
            assert abs(target_a - state.arousal) < abs(target_a - initial.arousal)

    success = apply_event(
        initial,
        kind="shared_success",
        occurred_at="2026-07-25T09:01:00+08:00",
        age=2,
        relationship_stage="attuned",
    )
    valence_half = decay(success, now="2026-07-25T10:31:00+08:00")
    arousal_half = decay(success, now="2026-07-25T09:36:00+08:00")
    assert abs(valence_half.valence - (BASELINE_VALENCE + success.valence) / 2) <= 1
    assert abs(arousal_half.arousal - success.arousal / 2) <= 1
    assert VALENCE_HALF_LIFE_SECONDS == 90 * 60
    assert AROUSAL_HALF_LIFE_SECONDS == 35 * 60
    expired = decay(success, now="2026-07-25T15:01:00+08:00")
    assert SNAPSHOT_TTL_SECONDS == 6 * 60 * 60
    assert (expired.valence, expired.arousal) == (150, 0)
    assert semantic_projection(success)["hardware_expression"] == {
        "kind": "bright_pulse",
        "intensity": "medium",
    }
    first_meeting_success = replace(success, relationship_stage="first_meeting")
    assert (
        semantic_projection(first_meeting_success)["hardware_expression"]["intensity"]
        == "low"
    )

    store, mind = _mind(tmp_path)
    mind.observe(
        _event(store, "va-distress", "user_distress", "2026-07-25T10:00:00+08:00")
    )
    projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject(), surface="voice", now="2026-07-25T10:01:00+08:00"
        )
    )
    assert projection.payload["policy"]["emotional_posture"] == "supportive"
    assert projection.payload["policy"]["question_budget"] == 0
    assert not _contains_raw_coordinates(projection.payload)

    mind.observe(
        _event(
            store,
            "va-negative",
            "negative_feedback",
            "2026-07-25T10:02:00+08:00",
        )
    )
    epoch = store.get_active_epoch(owner_user_id="owner-va", pet_id="pet-va")
    assert epoch is not None
    safe = store.load_va_projection(
        owner_user_id="owner-va",
        pet_id="pet-va",
        memory_subject_id="subject-va",
        relationship_epoch_id=epoch.epoch_id,
        now="2026-07-25T10:03:00+08:00",
        xiaoxin_age=2,
        relationship_stage="first_meeting",
    )
    assert safe["emotional_posture"] == "receptive_brief"
    assert safe["may_create_initiative"] is False
    assert semantic_projection(expired)["emotional_posture"] == "warm_neutral"


def test_va_event_replay_order_restart_and_invalid_snapshots_fail_closed(tmp_path):
    store, mind = _mind(tmp_path)
    event = _event(
        store, "va-success", "shared_success", "2026-07-25T10:00:00.500000+08:00"
    )
    assert mind.observe(event).status == "applied"
    assert mind.observe(event).status == "duplicate"
    with store.connection() as connection:
        before = tuple(
            connection.execute(
                "SELECT valence, arousal, observed_at, expires_at FROM companion_va_snapshots"
            ).fetchone()
        )

    restarted = CompanionMind(
        store=CompanionStore(store.database_path), token_secret=b"va-restart"
    )
    first = restarted.project(
        CompanionProjectionRequest(
            subject=_subject(), surface="voice", now="2026-07-25T10:45:00+08:00"
        )
    )
    second = restarted.project(
        CompanionProjectionRequest(
            subject=_subject(), surface="voice", now="2026-07-25T10:45:00+08:00"
        )
    )
    assert first.payload == second.payload
    with store.connection() as connection:
        after_read = tuple(
            connection.execute(
                "SELECT valence, arousal, observed_at, expires_at FROM companion_va_snapshots"
            ).fetchone()
        )
    assert after_read == before
    assert (
        restarted.observe(
            _event(
                store,
                "va-old",
                "ordinary_chat",
                "2026-07-25T10:00:00.499999+08:00",
                received_at="2026-07-25T10:01:00+08:00",
            )
        ).status
        == "ignored_out_of_order"
    )

    with store.connection() as connection:
        connection.execute(
            """
            UPDATE companion_va_snapshots
            SET model_version = 'future-model', observed_at = '2099-01-01T00:00:00+08:00',
                expires_at = '2099-01-01T06:00:00+08:00'
            """
        )
        connection.commit()
    epoch = store.get_active_epoch(owner_user_id="owner-va", pet_id="pet-va")
    assert epoch is not None
    failed_closed = store.load_va_projection(
        owner_user_id="owner-va",
        pet_id="pet-va",
        memory_subject_id="subject-va",
        relationship_epoch_id=epoch.epoch_id,
        now="2026-07-25T11:00:00+08:00",
        xiaoxin_age=2,
        relationship_stage="first_meeting",
    )
    assert failed_closed["emotional_posture"] == "warm_neutral"
    assert (
        restarted.observe(
            _event(
                store,
                "va-after-corrupt",
                "ordinary_chat",
                "2026-07-25T11:01:00+08:00",
            )
        ).status
        == "applied"
    )
    with store.connection() as connection:
        repaired = connection.execute(
            "SELECT model_version, observed_at FROM companion_va_snapshots"
        ).fetchone()
    assert repaired["model_version"] == "companion-va-v1"
    assert repaired["observed_at"] == "2026-07-25T11:01:00+08:00"


def test_va_reset_purge_credentials_and_low_battery_remain_isolated(tmp_path):
    store, mind = _mind(tmp_path)
    event = _event(
        store, "va-reset-proof", "shared_success", "2026-07-25T10:00:00+08:00"
    )
    baseline_initiative = mind.project(
        CompanionProjectionRequest(
            subject=_subject(), surface="voice", now="2026-07-25T09:59:00+08:00"
        )
    ).payload["policy"]["initiative_level"]
    mind.observe(event)
    bright = mind.project(
        CompanionProjectionRequest(
            subject=_subject(), surface="hardware", now="2026-07-25T10:01:00+08:00"
        )
    )
    low_battery = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="hardware",
            now="2026-07-25T10:01:00+08:00",
            device_state="low_battery",
        )
    )
    assert bright.payload["hardware_expression"]["intensity"] == "low"
    assert low_battery.payload["hardware_expression"]["intensity"] == "low"
    assert low_battery.payload["hardware_expression"]["kind"] == "low_power"
    assert (
        mind.project(
            CompanionProjectionRequest(
                subject=_subject(), surface="voice", now="2026-07-25T10:01:00+08:00"
            )
        ).payload["policy"]["initiative_level"]
        == baseline_initiative
    )

    mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={"now": "2026-07-25T11:00:00+08:00", "idempotency_key": "va-reset"},
        )
    )
    assert mind.observe(event).status == "duplicate"
    stale = _event(
        store,
        "va-late-old-epoch",
        "shared_success",
        "2026-07-25T10:30:00+08:00",
        relationship_epoch_id=event.relationship_epoch_id,
        received_at="2026-07-25T11:01:00+08:00",
    )
    assert mind.observe(stale).status == "ignored_stale_epoch"
    second_event = _event(
        store,
        "va-purge-proof",
        "ordinary_chat",
        "2026-07-25T11:01:00+08:00",
    )
    assert mind.observe(second_event).status == "applied"
    mind.apply_control(
        CompanionControlCommand(
            action="purge_personal_memory",
            subject=_subject(),
            payload={"now": "2026-07-25T12:00:00+08:00", "idempotency_key": "va-purge"},
        )
    )
    assert mind.observe(second_event).status == "duplicate"
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM companion_va_snapshots"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM companion_va_events").fetchone()[0]
            == 3
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(companion_va_events)")
        }
        assert not {"kind", "occurred_at", "memory_subject_id"} & columns
