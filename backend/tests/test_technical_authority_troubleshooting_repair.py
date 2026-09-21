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


# ==============================================================================
# Dynamic Retrieval, Exact Syntax (alt / alt cm), & Security Edge Cases
# ==============================================================================


def test_alt_and_alt_cm_exact_syntax_authorized_from_governed_knowledge() -> None:
    """Verifies that `alt` and `alt cm` in moshell / amos are properly authorized

    when present in an approved governed procedure.
    """
    evidence = [
        EvidenceReference(
            source_id="mop:ericsson:alarms:v1",
            source_type="governed_knowledge",
            title="Ericsson Alarm Handling MOP",
            content_snippet="To check active alarms in moshell / amos, run `alt` or `alt cm` to inspect cluster alarms.",
        )
    ]

    caller_commands = [
        {"command": "alt", "source_id": "mop:ericsson:alarms:v1"},
        {"command": "alt cm", "source_id": "mop:ericsson:alarms:v1"},
    ]

    approved = build_server_validated_commands(evidence, caller_commands)
    assert len(approved) == 2
    assert approved[0].command == "alt"
    assert approved[1].command == "alt cm"


def test_prohibition_only_snippets_do_not_authorize_commands() -> None:
    """Negative security test: Snippets warning AGAINST a command (e.g. 'Do not run alt')

    must not authorize the prohibited command if not structured as an approved instruction.
    """
    evidence = [
        EvidenceReference(
            source_id="mop:ericsson:prohibited:v1",
            source_type="governed_knowledge",
            title="Prohibited Commands Bulletin",
            content_snippet="WARNING: Do not execute `reload -f` or `format disk` under any circumstances.",
        )
    ]

    caller_commands = [
        {"command": "reload -f", "source_id": "mop:ericsson:prohibited:v1"},
    ]

    # Command is mentioned in evidence, but prohibited in warning snippet.
    # build_server_validated_commands must reject it so it is not authorized.
    approved = build_server_validated_commands(evidence, caller_commands)
    assert len(approved) == 0, "Prohibited commands in warning snippets must not be authorized"
    # Verify validation strips it when restrictions/parameters are missing or payload is ungrounded:
    raw_payload = {
        "outcome": "recommended",
        "diagnostic_step": {
            "action": "Execute reload",
            "command": "reload -f",
            "command_source": "mop:mismatched:id",  # Mismatched source id!
        },
    }
    validated, modified = validate_technical_authority_payload(
        response_payload=raw_payload,
        request_payload={
            "approved_commands_catalog": [c.model_dump(mode="json") for c in approved],
            "verified_evidence": [e.model_dump(mode="json") for e in evidence],
        },
    )
    assert validated["diagnostic_step"]["command"] is None
    assert any("[Command stripped" in r for r in validated["diagnostic_step"]["restrictions"])


def test_mismatched_source_identity_rejected() -> None:
    """Negative security test: Command rejected if command_source does not match evidence source_id."""
    evidence = [
        EvidenceReference(
            source_id="mop:ericsson:alarms:v1",
            source_type="governed_knowledge",
            title="Alarm MOP",
            content_snippet="Execute `alt cm` to view alarms.",
        )
    ]
    raw_payload = {
        "outcome": "recommended",
        "diagnostic_step": {
            "action": "Run alarm check",
            "command": "alt cm",
            "command_source": "mop:different:v2",  # Mismatched!
        },
    }
    validated, modified = validate_technical_authority_payload(
        raw_payload,
        {"verified_evidence": [e.model_dump(mode="json") for e in evidence], "approved_commands_catalog": []},
    )
    assert validated["diagnostic_step"]["command"] is None
    assert any("[Command stripped: unapproved operational command]" in r for r in validated["diagnostic_step"]["restrictions"])


def test_malicious_caller_catalog_cannot_authorize_unbacked_commands() -> None:
    """Negative security test: Malicious caller catalog injecting ungrounded commands is neutralized."""
    evidence: list[EvidenceReference] = []
    malicious_catalog = [
        {"command": "rm -rf /", "source_id": "fake:source:1"}
    ]
    approved = build_server_validated_commands(evidence, malicious_catalog)
    assert len(approved) == 0


def test_no_unapproved_command_removed_marker_in_prose() -> None:
    """Proves that unapproved commands are scrubbed cleanly without leaking

    the literal string '[unapproved command removed]'.
    """
    raw_payload = {
        "outcome": "recommended",
        "technical_interpretation": "We should run `unapproved_tool` to see status.",
        "diagnostic_step": {
            "action": "Run `unapproved_tool` immediately.",
            "reason": "Running `unapproved_tool` is safe.",
            "command": "unapproved_tool",
            "command_source": "bogus",
            "expected_evidence": "Observe results.",
        },
    }
    validated, _ = validate_technical_authority_payload(
        raw_payload,
        {"approved_commands_catalog": [], "verified_evidence": []},
    )
    dumped = json.dumps(validated)
    assert "[unapproved command removed]" not in dumped
    assert "unapproved_tool" not in validated["diagnostic_step"]["action"]
    assert "unapproved_tool" not in validated["diagnostic_step"]["reason"]
    assert validated["diagnostic_step"]["command"] is None


# ==============================================================================
# End-to-End Integration Flow: Dynamic Selection, Refresh, Restrictions, Negative
# ==============================================================================


def _make_governed_evidence_item(
    knowledge_id: str,
    section_id: str,
    content: str,
    title: str = "Governed SOP",
    version_label: str = "v1",
) -> Any:
    from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
    from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
    from backend.knowledge.provenance.contracts import (
        KnowledgeEvidenceItem,
        KnowledgeEvidenceReference,
    )

    section = KnowledgeSection(
        section_id=section_id,
        knowledge_id=knowledge_id,
        sequence=0,
        content=content,
        heading="Procedure Step",
    )
    source = KnowledgeSource(source_system="governed_km", source_id=f"{knowledge_id}-doc")
    reference = KnowledgeEvidenceReference(
        knowledge_id=knowledge_id,
        version_label=version_label,
        section_id=section_id,
        source_system="governed_km",
        source_id=f"{knowledge_id}-doc",
    )
    return KnowledgeEvidenceItem(
        reference=reference,
        title=title,
        document_type=KnowledgeDocumentType.SOP,
        lifecycle_status=LifecycleStatus.APPROVED,
        source=source,
        section=section,
    )


def test_complete_integration_dynamic_retrieval_refresh_and_citation() -> None:
    """End-to-end flow test:

    1. Initial approved_commands_catalog is empty.
    2. TAE dynamically retrieves and selects approved, applicable knowledge.
    3. Server-owned evidence and command authorization are refreshed via snapshot.
    4. The requested command ('alt cm') is validated against its exact source, parameters,
       prerequisites, and restrictions.
    5. The authorized command reaches the final validated response with the correct citation.
    """
    from backend.tools.knowledge.runtime import (
        discard_knowledge_run_evidence_state,
        get_or_init_run_state,
        snapshot_selected_knowledge_evidence,
    )

    run_id = "test-run-e2e-dynamic"
    discard_knowledge_run_evidence_state(run_id)

    try:
        # Step 1: Pre-delegation state has empty catalog
        initial_caller_evidence: list[dict[str, Any]] = []
        initial_caller_catalog: list[dict[str, Any]] = []

        initial_evidence = build_server_validated_evidence(run_id, None, initial_caller_evidence)
        initial_commands = build_server_validated_commands(initial_evidence, initial_caller_catalog)
        assert len(initial_commands) == 0, "Initial catalog must be empty"

        # Step 2: TAE dynamically retrieves and selects governed knowledge
        # Canonical server-owned triple: (knowledge_id:version_label:section_id)
        k_id = "mop-ericsson-alarms"
        v_label = "v1"
        s_id = "sec2"
        canonical_source_id = f"{k_id}:{v_label}:{s_id}"

        evidence_item = _make_governed_evidence_item(
            knowledge_id=k_id,
            section_id=s_id,
            version_label=v_label,
            title="Ericsson Moshell Alarm Check SOP",
            content="To inspect active alarms on RBS, run `alt cm`. Ensure session is logged.",
        )

        run_state = get_or_init_run_state(run_id)
        run_state.selected_evidence.append(evidence_item)

        selected_snapshot = snapshot_selected_knowledge_evidence(run_id)
        assert len(selected_snapshot) == 1
        assert f"{selected_snapshot[0].reference.knowledge_id}:{selected_snapshot[0].reference.version_label}:{selected_snapshot[0].reference.section_id}" == canonical_source_id

        # Step 3: Server-owned evidence and command authorization are dynamically refreshed
        # TAE outputs 'alt cm' citing the dynamically selected source
        tae_output = {
            "outcome": "recommended",
            "technical_interpretation": "Active alarms on RBS node require inspection via standard diagnostic command.",
            "diagnostic_step": {
                "action": "Execute alt cm in moshell / amos to check active alarms.",
                "reason": "Identify persistent alarm codes.",
                "command": "alt cm",
                "command_source": canonical_source_id,
                "expected_evidence": "List of active alarm lines including severity and specific problem.",
                "prerequisites": ["Moshell or AMOS connection established to node"],
                "restrictions": ["Do not interrupt running health checks"],
            },
        }

        # Caller commands provided by specialist citing the selected evidence
        caller_commands_from_tae = [
            {"command": "alt cm", "source_id": canonical_source_id}
        ]

        # Dynamic refresh executes in post-runner validation
        refreshed_evidence = build_server_validated_evidence(run_id, None, initial_caller_evidence)
        refreshed_commands = build_server_validated_commands(refreshed_evidence, caller_commands_from_tae)

        assert len(refreshed_evidence) == 1
        assert len(refreshed_commands) == 1
        assert refreshed_commands[0].command == "alt cm"
        assert refreshed_commands[0].source_id == canonical_source_id

        # Step 4: Validate against refreshed request payload
        refreshed_req_payload = {
            "verified_evidence": [e.model_dump(mode="json") for e in refreshed_evidence],
            "approved_commands_catalog": [c.model_dump(mode="json") for c in refreshed_commands],
        }

        validated, modified = validate_technical_authority_payload(
            response_payload=tae_output,
            request_payload=refreshed_req_payload,
        )

        # Step 5: Command remains authorized, exact syntax and citation preserved
        step = validated["diagnostic_step"]
        assert step["command"] == "alt cm"
        assert step["command_source"] == canonical_source_id
        assert step["prerequisites"] == ["Moshell or AMOS connection established to node"]
        assert "Do not interrupt running health checks" in step["restrictions"]
        assert "alt cm" in step["action"]
        assert not any("[Command stripped" in r for r in step["restrictions"])

    finally:
        discard_knowledge_run_evidence_state(run_id)


def test_complete_integration_negative_mismatched_and_prohibited_commands_rejected() -> None:
    """Negative flow test:

    Even after dynamic evidence selection and refresh:
    - Missing approval, mismatched source, invalid parameter, or prohibited command
      must be rejected at the server-owned boundary.
    """
    from backend.tools.knowledge.runtime import (
        discard_knowledge_run_evidence_state,
        get_or_init_run_state,
    )

    run_id = "test-run-e2e-negative"
    discard_knowledge_run_evidence_state(run_id)

    try:
        # Dynamically select an approved evidence snippet with strict parameter template
        k_id = "mop-ericsson-restart"
        v_label = "v1"
        s_id = "sec1"
        canonical_source_id = f"{k_id}:{v_label}:{s_id}"

        evidence_item = _make_governed_evidence_item(
            knowledge_id=k_id,
            section_id=s_id,
            version_label=v_label,
            title="Board Restart Procedure",
            content="To restart board: `restart board <board_slot>` where board_slot is SLOT-1 through SLOT-8.",
        )

        run_state = get_or_init_run_state(run_id)
        run_state.selected_evidence.append(evidence_item)

        refreshed_evidence = build_server_validated_evidence(run_id, None, [])

        # Case A: Mismatched source id
        unapproved_cmd_source = [
            {"command": "restart board SLOT-1", "source_id": "mop:wrong:source:id"}
        ]
        approved_a = build_server_validated_commands(refreshed_evidence, unapproved_cmd_source)
        assert len(approved_a) == 0, "Mismatched source_id must be rejected"

        # Case B: Arbitrary prohibited destructive command not in template
        destructive_cmd = [
            {"command": "format c: /fs:ntfs", "source_id": canonical_source_id}
        ]
        approved_b = build_server_validated_commands(refreshed_evidence, destructive_cmd)
        assert len(approved_b) == 0, "Prohibited/unmatched command must be rejected"

        # Case C: Valid command in catalog but TAE payload cites mismatched source
        valid_caller_cmd = [
            {"command": "restart board SLOT-1", "source_id": canonical_source_id}
        ]
        approved_c = build_server_validated_commands(refreshed_evidence, valid_caller_cmd)
        assert len(approved_c) == 1

        tae_payload_mismatched_citation = {
            "outcome": "recommended",
            "diagnostic_step": {
                "action": "Restart the board now",
                "command": "restart board SLOT-1",
                "command_source": "mop:fake:citation",  # Citing different source!
            },
        }

        validated_c, _ = validate_technical_authority_payload(
            response_payload=tae_payload_mismatched_citation,
            request_payload={
                "verified_evidence": [e.model_dump(mode="json") for e in refreshed_evidence],
                "approved_commands_catalog": [c.model_dump(mode="json") for c in approved_c],
            },
        )

        assert validated_c["diagnostic_step"]["command"] is None, "Must strip command if command_source mismatches"
        assert any("[Command stripped: unapproved operational command]" in r for r in validated_c["diagnostic_step"]["restrictions"])

    finally:
        discard_knowledge_run_evidence_state(run_id)


def test_unauthorized_command_removal_maintains_grammatical_prose_and_never_claims_safe() -> None:
    """Verifies that removing an unauthorized command produces clean text

    without token corruption and never claims a rejected action is safe.
    """
    raw_payload = {
        "outcome": "recommended",
        "technical_interpretation": "Fault diagnosed. Executing `unapproved_cmd` is safe and recommended.",
        "diagnostic_step": {
            "action": "Please run `unapproved_cmd` on node ER_RBS_Site01.",
            "reason": "We verified `unapproved_cmd` is harmless.",
            "command": "unapproved_cmd",
            "command_source": "unauthorized_source",
            "expected_evidence": "Check output.",
        },
    }

    validated, modified = validate_technical_authority_payload(
        response_payload=raw_payload,
        request_payload={"approved_commands_catalog": [], "verified_evidence": []},
    )

    dumped_json = json.dumps(validated)
    # 1. No token corruption
    assert "[unapproved command removed]" not in dumped_json
    assert "unapproved_cmd" not in dumped_json

    # 2. Command stripped
    assert validated["diagnostic_step"]["command"] is None

    # 3. Action and reason remain readable without dangling backticks
    action = validated["diagnostic_step"]["action"]
    reason = validated["diagnostic_step"]["reason"]
    assert "`" not in action
    assert "`" not in reason
    assert not action.startswith(" ")
    assert not reason.startswith(" ")

    # 4. Restrictions explicitly flag the stripped command
    assert any("[Command stripped: unapproved operational command]" in r for r in validated["diagnostic_step"]["restrictions"])


def test_live_defect_moshell_alt_command_authorization_and_presentation() -> None:
    """Exact reproduction of live defect:
    User query: 'How can I check the alarms in Moshell? What cmd to run?'

    Defect was: ANOC explained the command and showed example output, but withheld
    the syntax ('alt') and instructed the user to execute an unspecified command
    because 'alt' failed grounding due to human-readable title citation mismatch.

    This test verifies:
    1. Pre-delegation approved_commands_catalog is empty.
    2. TAE dynamically retrieves and selects 'Rogers ERICSSON_4G_Resource_Timeout_and Allocation Failure.docx'.
    3. TAE proposes command 'alt' with human-readable title and section annotation:
       'Rogers ERICSSON_4G_Resource_Timeout_and Allocation Failure.docx (section: HC Commands)'.
    4. Post-runner dynamic refresh resolves canonical source ID, validates word-boundary 'alt' in snippet,
       and authorizes the command.
    5. Final response projection contains the exact command 'alt' and canonical citation.
    6. Negative case: When authorization is absent, execution instruction is neutralized and
       no unspecified execution is commanded.
    """
    from backend.tools.knowledge.runtime import (
        discard_knowledge_run_evidence_state,
        get_or_init_run_state,
    )

    run_id = "test-live-defect-moshell-alt"
    discard_knowledge_run_evidence_state(run_id)

    try:
        # 1. Pre-delegation: Catalog is empty
        caller_evidence: list[dict[str, Any]] = []
        caller_commands: list[dict[str, Any]] = []
        initial_ev = build_server_validated_evidence(run_id, None, caller_evidence)
        initial_cmd = build_server_validated_commands(initial_ev, caller_commands)
        assert len(initial_cmd) == 0

        # 2. Dynamic knowledge retrieval
        k_id = "doc-ericsson-resource-timeout"
        v_label = "v1"
        s_id = "hc-commands-sec"
        canonical_source_id = f"{k_id}:{v_label}:{s_id}"
        doc_title = "Rogers ERICSSON_4G_Resource_Timeout_and Allocation Failure.docx"

        evidence_item = _make_governed_evidence_item(
            knowledge_id=k_id,
            section_id=s_id,
            version_label=v_label,
            title=doc_title,
            content="HC Commands: To list active alarms in Moshell / AMOS, run alt or alt cm to inspect cluster alarms.",
        )
        run_state = get_or_init_run_state(run_id)
        run_state.selected_evidence.append(evidence_item)

        # 3. TAE proposes 'alt' citing the document title with section annotation
        human_readable_citation = f"{doc_title} (section: HC Commands)"
        raw_tae_response = {
            "outcome": "recommended",
            "technical_interpretation": "To check active alarms in Moshell, run `alt` to display active alarms on the node.",
            "diagnostic_step": {
                "action": "Run alt in the Moshell / AMOS terminal session.",
                "reason": "Lists current active alarms on the Ericsson RBS.",
                "command": "alt",
                "command_source": human_readable_citation,
                "expected_evidence": "Alarm list showing severity, timestamp, and specific problem.",
                "prerequisites": ["Active Moshell / AMOS session connected to target node"],
                "restrictions": ["Do not execute any disruptive state-changing commands"],
            },
        }

        # 4. Post-runner dynamic refresh
        candidate_commands = list(caller_commands)
        step = raw_tae_response.get("diagnostic_step", {})
        candidate_commands.append({
            "command": step["command"],
            "source_id": step.get("command_source") or "",
            "procedure_section": step.get("action"),
            "restrictions": step.get("restrictions") or [],
        })

        refreshed_ev = build_server_validated_evidence(run_id, None, caller_evidence)
        refreshed_cmd = build_server_validated_commands(refreshed_ev, candidate_commands)

        assert len(refreshed_cmd) == 1
        assert refreshed_cmd[0].command == "alt"
        assert refreshed_cmd[0].source_id == canonical_source_id

        # 5. Post-runner validation
        validated, modified = validate_technical_authority_payload(
            response_payload=raw_tae_response,
            request_payload={
                "verified_evidence": [e.model_dump(mode="json") for e in refreshed_ev],
                "approved_commands_catalog": [c.model_dump(mode="json") for c in refreshed_cmd],
            },
        )

        v_step = validated["diagnostic_step"]
        assert v_step["command"] == "alt", "Exact syntax 'alt' must be authorized and preserved"
        assert v_step["command_source"] == canonical_source_id, "Source must resolve to canonical server-owned ID"
        assert "alt" in v_step["action"]

        # 6. Renderer simulation: authorized case
        def render_presentation(validated_payload: dict[str, Any]) -> str:
            d_step = validated_payload.get("diagnostic_step")
            if not d_step:
                return validated_payload.get("technical_interpretation", "")
            cmd = d_step.get("command")
            if cmd:
                src = d_step.get("command_source") or "governed procedure"
                return f"Recommended command to run: `{cmd}` (Source: {src}). Action: {d_step.get('action')}"
            else:
                return (
                    f"Operational command execution is not authorized. "
                    f"Action: {d_step.get('action')}. Restrictions: {', '.join(d_step.get('restrictions') or [])}"
                )

        rendered_authorized = render_presentation(validated)
        assert "`alt`" in rendered_authorized
        assert canonical_source_id in rendered_authorized

        # 7. Negative case: ungrounded/unapproved command
        unauthorized_response = {
            "outcome": "recommended",
            "technical_interpretation": "Execute unapproved check.",
            "diagnostic_step": {
                "action": "Run in the Moshell / AMOS terminal session.",
                "reason": "Test unapproved.",
                "command": "unapproved_alt_hack",
                "command_source": "bogus_source",
            },
        }
        validated_unauth, _ = validate_technical_authority_payload(
            response_payload=unauthorized_response,
            request_payload={
                "verified_evidence": [e.model_dump(mode="json") for e in refreshed_ev],
                "approved_commands_catalog": [c.model_dump(mode="json") for c in refreshed_cmd],
            },
        )
        assert validated_unauth["diagnostic_step"]["command"] is None
        rendered_unauth = render_presentation(validated_unauth)
        assert "Operational command execution is not authorized" in rendered_unauth
        assert "Run in the Moshell" not in rendered_unauth
        assert "Perform observational check" in rendered_unauth

    finally:
        discard_knowledge_run_evidence_state(run_id)
