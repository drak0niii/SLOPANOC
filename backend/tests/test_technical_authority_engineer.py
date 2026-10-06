"""Unit and integration tests for the Technical Authority Engineer (Phase 6A).

Historical alias: Troubleshooting Manager.

Verifies:
1. Typed request and response schemas and outcome states.
2. Strict citation provenance enforcement (fabricated IDs stripped).
3. Command grounding against approved catalog and verified evidence snippets.
4. Single-step diagnostic discipline and fail-closed safety.
5. Server-validated context envelope assembly.
6. Advisory-only invariants (tools=[], no write capabilities).
7. Feature flag gating (SLOPANOC_TECHNICAL_AUTHORITY_ENABLED default False).
8. Dynamic Team Manager instruction injection and tool availability.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from pydantic import PrivateAttr

from backend.agents.team_manager.agent import (
    _build_team_manager_tools,
    get_team_manager,
    incident_manager_tool,
    team_manager,
)
from backend.agents.team_manager.case_context import (
    TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM,
    team_manager_instruction_provider,
)
from backend.agents.team_manager.direct_read_fast_path import get_fast_path_team_manager
from backend.agents.technical_authority_engineer.agent import (
    technical_authority_engineer,
    technical_authority_engineer_tool,
    troubleshooting_manager,
    troubleshooting_manager_tool,
)
from backend.agents.technical_authority_engineer.agent_tool import (
    TechnicalAuthorityAgentTool,
    build_server_validated_commands,
    build_server_validated_evidence,
)
from backend.agents.technical_authority_engineer.prompts import TECHNICAL_AUTHORITY_ENGINEER_INSTRUCTION
from backend.agents.technical_authority_engineer.schemas import (
    ApprovedCommand,
    DiagnosticStep,
    EvidenceReference,
    TechnicalAuthorityOutcome,
    TechnicalAuthorityRequest,
    TechnicalAuthorityResponse,
)
from backend.agents.technical_authority_engineer.validation import (
    enforce_technical_authority_response_integrity,
    is_command_grounded,
    validate_technical_authority_payload,
)
from backend.config.settings import Settings, get_settings
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence


# ==============================================================================
# 1. Schemas & Contracts
# ==============================================================================


def test_technical_authority_outcome_values() -> None:
    assert TechnicalAuthorityOutcome.RECOMMENDED.value == "recommended"
    assert TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value == "insufficient_evidence"
    assert TechnicalAuthorityOutcome.ESCALATION_REQUIRED.value == "escalation_required"
    assert TechnicalAuthorityOutcome.ERROR.value == "error"


def test_evidence_reference_model() -> None:
    ref = EvidenceReference(
        source_id="doc1:v1:sec2",
        source_type="governed_knowledge",
        title="Test Procedure",
        content_snippet="Check optical power levels using show interface.",
        metadata={"section_id": "sec2"},
    )
    assert ref.source_id == "doc1:v1:sec2"
    assert ref.source_type == "governed_knowledge"
    assert "optical power" in (ref.content_snippet or "")


def test_approved_command_model() -> None:
    cmd = ApprovedCommand(
        command="show interface optical-power",
        source_id="doc1:v1:sec2",
        procedure_section="Section 4.1",
        restrictions=["Read-only check", "Do not reboot"],
    )
    assert cmd.command == "show interface optical-power"
    assert len(cmd.restrictions) == 2


def test_diagnostic_step_model() -> None:
    step = DiagnosticStep(
        action="Inspect physical layer alarms on port 1/1/1.",
        reason="Isolates whether the fiber link is experiencing loss of signal.",
        command="show port 1/1/1 detail",
        command_source="doc1:v1:sec2",
        expected_evidence="Port status should show Up or Down with signal dBm values.",
        restrictions=["Do not flap the port."],
    )
    assert step.command == "show port 1/1/1 detail"
    assert step.command_source == "doc1:v1:sec2"
    assert "Port status" in step.expected_evidence


def test_technical_authority_request_model() -> None:
    req = TechnicalAuthorityRequest(
        problem_statement="BGP session down between PE-01 and CE-01",
        verified_symptoms=["BGP state is Active, not Established", "Ping to peer succeeds"],
        missing_information=["Local AS configuration", "TCP port 179 reachability"],
        known_applicability_facts={"vendor": ["nokia"], "technology": ["ip-routing"]},
        prior_steps_taken=["Verified ping to peer IP"],
    )
    assert req.problem_statement.startswith("BGP session down")
    assert len(req.verified_symptoms) == 2
    assert req.known_applicability_facts == {"vendor": ["nokia"], "technology": ["ip-routing"]}


def test_technical_authority_response_recommended() -> None:
    resp = TechnicalAuthorityResponse(
        outcome=TechnicalAuthorityOutcome.RECOMMENDED,
        technical_interpretation="Observed: IP connectivity is intact. Hypothesis: TCP port 179 is blocked or AS misconfigured.",
        verified_evidence_citations=["doc1:v1:sec2"],
        missing_information=["TCP 179 state"],
        diagnostic_step=DiagnosticStep(
            action="Check TCP socket state for port 179.",
            reason="Determines if three-way handshake completed.",
            command="show router tcp socket",
            command_source="doc1:v1:sec2",
            expected_evidence="Output showing SYN_SENT or ESTABLISHED.",
        ),
    )
    assert resp.outcome == TechnicalAuthorityOutcome.RECOMMENDED
    assert resp.diagnostic_step is not None
    assert resp.diagnostic_step.command == "show router tcp socket"


def test_technical_authority_response_insufficient_evidence() -> None:
    resp = TechnicalAuthorityResponse(
        outcome=TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE,
        technical_interpretation="Alarm observed but node type is unconfirmed.",
        missing_information=["Node hardware model (DUS vs Baseband)"],
        diagnostic_step=None,
    )
    assert resp.outcome == TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE
    assert resp.diagnostic_step is None


def test_technical_authority_response_escalation_required() -> None:
    resp = TechnicalAuthorityResponse(
        outcome=TechnicalAuthorityOutcome.ESCALATION_REQUIRED,
        technical_interpretation="VSWR Over Threshold alarm present on Sector 2.",
        escalation_reason="Approved procedure strictly prohibits restart. Physical site dispatch required.",
        missing_information=[],
    )
    assert resp.outcome == TechnicalAuthorityOutcome.ESCALATION_REQUIRED
    assert "prohibits restart" in (resp.escalation_reason or "")


# ==============================================================================
# 2. Citation Provenance Enforcement
# ==============================================================================


def test_citation_provenance_preserves_valid_and_strips_unknown() -> None:
    request_payload = {
        "verified_evidence": [
            {"source_id": "doc123:v1:sec1", "source_type": "governed_knowledge"},
            {"source_id": "teams:msg987", "source_type": "teams_conversation"},
        ]
    }
    response_payload = {
        "outcome": "insufficient_evidence",
        "technical_interpretation": "Interpreting facts...",
        "verified_evidence_citations": [
            "doc123:v1:sec1",
            "fabricated_doc_999",  # Hallucinated citation
            "teams:msg987",
            "teams:unretrieved_msg",  # Hallucinated citation
        ],
    }

    sanitized, modified = validate_technical_authority_payload(response_payload, request_payload)

    assert modified is True
    assert sanitized["verified_evidence_citations"] == ["doc123:v1:sec1", "teams:msg987"]


def test_citation_provenance_handles_non_list_citations() -> None:
    request_payload = {"verified_evidence": [{"source_id": "doc1:v1:sec1"}]}
    response_payload = {
        "outcome": "insufficient_evidence",
        "technical_interpretation": "...",
        "verified_evidence_citations": "doc1:v1:sec1",  # Invalid type: str instead of list
    }
    sanitized, modified = validate_technical_authority_payload(response_payload, request_payload)
    assert modified is True
    assert sanitized["verified_evidence_citations"] == []


# ==============================================================================
# 3. Command Grounding & Safety
# ==============================================================================


def test_is_command_grounded_in_approved_catalog() -> None:
    catalog = [{"command": "show port 1/1/1", "source_id": "mop:v1:sec4"}]
    evidence: list[dict] = []

    assert is_command_grounded("show port 1/1/1", "mop:v1:sec4", catalog, evidence) is True
    assert is_command_grounded("show port 1/1/1 ", "mop:v1:sec4 ", catalog, evidence) is True
    assert is_command_grounded("reboot now", "mop:v1:sec4", catalog, evidence) is False
    assert is_command_grounded("show port 1/1/1", "wrong_src", catalog, evidence) is False


def test_is_command_grounded_in_evidence_snippet() -> None:
    catalog: list[dict] = []
    evidence = [
        {
            "source_id": "sop:v2:sec1",
            "content_snippet": "Run `traceroute 10.0.0.1` to confirm path reachability.",
        }
    ]

    assert is_command_grounded("traceroute 10.0.0.1", "sop:v2:sec1", catalog, evidence) is True
    assert is_command_grounded("ping 10.0.0.1", "sop:v2:sec1", catalog, evidence) is False


def test_is_command_grounded_in_parameterized_template() -> None:
    catalog = [{"command": "restart board <board_slot> --graceful", "source_id": "mop:v1:sec4"}]
    evidence: list[dict] = []

    assert is_command_grounded("restart board SLOT-4-DUS --graceful", "mop:v1:sec4", catalog, evidence) is True
    assert is_command_grounded("restart board SLOT-4-DUS --force", "mop:v1:sec4", catalog, evidence) is False


def test_is_command_grounded_with_parameter_highlighting() -> None:
    catalog = [{"command": "restart board <board_slot> --graceful", "source_id": "mop:v1:sec4"}]
    evidence: list[dict] = []

    # Command with markdown bold parameter highlighting
    assert is_command_grounded("restart board **SLOT-4-DUS** --graceful", "mop:v1:sec4", catalog, evidence) is True
    assert is_command_grounded("restart board `**`SLOT-4-DUS`**` --graceful", "mop:v1:sec4", catalog, evidence) is True


def test_command_grounding_strips_unapproved_command() -> None:
    request_payload = {
        "approved_commands_catalog": [{"command": "show port status", "source_id": "doc1:v1:sec1"}],
        "verified_evidence": [{"source_id": "doc1:v1:sec1", "content_snippet": "show port status"}],
    }
    response_payload = {
        "outcome": "recommended",
        "technical_interpretation": "Link is down.",
        "verified_evidence_citations": ["doc1:v1:sec1"],
        "diagnostic_step": {
            "action": "Restart the interface.",
            "reason": "Attempts interface bounce.",
            "command": "admin restart port 1/1/1",  # Unapproved destructive/operational command
            "command_source": "doc1:v1:sec1",
            "expected_evidence": "Port status Up.",
            "restrictions": [],
        },
    }

    sanitized, modified = validate_technical_authority_payload(response_payload, request_payload)

    assert modified is True
    step = sanitized["diagnostic_step"]
    assert step["command"] is None
    assert step["command_source"] is None
    assert any("[Command stripped" in r for r in step["restrictions"])


def test_command_grounding_preserves_valid_command() -> None:
    request_payload = {
        "approved_commands_catalog": [{"command": "show port status", "source_id": "doc1:v1:sec1"}],
        "verified_evidence": [{"source_id": "doc1:v1:sec1", "content_snippet": "Run show port status."}],
    }
    response_payload = {
        "outcome": "recommended",
        "technical_interpretation": "Checking port status.",
        "verified_evidence_citations": ["doc1:v1:sec1"],
        "diagnostic_step": {
            "action": "Check port status.",
            "reason": "Verifies link state.",
            "command": "show port status",
            "command_source": "doc1:v1:sec1",
            "expected_evidence": "Status display.",
            "restrictions": ["Read-only"],
        },
    }

    sanitized, modified = validate_technical_authority_payload(response_payload, request_payload)
    assert modified is False
    assert sanitized["diagnostic_step"]["command"] == "show port status"


# ==============================================================================
# 4. Single-Step Discipline & Fail-Closed Safety
# ==============================================================================


def test_single_step_downgrades_recommended_without_step() -> None:
    request_payload = {"verified_evidence": []}
    response_payload = {
        "outcome": "recommended",
        "technical_interpretation": "Issue identified.",
        "diagnostic_step": None,  # Recommended outcome without a diagnostic step
    }

    sanitized, modified = validate_technical_authority_payload(response_payload, request_payload)
    assert modified is True
    assert sanitized["outcome"] == "insufficient_evidence"
    assert sanitized["diagnostic_step"] is None
    assert "missing or invalid" in str(sanitized["missing_information"])


def test_single_step_strips_step_from_non_recommended_outcome() -> None:
    request_payload = {"verified_evidence": []}
    response_payload = {
        "outcome": "insufficient_evidence",
        "technical_interpretation": "Need more data.",
        "diagnostic_step": {  # Inconsistent: diagnostic step present on insufficient_evidence
            "action": "Some check",
            "reason": "Testing",
            "expected_evidence": "Something",
        },
    }

    sanitized, modified = validate_technical_authority_payload(response_payload, request_payload)
    assert modified is True
    assert sanitized["diagnostic_step"] is None


def test_escalation_required_populates_default_reason_if_missing() -> None:
    request_payload = {"verified_evidence": []}
    response_payload = {
        "outcome": "escalation_required",
        "technical_interpretation": "Cannot diagnose.",
        "escalation_reason": None,
    }

    sanitized, modified = validate_technical_authority_payload(response_payload, request_payload)
    assert modified is True
    assert sanitized["escalation_reason"] is not None
    assert "Escalation required" in sanitized["escalation_reason"]


# ==============================================================================
# 5. Response Integrity Callback
# ==============================================================================


@pytest.mark.asyncio
async def test_enforce_response_integrity_handles_malformed_json() -> None:
    ctx = MagicMock()
    session = MagicMock()
    event = MagicMock()
    event.author = "technical_authority_engineer"
    event.content = types.Content(role="model", parts=[types.Part.from_text(text="NOT VALID JSON {")])
    session.events = [event]
    ctx.session = session
    ctx.user_content = None

    corrected_content = await enforce_technical_authority_response_integrity(ctx)
    assert corrected_content is not None
    text = "".join(p.text for p in corrected_content.parts if p.text)
    payload = json.loads(text)
    assert payload["outcome"] == "error"
    assert "valid JSON" in payload["technical_interpretation"]


@pytest.mark.asyncio
async def test_enforce_response_integrity_applies_corrections() -> None:
    ctx = MagicMock()
    session = MagicMock()

    # Incoming request
    req_payload = {
        "problem_statement": "Link down",
        "verified_evidence": [{"source_id": "doc1:v1:sec1"}],
        "approved_commands_catalog": [],
    }
    user_part = types.Part.from_text(text=json.dumps(req_payload))
    ctx.user_content = types.Content(role="user", parts=[user_part])

    # Outgoing response with fabricated citation and ungrounded command
    resp_payload = {
        "outcome": "recommended",
        "technical_interpretation": "Link fault.",
        "verified_evidence_citations": ["doc1:v1:sec1", "fabricated:id"],
        "diagnostic_step": {
            "action": "Check status",
            "reason": "Test",
            "command": "secret_shell_exec",
            "command_source": "fabricated:id",
            "expected_evidence": "Output",
        },
    }
    model_part = types.Part.from_text(text=json.dumps(resp_payload))
    model_event = MagicMock()
    model_event.author = "technical_authority_engineer"
    model_event.content = types.Content(role="model", parts=[model_part])
    session.events = [model_event]
    ctx.session = session

    corrected_content = await enforce_technical_authority_response_integrity(ctx)
    assert corrected_content is not None
    text = "".join(p.text for p in corrected_content.parts if p.text)
    corrected = json.loads(text)

    # Fabricated citation stripped
    assert corrected["verified_evidence_citations"] == ["doc1:v1:sec1"]
    # Ungrounded command stripped
    assert corrected["diagnostic_step"]["command"] is None
    assert corrected["diagnostic_step"]["command_source"] is None


# ==============================================================================
# 6. Server-Validated Context Envelope Assembly
# ==============================================================================


def test_build_server_validated_evidence_and_commands() -> None:
    tool_context = MagicMock(spec=ToolContext)
    tool_context.state = MagicMock()
    tool_context.state.get.return_value = ["msg101", "msg102"]

    caller_evidence = [
        {"source_id": "fabricated:doc", "source_type": "governed_knowledge"},
        {"source_id": "case:context:1", "source_type": "case_context", "content_snippet": "Case info"},
        {
            "source_id": "mop:approved:sec1",
            "source_type": "governed_knowledge",
            "content_snippet": "Inspect connections using `check fiber`.",
        },
    ]

    evidence = build_server_validated_evidence(
        run_id=None,
        tool_context=tool_context,
        caller_evidence=caller_evidence,
    )

    source_ids = {e.source_id for e in evidence}
    assert "teams:msg101" in source_ids
    assert "teams:msg102" in source_ids
    assert "case:context:1" in source_ids
    assert "fabricated:doc" not in source_ids  # Fabricated governed knowledge rejected

    # Commands check:
    # case_context cannot authorize operational commands (Safeguard 1)
    # governed_knowledge with valid snippet authorizes matching command
    evidence.append(
        EvidenceReference(
            source_id="mop:approved:sec1",
            source_type="governed_knowledge",
            title="Approved MOP",
            content_snippet="Inspect connections using `check fiber`.",
            metadata={"lifecycle_status": "approved", "applicability_outcome": "match"},
        )
    )
    caller_commands = [
        {"command": "check fiber", "source_id": "mop:approved:sec1"},
        {"command": "unapproved reboot", "source_id": "unknown_doc"},
        {"command": "case command", "source_id": "case:context:1"},
    ]
    commands = build_server_validated_commands(evidence, caller_commands)
    cmd_names = [c.command for c in commands]
    assert "check fiber" in cmd_names
    assert "unapproved reboot" not in cmd_names
    assert "case command" not in cmd_names


# ==============================================================================
# 7. Agent Definition & Invariants
# ==============================================================================


def test_technical_authority_engineer_invariants() -> None:
    assert technical_authority_engineer.name == "technical_authority_engineer"
    # Shared Generic KM context service: read-only governed search & select, plus the read-only
    # server-issued ProcedureAction catalog for SELECTED evidence (Tranche 2). No write/execute tool.
    from backend.agents.technical_authority_engineer.procedure_actions import procedure_action_catalog

    assert technical_authority_engineer.tools == [knowledge_search, knowledge_select_evidence, procedure_action_catalog]
    assert technical_authority_engineer.input_schema == TechnicalAuthorityRequest
    assert technical_authority_engineer.output_schema == TechnicalAuthorityResponse
    assert "Technical Authority Engineer" in TECHNICAL_AUTHORITY_ENGINEER_INSTRUCTION
    assert "ONE-STEP DIAGNOSTIC DISCIPLINE" in TECHNICAL_AUTHORITY_ENGINEER_INSTRUCTION


def test_troubleshooting_manager_historical_alias() -> None:
    assert troubleshooting_manager is technical_authority_engineer
    assert troubleshooting_manager_tool is technical_authority_engineer_tool
    assert isinstance(technical_authority_engineer_tool, TechnicalAuthorityAgentTool)


# ==============================================================================
# 9. In-Process TechnicalAuthorityAgentTool Execution
# ==============================================================================


class _ScriptedSpecialistLlm(BaseLlm):
    """Scripted LLM representing the Technical Authority Engineer specialist."""

    _captured_contents: list[list[types.Content]] = PrivateAttr(default_factory=list)
    _canned_response: TechnicalAuthorityResponse = PrivateAttr()

    def __init__(self, canned_response: TechnicalAuthorityResponse, **kwargs: Any) -> None:
        super().__init__(model="scripted-tae-model", **kwargs)
        self._canned_response = canned_response
        self._captured_contents = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._captured_contents.append(list(llm_request.contents))
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=self._canned_response.model_dump_json())],
            ),
            partial=False,
        )


@pytest.mark.asyncio
async def test_technical_authority_agent_tool_in_process_execution() -> None:
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="test_app", user_id="u1", session_id="s1")

    expected_resp = TechnicalAuthorityResponse(
        outcome=TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE,
        technical_interpretation="Optical power levels are unknown.",
        missing_information=["show interface optical-power"],
    )

    specialist_llm = _ScriptedSpecialistLlm(canned_response=expected_resp)
    specialist_agent = Agent(
        name="technical_authority_engineer",
        model=specialist_llm,
        input_schema=TechnicalAuthorityRequest,
        output_schema=TechnicalAuthorityResponse,
    )

    tae_tool = TechnicalAuthorityAgentTool(agent=specialist_agent)

    class _OuterModel(BaseLlm):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(model="scripted-outer", **kwargs)

        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            # Step 1: Call technical_authority_engineer tool
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name="technical_authority_engineer",
                                args={
                                    "problem_statement": "Optical alarms on port 1/1/1",
                                    "verified_evidence": [],
                                    "approved_commands_catalog": [],
                                },
                            )
                        )
                    ],
                ),
                partial=False,
            )
            # Step 2: Final response
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part.from_text(text="Troubleshooting complete.")]),
                partial=False,
            )

    outer_agent = Agent(
        name="team_manager",
        model=_OuterModel(),
        tools=[tae_tool],
    )

    outer_runner = Runner(app_name="test_app", agent=outer_agent, session_service=session_service)

    # Attach image part to outer user content
    image_part = types.Part.from_uri(file_uri="gs://test-bucket/optical-alarm.png", mime_type="image/png")
    user_content = types.Content(
        role="user",
        parts=[types.Part.from_text(text="Diagnose optical fault"), image_part],
    )

    async for _ in outer_runner.run_async(user_id="u1", session_id="s1", new_message=user_content):
        pass

    # Assert specialist LLM was invoked and received the forwarded image part
    assert len(specialist_llm._captured_contents) == 1
    nested_contents = specialist_llm._captured_contents[0]
    last_nested_message = nested_contents[-1]

    # Verify structured request part first, then image part
    text_parts = [p.text for p in last_nested_message.parts if p.text]
    file_parts = [p for p in last_nested_message.parts if p.file_data is not None]

    assert len(text_parts) >= 1
    assert "Optical alarms on port 1/1/1" in text_parts[0]
    assert len(file_parts) == 1
    assert file_parts[0].file_data.file_uri == "gs://test-bucket/optical-alarm.png"




def test_feature_flag_default_is_enabled() -> None:
    settings = Settings(env={})
    assert settings.technical_authority_enabled is True


def test_feature_flag_truthy_values() -> None:
    for truthy in ("1", "true", "True", "TRUE", "yes", "on"):
        settings = Settings(env={"SLOPANOC_TECHNICAL_AUTHORITY_ENABLED": truthy})
        assert settings.technical_authority_enabled is True

    for falsy in ("0", "false", "False", "no", "off", ""):
        settings = Settings(env={"SLOPANOC_TECHNICAL_AUTHORITY_ENABLED": falsy})
        assert settings.technical_authority_enabled is False


def _get_tool_name(t: Any) -> str:
    return getattr(t, "name", None) or getattr(t, "__name__", str(t))


def test_team_manager_tools_when_disabled_matches_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLOPANOC_TECHNICAL_AUTHORITY_ENABLED", raising=False)
    tools = _build_team_manager_tools(enable_technical_authority=False)
    tool_names = [_get_tool_name(t) for t in tools]
    assert tool_names == [
        "incident_manager",
        "record_case_analysis",
        "record_conversation_target",
        "record_source_requirements",
    ]
    # Static imported team_manager has tools depending on default feature flags
    baseline_tools = [
        "incident_manager",
        "record_case_analysis",
        "record_conversation_target",
        "record_source_requirements",
    ]
    if get_settings().technical_authority_enabled:
        assert [_get_tool_name(t) for t in team_manager.tools] == baseline_tools + ["technical_authority_engineer"]
    else:
        assert [_get_tool_name(t) for t in team_manager.tools] == baseline_tools


def test_team_manager_tools_when_enabled_includes_tae(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLOPANOC_TECHNICAL_AUTHORITY_ENABLED", "true")
    tools = _build_team_manager_tools(enable_technical_authority=True)
    tool_names = [_get_tool_name(t) for t in tools]
    assert tool_names == [
        "incident_manager",
        "record_case_analysis",
        "record_conversation_target",
        "record_source_requirements",
        "technical_authority_engineer",
    ]

    active_team_mgr = get_team_manager(enable_technical_authority=True)
    assert any(_get_tool_name(t) == "technical_authority_engineer" for t in active_team_mgr.tools)


@pytest.mark.asyncio
async def test_technical_authority_execution_discards_legacy_troubleshooting_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
    from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
    from backend.api.troubleshooting_guidance_context import (
        pop_troubleshooting_guidance,
        register_troubleshooting_guidance,
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    run_id = "test-run-tae-overlap-123"
    token = bind_run_id(run_id)

    try:
        # Pre-seed legacy troubleshooting guidance as if incident_manager ran earlier in the turn
        legacy_guidance = TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
            next_action="Legacy check",
            command="st cell",
            evidence_requested="Cell state",
        )
        register_troubleshooting_guidance(run_id, legacy_guidance)

        # Confirm it was registered
        assert pop_troubleshooting_guidance(run_id) == legacy_guidance
        # Re-register it
        register_troubleshooting_guidance(run_id, legacy_guidance)

        # Create mock specialist runner that returns valid response
        specialist_response = TechnicalAuthorityResponse(
            outcome=TechnicalAuthorityOutcome.RECOMMENDED,
            technical_interpretation="TAE interpretation",
            verified_evidence_citations=[],
            missing_information=[],
            diagnostic_step=DiagnosticStep(
                action="TAE check optical power",
                reason="Verify SFP health",
                command=None,
                command_source=None,
                expected_evidence="Optical dBm",
                restrictions=[],
            ),
        )

        specialist_llm = _ScriptedSpecialistLlm(canned_response=specialist_response)
        specialist_agent = Agent(
            name="technical_authority_engineer",
            model=specialist_llm,
            input_schema=TechnicalAuthorityRequest,
            output_schema=TechnicalAuthorityResponse,
        )
        tae_tool = TechnicalAuthorityAgentTool(agent=specialist_agent)

        session_service = InMemorySessionService()
        await session_service.create_session(app_name="test_app", user_id="u1", session_id="s1")

        class _OuterModel(BaseLlm):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(model="scripted-outer", **kwargs)

            async def generate_content_async(
                self, llm_request: LlmRequest, stream: bool = False
            ) -> AsyncGenerator[LlmResponse, None]:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    name="technical_authority_engineer",
                                    args={
                                        "problem_statement": "Alarms on port 1/1/1",
                                        "verified_evidence": [],
                                        "approved_commands_catalog": [],
                                    },
                                )
                            )
                        ],
                    ),
                    partial=False,
                )
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part.from_text(text="Complete.")]),
                    partial=False,
                )

        outer_agent = Agent(
            name="team_manager",
            model=_OuterModel(),
            tools=[tae_tool],
        )

        outer_runner = Runner(app_name="test_app", agent=outer_agent, session_service=session_service)
        async for _ in outer_runner.run_async(
            user_id="u1",
            session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="Diagnose fault")]),
        ):
            pass

        # Assert that legacy guidance was discarded so TAE is not clobbered
        assert pop_troubleshooting_guidance(run_id) is None
    finally:
        reset_run_id(token)


@pytest.mark.asyncio
async def test_team_manager_instruction_provider_dynamic_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = MagicMock()
    ctx.state = {}
    ctx.user_id = "test-user"

    # 1. When disabled: no TAE addendum
    monkeypatch.setenv("SLOPANOC_TECHNICAL_AUTHORITY_ENABLED", "false")
    inst_disabled = await team_manager_instruction_provider(ctx)
    assert "TECHNICAL AUTHORITY ENGINEER DELEGATION" not in inst_disabled

    # 2. When enabled: dynamically includes TAE addendum
    monkeypatch.setenv("SLOPANOC_TECHNICAL_AUTHORITY_ENABLED", "true")
    inst_enabled = await team_manager_instruction_provider(ctx)
    assert "TECHNICAL AUTHORITY ENGINEER DELEGATION" in inst_enabled
    assert "recommends at most ONE evidence-grounded next check" in inst_enabled


# ==============================================================================
# GATE 1 MANDATORY HANDOFF & ARBITRATION TESTS (12 Conditions)
# ==============================================================================

@pytest.mark.asyncio
async def test_gate1_01_overlapping_operational_context_separated():
    """Condition 1: Overlapping operational context correctly separated between IM and TAE."""
    from backend.agents.team_manager.case_context import TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM
    # IM instruction limits to operational retrieval and communications
    assert "Use `incident_manager` strictly for operational retrieval" in TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM
    assert "Do NOT delegate technical diagnostic recommendations" in TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM
    # TAE instruction grants technical fault interpretation
    assert "Delegate technical fault interpretation and diagnostic step recommendations EXCLUSIVELY to the `technical_authority_engineer`" in TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM


@pytest.mark.asyncio
async def test_gate1_02_pure_operational_retrieval_remains_with_incident_manager(monkeypatch: pytest.MonkeyPatch):
    """Condition 2: Pure operational retrieval query (Teams messages, chat discovery) stays with IM."""
    from backend.agents.team_manager.case_context import team_manager_instruction_provider
    ctx = MagicMock()
    ctx.state = {}
    ctx.user_id = "test-user"
    monkeypatch.setenv("SLOPANOC_TECHNICAL_AUTHORITY_ENABLED", "true")
    inst = await team_manager_instruction_provider(ctx)
    assert "Use `incident_manager` strictly for operational retrieval (Teams messages, incident chat history, visual evidence)" in inst


@pytest.mark.asyncio
async def test_gate1_03_pure_diagnostic_troubleshooting_routes_to_tae(monkeypatch: pytest.MonkeyPatch):
    """Condition 3: Pure technical troubleshooting routes exclusively to TAE."""
    from backend.agents.team_manager.case_context import team_manager_instruction_provider
    ctx = MagicMock()
    ctx.state = {}
    ctx.user_id = "test-user"
    monkeypatch.setenv("SLOPANOC_TECHNICAL_AUTHORITY_ENABLED", "true")
    inst = await team_manager_instruction_provider(ctx)
    assert "When the user asks for technical troubleshooting, diagnosis of a fault or issue, or next steps to resolve a problem:" in inst
    assert "Delegate technical fault interpretation and diagnostic step recommendations EXCLUSIVELY to the `technical_authority_engineer` specialist tool." in inst


@pytest.mark.asyncio
async def test_gate1_04_mixed_query_routes_operational_retrieval_then_tae(monkeypatch: pytest.MonkeyPatch):
    """Condition 4: Mixed query routes operational retrieval first (IM), then technical diagnosis to TAE."""
    from backend.agents.team_manager.case_context import team_manager_instruction_provider
    ctx = MagicMock()
    ctx.state = {}
    ctx.user_id = "test-user"
    monkeypatch.setenv("SLOPANOC_TECHNICAL_AUTHORITY_ENABLED", "true")
    inst = await team_manager_instruction_provider(ctx)
    assert "If incident context or symptoms must be retrieved from Teams, first delegate to `incident_manager` to gather operational facts, then pass the verified symptoms, problem statement, and known applicability facts to `technical_authority_engineer`." in inst


@pytest.mark.asyncio
async def test_gate1_05_legacy_guidance_preserved_if_tae_not_invoked():
    """Condition 5: Legacy troubleshooting guidance is preserved if TAE is not invoked during the turn."""
    from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
    from backend.api.troubleshooting_guidance_context import (
        pop_troubleshooting_guidance,
        register_troubleshooting_guidance,
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    run_id = "gate1-test-05"
    token = bind_run_id(run_id)
    try:
        guidance = TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
            next_action="Check power connector",
            command="show port 1/1/1",
            evidence_requested="Port status",
        )
        register_troubleshooting_guidance(run_id, guidance)
        # TAE not invoked. Guidance must remain untouched.
        popped = pop_troubleshooting_guidance(run_id)
        assert popped is not None
        assert popped.interaction_mode == TroubleshootingInteractionMode.NEXT_STEP
        assert popped.next_action == "Check power connector"
    finally:
        reset_run_id(token)


@pytest.mark.asyncio
async def test_gate1_06_valid_tae_recommendation_supersedes_legacy_guidance_without_premature_deletion():
    """Condition 6: Valid TAE recommendation supersedes legacy IM guidance without premature deletion on tool entry."""
    from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
    from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
    from backend.api.troubleshooting_guidance_context import (
        pop_troubleshooting_guidance,
        register_troubleshooting_guidance,
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    run_id = "gate1-test-06"
    token = bind_run_id(run_id)
    try:
        legacy_guidance = TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
            next_action="Legacy IM check",
            command="st cell",
            evidence_requested="Cell state",
        )
        register_troubleshooting_guidance(run_id, legacy_guidance)

        tae_agent = Agent(
            name="technical_authority_engineer",
            model=_ScriptedSpecialistLlm(
                canned_response=TechnicalAuthorityResponse(
                    outcome=TechnicalAuthorityOutcome.RECOMMENDED,
                    technical_interpretation="Interpreted fault correctly.",
                    diagnostic_step=DiagnosticStep(
                        action="Inspect fiber link",
                        reason="Grounded in procedure",
                        command="show fiber-status port 1",
                        command_source="mop:v1:sec1",
                        expected_evidence="Optical signal loss",
                        restrictions=[],
                    ),
                )
            ),
            output_schema=TechnicalAuthorityResponse,
        )
        tae_tool = TechnicalAuthorityAgentTool(agent=tae_agent)

        session_service = InMemorySessionService()
        await session_service.create_session(app_name="test_app", user_id="u1", session_id="s1")

        class _OuterModel(BaseLlm):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(model="scripted-outer", **kwargs)

            async def generate_content_async(
                self, llm_request: LlmRequest, stream: bool = False
            ) -> AsyncGenerator[LlmResponse, None]:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    name="technical_authority_engineer",
                                    args={
                                        "problem_statement": "Diagnose fault",
                                        "verified_evidence": [],
                                        "approved_commands_catalog": [],
                                    },
                                )
                            )
                        ],
                    ),
                    partial=False,
                )
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part.from_text(text="Complete.")]),
                    partial=False,
                )

        outer_agent = Agent(
            name="team_manager",
            model=_OuterModel(),
            tools=[tae_tool],
        )

        outer_runner = Runner(app_name="test_app", agent=outer_agent, session_service=session_service)
        async for _ in outer_runner.run_async(
            user_id="u1",
            session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="Diagnose fault")]),
        ):
            pass

        # Assert legacy guidance was discarded so only TAE reaches user
        assert pop_troubleshooting_guidance(run_id) is None
    finally:
        reset_run_id(token)


@pytest.mark.asyncio
async def test_gate1_07_tae_insufficient_evidence_supersedes_legacy_guidance_without_falling_back():
    """Condition 7: TAE insufficient_evidence outcome supersedes legacy guidance without falling back to IM advice."""
    from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
    from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
    from backend.api.troubleshooting_guidance_context import (
        pop_troubleshooting_guidance,
        register_troubleshooting_guidance,
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    run_id = "gate1-test-07"
    token = bind_run_id(run_id)
    try:
        legacy_guidance = TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
            next_action="Legacy IM check",
            command="st cell",
            evidence_requested="Cell state",
        )
        register_troubleshooting_guidance(run_id, legacy_guidance)

        tae_agent = Agent(
            name="technical_authority_engineer",
            model=_ScriptedSpecialistLlm(
                canned_response=TechnicalAuthorityResponse(
                    outcome=TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE,
                    technical_interpretation="Cannot determine fault from available evidence.",
                    missing_information=["Missing optical power level measurement"],
                )
            ),
            output_schema=TechnicalAuthorityResponse,
        )
        tae_tool = TechnicalAuthorityAgentTool(agent=tae_agent)

        session_service = InMemorySessionService()
        await session_service.create_session(app_name="test_app", user_id="u1", session_id="s1")

        class _OuterModel(BaseLlm):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(model="scripted-outer", **kwargs)

            async def generate_content_async(
                self, llm_request: LlmRequest, stream: bool = False
            ) -> AsyncGenerator[LlmResponse, None]:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    name="technical_authority_engineer",
                                    args={
                                        "problem_statement": "Diagnose fault",
                                        "verified_evidence": [],
                                        "approved_commands_catalog": [],
                                    },
                                )
                            )
                        ],
                    ),
                    partial=False,
                )
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part.from_text(text="Complete.")]),
                    partial=False,
                )

        outer_agent = Agent(
            name="team_manager",
            model=_OuterModel(),
            tools=[tae_tool],
        )

        outer_runner = Runner(app_name="test_app", agent=outer_agent, session_service=session_service)
        async for _ in outer_runner.run_async(
            user_id="u1",
            session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="Diagnose fault")]),
        ):
            pass

        # Legacy guidance must be discarded to prevent falling back to IM advice
        assert pop_troubleshooting_guidance(run_id) is None
    finally:
        reset_run_id(token)


@pytest.mark.asyncio
async def test_gate1_08_tae_escalation_outcome_supersedes_legacy_guidance_without_falling_back():
    """Condition 8: TAE escalation_required outcome supersedes legacy guidance without falling back to IM advice."""
    from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
    from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
    from backend.api.troubleshooting_guidance_context import (
        pop_troubleshooting_guidance,
        register_troubleshooting_guidance,
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    run_id = "gate1-test-08"
    token = bind_run_id(run_id)
    try:
        legacy_guidance = TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
            next_action="Legacy IM check",
            command="st cell",
            evidence_requested="Cell state",
        )
        register_troubleshooting_guidance(run_id, legacy_guidance)

        tae_agent = Agent(
            name="technical_authority_engineer",
            model=_ScriptedSpecialistLlm(
                canned_response=TechnicalAuthorityResponse(
                    outcome=TechnicalAuthorityOutcome.ESCALATION_REQUIRED,
                    technical_interpretation="Hardware transceiver defect detected; immediate field dispatch required.",
                )
            ),
            output_schema=TechnicalAuthorityResponse,
        )
        tae_tool = TechnicalAuthorityAgentTool(agent=tae_agent)

        session_service = InMemorySessionService()
        await session_service.create_session(app_name="test_app", user_id="u1", session_id="s1")

        class _OuterModel(BaseLlm):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(model="scripted-outer", **kwargs)

            async def generate_content_async(
                self, llm_request: LlmRequest, stream: bool = False
            ) -> AsyncGenerator[LlmResponse, None]:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    name="technical_authority_engineer",
                                    args={
                                        "problem_statement": "Diagnose fault",
                                        "verified_evidence": [],
                                        "approved_commands_catalog": [],
                                    },
                                )
                            )
                        ],
                    ),
                    partial=False,
                )
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part.from_text(text="Complete.")]),
                    partial=False,
                )

        outer_agent = Agent(
            name="team_manager",
            model=_OuterModel(),
            tools=[tae_tool],
        )

        outer_runner = Runner(app_name="test_app", agent=outer_agent, session_service=session_service)
        async for _ in outer_runner.run_async(
            user_id="u1",
            session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="Diagnose fault")]),
        ):
            pass

        # Legacy guidance must be discarded to prevent falling back to IM advice
        assert pop_troubleshooting_guidance(run_id) is None
    finally:
        reset_run_id(token)


@pytest.mark.asyncio
async def test_gate1_09_tae_failure_or_malformed_yields_safe_error_never_silent_im_fallback():
    """Condition 9: TAE failure/malformed output yields safe error, never silent fallback to IM advice."""
    from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
    from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
    from backend.api.troubleshooting_guidance_context import (
        pop_troubleshooting_guidance,
        register_troubleshooting_guidance,
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    run_id = "gate1-test-09"
    token = bind_run_id(run_id)
    try:
        legacy_guidance = TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
            next_action="Legacy IM advice that must not be used",
            command="st cell",
            evidence_requested="Cell state",
        )
        register_troubleshooting_guidance(run_id, legacy_guidance)

        # Model raises an exception to simulate runner failure
        class _FailingModel(BaseLlm):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(model="test-fail-model", **kwargs)

            async def generate_content_async(
                self, llm_request: LlmRequest, stream: bool = False
            ) -> AsyncGenerator[LlmResponse, None]:
                raise RuntimeError("Simulated specialist model crash")
                yield  # pragma: no cover

        tae_agent = Agent(
            name="technical_authority_engineer",
            model=_FailingModel(),
            output_schema=TechnicalAuthorityResponse,
        )
        tae_tool = TechnicalAuthorityAgentTool(agent=tae_agent)

        session_service = InMemorySessionService()
        await session_service.create_session(app_name="test_app", user_id="u1", session_id="s1")

        class _OuterModel(BaseLlm):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(model="scripted-outer", **kwargs)

            async def generate_content_async(
                self, llm_request: LlmRequest, stream: bool = False
            ) -> AsyncGenerator[LlmResponse, None]:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    name="technical_authority_engineer",
                                    args={
                                        "problem_statement": "Diagnose fault",
                                        "verified_evidence": [],
                                        "approved_commands_catalog": [],
                                    },
                                )
                            )
                        ],
                    ),
                    partial=False,
                )
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part.from_text(text="Complete.")]),
                    partial=False,
                )

        outer_agent = Agent(
            name="team_manager",
            model=_OuterModel(),
            tools=[tae_tool],
        )

        outer_runner = Runner(app_name="test_app", agent=outer_agent, session_service=session_service)
        async for _ in outer_runner.run_async(
            user_id="u1",
            session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="Diagnose fault")]),
        ):
            pass

        # Legacy guidance MUST be discarded so chat_service does not render IM advice as completion override
        assert pop_troubleshooting_guidance(run_id) is None
    finally:
        reset_run_id(token)


@pytest.mark.asyncio
async def test_gate1_10_grounded_operational_commands_preserved():
    """Condition 10: Operational commands grounded in approved catalog or verified evidence are preserved."""
    request_payload = {
        "problem_statement": "Diagnose optical loss",
        "verified_evidence": [
            {
                "source_id": "mop:v1:sec1",
                "source_type": "governed_knowledge",
                "title": "Fiber Test MOP",
                "content_snippet": "Execute command `show fiber-status port 1` to verify optical rx levels.",
            }
        ],
        "approved_commands_catalog": [
            {
                "command": "show fiber-status port 1",
                "source_id": "mop:v1:sec1",
                "procedure_section": "sec1",
            }
        ],
    }
    response_payload = {
        "outcome": "recommended",
        "technical_interpretation": "Fiber optical levels need verification.",
        "verified_evidence_citations": ["mop:v1:sec1"],
        "diagnostic_step": {
            "action": "Verify optical power",
            "reason": "Verify optical signal levels against threshold.",
            "command": "show fiber-status port 1",
            "command_source": "mop:v1:sec1",
            "expected_evidence": "Optical rx power in dBm",
            "restrictions": [],
        },
    }
    sanitized, modified = validate_technical_authority_payload(response_payload, request_payload)
    assert modified is False
    step = sanitized["diagnostic_step"]
    assert step["command"] == "show fiber-status port 1"
    assert step["command_source"] == "mop:v1:sec1"


@pytest.mark.asyncio
async def test_gate1_11_ungrounded_operational_commands_stripped():
    """Condition 11: Ungrounded operational commands are stripped and safety restrictions appended."""
    request_payload = {
        "problem_statement": "Diagnose optical loss",
        "verified_evidence": [
            {
                "source_id": "mop:v1:sec1",
                "source_type": "governed_knowledge",
                "title": "Fiber Test MOP",
                "content_snippet": "Execute command `show fiber-status port 1` to verify optical rx levels.",
            }
        ],
        "approved_commands_catalog": [
            {
                "command": "show fiber-status port 1",
                "source_id": "mop:v1:sec1",
                "procedure_section": "sec1",
            }
        ],
    }
    response_payload = {
        "outcome": "recommended",
        "technical_interpretation": "Transceiver reboot attempt.",
        "verified_evidence_citations": ["mop:v1:sec1"],
        "diagnostic_step": {
            "action": "Reboot transceiver",
            "reason": "Attempt power cycle recovery.",
            "command": "reboot transceiver port 1 --force",  # Fabricated command not in evidence or catalog
            "command_source": "mop:v1:sec1",
            "expected_evidence": "Port reboot logs",
            "restrictions": [],
        },
    }
    sanitized, modified = validate_technical_authority_payload(response_payload, request_payload)
    assert modified is True
    step = sanitized["diagnostic_step"]
    assert step["command"] is None
    assert step["command_source"] is None
    assert any("[Command stripped: unapproved operational command]" in r for r in step["restrictions"])


@pytest.mark.asyncio
async def test_gate1_12_tae_disabled_preserves_baseline_incident_manager_behavior(monkeypatch: pytest.MonkeyPatch):
    """Condition 12: When TAE is disabled, original Incident Manager baseline guidance behavior is preserved."""
    from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
    from backend.agents.team_manager.case_context import team_manager_instruction_provider
    from backend.api.troubleshooting_guidance_context import (
        pop_troubleshooting_guidance,
        register_troubleshooting_guidance,
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    ctx = MagicMock()
    ctx.state = {}
    ctx.user_id = "test-user"

    # TAE disabled
    monkeypatch.setenv("SLOPANOC_TECHNICAL_AUTHORITY_ENABLED", "false")
    inst = await team_manager_instruction_provider(ctx)
    assert "TECHNICAL AUTHORITY ENGINEER DELEGATION" not in inst

    # Legacy guidance remains fully active and unmolested
    run_id = "gate1-test-12"
    token = bind_run_id(run_id)
    try:
        sample_guidance = TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
            next_action="Baseline IM next step",
            command="st cell",
            evidence_requested="Cell state",
        )
        register_troubleshooting_guidance(run_id, sample_guidance)
        popped = pop_troubleshooting_guidance(run_id)
        assert popped is not None
        assert popped.next_action == "Baseline IM next step"
    finally:
        reset_run_id(token)
