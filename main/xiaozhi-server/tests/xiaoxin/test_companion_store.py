from __future__ import annotations

from dataclasses import replace
import sqlite3

import pytest

from core.xiaoxin.companion import (
    CompanionEvidence,
    CompanionPolicy,
    CompanionTurnOutcome,
    PreparedCompanionTurn,
)
from core.xiaoxin.companion.store import (
    SCHEMA_VERSION,
    CompanionIdempotencyConflict,
    CompanionStore,
    PendingCompanionJob,
)


EXPECTED_TABLES = {
    "companion_turns",
    "companion_evidence",
    "evidence_relations",
    "relationship_epochs",
    "session_capsules",
    "capsule_evidence",
    "companion_adjustments",
    "adjustment_evidence",
    "adjustment_evidence_qualification",
    "companion_chapters",
    "chapter_evidence",
    "companion_growth_moments",
    "companion_academic_states",
    "companion_academic_transitions",
    "memory_controls",
    "consolidation_jobs",
    "initiative_decisions",
    "initiative_opportunities",
    "companion_relationship_needs",
}


def test_adjustment_v15_migration_marks_legacy_evidence_unconfirmed():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE companion_evidence (
            evidence_id TEXT PRIMARY KEY
        );
        CREATE TABLE companion_adjustments (
            adjustment_id TEXT PRIMARY KEY,
            pet_id TEXT NOT NULL,
            relationship_epoch_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            valid_until TEXT
        );
        INSERT INTO companion_evidence(evidence_id) VALUES ('legacy-evidence');
        INSERT INTO companion_adjustments(
            adjustment_id, pet_id, relationship_epoch_id, status, created_at
        ) VALUES (
            'legacy-adjustment', 'pet-1', 'epoch-1', 'active',
            '2026-07-01T10:00:00+08:00'
        );
        """
    )

    CompanionStore._migrate_adjustments_v15(connection)

    evidence = connection.execute(
        """
        SELECT speaker_identity
        FROM companion_evidence
        WHERE evidence_id = 'legacy-evidence'
        """
    ).fetchone()
    adjustment_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(companion_adjustments)")
    }
    indexes = {
        row["name"]
        for row in connection.execute("PRAGMA index_list(companion_adjustments)")
    }
    legacy_adjustment = connection.execute(
        """
        SELECT status, behavior_key, context_scope, direction, valid_until
        FROM companion_adjustments
        WHERE adjustment_id = 'legacy-adjustment'
        """
    ).fetchone()

    assert evidence["speaker_identity"] == "unknown"
    assert {"behavior_key", "context_scope", "direction"} <= adjustment_columns
    assert "uq_companion_adjustments_active_behavior" in indexes
    assert tuple(legacy_adjustment)[:4] == ("candidate", None, None, None)
    assert legacy_adjustment["valid_until"] is not None
    connection.close()


def test_commit_turn_persists_evidence_speaker_identity(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    store.commit_turn(
        _prepared(),
        _outcome(),
        evidence=(
            replace(
                _user_evidence("evidence-unknown-speaker"),
                speaker_identity="unknown",
            ),
        ),
    )

    with store.connection() as connection:
        speaker_identity = connection.execute(
            """
            SELECT speaker_identity
            FROM companion_evidence
            WHERE evidence_id = 'evidence-unknown-speaker'
            """
        ).fetchone()[0]

    assert speaker_identity == "unknown"


def _prepared(*, request_digest: str = "request-digest") -> PreparedCompanionTurn:
    return PreparedCompanionTurn(
        turn_id="turn-1",
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=None,
        request_digest=request_digest,
        occurred_at="2026-07-18T10:00:00+08:00",
        prepared_token="prepared-token",
        policy=CompanionPolicy(
            xiaoxin_age=2,
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


def _outcome(*, response: str = "你好") -> CompanionTurnOutcome:
    return CompanionTurnOutcome(
        visible_response=response,
        assistant_action="reply",
        delivery_status="delivered",
    )


def _user_evidence(evidence_id: str, *, pet_id: str = "pet-1") -> CompanionEvidence:
    return CompanionEvidence(
        evidence_id=evidence_id,
        pet_id=pet_id,
        memory_subject_id="subject-1",
        ownership_scope="user",
        relationship_epoch_id=None,
        kind="explicit_preference",
        content={"preference": "short_answers"},
        source_kind="turn",
        source_ref="turn-1",
        source_summary="用户明确要求简短回答。",
        attribution="explicit_user_statement",
        confidence=1.0,
        occurred_at="2026-07-18T10:00:00+08:00",
        retention="long_term",
        status="active",
        prompt_eligible=True,
    )


def _job(job_id: str, idempotency_key: str) -> PendingCompanionJob:
    return PendingCompanionJob(
        job_id=job_id,
        pet_id="pet-1",
        relationship_epoch_id=None,
        job_kind="session_consolidation",
        idempotency_key=idempotency_key,
        payload={"turn_id": "turn-1"},
        due_at="2026-07-18T10:01:00+08:00",
        schema_version="companion-reflection-v1",
    )


def test_store_initializes_the_v2_schema_and_sqlite_safety_pragmas(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"

    store = CompanionStore(database_path)

    with store.connection() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        schema_sql = "\n".join(
            row[0] or ""
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        ).lower()

    assert EXPECTED_TABLES <= tables
    assert journal_mode.lower() == "wal"
    assert foreign_keys == 1
    assert busy_timeout >= 5000
    assert "c_language" not in schema_sql
    assert "competition" not in schema_sql


def test_store_upgrades_existing_turn_table_with_relationship_epoch_audit(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE relationship_epochs (
                epoch_id TEXT PRIMARY KEY,
                pet_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                start_reason TEXT NOT NULL,
                end_reason TEXT,
                UNIQUE(epoch_id, pet_id),
                CHECK (
                    (ended_at IS NULL AND end_reason IS NULL)
                    OR (ended_at IS NOT NULL AND end_reason IS NOT NULL)
                )
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE companion_turns (
                turn_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                pet_id TEXT NOT NULL,
                memory_subject_id TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                outcome_digest TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY(turn_id, pet_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO relationship_epochs(
                epoch_id, pet_id, started_at, start_reason
            ) VALUES (
                'epoch-v2', 'pet-1', '2026-07-18T09:00:00+08:00', 'first_use'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO companion_turns(
                turn_id, owner_user_id, pet_id, memory_subject_id,
                request_digest, outcome_digest, occurred_at,
                committed_at, status
            ) VALUES (
                'turn-v2', 'owner-1', 'pet-1', 'subject-1',
                'request-v2', 'outcome-v2', '2026-07-18T10:00:00+08:00',
                '2026-07-18T10:00:01+08:00', 'delivered'
            )
            """
        )
        connection.execute("PRAGMA user_version = 2")
        connection.commit()

    store = CompanionStore(database_path)

    with store.connection() as connection:
        column_rows = connection.execute(
            "PRAGMA table_info(companion_turns)"
        ).fetchall()
        columns = {row["name"]: row for row in column_rows}
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(companion_turns)"
        ).fetchall()
        migrated_turn = connection.execute(
            """
            SELECT relationship_epoch_id, policy_version
            FROM companion_turns
            WHERE turn_id = 'turn-v2' AND pet_id = 'pet-1'
            """
        ).fetchone()
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert "relationship_epoch_id" in columns
    assert columns["relationship_epoch_id"]["notnull"] == 1
    assert columns["policy_version"]["notnull"] == 1
    assert any(row["table"] == "relationship_epochs" for row in foreign_keys)
    assert migrated_turn["relationship_epoch_id"] == "epoch-v2"
    assert migrated_turn["policy_version"] == "companion-policy-v1"
    assert user_version == SCHEMA_VERSION


def test_store_repairs_nullable_policy_version_in_claimed_v4_schema(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE relationship_epochs (
                epoch_id TEXT PRIMARY KEY,
                pet_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                start_reason TEXT NOT NULL,
                end_reason TEXT,
                UNIQUE(epoch_id, pet_id),
                CHECK (
                    (ended_at IS NULL AND end_reason IS NULL)
                    OR (ended_at IS NOT NULL AND end_reason IS NOT NULL)
                )
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE companion_turns (
                turn_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                pet_id TEXT NOT NULL,
                memory_subject_id TEXT NOT NULL,
                relationship_epoch_id TEXT NOT NULL,
                policy_version TEXT,
                request_digest TEXT NOT NULL,
                outcome_digest TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY(turn_id, pet_id),
                FOREIGN KEY(relationship_epoch_id, pet_id)
                    REFERENCES relationship_epochs(epoch_id, pet_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO relationship_epochs(
                epoch_id, pet_id, started_at, start_reason
            ) VALUES (
                'epoch-v4', 'pet-1', '2026-07-18T09:00:00+08:00', 'first_use'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO companion_turns(
                turn_id, owner_user_id, pet_id, memory_subject_id,
                relationship_epoch_id, policy_version, request_digest,
                outcome_digest, occurred_at, committed_at, status
            ) VALUES (
                'turn-v4', 'owner-1', 'pet-1', 'subject-1', 'epoch-v4', NULL,
                'request-v4', 'outcome-v4', '2026-07-18T10:00:00+08:00',
                '2026-07-18T10:00:01+08:00', 'delivered'
            )
            """
        )
        connection.execute("PRAGMA user_version = 4")
        connection.commit()

    store = CompanionStore(database_path)

    with store.connection() as connection:
        columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(companion_turns)")
        }
        migrated_turn = connection.execute(
            """
            SELECT policy_version
            FROM companion_turns
            WHERE turn_id = 'turn-v4' AND pet_id = 'pet-1'
            """
        ).fetchone()

    assert columns["policy_version"]["notnull"] == 1
    assert migrated_turn["policy_version"] == "companion-policy-v1"


def test_store_migrates_v4_evidence_and_adds_observation_audit_schema(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE companion_evidence (
                evidence_id TEXT PRIMARY KEY,
                pet_id TEXT NOT NULL,
                memory_subject_id TEXT NOT NULL,
                ownership_scope TEXT NOT NULL,
                relationship_epoch_id TEXT,
                kind TEXT NOT NULL,
                content_json TEXT NOT NULL,
                content_version INTEGER NOT NULL DEFAULT 1,
                source_kind TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                source_summary TEXT NOT NULL,
                attribution TEXT NOT NULL,
                confidence REAL NOT NULL,
                occurred_at TEXT NOT NULL,
                retention TEXT NOT NULL,
                status TEXT NOT NULL,
                prompt_eligible INTEGER NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(evidence_id, pet_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO companion_evidence(
                evidence_id, pet_id, memory_subject_id, ownership_scope,
                relationship_epoch_id, kind, content_json, source_kind,
                source_ref, source_summary, attribution, confidence,
                occurred_at, retention, status, prompt_eligible, expires_at,
                created_at
            ) VALUES (
                'evidence-v4', 'pet-1', 'subject-1', 'user', NULL,
                'profile_fact', '{"fact_key":"preferred_name","value":"小林"}',
                'turn', 'turn-v4', '用户明确给出了称呼。',
                'explicit_user_statement', 1.0,
                '2026-07-18T10:00:00+08:00', 'long_term', 'active', 1,
                NULL, '2026-07-18T10:00:01+08:00'
            )
            """
        )
        connection.execute("PRAGMA user_version = 4")
        connection.commit()

    store = CompanionStore(database_path)

    with store.connection() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(companion_evidence)")
        }
        migrated = connection.execute(
            """
            SELECT fact_key, importance, sensitivity, valid_from, valid_until
            FROM companion_evidence WHERE evidence_id = 'evidence-v4'
            """
        ).fetchone()
        observation_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name IN (
                    'companion_observations', 'observation_evidence',
                    'pending_companion_observations'
                )
                """
            )
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert {
        "fact_key",
        "importance",
        "sensitivity",
        "valid_from",
        "valid_until",
    } <= columns
    assert migrated["fact_key"] == "preferred_name"
    assert migrated["importance"] == 0.5
    assert migrated["sensitivity"] == "private"
    assert migrated["valid_from"] == "2026-07-18T10:00:00+08:00"
    assert migrated["valid_until"] is None
    assert observation_tables == {
        "companion_observations",
        "observation_evidence",
        "pending_companion_observations",
    }
    assert user_version == SCHEMA_VERSION


def test_store_migrates_v6_pending_queue_to_bounded_retry_metadata(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO companion_pets(pet_id, owner_user_id, created_at)
            VALUES ('pet-1', 'owner-1', '2026-07-20T10:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO pending_companion_observations(
                observation_id, idempotency_key, owner_user_id, pet_id,
                kind, source_kind, source_ref, payload_json, pending_digest,
                safe_summary, occurred_at, queued_reason, status,
                attempt_count, last_error_code, expires_at, created_at
            ) VALUES (
                'observation-1', 'pending-1', 'owner-1', 'pet-1',
                'todo_created', 'miniprogram_todo', 'todo-1', '{}', 'digest-1',
                '用户创建了一项未来待办。', '2026-07-20T10:00:00+00:00',
                'ambiguous_subject', 'pending', 0, NULL,
                '2026-08-19T10:00:00+00:00', '2026-07-20T10:00:00+00:00'
            )
            """
        )
        connection.execute(
            "ALTER TABLE pending_companion_observations DROP COLUMN attempt_count"
        )
        connection.execute(
            "ALTER TABLE pending_companion_observations DROP COLUMN last_error_code"
        )
        connection.execute(
            "ALTER TABLE pending_companion_observations DROP COLUMN expires_at"
        )
        connection.execute("PRAGMA user_version = 6")
        connection.commit()

    migrated_store = CompanionStore(database_path)
    with migrated_store.connection() as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(pending_companion_observations)"
            )
        }
        row = connection.execute(
            """
            SELECT attempt_count, last_error_code, expires_at
            FROM pending_companion_observations
            WHERE observation_id = 'observation-1'
            """
        ).fetchone()
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert {"attempt_count", "last_error_code", "expires_at"} <= columns
    assert row["attempt_count"] == 0
    assert row["last_error_code"] is None
    assert row["expires_at"].startswith("2026-08-19T10:00:00")
    assert user_version == SCHEMA_VERSION


def test_store_migrates_v10_to_v11_initiative_opportunity_schema(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    with store.connection() as connection:
        connection.execute("DROP TABLE initiative_opportunities")
        connection.execute("PRAGMA user_version = 10")
        connection.commit()

    migrated = CompanionStore(database_path)

    with migrated.connection() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(initiative_opportunities)"
            )
        }
    assert version == SCHEMA_VERSION
    assert {
        "opportunity_id",
        "opportunity_kind",
        "evidence_ids_json",
        "due_at",
        "status",
        "decision_id",
        "delivery_id",
        "outcome_code",
    } <= columns


def test_store_migrates_v20_deferred_schema_without_losing_opportunities(
    tmp_path,
):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    with store.connection() as connection:
        connection.execute("DROP INDEX idx_connection_bid_single_active")
        connection.execute("DROP TABLE initiative_opportunities")
        connection.executescript(
            """
            CREATE TABLE initiative_opportunities (
                opportunity_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                pet_id TEXT NOT NULL,
                memory_subject_id TEXT NOT NULL,
                relationship_epoch_id TEXT NOT NULL,
                opportunity_kind TEXT NOT NULL CHECK (
                    opportunity_kind IN (
                        'followup', 'reminder_result', 'goal_progress',
                        'future_event', 'celebration', 'checkin',
                        'connection_bid'
                    )
                ),
                reason_code TEXT NOT NULL,
                evidence_ids_json TEXT NOT NULL,
                safe_brief TEXT NOT NULL,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'scheduled', 'claimed', 'delivering', 'delivered',
                        'blocked', 'delivery_failed', 'invalidated'
                    )
                ),
                attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
                lease_until TEXT,
                next_attempt_at TEXT,
                decision_id TEXT UNIQUE,
                delivery_id TEXT,
                outcome_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(relationship_epoch_id, pet_id)
                    REFERENCES relationship_epochs(epoch_id, pet_id),
                FOREIGN KEY(decision_id)
                    REFERENCES initiative_decisions(decision_id)
            );
            CREATE INDEX idx_initiative_opportunities_due
            ON initiative_opportunities(
                status, due_at, next_attempt_at, lease_until
            );
            CREATE UNIQUE INDEX idx_connection_bid_single_active
            ON initiative_opportunities(
                owner_user_id, pet_id, memory_subject_id,
                relationship_epoch_id, opportunity_kind
            )
            WHERE opportunity_kind = 'connection_bid'
              AND status IN ('scheduled', 'claimed', 'delivering', 'delivered');
            """
        )
        connection.execute(
            """
            INSERT INTO companion_pets(pet_id, owner_user_id, created_at)
            VALUES ('pet-v20', 'owner-v20', '2026-08-03T10:00:00+08:00')
            """
        )
        connection.execute(
            """
            INSERT INTO relationship_epochs(
                epoch_id, pet_id, started_at, start_reason
            ) VALUES (
                'epoch-v20', 'pet-v20',
                '2026-08-03T10:00:00+08:00', 'first_use'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO initiative_opportunities(
                opportunity_id, owner_user_id, pet_id, memory_subject_id,
                relationship_epoch_id, opportunity_kind, reason_code,
                evidence_ids_json, safe_brief, due_at, status,
                created_at, updated_at
            ) VALUES (
                'opportunity-v20', 'owner-v20', 'pet-v20', 'subject-v20',
                'epoch-v20', 'connection_bid', 'relationship_connection_due',
                '["evidence-v20"]',
                '旧机会仍需保留。', '2026-08-04T10:00:00+08:00', 'scheduled',
                '2026-08-03T10:00:00+08:00', '2026-08-03T10:00:00+08:00'
            )
            """
        )
        connection.execute("PRAGMA user_version = 20")
        connection.commit()

    migrated = CompanionStore(database_path)
    with migrated.connection() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        opportunity = connection.execute(
            """
            SELECT opportunity_kind, reason_code
            FROM initiative_opportunities
            WHERE opportunity_id = 'opportunity-v20'
            """
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        opportunity_schema = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'initiative_opportunities'
            """
        ).fetchone()[0]

    assert version == SCHEMA_VERSION
    assert tuple(opportunity) == (
        "connection_bid",
        "relationship_connection_due",
    )
    assert "companion_relationship_needs" in tables
    assert "connection_bid" in opportunity_schema
    assert "deferred" in opportunity_schema


def test_store_migrates_v7_to_short_term_turn_source_schema(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    with store.connection() as connection:
        connection.execute("DROP TABLE companion_turn_sources")
        connection.execute("PRAGMA user_version = 7")
        connection.commit()

    migrated = CompanionStore(database_path)
    with migrated.connection() as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(companion_turn_sources)"
            )
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert {
        "turn_id",
        "pet_id",
        "memory_subject_id",
        "source_text",
        "source_digest",
        "occurred_at",
        "expires_at",
    } <= columns
    assert user_version == SCHEMA_VERSION


def test_store_refuses_v3_when_historical_turn_epoch_cannot_be_resolved(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE companion_turns (
                turn_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                pet_id TEXT NOT NULL,
                memory_subject_id TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                outcome_digest TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY(turn_id, pet_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO companion_turns(
                turn_id, owner_user_id, pet_id, memory_subject_id,
                request_digest, outcome_digest, occurred_at,
                committed_at, status
            ) VALUES (
                'orphan-turn', 'owner-1', 'pet-1', 'subject-1',
                'request', 'outcome', '2026-07-18T10:00:00+08:00',
                '2026-07-18T10:00:01+08:00', 'delivered'
            )
            """
        )
        connection.execute("PRAGMA user_version = 2")
        connection.commit()

    with pytest.raises(sqlite3.DatabaseError, match="exactly one relationship epoch"):
        CompanionStore(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(companion_turns)")
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert "relationship_epoch_id" not in columns
    assert user_version == 2


def test_store_repairs_intermediate_v3_nullable_turn_epoch_schema(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE relationship_epochs (
                epoch_id TEXT PRIMARY KEY,
                pet_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                start_reason TEXT NOT NULL,
                end_reason TEXT,
                UNIQUE(epoch_id, pet_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE companion_turns (
                turn_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                pet_id TEXT NOT NULL,
                memory_subject_id TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                outcome_digest TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                relationship_epoch_id TEXT,
                PRIMARY KEY(turn_id, pet_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO relationship_epochs(
                epoch_id, pet_id, started_at, start_reason
            ) VALUES (
                'epoch-intermediate', 'pet-1',
                '2026-07-18T09:00:00+08:00', 'first_use'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO companion_turns(
                turn_id, owner_user_id, pet_id, memory_subject_id,
                request_digest, outcome_digest, occurred_at,
                committed_at, status, relationship_epoch_id
            ) VALUES (
                'turn-intermediate', 'owner-1', 'pet-1', 'subject-1',
                'request', 'outcome', '2026-07-18T10:00:00+08:00',
                '2026-07-18T10:00:01+08:00', 'delivered', NULL
            )
            """
        )
        connection.execute("PRAGMA user_version = 3")
        connection.commit()

    store = CompanionStore(database_path)

    with store.connection() as connection:
        columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(companion_turns)")
        }
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(companion_turns)"
        ).fetchall()
        epoch_id = connection.execute(
            """
            SELECT relationship_epoch_id FROM companion_turns
            WHERE turn_id = 'turn-intermediate'
            """
        ).fetchone()[0]

    assert columns["relationship_epoch_id"]["notnull"] == 1
    assert any(row["table"] == "relationship_epochs" for row in foreign_keys)
    assert epoch_id == "epoch-intermediate"


def test_turn_commit_is_idempotent_and_rejects_changed_content(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    store.ensure_subject(
        owner_user_id="owner-1",
        pet_id="pet-1",
        started_at="2026-07-18T10:00:00+08:00",
    )

    first = store.commit_turn(_prepared(), _outcome())
    retry = store.commit_turn(_prepared(), _outcome())

    assert first.status == "committed"
    assert retry.status == "already_committed"

    with pytest.raises(CompanionIdempotencyConflict):
        store.commit_turn(_prepared(request_digest="different"), _outcome())


def test_turn_evidence_and_jobs_commit_atomically(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    store.ensure_subject(
        owner_user_id="owner-1",
        pet_id="pet-1",
        started_at="2026-07-18T10:00:00+08:00",
    )

    result = store.commit_turn(
        _prepared(),
        _outcome(),
        evidence=(_user_evidence("evidence-1"),),
        jobs=(_job("job-1", "job-key-1"),),
    )

    assert result.evidence_ids == ("evidence-1",)
    assert result.job_ids == ("job-1",)

    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_turns"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_evidence"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM consolidation_jobs"
        ).fetchone()[0] == 1

    second = replace(
        _prepared(request_digest="request-digest-2"),
        turn_id="turn-2",
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.commit_turn(
            second,
            _outcome(response="第二轮"),
            evidence=(_user_evidence("evidence-2"),),
            jobs=(_job("job-2", "job-key-1"),),
        )

    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_turns WHERE turn_id = 'turn-2'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_evidence WHERE evidence_id = 'evidence-2'"
        ).fetchone()[0] == 0


def test_schema_enforces_epoch_scope_and_prompt_eligibility(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")

    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO relationship_epochs(
                epoch_id, pet_id, started_at, start_reason
            ) VALUES ('epoch-1', 'pet-1', '2026-07-18T10:00:00+08:00', 'first_use')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO relationship_epochs(
                    epoch_id, pet_id, started_at, start_reason
                ) VALUES ('epoch-2', 'pet-1', '2026-07-18T10:01:00+08:00', 'race')
                """
            )
        connection.rollback()

    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO relationship_epochs(
                epoch_id, pet_id, started_at, start_reason
            ) VALUES ('epoch-1', 'pet-1', '2026-07-18T10:00:00+08:00', 'first_use')
            """
        )
        connection.execute(
            """
            INSERT INTO companion_evidence(
                evidence_id, pet_id, memory_subject_id, ownership_scope,
                relationship_epoch_id, kind, content_json, source_kind,
                source_ref, source_summary, attribution, confidence, occurred_at,
                retention, status, prompt_eligible, created_at
            ) VALUES (
                'user-evidence', 'pet-1', 'subject-1', 'user',
                NULL, 'profile_fact', '{}', 'turn', 'turn-1', 'safe',
                'explicit_user_statement', 1.0, '2026-07-18T10:00:00+08:00',
                'long_term', 'active', 1, '2026-07-18T10:00:00+08:00'
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO companion_evidence(
                    evidence_id, pet_id, memory_subject_id, ownership_scope,
                    relationship_epoch_id, kind, content_json, source_kind,
                    source_ref, source_summary, attribution, confidence, occurred_at,
                    retention, status, prompt_eligible, created_at
                ) VALUES (
                    'cross-pet', 'pet-2', 'subject-2', 'relationship',
                    'epoch-1', 'meaningful_moment', '{}', 'turn', 'turn-2', 'safe',
                    'assistant_observation', 0.8, '2026-07-18T10:00:00+08:00',
                    'long_term', 'active', 1, '2026-07-18T10:00:00+08:00'
                )
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO companion_evidence(
                    evidence_id, pet_id, memory_subject_id, ownership_scope,
                    relationship_epoch_id, kind, content_json, source_kind,
                    source_ref, source_summary, attribution, confidence, occurred_at,
                    retention, status, prompt_eligible, created_at
                ) VALUES (
                    'forgotten-prompt', 'pet-1', 'subject-1', 'user',
                    NULL, 'profile_fact', '{}', 'turn', 'turn-3', 'safe',
                    'explicit_user_statement', 1.0, '2026-07-18T10:00:00+08:00',
                    'long_term', 'forgotten', 1, '2026-07-18T10:00:00+08:00'
                )
                """
            )


def test_correction_supersedes_old_evidence_with_an_immutable_relation(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    store.ensure_subject(
        owner_user_id="owner-1",
        pet_id="pet-1",
        started_at="2026-07-18T10:00:00+08:00",
    )
    store.commit_turn(
        _prepared(),
        _outcome(),
        evidence=(_user_evidence("evidence-old"),),
    )
    replacement = replace(
        _user_evidence("evidence-new"),
        content={"preference": "detailed_answers"},
        source_ref="control-correction-1",
    )

    store.correct_evidence(
        old_evidence_id="evidence-old",
        replacement=replacement,
        relation_id="relation-1",
        created_at="2026-07-18T10:10:00+08:00",
    )

    with store.connection() as connection:
        old = connection.execute(
            """
            SELECT status, prompt_eligible
            FROM companion_evidence
            WHERE evidence_id = 'evidence-old'
            """
        ).fetchone()
        new = connection.execute(
            """
            SELECT status, prompt_eligible
            FROM companion_evidence
            WHERE evidence_id = 'evidence-new'
            """
        ).fetchone()
        relation = connection.execute(
            """
            SELECT relation_kind, source_evidence_id, target_evidence_id
            FROM evidence_relations
            WHERE relation_id = 'relation-1'
            """
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE evidence_relations
                SET relation_kind = 'changed'
                WHERE relation_id = 'relation-1'
                """
            )

    assert tuple(old) == ("superseded", 0)
    assert tuple(new) == ("active", 1)
    assert tuple(relation) == (
        "superseded_by",
        "evidence-old",
        "evidence-new",
    )


def test_v11_upgrade_creates_current_schema_and_preserves_pre_upgrade_backup(
    tmp_path,
):
    database_path = tmp_path / "companion-v11.db"
    CompanionStore(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE companion_context_job_pins")
        connection.execute("DROP TABLE companion_context_messages")
        connection.execute("DROP TABLE semantic_memory_evaluations")
        connection.execute("PRAGMA user_version = 11")
        connection.commit()

    store = CompanionStore(database_path)
    backup_path = database_path.with_name(
        f"{database_path.name}.pre-v{SCHEMA_VERSION}.bak"
    )

    assert backup_path.exists()
    with store.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "companion_context_messages",
        "companion_context_job_pins",
        "semantic_memory_evaluations",
        "companion_growth_moments",
    } <= tables
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 11
        backup_tables = {
            row[0]
            for row in backup.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "companion_context_messages" not in backup_tables


def test_v12_upgrade_adds_growth_moment_read_model_and_keeps_backup(tmp_path):
    database_path = tmp_path / "companion-v12.db"
    store = CompanionStore(database_path)
    with store.connection() as connection:
        connection.execute("DROP TABLE companion_growth_moments")
        connection.execute("PRAGMA user_version = 12")
        connection.commit()

    upgraded = CompanionStore(database_path)
    backup_path = database_path.with_name(
        f"{database_path.name}.pre-v{SCHEMA_VERSION}.bak"
    )

    assert backup_path.exists()
    with upgraded.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = 'companion_growth_moments'
            """
        ).fetchone()[0] == 1
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 12
        assert backup.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = 'companion_growth_moments'
            """
        ).fetchone()[0] == 0


def test_v13_upgrade_backs_up_then_backfills_birth_temperament_once(tmp_path):
    database_path = tmp_path / "companion-v13.db"
    store = CompanionStore(database_path)
    store.ensure_subject(
        owner_user_id="owner-1",
        pet_id="pet-legacy",
        started_at="2026-07-18T10:00:00+08:00",
    )
    store.commit_turn(
        replace(_prepared(), pet_id="pet-legacy"),
        _outcome(),
        evidence=(
            _user_evidence("evidence-before-v14", pet_id="pet-legacy"),
        ),
    )
    with store.connection() as connection:
        pet_before = tuple(
            connection.execute(
                "SELECT pet_id, owner_user_id, created_at FROM companion_pets"
            ).fetchone()
        )
        epoch_before = tuple(
            connection.execute(
                """
                SELECT epoch_id, pet_id, started_at, ended_at,
                       start_reason, end_reason
                FROM relationship_epochs
                WHERE pet_id = 'pet-legacy'
                """
            ).fetchone()
        )
        evidence_before = tuple(
            connection.execute(
                """
                SELECT *
                FROM companion_evidence
                WHERE evidence_id = 'evidence-before-v14'
                """
            ).fetchone()
        )
        connection.execute("DROP TABLE companion_birth_temperaments")
        connection.execute("PRAGMA user_version = 13")
        connection.commit()

    upgraded = CompanionStore(database_path)
    backup_path = database_path.with_name(
        f"{database_path.name}.pre-v{SCHEMA_VERSION}.bak"
    )
    temperament = upgraded.get_birth_temperament(
        owner_user_id="owner-1",
        pet_id="pet-legacy",
    )

    assert backup_path.exists()
    assert temperament is not None
    assert temperament.source_kind == "legacy_backfill"
    assert temperament.generated_at != "2026-07-18T10:00:00+08:00"
    with upgraded.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert tuple(
            connection.execute(
                "SELECT pet_id, owner_user_id, created_at FROM companion_pets"
            ).fetchone()
        ) == pet_before
        assert tuple(
            connection.execute(
                """
                SELECT epoch_id, pet_id, started_at, ended_at,
                       start_reason, end_reason
                FROM relationship_epochs
                WHERE pet_id = 'pet-legacy'
                """
            ).fetchone()
        ) == epoch_before
        assert tuple(
            connection.execute(
                """
                SELECT *
                FROM companion_evidence
                WHERE evidence_id = 'evidence-before-v14'
                """
            ).fetchone()
        ) == evidence_before
        assert connection.execute(
            """
            SELECT COUNT(*) FROM companion_birth_temperaments
            WHERE pet_id = 'pet-legacy' AND source_kind = 'legacy_backfill'
            """
        ).fetchone()[0] == 1
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 13
        assert backup.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = 'companion_birth_temperaments'
            """
        ).fetchone()[0] == 0

    reopened = CompanionStore(database_path)
    assert reopened.get_birth_temperament(
        owner_user_id="owner-1",
        pet_id="pet-legacy",
    ) == temperament
