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
