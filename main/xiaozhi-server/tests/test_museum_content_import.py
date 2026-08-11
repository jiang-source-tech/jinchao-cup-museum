from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.business_runtime_factory import create_conversation_runtime
from core.conversation_runtime import TurnRequest
from core.museum.content_import import (
    ContentPackageValidationError,
    audit_interaction_evidence,
    import_draft_content,
    load_content_package,
    parse_content_package,
    publish_revision,
    review_revision,
    rollback_revision,
    show_exhibit_versions,
    withdraw_revision,
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


def _second_revision_payload(first_payload: dict) -> dict:
    payload = json.loads(json.dumps(first_payload, ensure_ascii=False))
    revision = payload["exhibits"][0]["revision"]
    revision["id"] = "json-fixture-exhibit-r2"
    revision["number"] = 2
    revision["facts"][0].update(
        {
            "id": "json-fixture-fact-r2",
            "statement": "这是发布后的第二版材质事实。",
        }
    )
    return payload


def _turn_request(*, request_id: str, device_id: str) -> TurnRequest:
    return TurnRequest(
        request_id=request_id,
        transport_session_id=f"transport-{request_id}",
        visitor_session_id=None,
        device_id=device_id,
        user_text="JSON 测试展品是什么材质？",
        history=(),
        occurred_at=datetime.now().astimezone(),
        llm=None,
    )


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


def test_cli_reviews_publishes_and_shows_version_history(tmp_path, capsys):
    database_path = tmp_path / "museum.db"
    store = MuseumStore(database_path)
    import_draft_content(store, parse_content_package(_minimal_content_payload()))

    review_exit = content_import_main(
        [
            "review",
            "--database",
            str(database_path),
            "--revision-id",
            "json-fixture-exhibit-r1",
            "--actor",
            "fixture-reviewer",
            "--occurred-at",
            "2026-08-11T12:00:00+00:00",
            "--json",
        ]
    )
    review_payload = json.loads(capsys.readouterr().out)
    publish_exit = content_import_main(
        [
            "publish",
            "--database",
            str(database_path),
            "--revision-id",
            "json-fixture-exhibit-r1",
            "--actor",
            "fixture-publisher",
            "--occurred-at",
            "2026-08-11T13:00:00+00:00",
            "--json",
        ]
    )
    publish_payload = json.loads(capsys.readouterr().out)
    show_exit = content_import_main(
        [
            "show",
            "--database",
            str(database_path),
            "--exhibit-id",
            "json-fixture-exhibit",
            "--json",
        ]
    )
    show_payload = json.loads(capsys.readouterr().out)

    assert review_exit == publish_exit == show_exit == 0
    assert review_payload["status"] == "reviewed"
    assert publish_payload["status"] == "published"
    assert show_payload["current_published_revision_id"] == (
        "json-fixture-exhibit-r1"
    )
    assert [event["action"] for event in show_payload["events"]] == [
        "review",
        "publish",
    ]


def test_cli_withdraws_rolls_back_and_audits_historical_request(tmp_path, capsys):
    database_path = tmp_path / "museum.db"
    store = MuseumStore(database_path)
    import_draft_content(store, parse_content_package(_minimal_content_payload()))
    occurred_at = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        reviewed_by="fixture-reviewer",
        reviewed_at=occurred_at,
    )
    publish_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        published_by="fixture-publisher",
        published_at=occurred_at,
    )
    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(database_path),
                "exhibit_context_mode": "explicit",
            }
        }
    )
    runtime.handle_turn(
        _turn_request(request_id="cli-historical-request", device_id="cli-device")
    )

    withdraw_exit = content_import_main(
        [
            "withdraw",
            "--database",
            str(database_path),
            "--revision-id",
            "json-fixture-exhibit-r1",
            "--actor",
            "fixture-operator",
            "--reason",
            "临时撤回测试",
            "--json",
        ]
    )
    withdraw_payload = json.loads(capsys.readouterr().out)
    rollback_exit = content_import_main(
        [
            "rollback",
            "--database",
            str(database_path),
            "--revision-id",
            "json-fixture-exhibit-r1",
            "--actor",
            "fixture-operator",
            "--reason",
            "恢复已确认版本",
            "--json",
        ]
    )
    rollback_payload = json.loads(capsys.readouterr().out)
    audit_exit = content_import_main(
        [
            "audit",
            "--database",
            str(database_path),
            "--request-id",
            "cli-historical-request",
            "--json",
        ]
    )
    audit_payload = json.loads(capsys.readouterr().out)

    assert withdraw_exit == rollback_exit == audit_exit == 0
    assert withdraw_payload["status"] == "withdrawn"
    assert rollback_payload["status"] == "published"
    assert audit_payload["content_revision_id"] == "json-fixture-exhibit-r1"
    assert audit_payload["facts"][0]["fact_id"] == "json-fixture-fact"


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


def test_reviewed_revision_can_be_published_and_becomes_visible(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    package = parse_content_package(_minimal_content_payload())
    import_draft_content(store, package)
    occurred_at = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)

    reviewed = review_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        reviewed_by="fixture-reviewer",
        reviewed_at=occurred_at,
    )
    published = publish_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        published_by="fixture-publisher",
        published_at=occurred_at,
    )

    evidence = store.published_evidence("json-fixture-exhibit")
    assert reviewed.status == "reviewed"
    assert published.status == "published"
    assert evidence is not None
    assert evidence.content_revision_id == "json-fixture-exhibit-r1"
    assert evidence.fact_ids == ("json-fixture-fact",)


def test_publish_rejects_source_less_facts_without_changing_revision(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    package = parse_content_package(_minimal_content_payload())
    import_draft_content(store, package)
    occurred_at = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        reviewed_by="fixture-reviewer",
        reviewed_at=occurred_at,
    )
    with store.connection() as connection:
        connection.execute(
            "DELETE FROM fact_source WHERE fact_id = 'json-fixture-fact'"
        )

    with pytest.raises(ContentPackageValidationError) as error:
        publish_revision(
            store,
            revision_id="json-fixture-exhibit-r1",
            published_by="fixture-publisher",
            published_at=occurred_at,
        )

    assert "事实 json-fixture-fact 缺少来源" in str(error.value)
    with store.connection() as connection:
        revision = connection.execute(
            "SELECT status FROM content_revision WHERE id = ?",
            ("json-fixture-exhibit-r1",),
        ).fetchone()
    assert revision["status"] == "reviewed"


def test_publish_rejects_reviewed_status_without_review_metadata(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    import_draft_content(store, parse_content_package(_minimal_content_payload()))
    with store.connection() as connection:
        connection.execute(
            """
            UPDATE content_revision
            SET status = 'reviewed', reviewed_by = NULL, reviewed_at = NULL
            WHERE id = 'json-fixture-exhibit-r1'
            """
        )

    with pytest.raises(ContentPackageValidationError) as error:
        publish_revision(
            store,
            revision_id="json-fixture-exhibit-r1",
            published_by="fixture-publisher",
            published_at=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
        )

    assert "缺少审核人" in str(error.value)
    assert "缺少审核时间" in str(error.value)
    assert store.published_evidence("json-fixture-exhibit") is None


def test_publish_rejects_alias_conflict_with_visible_exhibit(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    first_payload = _minimal_content_payload()
    first_payload["exhibits"][0]["aliases"] = ["共享测试别名"]
    second_payload = json.loads(json.dumps(first_payload, ensure_ascii=False))
    second_payload["exhibits"][0].update(
        {
            "id": "json-fixture-exhibit-two",
            "name": "JSON 测试展品二",
        }
    )
    second_payload["exhibits"][0]["revision"].update(
        {
            "id": "json-fixture-exhibit-two-r1",
            "number": 1,
        }
    )
    second_payload["exhibits"][0]["revision"]["facts"][0]["id"] = (
        "json-fixture-fact-two"
    )
    import_draft_content(store, parse_content_package(first_payload))
    import_draft_content(store, parse_content_package(second_payload))
    occurred_at = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        reviewed_by="fixture-reviewer",
        reviewed_at=occurred_at,
    )
    publish_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        published_by="fixture-publisher",
        published_at=occurred_at,
    )
    review_revision(
        store,
        revision_id="json-fixture-exhibit-two-r1",
        reviewed_by="fixture-reviewer",
        reviewed_at=occurred_at,
    )

    with pytest.raises(ContentPackageValidationError) as error:
        publish_revision(
            store,
            revision_id="json-fixture-exhibit-two-r1",
            published_by="fixture-publisher",
            published_at=occurred_at,
        )

    assert "共享测试别名" in str(error.value)
    assert "json-fixture-exhibit" in str(error.value)
    with store.connection() as connection:
        statuses = {
            row["id"]: row["status"]
            for row in connection.execute(
                "SELECT id, status FROM content_revision"
            )
        }
    assert statuses == {
        "json-fixture-exhibit-r1": "published",
        "json-fixture-exhibit-two-r1": "reviewed",
    }


def test_publishing_new_revision_atomically_withdraws_previous_version(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    first_payload = _minimal_content_payload()
    import_draft_content(store, parse_content_package(first_payload))
    first_time = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        reviewed_by="fixture-reviewer",
        reviewed_at=first_time,
    )
    publish_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        published_by="fixture-publisher",
        published_at=first_time,
    )

    second_payload = json.loads(json.dumps(first_payload, ensure_ascii=False))
    second_revision = second_payload["exhibits"][0]["revision"]
    second_revision["id"] = "json-fixture-exhibit-r2"
    second_revision["number"] = 2
    second_revision["facts"][0].update(
        {
            "id": "json-fixture-fact-r2",
            "statement": "这是发布后的第二版材质事实。",
        }
    )
    import_draft_content(store, parse_content_package(second_payload))
    second_time = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r2",
        reviewed_by="fixture-reviewer",
        reviewed_at=second_time,
    )

    result = publish_revision(
        store,
        revision_id="json-fixture-exhibit-r2",
        published_by="fixture-publisher",
        published_at=second_time,
    )

    with store.connection() as connection:
        statuses = {
            row["id"]: row["status"]
            for row in connection.execute(
                "SELECT id, status FROM content_revision"
            )
        }
    evidence = store.published_evidence("json-fixture-exhibit")
    history = show_exhibit_versions(store, exhibit_id="json-fixture-exhibit")
    assert result.previous_published_revision_id == "json-fixture-exhibit-r1"
    assert statuses == {
        "json-fixture-exhibit-r1": "withdrawn",
        "json-fixture-exhibit-r2": "published",
    }
    assert evidence is not None
    assert evidence.content_revision_id == "json-fixture-exhibit-r2"
    assert evidence.fact_ids == ("json-fixture-fact-r2",)
    assert history.revisions[1].added_fact_ids == ("json-fixture-fact-r2",)
    assert history.revisions[1].removed_fact_ids == ("json-fixture-fact",)


def test_failed_publish_keeps_previous_version_and_events_unchanged(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    first_payload = _minimal_content_payload()
    import_draft_content(store, parse_content_package(first_payload))
    first_time = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        reviewed_by="fixture-reviewer",
        reviewed_at=first_time,
    )
    publish_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        published_by="fixture-publisher",
        published_at=first_time,
    )
    import_draft_content(
        store,
        parse_content_package(_second_revision_payload(first_payload)),
    )
    second_time = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r2",
        reviewed_by="fixture-reviewer",
        reviewed_at=second_time,
    )
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_second_revision_publish
            BEFORE UPDATE OF status ON content_revision
            WHEN NEW.id = 'json-fixture-exhibit-r2'
             AND NEW.status = 'published'
            BEGIN
                SELECT RAISE(ABORT, 'forced publish failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced publish failure"):
        publish_revision(
            store,
            revision_id="json-fixture-exhibit-r2",
            published_by="fixture-publisher",
            published_at=second_time,
        )

    history = show_exhibit_versions(store, exhibit_id="json-fixture-exhibit")
    assert history.current_published_revision_id == "json-fixture-exhibit-r1"
    assert {revision.revision_id: revision.status for revision in history.revisions} == {
        "json-fixture-exhibit-r1": "published",
        "json-fixture-exhibit-r2": "reviewed",
    }
    assert [event.action for event in history.events] == [
        "review",
        "publish",
        "review",
    ]


def test_new_session_uses_new_version_while_old_request_keeps_old_evidence(
    tmp_path,
):
    database_path = tmp_path / "museum.db"
    store = MuseumStore(database_path)
    first_payload = _minimal_content_payload()
    import_draft_content(store, parse_content_package(first_payload))
    first_time = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        reviewed_by="fixture-reviewer",
        reviewed_at=first_time,
    )
    publish_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        published_by="fixture-publisher",
        published_at=first_time,
    )
    runtime = create_conversation_runtime(
        {
            "business_runtime": {
                "type": "museum",
                "database_path": str(database_path),
                "exhibit_context_mode": "explicit",
            }
        }
    )
    first_answer = runtime.handle_turn(
        _turn_request(request_id="content-v1-answer", device_id="visitor-one")
    )

    import_draft_content(
        store,
        parse_content_package(_second_revision_payload(first_payload)),
    )
    second_time = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r2",
        reviewed_by="fixture-reviewer",
        reviewed_at=second_time,
    )
    publish_revision(
        store,
        revision_id="json-fixture-exhibit-r2",
        published_by="fixture-publisher",
        published_at=second_time,
    )
    second_answer = runtime.handle_turn(
        _turn_request(request_id="content-v2-answer", device_id="visitor-two")
    )
    historical = audit_interaction_evidence(
        store,
        request_id="content-v1-answer",
    )

    assert first_answer.knowledge_status == "grounded"
    assert "JSON 导入测试事实" in first_answer.spoken_text
    assert second_answer.knowledge_status == "grounded"
    assert "第二版材质事实" in second_answer.spoken_text
    assert "JSON 导入测试事实" not in second_answer.spoken_text
    assert historical.content_revision_id == "json-fixture-exhibit-r1"
    assert historical.facts[0].statement == "这是一条 JSON 导入测试事实。"


def test_rollback_restores_withdrawn_revision_and_withdraws_current(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    first_payload = _minimal_content_payload()
    import_draft_content(store, parse_content_package(first_payload))
    first_time = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        reviewed_by="fixture-reviewer",
        reviewed_at=first_time,
    )
    publish_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        published_by="fixture-publisher",
        published_at=first_time,
    )
    second_payload = json.loads(json.dumps(first_payload, ensure_ascii=False))
    second_revision = second_payload["exhibits"][0]["revision"]
    second_revision["id"] = "json-fixture-exhibit-r2"
    second_revision["number"] = 2
    second_revision["facts"][0].update(
        {
            "id": "json-fixture-fact-r2",
            "statement": "这是发布后的第二版材质事实。",
        }
    )
    import_draft_content(store, parse_content_package(second_payload))
    second_time = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r2",
        reviewed_by="fixture-reviewer",
        reviewed_at=second_time,
    )
    publish_revision(
        store,
        revision_id="json-fixture-exhibit-r2",
        published_by="fixture-publisher",
        published_at=second_time,
    )

    result = rollback_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        rolled_back_by="fixture-operator",
        rolled_back_at=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc),
        reason="第二版内容需要修正",
    )

    with store.connection() as connection:
        statuses = {
            row["id"]: row["status"]
            for row in connection.execute(
                "SELECT id, status FROM content_revision"
            )
        }
    evidence = store.published_evidence("json-fixture-exhibit")
    history = show_exhibit_versions(store, exhibit_id="json-fixture-exhibit")
    assert result.previous_published_revision_id == "json-fixture-exhibit-r2"
    assert statuses == {
        "json-fixture-exhibit-r1": "published",
        "json-fixture-exhibit-r2": "withdrawn",
    }
    assert evidence is not None
    assert evidence.content_revision_id == "json-fixture-exhibit-r1"
    assert evidence.fact_ids == ("json-fixture-fact",)
    assert [event.action for event in history.events] == [
        "review",
        "publish",
        "review",
        "supersede",
        "publish",
        "supersede",
        "rollback",
    ]


def test_version_history_records_review_publish_and_withdraw_events(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    import_draft_content(store, parse_content_package(_minimal_content_payload()))
    review_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        reviewed_by="fixture-reviewer",
        reviewed_at=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
    )
    publish_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        published_by="fixture-publisher",
        published_at=datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc),
    )
    withdraw_revision(
        store,
        revision_id="json-fixture-exhibit-r1",
        withdrawn_by="fixture-operator",
        withdrawn_at=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc),
        reason="来源需要重新确认",
    )

    history = show_exhibit_versions(store, exhibit_id="json-fixture-exhibit")

    assert history.current_published_revision_id is None
    assert len(history.revisions) == 1
    assert history.revisions[0].status == "withdrawn"
    assert history.revisions[0].fact_count == 1
    assert history.revisions[0].source_count == 1
    assert [event.action for event in history.events] == [
        "review",
        "publish",
        "withdraw",
    ]
    assert history.events[-1].actor == "fixture-operator"
    assert history.events[-1].reason == "来源需要重新确认"


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
