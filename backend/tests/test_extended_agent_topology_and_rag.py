"""Tests for Problem Manager, Automated Operations Engineer, Troubleshooting State,
Clarification Service, and Hybrid Search Reranking.
"""
from __future__ import annotations

import pytest
from backend.agents.problem_manager.agent import (
    problem_manager,
    ProblemManagerRequest,
    ProblemManagerResponse,
)
from backend.agents.automated_operations_engineer.agent import (
    automated_operations_engineer,
    AutomatedOperationsRequest,
    AutomatedOperationsResponse,
)
from backend.cases.troubleshooting_state import (
    TroubleshootingState,
    TroubleshootingStatus,
    DiagnosticCheckRecord,
)
from backend.api.clarification_service import (
    PendingClarificationRequest,
    ClarificationType,
    get_pending_clarification,
    set_pending_clarification,
    clear_pending_clarification,
)
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalItem
from backend.knowledge.domain.contracts import KnowledgeEvidenceReference
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeVersion, KnowledgeSource
from backend.knowledge.domain.applicability import ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.retrieval.reranking import compute_reciprocal_rank_fusion, HeuristicReranker


def test_problem_manager_agent_invariants() -> None:
    assert problem_manager.name == "problem_manager"
    assert problem_manager.input_schema == ProblemManagerRequest
    assert problem_manager.output_schema == ProblemManagerResponse
    assert "ITIL Problem Management" in problem_manager.description


def test_automated_operations_engineer_invariants() -> None:
    assert automated_operations_engineer.name == "automated_operations_engineer"
    assert automated_operations_engineer.input_schema == AutomatedOperationsRequest
    assert automated_operations_engineer.output_schema == AutomatedOperationsResponse
    assert "Level 1 Operations" in automated_operations_engineer.description


def test_troubleshooting_state_model() -> None:
    state = TroubleshootingState(
        fault_id="fault-ran-vswr-01",
        status=TroubleshootingStatus.INVESTIGATING,
        symptom_summary="High VSWR detected on Sector 2",
        working_hypothesis="Faulty jumper cable or feeder connection",
        competing_hypotheses=["Damaged RRU port", "Lightning strike surge"],
        verified_evidence_ids=["teams:178911436", "km:vswr:v1:sec2"],
    )
    assert state.fault_id == "fault-ran-vswr-01"
    assert state.status == TroubleshootingStatus.INVESTIGATING
    assert len(state.competing_hypotheses) == 2


def test_clarification_continuity_state_machine() -> None:
    session_state: dict = {}
    assert get_pending_clarification(session_state) is None

    req = PendingClarificationRequest(
        request_id="req-clarify-001",
        originating_agent="technical_authority_engineer",
        clarification_type=ClarificationType.PARAMETER_VALUE,
        prompt_to_user="Which board slot is affected (e.g. SLOT-4 or SLOT-5)?",
        expected_field_name="board_slot",
        context_payload={"fault_id": "fault-ran-01", "action": "restart_board"},
    )
    set_pending_clarification(session_state, req)

    retrieved = get_pending_clarification(session_state)
    assert retrieved is not None
    assert retrieved.request_id == "req-clarify-001"
    assert retrieved.expected_field_name == "board_slot"

    cleared = clear_pending_clarification(session_state)
    assert cleared is not None
    assert cleared.request_id == "req-clarify-001"
    assert get_pending_clarification(session_state) is None


@pytest.mark.asyncio
async def test_hybrid_search_reranking_reciprocal_rank_fusion() -> None:
    ref_a = KnowledgeEvidenceReference(
        knowledge_id="mop-vswr-01",
        version_label="v1",
        section_id="sec-check",
        source_system="slopanoc_generic_km",
        source_id="src-vswr-01",
    )
    k_obj_a = KnowledgeObject(
        knowledge_id="mop-vswr-01",
        version=KnowledgeVersion(label="v1"),
        document_type=KnowledgeDocumentType.MOP,
        lifecycle_status=LifecycleStatus.APPROVED,
        title="VSWR Troubleshooting MOP",
        source=KnowledgeSource(source_system="slopanoc_generic_km", source_id="src-vswr-01"),
        sections=[],
    )
    sec_a = KnowledgeSection(
        section_id="sec-check",
        knowledge_id="mop-vswr-01",
        sequence=1,
        heading="Check VSWR Jumper",
        content="Inspect the jumper cable connecting RRU to antenna.",
    )
    k_obj_a.sections = [sec_a]

    item_sparse = KnowledgeRetrievalItem(
        knowledge_id="mop-vswr-01",
        document_type=KnowledgeDocumentType.MOP,
        title="VSWR Troubleshooting MOP",
        version_label="v1",
        lifecycle_status=LifecycleStatus.APPROVED,
        section=sec_a,
        source=KnowledgeSource(source_system="slopanoc_generic_km", source_id="src-vswr-01"),
        relevance_score=0.85,
        applicability_outcome=ApplicabilityOutcome.MATCH,
        is_derived=False,
    )

    item_dense = KnowledgeRetrievalItem(
        knowledge_id="mop-vswr-01",
        document_type=KnowledgeDocumentType.MOP,
        title="VSWR Troubleshooting MOP",
        version_label="v1",
        lifecycle_status=LifecycleStatus.APPROVED,
        section=sec_a,
        source=KnowledgeSource(source_system="slopanoc_generic_km", source_id="src-vswr-01"),
        relevance_score=0.92,
        applicability_outcome=ApplicabilityOutcome.MATCH,
        is_derived=False,
    )

    fused = compute_reciprocal_rank_fusion([item_sparse], [item_dense])
    assert len(fused) == 1
    # RRF score = 1/(60+1) + 1/(60+1) = 2/61 ~ 0.03278
    assert fused[0].relevance_score > 0.03

    reranker = HeuristicReranker()
    reranked = await reranker.rerank("vswr jumper", fused, top_k=1)
    assert len(reranked) == 1
    assert reranked[0].knowledge_id == "mop-vswr-01"
