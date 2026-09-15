"""LIVE-CORR-4 -- DEF-0045: Free-Text Operational Content Authority
Boundary.

DEF-0041's own final live isolation (docs/DEFECT_REGISTER.md) confirmed,
twice, independently, against the real live stack, that the STRUCTURED
`command`/`step.command` grounding mechanism (DEF-0024/0026/0027,
LIVE-CORR-3A's `enforce_structural_operational_integrity`, LIVE-CORR-3B's
`_verify_source_section_reference`) correctly rejects a cross-section
command -- yet the EXACT SAME verbatim command string still reached the
user through the free-text `action` prose of an UNRELATED step in the
same `full_procedure_steps` list. This is DEF-0045: no existing
mechanism ever inspected `action`/`interpretation`/`next_action`/
`evidence_requested` at all.

This file proves:
  1. `_scrub_narrative_operational_leaks` (evidence.py) in isolation --
     the new deterministic mechanism.
  2. `enforce_procedure_scoped_command_grounding_with_reason` wires it in
     correctly for both FULL_PROCEDURE and NEXT_STEP, for every field,
     without over-suppressing unrelated prose or legitimately grounded
     commands.
  3. The actual rendering boundary -- `chat_service.py`'s own completion
     path -- via a real `enforce_incident_manager_response_integrity`
     callback + `ChatService`/`FakeRunner`.
  4. Canonical live/history equality for the exact DEF-0045 shape, driven
     through the REAL `team_manager`/`incident_manager` agent objects
     (only each one's own scripted `model`; every real tool/callback,
     including the production `after_agent_callback` wiring, stays as
     production uses it).

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write. Live acceptance against the real stack was
NOT separately performed in this pass (no browser/live-Gemini access in
this environment) -- consistent with every prior LIVE-CORR-3/3B pass's
own honest "no browser access" note.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.agents import Agent as _Agent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import PrivateAttr

from backend.agents.incident_manager.evidence import (
    CommandGroundingReason,
    _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT,
    _scrub_narrative_operational_leaks,
    enforce_incident_manager_response_integrity,
    enforce_procedure_scoped_command_grounding_with_reason,
)
from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
    TroubleshootingOperationalEffect,
    TroubleshootingStep,
)
from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    RequestContract,
    RequestIntent,
    RequestedOutput,
)
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.provenance.contracts import (
    KnowledgeEvidenceItem,
    KnowledgeEvidenceReference,
    KnowledgeEvidenceSelectionKey,
    KnowledgeEvidenceSet,
)
from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta
from backend.tools.knowledge import runtime as rt

_HGET_COMMAND = "hget near Rfportref"
_ALT_COMMAND = "alt"
_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_ACTIVE_HEADING_QUESTION = "How do I troubleshoot HW Partial Fault using the approved procedure?"
_KNOWLEDGE_ID = "A5-VALIDATION-DOCUMENT1"


def _item(section_id: str, heading: str, content: str) -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=_KNOWLEDGE_ID, sequence=0, content=content, heading=heading)
    source = KnowledgeSource(source_system="test", source_id=f"{_KNOWLEDGE_ID}-doc")
    reference = KnowledgeEvidenceReference(
        knowledge_id=_KNOWLEDGE_ID, version_label="v2", section_id=section_id, source_system="test", source_id=f"{_KNOWLEDGE_ID}-doc"
    )
    return KnowledgeEvidenceItem(
        reference=reference, title=_KNOWLEDGE_ID, document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section
    )


def _select_two_sibling_sections(run_id: str) -> None:
    """DEF-0041/DEF-0045's own documented shape: two sections of the SAME
    governed document -- an active "HW Partial Fault" section that does
    NOT contain `_HGET_COMMAND`, and a merely SUPPORTING sibling "HW
    Fault" section whose real content DOES."""
    active = _item(
        f"{_KNOWLEDGE_ID}:v2:section-0001",
        "HW Partial Fault",
        "HW Partial Fault\nThis section covers partial hardware faults on the RRU.\nFor Antenna Group / Unit alarms, execute: alt\n",
    )
    supporting = _item(
        f"{_KNOWLEDGE_ID}:v2:section-0002",
        "HW Fault",
        f"HW Fault\nThis section covers general hardware faults.\nFor Antenna Group / Unit alarms, execute the command to fetch the associated RRU: {_HGET_COMMAND}\n",
    )
    rt.get_or_init_run_state(run_id)
    execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=[active, supporting]))
    rt.record_search_result(run_id, execution)
    keys = [
        KnowledgeEvidenceSelectionKey(knowledge_id=i.reference.knowledge_id, version_label=i.reference.version_label, section_id=i.reference.section_id)
        for i in (active, supporting)
    ]
    rt.select_evidence(run_id, keys)


# =============================================================================
# Section A -- `_scrub_narrative_operational_leaks` in isolation
# =============================================================================


def test_no_op_when_no_rejected_commands() -> None:
    guidance = TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP, next_action="Do something.")
    corrected, scrubbed = _scrub_narrative_operational_leaks(guidance, frozenset())
    assert scrubbed is False
    assert corrected is guidance


def test_full_procedure_contaminated_action_replaced_unrelated_step_untouched() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[
            TroubleshootingStep(action="Check the alarm log.", command=None),
            TroubleshootingStep(action=f"Execute `{_HGET_COMMAND}` to fetch the associated RRU.", command=None),
        ],
    )
    corrected, scrubbed = _scrub_narrative_operational_leaks(guidance, frozenset({_HGET_COMMAND}))
    assert scrubbed is True
    assert corrected.full_procedure_steps[0].action == "Check the alarm log."
    assert _HGET_COMMAND not in corrected.full_procedure_steps[1].action
    assert corrected.full_procedure_steps[1].action == _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT


def test_next_step_next_action_replaced() -> None:
    guidance = TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP, next_action=f"Run `{_RRU_COMMAND}` now.")
    corrected, scrubbed = _scrub_narrative_operational_leaks(guidance, frozenset({_RRU_COMMAND}))
    assert scrubbed is True
    assert corrected.next_action == _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT


def test_next_step_interpretation_replaced_next_action_untouched() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation=f"The approved procedure says to run `{_RRU_COMMAND}`.",
        next_action="Confirm the unit type first.",
    )
    corrected, scrubbed = _scrub_narrative_operational_leaks(guidance, frozenset({_RRU_COMMAND}))
    assert scrubbed is True
    assert corrected.interpretation == _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT
    assert corrected.next_action == "Confirm the unit type first."


def test_next_step_evidence_requested_replaced() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        next_action="Confirm the unit type.",
        evidence_requested=f"Confirm whether `{_RRU_COMMAND}` was already run.",
    )
    corrected, scrubbed = _scrub_narrative_operational_leaks(guidance, frozenset({_RRU_COMMAND}))
    assert scrubbed is True
    assert corrected.evidence_requested == _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT


# =============================================================================
# Section B -- `enforce_procedure_scoped_command_grounding_with_reason`
# =============================================================================


def test_1_exact_live_defect_full_procedure_leak_in_unrelated_step_scrubbed() -> None:
    """Test 1 -- the exact live DEF-0045 shape: `step.command` is
    rejected (cross-section), and the SAME command string is ALSO
    reproduced, verbatim, as actionable prose in a DIFFERENT step's own
    `action`. Must fail against pre-fix behavior (the leaking step's
    action would previously be returned unchanged)."""
    run_id = "def0045-t1"
    _select_two_sibling_sections(run_id)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        interpretation="This is a HW Partial Fault.",
        full_procedure_steps=[
            TroubleshootingStep(action="Check the alarm log.", command=None, operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ),
            TroubleshootingStep(
                action=f"Execute `{_HGET_COMMAND}` to fetch the associated RRU. Then, restart the identified RRU.",
                command=None,
                operational_effect=TroubleshootingOperationalEffect.OBSERVATION,
            ),
            TroubleshootingStep(
                action="For Antenna Group / Unit alarms, execute the command to fetch the associated RRU.",
                command=_HGET_COMMAND,
                operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION,
            ),
        ],
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is True
    assert corrected.full_procedure_steps[2].command is None
    assert reason == CommandGroundingReason.TRUE_ABSENCE
    assert _HGET_COMMAND not in corrected.full_procedure_steps[1].action
    assert corrected.full_procedure_steps[1].action == _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT
    assert corrected.full_procedure_steps[0].action == "Check the alarm log."


def test_2_next_step_rejected_command_leak_in_next_action_scrubbed() -> None:
    """Test 2 -- the same bypass, for NEXT_STEP guidance's own `next_action`."""
    run_id = "def0045-t2"
    _select_two_sibling_sections(run_id)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="This is a HW Partial Fault.",
        next_action=f"Execute `{_HGET_COMMAND}` to fetch the associated RRU, then restart it.",
        command=_HGET_COMMAND,
        evidence_requested="Confirm the RRU identifier.",
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is True
    assert corrected.command is None
    assert _HGET_COMMAND not in corrected.next_action
    assert corrected.next_action == _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT
    assert corrected.evidence_requested == "Confirm the RRU identifier."


def test_3_next_step_rejected_command_leak_in_interpretation_scrubbed_no_over_suppression() -> None:
    """Test 3 -- the same bypass, for `interpretation`; ordinary
    explanatory `next_action` text must remain untouched."""
    run_id = "def0045-t3"
    _select_two_sibling_sections(run_id)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation=f"The supporting HW Fault section says to run `{_HGET_COMMAND}`.",
        next_action="Confirm the unit is affected before proceeding.",
        command=_HGET_COMMAND,
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is True
    assert corrected.command is None
    assert corrected.interpretation == _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT
    assert corrected.next_action == "Confirm the unit is affected before proceeding."


def test_4_legitimate_grounded_command_and_matching_action_are_never_touched() -> None:
    """Test 4 -- positive control: a genuinely grounded exact command
    (present in the active section's own real content) still renders,
    and prose that legitimately mentions it is never scrubbed -- no
    global command suppression."""
    run_id = "def0045-t4"
    _select_two_sibling_sections(run_id)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="HW Partial Fault confirmed.",
        next_action=f"For Antenna Group / Unit alarms, execute `{_ALT_COMMAND}`.",
        command=_ALT_COMMAND,
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is False
    assert reason is None
    assert corrected.command == _ALT_COMMAND
    assert _ALT_COMMAND in corrected.next_action


def test_5_prohibition_prose_is_safely_suppressed_not_turned_into_an_instruction() -> None:
    """Test 5 -- the exact prohibition-semantics case (section 8): a
    substring check cannot distinguish "do not run X" from "run X", so
    the field is safely, wholly suppressed rather than surgically edited
    into something that could read as an instruction."""
    run_id = "def0045-t5"
    _select_two_sibling_sections(run_id)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[
            TroubleshootingStep(
                action=f"Do not run `{_HGET_COMMAND}` here -- the approved HW Partial Fault procedure prohibits it.",
                command=None,
                operational_effect=TroubleshootingOperationalEffect.REFERENCE_DESCRIPTION,
            ),
            TroubleshootingStep(
                action="For Antenna Group / Unit alarms, execute the command to fetch the associated RRU.",
                command=_HGET_COMMAND,
                operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION,
            ),
        ],
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is True
    assert corrected.full_procedure_steps[1].command is None
    assert _HGET_COMMAND not in corrected.full_procedure_steps[0].action
    assert corrected.full_procedure_steps[0].action == _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT


def test_6_unrelated_prose_without_rejected_string_is_never_touched() -> None:
    """Test 6 -- ordinary explanatory prose containing no rejected
    operational string is completely unaffected, even in a guidance
    object where another step's command WAS rejected."""
    run_id = "def0045-t6"
    _select_two_sibling_sections(run_id)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        interpretation="This is a HW Partial Fault.",
        full_procedure_steps=[
            TroubleshootingStep(action="Check the alarm log.", command=None, operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ),
            TroubleshootingStep(action="Review the antenna group visually.", command=None, operational_effect=TroubleshootingOperationalEffect.OBSERVATION),
            TroubleshootingStep(
                action="For Antenna Group / Unit alarms, execute the command to fetch the associated RRU.",
                command=_HGET_COMMAND,
                operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION,
            ),
        ],
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is True
    assert corrected.full_procedure_steps[2].command is None
    assert corrected.full_procedure_steps[0].action == "Check the alarm log."
    assert corrected.full_procedure_steps[1].action == "Review the antenna group visually."
    assert corrected.interpretation == "This is a HW Partial Fault."


def test_7_cross_section_command_without_narrative_leak_remains_withheld_unaffected() -> None:
    """Test 7 -- regression safety: the pre-existing DEF-0041/0024
    cross-section withholding behavior (no narrative leak present at
    all) is completely unaffected by this pass."""
    run_id = "def0045-t7"
    _select_two_sibling_sections(run_id)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="This is a HW Partial Fault.",
        next_action="For Antenna Group / Unit alarms, fetch the associated RRU.",
        command=_HGET_COMMAND,
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is True
    assert corrected.command is None
    assert reason == CommandGroundingReason.TRUE_ABSENCE
    assert corrected.next_action == "For Antenna Group / Unit alarms, fetch the associated RRU."
    assert corrected.interpretation == "This is a HW Partial Fault."


# =============================================================================
# Section C -- the actual rendering boundary (section 16): structured
# specialist result -> guidance registration -> grounding -> execution
# policy -> rendering -> chat_service completion
# =============================================================================


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.parts = parts


class _FakeAgentEvent:
    def __init__(self, author: str, text: Optional[str] = None) -> None:
        self.author = author
        self.content = _FakeContent([_FakePart(text)]) if text is not None else None


class _FakeAgentSession:
    def __init__(self, events: list[_FakeAgentEvent]) -> None:
        self.events = events


class _FakeAgentCallbackContext:
    def __init__(self, events: list[_FakeAgentEvent], user_content_text: Optional[str], state: Optional[dict[str, Any]] = None) -> None:
        self.session = _FakeAgentSession(events)
        self.state = dict(state or {})
        self.user_content = _FakeContent([_FakePart(user_content_text)]) if user_content_text is not None else None


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


def _def0045_raw_incident_manager_response() -> str:
    return json.dumps(
        {
            "outcome": "ok",
            "summary": "placeholder -- must be overwritten by the deterministic renderer",
            "evidence": [],
            "candidate_titles": [],
            "detail": None,
            "troubleshooting_guidance": {
                "interaction_mode": "full_procedure",
                "interpretation": "This is a HW Partial Fault.",
                "full_procedure_steps": [
                    {"action": "Check the alarm log.", "command": None, "operational_effect": "diagnostic_read"},
                    {
                        "action": f"Execute `{_HGET_COMMAND}` to fetch the associated RRU. Then, restart the identified RRU.",
                        "command": None,
                        "operational_effect": "observation",
                    },
                    {
                        "action": "For Antenna Group / Unit alarms, execute the command to fetch the associated RRU.",
                        "command": _HGET_COMMAND,
                        "operational_effect": "state_change_recommendation",
                    },
                ],
            },
        }
    )


@pytest.mark.asyncio
async def test_full_pipeline_rendering_boundary_narrative_leak_never_reaches_completed_content() -> None:
    """Section 16 -- drives structured specialist result -> guidance
    registration -> grounding (via the REAL `enforce_incident_manager_
    response_integrity` callback, not an isolated pure function) ->
    execution policy -> rendering -> `chat_service.py` completion, and
    asserts against the ACTUAL user-visible final text."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select_two_sibling_sections(run_id)
        ctx = _FakeAgentCallbackContext(
            events=[_FakeAgentEvent("incident_manager", _def0045_raw_incident_manager_response())],
            user_content_text=json.dumps({"chat_topic": "HW Partial Fault", "question": _ACTIVE_HEADING_QUESTION}),
        )
        await enforce_incident_manager_response_integrity(ctx)
        contract = RequestContract(
            intent=RequestIntent.PROCEDURE,
            requested_output=RequestedOutput.PROCEDURE_STEPS,
            subject="HW Partial Fault",
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(
        session_id, "Give me the complete approved procedure for HW Partial Fault, all steps, do not wait for confirmation.", "api-user"
    ):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    content = completed.data["content"]
    assert _HGET_COMMAND not in content
    assert _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT in content


@pytest.mark.asyncio
async def test_8_canonical_live_completed_text_equals_refreshed_history_after_narrative_leak_scrub(tmp_path: Any) -> None:
    """Test 8 / section 20 -- for the exact DEF-0045 live shape, driven
    through the REAL `team_manager`/`incident_manager` agent objects
    (only each one's own `model` swapped for a scripted one -- every real
    tool/callback, including the production `after_agent_callback` wired
    on `incident_manager` in `agent.py`, stays exactly as production uses
    it): live `MESSAGE_COMPLETED` text equals the refreshed canonical-
    history projection, byte-for-byte, and the rejected command never
    reappears after refresh."""
    from backend.agents.incident_manager.agent import incident_manager
    from backend.agents.incident_manager.schemas import IncidentManagerRequest
    from backend.agents.team_manager.case_tools import record_case_analysis
    from backend.agents.team_manager.conversation_target import record_conversation_target
    from backend.agents.team_manager.multimodal_agent_tool import MultimodalAgentTool
    from backend.agents.team_manager.read_continuation_enforcement import enforce_read_continuation
    from backend.agents.team_manager.request_contract import record_request_contract, validate_and_persist_request_contract
    from backend.agents.team_manager.selection_delegation_guard import (
        block_repeated_delegation_after_selection_needed,
        record_selection_needed,
    )
    from backend.agents.team_manager.source_requirements import record_source_requirements
    from backend.agents.team_manager.state_sync import sync_incident_manager_result_to_state
    from backend.api.chat_service import ChatService
    from backend.api.session_history_service import get_session_history
    from backend.api.session_service import APP_NAME, ApiSessionService
    from backend.api.turn_context import current_run_id
    from backend.attachments.repository import AttachmentRepository
    from backend.attachments.service import AttachmentService

    def _function_call_response(name: str, args: dict[str, Any], call_id: str) -> LlmResponse:
        part = types.Part.from_function_call(name=name, args=args)
        part.function_call.id = call_id
        return LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)

    def _final_text_response(text: str) -> LlmResponse:
        return LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]), partial=False)

    class _OuterLlm(BaseLlm):
        _step: list[int] = PrivateAttr(default_factory=lambda: [0])

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(model="scripted-def0045-outer", **kwargs)

        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
            step = self._step[0]
            self._step[0] += 1
            if step == 0:
                yield _function_call_response(
                    "record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}, "def0045-o0"
                )
                return
            if step == 1:
                yield _function_call_response(
                    "record_request_contract",
                    {"intent": "procedure", "requested_output": "procedure_steps", "subject": "HW Partial Fault", "missing_context": []},
                    "def0045-o1",
                )
                return
            if step == 2:
                request = IncidentManagerRequest(
                    chat_topic="HW Partial Fault", question=_ACTIVE_HEADING_QUESTION, requires_governed_knowledge=True
                )
                part = types.Part.from_function_call(name="incident_manager", args=request.model_dump(mode="json"))
                part.function_call.id = "def0045-o2"
                yield LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)
                return
            yield _final_text_response("unused, replaced deterministically")

    class _InnerLlm(BaseLlm):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(model="scripted-def0045-inner", **kwargs)

        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part.from_text(text=_def0045_raw_incident_manager_response())]),
                partial=False,
            )

    def _seed_rt_evidence_after_source_requirements(tool: Any, args: dict[str, Any], tool_context: Any, tool_response: Any) -> None:
        if getattr(tool, "name", None) != "record_source_requirements":
            return None
        _select_two_sibling_sections(current_run_id())
        return None

    incident_manager_scripted = incident_manager.model_copy(update={"model": _InnerLlm()})
    incident_manager_tool = MultimodalAgentTool(agent=incident_manager_scripted)
    outer_agent = _Agent(
        name="team_manager",
        model=_OuterLlm(),
        tools=[
            incident_manager_tool,
            record_case_analysis,
            record_conversation_target,
            record_source_requirements,
            record_request_contract,
        ],
        before_tool_callback=[enforce_read_continuation, block_repeated_delegation_after_selection_needed],
        after_tool_callback=[
            _seed_rt_evidence_after_source_requirements,
            sync_incident_manager_result_to_state,
            record_selection_needed,
            validate_and_persist_request_contract,
        ],
    )

    db_file = tmp_path / "def0045_history.db"
    session_service = ApiSessionService(adk_session_service=DatabaseSessionService(f"sqlite+aiosqlite:///{db_file.as_posix()}"))
    session_id = await session_service.create_session("api-user")
    runner = Runner(app_name=APP_NAME, agent=outer_agent, session_service=session_service.adk_session_service)
    attachment_service = AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))
    chat_service = ChatService(session_service, runner=runner, attachment_service=attachment_service)

    live_text = None
    async for event in chat_service.execute_turn_events(
        session_id, "Give me the complete approved procedure for HW Partial Fault, all steps, do not wait for confirmation.", "api-user"
    ):
        if event.type.value == "message.completed":
            live_text = event.data["content"]

    assert live_text is not None
    assert _HGET_COMMAND not in live_text
    assert _NARRATIVE_OPERATIONAL_LEAK_FALLBACK_TEXT in live_text

    history = await get_session_history(session_service, attachment_service, session_id, "api-user")
    assistant_messages = [m for m in history.messages if m.role == "assistant"]
    assert assistant_messages
    assert assistant_messages[-1].text == live_text
    assert _HGET_COMMAND not in assistant_messages[-1].text
