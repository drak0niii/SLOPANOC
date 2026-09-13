"""Shared fixture builders for 6A.9 Troubleshooting Manager coordinator/
runtime tests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin, ContextProfileOwnerKind
from backend.context.domain.models import ContextAssertion, compute_context_state
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackage, ContextPackageInput, RequestContext
from backend.knowledge.hybrid_retrieval.contracts import ChannelHit, EvidenceIndexRecord, EvidenceSelectionResult, HybridRetrievalCandidate, RetrievalChannel

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def evidence_candidate(evidence_id: str = "ev-1", *, is_derived: bool = False) -> HybridRetrievalCandidate:
    record = EvidenceIndexRecord(
        evidence_id=evidence_id,
        knowledge_id="KO-1",
        version_label="v1",
        section_id="sec-1",
        is_derived=is_derived,
        indexable_text="Approved procedure: do not restart under condition Y.",
        content_hash="deadbeef",
    )
    return HybridRetrievalCandidate(record=record, channel_hits=[ChannelHit(evidence_id=evidence_id, channel=RetrievalChannel.EXACT, raw_score=1.0)], fusion_score=1.0, rerank_score=1.0)


def context_package_with_fault_known(
    owner_id: str = "OWNER-1",
    *,
    evidence_items: Optional[list[HybridRetrievalCandidate]] = None,
    case_id: Optional[str] = None,
    extra_assertions: Optional[list[ContextAssertion]] = None,
) -> ContextPackage:
    assertions = [ContextAssertion(assertion_id="a-fault", dimension=ContextDimension.FAULT, kind=AssertionKind.VALUE, raw_value="VSWR Over Threshold", canonical_value="VSWR OVER THRESHOLD", origin=ContextOrigin.USER, asserted_at=NOW)]
    assertions.extend(extra_assertions or [])
    state = compute_context_state(assertions)
    evidence_selection = EvidenceSelectionResult(query_text="q", selected=evidence_items or [], selection_reason="top_k_within_budget")
    input_ = ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION,
        owner_id=owner_id,
        case_id=case_id,
        request=RequestContext(question="what should I check next?"),
        telco_context_state=state,
        evidence_selection=evidence_selection,
    )
    return assemble_context_package(input_)


def context_package_fault_unknown(owner_id: str = "OWNER-1", *, evidence_items=None) -> ContextPackage:
    evidence_selection = EvidenceSelectionResult(query_text="q", selected=evidence_items or [], selection_reason="top_k_within_budget")
    input_ = ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION,
        owner_id=owner_id,
        telco_context_state={},
        evidence_selection=evidence_selection,
    )
    return assemble_context_package(input_)


def context_package_fault_conflicting(owner_id: str = "OWNER-1", *, evidence_items=None) -> ContextPackage:
    """DEF-0022 FIX (6A.11 Pass 1): the original version of this fixture
    asserted two different FAULT values, on the (incorrect) assumption
    that would produce CONFLICTING. `FAULT` is a MULTI-cardinality
    dimension (`backend/context/domain/enums.py`'s own `DIMENSION_
    CARDINALITY` table -- multiple genuinely concurrent faults are never
    a conflict merely for being different, e.g. two real simultaneous
    alarms), so `reduce_dimension` correctly resolved that shape to
    KNOWN with both values ACCEPTED, never CONFLICTING -- this fixture
    never actually exercised a conflict in the one place it was
    imported (`test_skill_resolution.py`, where it was imported but
    never once asserted against, so the mislabeling went undetected
    until 6A.11 Pass 1 tried to actually use it). Fixed to genuinely
    conflict on `VENDOR` (SINGULAR cardinality -- a node cannot
    legitimately have two vendors) while ALSO asserting a single, real,
    non-conflicting FAULT value, so callers needing "one KNOWN dimension
    a Skill can act on, plus one genuinely CONFLICTING, unrelated
    dimension that must never be silently resolved" get exactly that."""
    assertions = [
        ContextAssertion(assertion_id="a-fault", dimension=ContextDimension.FAULT, kind=AssertionKind.VALUE, raw_value="VSWR Over Threshold", canonical_value="VSWR OVER THRESHOLD", origin=ContextOrigin.USER, asserted_at=NOW),
        ContextAssertion(assertion_id="a-vendor-1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER, asserted_at=NOW),
        ContextAssertion(assertion_id="a-vendor-2", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Nokia", canonical_value="NOKIA", origin=ContextOrigin.CASE, asserted_at=NOW),
    ]
    state = compute_context_state(assertions)
    evidence_selection = EvidenceSelectionResult(query_text="q", selected=evidence_items or [], selection_reason="top_k_within_budget")
    input_ = ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION,
        owner_id=owner_id,
        telco_context_state=state,
        evidence_selection=evidence_selection,
    )
    return assemble_context_package(input_)
