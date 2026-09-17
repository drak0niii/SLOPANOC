"""Phase 6A.5: `hybrid_retrieve` -- the single composition entry point.

CANDIDATE-BOUNDARY ENFORCEMENT (§4/§58, THE mandatory invariant): every
one of the three channel calls below passes `query.permitted_knowledge_
ids` INTO the repository method itself
(`exact_match`/`lexical_search`/`semantic_search`, `repository.py`),
which constrains it INSIDE the SQL query's own `WHERE knowledge_id = ANY
(:ids)` clause -- never a post-hoc Python-side filter over an
unconstrained query result. An excluded or indeterminate Knowledge
object's evidence can therefore NEVER appear even as an intermediate hit
from any channel, by construction, not merely by a later filtering step
-- proven directly by `test_hybrid_retrieval_service.py`'s own candidate-
boundary tests (querying with a DELIBERATELY EXCLUDED id, then asserting
zero rows are returned from each channel individually).

EMPTY CANDIDATE SET (§41): `query.permitted_knowledge_ids == []` is
checked FIRST, before any channel is ever called -- `hybrid_retrieve`
returns an empty result immediately, without a single database query.
Never "search everything" -- an empty permitted set is never treated as
"no filter."

6A.5 ONLY -- NOT WIRED INTO 6A.9/Team-Manager/Incident-Manager: this
module is a new, additive, internal service API, mirroring 6A.4's own
`narrow_knowledge` (not consumed by any live agent path in this
milestone either).
"""
from __future__ import annotations

import logging

from backend.knowledge.hybrid_retrieval.contracts import (
    HybridRetrievalCandidate,
    HybridRetrievalQuery,
    HybridRetrievalResult,
    RetrievalTelemetry,
)
from backend.knowledge.hybrid_retrieval.embedding import EmbeddingProvider
from backend.knowledge.hybrid_retrieval.fusion import fuse
from backend.knowledge.hybrid_retrieval.reranking import rerank
from backend.knowledge.hybrid_retrieval.repository import EvidenceIndexRepository

_logger = logging.getLogger(__name__)

__all__ = ["hybrid_retrieve"]


async def hybrid_retrieve(
    query: HybridRetrievalQuery,
    repository: EvidenceIndexRepository,
    embedding_provider: EmbeddingProvider,
) -> HybridRetrievalResult:
    if not query.permitted_knowledge_ids:
        return HybridRetrievalResult(
            candidates=[],
            telemetry=RetrievalTelemetry(candidate_count_from_6a4=0, semantic_channel_mode="unavailable"),
        )

    # POST-6A -- VERSION IDENTITY IS PRESERVED INTO EVERY CHANNEL QUERY.
    # `permitted_version_keys` is the (knowledge_id, version_label) set
    # narrowing actually authorized; passing only `permitted_knowledge_ids`
    # would silently widen authorization to every version sharing an id,
    # including ARCHIVE/superseded ones whose sections remain indexed.
    version_keys = [tuple(key) for key in query.permitted_version_keys] or None
    exact_hits = await repository.exact_match(
        query.permitted_knowledge_ids, query.query_text, permitted_version_keys=version_keys
    )
    lexical_hits = await repository.lexical_search(
        query.permitted_knowledge_ids,
        query.query_text,
        limit=max(query.limit * 4, 20),
        permitted_version_keys=version_keys,
    )

    await repository.ensure_schema()
    semantic_hits = []
    semantic_mode = "unavailable"
    if repository.vector_available:
        # POST-6A -- EMBEDDING FAILURE MUST NOT DESTROY USABLE RESULTS.
        # An embedding-provider outage previously propagated out of this
        # function, discarding the exact/lexical hits already retrieved
        # above. Those hits are fully authorized and independently useful,
        # so the failure is recorded as an explicit DEGRADATION and the
        # authorized non-semantic results are returned. The caller can
        # always tell this apart from "genuinely nothing matched".
        try:
            [query_vector] = await embedding_provider.embed([query.query_text])
            semantic_hits = await repository.semantic_search(
                query.permitted_knowledge_ids,
                list(query_vector.values),
                limit=max(query.limit * 4, 20),
                permitted_version_keys=version_keys,
            )
            semantic_mode = "executed"
        except Exception:
            _logger.warning(
                "hybrid_retrieve: semantic channel unavailable -- returning authorized exact/lexical results",
                exc_info=True,
            )
            semantic_hits = []
            semantic_mode = "degraded_embedding_unavailable"
    else:
        semantic_mode = "degraded_no_vector_extension"

    hits_by_channel = {"exact": exact_hits, "lexical": lexical_hits, "semantic": semantic_hits}
    fusion_scores = fuse(hits_by_channel)

    all_evidence_ids = sorted(fusion_scores)
    records_by_id = await repository.get_many(all_evidence_ids)

    channel_hits_by_evidence: dict[str, list] = {}
    for hits in hits_by_channel.values():
        for hit in hits:
            channel_hits_by_evidence.setdefault(hit.evidence_id, []).append(hit)

    candidates = [
        HybridRetrievalCandidate(
            record=records_by_id[evidence_id],
            channel_hits=channel_hits_by_evidence.get(evidence_id, []),
            fusion_score=fusion_scores[evidence_id],
            rerank_score=0.0,
        )
        for evidence_id in all_evidence_ids
        if evidence_id in records_by_id
    ]

    reranked = rerank(candidates)[: query.limit]

    telemetry = RetrievalTelemetry(
        candidate_count_from_6a4=len(query.permitted_knowledge_ids),
        exact_hit_count=len(exact_hits),
        lexical_hit_count=len(lexical_hits),
        semantic_hit_count=len(semantic_hits),
        fusion_candidate_count=len(candidates),
        selected_evidence_count=len(reranked),
        semantic_channel_mode=semantic_mode,
    )
    return HybridRetrievalResult(candidates=reranked, telemetry=telemetry)
