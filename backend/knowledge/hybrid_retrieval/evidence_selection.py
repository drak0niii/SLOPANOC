"""Phase 6A.5: evidence selection -- distinct from retrieval (§31/§32).

>>> RETRIEVED RESULT != EVIDENCE ACTUALLY USED <<<

A top-N hybrid-retrieval result is a set of CANDIDATES, not automatically
evidence -- mirrors the exact same invariant 5.1H/5.1J already established
for governed-Knowledge evidence (`docs/KNOWLEDGE_CONTRACT.md` §17's
"SEARCH RESULT != EVIDENCE USED"), now extended to the hybrid-retrieval
layer. This module's own `select_evidence` is a bounded, deterministic
budget filter -- it does NOT generate a troubleshooting answer, does NOT
call a model, and does NOT decide *what the answer is* -- 6A.9's own
future Troubleshooting Manager owns reasoning over selected evidence,
never this milestone.
"""
from __future__ import annotations

from backend.knowledge.hybrid_retrieval.contracts import EvidenceSelectionResult, HybridRetrievalCandidate

__all__ = ["select_evidence"]


def select_evidence(
    query_text: str, candidates: list[HybridRetrievalCandidate], *, max_evidence_units: int
) -> EvidenceSelectionResult:
    """Deterministic top-K selection within a bounded budget (§33: "6A.5
    may define bounded evidence limits such as top K... do not build
    mature 6B Context Engineering budget logic here"). `candidates` is
    assumed ALREADY reranked (deterministic order) -- this function never
    re-sorts; it only truncates. Selecting zero candidates from an empty
    input is a normal, valid outcome, never an error.
    """
    selected = candidates[:max_evidence_units]
    reason = "top_k_within_budget" if selected else "no_candidates"
    return EvidenceSelectionResult(query_text=query_text, selected=selected, selection_reason=reason)
