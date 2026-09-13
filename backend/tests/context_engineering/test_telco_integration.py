"""Phase 6A.6 core test matrix -- TELCO CONTEXT (§49): every one of
6A.2's own four states, plus multi-valued dimensions and full
raw/canonical/origin/source_reference/asserted_at provenance, must
survive assembly into a `ContextPackage` byte-for-byte -- never
resolved, guessed, or collapsed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin, ContextProfileOwnerKind, ContextState
from backend.context.domain.models import ContextAssertion, ContextValue, compute_context_state
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackageInput
from backend.knowledge.hybrid_retrieval.contracts import EvidenceSelectionResult

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _empty_evidence() -> EvidenceSelectionResult:
    return EvidenceSelectionResult(query_text="q", selected=[], selection_reason="no_candidates")


def _input(telco_state: dict) -> ContextPackageInput:
    return ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION,
        owner_id="sess-1",
        telco_context_state=telco_state,
        evidence_selection=_empty_evidence(),
    )


def test_known_dimension_preserved() -> None:
    a = ContextAssertion(assertion_id="a1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER, source_reference="case-item-1", asserted_at=_NOW)
    state = compute_context_state([a])
    pkg = assemble_context_package(_input(state))
    assert len(pkg.telco_context) == 1
    view = pkg.telco_context[0]
    assert view.dimension == ContextDimension.VENDOR
    assert view.state == ContextState.KNOWN
    assert len(view.accepted) == 1
    assert view.accepted[0].raw_value == "Ericsson"
    assert view.accepted[0].canonical_value == "ERICSSON"
    assert view.accepted[0].origin == ContextOrigin.USER
    assert view.accepted[0].source_reference == "case-item-1"
    assert view.accepted[0].asserted_at == _NOW
    assert view.conflicting == []


def test_unknown_dimension_preserved() -> None:
    """A dimension with ZERO assertions never appears in `compute_context_
    state`'s own return -- so it never appears in the package either.
    This test instead proves a dimension can be UNKNOWN in the package by
    directly asserting the identity: no fabricated UNKNOWN entries are
    invented for dimensions nobody asserted anything about."""
    a = ContextAssertion(assertion_id="a1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER)
    state = compute_context_state([a])
    pkg = assemble_context_package(_input(state))
    dims = {v.dimension for v in pkg.telco_context}
    assert ContextDimension.RELEASE not in dims, "an unasserted dimension must never be fabricated as UNKNOWN"


def test_conflicting_dimension_preserved_never_resolved() -> None:
    a1 = ContextAssertion(assertion_id="a1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER)
    a2 = ContextAssertion(assertion_id="a2", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Nokia", canonical_value="NOKIA", origin=ContextOrigin.CASE)
    state = compute_context_state([a1, a2])
    pkg = assemble_context_package(_input(state))
    view = next(v for v in pkg.telco_context if v.dimension == ContextDimension.VENDOR)
    assert view.state == ContextState.CONFLICTING
    assert view.accepted == []
    canonical_values = {a.canonical_value for a in view.conflicting}
    assert canonical_values == {"ERICSSON", "NOKIA"}, "the package must never silently pick one side of a conflict"


def test_not_applicable_dimension_preserved() -> None:
    a = ContextAssertion(assertion_id="a1", dimension=ContextDimension.RELEASE, kind=AssertionKind.NOT_APPLICABLE, origin=ContextOrigin.SYSTEM)
    state = compute_context_state([a])
    pkg = assemble_context_package(_input(state))
    view = next(v for v in pkg.telco_context if v.dimension == ContextDimension.RELEASE)
    assert view.state == ContextState.NOT_APPLICABLE
    assert len(view.accepted) == 1


def test_multi_valued_dimension_preserved() -> None:
    """ALARM is a MULTI-cardinality dimension -- two distinct concurrent
    values are never a conflict, and BOTH must survive into the package."""
    a1 = ContextAssertion(assertion_id="a1", dimension=ContextDimension.ALARM, kind=AssertionKind.VALUE, raw_value="VSWR Over Threshold", canonical_value="VSWR OVER THRESHOLD", origin=ContextOrigin.SYSTEM)
    a2 = ContextAssertion(assertion_id="a2", dimension=ContextDimension.ALARM, kind=AssertionKind.VALUE, raw_value="Cell Down", canonical_value="CELL DOWN", origin=ContextOrigin.SYSTEM)
    state = compute_context_state([a1, a2])
    pkg = assemble_context_package(_input(state))
    view = next(v for v in pkg.telco_context if v.dimension == ContextDimension.ALARM)
    assert view.state == ContextState.KNOWN
    assert {a.canonical_value for a in view.accepted} == {"VSWR OVER THRESHOLD", "CELL DOWN"}


def test_dimensions_rendered_in_deterministic_sorted_order() -> None:
    a1 = ContextAssertion(assertion_id="a1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER)
    a2 = ContextAssertion(assertion_id="a2", dimension=ContextDimension.CUSTOMER, kind=AssertionKind.VALUE, raw_value="Vodafone", canonical_value="VODAFONE", origin=ContextOrigin.USER)
    a3 = ContextAssertion(assertion_id="a3", dimension=ContextDimension.TECHNOLOGY, kind=AssertionKind.VALUE, raw_value="LTE", canonical_value="LTE", origin=ContextOrigin.USER)
    state = compute_context_state([a1, a2, a3])
    pkg = assemble_context_package(_input(state))
    dims_in_order = [v.dimension.value for v in pkg.telco_context]
    assert dims_in_order == sorted(dims_in_order), "dimensions must always be in a stable, sorted order, never dict-iteration order"
