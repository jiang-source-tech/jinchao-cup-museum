from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config.settings import load_config  # noqa: E402
from core.business_runtime_factory import create_conversation_runtime  # noqa: E402
from scripts.museum_text_chat import (  # noqa: E402
    MuseumTextChatSession,
    outcome_payload,
)


DEFAULT_FIXTURE = (
    SERVER_ROOT / "tests" / "fixtures" / "museum_generalization_eval.json"
)


def load_fixture(path: str | Path) -> dict[str, Any]:
    fixture_path = Path(path)
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取泛化评测集 {fixture_path}: {exc}") from exc
    cases = fixture.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("泛化评测集必须包含非空 cases 数组")
    for case in cases:
        if not isinstance(case, dict) or not str(case.get("id", "")).strip():
            raise ValueError("每个泛化评测用例必须包含 id")
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"用例 {case.get('id')} 必须包含非空 turns 数组")
        for turn in turns:
            if not isinstance(turn, dict) or not str(turn.get("text", "")).strip():
                raise ValueError(f"用例 {case.get('id')} 存在空问题")
            if not isinstance(turn.get("expected"), dict):
                raise ValueError(f"用例 {case.get('id')} 的每轮必须包含 expected")
    return fixture


def evaluate_fixture(
    fixture: Mapping[str, Any],
    *,
    ask: Callable[[str], Mapping[str, Any]],
    reset: Callable[[], None],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in fixture["cases"]:
        reset()
        for turn_index, turn in enumerate(case["turns"], start=1):
            actual = dict(ask(str(turn["text"])))
            expected = dict(turn["expected"])
            mismatches = _compare(expected, actual)
            results.append(
                {
                    "case_id": str(case["id"]),
                    "category": str(case.get("category", "uncategorized")),
                    "tags": list(case.get("tags", [])),
                    "turn_index": turn_index,
                    "text": str(turn["text"]),
                    "expected": expected,
                    "actual": _result_snapshot(actual),
                    "passed": not mismatches,
                    "mismatches": mismatches,
                }
            )
    return _summarize(results, fixture_version=fixture.get("version"))


def _compare(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> list[str]:
    mismatches: list[str] = []
    for field in (
        "knowledge_status",
        "coarse_intent",
        "fine_intent",
        "exhibit_id",
        "resolution_status",
    ):
        if field not in expected:
            continue
        accepted = expected[field]
        accepted_values = accepted if isinstance(accepted, list) else [accepted]
        if actual.get(field) not in accepted_values:
            mismatches.append(
                f"{field}: expected={accepted_values!r} actual={actual.get(field)!r}"
            )
    expected_fact_ids = set(expected.get("fact_ids", []))
    if expected_fact_ids:
        actual_fact_ids = set(actual.get("fact_ids", []))
        missing = sorted(expected_fact_ids - actual_fact_ids)
        if missing:
            mismatches.append(f"fact_ids missing={missing!r}")
    if expected.get("no_fact_ids") and actual.get("fact_ids"):
        mismatches.append(f"fact_ids expected empty actual={actual.get('fact_ids')!r}")
    return mismatches


def _result_snapshot(actual: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: actual.get(field)
        for field in (
            "knowledge_status",
            "coarse_intent",
            "fine_intent",
            "intent_confidence",
            "exhibit_id",
            "resolution_status",
            "fact_ids",
            "answer",
            "error_code",
        )
    }


def _summarize(
    results: list[dict[str, Any]],
    *,
    fixture_version: Any,
) -> dict[str, Any]:
    category_totals: Counter[str] = Counter()
    category_passes: Counter[str] = Counter()
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        category = result["category"]
        category_totals[category] += 1
        if result["passed"]:
            category_passes[category] += 1
        expected_intent = result["expected"].get("fine_intent")
        if isinstance(expected_intent, str):
            confusion[expected_intent][
                str(result["actual"].get("fine_intent", ""))
            ] += 1
    passed = sum(result["passed"] for result in results)
    return {
        "fixture_version": fixture_version,
        "summary": {
            "turn_count": len(results),
            "passed_turn_count": passed,
            "failed_turn_count": len(results) - passed,
            "pass_rate": round(passed / len(results), 4) if results else 0.0,
        },
        "categories": {
            category: {
                "turn_count": category_totals[category],
                "passed_turn_count": category_passes[category],
                "pass_rate": round(
                    category_passes[category] / category_totals[category],
                    4,
                ),
            }
            for category in sorted(category_totals)
        },
        "intent_confusion": {
            expected: dict(sorted(actual.items()))
            for expected, actual in sorted(confusion.items())
        },
        "turns": results,
    }


def render_report(payload: Mapping[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# 博物馆 RAG 泛化评测",
        "",
        f"- 轮次：{summary['turn_count']}",
        f"- 通过：{summary['passed_turn_count']}",
        f"- 失败：{summary['failed_turn_count']}",
        f"- 通过率：{summary['pass_rate']:.2%}",
        "",
        "## 分类结果",
        "",
        "| 分类 | 通过 | 总数 | 通过率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for category, result in payload["categories"].items():
        lines.append(
            f"| {category} | {result['passed_turn_count']} | "
            f"{result['turn_count']} | {result['pass_rate']:.2%} |"
        )
    failed = [turn for turn in payload["turns"] if not turn["passed"]]
    lines.extend(["", "## 失败样例", ""])
    if not failed:
        lines.append("无。")
    for turn in failed:
        lines.extend(
            [
                f"### {turn['case_id']} / turn {turn['turn_index']}",
                "",
                f"- 问句：{turn['text']}",
                f"- 差异：{'；'.join(turn['mismatches'])}",
                f"- 实际回答：{turn['actual'].get('answer') or ''}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行博物馆 RAG 留出泛化评测")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--device-id",
        default="museum-generalization-eval",
        help="评测设备 ID 前缀",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    fixture = load_fixture(args.fixture)
    runtime = create_conversation_runtime(load_config())
    session = MuseumTextChatSession(
        runtime=runtime,
        llm=None,
        device_prefix=args.device_id,
    )
    payload = evaluate_fixture(
        fixture,
        ask=lambda text: outcome_payload(session.ask(text)),
        reset=session.reset,
    )
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    report = render_report(payload)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
    print(report, end="")
    return 0 if payload["summary"]["failed_turn_count"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"泛化评测启动失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
