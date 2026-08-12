from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

import pytest

from core.business_runtime_factory import create_conversation_runtime
from core.museum.store import DEMO_EXHIBIT_ID, MuseumStore
from scripts.museum_text_chat import (
    MuseumTextChatSession,
    initialize_chat_llm,
    outcome_payload,
)


def _session(tmp_path) -> MuseumTextChatSession:
    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(tmp_path / "museum.db"),
                "seed_demo_content": True,
                "exhibit_context_mode": "explicit",
            }
        }
    )
    return MuseumTextChatSession(runtime=runtime, llm=None, device_prefix="test-chat")


def test_text_chat_keeps_session_context_and_history(tmp_path):
    session = _session(tmp_path)

    first = session.ask("战国水晶杯是什么材质？")
    follow_up = session.ask("这个杯子是怎么做出来的？")
    payload = outcome_payload(follow_up)

    assert first.knowledge_status == "grounded"
    assert session.visitor_session_id
    assert follow_up.knowledge_status == "grounded"
    assert payload["fine_intent"] == "craft"
    assert payload["fact_ids"] == ["fact-crystal-cup-craft-limit"]
    assert len(session.history) == 4


def test_session_idle_expiry_renews_only_until_maximum_lifetime(tmp_path):
    store = MuseumStore(
        tmp_path / "museum.db",
        session_idle_ttl_minutes=5,
        session_max_ttl_minutes=30,
    )
    store.seed_demo_content()
    started_at = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)

    created, _ = store.resolve_or_create_session(
        device_id="ttl-device",
        occurred_at=started_at,
        explicit_exhibit_id=DEMO_EXHIBIT_ID,
        allow_device_placement=False,
    )
    current = created
    for elapsed_minutes in range(4, 29, 4):
        current, _ = store.resolve_or_create_session(
            device_id="ttl-device",
            occurred_at=started_at + timedelta(minutes=elapsed_minutes),
            requested_session_id=created.id,
            allow_device_placement=False,
        )

    assert current.id == created.id
    assert current.expires_at == started_at + timedelta(minutes=30)

    replacement, _ = store.resolve_or_create_session(
        device_id="ttl-device",
        occurred_at=started_at + timedelta(minutes=30),
        explicit_exhibit_id=DEMO_EXHIBIT_ID,
        allow_device_placement=False,
    )
    assert replacement.id != created.id


def test_reset_clears_exhibit_context_by_starting_a_new_device_session(tmp_path):
    session = _session(tmp_path)
    session.ask("战国水晶杯是什么材质？")
    previous_device_id = session.device_id
    previous_session_id = session.visitor_session_id

    session.reset()
    store = MuseumStore(tmp_path / "museum.db")
    with store.connection() as connection:
        ended_at = connection.execute(
            "SELECT ended_at FROM visitor_session WHERE id = ? AND device_id = ?",
            (previous_session_id, previous_device_id),
        ).fetchone()["ended_at"]
    assert ended_at is not None
    assert session.runtime.end_session(
        previous_session_id,
        previous_device_id,
        datetime.now().astimezone(),
    ) is True
    outcome = session.ask("它是怎么做出来的？")

    assert session.device_id != previous_device_id
    assert outcome.knowledge_status == "missing_context"
    assert outcome.display_state["context"]["exhibit_id"] == ""


def test_separate_text_chat_instances_do_not_share_exhibit_context(tmp_path):
    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(tmp_path / "museum.db"),
                "seed_demo_content": True,
                "exhibit_context_mode": "explicit",
            }
        }
    )
    first = MuseumTextChatSession(
        runtime=runtime,
        llm=None,
        device_prefix="same-console-prefix",
    )
    first.ask("战国水晶杯是什么材质？")

    second = MuseumTextChatSession(
        runtime=runtime,
        llm=None,
        device_prefix="same-console-prefix",
    )
    outcome = second.ask("它是什么材质？")

    assert second.device_id != first.device_id
    assert outcome.knowledge_status == "missing_context"
    assert outcome.display_state["context"]["exhibit_id"] == ""


def test_text_chat_queries_complete_audit_by_request_id(tmp_path):
    session = _session(tmp_path)
    outcome = session.ask("战国水晶杯是什么材质？")
    request_id = outcome.audit_record["request_id"]

    trace = session.audit(request_id)

    assert trace["id"] == outcome.audit_id
    assert trace["request_id"] == request_id
    assert trace["resolution_status"] == "explicit"
    assert trace["context_source"] == "explicit_mention"
    assert session.audit("missing-request-id") is None


def test_llm_mode_is_explicit_and_can_be_required():
    config = {"selected_module": {}, "LLM": {}}

    llm, mode = initialize_chat_llm(config)
    assert llm is None
    assert mode == "deterministic-only"

    with pytest.raises(RuntimeError, match="没有 selected_module.LLM"):
        initialize_chat_llm(config, required=True)
    with pytest.raises(RuntimeError, match="不能同时"):
        initialize_chat_llm(config, disabled=True, required=True)


def test_invalid_selected_llm_configuration_never_silently_falls_back():
    config = {"selected_module": {"LLM": "missing"}, "LLM": {}}

    with pytest.raises(RuntimeError, match="没有对应配置"):
        initialize_chat_llm(config)


def test_text_chat_script_starts_directly_outside_server_root(tmp_path):
    server_root = Path(__file__).resolve().parents[1]
    script = server_root / "scripts" / "museum_text_chat.py"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"museum_text_chat.py" in result.stdout
