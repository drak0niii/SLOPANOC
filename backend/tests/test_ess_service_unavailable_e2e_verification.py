"""End-to-End Live Verification Test for Step 4:
ESS Service Unavailable
-> governed knowledge search
-> explicit evidence selection
-> APPROVED/current + applicability MATCH
-> Step 2 grounding + authorization
-> TAE selects next diagnostic step (alt)
-> Team Manager final-response boundary
-> source/provenance shown
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.agents import Agent
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import AgentTool
from google.genai import types

from backend.agents.technical_authority_engineer.agent_tool import (
    TechnicalAuthorityAgentTool,
    authorize_grounded_command,
    build_server_validated_commands,
    build_server_validated_evidence,
    classify_command_operation,
    ground_command_candidate,
)
from backend.agents.technical_authority_engineer.execution_context import (
    discard_technical_authority_execution,
    get_technical_authority_execution,
)
from backend.agents.technical_authority_engineer.schemas import (
    CommandOperationType,
    EvidenceReference,
    GroundedCommand,
    TechnicalAuthorityOutcome,
    TechnicalAuthorityRequest,
    TechnicalAuthorityResponse,
)
from backend.agents.technical_authority_engineer.validation import (
    is_command_authorized,
    validate_technical_authority_payload,
)
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.cases.service import CaseService
from backend.knowledge.domain.applicability import Applicability, ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.provenance.contracts import KnowledgeEvidenceSelectionKey
from backend.tools.knowledge import runtime as rt
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence


class _ScriptedCallLlm(BaseLlm):
    """Deterministic scripted LLM yielding distinct responses across consecutive calls."""

    def __init__(self, model: str, parts_by_call: list[list[types.Part]], **kwargs: Any) -> None:
        super().__init__(model=model, **kwargs)
        self._parts_by_call = parts_by_call
        self._call_count = 0
        self._requests: list[LlmRequest] = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._requests.append(llm_request)
        if self._call_count >= len(self._parts_by_call):
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part.from_text(text="")]),
                partial=False,
            )
            return

        parts = self._parts_by_call[self._call_count]
        self._call_count += 1
        yield LlmResponse(
            content=types.Content(role="model", parts=parts),
            partial=False,
        )


@pytest.fixture
def isolated_km_repo(monkeypatch):
    """Provides an isolated in-memory knowledge repository."""
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()
    repository = rt.get_knowledge_repository()
    yield repository
    import asyncio
    asyncio.run(repository.close())
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()


@pytest.mark.asyncio
async def test_step4_ess_service_unavailable_full_live_flow(isolated_km_repo, monkeypatch) -> None:
    """Verifies the complete end-to-end flow for:
    'how can i troubleshoot ESS Service Unavailable?'

    Verifies:
    1. knowledge_search retrieves the relevant governed ESS procedure.
    2. Evidence is explicitly SELECTED -- AVAILABLE alone is not enough.
    3. Selected evidence is: current, APPROVED, applicability MATCH.
    4. The command comes from that selected evidence.
    5. alt becomes GroundedCommand, AuthorizedCommand, TAE-selected diagnostic_step.command.
    6. Team Manager exposes only the TAE-selected step.
    7. No extra command/action is added.
    8. Source card/provenance points to the same SELECTED evidence.
    9. No unselected evidence appears as a source.
    10. No raw Team Manager streaming leaks before validation.
    11. ESS Service Unavailable does NOT infer reset/restart solely from the alarm.
    """
    repo = isolated_km_repo

    # 1. Seed Governed Knowledge: Approved ESS MOP
    ess_mop_content = (
        "Alarms List for Resolution:\n"
        "Table:\n"
        "Alarms Name | Remarks\n"
        "Service Degraded | Restart the affected radio(RRU)\n"
        "Service Unavailable | Restart the affected radio(RRU) if no other HW/SW related alarms or existing MATE resolution.\n"
        "ESS Service Unavailable | Restart the affected radio(RRU)\n"
        "Resource Activation Timeout | Restart the affected radio(RRU)\n"
        "Resource Allocation Failure | Restart the affected radio(RRU)\n"
        "Rule will trigger on Specific Problem\n"
        "Rule will trigger the Node name\n"
        "Rule will wait alarm short live time (10min)\n"
        "If alarm still active after short live time , Rule will reset the Radio  once in 24 hours\n"
        "Restart the affected radio and after 5 mins check if the alarm clears.\n"
        "If the alarm remains active on node after reset, then raise a ticket and assign ticket to Group(Helix) as per the ZLD.\n"
        "Ericsson Network login Details: -\n"
        "Login the EnodeB by using amos \n"
        " amos “EnodeB Name”\n"
        "HC Commands:\n"
        "amos xxxx\n"
        "lt all\n"
        "alt\n"
        "st pluginunit\n"
        "st fieldr\n"
        "st nrcell|f\n"
        "st sector\n"
        "st ru\n"
        "ue print -admitted\n"
        "q\n"
        "HC Logs:\n"
        "Alarm check if alarm present on node then go do the  reset\n"
    )
    ess_mop = KnowledgeObject(
        knowledge_id="A5-VALIDATION-ROGERS-4G5G",
        document_type=KnowledgeDocumentType.MOP,
        title="MOP_Rogers ERICSSON_4G5G_Resource_Timeout_ Allocation Failure_Service Degraded_Serive_Unavailable Alarms Resolution",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        applicability=Applicability(dimensions={}),  # Unconstrained = MATCH
        source=KnowledgeSource(
            source_system="a5_validation_local_file",
            source_id="MOP_Rogers ERICSSON_4G5G_Resource_Timeout_ Allocation Failure_Service Degraded_Serive_Unavailable Alarms Resolution.docx",
            display_name="MOP_Rogers ERICSSON_4G5G Alarms Resolution",
        ),
        sections=[
            KnowledgeSection(
                section_id="A5-VALIDATION-ROGERS-4G5G:v1:section-0000",
                knowledge_id="A5-VALIDATION-ROGERS-4G5G",
                heading="Alarms List for Resolution",
                sequence=0,
                content=ess_mop_content,
                source_locator="lines:1-66",
            )
        ],
    )
    await repo.add(ess_mop)

    # 2. Seed an unselected sibling procedure to verify negative isolation (Requirement 9)
    unselected_mop = KnowledgeObject(
        knowledge_id="UNSELECTED-SIBLING-MOP",
        document_type=KnowledgeDocumentType.SOP,
        title="Unrelated Optical Interface Cleaning Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id="unrelated.docx", display_name="Unrelated SOP"),
        sections=[
            KnowledgeSection(
                section_id="UNSELECTED-SIBLING-MOP:v1:section-0000",
                knowledge_id="UNSELECTED-SIBLING-MOP",
                heading="Cleaning",
                sequence=0,
                content="Inspect optical connector for dust. Do not use alcohol.",
                source_locator="lines:1-10",
            )
        ],
    )
    await repo.add(unselected_mop)

    # Setup Session and Case Services
    session_service = ApiSessionService(adk_session_service=InMemorySessionService())
    case_service = CaseService()
    session_id = await session_service.create_session(user_id="test-engineer")
    case = await case_service.create_case(
        user_id="test-engineer",
        title="ESS Service Unavailable Troubleshooting",
        problem_statement="Investigating ESS Service Unavailable on node",
    )
    await case_service.link_session(
        user_id="test-engineer",
        case_id=case.case_id,
        session_id=session_id,
        session_owner_user_id="test-engineer",
    )

    # 3. Setup Scripted TAE Specialist LLM
    # TAE performs:
    # Call 1: knowledge_search("ESS Service Unavailable")
    # Call 2: knowledge_select_evidence(selections=[{"knowledge_id": "A5-VALIDATION-ROGERS-4G5G", ...}])
    # Call 3: TechnicalAuthorityResponse selecting alt as the single diagnostic step
    tae_call_1_parts = [
        types.Part(
            function_call=types.FunctionCall(
                name="knowledge_search",
                args={"query_text": "ESS Service Unavailable"},
            )
        )
    ]
    tae_call_2_parts = [
        types.Part(
            function_call=types.FunctionCall(
                name="knowledge_select_evidence",
                args={
                    "selections": [
                        {
                            "knowledge_id": "A5-VALIDATION-ROGERS-4G5G",
                            "version_label": "v1",
                            "section_id": "A5-VALIDATION-ROGERS-4G5G:v1:section-0000",
                        }
                    ]
                },
            )
        )
    ]
    tae_response_payload = {
        "outcome": "recommended",
        "technical_interpretation": (
            "The node is reporting ESS Service Unavailable. Based on governed MOP "
            "'MOP_Rogers ERICSSON_4G5G_Resource_Timeout_ Allocation Failure_Service Degraded_Serive_Unavailable Alarms Resolution', "
            "active alarms must be checked first to isolate the affected radio unit and verify fault persistence. "
            "No reset or restart may be performed without prior alarm confirmation."
        ),
        "verified_evidence_citations": ["A5-VALIDATION-ROGERS-4G5G:v1:section-0000"],
        "diagnostic_step": {
            "action": "Check active alarms on the node",
            "reason": "Identify active alarms and isolate affected radio unit before taking operational actions.",
            "expected_evidence": "List of active alarms and affected Managed Objects from Moshell",
            "command": "alt",
            "command_source": "A5-VALIDATION-ROGERS-4G5G:v1:section-0000",
            "restrictions": ["Do not restart or reset without verifying active alarms"],
        },
        "approved_commands_catalog": [],
        "verified_evidence": [],
    }
    tae_call_3_parts = [types.Part.from_text(text=json.dumps(tae_response_payload))]

    tae_llm = _ScriptedCallLlm(
        model="scripted-tae-specialist",
        parts_by_call=[tae_call_1_parts, tae_call_2_parts, tae_call_3_parts],
    )
    specialist_agent = Agent(
        name="technical_authority_engineer",
        model=tae_llm,
        tools=[knowledge_search, knowledge_select_evidence],
        input_schema=TechnicalAuthorityRequest,
        output_schema=TechnicalAuthorityResponse,
    )
    tae_tool = TechnicalAuthorityAgentTool(agent=specialist_agent)

    # 4. Setup Team Manager LLM
    # Team Manager:
    # Call 1: record_source_requirements(requires_governed_knowledge=True)
    # Call 2: technical_authority_engineer(...)
    # Call 3: Model synthesis text. To test boundary: Team Manager attempts to synthesize alt
    #         AND injects an unauthorized 'restart radio' instruction.
    tm_call_1_parts = [
        types.Part(
            function_call=types.FunctionCall(
                name="record_source_requirements",
                args={"requires_teams": False, "requires_governed_knowledge": True},
            )
        )
    ]
    tm_call_2_parts = [
        types.Part(
            function_call=types.FunctionCall(
                name="technical_authority_engineer",
                args={"problem_statement": "how can i troubleshoot ESS Service Unavailable?"},
            )
        )
    ]
    tm_call_3_parts = [
        types.Part.from_text(
            text="To troubleshoot ESS Service Unavailable, check active alarms on the node. Run alt, then restart the radio."
        )
    ]

    from backend.agents.team_manager.agent import team_manager
    from backend.agents.team_manager.source_requirements import record_source_requirements

    tm_llm = _ScriptedCallLlm(
        model="scripted-team-manager",
        parts_by_call=[tm_call_1_parts, tm_call_2_parts, tm_call_3_parts],
    )
    tm_tools = [
        record_source_requirements,
        tae_tool,
    ]
    outer_tm_agent = team_manager.model_copy(update={"model": tm_llm, "tools": tm_tools})

    from backend.api.session_service import APP_NAME

    outer_runner = Runner(
        app_name=APP_NAME,
        agent=outer_tm_agent,
        session_service=session_service.adk_session_service,
    )

    chat_service = ChatService(
        session_service=session_service,
        runner=outer_runner,
        case_service=case_service,
    )

    # 5. Execute Turn and Collect SSE Stream Events
    user_query = "how can i troubleshoot ESS Service Unavailable?"
    streamed_deltas: list[str] = []
    final_message_content: Optional[str] = None
    captured_run_id: Optional[str] = None

    async for event in chat_service.execute_turn_events(
        session_id=session_id,
        message_text=user_query,
        user_id="test-engineer",
    ):
        if event.type == StreamEventType.RUN_STARTED:
            captured_run_id = event.data.get("run_id")
        elif event.type == StreamEventType.MESSAGE_DELTA:
            streamed_deltas.append(event.data.get("text", ""))
        elif event.type == StreamEventType.MESSAGE_COMPLETED:
            final_message_content = event.data.get("content")

    # ==============================================================================
    # VERIFICATION OF THE 11 CONTRACT GUARANTEES
    # ==============================================================================

    # 1. Verification of knowledge_search retrieval
    assert tae_llm._call_count >= 2, "TAE must have called knowledge_search and knowledge_select_evidence"

    # 2 & 3. Evidence is explicitly SELECTED and meets: current, APPROVED, applicability MATCH
    reloaded_session = await session_service.get_session(session_id, "test-engineer")
    turn_sources = reloaded_session.state.get("turn_source_references", {})
    assert turn_sources, "Turn source references must be populated"

    # Extract source cards from session state
    found_knowledge_sources = []
    for turn_id, turn_ref in turn_sources.items():
        if isinstance(turn_ref, dict):
            for ks in turn_ref.get("knowledge_sources", []):
                found_knowledge_sources.append(ks)

    assert len(found_knowledge_sources) > 0, "Selected governed knowledge source must appear in turn sources"
    selected_source = found_knowledge_sources[0]

    # Verify ID, title, and version of selected evidence
    assert selected_source.get("knowledge_id") == "A5-VALIDATION-ROGERS-4G5G"
    assert selected_source.get("version_label") == "v1"
    assert "MOP_Rogers ERICSSON_4G5G" in selected_source.get("title", "")
    assert selected_source.get("document_type") == "mop"

    # 4 & 5. Command comes from selected evidence and becomes GroundedCommand and AuthorizedCommand
    # Verify grounded command derivation
    ev_ref = EvidenceReference(
        source_id="A5-VALIDATION-ROGERS-4G5G:v1:section-0000",
        source_type="governed_knowledge",
        title=ess_mop.title,
        content_snippet=ess_mop_content,
        metadata={
            "knowledge_id": "A5-VALIDATION-ROGERS-4G5G",
            "version_label": "v1",
            "section_id": "A5-VALIDATION-ROGERS-4G5G:v1:section-0000",
            "lifecycle_status": "approved",
            "applicability_outcome": "match",
        },
    )
    auth_sources = {ev_ref.source_id: ev_ref}
    candidate = {"command": "alt", "source_id": ev_ref.source_id}
    grounded = ground_command_candidate(candidate, [ev_ref], auth_sources)
    assert grounded is not None
    assert isinstance(grounded, GroundedCommand)
    assert grounded.command == "alt"
    assert grounded.grounding_method in ("exact_match", "template_pattern")

    authorized = authorize_grounded_command(grounded, candidate)
    assert authorized is not None
    assert authorized.command == "alt"
    assert authorized.operation_type == CommandOperationType.READ_ONLY_DIAGNOSTIC
    assert authorized.authorization_decision == "authorized"

    # Verify TAE selected diagnostic step
    ts_dict = reloaded_session.state.get("troubleshooting_state")
    assert ts_dict is not None
    diagnostic_history = ts_dict.get("diagnostic_history", [])
    assert len(diagnostic_history) > 0
    rec_check = diagnostic_history[0]
    assert rec_check.get("grounded_command") == "alt"
    assert rec_check.get("action") == "Check active alarms on the node"

    # 6. Team Manager exposes only the TAE-selected step ("alt")
    assert final_message_content is not None
    assert "alt" in final_message_content

    # 7. No extra command/action is added: "restart" was stripped by synthesis boundary
    assert "restart" not in final_message_content.lower()
    assert "st pluginunit" not in final_message_content.lower()
    assert final_message_content == "To troubleshoot ESS Service Unavailable, check active alarms on the node. Run alt."

    # 8 & 9. Source card points strictly to SELECTED evidence; unselected evidence is completely excluded
    source_knowledge_ids = [s.get("knowledge_id") for s in found_knowledge_sources]
    assert "A5-VALIDATION-ROGERS-4G5G" in source_knowledge_ids
    assert "UNSELECTED-SIBLING-MOP" not in source_knowledge_ids

    # 10. No raw Team Manager streaming leaks before validation
    assert "".join(streamed_deltas) == "", "Speculative/unvalidated delta stream must be suppressed"

    # 11. Specifically verify: ESS Service Unavailable does NOT infer reset/restart
    assert "reset" not in final_message_content.lower()
    assert "restart" not in final_message_content.lower()


# ==============================================================================
# NEGATIVE SECURITY & BOUNDARY CHECKS
# ==============================================================================

def test_negative_available_evidence_alone_cannot_authorize_command() -> None:
    """Negative check: AVAILABLE evidence (retrieved via search but NOT selected)
    cannot authorize any operational command.
    """
    # Evidence is retrieved into available_evidence, but snapshot_selected is empty
    available_ev = EvidenceReference(
        source_id="A5-VALIDATION-ROGERS-4G5G:v1:sec0",
        source_type="governed_knowledge",
        title="ESS MOP",
        content_snippet="Execute `alt` to inspect active alarms.",
        metadata={"lifecycle_status": "approved", "applicability_outcome": "match"},
    )
    # When evidence is NOT selected, build_server_validated_evidence returns empty list
    server_evidence: list[EvidenceReference] = []  # No selected evidence
    caller_commands = [{"command": "alt", "source_id": "A5-VALIDATION-ROGERS-4G5G:v1:sec0"}]

    approved = build_server_validated_commands(server_evidence, caller_commands)
    assert len(approved) == 0, "AVAILABLE evidence without selection must NEVER authorize commands"


def test_negative_unapproved_or_non_matching_applicability_cannot_authorize() -> None:
    """Negative check: Candidate lifecycle status != APPROVED or applicability != MATCH
    fails closed and cannot authorize operational commands.
    """
    # Case 1: Candidate lifecycle status
    candidate_ev = EvidenceReference(
        source_id="mop:draft:v1:sec1",
        source_type="governed_knowledge",
        title="Draft MOP",
        content_snippet="Execute `alt`.",
        metadata={"lifecycle_status": "candidate", "applicability_outcome": "match"},
    )
    approved_1 = build_server_validated_commands([candidate_ev], [{"command": "alt", "source_id": candidate_ev.source_id}])
    assert len(approved_1) == 0, "CANDIDATE lifecycle status must not authorize commands"

    # Case 2: Partial match / Unknown applicability
    unknown_ev = EvidenceReference(
        source_id="mop:unknown:v1:sec1",
        source_type="governed_knowledge",
        title="Unknown MOP",
        content_snippet="Execute `alt`.",
        metadata={"lifecycle_status": "approved", "applicability_outcome": "unknown"},
    )
    approved_2 = build_server_validated_commands([unknown_ev], [{"command": "alt", "source_id": unknown_ev.source_id}])
    assert len(approved_2) == 0, "UNKNOWN applicability outcome must not authorize commands"


def test_negative_alarm_alone_must_not_authorize_reset_or_restart() -> None:
    """Negative check: A single alarm does not authorize restart or reset.
    Mutating operations require server-verified target confirmation.
    """
    ev = EvidenceReference(
        source_id="mop:ericsson:v1:sec1",
        source_type="governed_knowledge",
        title="ESS MOP",
        content_snippet="If ESS Service Unavailable is active, run `acc RRU-1 manualrestart` to recover.",
        metadata={"lifecycle_status": "approved", "applicability_outcome": "match"},
    )
    candidate = {"command": "acc RRU-1 manualrestart", "source_id": ev.source_id}
    grounded = ground_command_candidate(candidate, [ev], {ev.source_id: ev})
    assert grounded is not None

    # Mutating command without target_confirmed=True fails closed
    authorized = authorize_grounded_command(grounded, candidate, trusted_context={"target_confirmed": False})
    assert authorized is None, "Mutating restart command without target confirmation must fail closed"


# ==============================================================================
# STEP 4 LIVE REPAIR: explicit selection, canonical source, applicability, safety
# ==============================================================================

from backend.agents.technical_authority_engineer import agent_tool as tae_agent_tool
from backend.agents.technical_authority_engineer.agent_tool import (
    UNSELECTED_EVIDENCE_MISSING_INFORMATION,
    requires_selection_remediation,
)
from backend.agents.technical_authority_engineer.validation import (
    match_source_to_evidence,
    resolve_canonical_source_id,
)

_S4_KID = "S4-GOVERNED-MOP"
_S4_SECTION = f"{_S4_KID}:v1:section-0000"
_S4_CANONICAL = f"{_S4_KID}:v1:{_S4_SECTION}"
_S4_FILENAME = "MOP_S4 Governed Alarm Procedure.docx"
_S4_CONTENT = (
    "ESS Service Unavailable | Restart the affected radio(RRU)\n"
    "HC Commands:\n"
    "alt\n"
    "st pluginunit\n"
)


def _s4_mop(dimensions: Optional[dict[str, list[str]]] = None) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_S4_KID,
        document_type=KnowledgeDocumentType.MOP,
        title="S4 Governed Alarm Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        applicability=Applicability(dimensions=dimensions or {}),
        source=KnowledgeSource(source_system="test", source_id=_S4_FILENAME, display_name="S4 MOP"),
        sections=[
            KnowledgeSection(
                section_id=_S4_SECTION,
                knowledge_id=_S4_KID,
                heading="Alarm resolution",
                sequence=0,
                content=_S4_CONTENT,
                source_locator="lines:1-4",
            )
        ],
    )


def _fc(name: str, args: dict[str, Any]) -> list[types.Part]:
    return [types.Part(function_call=types.FunctionCall(name=name, args=args))]


_S4_SEARCH = _fc("knowledge_search", {"query_text": "ESS Service Unavailable"})
_S4_SELECT = _fc(
    "knowledge_select_evidence",
    {"selections": [{"knowledge_id": _S4_KID, "version_label": "v1", "section_id": _S4_SECTION}]},
)


def _s4_response(command: Optional[str], command_source: Optional[str], action: str = "Check active alarms on the node") -> list[types.Part]:
    payload = {
        "outcome": "recommended",
        "technical_interpretation": "ESS Service Unavailable reported; active alarms must be checked first.",
        "verified_evidence_citations": [],
        "diagnostic_step": {
            "action": action,
            "reason": "Isolate the affected unit before any operational action.",
            "expected_evidence": "Active alarm list",
            "command": command,
            "command_source": command_source,
            "restrictions": [],
        },
    }
    return [types.Part.from_text(text=json.dumps(payload))]


class _S4Session:
    """One real ChatService session reused across turns (session state persists between turns)."""

    def __init__(self, monkeypatch: Any) -> None:
        self.records: list[dict[str, Any]] = []
        original_record = tae_agent_tool.record_technical_authority_execution

        def _capture(run_id: str, record: dict[str, Any]) -> None:
            self.records.append(record)
            original_record(run_id, record)

        monkeypatch.setattr(tae_agent_tool, "record_technical_authority_execution", _capture)
        self.session_service = ApiSessionService(adk_session_service=InMemorySessionService())
        self.session_id: Optional[str] = None

    async def turn(
        self,
        tae_calls: list[list[types.Part]],
        tm_final_text: str,
        tae_args: Optional[dict[str, Any]] = None,
        user_text: str = "how can i troubleshoot ESS Service Unavailable?",
    ) -> dict[str, Any]:
        if self.session_id is None:
            self.session_id = await self.session_service.create_session(user_id="test-engineer")
        before = len(self.records)
        tae_llm = _ScriptedCallLlm(model="scripted-tae", parts_by_call=tae_calls)
        specialist_agent = Agent(
            name="technical_authority_engineer",
            model=tae_llm,
            tools=[knowledge_search, knowledge_select_evidence],
            input_schema=TechnicalAuthorityRequest,
            output_schema=TechnicalAuthorityResponse,
        )
        tae_tool = TechnicalAuthorityAgentTool(agent=specialist_agent)

        from backend.agents.team_manager.agent import team_manager
        from backend.agents.team_manager.source_requirements import record_source_requirements
        from backend.api.session_service import APP_NAME

        tm_llm = _ScriptedCallLlm(
            model="scripted-tm",
            parts_by_call=[
                _fc("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
                _fc("technical_authority_engineer", tae_args or {"problem_statement": user_text}),
                [types.Part.from_text(text=tm_final_text)],
            ],
        )
        outer_agent = team_manager.model_copy(update={"model": tm_llm, "tools": [record_source_requirements, tae_tool]})
        runner = Runner(app_name=APP_NAME, agent=outer_agent, session_service=self.session_service.adk_session_service)
        chat_service = ChatService(session_service=self.session_service, runner=runner, case_service=CaseService())

        final: Optional[str] = None
        async for event in chat_service.execute_turn_events(
            session_id=self.session_id, message_text=user_text, user_id="test-engineer"
        ):
            if event.type == StreamEventType.MESSAGE_COMPLETED:
                final = event.data.get("content")

        session = await self.session_service.get_session(self.session_id, "test-engineer")
        ts = session.state.get("troubleshooting_state") or {}
        return {
            "final": final or "",
            "tae_llm": tae_llm,
            "records": self.records[before:],
            "history": ts.get("diagnostic_history", []),
            "state": dict(session.state),
        }


async def _s4_run_turn(
    repo: Any,
    monkeypatch: Any,
    tae_calls: list[list[types.Part]],
    tm_final_text: str,
    tae_args: Optional[dict[str, Any]] = None,
    dimensions: Optional[dict[str, list[str]]] = None,
    user_text: str = "how can i troubleshoot ESS Service Unavailable?",
) -> dict[str, Any]:
    await repo.add(_s4_mop(dimensions))
    return await _S4Session(monkeypatch).turn(tae_calls, tm_final_text, tae_args=tae_args, user_text=user_text)


def _s4_selected_ev(source_id: str, filename: str, applicability: str = "match", dims: Optional[list[str]] = None) -> EvidenceReference:
    kid, version, section = source_id.split(":", 2)
    return EvidenceReference(
        source_id=source_id,
        source_type="governed_knowledge",
        title=f"Title {kid}",
        content_snippet="HC Commands:\nalt\n",
        metadata={
            "knowledge_id": kid,
            "version_label": version,
            "section_id": section,
            "source_id": filename,
            "lifecycle_status": "approved",
            "applicability_outcome": applicability,
            "unresolved_applicability_dimensions": dims or [],
        },
    )


# --- A ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step4_a_unselected_governed_recommendation_gets_one_bounded_remediation(isolated_km_repo, monkeypatch) -> None:
    """Governed command without SELECTED evidence: no authority on first pass, exactly one
    remediation instructing explicit selection, and AVAILABLE is never auto-promoted."""
    result = await _s4_run_turn(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[
            _S4_SEARCH,
            _s4_response("alt", _S4_FILENAME),  # governed recommendation, nothing selected
            _S4_SELECT,  # remediation: explicit selection by the TAE itself
            _s4_response("alt", _S4_FILENAME),
        ],
        tm_final_text="Check active alarms on the node. Run alt.",
    )
    assert result["tae_llm"]._call_count == 4, "exactly one remediation pass"
    record = result["records"][-1]
    assert record["diagnostic_step"]["command"] == "alt"
    assert record["diagnostic_step"]["command_source"] == _S4_CANONICAL
    assert [c["source_id"] for c in record["approved_commands_catalog"]] == [_S4_CANONICAL]


def test_step4_a_available_evidence_is_never_promoted_to_selected() -> None:
    run_id = "step4-a-no-promotion"
    governed = {"outcome": "recommended", "diagnostic_step": {"action": "x", "command": "alt", "command_source": _S4_FILENAME}}
    try:
        get_or_init = rt.get_or_init_run_state(run_id)
        assert get_or_init is not None
        assert requires_selection_remediation(run_id, [governed]) is True
        assert rt.snapshot_selected_knowledge_evidence(run_id) == []
        assert build_server_validated_evidence(run_id, None, []) == []
        observational = {"outcome": "recommended", "diagnostic_step": {"action": "Observe LEDs", "command": None}}
        assert requires_selection_remediation(run_id, [observational]) is False
    finally:
        rt.discard_knowledge_run_evidence_state(run_id)


# --- B ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step4_b_selection_still_empty_after_remediation_fails_closed(isolated_km_repo, monkeypatch) -> None:
    result = await _s4_run_turn(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[
            _S4_SEARCH,
            _s4_response("alt", _S4_FILENAME),
            _s4_response("alt", _S4_FILENAME),  # remediation ignored: still nothing selected
            _s4_response("alt", _S4_FILENAME),  # must never be requested (bounded)
        ],
        tm_final_text="Check active alarms on the node. Run alt.",
    )
    assert result["tae_llm"]._call_count == 3, "no second remediation / no loop"
    record = result["records"][-1]
    assert record["outcome"] == TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value
    assert record.get("diagnostic_step") is None
    assert UNSELECTED_EVIDENCE_MISSING_INFORMATION in record["missing_information"]
    assert record["approved_commands_catalog"] == []
    assert result["history"] == []
    assert "alt" not in result["final"].split()


# --- C ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step4_c_raw_filename_resolves_to_unique_selected_canonical_source(isolated_km_repo, monkeypatch) -> None:
    result = await _s4_run_turn(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_S4_SEARCH, _S4_SELECT, _s4_response("alt", _S4_FILENAME)],
        tm_final_text="Check active alarms on the node. Run alt.",
    )
    assert result["tae_llm"]._call_count == 3, "no remediation when evidence was selected"
    record = result["records"][-1]
    assert record["diagnostic_step"]["command"] == "alt"
    assert record["diagnostic_step"]["command_source"] == _S4_CANONICAL
    assert result["history"][0]["command_source_id"] == _S4_CANONICAL
    assert result["history"][0]["grounded_command"] == "alt"


def test_step4_c_resolution_unit() -> None:
    ev = _s4_selected_ev("KID-1:v1:sec-1", "Procedure One.docx")
    assert resolve_canonical_source_id("Procedure One.docx", [ev]) == "KID-1:v1:sec-1"
    assert resolve_canonical_source_id("procedure one.docx (section: HC Commands)", [ev]) == "KID-1:v1:sec-1"
    approved = build_server_validated_commands([ev], [{"command": "alt", "source_id": "Procedure One.docx"}])
    assert [(c.command, c.source_id) for c in approved] == [("alt", "KID-1:v1:sec-1")]


# --- D ------------------------------------------------------------------------
def test_step4_d_filename_matching_only_available_evidence_is_rejected() -> None:
    selected = _s4_selected_ev("KID-SEL:v1:sec-1", "Selected.docx")
    # "Available.docx" exists only in AVAILABLE evidence, which never enters verified_evidence.
    assert resolve_canonical_source_id("Available.docx", [selected]) is None
    assert build_server_validated_commands([selected], [{"command": "alt", "source_id": "Available.docx"}]) == []
    validated, _ = validate_technical_authority_payload(
        {"outcome": "recommended", "diagnostic_step": {"action": "Check alarms", "reason": "r", "expected_evidence": "e", "command": "alt", "command_source": "Available.docx"}},
        {"verified_evidence": [selected.model_dump(mode="json")], "approved_commands_catalog": []},
    )
    assert validated["diagnostic_step"]["command"] is None
    assert "[Command source does not resolve to selected governed evidence]" in validated["diagnostic_step"]["restrictions"]


# --- E ------------------------------------------------------------------------
def test_step4_e_filename_matching_multiple_selected_items_is_ambiguous() -> None:
    ev1 = _s4_selected_ev("KID-M:v1:sec-1", "Shared.docx")
    ev2 = _s4_selected_ev("KID-M:v1:sec-2", "Shared.docx")
    assert sorted(match_source_to_evidence("Shared.docx", [ev1, ev2])) == ["KID-M:v1:sec-1", "KID-M:v1:sec-2"]
    assert resolve_canonical_source_id("Shared.docx", [ev1, ev2]) is None
    assert build_server_validated_commands([ev1, ev2], [{"command": "alt", "source_id": "Shared.docx"}]) == []
    validated, _ = validate_technical_authority_payload(
        {"outcome": "recommended", "diagnostic_step": {"action": "Check alarms", "reason": "r", "expected_evidence": "e", "command": "alt", "command_source": "Shared.docx"}},
        {"verified_evidence": [ev1.model_dump(mode="json"), ev2.model_dump(mode="json")], "approved_commands_catalog": []},
    )
    assert validated["diagnostic_step"]["command"] is None
    assert any("ambiguous" in r for r in validated["diagnostic_step"]["restrictions"])
    # An exact canonical id stays unique even when aliases collide.
    assert resolve_canonical_source_id("KID-M:v1:sec-2", [ev1, ev2]) == "KID-M:v1:sec-2"


# --- F ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step4_f_unknown_applicability_gives_targeted_clarification(isolated_km_repo, monkeypatch) -> None:
    result = await _s4_run_turn(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_S4_SEARCH, _S4_SELECT, _s4_response("alt", _S4_FILENAME)],
        tm_final_text="Check active alarms on the node. Run alt.",
        dimensions={"dim_alpha": ["a1"], "dim_beta": ["b1"]},
    )
    record = result["records"][-1]
    ev = [e for e in record["verified_evidence"] if e["source_id"] == _S4_CANONICAL][0]
    assert ev["metadata"]["applicability_outcome"] == "unknown"
    assert ev["metadata"]["unresolved_applicability_dimensions"] == ["dim_alpha", "dim_beta"]
    assert record["approved_commands_catalog"] == []
    step = record.get("diagnostic_step")
    assert step is None or step.get("command") is None
    clarifications = [m for m in record["missing_information"] if m.startswith("Missing applicability context:")]
    assert clarifications == ["Missing applicability context: dim_alpha", "Missing applicability context: dim_beta"]
    assert "alt" not in result["final"].split()


def test_step4_f_partial_match_requests_only_unresolved_dimensions() -> None:
    ev = _s4_selected_ev("KID-P:v1:sec-1", "Partial.docx", applicability="partial_match", dims=["dim_beta"])
    validated, _ = validate_technical_authority_payload(
        {"outcome": "recommended", "diagnostic_step": {"action": "Check alarms", "reason": "r", "expected_evidence": "e", "command": "alt", "command_source": "Partial.docx"}},
        {"verified_evidence": [ev.model_dump(mode="json")], "approved_commands_catalog": build_server_validated_commands([ev], [{"command": "alt", "source_id": "Partial.docx"}])},
    )
    assert validated["diagnostic_step"]["command"] is None
    assert [m for m in validated["missing_information"] if m.startswith("Missing applicability context:")] == [
        "Missing applicability context: dim_beta"
    ]


# --- G ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step4_g_supplied_applicability_facts_match_and_authorize_read_only(isolated_km_repo, monkeypatch) -> None:
    result = await _s4_run_turn(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_S4_SEARCH, _S4_SELECT, _s4_response("alt", _S4_FILENAME)],
        tm_final_text="Check active alarms on the node. Run alt.",
        tae_args={
            "problem_statement": "how can i troubleshoot ESS Service Unavailable?",
            "known_applicability_facts": {"dim_alpha": ["a1"], "dim_beta": ["b1"]},
        },
        dimensions={"dim_alpha": ["a1"], "dim_beta": ["b1"]},
        user_text="how can i troubleshoot ESS Service Unavailable on a1 b1?",
    )
    record = result["records"][-1]
    ev = [e for e in record["verified_evidence"] if e["source_id"] == _S4_CANONICAL][0]
    assert ev["metadata"]["applicability_outcome"] == "match"
    assert ev["metadata"]["unresolved_applicability_dimensions"] == []
    assert record["diagnostic_step"]["command"] == "alt"
    assert record["diagnostic_step"]["command_source"] == _S4_CANONICAL
    assert record["approved_commands_catalog"][0]["operation_type"] == CommandOperationType.READ_ONLY_DIAGNOSTIC.value


# --- H ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step4_h_ess_alarm_text_does_not_authorize_restart(isolated_km_repo, monkeypatch) -> None:
    result = await _s4_run_turn(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[
            _S4_SEARCH,
            _S4_SELECT,
            _s4_response("Restart the affected radio(RRU)", _S4_FILENAME, action="Restart the affected radio"),
        ],
        tm_final_text="ESS Service Unavailable is active. Restart the affected radio, then reset the unit.",
    )
    record = result["records"][-1]
    assert record["approved_commands_catalog"] == []
    step = record.get("diagnostic_step")
    assert step is None or step.get("command") is None
    final = result["final"].lower()
    for forbidden in ("restart", "reset", "manualrestart", "restartunit"):
        assert forbidden not in final


# --- I ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step4_i_team_manager_exposes_only_tae_selected_command(isolated_km_repo, monkeypatch) -> None:
    result = await _s4_run_turn(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_S4_SEARCH, _S4_SELECT, _s4_response("alt", _S4_FILENAME)],
        tm_final_text="Check active alarms on the node. Run alt. Then run st pluginunit.",
    )
    assert result["records"][-1]["diagnostic_step"]["command"] == "alt"
    final = result["final"]
    assert "alt" in final
    assert "st pluginunit" not in final
    assert "restart" not in final.lower()


# ==============================================================================
# STEP 4 CONTEXT REPAIR: applicability continuity across clarification turns
# ==============================================================================

from backend.agents.team_manager.case_context import TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM
from backend.agents.team_manager.governed_knowledge_completion import SAFE_COMPLETION_FAILURE_TEXT
from backend.agents.technical_authority_engineer.applicability_context import (
    CONFIRMED_APPLICABILITY_FACTS_STATE_KEY,
    build_governed_vocabulary,
    reconcile_applicability_facts,
)
from backend.knowledge.domain.applicability import ApplicabilityContext

_CTX_DIMS = {"customer": ["Rogers"], "domain": ["RAN"], "technology": ["4G", "5G"], "vendor": ["Ericsson"]}


def _insufficient_response() -> list[types.Part]:
    return [
        types.Part.from_text(
            text=json.dumps(
                {
                    "outcome": "insufficient_evidence",
                    "technical_interpretation": "Applicable procedure not yet validated for this context.",
                    "missing_information": [],
                }
            )
        )
    ]


def _tae_request_text(tae_llm: _ScriptedCallLlm) -> str:
    first = tae_llm._requests[0]
    return " ".join(p.text for c in first.contents for p in (c.parts or []) if getattr(p, "text", None))


def test_ctx_a_account_name_is_not_classified_as_vendor() -> None:
    vocabulary = build_governed_vocabulary([_s4_mop(_CTX_DIMS)])
    accepted, unconfirmed = reconcile_applicability_facts({"vendor": ["Rogers"]}, "Rogers account", {}, vocabulary)
    assert accepted == {"customer": ["Rogers"]}
    assert "vendor" not in accepted
    # A value governed metadata does not know for a governed dimension is never asserted.
    accepted, unconfirmed = reconcile_applicability_facts({"vendor": ["Acme"]}, "Acme account", {}, vocabulary)
    assert accepted == {}
    assert [(f.dimension, f.value) for f in unconfirmed] == [("vendor", "Acme")]
    # An implied (not literally stated) value is never silently asserted.
    accepted, unconfirmed = reconcile_applicability_facts({"vendor": ["Ericsson"]}, "RAN / Moshell", {}, vocabulary)
    assert accepted == {}
    assert unconfirmed[0].reason == "not stated by the user"


def test_ctx_c_context_enrichment_discards_stale_applicability_outcomes() -> None:
    run_id = "ctx-c-refresh"
    try:
        state = rt.get_or_init_run_state(run_id)
        ident = (_S4_KID, "v1", _S4_SECTION)
        state.applicability_by_identity[ident] = "unknown"
        state.unresolved_dimensions_by_identity[ident] = ["vendor"]
        rt.refresh_run_applicability_context(run_id, ApplicabilityContext(dimensions={"vendor": ["x"]}))
        assert rt.get_evidence_applicability_outcome(run_id, ident) is None, "stale UNKNOWN must not be reused"
        assert rt.get_evidence_unresolved_applicability_dimensions(run_id, ident) == []
        assert state.execution_context.applicability_context.dimensions == {"vendor": ["x"]}
    finally:
        rt.discard_knowledge_run_evidence_state(run_id)


def test_ctx_f_command_policy_wording() -> None:
    text = TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM.lower()
    assert "explain plainly that operational command execution is not authorized" not in text
    assert "do not claim that commands can never be provided" in text
    assert "commands are provided when a current approved governed procedure is explicitly selected" in text


@pytest.mark.asyncio
async def test_ctx_sequence_rogers_ran_4g5g_vendor_then_followup(isolated_km_repo, monkeypatch) -> None:
    """Real multi-turn sequence: ESS -> Rogers account -> RAN/Moshell -> 4G and 5G -> Ericsson -> follow-up."""
    await isolated_km_repo.add(_s4_mop(_CTX_DIMS))
    conv = _S4Session(monkeypatch)
    alt_turn = [_S4_SEARCH, _S4_SELECT, _s4_response("alt", _S4_FILENAME)]

    # Turn 1: no context -> UNKNOWN, no command, clarification lists every unresolved dimension.
    t1 = await conv.turn(alt_turn, "Checking the governed procedure.", user_text="how can i troubleshoot ESS service unavailable ?")
    r1 = t1["records"][-1]
    assert r1["approved_commands_catalog"] == []
    assert r1["applicability_clarification"]["missing_dimensions"] == ["customer", "domain", "technology", "vendor"]

    # Turn 2 (A): model mislabels the account as vendor -> server re-keys to the governed customer dimension.
    t2 = await conv.turn(
        alt_turn,
        "Noted.",
        user_text="Rogers account",
        tae_args={"problem_statement": "Rogers account", "known_applicability_facts": {"vendor": ["Rogers"]}},
    )
    assert t2["state"][CONFIRMED_APPLICABILITY_FACTS_STATE_KEY] == {"customer": ["Rogers"]}
    r2 = t2["records"][-1]
    assert r2["applicability_clarification"]["missing_dimensions"] == ["domain", "technology", "vendor"]

    # Turn 3 (B): RAN accepted; vendor inferred from a tool name is NOT asserted.
    t3 = await conv.turn(
        alt_turn,
        "Noted.",
        user_text="RAN / Moshell",
        tae_args={"problem_statement": "RAN / Moshell", "known_applicability_facts": {"domain": ["RAN"], "vendor": ["Ericsson"]}},
    )
    assert t3["state"][CONFIRMED_APPLICABILITY_FACTS_STATE_KEY] == {"customer": ["Rogers"], "domain": ["RAN"]}

    # Turn 4 (B + D): 4G/5G accumulate; nothing selected -> targeted vendor-only clarification, no generic failure.
    t4 = await conv.turn(
        [_S4_SEARCH, _insufficient_response()],
        "I cannot provide operational commands.",
        user_text="4G and 5G",
        tae_args={"problem_statement": "4G and 5G", "known_applicability_facts": {"technology": ["4G", "5G"]}},
    )
    assert t4["state"][CONFIRMED_APPLICABILITY_FACTS_STATE_KEY] == {
        "customer": ["Rogers"],
        "domain": ["RAN"],
        "technology": ["4G", "5G"],
    }
    clar = t4["records"][-1]["applicability_clarification"]
    assert clar["missing_dimensions"] == ["vendor"]
    assert t4["final"] == clar["text"]
    assert t4["final"] != SAFE_COMPLETION_FAILURE_TEXT
    # Partial clarification answer (clarification continuity): only the unresolved field is asked for.
    assert "I still need:\n- vendor" in t4["final"]
    assert "cannot provide" not in t4["final"].lower()

    # Turn 5 (C + E): vendor supplied -> applicability recomputed by evaluation -> MATCH -> alt authorized.
    t5 = await conv.turn(
        alt_turn,
        "Check active alarms on the node. Run alt.",
        user_text="Ericsson",
        tae_args={"problem_statement": "Ericsson", "known_applicability_facts": {"vendor": ["Ericsson"]}},
    )
    r5 = t5["records"][-1]
    ev = [e for e in r5["verified_evidence"] if e["source_id"] == _S4_CANONICAL][0]
    assert ev["metadata"]["applicability_outcome"] == "match"
    assert r5["diagnostic_step"]["command"] == "alt"
    assert r5["diagnostic_step"]["command_source"] == _S4_CANONICAL
    assert "applicability_clarification" not in r5
    assert "alt" in t5["final"]

    # Turn 6 (G): follow-up keeps the ESS target and all confirmed facts.
    t6 = await conv.turn(
        alt_turn,
        "Run alt.",
        user_text="what is the command to see all active alarms?",
        tae_args={"problem_statement": "what is the command to see all active alarms?"},
    )
    request_text = _tae_request_text(t6["tae_llm"])
    # The active investigation is kept as separate BACKGROUND (never merged into the operator's
    # current request, which takes precedence -- see turn_request.py).
    assert '"active_investigation_context":"how can i troubleshoot ESS service unavailable ?' in request_text
    assert '"problem_statement":"what is the command to see all active alarms?"' in request_text
    assert '"user_request_text":"what is the command to see all active alarms?"' in request_text
    assert '"vendor":["Ericsson"]' in request_text and '"customer":["Rogers"]' in request_text
    r6 = t6["records"][-1]
    assert r6["diagnostic_step"]["command"] == "alt"
    assert t6["state"][CONFIRMED_APPLICABILITY_FACTS_STATE_KEY]["vendor"] == ["Ericsson"]


# ==============================================================================
# STEP 4 FINAL BOUNDARY: live-flow projection (real ChatService path)
# ==============================================================================


def _turn_knowledge_ids(state: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for turn_ref in (state.get("turn_source_references") or {}).values():
        if isinstance(turn_ref, dict):
            ids.extend(ks.get("knowledge_id") for ks in turn_ref.get("knowledge_sources", []))
    return ids


@pytest.mark.asyncio
async def test_final_boundary_live_shape_stripped_command_no_restart_narrative(isolated_km_repo, monkeypatch) -> None:
    """Live shape: TAE picks an observational check, its command fails grounding
    ('st pluginunit; st fieldr'), and Team Manager narrates a future radio restart."""
    result = await _s4_run_turn(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[
            _S4_SEARCH,
            _S4_SELECT,
            _s4_response("st pluginunit; st fieldr", _S4_FILENAME, action="Check the type of unit the affected radio is connected to"),
        ],
        tm_final_text=(
            "Check the type of unit the affected radio is connected to. "
            "This distinction will determine the specific command to be used for a radio restart, "
            "which is suggested as the primary resolution step. "
            "I cannot provide the exact command because an approved governed procedure is required."
        ),
    )
    record = result["records"][-1]
    # Command Authority refuses the composed command (single-invocation boundary) and the Progression
    # Controller independently refuses the multi-action step: nothing is presented as executable.
    assert record["outcome"] == "insufficient_evidence" and record["diagnostic_step"] is None
    assert record["approved_commands_catalog"] == []
    assert "One operational diagnostic action per step: the proposal combined several actions." in record["missing_information"]
    final = result["final"]
    assert final.startswith("Check the type of unit the affected radio is connected to.")
    for leaked in ("restart", "reset", "st pluginunit", "st fieldr", "resolution step", "approved governed procedure is required"):
        assert leaked not in final.lower()
    assert "no command for this diagnostic step has passed the current grounding and authorization checks" in final


@pytest.mark.asyncio
async def test_final_boundary_authorized_alt_projected_with_selected_source(isolated_km_repo, monkeypatch) -> None:
    result = await _s4_run_turn(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_S4_SEARCH, _S4_SELECT, _s4_response("alt", _S4_FILENAME)],
        tm_final_text=(
            "Check active alarms on the node. Run alt. "
            "If the alarm persists, a radio restart is the primary resolution. Then run st pluginunit."
        ),
    )
    record = result["records"][-1]
    # Structured authorization observability (execution record, no debug logging).
    assert [(c["command"], c["source_id"], c["operation_type"]) for c in record["approved_commands_catalog"]] == [
        ("alt", _S4_CANONICAL, CommandOperationType.READ_ONLY_DIAGNOSTIC.value)
    ]
    assert record["diagnostic_step"]["command"] == "alt"
    assert record["diagnostic_step"]["command_source"] == _S4_CANONICAL
    selected_ids = [e["source_id"] for e in record["verified_evidence"] if e["source_type"] == "governed_knowledge"]
    assert selected_ids == [_S4_CANONICAL], "command source must belong to the current SELECTED evidence"
    # Final projection: exactly the TAE-selected command, nothing further.
    assert result["final"] == "Check active alarms on the node. Run alt."
    assert _turn_knowledge_ids(result["state"]) == [_S4_KID]
