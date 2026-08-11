from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.museum.content_import import (
    ContentPackageValidationError,
    import_draft_content,
    load_content_package,
    parse_content_package,
)
from core.museum.store import MuseumStore
from scripts.import_museum_content import main as content_import_main


FIXTURES = Path(__file__).parent / "fixtures" / "museum_content"


def _minimal_content_payload() -> dict:
    return {
        "schema_version": 1,
        "museum": {
            "id": "json-fixture-museum",
            "name": "JSON 自动化测试博物馆",
            "status": "active",
        },
        "zones": [
            {
                "id": "json-fixture-gallery",
                "name": "JSON 自动化测试展区",
                "sort_order": 1,
            }
        ],
        "sources": [
            {
                "id": "json-fixture-source",
                "title": "JSON 测试资料",
                "source_type": "test_fixture",
                "locator": "fixture://json-exhibit",
                "rights_note": "自动化测试专用。",
            }
        ],
        "exhibits": [
            {
                "id": "json-fixture-exhibit",
                "zone_id": "json-fixture-gallery",
                "name": "JSON 测试展品",
                "aliases": ["JSON 测试别名"],
                "status": "active",
                "revision": {
                    "id": "json-fixture-exhibit-r1",
                    "number": 1,
                    "status": "draft",
                    "facts": [
                        {
                            "id": "json-fixture-fact",
                            "type": "material",
                            "statement": "这是一条 JSON 导入测试事实。",
                            "keywords": ["材质"],
                            "confidence": "test_fixture",
                            "sources": ["json-fixture-source"],
                        }
                    ],
                },
            }
        ],
    }


def test_loads_utf8_json_content_package(tmp_path):
    source_path = tmp_path / "content.json"
    source_path.write_text(
        json.dumps(_minimal_content_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    package = load_content_package(source_path)

    assert package.museum.id == "json-fixture-museum"
    assert package.exhibits[0].revision.facts[0].statement.endswith("测试事实。")


def test_non_utf8_content_package_returns_validation_error(tmp_path):
    source_path = tmp_path / "content.yaml"
    source_path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ContentPackageValidationError, match="UTF-8"):
        load_content_package(source_path)


def test_duplicate_fact_source_is_rejected_before_database_writes():
    payload = _minimal_content_payload()
    payload["exhibits"][0]["revision"]["facts"][0]["sources"] = [
        "json-fixture-source",
        "json-fixture-source",
    ]

    with pytest.raises(ContentPackageValidationError) as error:
        parse_content_package(payload)

    assert ".sources ID 重复：json-fixture-source" in str(error.value)


def test_cli_json_error_is_machine_readable(capsys):
    exit_code = content_import_main(
        [
            "validate",
            "--input",
            str(FIXTURES / "invalid-content.yaml"),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert exit_code == 2
    assert payload["status"] == "error"
    assert payload["error"] == "validation_error"
    assert len(payload["issues"]) == 3


def test_imports_valid_multi_exhibit_package_as_hidden_drafts(tmp_path):
    package = load_content_package(FIXTURES / "valid-content.yaml")
    store = MuseumStore(tmp_path / "museum.db")

    result = import_draft_content(store, package)

    assert result.exhibit_ids == (
        "fixture-bronze-bell",
        "fixture-painted-pottery",
        "fixture-jade-pendant",
    )
    assert result.revision_count == 3
    assert result.fact_count == 3
    with store.connection() as connection:
        statuses = connection.execute(
            "SELECT status FROM content_revision ORDER BY id"
        ).fetchall()
        fts_count = connection.execute(
            "SELECT COUNT(*) AS count FROM exhibit_fact_fts"
        ).fetchone()["count"]
    assert [row["status"] for row in statuses] == ["draft", "draft", "draft"]
    assert fts_count == 3
    assert store.active_exhibits() == ()
    assert store.published_evidence("fixture-bronze-bell") is None


def test_invalid_package_reports_all_content_contract_errors():
    with pytest.raises(ContentPackageValidationError) as error:
        load_content_package(FIXTURES / "invalid-content.yaml")

    message = str(error.value)
    assert "revision.status 必须是 draft" in message
    assert "引用了内容包中不存在的来源 missing-source" in message
    assert "别名或名称冲突" in message


def test_database_failure_rolls_back_the_entire_import(tmp_path):
    package = load_content_package(FIXTURES / "valid-content.yaml")
    store = MuseumStore(tmp_path / "museum.db")
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_fixture_jade
            BEFORE INSERT ON exhibit
            WHEN NEW.id = 'fixture-jade-pendant'
            BEGIN
                SELECT RAISE(ABORT, 'forced import failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced import failure"):
        import_draft_content(store, package)

    with store.connection() as connection:
        for table in (
            "museum",
            "zone",
            "source_document",
            "exhibit",
            "content_revision",
            "exhibit_fact",
            "fact_source",
            "exhibit_fact_fts",
        ):
            count = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            assert count == 0, table
