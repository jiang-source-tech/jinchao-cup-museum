from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from core.museum.contracts import EvidenceFact, EvidenceSnapshot
from core.museum.store import MuseumStore


SERVER_ROOT = Path(__file__).resolve().parents[1]


def _record_trace(
    store: MuseumStore,
    *,
    request_id: str,
    question: str,
    occurred_at: datetime,
    exhibit_id: str | None = "warring-states-crystal-cup",
    resolution_status: str = "inherited",
    grounding_status: str = "unsupported",
    unanswered_reason: str | None = "no_published_fact_match",
    coarse_intent: str = "exhibit_knowledge",
    fine_intent: str = "price",
    guard_result: str = "unsupported_fallback",
    matched_exhibit_text: str | None = None,
    candidate_exhibit_ids: tuple[str, ...] = (),
    evidence: EvidenceSnapshot | None = None,
) -> None:
    store.record_interaction(
        request_id=request_id,
        visitor_session_id=None,
        device_id="unanswered-device",
        exhibit_id=exhibit_id,
        user_text=question,
        grounding_status=grounding_status,
        evidence=evidence,
        answer_text="当前资料还不能回答这个问题。",
        unanswered_reason=unanswered_reason,
        coarse_intent=coarse_intent,
        fine_intent=fine_intent,
        intent_confidence=0.9,
        guard_result=guard_result,
        stage_latency={"total_ms": 1},
        duration_ms=1,
        occurred_at=occurred_at,
        resolution_status=resolution_status,
        context_source="inherited_session",
        matched_exhibit_text=matched_exhibit_text,
        candidate_exhibit_ids=candidate_exhibit_ids,
    )


def test_store_aggregates_repeated_unanswered_questions_and_excludes_social_turns(
    tmp_path,
):
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    _record_trace(
        store,
        request_id="fact-gap-old",
        question="它值多少钱？",
        occurred_at=datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
    )
    _record_trace(
        store,
        request_id="fact-gap-latest",
        question=" 它值多少钱 ",
        occurred_at=datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc),
    )
    _record_trace(
        store,
        request_id="fact-gap-latest",
        question="它值多少钱？？",
        occurred_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
    )
    _record_trace(
        store,
        request_id="social-turn",
        question="你好",
        occurred_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
        exhibit_id=None,
        resolution_status="missing",
        grounding_status="conversational",
        unanswered_reason=None,
        coarse_intent="social",
        fine_intent="social",
        guard_result="conversational_scope",
    )

    issues = store.list_unanswered_issues()

    assert len(issues) == 1
    issue = issues[0]
    assert issue.request_id == "fact-gap-latest"
    assert issue.original_question == "它值多少钱？？"
    assert issue.resolution_status == "inherited"
    assert issue.exhibit_id == "warring-states-crystal-cup"
    assert issue.unanswered_reason == "fact_not_covered"
    assert issue.recorded_unanswered_reason == "no_published_fact_match"
    assert issue.coarse_intent == "exhibit_knowledge"
    assert issue.fine_intent == "price"
    assert issue.occurrence_count == 2
    assert issue.last_occurred_at == "2026-08-11T10:00:00+00:00"
    assert issue.fact_candidate_ids == ()
    assert issue.guard_result == "unsupported_fallback"


def test_store_classifies_six_actionable_unanswered_reasons(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    base_time = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)

    _record_trace(
        store,
        request_id="not-found",
        question="良渚玉琮是什么材质？",
        occurred_at=base_time,
        exhibit_id=None,
        resolution_status="not_found",
        unanswered_reason="exhibit_reference_missing",
        fine_intent="material",
        matched_exhibit_text="良渚玉琮",
    )
    _record_trace(
        store,
        request_id="ambiguous",
        question="玉杯是什么材质？",
        occurred_at=base_time,
        exhibit_id=None,
        resolution_status="ambiguous",
        unanswered_reason="exhibit_reference_missing",
        fine_intent="material",
        candidate_exhibit_ids=("jade-cup-a", "jade-cup-b"),
    )
    _record_trace(
        store,
        request_id="fact-gap",
        question="战国水晶杯值多少钱？",
        occurred_at=base_time,
    )
    _record_trace(
        store,
        request_id="out-of-scope",
        question="战国水晶杯和别的杯子哪个更贵？",
        occurred_at=base_time,
        coarse_intent="comparison",
        fine_intent="comparison",
    )
    _record_trace(
        store,
        request_id="asr-suspected",
        question="战国水金杯是什么材质？",
        occurred_at=base_time,
        exhibit_id=None,
        resolution_status="not_found",
        unanswered_reason="exhibit_reference_missing",
        fine_intent="material",
        matched_exhibit_text="战国水金杯",
    )
    _record_trace(
        store,
        request_id="short-not-found",
        question="水杯是什么材质？",
        occurred_at=base_time,
        exhibit_id=None,
        resolution_status="not_found",
        unanswered_reason="exhibit_reference_missing",
        fine_intent="material",
        matched_exhibit_text="水杯",
    )
    _record_trace(
        store,
        request_id="retrieval-failure",
        question="战国水晶杯是什么材质？",
        occurred_at=base_time,
        grounding_status="temporary_failure",
        unanswered_reason="retrieval_timeout",
        fine_intent="material",
        guard_result="retrieval_not_evaluated",
    )
    _record_trace(
        store,
        request_id="missing-reference",
        question="它是什么材质？",
        occurred_at=base_time,
        exhibit_id=None,
        resolution_status="missing",
        grounding_status="missing_context",
        unanswered_reason="exhibit_reference_missing",
        fine_intent="material",
        guard_result="missing_context",
    )

    issues = store.list_unanswered_issues()

    assert {issue.unanswered_reason for issue in issues} == {
        "exhibit_not_found",
        "exhibit_ambiguous",
        "fact_not_covered",
        "out_of_scope",
        "asr_suspected",
        "retrieval_failure",
    }
    assert {issue.request_id for issue in issues} == {
        "not-found",
        "ambiguous",
        "fact-gap",
        "out-of-scope",
        "asr-suspected",
        "short-not-found",
        "retrieval-failure",
    }
    reason_by_request = {
        issue.request_id: issue.unanswered_reason for issue in issues
    }
    assert reason_by_request["short-not-found"] == "exhibit_not_found"


def test_representative_request_id_returns_complete_structured_audit(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    evidence = EvidenceSnapshot(
        exhibit_id="warring-states-crystal-cup",
        content_revision_id="warring-states-crystal-cup-r1",
        content_version=1,
        facts=(
            EvidenceFact(
                id="fact-crystal-cup-material",
                fact_type="material",
                statement="它由一整块天然水晶琢制而成。",
                source_ids=("source-hangzhou-portal-2020",),
            ),
        ),
    )
    _record_trace(
        store,
        request_id="guarded-request",
        question="战国水晶杯是塑料做的吗？",
        occurred_at=datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc),
        grounding_status="temporary_failure",
        unanswered_reason="retrieval_failure",
        fine_intent="material",
        guard_result="model_answer_unsupported_claim",
        candidate_exhibit_ids=("warring-states-crystal-cup",),
        evidence=evidence,
    )

    issue = store.list_unanswered_issues()[0]
    audit = store.get_interaction_audit_by_request_id(issue.request_id)

    assert issue.fact_candidate_ids == ("fact-crystal-cup-material",)
    assert audit is not None
    assert audit["request_id"] == "guarded-request"
    assert audit["user_text"] == "战国水晶杯是塑料做的吗？"
    assert audit["candidate_exhibit_ids"] == ["warring-states-crystal-cup"]
    assert audit["evidence"] == {
        "content_revision_id": "warring-states-crystal-cup-r1",
        "content_version": 1,
        "fact_ids": ["fact-crystal-cup-material"],
        "source_ids": ["source-hangzhou-portal-2020"],
    }
    assert audit["guard_result"] == "model_answer_unsupported_claim"
    assert audit["stage_latency"] == {"total_ms": 1}
    assert audit["duration_ms"] == 1
    assert audit["created_at"] == "2026-08-11T11:00:00+00:00"
    assert store.get_interaction_audit_by_request_id("does-not-exist") is None


def test_cli_exports_json_csv_and_audits_representative_request(tmp_path):
    database = tmp_path / "museum.db"
    store = MuseumStore(database)
    store.seed_demo_content()
    _record_trace(
        store,
        request_id="cli-fact-old",
        question="它值多少钱？",
        occurred_at=datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
    )
    _record_trace(
        store,
        request_id="cli-fact-latest",
        question="它值多少钱",
        occurred_at=datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc),
    )
    _record_trace(
        store,
        request_id="cli-not-found",
        question="良渚玉琮是什么材质？",
        occurred_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
        exhibit_id=None,
        resolution_status="not_found",
        unanswered_reason="exhibit_reference_missing",
        fine_intent="material",
        matched_exhibit_text="良渚玉琮",
    )
    json_output = tmp_path / "unanswered.json"
    csv_output = tmp_path / "unanswered.csv"
    audit_output = tmp_path / "audit.json"

    exported_json = _run_cli(
        "export",
        "--database",
        str(database),
        "--output",
        str(json_output),
        "--format",
        "json",
    )
    exported_csv = _run_cli(
        "export",
        "--database",
        str(database),
        "--output",
        str(csv_output),
        "--format",
        "csv",
    )
    audited = _run_cli(
        "audit",
        "--database",
        str(database),
        "--request-id",
        "cli-fact-latest",
        "--output",
        str(audit_output),
    )

    assert exported_json.returncode == 0, exported_json.stderr
    assert exported_csv.returncode == 0, exported_csv.stderr
    assert audited.returncode == 0, audited.stderr
    json_payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert json_payload["schema_version"] == 1
    assert json_payload["issue_count"] == 2
    assert json_payload["issues"][0]["request_id"] == "cli-fact-latest"
    assert json_payload["issues"][0]["occurrence_count"] == 2
    assert json_payload["issues"][0]["unanswered_reason"] == "fact_not_covered"
    with csv_output.open(encoding="utf-8-sig", newline="") as csv_file:
        csv_rows = list(csv.DictReader(csv_file))
    assert [row["request_id"] for row in csv_rows] == [
        "cli-fact-latest",
        "cli-not-found",
    ]
    assert csv_rows[0]["occurrence_count"] == "2"
    audit_payload = json.loads(audit_output.read_text(encoding="utf-8"))
    assert audit_payload["audit"]["request_id"] == "cli-fact-latest"
    assert audit_payload["audit"]["evidence"]["fact_ids"] == []
    assert "interaction_trace" in audit_payload["audit"]["record_type"]


def test_cli_rejects_missing_database_without_creating_it(tmp_path):
    database = tmp_path / "missing.db"

    completed = _run_cli(
        "export",
        "--database",
        str(database),
        "--output",
        str(tmp_path / "unanswered.json"),
        "--format",
        "json",
    )

    assert completed.returncode == 2
    assert not database.exists()
    assert "database_not_found" in completed.stderr


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/export_museum_unanswered.py",
            *arguments,
        ],
        cwd=SERVER_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
