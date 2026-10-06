"""Hybrid Search Reranking & Fusion Pipeline for SLOPANOC Generic KM.

Combines:
1. Reciprocal Rank Fusion (RRF) over sparse lexical rank and dense semantic rank.
2. Cross-encoder / Neural semantic reranking stage.
3. Deterministic tie-breaking preserving native source text precedence over derived visual descriptions.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Protocol
from pydantic import BaseModel, Field

from backend.knowledge.retrieval.contracts import KnowledgeRetrievalItem

logger = logging.getLogger(__name__)

RRF_K_CONSTANT = 60


def compute_reciprocal_rank_fusion(
    sparse_items: list[KnowledgeRetrievalItem],
    dense_items: list[KnowledgeRetrievalItem],
    k: int = RRF_K_CONSTANT,
) -> list[KnowledgeRetrievalItem]:
    """Computes Reciprocal Rank Fusion (RRF) scores across sparse and dense candidates.

    Score(d) = 1 / (k + rank_sparse(d)) + 1 / (k + rank_dense(d))
    """
    item_map: dict[str, KnowledgeRetrievalItem] = {}
    rrf_scores: dict[str, float] = {}

    def item_key(item: KnowledgeRetrievalItem) -> str:
        return f"{item.knowledge_id}:{item.version_label}:{item.section.section_id}"

    for rank, item in enumerate(sparse_items, start=1):
        key = item_key(item)
        item_map[key] = item
        rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / (k + rank))

    for rank, item in enumerate(dense_items, start=1):
        key = item_key(item)
        if key not in item_map:
            item_map[key] = item
        rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / (k + rank))

    # Update relevance_score with combined RRF score and sort
    fused_items: list[KnowledgeRetrievalItem] = []
    for key, score in rrf_scores.items():
        base_item = item_map[key]
        fused_items.append(
            KnowledgeRetrievalItem(
                knowledge_id=base_item.knowledge_id,
                document_type=base_item.document_type,
                title=base_item.title,
                version_label=base_item.version_label,
                lifecycle_status=base_item.lifecycle_status,
                section=base_item.section,
                source=base_item.source,
                relevance_score=min(max(score, 0.0), 1.0),
                applicability_outcome=base_item.applicability_outcome,
                unresolved_applicability_dimensions=list(base_item.unresolved_applicability_dimensions),
                is_derived=base_item.is_derived,
            )
        )

    # Sort descending by RRF score, native text first on ties
    fused_items.sort(key=lambda i: (-i.relevance_score, i.is_derived))
    return fused_items


class NeuralReranker(Protocol):
    """Protocol for semantic neural / cross-encoder rerankers."""

    async def rerank(
        self,
        query: str,
        candidates: list[KnowledgeRetrievalItem],
        top_k: int = 5,
    ) -> list[KnowledgeRetrievalItem]:
        ...


class HeuristicReranker:
    """Deterministic reference reranker maintaining native-source precedence."""

    async def rerank(
        self,
        query: str,
        candidates: list[KnowledgeRetrievalItem],
        top_k: int = 5,
    ) -> list[KnowledgeRetrievalItem]:
        # Preserve native text precedence: native source text strictly outranks derived text on equivalent score bands
        sorted_candidates = sorted(
            candidates,
            key=lambda item: (
                -round(item.relevance_score / 0.15),
                item.is_derived,
                -item.relevance_score,
            ),
        )
        return sorted_candidates[:top_k]
