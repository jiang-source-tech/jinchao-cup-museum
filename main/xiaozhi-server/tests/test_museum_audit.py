from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from core.business_runtime_factory import create_conversation_runtime
from core.conversation_runtime import TurnRequest
from core.museum.store import MuseumStore


def _request(*, text: str, request_id: str, device_id: str = "audit-device"):
    return TurnRequest(
        request_id=request_id,
        transport_session_id=f"transport-{request_id}",
        visitor_session_id=None,
        device_id=device_id,
        user_text=text,
        history=(),
        occurred_at=datetime.now().astimezone(),
        llm=None,
    )


def _runtime(tmp_path):
    return create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(tmp_path / "museum.db"),
                "exhibit_context_mode": "explicit",
            }
        }
    )


def test_trace_persists_resolution_evidence_and_is_queryable_by_request_id(
    tmp_path,
):
    runtime = _runtime(tmp_path)
    store = MuseumStore(tmp_path / "museum.db")

    explicit = runtime.handle_turn(
        _request(
            text="战国水晶杯是什么材质？",
            request_id="audit-explicit",
        )
    )
    explicit_trace = store.get_interaction_trace_by_request_id("audit-explicit")

    assert explicit_trace["id"] == explicit.audit_id
    assert explicit_trace["resolution_status"] == "explicit"
    assert explicit_trace["context_source"] == "explicit_mention"
    assert explicit_trace["matched_exhibit_text"] == "战国水晶杯"
    assert json.loads(explicit_trace["candidate_exhibit_ids_json"]) == [
        "warring-states-crystal-cup"
    ]

    inherited = runtime.handle_turn(
        _request(
            text="它是怎么做出来的？",
            request_id="audit-inherited",
        )
    )
    inherited_trace = store.get_interaction_trace_by_request_id("audit-inherited")

    assert inherited.knowledge_status == "grounded"
    assert inherited_trace["resolution_status"] == "inherited"
    assert inherited_trace["context_source"] == "inherited_session"
    assert inherited_trace["matched_exhibit_text"] is None
    assert json.loads(inherited_trace["candidate_exhibit_ids_json"]) == [
        "warring-states-crystal-cup"
    ]

    missing = runtime.handle_turn(
        _request(
            text="它是什么材质？",
            request_id="audit-missing",
            device_id="fresh-audit-device",
        )
    )
    missing_trace = store.get_interaction_trace_by_request_id("audit-missing")

    assert missing.knowledge_status == "missing_context"
    assert missing_trace["resolution_status"] == "missing"
    assert missing_trace["context_source"] == "missing"
    assert missing_trace["matched_exhibit_text"] is None
    assert json.loads(missing_trace["candidate_exhibit_ids_json"]) == []


def test_trace_keeps_resolution_evidence_for_unsupported_and_social_turns(
    tmp_path,
):
    runtime = _runtime(tmp_path)
    store = MuseumStore(tmp_path / "museum.db")
    runtime.handle_turn(
        _request(
            text="战国水晶杯是什么材质？",
            request_id="audit-context-setup",
        )
    )

    unsupported = runtime.handle_turn(
        _request(text="它值多少钱？", request_id="audit-unsupported")
    )
    unsupported_trace = store.get_interaction_trace_by_request_id(
        "audit-unsupported"
    )

    assert unsupported.knowledge_status == "unsupported"
    assert unsupported_trace["resolution_status"] == "inherited"
    assert unsupported_trace["context_source"] == "inherited_session"
    assert unsupported_trace["grounding_status"] == "unsupported"
    assert json.loads(unsupported_trace["evidence_json"])["fact_ids"] == []

    conversational = runtime.handle_turn(
        _request(
            text="你好，你是谁？",
            request_id="audit-conversational",
            device_id="fresh-social-device",
        )
    )
    conversational_trace = store.get_interaction_trace_by_request_id(
        "audit-conversational"
    )

    assert conversational.knowledge_status == "conversational"
    assert conversational_trace["resolution_status"] == "missing"
    assert conversational_trace["context_source"] == "missing"
    assert conversational_trace["grounding_status"] == "conversational"
    assert json.loads(conversational_trace["candidate_exhibit_ids_json"]) == []


def test_existing_trace_schema_is_migrated_with_resolution_columns(tmp_path):
    database_path = tmp_path / "legacy-museum.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE interaction_trace (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                visitor_session_id TEXT,
                device_id TEXT,
                exhibit_id TEXT,
                user_text TEXT NOT NULL,
                grounding_status TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                answer_text TEXT NOT NULL,
                unanswered_reason TEXT,
                coarse_intent TEXT NOT NULL DEFAULT '',
                fine_intent TEXT NOT NULL DEFAULT '',
                intent_confidence REAL NOT NULL DEFAULT 0,
                guard_result TEXT NOT NULL,
                stage_latency_json TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    MuseumStore(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(interaction_trace)"
            )
        }
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(interaction_trace)")
        }

    assert {
        "resolution_status",
        "context_source",
        "matched_exhibit_text",
        "candidate_exhibit_ids_json",
    } <= columns
    assert "interaction_trace_by_request" in indexes
