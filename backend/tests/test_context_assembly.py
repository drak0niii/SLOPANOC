"""Tests for backend/context/assembly.py -- Context Engineering Layer broker.
"""
from __future__ import annotations

from backend.context.assembly import (
    ContextDomain,
    ContextItem,
    ContextEngineeringBroker,
    AssembledContext,
)
from backend.cases.troubleshooting_state import TroubleshootingState, TroubleshootingStatus


def test_context_item_instantiation() -> None:
    item = ContextItem(
        domain=ContextDomain.KNOWLEDGE,
        source_id="MOP-ERICSSON-4G-VSWR",
        title="VSWR Over Threshold Procedure",
        content="Do not restart board when VSWR alarm is active.",
        metadata={"vendor": "ericsson", "technology": "4g"},
        relevance_score=0.95,
        is_authoritative=True,
    )
    assert item.domain == ContextDomain.KNOWLEDGE
    assert item.source_id == "MOP-ERICSSON-4G-VSWR"
    assert item.is_authoritative is True
    assert item.relevance_score == 0.95


def test_context_engineering_broker_assemble() -> None:
    broker = ContextEngineeringBroker(max_context_items=5)

    km_item = ContextItem(
        domain=ContextDomain.KNOWLEDGE,
        source_id="MOP-001",
        title="Baseband Reset Procedure",
        content="Execute restart board <slot> after confirming DUS.",
        relevance_score=0.9,
    )
    op_item = ContextItem(
        domain=ContextDomain.OPERATIONAL,
        source_id="TEAMS-MSG-1789",
        title="Teams Message from John",
        content="Alarm VSWR is firing on Cell 42.",
        relevance_score=0.8,
    )
    exp_item = ContextItem(
        domain=ContextDomain.EXPERIENCE_MEMORY,
        source_id="EXP-RCA-2025-09",
        title="Prior VSWR incident on Node 12",
        content="Turned out to be loose feeder cable connector.",
        relevance_score=0.7,
    )

    ts = TroubleshootingState(
        fault_id="FAULT-RAN-9901",
        status=TroubleshootingStatus.INVESTIGATING,
        symptom_summary="High VSWR observed on Cell 42",
        working_hypothesis="Damaged RF jumper or connector",
        competing_hypotheses=["Faulty transceiver", "Antenna tilt issue"],
    )
    case_info = {"title": "Outage on Node B71", "status": "INVESTIGATING"}

    assembled = broker.assemble(
        query="What is the next diagnostic step for Cell 42?",
        knowledge_items=[km_item],
        operational_items=[op_item],
        case_details=case_info,
        troubleshooting_state=ts,
        experience_items=[exp_item],
    )

    assert assembled.total_items() == 5
    assert len(assembled.knowledge_context) == 1
    assert len(assembled.operational_context) == 1
    assert len(assembled.experience_memory) == 1
    assert assembled.case_context == case_info
    assert assembled.troubleshooting_state.fault_id == "FAULT-RAN-9901"

    prompt = assembled.format_prompt_block()
    assert "Case & Troubleshooting State" in prompt
    assert "FAULT-RAN-9901" in prompt
    assert "Governed Knowledge Context" in prompt
    assert "MOP-001" in prompt
    assert "Operational Context (Teams & Visual Media)" in prompt
    assert "Historical Experience Memory" in prompt


def test_context_engineering_broker_re_evaluate() -> None:
    broker = ContextEngineeringBroker()

    km1 = ContextItem(
        domain=ContextDomain.KNOWLEDGE,
        source_id="MOP-001",
        title="Procedure A",
        content="Check optical transceiver power levels.",
        relevance_score=0.5,
    )
    km2 = ContextItem(
        domain=ContextDomain.KNOWLEDGE,
        source_id="MOP-002",
        title="Procedure B",
        content="Reboot microwave link controller.",
        relevance_score=0.5,
    )

    assembled = broker.assemble(
        query="Investigate link degradation",
        knowledge_items=[km1, km2],
    )

    # New evidence indicates optical transceiver is suspect
    re_evaluated = broker.re_evaluate(assembled, new_evidence_keys=["transceiver", "MOP-001"])

    # km1 relevance boosted
    top_km = re_evaluated.knowledge_context[0]
    assert top_km.source_id == "MOP-001"
    assert top_km.relevance_score > 0.5
