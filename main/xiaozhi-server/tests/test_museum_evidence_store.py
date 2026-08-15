from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.museum.evidence_store import EvidenceStore
from core.museum.source_ingestion import (
    SourceManifestError,
    ingest_source_manifest,
    load_source_manifest,
    parse_source_file,
)
from core.museum.store import DEMO_EXHIBIT_ID, DEMO_MUSEUM_ID, MuseumStore


def _write_manifest(root: Path) -> Path:
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "cup.md").write_text(
        """# 材质\n\n这件演示展品由天然水晶制成。\n\n# 研究限制\n\n部分制作细节仍需进一步研究。\n""",
        encoding="utf-8",
    )
    (root / "sources" / "cup.html").write_text(
        """<html><body><h1>展品说明</h1><p>这件展品用于验证原文片段摄取。</p></body></html>""",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "dataset_id": "museum-demo-pilot-001",
        "museum": {"id": DEMO_MUSEUM_ID, "name": "杭州博物馆（演示数据）"},
        "sources": [
            {
                "id": "pilot-cup-markdown",
                "title": "演示展品 Markdown 资料",
                "source_type": "markdown",
                "path": "sources/cup.md",
                "rights_note": "自动化测试夹具。",
                "source_level": "demo_curated",
                "exhibit_ids": [DEMO_EXHIBIT_ID],
            },
            {
                "id": "pilot-cup-html",
                "title": "演示展品 HTML 资料",
                "source_type": "html",
                "path": "sources/cup.html",
                "rights_note": "自动化测试夹具。",
                "source_level": "synthetic_demo",
                "exhibit_ids": [DEMO_EXHIBIT_ID],
            },
        ],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest_path


def test_ingestion_persists_locatable_segments_and_is_idempotent(tmp_path: Path):
    manifest_path = _write_manifest(tmp_path)
    database = tmp_path / "museum.db"
    store = MuseumStore(database)
    store.seed_demo_content()

    first = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="pilot-run-001",
    )
    assert first.ok
    assert len(first.source_ids) == 2
    assert len(first.segment_ids) >= 3

    evidence_store = EvidenceStore(store)
    segments = evidence_store.hydrate_segments(first.segment_ids)
    assert len(segments) == len(first.segment_ids)
    assert all(segment.source_id for segment in segments)
    assert all(segment.locator for segment in segments)
    assert all(DEMO_EXHIBIT_ID in segment.exhibit_ids for segment in segments)

    candidates = evidence_store.lexical_segment_candidates(
        question="这件展品的材质是什么",
        exhibit_ids=(DEMO_EXHIBIT_ID,),
        limit=5,
    )
    assert candidates
    assert candidates[0].segment_id in first.segment_ids

    second = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="pilot-run-002",
    )
    assert second.ok
    assert second.segment_ids == first.segment_ids
    with store.connection() as connection:
        active_count = connection.execute(
            "SELECT COUNT(*) AS count FROM source_segment WHERE status = 'published'"
        ).fetchone()["count"]
        run_count = connection.execute(
            "SELECT COUNT(*) AS count FROM ingestion_run"
        ).fetchone()["count"]
    assert active_count == len(first.segment_ids)
    assert run_count == 2


def test_ingestion_versions_changed_sources_and_reactivates_a_prior_version(
    tmp_path: Path,
):
    manifest_path = _write_manifest(tmp_path)
    source_path = tmp_path / "sources" / "cup.md"
    original_text = source_path.read_text(encoding="utf-8")
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()

    first = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="version-run-001",
    )
    first_markdown_ids = tuple(
        segment_id
        for segment_id in first.segment_ids
        if segment_id.startswith("pilot-cup-markdown-")
    )

    source_path.write_text(
        original_text + "\n# 补充\n\n新增的演示资料片段。\n",
        encoding="utf-8",
    )
    second = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="version-run-002",
    )
    second_markdown_ids = tuple(
        segment_id
        for segment_id in second.segment_ids
        if segment_id.startswith("pilot-cup-markdown-")
    )

    assert second.ok
    assert set(first_markdown_ids).isdisjoint(second_markdown_ids)

    source_path.write_text(original_text, encoding="utf-8")
    rollback = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="version-run-003",
    )
    rollback_markdown_ids = tuple(
        segment_id
        for segment_id in rollback.segment_ids
        if segment_id.startswith("pilot-cup-markdown-")
    )

    assert rollback.ok
    assert rollback_markdown_ids == first_markdown_ids
    with store.connection() as connection:
        statuses = {
            str(row["id"]): str(row["status"])
            for row in connection.execute(
                "SELECT id, status FROM source_segment "
                "WHERE source_id = 'pilot-cup-markdown'"
            ).fetchall()
        }
    assert all(statuses[segment_id] == "published" for segment_id in first_markdown_ids)
    assert all(statuses[segment_id] == "withdrawn" for segment_id in second_markdown_ids)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][0]["locator"] = "archive/crystal-cup.md"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    metadata_update = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="version-run-004",
    )
    metadata_markdown_ids = tuple(
        segment_id
        for segment_id in metadata_update.segment_ids
        if segment_id.startswith("pilot-cup-markdown-")
    )
    metadata_segments = EvidenceStore(store).hydrate_segments(metadata_markdown_ids)

    assert metadata_update.ok
    assert set(metadata_markdown_ids).isdisjoint(first_markdown_ids)
    assert all(
        segment.locator.startswith("archive/crystal-cup.md")
        for segment in metadata_segments
    )
    with store.connection() as connection:
        version_rows = connection.execute(
            """
            SELECT id, version_no, locator, status
            FROM source_document_version
            WHERE source_id = 'pilot-cup-markdown'
            ORDER BY version_no
            """
        ).fetchall()
        source_row = connection.execute(
            """
            SELECT status, content_version, active_version_id
            FROM source_document
            WHERE id = 'pilot-cup-markdown'
            """
        ).fetchone()
    assert len(version_rows) == 3
    assert [int(row["version_no"]) for row in version_rows] == [1, 2, 3]
    assert [str(row["status"]) for row in version_rows] == [
        "withdrawn",
        "withdrawn",
        "published",
    ]
    assert str(source_row["status"]) == "published"
    assert int(source_row["content_version"]) == 3
    assert str(source_row["active_version_id"]) == (
        metadata_segments[0].source_version_id
    )

    evidence_store = EvidenceStore(store)
    evidence_store.withdraw_source(
        "pilot-cup-markdown",
        reason="automated withdrawal fixture",
    )
    assert evidence_store.hydrate_segments(metadata_markdown_ids) == ()
    historical = evidence_store.hydrate_segments(
        metadata_markdown_ids,
        include_withdrawn=True,
    )
    assert tuple(segment.id for segment in historical) == metadata_markdown_ids
    assert all(segment.status == "withdrawn" for segment in historical)
    with pytest.raises(ValueError, match="withdrawn source"):
        evidence_store.replace_source_segments(
            "pilot-cup-markdown",
            historical,
        )
    assert all(
        record["source_id"] != "pilot-cup-markdown"
        for record in evidence_store.published_segment_index_records()
    )

    reactivated = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="version-run-005",
    )
    assert reactivated.ok
    assert tuple(
        segment_id
        for segment_id in reactivated.segment_ids
        if segment_id.startswith("pilot-cup-markdown-")
    ) == metadata_markdown_ids


def test_failed_manifest_publish_does_not_leave_partial_sources(tmp_path: Path):
    manifest_path = _write_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][1]["exhibit_ids"] = ["missing-exhibit"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()

    report = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="atomic-run-001",
    )

    assert not report.ok
    with store.connection() as connection:
        source_count = connection.execute(
            "SELECT COUNT(*) AS count FROM source_document "
            "WHERE id IN ('pilot-cup-markdown', 'pilot-cup-html')"
        ).fetchone()["count"]
        segment_count = connection.execute(
            "SELECT COUNT(*) AS count FROM source_segment "
            "WHERE source_id IN ('pilot-cup-markdown', 'pilot-cup-html')"
        ).fetchone()["count"]
    assert source_count == 0
    assert segment_count == 0


def test_ingestion_rejects_cross_museum_exhibits_and_source_id_reassignment(
    tmp_path: Path,
):
    manifest_path = _write_manifest(tmp_path)
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()

    valid_manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    cross_museum_manifest = dict(valid_manifest_payload)
    cross_museum_manifest["museum"] = {
        "id": "other-museum",
        "name": "其他博物馆",
    }
    manifest_path.write_text(
        json.dumps(cross_museum_manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    cross_museum = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="cross-museum-run-001",
    )

    assert not cross_museum.ok
    assert "cross-museum exhibit" in cross_museum.errors[0]

    manifest_path.write_text(
        json.dumps(valid_manifest_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    first = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="source-owner-run-001",
    )
    assert first.ok

    reassignment_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    reassignment_manifest["museum"] = {
        "id": "other-museum",
        "name": "其他博物馆",
    }
    for source in reassignment_manifest["sources"]:
        source["exhibit_ids"] = []
    manifest_path.write_text(
        json.dumps(reassignment_manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    reassignment = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="source-owner-run-002",
    )

    assert not reassignment.ok
    assert "already belongs to another museum" in reassignment.errors[0]
    with store.connection() as connection:
        owners = {
            str(row["id"]): str(row["museum_id"])
            for row in connection.execute(
                "SELECT id, museum_id FROM source_document "
                "WHERE id IN ('pilot-cup-markdown', 'pilot-cup-html')"
            ).fetchall()
        }
    assert owners == {
        "pilot-cup-markdown": DEMO_MUSEUM_ID,
        "pilot-cup-html": DEMO_MUSEUM_ID,
    }


def test_ocr_sidecar_changes_create_a_new_source_version(tmp_path: Path):
    image_path = tmp_path / "label.png"
    image_path.write_bytes(b"demo-image-bytes")
    sidecar_path = tmp_path / "label.png.ocr.json"
    sidecar_path.write_text(
        json.dumps({"text": "展签文字：天然水晶。", "confidence": 0.8}, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "ocr-version-test",
                "museum": {"id": DEMO_MUSEUM_ID, "name": "杭州博物馆（演示数据）"},
                "sources": [
                    {
                        "id": "ocr-label",
                        "title": "OCR 展签测试",
                        "source_type": "image",
                        "path": "label.png",
                        "rights_note": "自动化测试夹具。",
                        "exhibit_ids": [DEMO_EXHIBIT_ID],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    first = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="ocr-version-001",
    )

    sidecar_path.write_text(
        json.dumps({"text": "展签文字：天然水晶。", "confidence": 0.9}, ensure_ascii=False),
        encoding="utf-8",
    )
    second = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="ocr-version-002",
    )

    assert first.ok and second.ok
    assert first.segment_ids != second.segment_ids
    segments = EvidenceStore(store).hydrate_segments(second.segment_ids)
    assert [segment.ocr_confidence for segment in segments] == [0.9]


def test_conflict_groups_include_supporting_and_conflicting_segments(tmp_path: Path):
    manifest_path = _write_manifest(tmp_path)
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    report = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="conflict-run-001",
    )
    supporting_segment, conflicting_segment = report.segment_ids[:2]

    with store.connection() as connection:
        connection.execute(
            "INSERT INTO knowledge_claim_support(fact_id, segment_id) VALUES (?, ?)",
            ("fact-crystal-cup-material", supporting_segment),
        )
        connection.execute(
            """
            INSERT INTO knowledge_claim_conflict(fact_id, segment_id, reason)
            VALUES (?, ?, ?)
            """,
            (
                "fact-crystal-cup-material",
                conflicting_segment,
                "test fixture disagreement",
            ),
        )

    groups = EvidenceStore(store).conflict_groups((DEMO_EXHIBIT_ID,))

    assert any(
        set(group) == {supporting_segment, conflicting_segment}
        for group in groups
    )


def test_manifest_rejects_paths_outside_manifest_root(tmp_path: Path):
    manifest = {
        "schema_version": 1,
        "dataset_id": "invalid-path",
        "museum": {"id": "museum-demo", "name": "测试博物馆"},
        "sources": [
            {
                "id": "outside-source",
                "title": "越界文件",
                "source_type": "text",
                "path": "../outside.txt",
                "rights_note": "测试",
                "exhibit_ids": [],
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("不应被读取", encoding="utf-8")
    try:
        loaded = load_source_manifest(path)
        store = MuseumStore(tmp_path / "museum.db")
        report = ingest_source_manifest(path, store=store, run_id="invalid-path-001")
        assert not report.ok
        assert "资料路径必须位于 manifest 根目录内" in report.errors[0]
        assert loaded.dataset_id == "invalid-path"
    finally:
        outside.unlink(missing_ok=True)


def test_manifest_validation_reports_duplicate_ids(tmp_path: Path):
    manifest = {
        "schema_version": 1,
        "dataset_id": "duplicate-source",
        "museum": {"id": "museum-demo", "name": "测试博物馆"},
        "sources": [
            {
                "id": "duplicate",
                "title": "第一份",
                "source_type": "text",
                "path": "one.txt",
                "rights_note": "测试",
                "exhibit_ids": [],
            },
            {
                "id": "duplicate",
                "title": "第二份",
                "source_type": "text",
                "path": "two.txt",
                "rights_note": "测试",
                "exhibit_ids": [],
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SourceManifestError, match="重复"):
        load_source_manifest(path)


def test_manifest_rejects_source_from_a_different_museum(tmp_path: Path):
    manifest = {
        "schema_version": 1,
        "dataset_id": "museum-mismatch",
        "museum": {"id": "museum-a", "name": "测试博物馆 A"},
        "sources": [
            {
                "id": "foreign-source",
                "title": "跨馆来源",
                "source_type": "text",
                "path": "source.txt",
                "museum_id": "museum-b",
                "rights_note": "测试",
                "exhibit_ids": [],
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SourceManifestError, match="museum.id 一致"):
        load_source_manifest(path)


def test_pdf_parser_keeps_page_locator(tmp_path: Path):
    pdf = tmp_path / "source.pdf"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length 67 >>\nstream\nBT /F1 12 Tf 72 220 Td (Crystal cup material: natural quartz.) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, content in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode("ascii"))
        body.extend(content)
        body.extend(b"\nendobj\n")
    xref_offset = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    body.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    pdf.write_bytes(bytes(body))

    segments = parse_source_file(pdf, source_type="pdf")

    assert segments
    assert segments[0].page == 1
    assert "natural quartz" in segments[0].text
    assert segments[0].locator.endswith("#page=1")
