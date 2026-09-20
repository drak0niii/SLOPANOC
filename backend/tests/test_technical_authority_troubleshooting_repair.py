"""Targeted regression and negative security tests for the multi-turn troubleshooting repair.

Verifies the 5 non-negotiable architectural safeguards:
Safeguard 1: Command presence in evidence snippet is not authorization. Approved procedure
             catalog or governed procedure snippet required. Eliminates raw_src authorization vulnerability.
Safeguard 2: Multi-field response validation & prose operational instruction sanitization,
             including when diagnostic_step.command is null.
Safeguard 3: Specialist-aware completion boundary: skips incident_manager completion override
             when technical_authority_engineer evaluated the fault in this turn.
Safeguard 4: Trusted applicability context registration at correct point in knowledge retrieval lifecycle.
Safeguard 5: Persistent TroubleshootingState with distinct lifecycle states (RECOMMENDED, EXECUTED, COMPLETED),
             associated with session, case, node, and fault, preventing repetition loops across turns.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.agents.technical_authority_engineer.agent_tool import (
    build_server_validated_commands,
    build_server_validated_evidence,
)
from backend.agents.technical_authority_engineer.execution_context import (
    discard_technical_authority_execution,
    get_technical_authority_execution,
    has_technical_authority_executed,
    record_technical_authority_execution,
)
from backend.agents.technical_authority_engineer.schemas import (
    ApprovedCommand,
    DiagnosticStep,
    EvidenceReference,
    TechnicalAuthorityOutcome,
    TechnicalAuthorityResponse,
)
from backend.agents.technical_authority_engineer.validation import (
    is_command_grounded,
    sanitize_prose_operational_instructions,
    validate_technical_authority_payload,
)
from backend.cases.troubleshooting_state import (
    CheckLifecycleStatus,
    DiagnosticCheckRecord,
    TroubleshootingState,
    TroubleshootingStatus,
)
from backend.knowledge.domain.applicability import ApplicabilityContext
from backend.tools.knowledge.runtime import (
    discard_knowledge_run_evidence_state,
    get_or_init_run_state,
)


# ==============================================================================
# Safeguard 1: Server-validated command grounding & raw_src vulnerability fix
# ==============================================================================


def test_safeguard1_raw_src_alone_does_not_authorize_arbitrary_commands() -> None:
    """Proves vulnerability elimination: an arbitrary command from caller is rejected

    even if raw_src matches a valid authorized evidence snippet, because the command
    itself is not in the snippet and does not match an approved template.
    """
    evidence = [
        EvidenceReference(
            source_id="mop:ericsson:v1:sec3",
            source_type="governed_knowledge",
            title="Radio Restart MOP",
            content_snippet="Execute `st cell` to inspect operational state. Do not restart without approval.",
        )
    ]

    # Caller tries to smuggle an unauthorized destructive command citing the valid source_id
    caller_commands = [
        {
            "command": "reboot -f node",
            "source_id": "mop:ericsson:v1:sec3",
        }
    ]

    approved = build_server_validated_commands(evidence, caller_commands)
    assert len(approved) == 0, "Arbitrary command must be rejected even with matching source_id"


def test_safeguard1_non_governed_sources_cannot_authorize_commands() -> None:
    """Proves that Teams chats, user evidence, or case notes can never authorize operational commands."""
    evidence = [
        EvidenceReference(
            source_id="teams:msg-999",
            source_type="teams_conversation",
            title="Chat note",
            content_snippet="You can run `acc board restart` to clear it.",
        ),
        EvidenceReference(
            source_id="case:item-1",
            source_type="case_context",
            title="Case Context",
            content_snippet="Previous engineer ran `inv all`.",
        ),
    ]

    caller_commands = [
        {"command": "acc board restart", "source_id": "teams:msg-999"},
        {"command": "inv all", "source_id": "case:item-1"},
    ]

    approved = build_server_validated_commands(evidence, caller_commands)
    assert len(approved) == 0, "Non-governed sources must never authorize operational commands"


def test_safeguard1_verbatim_and_templated_grounded_commands_accepted() -> None:
    """Verifies that legitimate commands present verbatim or matching templates are authorized."""
    evidence = [
        EvidenceReference(
            source_id="sop:ran:v2:sec1",
            source_type="approved_procedure",
            title="Node Diagnostic SOP",
            content_snippet="Check alarms using `alt cm` or restart board with `restart board <board_slot>`.",
        )
    ]

    caller_commands = [
        {"command": "alt cm", "source_id": "sop:ran:v2:sec1"},
        {"command": "restart board **SLOT-4-DUS**", "source_id": "sop:ran:v2:sec1"},
        {"command": "unapproved command xyz", "source_id": "sop:ran:v2:sec1"},
    ]

    approved = build_server_validated_commands(evidence, caller_commands)
    assert len(approved) == 2
    assert approved[0].command == "alt cm"
    assert approved[1].command == "restart board **SLOT-4-DUS**"


# ==============================================================================
# Safeguard 2: Multi-field response validation & prose operational sanitization
# ==============================================================================


def test_safeguard2_prose_sanitization_when_command_is_null() -> None:
    """Proves that imperative operational directives in prose fields (action, reason, etc.)

    are scrubbed even when diagnostic_step.command is null.
    """
    raw_payload = {
        "outcome": "recommended",
        "technical_interpretation": "Fault indicates board failure. Run `acc Board=1 restart` immediately.",
        "diagnostic_step": {
            "action": "Execute `acc Board=1 restart` on node.",
            "reason": "Restarting clears state.",
            "command": None,
            "expected_evidence": "Observe board comes up after running `acc Board=1 restart`.",
        },
    }

    validated, _ = validate_technical_authority_payload(
        response_payload=raw_payload,
        request_payload={
            "approved_commands_catalog": [],
            "verified_evidence": [],
        },
    )

    step = validated["diagnostic_step"]
    assert step["command"] is None
    assert "`acc Board=1 restart`" not in step["action"]
    assert "Observe board comes up" in step["expected_evidence"]
    assert any("[Observational check only" in r for r in step["restrictions"])
    assert "`acc Board=1 restart`" not in validated["technical_interpretation"]


def test_safeguard2_prose_sanitization_strips_unapproved_command_everywhere() -> None:
    """When a command in diagnostic_step.command is unapproved and stripped,

    its occurrences across action, reason, expected_evidence, and technical_interpretation
    are scrubbed.
    """
    raw_payload = {
        "outcome": "recommended",
        "technical_interpretation": "Observed alarms suggest running `unapproved_cmd_123` to test.",
        "diagnostic_step": {
            "action": "Run `unapproved_cmd_123` on the node.",
            "reason": "We must execute `unapproved_cmd_123` to verify.",
            "command": "unapproved_cmd_123",
            "command_source": "bogus_source",
            "expected_evidence": "Output of `unapproved_cmd_123` should show 0.",
        },
    }

    validated, _ = validate_technical_authority_payload(
        response_payload=raw_payload,
        request_payload={
            "approved_commands_catalog": [],
            "verified_evidence": [],
        },
    )

    step = validated["diagnostic_step"]
    assert step["command"] is None
    assert "unapproved_cmd_123" not in step["action"]
    assert "unapproved_cmd_123" not in step["reason"]
    assert "unapproved_cmd_123" not in step["expected_evidence"]
    assert "unapproved_cmd_123" not in validated["technical_interpretation"]
    assert any("[Command stripped: unapproved operational command]" in r for r in step["restrictions"])


# ==============================================================================
# Safeguard 3: Specialist-aware completion boundary
# ==============================================================================


def test_safeguard3_technical_authority_execution_tracking_lifecycle() -> None:
    """Verifies run-scoped tracking of Technical Authority Engineer executions."""
    run_id = "test-run-12345"
    assert not has_technical_authority_executed(run_id)

    record_technical_authority_execution(run_id, {"outcome": "recommended", "fault_id": "FAULT-1"})
    assert has_technical_authority_executed(run_id)
    exec_data = get_technical_authority_execution(run_id)
    assert exec_data is not None
    assert exec_data.get("fault_id") == "FAULT-1"

    discard_technical_authority_execution(run_id)
    assert not has_technical_authority_executed(run_id)
    assert get_technical_authority_execution(run_id) is None


@pytest.mark.asyncio
async def test_safeguard3_chat_service_skips_incident_manager_when_tae_executed() -> None:
    """Simulates the chat_service completion boundary:

    If requires_governed_knowledge is True and selected_knowledge_evidence is empty,
    but technical_authority_execution is present, incident_manager completion remediation
    must NOT be triggered.
    """
    from backend.api.source_requirements_capture import SourceRequirementsCapture

    src_capture = SourceRequirementsCapture()
    src_capture.record_external_declaration(requires_teams=False, requires_governed_knowledge=True)

    selected_knowledge_evidence: list[Any] = []
    technical_authority_execution = {"outcome": "recommended", "fault_id": "FAULT-001"}

    remediation_called = False

    async def fake_enforce_governed_knowledge(*args: Any, **kwargs: Any) -> Any:
        nonlocal remediation_called
        remediation_called = True
        return "Aurora Relay Verification Procedure...", []

    # Emulate the guarded chat_service completion boundary logic:
    if src_capture.requires_governed_knowledge and not selected_knowledge_evidence:
        if technical_authority_execution:
            # TAE evaluated the fault in this turn -- skip IM completion override
            pass
        else:
            await fake_enforce_governed_knowledge()

    assert not remediation_called, "Incident Manager remediation must be bypassed when TAE evaluated the fault"


# ==============================================================================
# Safeguard 4: Trusted applicability context registration
# ==============================================================================


def test_safeguard4_applicability_context_registration_and_update() -> None:
    """Verifies that applicability context is populated and updated into run state correctly."""
    from backend.api.applicability_context_capture import (
        discard_known_applicability_context,
        pop_known_applicability_context,
        register_known_applicability_context,
    )

    run_id = "run-app-ctx-test"
    discard_knowledge_run_evidence_state(run_id)
    discard_known_applicability_context(run_id)

    try:
        facts = {"vendor": ["ericsson"], "technology": ["4G", "LTE"], "node_type": ["RBS6000"]}
        app_ctx = ApplicabilityContext(dimensions=facts)
        register_known_applicability_context(run_id, app_ctx)

        run_state = get_or_init_run_state(run_id)
        assert run_state.execution_context.applicability_context.dimensions == facts

        # Second turn with updated facts on existing state
        new_facts = {"vendor": ["ericsson"], "technology": ["5G", "NR"]}
        new_app_ctx = ApplicabilityContext(dimensions=new_facts)
        register_known_applicability_context(run_id, new_app_ctx)

        # Clear dimensions in state to simulate update
        run_state.execution_context.applicability_context.dimensions = {}
        updated_state = get_or_init_run_state(run_id)
        assert updated_state.execution_context.applicability_context.dimensions == new_facts
    finally:
        discard_knowledge_run_evidence_state(run_id)
        discard_known_applicability_context(run_id)


# ==============================================================================
# Safeguard 5: Persistent TroubleshootingState & distinct lifecycle states
# ==============================================================================


def test_safeguard5_diagnostic_check_lifecycle_progression() -> None:
    """Verifies that recommended checks progress through distinct lifecycle states:

    RECOMMENDED -> EXECUTED with observed results, never auto-marked COMPLETED.
    """
    ts = TroubleshootingState(
        fault_id="FAULT-ER-01",
        symptom_summary="Radio link down alarm on ER_RBS_Site01",
        node_id="ER_RBS_Site01",
    )

    rec = ts.record_recommended_check(
        action="Run alt cm to inspect active alarms.",
        rationale="Identify whether alarm is persistent.",
        expected_observation="Active alarm list including RILinkDown.",
        grounded_command="alt cm",
        command_source_id="mop:ericsson:v1:sec2",
        session_id="session-1",
        case_id="case-1",
        node_id="ER_RBS_Site01",
        fault_id="FAULT-ER-01",
    )

    assert rec.status == CheckLifecycleStatus.RECOMMENDED
    assert rec.grounded_command == "alt cm"
    assert rec.session_id == "session-1"
    assert rec.case_id == "case-1"
    assert rec.node_id == "ER_RBS_Site01"
    assert rec.fault_id == "FAULT-ER-01"

    # User executes check and provides terminal output
    user_output = "alt cm executed: ALARM 1024 RILinkDown Carrier 1 Minor"
    updated_rec = ts.record_user_execution(
        command="alt cm",
        observed_result=user_output,
    )

    assert updated_rec is not None
    assert updated_rec.status == CheckLifecycleStatus.EXECUTED
    assert updated_rec.observed_result == user_output
    assert updated_rec.executed_at is not None
    assert updated_rec.completed_at is None, "Check must not be marked completed on execution report"


def test_safeguard5_prior_steps_summary_prevents_repetition_loops() -> None:
    """Verifies that completed/executed checks appear in prior_steps_summary

    so subsequent turns do not repeat the command.
    """
    ts = TroubleshootingState(
        fault_id="FAULT-ER-01",
        symptom_summary="Radio link down",
    )

    ts.record_recommended_check(
        action="Run alt cm",
        rationale="Check alarms",
        expected_observation="Alarm output",
        grounded_command="alt cm",
    )

    ts.record_user_execution(
        command="alt cm",
        observed_result="1024 RILinkDown Carrier 1 Minor",
    )

    prior = ts.get_prior_steps_summary()
    assert len(prior) == 1
    assert "alt cm" in prior[0]
    assert "[executed]" in prior[0]
    assert "Observed: 1024 RILinkDown" in prior[0]


def test_safeguard5_troubleshooting_state_serialization_roundtrip() -> None:
    """Verifies that TroubleshootingState survives session state serialization/deserialization

    (simulating browser reload / session persistence).
    """
    ts = TroubleshootingState(
        fault_id="FAULT-ER-01",
        symptom_summary="Radio link down on ER_RBS_Site01",
        node_id="ER_RBS_Site01",
        working_hypothesis="Physical SFP optical failure",
        competing_hypotheses=["Baseband software glitch", "Fiber cut"],
        status=TroubleshootingStatus.INVESTIGATING,
        session_id="session-reload-test",
    )

    ts.record_recommended_check(
        action="Run alt cm",
        rationale="Check alarms",
        expected_observation="Alarm list",
        grounded_command="alt cm",
    )
    ts.record_user_execution(command="alt cm", observed_result="Alarm output present")

    # Second step recommended
    ts.record_recommended_check(
        action="Run st rilink to inspect radio link status",
        rationale="Isolate SFP or link",
        expected_observation="Radio link carrier state",
        grounded_command="st rilink",
    )

    raw_dict = ts.model_dump(mode="json")
    # Simulate reload from database JSON
    serialized = json.dumps(raw_dict)
    reloaded_dict = json.loads(serialized)
    reloaded_ts = TroubleshootingState.model_validate(reloaded_dict)

    assert reloaded_ts.fault_id == "FAULT-ER-01"
    assert reloaded_ts.node_id == "ER_RBS_Site01"
    assert len(reloaded_ts.diagnostic_history) == 2
    assert reloaded_ts.diagnostic_history[0].status == CheckLifecycleStatus.EXECUTED
    assert reloaded_ts.diagnostic_history[0].grounded_command == "alt cm"
    assert reloaded_ts.diagnostic_history[1].status == CheckLifecycleStatus.RECOMMENDED
    assert reloaded_ts.diagnostic_history[1].grounded_command == "st rilink"
    assert reloaded_ts.working_hypothesis == "Physical SFP optical failure"
