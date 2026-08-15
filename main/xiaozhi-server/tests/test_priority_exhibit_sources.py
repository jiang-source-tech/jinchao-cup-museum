from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from shutil import copyfile

from core.museum.answering import GroundedAnswerService
from core.museum.content_import import (
    import_draft_content,
    load_content_package,
    publish_revision,
    review_revision,
)
from core.museum.evidence_index import DenseEvidenceHit
from core.museum.evidence_retrieval import EvidenceSearchService
from core.museum.query_understanding import understand_question
from core.museum.source_ingestion import ingest_source_manifest, load_source_manifest
from core.museum.store import DEMO_EXHIBIT_ID, MuseumStore


SERVER_ROOT = Path(__file__).resolve().parents[1]
CONTENT_PACKAGES = (
    SERVER_ROOT / "content/museum/hangzhou-museum-crystal-cup.yaml",
    SERVER_ROOT / "content/museum/liangzhu-museum.yaml",
)
SOURCE_MANIFESTS = (
    SERVER_ROOT / "content/museum-sources/hangzhou-museum/manifest.yaml",
    SERVER_ROOT / "content/museum-sources/liangzhu-museum/manifest.yaml",
)
PRIORITY_EXHIBITS = {
    "warring-states-crystal-cup",
    "liangzhu-jade-yue-set",
    "liangzhu-jade-trident",
}


class _Embedder:
    model = "priority-source-test"
    dimension = 3

    def embed(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class _AllSegmentsIndex:
    collection_name = "priority-source-test"

    def __init__(self, segment_ids: tuple[str, ...]):
        self._segment_ids = segment_ids

    def search(self, **_kwargs):
        return tuple(
            DenseEvidenceHit(
                segment_id=segment_id,
                score=1.0 - index * 0.001,
                payload={"segment_id": segment_id},
            )
            for index, segment_id in enumerate(self._segment_ids)
        )


def _publish_priority_content(store: MuseumStore) -> None:
    occurred_at = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)
    for package_path in CONTENT_PACKAGES:
        package = load_content_package(package_path)
        import_draft_content(store, package)
        for exhibit in package.exhibits:
            review_revision(
                store,
                revision_id=exhibit.revision.id,
                reviewed_by="priority-source-test-reviewer",
                reviewed_at=occurred_at,
            )
            publish_revision(
                store,
                revision_id=exhibit.revision.id,
                published_by="priority-source-test-publisher",
                published_at=occurred_at,
            )


def test_priority_exhibits_have_five_dimension_claim_support_and_idempotent_sources(
    tmp_path: Path,
):
    research_question = understand_question("战国水晶杯还有哪些研究争议？")
    assert research_question.fine_intent == "research_limit"
    assert research_question.fact_types == ("research_limit",)

    for package_path, manifest_path in zip(
        CONTENT_PACKAGES, SOURCE_MANIFESTS, strict=True
    ):
        package = load_content_package(package_path)
        manifest = load_source_manifest(manifest_path)
        content_sources = {source.id: source for source in package.sources}
        assert set(content_sources) == {source.id for source in manifest.sources}
        for source in manifest.sources:
            content_source = content_sources[source.id]
            assert (
                content_source.title,
                content_source.source_type,
                content_source.locator,
                content_source.rights_note,
                content_source.publisher,
                content_source.published_date,
                content_source.accessed_at,
                content_source.language,
            ) == (
                source.title,
                source.source_type,
                source.locator,
                source.rights_note,
                source.publisher,
                source.published_date,
                source.accessed_at,
                source.language,
            )

    store = MuseumStore(tmp_path / "priority-sources.db")
    _publish_priority_content(store)

    first_reports = tuple(
        ingest_source_manifest(
            manifest,
            store=store,
            run_id=f"priority-first-{index}",
        )
        for index, manifest in enumerate(SOURCE_MANIFESTS, start=1)
    )
    second_reports = tuple(
        ingest_source_manifest(
            manifest,
            store=store,
            run_id=f"priority-second-{index}",
        )
        for index, manifest in enumerate(SOURCE_MANIFESTS, start=1)
    )

    assert all(report.ok for report in first_reports + second_reports)
    assert [report.segment_ids for report in first_reports] == [
        report.segment_ids for report in second_reports
    ]

    answer = GroundedAnswerService(
        store,
        evidence_search=EvidenceSearchService(
            store=store,
            embedder=_Embedder(),
            index=_AllSegmentsIndex(first_reports[0].segment_ids),
        ),
    ).answer(
        exhibit_id="warring-states-crystal-cup",
        exhibit_name="战国水晶杯",
        question="请详细介绍一下战国水晶杯",
        llm=None,
        query_id="priority-crystal-detailed",
    )
    assert answer.knowledge_status == "grounded"
    assert answer.retrieval_trace["answer_depth"] == "detailed"
    assert len(answer.spoken_text) >= 300
    assert "战国中晚期" in answer.spoken_text
    assert "1990年" in answer.spoken_text
    assert "取芯" in answer.spoken_text or "金刚砂" in answer.spoken_text
    assert "无定论" in answer.spoken_text or "不能确定" in answer.spoken_text

    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT cr.exhibit_id, f.id AS fact_id, f.fact_type, f.certainty,
                   COUNT(DISTINCT ss.id) AS support_count
            FROM content_revision cr
            JOIN exhibit_fact f ON f.revision_id = cr.id
            LEFT JOIN knowledge_claim_support kcs ON kcs.fact_id = f.id
            LEFT JOIN source_segment ss
              ON ss.id = kcs.segment_id AND ss.status = 'published'
            WHERE cr.status = 'published'
              AND cr.exhibit_id IN (?, ?, ?)
            GROUP BY cr.exhibit_id, f.id, f.fact_type, f.certainty
            """,
            tuple(sorted(PRIORITY_EXHIBITS)),
        ).fetchall()

    by_exhibit: dict[str, list] = {}
    for row in rows:
        by_exhibit.setdefault(str(row["exhibit_id"]), []).append(row)
        assert int(row["support_count"]) >= 1

    assert set(by_exhibit) == PRIORITY_EXHIBITS
    for exhibit_rows in by_exhibit.values():
        fact_types = {str(row["fact_type"]) for row in exhibit_rows}
        assert fact_types & {"era", "history"}
        assert {"excavation", "craft", "usage", "research_limit"} <= fact_types
        assert any(
            str(row["certainty"]) in {"qualified", "disputed", "unknown"}
            for row in exhibit_rows
        )

    source_manifest = load_source_manifest(SOURCE_MANIFESTS[0])
    manifest_without_claims = tmp_path / "hangzhou-without-claim-support.json"
    sources_without_claims = []
    for source in source_manifest.sources:
        payload = asdict(source)
        payload.pop("museum_id")
        source_path = SOURCE_MANIFESTS[0].parent / source.path
        copied_path = manifest_without_claims.parent / source.path
        copied_path.parent.mkdir(parents=True, exist_ok=True)
        copyfile(source_path, copied_path)
        if source.id == source_manifest.sources[0].id:
            copied_path.write_text(
                copied_path.read_text(encoding="utf-8")
                + "\n\n版本变更标记：验证旧声明绑定会被清理。\n",
                encoding="utf-8",
            )
        sources_without_claims.append(payload)
    manifest_without_claims.write_text(
        json.dumps(
            {
                "schema_version": source_manifest.schema_version,
                "dataset_id": "priority-hangzhou-without-claim-support",
                "museum": {
                    "id": source_manifest.museum_id,
                    "name": source_manifest.museum_name,
                },
                "sources": sources_without_claims,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cleared = ingest_source_manifest(
        manifest_without_claims,
        store=store,
        run_id="priority-clear-claim-support",
    )
    assert cleared.ok
    with store.connection() as connection:
        remaining = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM knowledge_claim_support kcs
            JOIN source_segment ss ON ss.id = kcs.segment_id
            WHERE ss.source_id IN (?, ?, ?)
            """,
            tuple(source.id for source in source_manifest.sources),
        ).fetchone()
    assert int(remaining["count"]) == 0


def test_invalid_claim_support_rolls_back_source_publication(tmp_path: Path):
    store = MuseumStore(tmp_path / "claim-support-rollback.db")
    store.seed_demo_content()
    dataset = tmp_path / "invalid-claim-support"
    dataset.mkdir()
    (dataset / "source.md").write_text(
        "# 材质\n\n这件展品由天然水晶制成。\n",
        encoding="utf-8",
    )
    manifest = dataset / "manifest.json"
    def write_manifest(*, fact_id: str, sections: list[str]) -> None:
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "dataset_id": "invalid-claim-support",
                    "museum": {
                        "id": "hangzhou-museum-demo",
                        "name": "杭州博物馆（演示数据）",
                    },
                    "sources": [
                        {
                            "id": "invalid-claim-source",
                            "title": "无效声明绑定测试资料",
                            "source_type": "markdown",
                            "path": "source.md",
                            "rights_note": "自动化测试夹具。",
                            "exhibit_ids": [DEMO_EXHIBIT_ID],
                        }
                    ],
                    "claim_support": [
                        {
                            "fact_id": fact_id,
                            "source_id": "invalid-claim-source",
                            "sections": sections,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    cases = (
        ("missing-fact-id", ["材质"], "unknown fact"),
        (
            "fact-crystal-cup-material",
            ["材质", "不存在的章节"],
            "缺少章节：不存在的章节",
        ),
        (
            "fact-crystal-cup-era",
            ["材质"],
            "claim support source is not declared by fact",
        ),
    )
    for index, (fact_id, sections, expected_error) in enumerate(cases, start=1):
        write_manifest(fact_id=fact_id, sections=sections)
        report = ingest_source_manifest(
            manifest,
            store=store,
            run_id=f"invalid-claim-support-run-{index}",
        )

        assert not report.ok
        assert expected_error in report.errors[0]
        with store.connection() as connection:
            assert connection.execute(
                "SELECT 1 FROM source_document WHERE id = 'invalid-claim-source'"
            ).fetchone() is None
