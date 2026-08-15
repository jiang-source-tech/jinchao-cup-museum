from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from qdrant_client import QdrantClient

from core.museum.embedding import EmbeddingBatchResult
from core.museum.qdrant_index import QdrantFactIndex
from scripts.build_museum_isolated_release import build_isolated_release
from scripts.build_museum_isolated_vector_index import build_isolated_vector_index


CONTENT_DIRECTORY = Path(__file__).parents[1] / "content" / "museum"


class RecordingEmbedder:
    def __init__(self, *, dimension, **_kwargs):
        self.dimension = dimension

    def embed_many_with_usage(self, texts):
        return EmbeddingBatchResult(
            vectors=tuple((1.0,) * self.dimension for _ in texts),
            request_id=f"request-{len(texts)}",
            usage={"total_tokens": len(texts) * 3},
        )


def test_isolated_vector_build_preserves_aliases_and_records_usage(tmp_path):
    database_path = tmp_path / "stage3-isolated.db"
    build_isolated_release(
        content_directory=CONTENT_DIRECTORY,
        database_path=database_path,
        reviewer="test-reviewer",
        publisher="test-publisher",
        occurred_at=datetime.fromisoformat("2026-08-12T12:00:00+00:00"),
    )
    client = QdrantClient(location=":memory:")

    def index_factory(**kwargs):
        return QdrantFactIndex(client=client, **kwargs)

    result = build_isolated_vector_index(
        database_path=database_path,
        qdrant_url="http://unused.local",
        api_key="test-key",
        dimension=4,
        batch_size=10,
        embedder_factory=RecordingEmbedder,
        index_factory=index_factory,
    )

    assert result["indexed_point_count"] == 187
    assert result["embedding_batch_count"] == 19
    assert result["embedding_usage"] == {"total_tokens": 561}
    assert result["payload_verification"]["ok"] is True
    assert result["alias_switched"] is False
    assert client.get_aliases().aliases == []


def test_dashscope_embedder_exposes_request_id_and_usage(monkeypatch):
    from core.museum.embedding import DashScopeTextEmbedder

    monkeypatch.setattr(
        "dashscope.TextEmbedding.call",
        lambda **_kwargs: SimpleNamespace(
            status_code=200,
            request_id="dashscope-request-1",
            usage={"total_tokens": 7},
            output={
                "embeddings": [
                    {"text_index": 0, "embedding": [0.1, 0.2, 0.3]}
                ]
            },
        ),
    )
    result = DashScopeTextEmbedder(
        api_key="test-key",
        dimension=3,
    ).embed_many_with_usage(["测试文本"])

    assert result.request_id == "dashscope-request-1"
    assert result.usage == {"total_tokens": 7}
    assert result.vectors == ((0.1, 0.2, 0.3),)
