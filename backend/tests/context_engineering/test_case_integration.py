"""Phase 6A.6: Case Context integration (§14). 6A.6 accepts the ALREADY-
EXISTING, already-budgeted `CaseContextSnapshot` (`backend.cases
.schemas`) verbatim -- it never redefines a Case model, never re-derives
its own truncation, and never touches `backend.cases.snapshot`/
`backend.cases.service` (proven separately by the dependency-boundary
test)."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.cases.schemas import CaseContextSnapshot, CaseContextSnapshotItem, CaseStatus, ContextItemKind, SourceType
from backend.context.domain.enums import ContextProfileOwnerKind
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackageInput
from backend.knowledge.hybrid_retrieval.contracts import EvidenceSelectionResult

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _empty_evidence() -> EvidenceSelectionResult:
    return EvidenceSelectionResult(query_text="q", selected=[], selection_reason="no_candidates")


def test_case_context_passed_through_verbatim() -> None:
    snapshot = CaseContextSnapshot(
        case_id="CASE-1",
        title="VSWR investigation",
        status=CaseStatus.INVESTIGATING,
        problem_statement="High VSWR alarm on cell 12.",
        items=[
            CaseContextSnapshotItem(kind=ContextItemKind.OBSERVATION, content="VSWR alarm active since 08:00", source_type=SourceType.SYSTEM, created_at=_NOW),
            CaseContextSnapshotItem(kind=ContextItemKind.HYPOTHESIS, content="Possible feeder cable fault", source_type=SourceType.AGENT, created_at=_NOW),
        ],
        context_truncated=False,
        total_item_count=2,
        included_item_count=2,
    )
    input_ = ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.CASE,
        owner_id="CASE-1",
        case_id="CASE-1",
        case_context=snapshot,
        evidence_selection=_empty_evidence(),
    )
    pkg = assemble_context_package(input_)
    assert pkg.case_context is not None
    assert pkg.case_context.case_id == "CASE-1"
    assert pkg.case_context.status == CaseStatus.INVESTIGATING
    assert len(pkg.case_context.items) == 2
    assert pkg.case_context.items[0].content == "VSWR alarm active since 08:00"


def test_case_context_truncation_flag_never_hidden() -> None:
    snapshot = CaseContextSnapshot(
        case_id="CASE-2", title="t", status=CaseStatus.OPEN, problem_statement="p",
        items=[], context_truncated=True, total_item_count=50, included_item_count=10,
    )
    input_ = ContextPackageInput(owner_kind=ContextProfileOwnerKind.CASE, owner_id="CASE-2", case_context=snapshot, evidence_selection=_empty_evidence())
    pkg = assemble_context_package(input_)
    assert pkg.case_context.context_truncated is True
    assert pkg.case_context.total_item_count == 50
    assert pkg.case_context.included_item_count == 10


def test_no_case_linked_produces_explicit_none_never_a_guess() -> None:
    input_ = ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1", evidence_selection=_empty_evidence())
    pkg = assemble_context_package(input_)
    assert pkg.case_context is None
    assert pkg.case_id is None
