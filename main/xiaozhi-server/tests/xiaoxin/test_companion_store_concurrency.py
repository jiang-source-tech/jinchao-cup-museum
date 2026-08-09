from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
from threading import Barrier
import time

from core.xiaoxin.companion import (
    CompanionPolicy,
    CompanionTurnOutcome,
    PreparedCompanionTurn,
)
from core.xiaoxin.companion.store import CompanionStore


def _prepared(turn_id: str) -> PreparedCompanionTurn:
    return PreparedCompanionTurn(
        turn_id=turn_id,
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=None,
        request_digest=f"digest-{turn_id}",
        occurred_at="2026-07-18T10:00:00+08:00",
        prepared_token=f"token-{turn_id}",
        policy=CompanionPolicy(
            xiaoxin_age=1,
            relationship_stage="first_meeting",
            response_length="standard",
            question_budget=1,
            memory_reference_budget=0,
            initiative_level="low",
            emotional_posture="warm",
            closure_style="concise",
        ),
        persistence_allowed=True,
    )


OUTCOME = CompanionTurnOutcome(
    visible_response="你好",
    assistant_action="reply",
    delivery_status="delivered",
)


def test_two_writers_commit_without_lost_updates(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    store.ensure_subject(
        owner_user_id="owner-1",
        pet_id="pet-1",
        started_at="2026-07-18T10:00:00+08:00",
    )
    barrier = Barrier(2)

    def commit(turn_id: str) -> str:
        barrier.wait()
        return store.commit_turn(_prepared(turn_id), OUTCOME).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = set(executor.map(commit, ("turn-1", "turn-2")))

    assert statuses == {"committed"}
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_turns"
        ).fetchone()[0] == 2


def test_concurrent_retry_commits_one_turn_once(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    store.ensure_subject(
        owner_user_id="owner-1",
        pet_id="pet-1",
        started_at="2026-07-18T10:00:00+08:00",
    )
    barrier = Barrier(2)

    def commit(_: int) -> str:
        barrier.wait()
        return store.commit_turn(_prepared("turn-1"), OUTCOME).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(commit, (1, 2)))

    assert statuses == ["already_committed", "committed"]

    reopened = CompanionStore(database_path)
    with reopened.connection() as connection:
        row = connection.execute(
            "SELECT request_digest FROM companion_turns WHERE turn_id = 'turn-1'"
        ).fetchone()
    assert row["request_digest"] == "digest-turn-1"


def test_concurrent_pet_initialization_persists_one_birth_temperament(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    barrier = Barrier(2)

    def initialize(_: int):
        barrier.wait()
        store.ensure_subject(
            owner_user_id="owner-1",
            pet_id="pet-1",
            started_at="2026-07-25T09:00:00+08:00",
        )
        return store.get_birth_temperament(
            owner_user_id="owner-1",
            pet_id="pet-1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(initialize, (1, 2)))

    assert results[0] == results[1]
    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_birth_temperaments WHERE pet_id = 'pet-1'"
        ).fetchone()[0] == 1


def test_pet_reflection_guard_serializes_across_processes(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    started_path = tmp_path / "child-started"
    acquired_path = tmp_path / "child-acquired"
    store = CompanionStore(database_path)
    child_script = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "from core.xiaoxin.companion.store import CompanionStore",
            "database_path, started_path, acquired_path = map(Path, sys.argv[1:])",
            "started_path.write_text('started', encoding='utf-8')",
            "with CompanionStore(database_path).pet_reflection_guard('pet-1'):",
            "    acquired_path.write_text('acquired', encoding='utf-8')",
        )
    )

    with store.pet_reflection_guard("pet-1"):
        child = subprocess.Popen(
            (
                sys.executable,
                "-c",
                child_script,
                str(database_path),
                str(started_path),
                str(acquired_path),
            )
        )
        deadline = time.monotonic() + 5
        while not started_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started_path.exists()
        time.sleep(0.05)
        assert acquired_path.exists() is False

    assert child.wait(timeout=5) == 0
    assert acquired_path.read_text(encoding="utf-8") == "acquired"
