import sqlite3

from core.museum.knowledge_release import prepare_index_records
from core.museum.readiness import check_museum_readiness
from core.museum.store import MuseumStore


def _config(database_path, mode="hybrid"):
    return {
        "business_runtime": {
            "database_path": str(database_path),
            "retrieval": {
                "mode": mode,
                "embedding_model": "text-embedding-v4",
                "embedding_dimension": 1024,
                "qdrant_collection": "museum_facts_v1",
                "qdrant_url": "http://qdrant.test:6333",
            },
        }
    }


def test_readiness_requires_the_hybrid_index_to_match_published_facts(tmp_path):
    database_path = tmp_path / "museum.db"
    store = MuseumStore(database_path)
    store.seed_demo_content()
    records = prepare_index_records(
        store.published_fact_index_records(),
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
    )

    class MatchingIndex:
        def __init__(self, **_kwargs):
            pass

        def all_payloads(self):
            return records

    ready = check_museum_readiness(
        _config(database_path),
        server_root=tmp_path,
        index_factory=MatchingIndex,
    )
    assert ready["ready"] is True

    class StaleIndex(MatchingIndex):
        def all_payloads(self):
            return records[:-1]

    stale = check_museum_readiness(
        _config(database_path),
        server_root=tmp_path,
        index_factory=StaleIndex,
    )
    assert stale["ready"] is False
    assert any(
        check["name"] == "qdrant_release_matches" and not check["ok"]
        for check in stale["checks"]
    )


def test_readiness_does_not_initialize_an_incomplete_database(tmp_path):
    database_path = tmp_path / "empty.db"
    sqlite3.connect(database_path).close()

    result = check_museum_readiness(
        _config(database_path, mode="rules"),
        server_root=tmp_path,
    )

    assert result["ready"] is False
    with sqlite3.connect(database_path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    assert tables == []
