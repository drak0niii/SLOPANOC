"""Phase 6A.5: deterministic reranking (§28/§65.B).

NO LLM. NO GEMINI. NO CROSS-ENCODER. NO MODEL CALL OF ANY KIND. Verified
structurally: no `google.adk`/`google.genai` import anywhere in this
module (enforced by this milestone's own extension of `test_dependency_
boundary.py`) -- a real, automated proof, not merely a claim.

Reranking combines ONLY deterministic signals already available on each
`HybridRetrievalCandidate`:
  1. fusion score (RRF, `fusion.py`) -- the primary signal.
  2. exact-match presence -- a real, deterministic identifier hit is a
     strong, structural signal (§35: "EUtranCellFDD"/"VSWR" must not lose
     to a vaguely semantically similar document) -- boosted additively,
     never allowed to be silently outweighed by channel noise.
  3. multi-channel agreement -- more channels independently hitting the
     same evidence unit is itself informative (§30's "multi-channel
     agreement" example).
  4. source vs. derived (§29/§14): `is_derived=False` (source) preferred
     over `is_derived=True` (derived) ONLY as a tie-break among
     candidates whose other signals are otherwise equal-ranked -- never
     a blanket suppression of derived evidence (mirrors A5's own
     retrieval-scoring precedent for the identical principle, `retrieval/
     service.py`'s `_item_sort_key`).
  5. stable, deterministic identity tie-breaker (`evidence_id`) --
     guarantees a single, reproducible total order regardless of any
     upstream nondeterminism (e.g. database row return order).

Given the SAME corpus, candidate set, query, and index state, this
function ALWAYS returns the same order (proven by a dedicated repeated-
call test) -- §40.

APPLICABILITY IS NEVER REDISCOVERED HERE (§28's own explicit invariant):
this module receives ONLY candidates the caller already resolved from
6A.4's `permitted_knowledge_ids` (`service.py`'s own composition) -- it
has no code path that could reintroduce an excluded/indeterminate
Knowledge object, since it never queries the corpus itself at all.
"""
from __future__ import annotations

from backend.knowledge.hybrid_retrieval.contracts import HybridRetrievalCandidate

__all__ = ["rerank"]

_EXACT_MATCH_BOOST = 1.0
"""A deliberately large, fixed additive boost -- large enough that a
real exact-identifier hit always outranks a purely semantic/lexical
match of otherwise-comparable fusion score (§35), never so large that it
becomes the only signal that matters (multi-channel agreement and RRF's
own rank-based scoring still discriminate among multiple exact hits)."""

_MULTI_CHANNEL_BOOST_PER_EXTRA_CHANNEL = 0.05
"""Small, deliberately generic (not query/corpus-tuned) per-additional-
channel bonus -- rewards agreement without letting it dominate the
primary fusion signal."""


def _channel_names(candidate: HybridRetrievalCandidate) -> set[str]:
    return {hit.channel.value for hit in candidate.channel_hits}


def rerank(candidates: list[HybridRetrievalCandidate]) -> list[HybridRetrievalCandidate]:
    """Returns a NEW list, sorted deterministically; input order never
    matters (verified by test). Each candidate's own `rerank_score` field
    is populated (mutated via `model_copy`, never in place) so callers
    can inspect exactly why the final order is what it is.
    """
    scored: list[HybridRetrievalCandidate] = []
    for candidate in candidates:
        channels = _channel_names(candidate)
        exact_bonus = _EXACT_MATCH_BOOST if "exact" in channels else 0.0
        multi_channel_bonus = max(0, len(channels) - 1) * _MULTI_CHANNEL_BOOST_PER_EXTRA_CHANNEL
        rerank_score = candidate.fusion_score + exact_bonus + multi_channel_bonus
        scored.append(candidate.model_copy(update={"rerank_score": rerank_score}))

    return sorted(
        scored,
        key=lambda c: (
            -c.rerank_score,
            c.record.is_derived,  # False (source) sorts before True (derived) -- Python bool ordering.
            c.record.evidence_id,  # final, guaranteed-unique, deterministic tie-breaker.
        ),
    )
