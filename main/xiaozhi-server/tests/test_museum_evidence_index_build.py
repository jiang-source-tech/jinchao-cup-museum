from __future__ import annotations

import json
from pathlib import Path

from qdrant_client import QdrantClient

from core.museum.embedding import EmbeddingBatchResult
from core.museum.evidence_index import QdrantEvidenceIndex
from core.museum.source_ingestion import ingest_source_manifest
from core.museum.store import DEMO_EXHIBIT_ID, DEMO_MUSEUM_ID, MuseumStore
from scripts.build_museum_evidence_index import build_museum_evidence_index


class RecordingEmbedder:
    def __init__(self, *, dimension: int, **_kwargs):
        self.dimension = dimension

    def embed_many_with_usage(self, texts):
        return EmbeddingBatchResult(
            vectors=tuple((1.0,) * self.dimension for _ in texts),
            request_id=f"request-{len(texts)}",
            usage={"total_tokens": len(texts)},
        )


def test_build_museum_evidence_index_uses_published_segments(tmp_path: Path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "source.md").write_text(
        "# 材质\n\n天然水晶。\n\n# 年代\n\n战国时期。\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "dataset_id": "index-build-test",
        "museum": {"id": DEMO_MUSEUM_ID, "name": "杭州博物馆（演示数据）"},
        "sources": [
            {
                "id": "index-build-source",
                "title": "索引构建测试资料",
                "source_type": "markdown",
                "path": "source.md",
                "rights_note": "自动化测试夹具。",
                "exhibit_ids": [DEMO_EXHIBIT_ID],
            }
        ],
    }
    manifest_path = dataset / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    database = tmp_path / "museum.db"
    store = MuseumStore(database)
    store.seed_demo_content()
    report = ingest_source_manifest(
        manifest_path,
        store=store,
        run_id="index-build-ingest",
    )
    assert report.ok

    client = QdrantClient(location=":memory:")

    def index_factory(**kwargs):
        return QdrantEvidenceIndex(client=client, **kwargs)

    result = build_museum_evidence_index(
        database_path=database,
        qdrant_url="http://unused.local",
        api_key="test-key",
        collection="museum-evidence-build-test",
        dimension=4,
        batch_size=1,
        switch_alias=False,
        embedder_factory=RecordingEmbedder,
        index_factory=index_factory,
    )
    assert result["indexed_point_count"] == len(report.segment_ids)
    assert result["embedding_batch_count"] == len(report.segment_ids)
    assert result["alias_switched"] is False
    assert client.get_aliases().aliases == []
