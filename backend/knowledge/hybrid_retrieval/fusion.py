"""Phase 6A.5: deterministic hybrid fusion (§27/§39).

ALGORITHM CHOICE: Reciprocal Rank Fusion (RRF), not weighted-normalized
scoring. Justification (per §27's own explicit requirement to justify
the choice): exact-match raw scores (always `1.0`), PostgreSQL `ts_rank`
values, and pgvector cosine-similarity values live on THREE completely
different, non-comparable scales -- summing or weighting them directly
(§39's own explicit example of what NOT to do) would require an
arbitrary, untested normalization scheme. RRF sidesteps this entirely by
operating on each channel's own RANK POSITION (1st, 2nd, 3rd, ...) rather
than its raw score value -- no cross-channel score comparability is
needed, so no normalization step exists at this layer (subsequent
DETERMINISTIC RERANKING, `reranking.py`, is the one place score
MAGNITUDE differences are deliberately reintroduced as ranking signals,
never at fusion). RRF is simple, deterministic, well-established, and
robust to wildly different score distributions -- exactly the properties
§27 asks the fusion algorithm to have.

No model call anywhere in this module.
"""
from __future__ import annotations

from backend.knowledge.hybrid_retrieval.contracts import ChannelHit

__all__ = ["fuse", "RRF_K"]

RRF_K = 60
"""The standard RRF damping constant (widely used in information-
retrieval literature, e.g. Elasticsearch's own RRF implementation) --
deliberately not query/corpus-tuned, matching this codebase's own
existing "deliberately generic, not query/document/format-tuned"
convention (A5's own `_RELEVANCE_TIE_BREAK_BUCKET_WIDTH`, `retrieval/
service.py`)."""


def fuse(hits_by_channel: dict[str, list[ChannelHit]]) -> dict[str, float]:
    """Returns `{evidence_id: fusion_score}` -- higher is better. Each
    channel's own hit list is independently ranked by ITS OWN `raw_score`
    (descending) BEFORE RRF is applied, so a channel's own internal
    ordering is always honored regardless of the other channels' score
    scales. Order-independent with respect to the INPUT dict's own key
    iteration order (verified by test) -- only each channel's already-
    sorted rank contributes. Stable under ties WITHIN one channel: two
    hits with the identical `raw_score` receive the SAME rank contribution
    (`1/(k+rank)` for the tied rank position) rather than an arbitrary
    ordering silently breaking the tie -- `reranking.py`'s own stable
    tie-breakers (never this function) resolve any remaining ambiguity.
    """
    scores: dict[str, float] = {}
    for channel_hits in hits_by_channel.values():
        ranked = sorted(channel_hits, key=lambda hit: -hit.raw_score)
        rank = 0
        previous_score: float | None = None
        for hit in ranked:
            if hit.raw_score != previous_score:
                rank += 1
                previous_score = hit.raw_score
            scores[hit.evidence_id] = scores.get(hit.evidence_id, 0.0) + 1.0 / (RRF_K + rank)
    return scores
