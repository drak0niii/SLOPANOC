"""Concrete, embedding-model-backed `DenseSimilarityProvider` for hybrid Generic KM
retrieval (the retrieval package itself stays SDK-free -- see
backend/tests/knowledge/test_dependency_boundary.py).

Relevance only. A similarity never influences currentness, applicability, lifecycle,
evidence selection, provenance, or command authority -- those remain in their own
deterministic layers, unchanged.

FAIL-OPEN-TO-LEXICAL, NEVER TO SYNTHETIC: any embedding failure raises; the retrieval
service records `dense_status=unavailable:<reason>` and ranks lexically for that query.
No pseudo/hash embedding is ever substituted for a real one (unlike
`backend.knowledge.embeddings.service.VectorEmbeddingService`'s test fallback, which is
deliberately not used here).

Section vectors are cached in-process by (model, knowledge_id, version, section_id,
content hash) -- a derived, disposable index, never governed state. A changed section
text produces a new hash and is re-embedded.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Callable, Optional, Sequence

from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection
from backend.knowledge.embeddings.service import compute_cosine_similarity
from backend.knowledge.retrieval.scoring import section_retrieval_text

DEFAULT_EMBEDDING_MODEL = "text-embedding-005"
_QUERY_TASK = "RETRIEVAL_QUERY"
_DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"


class DenseEmbeddingError(RuntimeError):
    """Embedding response missing/malformed. Message never contains document text."""


class VertexEmbeddingSimilarityProvider:
    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        dimension: int = 768,
        batch_size: int = 16,
        max_concurrency: int = 4,
        max_chars: int = 8000,
        cache_max_entries: int = 50_000,
        client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self._batch_size = batch_size
        self._max_concurrency = max_concurrency
        self._max_chars = max_chars
        self._cache_max_entries = cache_max_entries
        self._client_factory = client_factory
        self._client: Any = None
        self._cache: dict[tuple[str, str, str, str, str], list[float]] = {}

    @property
    def model_name(self) -> str:
        return self._model_name

    def _get_client(self) -> Any:
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                from google.genai import Client

                self._client = Client()
        return self._client

    async def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        response = await self._get_client().aio.models.embed_content(
            model=self._model_name,
            contents=[t[: self._max_chars] for t in texts],
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=self._dimension),
        )
        embeddings = getattr(response, "embeddings", None) or []
        vectors = [list(getattr(e, "values", None) or []) for e in embeddings]
        if len(vectors) != len(texts) or any(len(v) != self._dimension for v in vectors):
            raise DenseEmbeddingError("embedding response count/dimension mismatch")
        return vectors

    async def similarities(
        self, query_text: str, candidates: Sequence[tuple[KnowledgeObject, KnowledgeSection]]
    ) -> list[float]:
        (query_vector,) = await self._embed([query_text], _QUERY_TASK)

        keys: list[tuple[str, str, str, str, str]] = []
        resolved: dict[tuple[str, str, str, str, str], list[float]] = {}
        texts: dict[tuple[str, str, str, str, str], str] = {}
        for obj, section in candidates:
            text = section_retrieval_text(obj, section)
            key = (
                self._model_name,
                obj.knowledge_id,
                obj.version.label,
                section.section_id,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
            keys.append(key)
            cached = self._cache.get(key)
            if cached is not None:
                resolved[key] = cached
            else:
                texts[key] = text

        missing = list(texts)
        batches = [missing[i : i + self._batch_size] for i in range(0, len(missing), self._batch_size)]
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _fill(batch: list[tuple[str, str, str, str, str]]) -> None:
            async with semaphore:
                vectors = await self._embed([texts[k] for k in batch], _DOCUMENT_TASK)
            if len(self._cache) + len(batch) > self._cache_max_entries:
                self._cache.clear()
            for k, v in zip(batch, vectors):
                self._cache[k] = v
                resolved[k] = v

        await asyncio.gather(*(_fill(b) for b in batches))
        return [compute_cosine_similarity(query_vector, resolved[k]) for k in keys]
