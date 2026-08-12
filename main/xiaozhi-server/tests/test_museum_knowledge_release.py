from __future__ import annotations

from core.museum.knowledge_release import (
    build_knowledge_release_manifest,
    prepare_index_records,
    verify_knowledge_release_payloads,
)
from core.museum.store import MuseumStore
from scripts.verify_museum_knowledge_release import verify_from_config


def _records(store: MuseumStore):
    return store.published_fact_index_records()


def _config(database_path) -> dict:
    return {
        "business_runtime": {
            "database_path": str(database_path),
            "retrieval": {
                "embedding_model": "text-embedding-v4",
                "embedding_dimension": 1024,
                "qdrant_collection": "museum_facts_v1",
            },
        }
    }


def test_manifest_is_deterministic_and_covers_published_content(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()

    manifest = build_knowledge_release_manifest(
        reversed(_records(store)),
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        collection="museum_facts_v1",
    )
    repeated = build_knowledge_release_manifest(
        _records(store),
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        collection="museum_facts_v1",
    )

    assert manifest == repeated
    assert manifest["release_id"].startswith("kr-")
    assert manifest["published_fact_count"] == len(_records(store))
    assert manifest["fact_ids"] == sorted(manifest["fact_ids"])
    assert set(manifest["revision_ids"])
    assert set(manifest["source_ids"])
    assert all(len(fact["content_hash"]) == 64 for fact in manifest["facts"])


def test_payload_verification_reports_missing_unexpected_and_stale_metadata(
    tmp_path,
):
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    records = prepare_index_records(
        _records(store),
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
    )
    manifest = build_knowledge_release_manifest(
        records,
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        collection="museum_facts_v1",
    )
    payloads = [dict(record) for record in records]
    missing = payloads.pop()
    payloads[0]["content_hash"] = "stale"
    payloads[1]["source_ids"] = ["stale-source"]
    payloads[2]["exhibit_id"] = "wrong-exhibit"
    payloads[3]["fact_type"] = "wrong-type"
    payloads.append(
        {
            "fact_id": "unexpected-fact",
            "embedding_model": "text-embedding-v4",
            "embedding_dimension": 1024,
            "index_version": "facts-v1",
            "content_hash": "unexpected",
        }
    )

    result = verify_knowledge_release_payloads(manifest, payloads)

    assert result["ok"] is False
    assert result["missing_fact_ids"] == [missing["fact_id"]]
    assert result["unexpected_fact_ids"] == ["unexpected-fact"]
    assert {
        (item["fact_id"], item["field"])
        for item in result["mismatches"]
    } == {
        (payloads[0]["fact_id"], "content_hash"),
        (payloads[1]["fact_id"], "source_ids"),
        (payloads[2]["fact_id"], "exhibit_id"),
        (payloads[3]["fact_id"], "fact_type"),
    }


def test_verify_cli_service_supports_manifest_only_and_mock_qdrant(tmp_path):
    database_path = tmp_path / "museum.db"
    store = MuseumStore(database_path)
    store.seed_demo_content()
    config = _config(database_path)

    manifest_only = verify_from_config(config)

    assert manifest_only["ok"] is True
    assert manifest_only["mode"] == "manifest_only"
    assert manifest_only["qdrant"]["checked"] is False

    class FakeIndex:
        def __init__(self, **_kwargs):
            self.payloads = prepare_index_records(
                _records(store),
                embedding_model="text-embedding-v4",
                embedding_dimension=1024,
            )

        def all_payloads(self):
            return self.payloads

    verified = verify_from_config(
        config,
        qdrant_url="http://qdrant.test:6333",
        index_factory=FakeIndex,
    )

    assert verified["ok"] is True
    assert verified["mode"] == "qdrant_verified"
    assert verified["qdrant"]["checked"] is True
    assert verified["qdrant"]["actual_point_count"] == len(_records(store))
