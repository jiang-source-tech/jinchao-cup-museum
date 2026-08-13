from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class TextEmbedder(Protocol):
    model: str
    dimension: int

    def embed(self, text: str) -> list[float]: ...

    def embed_many(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class EmbeddingBatchResult:
    vectors: tuple[tuple[float, ...], ...]
    request_id: str
    usage: dict[str, Any]


@dataclass(frozen=True)
class DashScopeTextEmbedder:
    api_key: str
    model: str = "text-embedding-v4"
    dimension: int = 1024
    timeout_seconds: float = 3.0

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        result = self.embed_many_with_usage(texts)
        return [list(vector) for vector in result.vectors]

    def embed_many_with_usage(self, texts: list[str]) -> EmbeddingBatchResult:
        if not texts:
            return EmbeddingBatchResult(vectors=(), request_id="", usage={})
        if not self.api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")

        from dashscope import TextEmbedding

        response = TextEmbedding.call(
            model=self.model,
            input=texts,
            api_key=self.api_key,
            dimension=self.dimension,
            request_timeout=self.timeout_seconds,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code != 200:
            code = getattr(response, "code", "unknown")
            message = getattr(response, "message", "embedding request failed")
            raise RuntimeError(f"DashScope embedding failed: {code}: {message}")

        output = getattr(response, "output", None) or {}
        embeddings = output.get("embeddings", [])
        ordered = sorted(embeddings, key=lambda item: int(item.get("text_index", 0)))
        vectors = [list(item["embedding"]) for item in ordered]
        if len(vectors) != len(texts):
            raise RuntimeError("DashScope embedding response count mismatch")
        if any(len(vector) != self.dimension for vector in vectors):
            raise RuntimeError("DashScope embedding dimension mismatch")
        usage = getattr(response, "usage", None) or {}
        if not isinstance(usage, dict):
            usage = dict(usage)
        return EmbeddingBatchResult(
            vectors=tuple(tuple(float(value) for value in vector) for vector in vectors),
            request_id=str(getattr(response, "request_id", "") or ""),
            usage=dict(usage),
        )
