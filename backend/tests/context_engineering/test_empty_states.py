"""Phase 6A.6 core test matrix -- EMPTY STATES (§36/§52). Empty evidence,
empty operational observations, missing case data, and an empty TELCO
state must all produce a VALID, explicit `ContextPackage` -- never a
guess, never a fallback to a broader search, never a raised exception.
"""
from __future__ import annotations

from backend.context.domain.enums import ContextProfileOwnerKind
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackageInput
from backend.knowledge.hybrid_retrieval.contracts import EvidenceSelectionResult


def test_empty_selected_evidence_produces_valid_package() -> None:
    sel = EvidenceSelectionResult(query_text="q", selected=[], selection_reason="no_candidates")
    pkg = assemble_context_package(ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="s", evidence_selection=sel))
    assert pkg.evidence.items == []
    assert pkg.evidence.selected_evidence_count == 0
    assert pkg.evidence.source_evidence_count == 0
    assert pkg.evidence.derived_evidence_count == 0
    assert pkg.content_fingerprint != ""


def test_empty_operational_observations_produces_valid_package() -> None:
    sel = EvidenceSelectionResult(query_text="q", selected=[], selection_reason="no_candidates")
    pkg = assemble_context_package(ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="s", operational_observations=[], evidence_selection=sel))
    assert pkg.operational_observations == []
    assert pkg.assembly_trace.operational_observation_count == 0


def test_empty_telco_context_produces_valid_package() -> None:
    sel = EvidenceSelectionResult(query_text="q", selected=[], selection_reason="no_candidates")
    pkg = assemble_context_package(ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="s", telco_context_state={}, evidence_selection=sel))
    assert pkg.telco_context == []
    assert pkg.assembly_trace.telco_dimensions_included == 0


def test_missing_optional_case_data_produces_valid_package() -> None:
    sel = EvidenceSelectionResult(query_text="q", selected=[], selection_reason="no_candidates")
    pkg = assemble_context_package(ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="s", case_context=None, case_id=None, evidence_selection=sel))
    assert pkg.case_context is None
    assert pkg.case_id is None


def test_fully_minimal_input_never_raises() -> None:
    """The absolute minimum legal input -- an owner and an empty
    evidence selection -- must assemble cleanly, with no exception, no
    guessed default anywhere."""
    sel = EvidenceSelectionResult(query_text="", selected=[], selection_reason="no_candidates")
    pkg = assemble_context_package(ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="s", evidence_selection=sel))
    assert pkg.context_schema_version == "1.0"
    assert pkg.provenance_manifest == []
