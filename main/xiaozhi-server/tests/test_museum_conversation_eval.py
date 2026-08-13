from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

from qdrant_client import QdrantClient

from core.museum.evaluation import (
    load_evaluation_fixture,
    prepare_evaluation_runtime,
    render_evaluation_report,
    run_evaluation,
)
from core.museum.content_import import load_content_package
from core.museum.knowledge_release import prepare_index_records
from core.museum.qdrant_index import QdrantFactIndex
from core.museum.retrieval import HybridEvidenceRetriever


SERVER_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "museum_conversation_eval.json"


class ConstantEvaluationEmbedder:
    model = "local-evaluation-constant"
    dimension = 4

    def embed(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]


def _hybrid_retriever(store):
    embedder = ConstantEvaluationEmbedder()
    index = QdrantFactIndex(
        url="http://unused.local",
        collection_name="museum_eval_facts",
        dimension=embedder.dimension,
        client=QdrantClient(location=":memory:"),
    )
    records = prepare_index_records(
        store.published_fact_index_records(),
        embedding_model=embedder.model,
        embedding_dimension=embedder.dimension,
    )
    index.rebuild(records, [embedder.embed("") for _ in records])
    return HybridEvidenceRetriever(
        store=store,
        embedder=embedder,
        index=index,
        mode="hybrid",
        dense_score_threshold=0.0,
    )


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
    assert result["summary"]["case_count"] == 197
    assert result["summary"]["turn_count"] == 224
    assert result["summary"]["failed_turn_count"] == 0
    assert result["summary"]["coverage"]["expected_exhibit_count"] == 17
    assert result["summary"]["coverage"]["passed_exhibit_count"] == 17
    assert result["summary"]["coverage"]["passed"] is True
    assert result["overall_pass"] is True

    metrics = {metric["id"]: metric for metric in result["metrics"]}
    assert metrics["canonical_name_accuracy"]["value"] == 1.0
    assert metrics["reviewed_alias_accuracy"]["value"] >= 0.95
    assert metrics["asr_alias_accuracy"]["value"] >= 0.95
    assert metrics["ambiguous_wrong_binding_rate"]["value"] == 0.0
    assert metrics["unlisted_silent_inheritance_rate"]["value"] == 0.0
    assert metrics["grounded_boundary_violation_rate"]["value"] == 0.0
    assert metrics["unsupported_hallucination_rate"]["value"] == 0.0
    assert metrics["correct_unsupported_rate"]["value"] == 1.0
    assert metrics["conversation_context_accuracy"]["value"] == 1.0
    assert metrics["retrieval_recall_at_3"]["value"] >= 0.95
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
    assert re.search(r"\bsk-[A-Za-z0-9_-]{16,}\b", serialized) is None

    report = render_evaluation_report(fixture=fixture, runs=[result])
    assert "规则基线与真实 LLM 使用同一批问题" in report
    assert "真机验收继续由 REQ-015 独立完成" in report
    assert "人工流畅度评审：未执行" not in report


def test_fixture_covers_every_official_exhibit_with_grounded_and_unsupported_cases():
    fixture = load_evaluation_fixture(FIXTURE)
    exhibit_fact_sources: dict[str, dict[str, tuple[str, ...]]] = {}
    for relative_path in fixture["content_packages"]:
        package = load_content_package(SERVER_ROOT / relative_path)
        for exhibit in package.exhibits:
            exhibit_fact_sources[exhibit.id] = {
                fact.id: fact.source_ids for fact in exhibit.revision.facts
            }

    grounded_exhibits: set[str] = set()
    unsupported_exhibits: set[str] = set()
    for case in fixture["cases"]:
        for turn in case["turns"]:
            expected = turn.get("expected", {})
            exhibit_id = expected.get("context_exhibit_id", "")
            if not exhibit_id:
                continue
            if expected.get("knowledge_status") == "grounded":
                grounded_exhibits.add(exhibit_id)
                for fact_id in expected.get("fact_ids", []):
                    assert fact_id in exhibit_fact_sources[exhibit_id]
                    expected_sources = tuple(expected.get("source_ids", []))
                    if expected_sources:
                        assert expected_sources == exhibit_fact_sources[exhibit_id][fact_id]
            elif expected.get("knowledge_status") == "unsupported":
                unsupported_exhibits.add(exhibit_id)
                assert expected.get("fact_ids") == []

    official_exhibits = set(exhibit_fact_sources)
    assert len(official_exhibits) == 17
    assert grounded_exhibits == official_exhibits
    assert unsupported_exhibits == official_exhibits


def test_full_fixture_passes_with_the_production_hybrid_retrieval_path(tmp_path):
    fixture = load_evaluation_fixture(FIXTURE)
    runtime = prepare_evaluation_runtime(
        database_path=tmp_path / "museum-hybrid-evaluation.db",
        server_root=SERVER_ROOT,
        fixture=fixture,
        retriever_factory=_hybrid_retriever,
    )

    result = run_evaluation(
        fixture=fixture,
        runtime=runtime,
        mode="rules",
        llm=None,
        run_id="pytest-hybrid",
    )

    assert result["overall_pass"] is True
    grounded_turns = [
        turn
        for turn in result["turns"]
        if turn["actual"]["knowledge_status"] == "grounded"
    ]
    assert grounded_turns
    for turn in grounded_turns:
        trace = runtime.get_interaction_trace_by_request_id(
            turn["actual"]["request_id"]
        )
        retrieval = json.loads(trace["retrieval_trace_json"])
        assert retrieval["mode"] == "hybrid"
        assert retrieval["fallback_reason"] == ""
        assert retrieval["dense_candidates"]


def test_evaluation_cli_runs_from_outside_server_root(tmp_path):
    report_path = tmp_path / "report.md"
    json_path = tmp_path / "result.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SERVER_ROOT / "scripts" / "evaluate_museum_rag.py"),
            "--mode",
            "rules",
            "--retrieval-mode",
            "hybrid",
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
    assert payload["retrieval_mode"] == "hybrid"
    assert payload["runs"][0]["overall_pass"] is True
    assert payload["runs"][0]["summary"]["turn_count"] == 224
    assert "P0 结论" in report_path.read_text(encoding="utf-8")
