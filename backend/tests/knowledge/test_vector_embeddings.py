"""Tests for backend/knowledge/embeddings/service.py -- VectorEmbeddingService.
"""
import pytest

from backend.knowledge.embeddings.service import (
    VectorEmbeddingService,
    compute_cosine_similarity,
    get_vector_embedding_service,
)


def test_cosine_similarity_computation() -> None:
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [1.0, 0.0, 0.0]
    assert compute_cosine_similarity(vec_a, vec_b) == pytest.approx(1.0)

    vec_c = [0.0, 1.0, 0.0]
    assert compute_cosine_similarity(vec_a, vec_c) == pytest.approx(0.0)

    vec_d = [-1.0, 0.0, 0.0]
    assert compute_cosine_similarity(vec_a, vec_d) == pytest.approx(-1.0)

    # Edge cases
    assert compute_cosine_similarity([], []) == 0.0
    assert compute_cosine_similarity([1.0], [1.0, 2.0]) == 0.0
    assert compute_cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0


@pytest.mark.asyncio
async def test_vector_embedding_service_dimension_and_deterministic_output() -> None:
    svc = VectorEmbeddingService()
    assert svc.dimension == 768
    assert svc.model_name == "text-embedding-005"

    vec1 = await svc.embed_text("High VSWR alarm on Ericsson Node B cell 42")
    assert len(vec1) == 768
    assert any(x != 0.0 for x in vec1)

    vec2 = await svc.embed_text("High VSWR alarm on Ericsson Node B cell 42")
    assert compute_cosine_similarity(vec1, vec2) == pytest.approx(1.0)

    vec3 = await svc.embed_text("Microwave optical link power loss")
    assert compute_cosine_similarity(vec1, vec3) < 0.95

    batch = await svc.embed_many(["test 1", "test 2"])
    assert len(batch) == 2
    assert len(batch[0]) == 768
    assert len(batch[1]) == 768


def test_get_vector_embedding_service_singleton() -> None:
    s1 = get_vector_embedding_service()
    s2 = get_vector_embedding_service()
    assert s1 is s2
