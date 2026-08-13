from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

from core.conversation_runtime import TurnOutcome
from core.museum.canary import CanaryCase, run_canary
from core.museum.observability import summarize_interaction_traces
from scripts import run_museum_canary


def test_observability_supports_legacy_schema_time_window_and_read_only_access(
    tmp_path,
):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE interaction_trace (
                id TEXT PRIMARY KEY,
                grounding_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO interaction_trace VALUES (?, ?, ?)",
            [
                ("old", "grounded", "2026-08-12T23:59:59+00:00"),
                ("new", "system_error", "2026-08-13T00:00:01+00:00"),
            ],
        )
    before = database.read_bytes()

    result = summarize_interaction_traces(
        database,
        since=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    assert result["request_count"] == 1
    assert result["status_counts"] == {"system_error": 1}
    assert result["failure_count"] == 1
    assert result["latency"] == {}
    assert result["schema"]["missing_observability_columns"] == [
        "duration_ms",
        "guard_result",
        "retrieval_trace_json",
        "stage_latency_json",
    ]
    assert database.read_bytes() == before


def test_canary_reports_success_and_behavioral_failure():
    case = CanaryCase(
        id="case",
        question="展品是什么材质？",
        knowledge_status="grounded",
        exhibit_id="exhibit-1",
        fact_ids=("fact-1",),
    )

    class Runtime:
        def __init__(self, *, fact_ids=("fact-1",), duration_ms=10):
            self.fact_ids = fact_ids
            self.duration_ms = duration_ms

        def handle_turn(self, request):
            return TurnOutcome(
                handled=True,
                spoken_text="公开事实回答",
                knowledge_status="grounded",
                fact_ids=self.fact_ids,
                source_ids=("source-1",),
                display_state={"context": {"exhibit_id": "exhibit-1"}},
                audit_record={
                    "request_id": request.request_id,
                    "guard_result": "published_facts_only",
                    "duration_ms": self.duration_ms,
                    "stage_latency": {"total_ms": self.duration_ms},
                },
            )

        def get_interaction_trace_by_request_id(self, _request_id):
            return {"id": "trace-1"}

    passed = run_canary(Runtime(), llm=None, cases=[case], run_id="pass")
    failed = run_canary(
        Runtime(fact_ids=("wrong",), duration_ms=3001),
        llm=None,
        cases=[case],
        run_id="fail",
    )

    assert passed["passed"] is True
    assert passed["failed_case_count"] == 0
    assert failed["passed"] is False
    assert failed["failed_case_count"] == 1
    assert len(failed["cases"][0]["failures"]) == 2


def test_canary_cli_does_not_print_exception_secrets(monkeypatch, capsys):
    secret = "sk-stage2-secret-value"
    monkeypatch.setattr(
        run_museum_canary,
        "load_config",
        lambda: (_ for _ in ()).throw(
            RuntimeError(f"https://user:{secret}@example.test/api?token={secret}")
        ),
    )

    exit_code = run_museum_canary.main([])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["error"] == "RuntimeError"
    assert secret not in output
    assert "https://" not in output
