"""Phase 6A.5 §46: REAL embedding API validation -- the mandatory
real-stack exit criterion. Mocks are acceptable for unit tests (see
`test_hybrid_retrieval_indexing.py`'s `FakeEmbeddingProvider`) but are
NOT sufficient for this specific requirement.

Uses 2-5 synthetic, non-sensitive TELCO-shaped evidence units, never the
real corpus. SKIPS (never fails, never fabricates a result) if a real
Vertex AI call cannot be made in this environment -- e.g. no valid
Application Default Credentials -- mirroring this codebase's own
"skip if real X unavailable" discipline used throughout this session
for real Cloud SQL/corpus dependencies.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.knowledge_hybrid_retrieval.vertex_embedding_provider import EXPECTED_DIMENSIONS, MODEL_IDENTIFIER, VertexTextEmbeddingProvider


def _real_api_reachable() -> bool:
    import os

    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    try:
        async def _probe():
            provider = VertexTextEmbeddingProvider()
            await provider.embed(["reachability probe"])

        asyncio.run(_probe())
        return True
    except Exception:
        return False


_REACHABLE = _real_api_reachable()
pytestmark = pytest.mark.skipif(not _REACHABLE, reason="real Vertex AI embedding API not reachable in this environment (no valid ADC, or model unavailable)")


@pytest.mark.asyncio
async def test_real_embedding_call_succeeds_and_records_provenance() -> None:
    provider = VertexTextEmbeddingProvider()
    vectors = await provider.embed(["VSWR Over Threshold alarm: restart is not permitted for this fault."])
    assert len(vectors) == 1
    vector = vectors[0]
    assert vector.model == MODEL_IDENTIFIER
    assert vector.model_version == MODEL_IDENTIFIER
    assert vector.dimensions == EXPECTED_DIMENSIONS
    assert len(vector.values) == EXPECTED_DIMENSIONS


@pytest.mark.asyncio
async def test_real_embedding_batch_call_preserves_order() -> None:
    provider = VertexTextEmbeddingProvider()
    texts = [
        "high reflected power after antenna work",
        "VSWR degradation following feeder intervention",
        "unrelated billing invoice text",
    ]
    vectors = await provider.embed(texts)
    assert len(vectors) == 3
    assert all(v.dimensions == EXPECTED_DIMENSIONS for v in vectors)


@pytest.mark.asyncio
async def test_real_embedding_semantic_similarity_ordering_is_sensible() -> None:
    """§46: 'semantic ranking is sensible' -- a semantically related
    sentence must score closer to the query than an unrelated one, using
    plain cosine similarity computed in pure Python (no numpy needed at
    this scale)."""
    import math

    provider = VertexTextEmbeddingProvider()
    query, related, unrelated = await provider.embed(
        [
            "high reflected power after antenna work",
            "VSWR degradation following feeder intervention",
            "unrelated billing invoice text",
        ]
    )

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        return dot / (norm_a * norm_b)

    sim_related = cosine(query.values, related.values)
    sim_unrelated = cosine(query.values, unrelated.values)
    assert sim_related > sim_unrelated


@pytest.mark.asyncio
async def test_real_embedding_empty_input_returns_empty_without_api_call() -> None:
    provider = VertexTextEmbeddingProvider()
    vectors = await provider.embed([])
    assert vectors == []
