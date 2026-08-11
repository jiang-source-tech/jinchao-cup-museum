from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SERVER_ROOT.parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config.settings import load_config  # noqa: E402
from core.museum.evaluation import (  # noqa: E402
    load_evaluation_fixture,
    prepare_evaluation_runtime,
    render_evaluation_report,
    run_evaluation,
)
from scripts.museum_text_chat import initialize_chat_llm  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行杭州馆方藏品的规则基线与真实 LLM RAG 评测"
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=SERVER_ROOT / "tests" / "fixtures" / "museum_conversation_eval.json",
        help="评测集 JSON",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=SERVER_ROOT / "tmp" / "museum-rag-evaluation.db",
        help="隔离评测数据库",
    )
    parser.add_argument(
        "--mode",
        choices=("rules", "llm", "both"),
        default="rules",
        help="运行规则基线、真实 LLM 或两者",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "docs" / "requirements" / "rag-evaluation-report.md",
        help="Markdown 报告输出路径",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=SERVER_ROOT / "tmp" / "museum-rag-evaluation.json",
        help="完整机器可读结果输出路径",
    )
    parser.add_argument(
        "--run-id",
        default=datetime.now().astimezone().strftime("%Y%m%d-%H%M%S"),
        help="写入请求 ID 和报告的稳定运行标识",
    )
    parser.add_argument(
        "--replace-database",
        action="store_true",
        help="删除同名旧评测数据库后重建",
    )
    parser.add_argument(
        "--fluency-score",
        type=float,
        help="可选人工流畅度评分；不提供时报告明确标记为未执行",
    )
    parser.add_argument(
        "--fluency-scale",
        type=float,
        default=5.0,
        help="人工流畅度评分满分，默认 5",
    )
    parser.add_argument(
        "--fluency-reviewer",
        default="项目内样本复核",
        help="人工流畅度评审主体",
    )
    parser.add_argument(
        "--fluency-note",
        default="",
        help="人工流畅度观察说明",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    manual_fluency_review = _manual_fluency_review(args)
    fixture = load_evaluation_fixture(args.fixture)
    _prepare_database_path(args.database, replace=args.replace_database)
    runtime = prepare_evaluation_runtime(
        database_path=args.database,
        server_root=SERVER_ROOT,
        fixture=fixture,
    )

    llm = None
    selected_mode = ""
    if args.mode in {"llm", "both"}:
        llm, selected_mode = initialize_chat_llm(load_config(), required=True)

    modes = ("rules", "llm") if args.mode == "both" else (args.mode,)
    runs = [
        run_evaluation(
            fixture=fixture,
            runtime=runtime,
            mode=mode,
            llm=llm if mode == "llm" else None,
            run_id=args.run_id,
        )
        for mode in modes
    ]
    payload = {
        "run_id": args.run_id,
        "llm_mode": selected_mode,
        "fixture": str(Path(args.fixture).resolve()),
        "database": str(Path(args.database).resolve()),
        "manual_fluency_review": manual_fluency_review,
        "runs": runs,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render_evaluation_report(
            fixture=fixture,
            runs=runs,
            manual_fluency_review=manual_fluency_review,
        ),
        encoding="utf-8",
    )

    for result in runs:
        summary = result["summary"]
        print(
            f"{result['mode']}: model={result['model_name']} "
            f"turns={summary['turn_count']} failed={summary['failed_turn_count']} "
            f"p0={'PASS' if result['overall_pass'] else 'FAIL'}"
        )
    print(f"report={args.report.resolve()}")
    print(f"json={args.json_output.resolve()}")
    return 0 if all(result["overall_pass"] for result in runs) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"评测启动失败：{exc}", file=sys.stderr)
        return 2


def _prepare_database_path(path: Path, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = [path, Path(f"{path}-wal"), Path(f"{path}-shm")]
    present = [candidate for candidate in existing if candidate.exists()]
    if present and not replace:
        raise ValueError(
            f"评测数据库已存在：{path}；确认可覆盖后使用 --replace-database"
        )
    for candidate in present:
        candidate.unlink()


def _manual_fluency_review(args: argparse.Namespace) -> dict | None:
    if args.fluency_score is None:
        return None
    if args.mode == "rules":
        raise ValueError("规则模式没有 LLM 回答，不能记录人工流畅度评分")
    if args.fluency_scale <= 0:
        raise ValueError("--fluency-scale 必须大于 0")
    if not 0 <= args.fluency_score <= args.fluency_scale:
        raise ValueError("--fluency-score 必须位于 0 到 --fluency-scale 之间")
    return {
        "score": args.fluency_score,
        "scale": args.fluency_scale,
        "reviewer": str(args.fluency_reviewer).strip() or "项目内样本复核",
        "note": str(args.fluency_note).strip(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
