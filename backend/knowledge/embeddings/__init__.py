"""Embeddings package for dense vector representations in Generic KM."""
from backend.knowledge.embeddings.service import (
    VectorEmbeddingService,
    compute_cosine_similarity,
    get_vector_embedding_service,
)

__all__ = [
    "VectorEmbeddingService",
    "compute_cosine_similarity",
    "get_vector_embedding_service",
]
