"""Phase 6A.6 core test matrix -- DETERMINISM (§26/§53) and the content
FINGERPRINT (§27). Given identical logical inputs, repeated assembly
must produce identical package content, identical ordering, and an
identical `content_fingerprint`; only `created_at` (a real wall-clock
value, deliberately excluded from the fingerprint) may differ.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin, ContextProfileOwnerKind
from backend.context.domain.models import ContextAssertion, compute_context_state
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackageInput, RequestContext
from backend.knowledge.hybrid_retrieval.contracts import (
    ChannelHit,
    EvidenceIndexRecord,
    EvidenceSelectionResult,
    HybridRetrievalCandidate,
    RetrievalChannel,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_input() -> ContextPackageInput:
    a1 = ContextAssertion(assertion_id="a1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER)
    a2 = ContextAssertion(assertion_id="a2", dimension=ContextDimension.TECHNOLOGY, kind=AssertionKind.VALUE, raw_value="LTE", canonical_value="LTE", origin=ContextOrigin.USER)
    record = EvidenceIndexRecord(evidence_id="ev1", knowledge_id="K1", version_label="1.0", section_id="S1", is_derived=False, indexable_text="VSWR text", content_hash="h", created_at=_NOW, updated_at=_NOW)
    candidate = HybridRetrievalCandidate(record=record, channel_hits=[ChannelHit(evidence_id="ev1", channel=RetrievalChannel.EXACT, raw_score=1.0)], fusion_score=0.5, rerank_score=1.5)
    sel = EvidenceSelectionResult(query_text="VSWR", selected=[candidate], selection_reason="top_k_within_budget")
    return ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1", session_id="sess-1",
        request=RequestContext(question="what should I check?"),
        telco_context_state=compute_context_state([a1, a2]),
        evidence_selection=sel, retrieved_candidate_count=3,
    )


def test_repeated_assembly_produces_identical_fingerprint() -> None:
    input_ = _make_input()
    pkg1 = assemble_context_package(input_)
    time.sleep(0.01)  # ensure created_at would differ if it were included
    pkg2 = assemble_context_package(input_)
    assert pkg1.content_fingerprint == pkg2.content_fingerprint
    assert pkg1.content_fingerprint != ""
    assert pkg1.created_at != pkg2.created_at, "created_at is real wall-clock time and is expected to differ"


def test_fingerprint_is_stable_across_many_repeated_calls() -> None:
    input_ = _make_input()
    fingerprints = {assemble_context_package(input_).content_fingerprint for _ in range(10)}
    assert len(fingerprints) == 1


def test_materially_different_input_produces_different_fingerprint() -> None:
    input_a = _make_input()
    a3 = ContextAssertion(assertion_id="a3", dimension=ContextDimension.CUSTOMER, kind=AssertionKind.VALUE, raw_value="Vodafone", canonical_value="VODAFONE", origin=ContextOrigin.USER)
    input_b = input_a.model_copy(update={"telco_context_state": compute_context_state([a3])})
    fp_a = assemble_context_package(input_a).content_fingerprint
    fp_b = assemble_context_package(input_b).content_fingerprint
    assert fp_a != fp_b


def test_ordering_deterministic_regardless_of_dict_construction_order() -> None:
    a1 = ContextAssertion(assertion_id="a1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER)
    a2 = ContextAssertion(assertion_id="a2", dimension=ContextDimension.CUSTOMER, kind=AssertionKind.VALUE, raw_value="Vodafone", canonical_value="VODAFONE", origin=ContextOrigin.USER)
    state_forward = compute_context_state([a1, a2])
    state_backward = compute_context_state([a2, a1])

    empty_sel = EvidenceSelectionResult(query_text="q", selected=[], selection_reason="no_candidates")
    pkg1 = assemble_context_package(ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="s", telco_context_state=state_forward, evidence_selection=empty_sel))
    pkg2 = assemble_context_package(ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="s", telco_context_state=state_backward, evidence_selection=empty_sel))

    dims1 = [v.dimension for v in pkg1.telco_context]
    dims2 = [v.dimension for v in pkg2.telco_context]
    assert dims1 == dims2
    assert pkg1.content_fingerprint == pkg2.content_fingerprint


def test_fingerprint_excludes_only_created_at_and_itself() -> None:
    """A direct proof that the fingerprint computation's exclusion set is
    exactly {created_at, content_fingerprint} -- no other field is
    silently excluded, which could otherwise mask a real content change."""
    from backend.context_engineering.fingerprint import _EXCLUDED_FIELDS

    assert _EXCLUDED_FIELDS == frozenset({"created_at", "content_fingerprint"})
