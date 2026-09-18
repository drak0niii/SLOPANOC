"""The generic relevance-scoring abstraction and its one deterministic
local reference implementation.

`KnowledgeRelevanceScorer` deliberately keeps retrieval orchestration
(service.py) independent of the scoring implementation -- a future
semantic/embedding-backed scorer could satisfy this same Protocol
without `service.py` changing at all, as long as it preserves the same
currentness/applicability semantics service.py already enforces around
it (see docs/KNOWLEDGE_CONTRACT.md's Phase 5.1G section).
"""
from __future__ import annotations

import re
from typing import Protocol

from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection

_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    """Generic SYNTACTIC normalization only: casefold, then split on
    generic word boundaries (`\\w+`, Unicode-aware) -- never stemming,
    lemmatization, domain synonyms, abbreviation expansion, or fuzzy
    matching. Returns a SET (deduplicated) so repeated terms -- in
    either the query or the candidate text -- never artificially inflate
    a score. Empty/whitespace-only text produces an empty set.
    """
    return set(_TOKEN_PATTERN.findall(text.casefold()))


class KnowledgeRelevanceScorer(Protocol):
    """Structural contract: score how relevant one `KnowledgeSection`
    (within its owning `KnowledgeObject`, for title/metadata context) is
    to `query_text`. Deterministic, synchronous, and local -- no network,
    no model call is implied or required by this shape.
    """

    def score(self, query_text: str, knowledge_object: KnowledgeObject, section: KnowledgeSection) -> float:
        """Return a relevance score in `[0.0, 1.0]` -- `0.0` means no
        relevance at all (the caller excludes such sections); `1.0`
        means the strongest match this scorer can produce.
        """
        ...


class TokenOverlapRelevanceScorer:
    """The Phase 5.1G reference scorer: normalized lexical token-overlap
    coverage. Deterministic, requires no network/model/embedding, and no
    new dependency (`re`/`str.casefold` are stdlib).

    FORMULA:

        relevance = |unique query tokens ALSO present in candidate text|
                    -----------------------------------------------------
                          |unique query tokens|

    -- `0.0` when no normalized query term appears anywhere in the
    candidate text, `1.0` when every unique normalized query term
    appears at least once. Duplicate query terms are deduplicated before
    the ratio is computed (via `_tokenize`'s set semantics), so repeating
    a term in the query text can never inflate the score.

    TEXT SURFACE (deliberately small and explicit, per instruction
    section 24): `KnowledgeObject.title`, `KnowledgeMetadata.tags`,
    `KnowledgeSection.heading`, and `KnowledgeSection.content` --
    concatenated and tokenized together. `source_system`,
    `lifecycle_status`, and `version.label` never participate; document
    type and source are never used to inflate or suppress a score
    (instruction sections 43/44/45).

    NO FUZZY/SYNONYM/SEMANTIC BEHAVIOR: "Ericsson" and "ericsson" match
    only because casefold normalizes case, never because of substring or
    edit-distance logic ("Eric" does NOT match "Ericsson"); "5G"/"NR" and
    "failure"/"fault" are never treated as equivalent -- there is no
    synonym table of any kind in this module.
    """

    def score(self, query_text: str, knowledge_object: KnowledgeObject, section: KnowledgeSection) -> float:
        query_tokens = _tokenize(query_text)
        if not query_tokens:
            return 0.0

        candidate_text_parts = [
            knowledge_object.title,
            " ".join(knowledge_object.metadata.tags),
            section.heading or "",
            section.content,
        ]
        candidate_tokens = _tokenize(" ".join(candidate_text_parts))

        overlap = query_tokens & candidate_tokens
        return len(overlap) / len(query_tokens)


class DenseVectorRelevanceScorer:
    """Computes dense semantic relevance using 768-dimensional embeddings."""

    def __init__(self, embedding_service: Optional[Any] = None) -> None:
        if embedding_service is None:
            from backend.knowledge.embeddings.service import get_vector_embedding_service
            self._embedding_service = get_vector_embedding_service()
        else:
            self._embedding_service = embedding_service

    def score(self, query_text: str, knowledge_object: KnowledgeObject, section: KnowledgeSection) -> float:
        if not query_text.strip() or not section.content.strip():
            return 0.0

        from backend.knowledge.embeddings.service import compute_cosine_similarity

        candidate_parts = [
            knowledge_object.title,
            " ".join(knowledge_object.metadata.tags),
            section.heading or "",
            section.content,
        ]
        candidate_text = " ".join(p for p in candidate_parts if p)

        query_vec = self._embedding_service.embed_text_sync(query_text)
        candidate_vec = self._embedding_service.embed_text_sync(candidate_text)

        sim = compute_cosine_similarity(query_vec, candidate_vec)
        # Normalize [-1.0, 1.0] to [0.0, 1.0]
        return max(0.0, min(1.0, (sim + 1.0) / 2.0))


class HybridRelevanceScorer:
    """Fuses sparse lexical overlap and dense semantic similarity scores."""

    def __init__(
        self,
        sparse_scorer: Optional[KnowledgeRelevanceScorer] = None,
        dense_scorer: Optional[KnowledgeRelevanceScorer] = None,
        sparse_weight: float = 0.5,
        dense_weight: float = 0.5,
    ) -> None:
        self._sparse_scorer = sparse_scorer if sparse_scorer is not None else TokenOverlapRelevanceScorer()
        self._dense_scorer = dense_scorer if dense_scorer is not None else DenseVectorRelevanceScorer()
        self._sparse_weight = sparse_weight
        self._dense_weight = dense_weight

    def score(self, query_text: str, knowledge_object: KnowledgeObject, section: KnowledgeSection) -> float:
        sparse_score = self._sparse_scorer.score(query_text, knowledge_object, section)
        dense_score = self._dense_scorer.score(query_text, knowledge_object, section)
        total_weight = self._sparse_weight + self._dense_weight
        if total_weight <= 0.0:
            return 0.0
        combined = (self._sparse_weight * sparse_score + self._dense_weight * dense_score) / total_weight
        return max(0.0, min(1.0, combined))

