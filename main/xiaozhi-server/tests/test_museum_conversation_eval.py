from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from core.museum.evaluation import (
    load_evaluation_fixture,
    prepare_evaluation_runtime,
    render_evaluation_report,
    run_evaluation,
)


SERVER_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "museum_conversation_eval.json"


def test_hangzhou_conversation_fixture_passes_rules_baseline(tmp_path):
    fixture = load_evaluation_fixture(FIXTURE)
    runtime = prepare_evaluation_runtime(
        database_path=tmp_path / "museum-evaluation.db",
        server_root=SERVER_ROOT,
        fixture=fixture,
    )

    result = run_evaluation(
        fixture=fixture,
        runtime=runtime,
        mode="rules",
        llm=None,
        run_id="pytest-rules",
    )

    assert fixture["version"] == 2
    assert result["summary"]["case_count"] >= 30
    assert result["summary"]["turn_count"] >= 35
    assert result["summary"]["failed_turn_count"] == 0
    assert result["overall_pass"] is True

    metrics = {metric["id"]: metric for metric in result["metrics"]}
    assert metrics["canonical_name_accuracy"]["value"] == 1.0
    assert metrics["reviewed_alias_accuracy"]["value"] >= 0.95
    assert metrics["asr_alias_accuracy"]["value"] >= 0.95
    assert metrics["ambiguous_wrong_binding_rate"]["value"] == 0.0
    assert metrics["unlisted_silent_inheritance_rate"]["value"] == 0.0
    assert metrics["grounded_boundary_violation_rate"]["value"] == 0.0
    assert metrics["unsupported_hallucination_rate"]["value"] == 0.0
    assert metrics["evidence_audit_reproducibility"]["value"] == 1.0
    assert all(not turn["actual"]["llm_invoked"] for turn in result["turns"])
    assert all(
        turn["actual"]["llm_result"] == "not_called"
        for turn in result["turns"]
    )
    assert all(
        not turn["actual"]["guard_result"].startswith("model_")
        for turn in result["turns"]
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert "api_key" not in serialized
    assert "sk-" not in serialized

    report = render_evaluation_report(fixture=fixture, runs=[result])
    assert "规则基线与真实 LLM 使用同一批问题" in report
    assert "真机验收继续由 REQ-015 独立完成" in report
    assert "人工流畅度评审：未执行" not in report


def test_evaluation_cli_runs_from_outside_server_root(tmp_path):
    report_path = tmp_path / "report.md"
    json_path = tmp_path / "result.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SERVER_ROOT / "scripts" / "evaluate_museum_rag.py"),
            "--mode",
            "rules",
            "--run-id",
            "pytest-cli",
            "--database",
            str(tmp_path / "evaluation.db"),
            "--json-output",
            str(json_path),
            "--report",
            str(report_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["runs"][0]["overall_pass"] is True
    assert payload["runs"][0]["summary"]["turn_count"] == 45
    assert "P0 结论" in report_path.read_text(encoding="utf-8")
