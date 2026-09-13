"""Phase 6A.6 core test matrix -- OWNER ISOLATION (§10/§50). 6A.6's own
`assemble_context_package` accepts exactly ONE (owner_kind, owner_id)
pair, ONE optional case, ONE optional session -- there is structurally
no code path that could combine two different owners' data, since the
function signature never accepts a list of profiles/cases to merge.
These tests prove that structural guarantee behaviorally, by
constructing two independent packages for two different owners from two
independent inputs and asserting neither leaks into the other.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.cases.schemas import CaseContextSnapshot, CaseContextSnapshotItem, CaseStatus, ContextItemKind, SourceType
from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin, ContextProfileOwnerKind
from backend.context.domain.models import ContextAssertion, compute_context_state
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackageInput
from backend.knowledge.hybrid_retrieval.contracts import EvidenceSelectionResult

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _empty_evidence() -> EvidenceSelectionResult:
    return EvidenceSelectionResult(query_text="q", selected=[], selection_reason="no_candidates")


def test_session_a_context_cannot_leak_into_session_b() -> None:
    a1 = ContextAssertion(assertion_id="a1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER)
    a2 = ContextAssertion(assertion_id="a2", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Nokia", canonical_value="NOKIA", origin=ContextOrigin.USER)

    pkg_a = assemble_context_package(ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION, owner_id="session-A", session_id="session-A",
        telco_context_state=compute_context_state([a1]), evidence_selection=_empty_evidence(),
    ))
    pkg_b = assemble_context_package(ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION, owner_id="session-B", session_id="session-B",
        telco_context_state=compute_context_state([a2]), evidence_selection=_empty_evidence(),
    ))

    assert pkg_a.owner_id == "session-A"
    assert pkg_b.owner_id == "session-B"
    values_a = {a.canonical_value for v in pkg_a.telco_context for a in v.accepted}
    values_b = {a.canonical_value for v in pkg_b.telco_context for a in v.accepted}
    assert values_a == {"ERICSSON"}
    assert values_b == {"NOKIA"}
    assert values_a.isdisjoint(values_b)


def test_case_a_context_cannot_leak_into_case_b() -> None:
    snapshot_a = CaseContextSnapshot(case_id="CASE-A", title="A", status=CaseStatus.OPEN, problem_statement="p-a",
        items=[CaseContextSnapshotItem(kind=ContextItemKind.OBSERVATION, content="fact only in case A", source_type=SourceType.SYSTEM, created_at=_NOW)])
    snapshot_b = CaseContextSnapshot(case_id="CASE-B", title="B", status=CaseStatus.OPEN, problem_statement="p-b",
        items=[CaseContextSnapshotItem(kind=ContextItemKind.OBSERVATION, content="fact only in case B", source_type=SourceType.SYSTEM, created_at=_NOW)])

    pkg_a = assemble_context_package(ContextPackageInput(owner_kind=ContextProfileOwnerKind.CASE, owner_id="CASE-A", case_id="CASE-A", case_context=snapshot_a, evidence_selection=_empty_evidence()))
    pkg_b = assemble_context_package(ContextPackageInput(owner_kind=ContextProfileOwnerKind.CASE, owner_id="CASE-B", case_id="CASE-B", case_context=snapshot_b, evidence_selection=_empty_evidence()))

    assert pkg_a.case_context.case_id == "CASE-A"
    assert pkg_b.case_context.case_id == "CASE-B"
    contents_a = {i.content for i in pkg_a.case_context.items}
    contents_b = {i.content for i in pkg_b.case_context.items}
    assert "fact only in case B" not in contents_a
    assert "fact only in case A" not in contents_b


def test_case_context_never_silently_mixed_with_unrelated_session() -> None:
    """A CASE-owned package must never pick up a session_id belonging to
    a different, unrelated session merely because both happen to be
    assembled in the same process/test run."""
    snapshot = CaseContextSnapshot(case_id="CASE-X", title="t", status=CaseStatus.OPEN, problem_statement="p", items=[])
    pkg = assemble_context_package(ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.CASE, owner_id="CASE-X", case_id="CASE-X",
        session_id=None,  # no session supplied -- must stay None, never inferred
        case_context=snapshot, evidence_selection=_empty_evidence(),
    ))
    assert pkg.session_id is None
    assert pkg.case_id == "CASE-X"
