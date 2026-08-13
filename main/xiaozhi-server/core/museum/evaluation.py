from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from core.conversation_runtime import TurnRequest
from core.museum.content_import import (
    import_draft_content,
    load_content_package,
    publish_revision,
    review_revision,
)
from core.museum.llm_contract import MUSEUM_LLM_PROMPT_VERSION
from core.museum.runtime import MuseumRuntime
from core.museum.store import MuseumStore


_METRIC_DEFINITIONS = (
    (
        "canonical_name_accuracy",
        "规范名称解析准确率",
        "canonical_resolution",
        "minimum",
    ),
    (
        "reviewed_alias_accuracy",
        "审核别名解析准确率",
        "alias_resolution",
        "minimum",
    ),
    (
        "asr_alias_accuracy",
        "ASR 常见误识别别名准确率",
        "asr_alias_resolution",
        "minimum",
    ),
    (
        "ambiguous_wrong_binding_rate",
        "歧义错误绑定率",
        "ambiguous_no_binding",
        "maximum_error",
    ),
    (
        "unlisted_silent_inheritance_rate",
        "未收录展品静默继承率",
        "unlisted_no_inherit",
        "maximum_error",
    ),
    (
        "grounded_boundary_violation_rate",
        "有依据回答越界率",
        "grounded_boundary",
        "maximum_error",
    ),
    (
        "unsupported_hallucination_rate",
        "资料不足编造率",
        "unsupported_no_hallucination",
        "maximum_error",
    ),
    (
        "correct_unsupported_rate",
        "资料不足正确拒答率",
        "unsupported_correct_rejection",
        "minimum",
    ),
    (
        "conversation_context_accuracy",
        "连续会话上下文准确率",
        "conversation_context",
        "minimum",
    ),
    (
        "retrieval_recall_at_3",
        "真实内容 Retrieval Recall@3",
        "retrieval_recall_at_3",
        "minimum",
    ),
    (
        "evidence_audit_reproducibility",
        "依据快照可复核率",
        "audit_reproducibility",
        "minimum",
    ),
)
_KNOWN_CHECKS = {definition[2] for definition in _METRIC_DEFINITIONS}


def load_evaluation_fixture(path: str | Path) -> dict[str, Any]:
    fixture_path = Path(path)
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取评测集 {fixture_path}: {exc}") from exc
    coverage_profile = fixture.get("coverage_profile")
    if coverage_profile:
        fixture_dir = fixture_path.parent.resolve()
        coverage_path = (fixture_dir / str(coverage_profile)).resolve()
        if not coverage_path.is_relative_to(fixture_dir):
            raise ValueError(f"覆盖档案越出评测集目录: {coverage_profile}")
        try:
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取覆盖档案 {coverage_path}: {exc}") from exc
        _validate_coverage_profile(coverage)
        fixture["coverage"] = coverage
        fixture["cases"] = [
            *fixture.get("cases", []),
            *_coverage_cases(coverage),
        ]
    _validate_fixture(fixture)
    return fixture


def prepare_evaluation_runtime(
    *,
    database_path: str | Path,
    server_root: str | Path,
    fixture: Mapping[str, Any],
    retriever_factory: Callable[[MuseumStore], Any] | None = None,
) -> MuseumRuntime:
    root = Path(server_root).resolve()
    store = MuseumStore(database_path)
    occurred_at = datetime.now().astimezone()
    for relative_path in fixture["content_packages"]:
        package_path = (root / str(relative_path)).resolve()
        if not package_path.is_relative_to(root):
            raise ValueError(f"内容包越出服务端目录: {relative_path}")
        package = load_content_package(package_path)
        import_draft_content(store, package)
        for exhibit in package.exhibits:
            review_revision(
                store,
                revision_id=exhibit.revision.id,
                reviewed_by="rag-evaluator",
                reviewed_at=occurred_at,
            )
            publish_revision(
                store,
                revision_id=exhibit.revision.id,
                published_by="rag-evaluator",
                published_at=occurred_at,
            )
    retriever = retriever_factory(store) if retriever_factory else None
    return MuseumRuntime(
        store,
        retriever=retriever,
        exhibit_context_mode="explicit",
    )


def run_evaluation(
    *,
    fixture: Mapping[str, Any],
    runtime: MuseumRuntime,
    mode: str,
    llm: Any | None,
    run_id: str,
) -> dict[str, Any]:
    if mode not in {"rules", "llm"}:
        raise ValueError("评测 mode 必须是 rules 或 llm")
    if mode == "llm" and llm is None:
        raise ValueError("llm 模式必须提供真实或可控的 LLM 实例")

    turn_results: list[dict[str, Any]] = []
    for case in fixture["cases"]:
        case_id = str(case["id"])
        device_id = f"rag-eval-{_slug(run_id)}-{mode}-{_slug(case_id)}"
        visitor_session_id: str | None = None
        history: list[dict[str, str]] = []
        for turn_index, turn in enumerate(case["turns"], start=1):
            request_id = (
                f"rag-eval-{_slug(run_id)}-{mode}-{_slug(case_id)}-{turn_index}"
            )
            outcome = runtime.handle_turn(
                TurnRequest(
                    request_id=request_id,
                    transport_session_id=f"transport-{device_id}",
                    visitor_session_id=visitor_session_id,
                    device_id=device_id,
                    user_text=str(turn["text"]),
                    history=tuple(history[-8:]),
                    occurred_at=datetime.now().astimezone(),
                    llm=llm if mode == "llm" else None,
                    metadata={
                        "client": "museum_rag_evaluator",
                        "evaluation_mode": mode,
                    },
                )
            )
            persisted = runtime.get_interaction_trace_by_request_id(request_id)
            trace = dict(persisted) if persisted is not None else None
            expected = _expected_for_mode(turn, mode)
            result = _evaluate_turn(
                case=case,
                turn=turn,
                turn_index=turn_index,
                expected=expected,
                outcome=outcome,
                trace=trace,
                mode=mode,
            )
            turn_results.append(result)
            visitor_value = outcome.audit_record.get("visitor_session_id")
            if visitor_value:
                visitor_session_id = str(visitor_value)
            history.append({"role": "user", "content": str(turn["text"])})
            history.append(
                {"role": "assistant", "content": outcome.spoken_text or ""}
            )
            history = history[-8:]

    metrics = _calculate_metrics(
        turn_results=turn_results,
        thresholds=fixture["thresholds"],
    )
    failed_turn_count = sum(not result["passed"] for result in turn_results)
    latency = _latency_summary(turn_results)
    coverage = _coverage_summary(fixture, turn_results)
    model_name = (
        str(getattr(llm, "model_name", "") or llm.__class__.__name__)
        if llm is not None
        else "deterministic-rules"
    )
    return {
        "run_id": run_id,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fixture_version": fixture["version"],
        "mode": mode,
        "model_name": model_name,
        "prompt_version": MUSEUM_LLM_PROMPT_VERSION if mode == "llm" else "",
        "summary": {
            "case_count": len(fixture["cases"]),
            "turn_count": len(turn_results),
            "passed_turn_count": len(turn_results) - failed_turn_count,
            "failed_turn_count": failed_turn_count,
            "latency": latency,
            "coverage": coverage,
        },
        "metrics": metrics,
        "overall_pass": failed_turn_count == 0
        and all(metric["passed"] for metric in metrics)
        and coverage["passed"]
        and latency["total_ms"]["p95"]
        <= float(fixture["thresholds"]["text_chat_p95_ms"]),
        "turns": turn_results,
    }


def render_evaluation_report(
    *,
    fixture: Mapping[str, Any],
    runs: list[Mapping[str, Any]],
    manual_fluency_review: Mapping[str, Any] | None = None,
) -> str:
    if not runs:
        raise ValueError("至少需要一个评测结果")
    rules_by_turn = {
        (turn["case_id"], turn["turn_index"]): turn
        for run in runs
        if run["mode"] == "rules"
        for turn in run["turns"]
    }
    lines = [
        "# RAG-NEXT-05 自然问法与真实 LLM 评测报告",
        "",
        f"- 生成时间：{runs[-1]['generated_at']}",
        f"- 评测集版本：{fixture['version']}",
        f"- 内容范围：{len(fixture['content_packages'])} 个内容包、杭州地区 {_fixture_exhibit_count(fixture)} 件藏品",
        "- 验收边界：服务端文本层；不代表麦克风、ASR、TTS、扬声器或真机链路通过",
        "",
        "## 运行结论",
        "",
        "| 模式 | 模型 | 用例 | 轮次 | 失败轮次 | P0 结论 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for run in runs:
        summary = run["summary"]
        lines.append(
            "| "
            f"{_md(run['mode'])} | {_md(run['model_name'])} | "
            f"{summary['case_count']} | {summary['turn_count']} | "
            f"{summary['failed_turn_count']} | "
            f"{'通过' if run['overall_pass'] else '未通过'} |"
        )

    for run in runs:
        lines.extend(
            [
                "",
                f"## 指标：{_md(run['mode'])}",
                "",
                f"- 模型：`{_md(run['model_name'])}`",
                f"- 提示版本：`{_md(run['prompt_version'] or '不调用 LLM')}`",
                "",
                "| 指标 | 实测值 | 门槛 | 样本数 | 结论 |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
        )
        for metric in run["metrics"]:
            lines.append(
                "| "
                f"{_md(metric['label'])} | {_rate(metric['value'])} | "
                f"{_metric_threshold(metric)} | {metric['denominator']} | "
                f"{'通过' if metric['passed'] else '未通过'} |"
            )

        latency = run["summary"]["latency"]
        lines.extend(
            [
                "",
                "### 时延",
                "",
                "| 阶段 | 样本数 | P50 (ms) | P95 (ms) |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for stage, values in latency.items():
            lines.append(
                f"| `{_md(stage)}` | {values['count']} | "
                f"{values['p50']:g} | {values['p95']:g} |"
            )
        lines.append(
            f"\n文本聊天 P95 门槛：<= "
            f"{float(fixture['thresholds']['text_chat_p95_ms']):g} ms。"
        )

        coverage = run["summary"]["coverage"]
        lines.extend(
            [
                "",
                "### 全展品覆盖矩阵",
                "",
                "| 展品 ID | 规范名 | 别名 | 事实类型 | 干扰 | 拒答 | 连续追问 | 结论 |",
                "| --- | --- | ---: | ---: | --- | --- | --- | --- |",
            ]
        )
        for exhibit in coverage["exhibits"]:
            lines.append(
                "| "
                f"`{_md(exhibit['exhibit_id'])}` | "
                f"{'是' if exhibit['canonical'] else '否'} | "
                f"{exhibit['alias_count']}/2 | "
                f"{exhibit['fact_type_count']}/2 | "
                f"{'是' if exhibit['interference'] else '否'} | "
                f"{'是' if exhibit['unsupported'] else '否'} | "
                f"{'是' if exhibit['conversation'] else '否'} | "
                f"{'通过' if exhibit['passed'] else '未通过'} |"
            )

        guard_counts = Counter(
            str(turn["actual"].get("guard_result", ""))
            for turn in run["turns"]
        )
        lines.extend(["", "### 守卫结果", ""])
        for guard_result, count in sorted(guard_counts.items()):
            lines.append(f"- `{_md(guard_result or 'missing')}`：{count} 轮")

        if run["mode"] == "llm":
            invoked_turns = [
                turn for turn in run["turns"] if turn["actual"]["llm_invoked"]
            ]
            parsed_turns = [
                turn
                for turn in invoked_turns
                if turn["actual"]["llm_result"] == "parsed"
            ]
            decision_statuses = Counter(
                _response_summary(turn).get("status", "unknown")
                for turn in parsed_turns
            )
            grounded_decisions = decision_statuses.get("grounded", 0)
            accepted_answers = guard_counts.get("model_answer_accepted", 0)
            semantic_recoveries = sum(
                1
                for turn in run["turns"]
                if turn["actual"]["knowledge_status"] == "grounded"
                and rules_by_turn.get(
                    (turn["case_id"], turn["turn_index"]), {}
                ).get("actual", {}).get("knowledge_status")
                == "unsupported"
            )
            lines.extend(
                [
                    "",
                    "### LLM 诊断",
                    "",
                    f"- 实际调用：{len(invoked_turns)} 轮；结构化响应成功解析：{len(parsed_turns)} 轮。",
                    f"- 模型判定 grounded：{grounded_decisions} 轮；判定 unsupported：{decision_statuses.get('unsupported', 0)} 轮。",
                    f"- 模型措辞直接通过守卫：{accepted_answers}/{grounded_decisions or 0} 轮；其余 grounded 轮次使用确定性回答回退。",
                    f"- 相比规则基线，真实 LLM 额外接住自然语义问法：{semantic_recoveries} 轮。",
                    _manual_fluency_line(manual_fluency_review),
                ]
            )

        failures = [turn for turn in run["turns"] if not turn["passed"]]
        lines.extend(["", "### 失败样本", ""])
        if not failures:
            lines.append("- 无。")
        else:
            for turn in failures:
                lines.append(
                    f"- `{_md(turn['case_id'])}#{turn['turn_index']}` "
                    f"{_md(turn['text'])}：{_md('; '.join(turn['failures']))}"
                )

        natural_turns = [
            turn
            for turn in run["turns"]
            if turn["category"] in {"natural_rephrase", "multi_turn"}
        ]
        lines.extend(
            [
                "",
                "### 自然问法回答样本",
                "",
                "| 用例 | 问题 | 状态 | 事实 ID | 回答 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for turn in natural_turns[:12]:
            actual = turn["actual"]
            lines.append(
                "| "
                f"{_md(turn['case_id'])} | {_md(turn['text'])} | "
                f"{_md(actual['knowledge_status'])} | "
                f"{_md(', '.join(actual['fact_ids']) or '-')} | "
                f"{_md(actual['answer'])} |"
            )

    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- 规则基线与真实 LLM 使用同一批问题、同一批已发布事实和同一套审计检查。",
            "- LLM 只能选择本轮候选事实；结构解析失败、事实 ID 非法或回答越界时立即回退到确定性回答。",
            "- 自然问法流畅度保留人工判断，不用事实准确率替代。",
            "- 真机验收继续由 REQ-015 独立完成。",
            "",
        ]
    )
    return "\n".join(lines)


def _evaluate_turn(
    *,
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    turn_index: int,
    expected: Mapping[str, Any],
    outcome,
    trace: dict[str, Any] | None,
    mode: str,
) -> dict[str, Any]:
    context = outcome.display_state.get("context", {})
    actual = {
        "answer": outcome.spoken_text or "",
        "request_id": outcome.audit_record.get("request_id", ""),
        "knowledge_status": outcome.knowledge_status,
        "resolution_status": outcome.audit_record.get("resolution_status", ""),
        "context_exhibit_id": context.get("exhibit_id", ""),
        "context_source": outcome.audit_record.get(
            "context_source", context.get("source", "")
        ),
        "coarse_intent": outcome.audit_record.get("coarse_intent", ""),
        "fine_intent": outcome.audit_record.get("fine_intent", ""),
        "fact_ids": list(outcome.fact_ids),
        "source_ids": list(outcome.source_ids),
        "guard_result": outcome.audit_record.get("guard_result", ""),
        "llm_invoked": bool(outcome.audit_record.get("llm_invoked", False)),
        "llm_model": outcome.audit_record.get("llm_model", ""),
        "llm_prompt_version": outcome.audit_record.get(
            "llm_prompt_version", ""
        ),
        "llm_result": outcome.audit_record.get("llm_result", "not_called"),
        "llm_response_summary": outcome.audit_record.get(
            "llm_response_summary", "{}"
        ),
        "duration_ms": outcome.audit_record.get("duration_ms", 0),
        "stage_latency": dict(outcome.audit_record.get("stage_latency", {})),
        "retrieval_trace": dict(outcome.audit_record.get("retrieval_trace", {})),
    }
    failures = _expectation_failures(expected=expected, actual=actual)
    audit_ok = _audit_is_reproducible(actual=actual, trace=trace)
    metrics = tuple(str(metric) for metric in turn.get("metrics", []))
    checks: dict[str, bool] = {}
    for metric in metrics:
        if metric == "canonical_resolution":
            checks[metric] = _resolved_expected_exhibit(actual, expected)
        elif metric == "alias_resolution":
            checks[metric] = _resolved_expected_exhibit(actual, expected)
        elif metric == "asr_alias_resolution":
            checks[metric] = _resolved_expected_exhibit(actual, expected)
        elif metric == "ambiguous_no_binding":
            checks[metric] = (
                actual["resolution_status"] == "ambiguous"
                and not actual["context_exhibit_id"]
                and not actual["fact_ids"]
            )
        elif metric == "unlisted_no_inherit":
            checks[metric] = (
                actual["resolution_status"] == "not_found"
                and not actual["context_exhibit_id"]
                and not actual["fact_ids"]
            )
        elif metric == "grounded_boundary":
            checks[metric] = _grounded_boundary_is_safe(
                expected=expected,
                actual=actual,
                trace=trace,
            )
        elif metric == "unsupported_no_hallucination":
            checks[metric] = (
                actual["knowledge_status"] == "unsupported"
                and not actual["fact_ids"]
                and not actual["source_ids"]
                and _trace_evidence(trace).get("fact_ids", []) == []
            )
        elif metric == "unsupported_correct_rejection":
            checks[metric] = (
                expected.get("knowledge_status") == "unsupported"
                and actual["knowledge_status"] == "unsupported"
                and not actual["fact_ids"]
                and not actual["source_ids"]
            )
        elif metric == "conversation_context":
            checks[metric] = (
                actual["context_exhibit_id"]
                == expected.get("context_exhibit_id", "")
                and actual["context_source"]
                == expected.get("context_source", actual["context_source"])
            )
        elif metric == "retrieval_recall_at_3":
            expected_fact_ids = set(
                expected.get("required_fact_ids", expected.get("fact_ids", []))
            )
            ranked_fact_ids = _retrieval_top_fact_ids(actual["retrieval_trace"])
            checks[metric] = bool(expected_fact_ids) and expected_fact_ids.issubset(
                set(ranked_fact_ids[:3])
            )
        elif metric == "audit_reproducibility":
            checks[metric] = audit_ok
        else:
            checks[metric] = False
    failures.extend(
        f"metric {name} failed" for name, passed in checks.items() if not passed
    )
    return {
        "case_id": str(case["id"]),
        "category": str(case.get("category", "uncategorized")),
        "turn_index": turn_index,
        "mode": mode,
        "text": str(turn["text"]),
        "expected": dict(expected),
        "actual": actual,
        "checks": checks,
        "coverage": dict(turn.get("coverage", {})),
        "failures": failures,
        "passed": not failures,
    }


def _expectation_failures(
    *,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> list[str]:
    failures: list[str] = []
    for key in (
        "knowledge_status",
        "resolution_status",
        "context_exhibit_id",
        "context_source",
        "coarse_intent",
        "fine_intent",
    ):
        if key in expected and actual.get(key) != expected[key]:
            failures.append(
                f"{key}: expected {expected[key]!r}, got {actual.get(key)!r}"
            )
    if "fact_ids" in expected and actual["fact_ids"] != expected["fact_ids"]:
        failures.append(
            f"fact_ids: expected {expected['fact_ids']!r}, got {actual['fact_ids']!r}"
        )
    required = set(expected.get("required_fact_ids", []))
    actual_facts = set(actual["fact_ids"])
    if required and not required.issubset(actual_facts):
        failures.append(
            f"required_fact_ids missing: {sorted(required - actual_facts)!r}"
        )
    allowed = set(expected.get("allowed_fact_ids", []))
    if allowed and not actual_facts.issubset(allowed):
        failures.append(
            f"fact_ids outside allowed set: {sorted(actual_facts - allowed)!r}"
        )
    contains = expected.get("contains", [])
    if isinstance(contains, str):
        contains = [contains]
    for fragment in contains:
        if str(fragment) not in str(actual["answer"]):
            failures.append(f"answer missing fragment: {fragment!r}")
    return failures


def _resolved_expected_exhibit(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    return (
        actual["resolution_status"] == "explicit"
        and bool(expected.get("context_exhibit_id"))
        and actual["context_exhibit_id"] == expected["context_exhibit_id"]
    )


def _grounded_boundary_is_safe(
    *,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    trace: dict[str, Any] | None,
) -> bool:
    expected_status = expected.get("knowledge_status")
    if expected_status != "grounded":
        return not actual["fact_ids"] and not actual["source_ids"]
    if actual["knowledge_status"] != "grounded":
        return False
    if not actual["fact_ids"] or not actual["source_ids"]:
        return False
    if _expectation_failures(expected=expected, actual=actual):
        return False
    evidence = _trace_evidence(trace)
    return (
        evidence.get("fact_ids", []) == actual["fact_ids"]
        and evidence.get("source_ids", []) == actual["source_ids"]
        and bool(evidence.get("content_revision_id"))
        and evidence.get("content_version") is not None
    )


def _audit_is_reproducible(
    *,
    actual: Mapping[str, Any],
    trace: dict[str, Any] | None,
) -> bool:
    if trace is None:
        return False
    evidence = _trace_evidence(trace)
    return (
        str(trace.get("request_id", "")) == actual["request_id"]
        and str(trace.get("grounding_status", "")) == actual["knowledge_status"]
        and str(trace.get("answer_text", "")) == actual["answer"]
        and str(trace.get("exhibit_id") or "") == actual["context_exhibit_id"]
        and evidence.get("fact_ids", []) == actual["fact_ids"]
        and evidence.get("source_ids", []) == actual["source_ids"]
        and bool(trace.get("llm_invoked")) == actual["llm_invoked"]
        and str(trace.get("llm_model", "")) == actual["llm_model"]
        and str(trace.get("llm_prompt_version", ""))
        == actual["llm_prompt_version"]
        and str(trace.get("llm_result", "not_called")) == actual["llm_result"]
        and str(trace.get("llm_response_summary", "{}"))
        == actual["llm_response_summary"]
        and str(trace.get("guard_result", "")) == actual["guard_result"]
    )


def _trace_evidence(trace: dict[str, Any] | None) -> dict[str, Any]:
    if trace is None:
        return {}
    try:
        evidence = json.loads(str(trace.get("evidence_json", "{}")))
    except json.JSONDecodeError:
        return {}
    return evidence if isinstance(evidence, dict) else {}


def _retrieval_top_fact_ids(trace: Mapping[str, Any]) -> list[str]:
    mode = str(trace.get("mode", "rules"))
    key = "fused_candidates" if mode == "hybrid" else "lexical_candidates"
    candidates = trace.get(key, [])
    if not isinstance(candidates, list):
        return []
    ranked = sorted(
        (candidate for candidate in candidates if isinstance(candidate, Mapping)),
        key=lambda candidate: int(candidate.get("rank", 10**9)),
    )
    return [str(candidate.get("fact_id", "")) for candidate in ranked]


def _latency_summary(
    turn_results: list[Mapping[str, Any]],
) -> dict[str, dict[str, float | int]]:
    samples: dict[str, list[float]] = {"total_ms": []}
    for turn in turn_results:
        actual = turn["actual"]
        samples["total_ms"].append(float(actual.get("duration_ms", 0)))
        stage_latency = actual.get("stage_latency", {})
        if isinstance(stage_latency, Mapping):
            for name, value in stage_latency.items():
                try:
                    samples.setdefault(str(name), []).append(float(value))
                except (TypeError, ValueError):
                    continue
        retrieval = actual.get("retrieval_trace", {})
        retrieval_stages = (
            retrieval.get("stage_latency_ms", {})
            if isinstance(retrieval, Mapping)
            else {}
        )
        if isinstance(retrieval_stages, Mapping):
            for name, value in retrieval_stages.items():
                try:
                    samples.setdefault(f"retrieval_{name}_ms", []).append(
                        float(value)
                    )
                except (TypeError, ValueError):
                    continue
    return {
        name: {
            "count": len(values),
            "p50": _percentile(values, 50),
            "p95": _percentile(values, 95),
        }
        for name, values in sorted(samples.items())
        if values
    }


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * fraction,
        2,
    )


def _coverage_summary(
    fixture: Mapping[str, Any],
    turn_results: list[Mapping[str, Any]],
) -> dict[str, Any]:
    profile = fixture.get("coverage", {})
    exhibits = profile.get("exhibits", []) if isinstance(profile, Mapping) else []
    by_exhibit: dict[str, list[Mapping[str, Any]]] = {}
    for turn in turn_results:
        coverage = turn.get("coverage", {})
        if not isinstance(coverage, Mapping):
            continue
        exhibit_id = str(coverage.get("exhibit_id", ""))
        if exhibit_id:
            by_exhibit.setdefault(exhibit_id, []).append(turn)

    matrix: list[dict[str, Any]] = []
    for exhibit in exhibits:
        exhibit_id = str(exhibit["exhibit_id"])
        samples = by_exhibit.get(exhibit_id, [])
        passed_kinds = {
            str(turn["coverage"].get("kind", ""))
            for turn in samples
            if turn["passed"]
        }
        passed_aliases = {
            str(turn["coverage"].get("slot", ""))
            for turn in samples
            if turn["passed"] and turn["coverage"].get("kind") == "alias"
        }
        passed_fact_types = {
            str(turn["coverage"].get("fact_type", ""))
            for turn in samples
            if turn["passed"] and turn["coverage"].get("kind") == "fact"
        }
        row = {
            "exhibit_id": exhibit_id,
            "canonical": "canonical" in passed_kinds,
            "alias_count": len(passed_aliases - {""}),
            "fact_type_count": len(passed_fact_types - {""}),
            "interference": "interference" in passed_kinds,
            "unsupported": "unsupported" in passed_kinds,
            "conversation": "conversation" in passed_kinds,
        }
        row["passed"] = (
            row["canonical"]
            and row["alias_count"] >= 2
            and row["fact_type_count"] >= 2
            and row["interference"]
            and row["unsupported"]
            and row["conversation"]
        )
        matrix.append(row)
    return {
        "expected_exhibit_count": len(exhibits),
        "passed_exhibit_count": sum(row["passed"] for row in matrix),
        "passed": bool(matrix) and all(row["passed"] for row in matrix),
        "exhibits": matrix,
    }


def _coverage_cases(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for exhibit in profile["exhibits"]:
        exhibit_id = str(exhibit["exhibit_id"])
        prefix = f"stage2-coverage-{exhibit_id}"
        cases.append(
            _single_coverage_case(
                case_id=f"{prefix}-canonical",
                category="coverage_canonical",
                exhibit_id=exhibit_id,
                kind="canonical",
                sample=exhibit["canonical"],
                resolution_metric="canonical_resolution",
            )
        )
        for index, sample in enumerate(exhibit["aliases"], start=1):
            cases.append(
                _single_coverage_case(
                    case_id=f"{prefix}-alias-{index}",
                    category="coverage_alias",
                    exhibit_id=exhibit_id,
                    kind="alias",
                    slot=str(index),
                    sample=sample,
                    resolution_metric="alias_resolution",
                )
            )
        for index, sample in enumerate(exhibit["facts"], start=1):
            cases.append(
                _single_coverage_case(
                    case_id=f"{prefix}-fact-{index}",
                    category="coverage_fact",
                    exhibit_id=exhibit_id,
                    kind="fact",
                    fact_type=str(sample["fact_type"]),
                    sample=sample,
                )
            )
        cases.append(
            _single_coverage_case(
                case_id=f"{prefix}-interference",
                category="coverage_interference",
                exhibit_id=exhibit_id,
                kind="interference",
                sample=exhibit["interference"],
            )
        )
        unsupported = exhibit["unsupported"]
        cases.append(
            {
                "id": f"{prefix}-unsupported",
                "category": "coverage_unsupported",
                "turns": [
                    {
                        "text": unsupported["text"],
                        "metrics": [
                            "unsupported_no_hallucination",
                            "unsupported_correct_rejection",
                            "grounded_boundary",
                            "audit_reproducibility",
                        ],
                        "coverage": {"exhibit_id": exhibit_id, "kind": "unsupported"},
                        "expected": {
                            "knowledge_status": "unsupported",
                            "resolution_status": "explicit",
                            "context_exhibit_id": exhibit_id,
                            "fact_ids": [],
                        },
                    }
                ],
            }
        )
        conversation = exhibit["conversation"]
        cases.append(
            {
                "id": f"{prefix}-conversation",
                "category": "coverage_conversation",
                "turns": [
                    _grounded_coverage_turn(
                        exhibit_id=exhibit_id,
                        sample=conversation["initial"],
                    ),
                    _grounded_coverage_turn(
                        exhibit_id=exhibit_id,
                        sample=conversation["followup"],
                        inherited=True,
                        coverage={"exhibit_id": exhibit_id, "kind": "conversation"},
                        extra_metrics=["conversation_context"],
                    ),
                ],
            }
        )
    return cases


def _single_coverage_case(
    *,
    case_id: str,
    category: str,
    exhibit_id: str,
    kind: str,
    sample: Mapping[str, Any],
    slot: str = "",
    fact_type: str = "",
    resolution_metric: str = "",
) -> dict[str, Any]:
    coverage = {"exhibit_id": exhibit_id, "kind": kind}
    if slot:
        coverage["slot"] = slot
    if fact_type:
        coverage["fact_type"] = fact_type
    metrics = [
        metric
        for metric in (
            resolution_metric,
            "grounded_boundary",
            "retrieval_recall_at_3",
            "audit_reproducibility",
        )
        if metric
    ]
    return {
        "id": case_id,
        "category": category,
        "turns": [
            _grounded_coverage_turn(
                exhibit_id=exhibit_id,
                sample=sample,
                coverage=coverage,
                extra_metrics=metrics,
                replace_metrics=True,
            )
        ],
    }


def _grounded_coverage_turn(
    *,
    exhibit_id: str,
    sample: Mapping[str, Any],
    inherited: bool = False,
    coverage: Mapping[str, Any] | None = None,
    extra_metrics: list[str] | None = None,
    replace_metrics: bool = False,
) -> dict[str, Any]:
    metrics = list(extra_metrics or [])
    if not replace_metrics:
        metrics.extend(
            ["grounded_boundary", "retrieval_recall_at_3", "audit_reproducibility"]
        )
    expected = {
        "knowledge_status": "grounded",
        "resolution_status": "inherited" if inherited else "explicit",
        "context_exhibit_id": exhibit_id,
        "context_source": "inherited_session" if inherited else "explicit_mention",
    }
    if "required_fact_ids" in sample or "allowed_fact_ids" in sample:
        expected["required_fact_ids"] = list(sample.get("required_fact_ids", []))
        expected["allowed_fact_ids"] = list(sample.get("allowed_fact_ids", []))
    else:
        expected["fact_ids"] = [sample["fact_id"]]
    return {
        "text": sample["text"],
        "metrics": list(dict.fromkeys(metrics)),
        "coverage": dict(coverage or {}),
        "expected": expected,
    }


def _validate_coverage_profile(profile: Any) -> None:
    if not isinstance(profile, dict) or profile.get("version") != 1:
        raise ValueError("覆盖档案必须是 version=1 的 JSON 对象")
    exhibits = profile.get("exhibits")
    if not isinstance(exhibits, list) or not exhibits:
        raise ValueError("覆盖档案 exhibits 必须是非空数组")
    exhibit_ids: set[str] = set()
    for exhibit in exhibits:
        if not isinstance(exhibit, dict):
            raise ValueError("覆盖档案 exhibit 必须是对象")
        exhibit_id = str(exhibit.get("exhibit_id", "")).strip()
        if not exhibit_id or exhibit_id in exhibit_ids:
            raise ValueError(f"覆盖档案展品 ID 为空或重复: {exhibit_id!r}")
        exhibit_ids.add(exhibit_id)
        aliases = exhibit.get("aliases")
        facts = exhibit.get("facts")
        if not isinstance(aliases, list) or len(aliases) < 2:
            raise ValueError(f"覆盖档案 {exhibit_id} 至少需要两个别名问题")
        if not isinstance(facts, list) or len(facts) < 2:
            raise ValueError(f"覆盖档案 {exhibit_id} 至少需要两个事实问题")
        fact_types = {str(sample.get("fact_type", "")) for sample in facts}
        if "" in fact_types or len(fact_types) < 2:
            raise ValueError(f"覆盖档案 {exhibit_id} 事实问题类型必须至少两种")
        required = ("canonical", "interference", "unsupported", "conversation")
        if any(not isinstance(exhibit.get(key), dict) for key in required):
            raise ValueError(f"覆盖档案 {exhibit_id} 缺少必需场景")
        samples = [
            exhibit["canonical"],
            *aliases,
            *facts,
            exhibit["interference"],
            exhibit["unsupported"],
            exhibit["conversation"].get("initial", {}),
            exhibit["conversation"].get("followup", {}),
        ]
        for sample in samples:
            if not isinstance(sample, dict) or not str(sample.get("text", "")).strip():
                raise ValueError(f"覆盖档案 {exhibit_id} 存在空问题")
        grounded = [
            exhibit["canonical"],
            *aliases,
            *facts,
            exhibit["interference"],
            exhibit["conversation"]["initial"],
            exhibit["conversation"]["followup"],
        ]
        for sample in grounded:
            fact_id = str(sample.get("fact_id", "")).strip()
            required = sample.get("required_fact_ids", [])
            allowed = sample.get("allowed_fact_ids", [])
            if not fact_id and not required:
                raise ValueError(
                    f"覆盖档案 {exhibit_id} 有依据问题缺少 fact_id 或 required_fact_ids"
                )
            if required and not isinstance(required, list):
                raise ValueError(
                    f"覆盖档案 {exhibit_id} required_fact_ids 必须是数组"
                )
            if allowed and not isinstance(allowed, list):
                raise ValueError(
                    f"覆盖档案 {exhibit_id} allowed_fact_ids 必须是数组"
                )
            if allowed and not set(required).issubset(set(allowed)):
                raise ValueError(
                    f"覆盖档案 {exhibit_id} required_fact_ids 必须包含在 allowed_fact_ids 中"
                )


def _response_summary(turn: Mapping[str, Any]) -> dict[str, Any]:
    try:
        summary = json.loads(str(turn["actual"]["llm_response_summary"]))
    except (KeyError, TypeError, json.JSONDecodeError):
        return {}
    return summary if isinstance(summary, dict) else {}


def _manual_fluency_line(review: Mapping[str, Any] | None) -> str:
    if not review:
        return "- 人工流畅度评审：未执行；自动化事实指标不能替代人工评分。"
    score = float(review.get("score", 0))
    scale = float(review.get("scale", 5))
    if scale <= 0 or score < 0 or score > scale:
        raise ValueError("人工流畅度评分必须位于 0 到 scale 之间")
    reviewer = (
        str(review.get("reviewer", "项目内样本复核")).strip()
        or "项目内样本复核"
    )
    note = str(review.get("note", "")).strip()
    suffix = f"{note}；" if note else ""
    return (
        f"- 人工流畅度评审：{score:g}/{scale:g}（{reviewer}）。"
        f"{suffix}这不是儿童独立评审结论。"
    )


def _calculate_metrics(
    *,
    turn_results: list[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for metric_id, label, check_name, direction in _METRIC_DEFINITIONS:
        samples = [
            bool(turn["checks"][check_name])
            for turn in turn_results
            if check_name in turn["checks"]
        ]
        if not samples:
            raise ValueError(f"评测集缺少指标样本: {metric_id}")
        passed_count = sum(samples)
        pass_rate = passed_count / len(samples)
        value = pass_rate if direction == "minimum" else 1.0 - pass_rate
        threshold = float(thresholds[metric_id])
        passed = value >= threshold if direction == "minimum" else value <= threshold
        metrics.append(
            {
                "id": metric_id,
                "label": label,
                "value": value,
                "threshold": threshold,
                "direction": direction,
                "passed": passed,
                "numerator": passed_count,
                "denominator": len(samples),
            }
        )
    return metrics


def _fixture_exhibit_count(fixture: Mapping[str, Any]) -> int:
    exhibit_ids: set[str] = set()
    for case in fixture["cases"]:
        for turn in case["turns"]:
            expectations = [turn.get("expected", {})]
            mode_overrides = turn.get("expected_by_mode", {})
            if isinstance(mode_overrides, Mapping):
                expectations.extend(mode_overrides.values())
            for expected in expectations:
                if not isinstance(expected, Mapping):
                    continue
                exhibit_id = str(expected.get("context_exhibit_id", "")).strip()
                if exhibit_id:
                    exhibit_ids.add(exhibit_id)
    return len(exhibit_ids)


def _expected_for_mode(
    turn: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    expected = dict(turn.get("expected", {}))
    mode_overrides = turn.get("expected_by_mode", {})
    if isinstance(mode_overrides, Mapping):
        override = mode_overrides.get(mode, {})
        if isinstance(override, Mapping):
            expected.update(override)
    if "knowledge_status" not in expected:
        raise ValueError(f"评测轮次缺少 {mode} 模式的 knowledge_status")
    return expected


def _validate_fixture(fixture: Any) -> None:
    if not isinstance(fixture, dict) or fixture.get("version") != 2:
        raise ValueError("评测集必须是 version=2 的 JSON 对象")
    packages = fixture.get("content_packages")
    if not isinstance(packages, list) or not packages or any(
        not isinstance(path, str) or not path.strip() for path in packages
    ):
        raise ValueError("评测集 content_packages 必须是非空字符串数组")
    thresholds = fixture.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("评测集缺少 thresholds")
    missing_thresholds = {
        definition[0] for definition in _METRIC_DEFINITIONS
    } - set(thresholds)
    if missing_thresholds:
        raise ValueError(f"评测集缺少门槛: {sorted(missing_thresholds)}")
    if "text_chat_p95_ms" not in thresholds:
        raise ValueError("评测集缺少门槛: text_chat_p95_ms")
    cases = fixture.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("评测集 cases 必须是非空数组")
    case_ids: set[str] = set()
    used_checks: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("评测 case 必须是对象")
        case_id = str(case.get("id", "")).strip()
        if not case_id or case_id in case_ids:
            raise ValueError(f"评测 case ID 为空或重复: {case_id!r}")
        case_ids.add(case_id)
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"评测 case {case_id} 没有 turns")
        for turn in turns:
            if not isinstance(turn, dict) or not str(turn.get("text", "")).strip():
                raise ValueError(f"评测 case {case_id} 存在空问题")
            metrics = turn.get("metrics", [])
            if not isinstance(metrics, list) or any(
                metric not in _KNOWN_CHECKS for metric in metrics
            ):
                raise ValueError(f"评测 case {case_id} 使用未知指标")
            used_checks.update(metrics)
            _expected_for_mode(turn, "rules")
            _expected_for_mode(turn, "llm")
    missing_checks = _KNOWN_CHECKS - used_checks
    if missing_checks:
        raise ValueError(f"评测集缺少指标样本: {sorted(missing_checks)}")


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return normalized or "run"


def _rate(value: float) -> str:
    return f"{value * 100:.2f}%"


def _metric_threshold(metric: Mapping[str, Any]) -> str:
    operator = ">=" if metric["direction"] == "minimum" else "<="
    return f"{operator} {_rate(float(metric['threshold']))}"


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()
