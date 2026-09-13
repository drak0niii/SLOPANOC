"""Shared, real 6A.6 `ContextPackage` fixture helpers for 6A.7's own
test suite -- builds packages via the REAL `assemble_context_package`
(never a hand-faked package), exactly the same discipline `test_end_to_
end_6a2_to_6a6.py` (6A.6) already established.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin, ContextProfileOwnerKind
from backend.context.domain.models import ContextAssertion, compute_context_state
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackage, ContextPackageInput
from backend.knowledge.hybrid_retrieval.contracts import (
    ChannelHit,
    EvidenceIndexRecord,
    EvidenceSelectionResult,
    HybridRetrievalCandidate,
    RetrievalChannel,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candidate(evidence_id: str, knowledge_id: str = "K1", is_derived: bool = False) -> HybridRetrievalCandidate:
    record = EvidenceIndexRecord(
        evidence_id=evidence_id, knowledge_id=knowledge_id, version_label="1.0", section_id=evidence_id,
        is_derived=is_derived, indexable_text=f"text for {evidence_id}", content_hash="h", created_at=_NOW, updated_at=_NOW,
    )
    return HybridRetrievalCandidate(record=record, channel_hits=[ChannelHit(evidence_id=evidence_id, channel=RetrievalChannel.EXACT, raw_score=1.0)], fusion_score=0.5, rerank_score=1.5)


def build_context_package(
    *,
    known: dict[ContextDimension, str] | None = None,
    unknown: list[ContextDimension] | None = None,
    conflicting: list[ContextDimension] | None = None,
    selected_evidence_count: int = 0,
    derived_evidence_count: int = 0,
    case_context=None,
) -> ContextPackage:
    """Builds a REAL, assembled `ContextPackage` -- `known` maps a
    dimension to its single canonical value; `conflicting` dimensions get
    two distinct assertions; `unknown` dimensions are simply never
    asserted (compute_context_state never fabricates an UNKNOWN entry --
    consistent with 6A.2's own semantics, so an "unknown" dimension here
    is genuinely absent from the resulting ContextPackage.telco_context,
    exactly matching real production behavior)."""
    assertions = []
    idx = 0
    for dimension, value in (known or {}).items():
        idx += 1
        assertions.append(ContextAssertion(assertion_id=f"a{idx}", dimension=dimension, kind=AssertionKind.VALUE, raw_value=value, canonical_value=value.upper(), origin=ContextOrigin.USER))
    for dimension in conflicting or []:
        idx += 1
        assertions.append(ContextAssertion(assertion_id=f"a{idx}", dimension=dimension, kind=AssertionKind.VALUE, raw_value="X", canonical_value="X", origin=ContextOrigin.USER))
        idx += 1
        assertions.append(ContextAssertion(assertion_id=f"a{idx}", dimension=dimension, kind=AssertionKind.VALUE, raw_value="Y", canonical_value="Y", origin=ContextOrigin.CASE))
    # `unknown` dimensions are intentionally never asserted -- listed only for test readability at call sites.
    _ = unknown

    candidates = []
    for i in range(selected_evidence_count):
        candidates.append(_candidate(f"ev{i}", is_derived=(i < derived_evidence_count)))
    selection = EvidenceSelectionResult(query_text="q", selected=candidates, selection_reason="top_k_within_budget" if candidates else "no_candidates")

    return assemble_context_package(ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1",
        telco_context_state=compute_context_state(assertions),
        case_context=case_context,
        evidence_selection=selection,
    ))
