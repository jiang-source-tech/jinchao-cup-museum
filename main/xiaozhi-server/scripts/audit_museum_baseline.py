from __future__ import annotations

"""Audit the museum content/index baseline without mutating the database."""

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from core.museum.knowledge_release import build_knowledge_release_manifest
from core.museum.store import MuseumStore


LEGACY_TERMS = ("小芯", "小智", "学生", "课程", "待办", "陪伴宠物")


def audit(database: Path, *, collection: str, model: str, dimension: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "database": str(database),
        "database_exists": database.exists(),
        "legacy_terms": [],
    }
    if not database.exists():
        result["ok"] = False
        return result

    with sqlite3.connect(database) as connection:
        result["integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {}
        for table in ("museum", "zone", "exhibit", "source_document", "content_revision", "exhibit_fact"):
            counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        result["counts"] = counts
        for table, columns in {
            "museum": ("id", "name"),
            "zone": ("id", "name"),
            "exhibit": ("id", "name", "aliases_json"),
            "source_document": ("id", "title", "rights_note", "locator"),
            "exhibit_fact": ("id", "statement"),
        }.items():
            rows = connection.execute(
                f"SELECT {', '.join(columns)} FROM {table}"
            ).fetchall()
            for row in rows:
                text = " ".join(str(value) for value in row)
                for term in LEGACY_TERMS:
                    if term in text and term not in result["legacy_terms"]:
                        result["legacy_terms"].append(term)

    records = MuseumStore(database, read_only=True).published_fact_index_records()
    result["published_fact_count"] = len(records)
    result["manifest"] = build_knowledge_release_manifest(
        records,
        embedding_model=model,
        embedding_dimension=dimension,
        collection=collection,
    )
    result["ok"] = (
        result["integrity"] == "ok"
        and not result["legacy_terms"]
        and result["published_fact_count"] <= result["counts"]["exhibit_fact"]
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="审计金潮杯博物馆内容与索引基线")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--collection", default="museum_facts_v1")
    parser.add_argument("--model", default="text-embedding-v4")
    parser.add_argument("--dimension", type=int, default=1024)
    args = parser.parse_args()
    payload = audit(
        args.database,
        collection=args.collection,
        model=args.model,
        dimension=args.dimension,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
