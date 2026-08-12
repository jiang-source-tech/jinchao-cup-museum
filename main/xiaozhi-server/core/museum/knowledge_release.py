from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


KNOWLEDGE_RELEASE_SCHEMA_VERSION = 1
INDEX_VERSION = "facts-v1"


def build_index_text(record: Mapping[str, Any]) -> str:
    aliases = "、".join(str(value) for value in record.get("aliases", ()))
    keywords = "、".join(str(value) for value in record.get("keywords", ()))
    return "\n".join(
        (
            f"展品：{record['exhibit_name']}",
            f"别名：{aliases}",
            f"事实类型：{record['fact_type']}",
            f"事实：{record['statement']}",
            f"关键词：{keywords}",
        )
    )


def index_content_hash(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(build_index_text(record).encode("utf-8")).hexdigest()


def prepare_index_records(
    records: Iterable[Mapping[str, Any]],
    *,
    embedding_model: str,
    embedding_dimension: int,
) -> tuple[dict[str, Any], ...]:
    prepared = []
    for source_record in records:
        record = dict(source_record)
        record.update(
            {
                "embedding_model": embedding_model,
                "embedding_dimension": embedding_dimension,
                "index_version": INDEX_VERSION,
                "content_hash": index_content_hash(record),
            }
        )
        prepared.append(record)
    return tuple(prepared)


def build_knowledge_release_manifest(
    records: Iterable[Mapping[str, Any]],
    *,
    embedding_model: str,
    embedding_dimension: int,
    collection: str,
) -> dict[str, Any]:
    prepared = prepare_index_records(
        records,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
    )
    ordered = sorted(prepared, key=lambda record: str(record["fact_id"]))
    fact_ids = [str(record["fact_id"]) for record in ordered]
    if len(fact_ids) != len(set(fact_ids)):
        raise ValueError("published fact IDs must be unique")

    facts = [
        {
            "fact_id": str(record["fact_id"]),
            "exhibit_id": str(record["exhibit_id"]),
            "revision_id": str(record["revision_id"]),
            "fact_type": str(record["fact_type"]),
            "source_ids": sorted(
                {str(source_id) for source_id in record.get("source_ids", ())}
            ),
            "content_hash": str(record["content_hash"]),
        }
        for record in ordered
    ]
    content_set_hash = _sha256_json(facts)
    release_basis = {
        "schema_version": KNOWLEDGE_RELEASE_SCHEMA_VERSION,
        "content_set_hash": content_set_hash,
        "embedding_model": embedding_model,
        "embedding_dimension": embedding_dimension,
        "collection": collection,
        "index_version": INDEX_VERSION,
    }
    release_hash = _sha256_json(release_basis)
    return {
        "schema_version": KNOWLEDGE_RELEASE_SCHEMA_VERSION,
        "release_id": f"kr-{release_hash[:24]}",
        "content_set_hash": content_set_hash,
        "embedding_model": embedding_model,
        "embedding_dimension": embedding_dimension,
        "collection": collection,
        "index_version": INDEX_VERSION,
        "published_fact_count": len(facts),
        "fact_ids": fact_ids,
        "revision_ids": sorted({fact["revision_id"] for fact in facts}),
        "source_ids": sorted(
            {
                source_id
                for fact in facts
                for source_id in fact["source_ids"]
            }
        ),
        "facts": facts,
    }


def verify_knowledge_release_payloads(
    manifest: Mapping[str, Any],
    payloads: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    expected = {
        str(fact["fact_id"]): fact
        for fact in manifest.get("facts", ())
    }
    actual: dict[str, Mapping[str, Any]] = {}
    duplicate_fact_ids: list[str] = []
    invalid_payload_count = 0
    for payload in payloads:
        fact_id = str(payload.get("fact_id", ""))
        if not fact_id:
            invalid_payload_count += 1
            continue
        if fact_id in actual:
            duplicate_fact_ids.append(fact_id)
            continue
        actual[fact_id] = payload

    missing_fact_ids = sorted(set(expected) - set(actual))
    unexpected_fact_ids = sorted(set(actual) - set(expected))
    mismatches: list[dict[str, Any]] = []
    expected_model = str(manifest["embedding_model"])
    expected_dimension = int(manifest["embedding_dimension"])
    expected_index_version = str(manifest["index_version"])
    for fact_id in sorted(set(expected) & set(actual)):
        payload = actual[fact_id]
        expected_values = {
            "exhibit_id": str(expected[fact_id]["exhibit_id"]),
            "revision_id": str(expected[fact_id]["revision_id"]),
            "fact_type": str(expected[fact_id]["fact_type"]),
            "source_ids": sorted(expected[fact_id]["source_ids"]),
            "embedding_model": expected_model,
            "embedding_dimension": expected_dimension,
            "index_version": expected_index_version,
            "content_hash": str(expected[fact_id]["content_hash"]),
        }
        for field, expected_value in expected_values.items():
            actual_value = payload.get(field)
            if field == "source_ids" and isinstance(actual_value, (list, tuple)):
                actual_value = sorted(str(value) for value in actual_value)
            if actual_value != expected_value:
                mismatches.append(
                    {
                        "fact_id": fact_id,
                        "field": field,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )

    ok = not any(
        (
            missing_fact_ids,
            unexpected_fact_ids,
            duplicate_fact_ids,
            invalid_payload_count,
            mismatches,
        )
    )
    return {
        "ok": ok,
        "expected_point_count": len(expected),
        "actual_point_count": (
            len(actual) + len(duplicate_fact_ids) + invalid_payload_count
        ),
        "missing_fact_ids": missing_fact_ids,
        "unexpected_fact_ids": unexpected_fact_ids,
        "duplicate_fact_ids": sorted(duplicate_fact_ids),
        "invalid_payload_count": invalid_payload_count,
        "mismatches": mismatches,
    }


def _sha256_json(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
