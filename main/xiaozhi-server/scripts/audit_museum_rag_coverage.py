from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.museum.answering import GroundedAnswerService  # noqa: E402
from core.museum.exhibit_resolver import ExhibitResolver  # noqa: E402
from core.museum.query_understanding import understand_question  # noqa: E402
from core.museum.store import MuseumStore  # noqa: E402


QUESTION_TEMPLATES = {
    "appearance": (
        "{name}看起来有什么特征？",
        "{name}长得怎么样？",
        "说说{name}的造型。",
    ),
    "craft": (
        "{name}当时是怎么弄出来的？",
        "{name}采用了什么制作手法？",
        "这件{name}是如何加工的？",
    ),
    "dimensions": (
        "{name}个头有多大？",
        "{name}具体尺寸是多少？",
        "这件{name}量起来多大？",
    ),
    "era": (
        "{name}大概是哪会儿的东西？",
        "{name}距今多久了？",
        "这件{name}属于哪个年代？",
    ),
    "excavation": (
        "{name}是从哪儿找到的？",
        "{name}最初在哪里被挖出来？",
        "这件{name}发现于什么地方？",
    ),
    "history": (
        "介绍一下{name}。",
        "{name}在馆方藏品中登记的是什么？",
        "这个{name}公开叫什么？",
    ),
    "material": (
        "{name}本身是什么东西做的？",
        "{name}用的是什么料子？",
        "做{name}选了什么原料？",
    ),
    "research_limit": (
        "{name}的加工还有哪些地方没弄清楚？",
        "关于{name}的制作目前还有什么谜团？",
        "{name}怎么做成的现在都研究清楚了吗？",
    ),
    "usage": (
        "{name}以前拿来做什么？",
        "{name}原本派什么用场？",
        "这件{name}是干嘛的？",
    ),
}


def run(database: Path) -> dict[str, object]:
    store = MuseumStore(database, read_only=True)
    resolver = ExhibitResolver(store)
    service = GroundedAnswerService(store)
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT e.id AS exhibit_id, e.name, f.id AS fact_id, f.fact_type
            FROM exhibit e
            JOIN content_revision r
              ON r.exhibit_id = e.id AND r.status = 'published'
            JOIN exhibit_fact f ON f.revision_id = r.id
            ORDER BY e.id, f.id
            """
        ).fetchall()

    facts_by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    names: dict[str, str] = {}
    for row in rows:
        exhibit_id = str(row["exhibit_id"])
        fact_type = str(row["fact_type"])
        facts_by_key[(exhibit_id, fact_type)].append(str(row["fact_id"]))
        names[exhibit_id] = str(row["name"])

    cases: list[dict[str, object]] = []
    for (exhibit_id, fact_type), expected_ids in facts_by_key.items():
        for variant, template in enumerate(QUESTION_TEMPLATES[fact_type], start=1):
            name = names[exhibit_id]
            question = template.format(name=name)
            resolution = resolver.resolve(
                question=question,
                current_exhibit_id=None,
            )
            understanding = understand_question(question)
            answer = service.answer(
                exhibit_id=exhibit_id,
                exhibit_name=name,
                question=question,
            )
            evidence = answer.evidence
            hit = bool(
                evidence
                and any(fact.fact_type == fact_type for fact in evidence.facts)
            )
            cases.append(
                {
                    "exhibit_id": exhibit_id,
                    "fact_type": fact_type,
                    "variant": variant,
                    "question": question,
                    "resolution": resolution.status,
                    "fine_intent": understanding.fine_intent,
                    "fact_types": list(understanding.fact_types),
                    "expected_fact_ids": expected_ids,
                    "selected_fact_ids": list(evidence.fact_ids) if evidence else [],
                    "grounding_status": answer.knowledge_status,
                    "hit": hit,
                }
            )

    summary: dict[str, dict[str, int]] = {}
    for fact_type in QUESTION_TEMPLATES:
        subset = [case for case in cases if case["fact_type"] == fact_type]
        summary[fact_type] = {
            "cases": len(subset),
            "unknown_intent": sum(case["fine_intent"] == "unknown" for case in subset),
            "resolution_failures": sum(
                case["resolution"] not in {"explicit", "inherited"}
                for case in subset
            ),
            "unsupported": sum(
                case["grounding_status"] != "grounded" for case in subset
            ),
            "fact_type_misses": sum(not case["hit"] for case in subset),
        }
    return {
        "audit_kind": "published_fact_coverage_smoke",
        "generalization_claim": False,
        "audit_note": (
            "This audit checks published fact coverage with fixed probes only; "
            "it is not an intent-recognition or natural-language generalization benchmark."
        ),
        "database": str(database.resolve()),
        "published_exhibit_count": len(names),
        "published_fact_count": len(rows),
        "case_count": len(cases),
        "summary": summary,
        "failures": [case for case in cases if not case["hit"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run(args.database)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
