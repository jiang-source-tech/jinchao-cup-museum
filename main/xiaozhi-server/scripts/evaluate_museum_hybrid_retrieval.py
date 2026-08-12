from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config.settings import load_config  # noqa: E402
from core.business_runtime_factory import create_conversation_runtime  # noqa: E402
from core.museum.retrieval import RetrievalRequest  # noqa: E402
from core.museum.retrieval import dense_fact_types_for_intent  # noqa: E402
from core.museum.query_understanding import understand_question  # noqa: E402


DEFAULT_CASES = (
    {
        "exhibit_id": "warring-states-crystal-cup",
        "question": "古人拿哪种矿物琢成了这个杯子？",
        "expected_fact_id": "fact-crystal-cup-material",
    },
    {
        "exhibit_id": "liangzhu-jade-yue-set",
        "question": "这套东西是不是代表了主人的权力？",
        "expected_fact_id": "fact-liangzhu-yue-usage",
    },
    {
        "exhibit_id": "liangzhu-jade-trident",
        "question": "它原来戴在身体哪个位置？",
        "expected_fact_id": "fact-liangzhu-trident-usage",
    },
    {
        "exhibit_id": "southern-song-guan-bagua-incense-lid",
        "question": "上面的洞除了好看还有什么用？",
        "expected_fact_id": "fact-west-lake-bagua-usage",
    },
    {
        "exhibit_id": "qing-butterfly-medallion-robe-fabric",
        "question": "这块衣料上的图案是怎么做上去的？",
        "expected_fact_id": "fact-china-silk-butterfly-craft",
    },
)


def evaluate(config: dict, cases: list[dict[str, str]]) -> dict[str, object]:
    runtime = create_conversation_runtime(config)
    retriever = runtime._answering._retriever
    rows = []
    latencies = []
    branch_hits = {"lexical": 0, "dense": 0, "hybrid": 0, "selected": 0}
    for case in cases:
        started = perf_counter()
        understanding = understand_question(case["question"])
        result = retriever.retrieve(
            RetrievalRequest(
                exhibit_id=case["exhibit_id"],
                question=case["question"],
                fact_types=understanding.fact_types,
                query_terms=understanding.query_terms,
                overview=understanding.fine_intent == "overview",
                allow_dense_only=(
                    understanding.coarse_intent == "exhibit_knowledge"
                    and understanding.fine_intent != "unknown"
                    and "price" not in understanding.fact_types
                ),
                dense_fact_types=dense_fact_types_for_intent(
                    understanding.fine_intent,
                    understanding.fact_types,
                    understanding.query_terms,
                ),
            )
        )
        latency_ms = round((perf_counter() - started) * 1000)
        latencies.append(latency_ms)
        expected = case["expected_fact_id"]
        lexical_ids = [item.fact_id for item in result.diagnostics.lexical_candidates]
        dense_ids = [item.fact_id for item in result.diagnostics.dense_candidates]
        hybrid_ids = [item.fact_id for item in result.diagnostics.fused_candidates]
        selected_ids = list(result.evidence.fact_ids) if result.evidence else []
        for name, ids in (
            ("lexical", lexical_ids),
            ("dense", dense_ids),
            ("hybrid", hybrid_ids),
            ("selected", selected_ids),
        ):
            branch_hits[name] += int(expected in ids[:3])
        rows.append(
            {
                **case,
                "lexical_ids": lexical_ids[:3],
                "dense_ids": dense_ids[:3],
                "hybrid_ids": hybrid_ids[:3],
                "selected_ids": selected_ids,
                "fine_intent": understanding.fine_intent,
                "fallback_reason": result.diagnostics.fallback_reason,
                "latency_ms": latency_ms,
            }
        )
    count = len(cases)
    ordered_latency = sorted(latencies)
    p95_index = max(0, min(count - 1, round(0.95 * count + 0.5) - 1))
    return {
        "mode": config["business_runtime"]["retrieval"]["mode"],
        "case_count": count,
        "recall_at_3": {
            name: round(hits / count, 4) if count else 0
            for name, hits in branch_hits.items()
        },
        "latency_ms": {
            "p50": round(statistics.median(latencies)) if latencies else 0,
            "p95": ordered_latency[p95_index] if latencies else 0,
        },
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="评测博物馆 lexical、dense 与 hybrid 事实召回"
    )
    parser.add_argument("--mode", choices=("shadow", "hybrid"), default="shadow")
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    cases = (
        json.loads(args.cases.read_text(encoding="utf-8"))
        if args.cases
        else list(DEFAULT_CASES)
    )
    config = load_config()
    config.setdefault("business_runtime", {}).setdefault("retrieval", {})[
        "mode"
    ] = args.mode
    result = evaluate(config, cases)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
