from __future__ import annotations

import argparse
import json

from .store import CompanionStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run validated operator repairs for Xiaoxin companion memory."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    repair = subparsers.add_parser(
        "supersede-misclassified-evidence",
        help="Supersede one polluted fact with an existing active fact.",
    )
    repair.add_argument("--db", required=True)
    repair.add_argument("--owner-user-id", required=True)
    repair.add_argument("--pet-id", required=True)
    repair.add_argument("--memory-subject-id", required=True)
    repair.add_argument("--obsolete-evidence-id", required=True)
    repair.add_argument("--replacement-evidence-id", required=True)
    repair.add_argument("--obsolete-fact-key", required=True)
    repair.add_argument("--replacement-fact-key", required=True)
    repair.add_argument("--now", required=True)
    args = parser.parse_args(argv)

    store = CompanionStore(args.db)
    result = store.repair_misclassified_semantic_evidence(
        owner_user_id=args.owner_user_id,
        pet_id=args.pet_id,
        memory_subject_id=args.memory_subject_id,
        obsolete_evidence_id=args.obsolete_evidence_id,
        replacement_evidence_id=args.replacement_evidence_id,
        obsolete_fact_key=args.obsolete_fact_key,
        replacement_fact_key=args.replacement_fact_key,
        now=args.now,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
