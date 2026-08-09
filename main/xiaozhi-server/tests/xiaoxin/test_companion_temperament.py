from __future__ import annotations

from dataclasses import asdict
from itertools import product
import sqlite3

import pytest

from core.xiaoxin.companion import (
    BirthTemperament,
    CompanionMind,
    CompanionObservation,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.store import CompanionStore
from core.xiaoxin.companion.temperament import (
    TEMPERAMENT_AXIS_LEVELS,
    TEMPERAMENT_GENERATOR_VERSION,
    generate_birth_temperament,
)


GENERATED_AT = "2026-07-25T09:00:00+08:00"


def _ensure_pet(
    store: CompanionStore,
    *,
    owner_user_id: str = "owner-1",
    pet_id: str = "pet-1",
) -> BirthTemperament:
    store.ensure_subject(
        owner_user_id=owner_user_id,
        pet_id=pet_id,
        started_at=GENERATED_AT,
    )
    temperament = store.get_birth_temperament(
        owner_user_id=owner_user_id,
        pet_id=pet_id,
    )
    assert temperament is not None
    return temperament


def _commit_first_turn(
    mind: CompanionMind,
    *,
    owner_user_id: str = "owner-1",
    pet_id: str = "pet-1",
):
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-first",
            subject=CompanionSubjectContext(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id="subject-1",
                speaker_identity="confirmed",
                academic_stage="freshman",
                persistence_allowed=True,
            ),
            request_digest="digest-first",
            surface="voice",
            occurred_at=GENERATED_AT,
        )
    )
    return mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="delivered",
        ),
    )


def test_sha256_v1_frozen_vector_uses_axis_specific_semantic_enums():
    temperament = generate_birth_temperament(
        pet_id="pet-vector-1",
        generated_at=GENERATED_AT,
        source_kind="pet_created",
    )

    assert temperament == BirthTemperament(
        pet_id="pet-vector-1",
        generator_version="xiaoxin-temperament-v1",
        exploration_orientation="balanced",
        expression_energy="natural",
        thought_organization="structured",
        playfulness="restrained",
        companion_initiative="proactive",
        generated_at=GENERATED_AT,
        source_kind="pet_created",
    )


def test_all_243_semantic_combinations_pass_the_birth_contract():
    signatures = set()
    axes = tuple(TEMPERAMENT_AXIS_LEVELS)
    for values in product(*(TEMPERAMENT_AXIS_LEVELS[axis] for axis in axes)):
        dimensions = dict(zip(axes, values, strict=True))
        temperament = BirthTemperament(
            pet_id="pet-contract",
            generator_version=TEMPERAMENT_GENERATOR_VERSION,
            **dimensions,
            generated_at=GENERATED_AT,
            source_kind="pet_created",
        )
        signatures.add(tuple(asdict(temperament)[axis] for axis in axes))

    assert len(signatures) == 243


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("exploration_orientation", "high"),
        ("expression_energy", "energetic"),
        ("thought_organization", "ordered"),
        ("playfulness", "funny"),
        ("companion_initiative", "active"),
        ("source_kind", "runtime_recalculation"),
    ),
)
def test_birth_contract_rejects_generic_or_unfrozen_values(
    field_name: str,
    invalid_value: str,
):
    values = {
        "pet_id": "pet-invalid",
        "generator_version": TEMPERAMENT_GENERATOR_VERSION,
        "exploration_orientation": "balanced",
        "expression_energy": "natural",
        "thought_organization": "balanced",
        "playfulness": "lighthearted",
        "companion_initiative": "timely",
        "generated_at": GENERATED_AT,
        "source_kind": "pet_created",
    }
    values[field_name] = invalid_value

    with pytest.raises(ValueError):
        BirthTemperament(**values)


def test_new_pet_is_persisted_once_with_pet_created_source(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")

    first = _ensure_pet(store)
    second = _ensure_pet(store)

    assert first == second
    assert first.source_kind == "pet_created"
    assert first.generated_at == GENERATED_AT
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_birth_temperaments WHERE pet_id = ?",
            (first.pet_id,),
        ).fetchone()[0] == 1


def test_process_restart_and_database_reopen_do_not_redraw(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    original = _ensure_pet(CompanionStore(database_path))

    reopened = CompanionStore(database_path)
    after_restart = reopened.get_birth_temperament(
        owner_user_id="owner-1",
        pet_id="pet-1",
    )

    assert after_restart == original


def test_different_pets_may_legally_share_the_same_temperament(tmp_path):
    generated_by_signature: dict[tuple[str, ...], str] = {}
    collision: tuple[str, str] | None = None
    axes = tuple(TEMPERAMENT_AXIS_LEVELS)
    for index in range(244):
        pet_id = f"collision-pet-{index}"
        temperament = generate_birth_temperament(
            pet_id=pet_id,
            generated_at=GENERATED_AT,
            source_kind="pet_created",
        )
        signature = tuple(asdict(temperament)[axis] for axis in axes)
        previous = generated_by_signature.setdefault(signature, pet_id)
        if previous != pet_id:
            collision = previous, pet_id
            break

    assert collision is not None
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    first = _ensure_pet(store, owner_user_id="owner-a", pet_id=collision[0])
    second = _ensure_pet(store, owner_user_id="owner-b", pet_id=collision[1])

    assert tuple(asdict(first)[axis] for axis in axes) == tuple(
        asdict(second)[axis] for axis in axes
    )


def test_owner_pet_mismatch_fails_closed(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    _ensure_pet(store)

    with pytest.raises(PermissionError):
        store.get_birth_temperament(
            owner_user_id="owner-other",
            pet_id="pet-1",
        )


def test_stored_audit_mismatch_warns_and_never_overwrites(tmp_path, caplog):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    original = _ensure_pet(store)
    replacement = (
        "focused" if original.exploration_orientation != "focused" else "exploratory"
    )
    with store.connection() as connection:
        connection.execute(
            """
            UPDATE companion_birth_temperaments
            SET exploration_orientation = ?
            WHERE pet_id = ?
            """,
            (replacement, original.pet_id),
        )
        connection.commit()

    with caplog.at_level("WARNING"):
        stored = store.get_birth_temperament(
            owner_user_id="owner-1",
            pet_id="pet-1",
        )

    assert stored is not None
    assert stored.exploration_orientation == replacement
    assert "birth temperament audit mismatch" in caplog.text.lower()
    with store.connection() as connection:
        assert connection.execute(
            """
            SELECT exploration_orientation
            FROM companion_birth_temperaments
            WHERE pet_id = 'pet-1'
            """
        ).fetchone()[0] == replacement


def test_relationship_reset_and_personal_memory_purge_preserve_temperament(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    original = _ensure_pet(store)

    store.reset_relationship(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        now="2026-07-26T09:00:00+08:00",
        idempotency_key="reset-temperament",
    )
    after_reset = store.get_birth_temperament(
        owner_user_id="owner-1",
        pet_id="pet-1",
    )
    store.purge_personal_memory(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        now="2026-07-27T09:00:00+08:00",
        idempotency_key="purge-temperament",
    )
    after_purge = store.get_birth_temperament(
        owner_user_id="owner-1",
        pet_id="pet-1",
    )

    assert after_reset == original
    assert after_purge == original


def test_companion_mind_first_committed_turn_persists_temperament(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"temperament-test-secret")

    assert _commit_first_turn(mind).status == "committed"
    temperament = store.get_birth_temperament(
        owner_user_id="owner-1",
        pet_id="pet-1",
    )

    assert temperament is not None
    assert temperament.source_kind == "pet_created"
    assert temperament.generated_at == GENERATED_AT


def test_first_recorded_observation_persists_pet_created_temperament(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"temperament-observation")
    subject = CompanionSubjectContext(
        owner_user_id="owner-observed",
        pet_id="pet-observed",
        memory_subject_id="subject-observed",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )

    result = mind.observe(
        CompanionObservation(
            idempotency_key="todo-created:temperament",
            subject=subject,
            kind="todo_created",
            source_kind="miniprogram_todo",
            source_ref="todo-temperament",
            occurred_at=GENERATED_AT,
            payload={
                "todo_id": "todo-temperament",
                "title": "Complete temperament test",
                "due_at": "2026-07-26T09:00:00+08:00",
                "status": "pending",
            },
            safe_summary="User created a todo.",
        )
    )
    temperament = store.get_birth_temperament(
        owner_user_id=subject.owner_user_id,
        pet_id=subject.pet_id,
    )

    assert result.status == "recorded"
    assert temperament is not None
    assert temperament.source_kind == "pet_created"
    assert temperament.generated_at == GENERATED_AT


def test_first_deferred_observation_persists_pet_created_temperament(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")

    result = store.defer_observation(
        owner_user_id="owner-deferred",
        pet_id="pet-deferred",
        idempotency_key="todo-deferred:temperament",
        kind="todo_created",
        source_kind="miniprogram_todo",
        source_ref="todo-deferred",
        occurred_at=GENERATED_AT,
        payload={"todo_id": "todo-deferred", "status": "pending"},
        safe_summary="User created a todo before subject resolution.",
        queued_reason="ambiguous_subject",
    )
    temperament = store.get_birth_temperament(
        owner_user_id="owner-deferred",
        pet_id="pet-deferred",
    )

    assert result.status == "deferred"
    assert temperament is not None
    assert temperament.source_kind == "pet_created"


@pytest.mark.parametrize(
    "field_name",
    (
        "exploration_orientation",
        "expression_energy",
        "thought_organization",
        "playfulness",
        "companion_initiative",
    ),
)
def test_database_checks_reject_invalid_axis_values(tmp_path, field_name: str):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    _ensure_pet(store)

    with store.connection() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            f"""
            UPDATE companion_birth_temperaments
            SET {field_name} = 'invalid'
            WHERE pet_id = 'pet-1'
            """
        )
