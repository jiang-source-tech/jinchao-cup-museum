from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

import yaml

from core.business_runtime_factory import create_conversation_runtime
from core.conversation_runtime import TurnRequest
from core.museum.content_import import (
    import_draft_content,
    load_content_package,
    parse_content_package,
    publish_revision,
    review_revision,
    withdraw_revision,
)
from core.museum.store import MuseumStore


CONTENT_DIR = Path(__file__).resolve().parents[1] / "content" / "museum"
CONTENT_PATHS = (
    CONTENT_DIR / "liangzhu-museum.yaml",
    CONTENT_DIR / "hangzhou-west-lake-museum.yaml",
    CONTENT_DIR / "china-national-silk-museum.yaml",
)
OCCURRED_AT = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _packages():
    return tuple(load_content_package(path) for path in CONTENT_PATHS)


def _import_packages(store: MuseumStore):
    packages = _packages()
    for package in packages:
        import_draft_content(store, package)
    return packages


def _publish_packages(store: MuseumStore):
    packages = _import_packages(store)
    for package in packages:
        for exhibit in package.exhibits:
            review_revision(
                store,
                revision_id=exhibit.revision.id,
                reviewed_by="official-content-reviewer",
                reviewed_at=OCCURRED_AT,
            )
            publish_revision(
                store,
                revision_id=exhibit.revision.id,
                published_by="official-content-publisher",
                published_at=OCCURRED_AT,
            )
    return packages


def _runtime(database_path: Path):
    return create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(database_path),
                "exhibit_context_mode": "explicit",
            }
        }
    )


def _request(
    *,
    text: str,
    request_id: str,
    device_id: str = "official-content-device",
    llm=None,
) -> TurnRequest:
    return TurnRequest(
        request_id=request_id,
        transport_session_id="official-content-transport",
        visitor_session_id=None,
        device_id=device_id,
        user_text=text,
        history=(),
        occurred_at=OCCURRED_AT,
        llm=llm,
    )


def test_official_hangzhou_content_is_source_backed_and_draft_by_default(tmp_path):
    packages = _packages()
    stores = {package.museum.id for package in packages}
    exhibits = [exhibit for package in packages for exhibit in package.exhibits]
    facts = [fact for exhibit in exhibits for fact in exhibit.revision.facts]
    sources = [source for package in packages for source in package.sources]

    assert stores == {
        "liangzhu-museum",
        "hangzhou-west-lake-museum",
        "china-national-silk-museum",
    }
    assert len(exhibits) == 5
    assert len(facts) == 26
    assert all(exhibit.revision.status == "draft" for exhibit in exhibits)
    assert all(fact.source_ids for fact in facts)
    assert {
        urlparse(source.locator).hostname for source in sources
    } == {
        "www.lzmuseum.cn",
        "www.westlakemuseum.com",
        "www.chinasilkmuseum.com",
    }

    store = MuseumStore(tmp_path / "museum.db")
    _import_packages(store)

    assert store.active_exhibits() == ()
    assert store.retrieve_evidence(
        exhibit_id="liangzhu-jade-trident",
        question="玉三叉形器是什么材质？",
        fact_types=("material",),
    ) is None


def test_retrieval_is_isolated_by_exhibit_and_published_revision(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    _publish_packages(store)
    cases = (
        (
            "liangzhu-jade-trident",
            "玉三叉形器是什么材质？",
            "liangzhu-jade-trident-r1",
            "fact-liangzhu-trident-material",
            "source-liangzhu-jade-trident-2019393530",
        ),
        (
            "southern-song-guan-zun-incense-burner",
            "南宋官窑青瓷樽式炉是什么材质？",
            "southern-song-guan-zun-incense-burner-r1",
            "fact-west-lake-zun-material",
            "source-west-lake-zun-incense-burner-850",
        ),
        (
            "qing-butterfly-medallion-robe-fabric",
            "团花蝴蝶纹袍料是什么材质？",
            "qing-butterfly-medallion-robe-fabric-r1",
            "fact-china-silk-butterfly-material",
            "source-china-silk-butterfly-robe-2639",
        ),
    )

    returned_fact_ids: set[str] = set()
    for exhibit_id, question, revision_id, fact_id, source_id in cases:
        evidence = store.retrieve_evidence(
            exhibit_id=exhibit_id,
            question=question,
            fact_types=("material",),
        )

        assert evidence is not None
        assert evidence.exhibit_id == exhibit_id
        assert evidence.content_revision_id == revision_id
        assert evidence.fact_ids == (fact_id,)
        assert evidence.source_ids == (source_id,)
        assert returned_fact_ids.isdisjoint(evidence.fact_ids)
        returned_fact_ids.update(evidence.fact_ids)


def test_runtime_switches_exhibits_without_reusing_previous_facts(tmp_path):
    database_path = tmp_path / "museum.db"
    store = MuseumStore(database_path)
    _publish_packages(store)
    runtime = _runtime(database_path)

    jade_answer = runtime.handle_turn(
        _request(
            text="玉三叉形器是什么材质？",
            request_id="official-jade-material",
        )
    )
    porcelain_answer = runtime.handle_turn(
        _request(
            text="换成南宋官窑青瓷樽式炉，它是什么材质？",
            request_id="official-porcelain-material",
        )
    )

    assert jade_answer.fact_ids == ("fact-liangzhu-trident-material",)
    assert "南瓜黄色玉器" in jade_answer.spoken_text
    assert porcelain_answer.fact_ids == ("fact-west-lake-zun-material",)
    assert "灰青釉" in porcelain_answer.spoken_text
    assert "南瓜黄色玉器" not in porcelain_answer.spoken_text
    assert porcelain_answer.display_state["context"]["exhibit_id"] == (
        "southern-song-guan-zun-incense-burner"
    )


def test_numeric_answer_keeps_exact_revision_fact_and_source_snapshot(tmp_path):
    database_path = tmp_path / "museum.db"
    store = MuseumStore(database_path)
    _publish_packages(store)
    runtime = _runtime(database_path)

    outcome = runtime.handle_turn(
        _request(
            text="玉三叉形器有多大？",
            request_id="official-trident-dimensions",
        )
    )
    trace = store.get_interaction_trace(outcome.audit_id)
    evidence = json.loads(trace["evidence_json"])

    assert outcome.knowledge_status == "grounded"
    assert outcome.fact_ids == ("fact-liangzhu-trident-dimensions",)
    assert "4.8厘米" in outcome.spoken_text
    assert "8.5厘米" in outcome.spoken_text
    assert evidence["content_revision_id"] == "liangzhu-jade-trident-r1"
    assert evidence["content_version"] == 1
    assert evidence["fact_ids"] == ["fact-liangzhu-trident-dimensions"]
    assert evidence["source_ids"] == [
        "source-liangzhu-jade-trident-2019393530"
    ]


def test_withdrawn_exhibit_is_resolvable_but_has_no_visible_facts(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    _publish_packages(store)

    withdraw_revision(
        store,
        revision_id="liangzhu-jade-trident-r1",
        withdrawn_by="official-content-operator",
        withdrawn_at=OCCURRED_AT,
        reason="验证撤回后的检索隔离",
    )

    active_ids = {row[0] for row in store.active_exhibits()}
    assert "liangzhu-jade-trident" in active_ids
    assert store.retrieve_evidence(
        exhibit_id="liangzhu-jade-trident",
        question="玉三叉形器是什么材质？",
        fact_types=("material",),
    ) is None
    assert store.retrieve_evidence(
        exhibit_id="southern-song-guan-zun-incense-burner",
        question="南宋官窑青瓷樽式炉是什么材质？",
        fact_types=("material",),
    ) is not None


def test_known_intent_without_fact_match_does_not_call_llm(tmp_path):
    class RecordingLLM:
        def __init__(self):
            self.calls = 0

        def response_no_stream(self, *_args, **_kwargs):
            self.calls += 1
            return json.dumps(
                {
                    "status": "unsupported",
                    "fact_ids": [],
                    "social_intent": "",
                    "answer": "",
                },
                ensure_ascii=False,
            )

    database_path = tmp_path / "museum.db"
    store = MuseumStore(database_path)
    _publish_packages(store)
    runtime = _runtime(database_path)
    llm = RecordingLLM()

    outcome = runtime.handle_turn(
        _request(
            text="玉三叉形器值多少钱？",
            request_id="official-trident-price",
            llm=llm,
        )
    )

    assert outcome.knowledge_status == "unsupported"
    assert outcome.fact_ids == ()
    assert llm.calls == 0


def test_legacy_fts_schema_is_rebuilt_with_exhibit_and_revision_boundaries(
    tmp_path,
):
    database_path = tmp_path / "museum.db"
    store = MuseumStore(database_path)
    packages = _publish_packages(store)
    fact_count = sum(
        len(exhibit.revision.facts)
        for package in packages
        for exhibit in package.exhibits
    )
    with store.connection() as connection:
        connection.execute("DROP TABLE exhibit_fact_fts")
        connection.execute(
            """
            CREATE VIRTUAL TABLE exhibit_fact_fts USING fts5(
                fact_id UNINDEXED,
                exhibit_name,
                aliases,
                fact_type,
                statement,
                keywords,
                tokenize = 'unicode61'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO exhibit_fact_fts(
                fact_id, exhibit_name, aliases, fact_type, statement, keywords
            ) VALUES ('stale-fact', '错误展品', '', 'material', '错误事实', '材质')
            """
        )

    migrated_store = MuseumStore(database_path)
    with migrated_store.connection() as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(exhibit_fact_fts)"
            ).fetchall()
        }
        rebuilt_count = connection.execute(
            "SELECT COUNT(*) AS count FROM exhibit_fact_fts"
        ).fetchone()["count"]

    assert {"exhibit_id", "revision_id"}.issubset(columns)
    assert rebuilt_count == fact_count
    evidence = migrated_store.retrieve_evidence(
        exhibit_id="liangzhu-jade-trident",
        question="玉三叉形器是什么材质？",
        fact_types=("material",),
    )
    assert evidence is not None
    assert evidence.fact_ids == ("fact-liangzhu-trident-material",)


def test_new_revision_cannot_return_old_revision_fact_or_fts_match(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    west_lake_package = load_content_package(
        CONTENT_DIR / "hangzhou-west-lake-museum.yaml"
    )
    import_draft_content(store, west_lake_package)
    for exhibit in west_lake_package.exhibits:
        review_revision(
            store,
            revision_id=exhibit.revision.id,
            reviewed_by="official-content-reviewer",
            reviewed_at=OCCURRED_AT,
        )
        publish_revision(
            store,
            revision_id=exhibit.revision.id,
            published_by="official-content-publisher",
            published_at=OCCURRED_AT,
        )

    payload = yaml.safe_load(
        (CONTENT_DIR / "hangzhou-west-lake-museum.yaml").read_text(
            encoding="utf-8"
        )
    )
    payload["sources"] = [payload["sources"][0]]
    payload["exhibits"] = [payload["exhibits"][0]]
    payload["exhibits"][0]["revision"] = {
        "id": "southern-song-guan-zun-incense-burner-r2",
        "number": 2,
        "status": "draft",
        "facts": [
            {
                "id": "fact-west-lake-zun-r2-material",
                "type": "material",
                "statement": "第二版馆方摘要继续确认器物为浅灰胎并施灰青釉。",
                "keywords": ["材质", "浅灰胎", "灰青釉"],
                "confidence": "official_museum_webpage",
                "sources": ["source-west-lake-zun-incense-burner-850"],
            }
        ],
    }
    revision_two = parse_content_package(payload)
    import_draft_content(store, revision_two)
    review_revision(
        store,
        revision_id="southern-song-guan-zun-incense-burner-r2",
        reviewed_by="official-content-reviewer",
        reviewed_at=OCCURRED_AT,
    )
    publish_revision(
        store,
        revision_id="southern-song-guan-zun-incense-burner-r2",
        published_by="official-content-publisher",
        published_at=OCCURRED_AT,
    )

    old_dimension = store.retrieve_evidence(
        exhibit_id="southern-song-guan-zun-incense-burner",
        question="这件樽式炉尺寸多大？",
        fact_types=("dimensions",),
    )
    current_material = store.retrieve_evidence(
        exhibit_id="southern-song-guan-zun-incense-burner",
        question="这件樽式炉是什么材质？",
        fact_types=("material",),
    )

    assert old_dimension is None
    assert current_material is not None
    assert current_material.content_revision_id == (
        "southern-song-guan-zun-incense-burner-r2"
    )
    assert current_material.fact_ids == ("fact-west-lake-zun-r2-material",)
    with store.connection() as connection:
        old_fts_rows = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM exhibit_fact_fts
            WHERE exhibit_id = ? AND revision_id = ?
            """,
            (
                "southern-song-guan-zun-incense-burner",
                "southern-song-guan-zun-incense-burner-r1",
            ),
        ).fetchone()["count"]
    assert old_fts_rows > 0
