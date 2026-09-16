"""LIVE-CORR-13 -- Restore Policy-Bound Final Responses After Control-Plane
Redesign.

CONFIRMED LIVE DEFECTS:

A. "give me a command to restart an RRU" -- backend proved `status=
   needs_information may_emit_command=False`, yet the UI received the
   generic "The assistant could not complete this request." with no
   user-visible clarification.

B. "how can i check alarms on ericsson enm ?" -- backend proved
   `status=allow may_emit_command=False` with grounding already stripping
   a cross-procedure-evidence command, yet the UI again received the same
   generic failure instead of the remaining safe troubleshooting content.

C. "hello" / "what can you do ?" -- correctly classified as
   `general_conversation`, but the user-visible text additionally exposed
   internal governance narration ("I'm calling `record_source_
   requirements`...", "I'm calling `record_request_contract`...").

PROVEN ROOT CAUSES:

A/B. `chat_service.py`'s own response-completion block (guidance
   rendering, `requires_unstructured_response_backstop`, and the SEQ-05
   final authority boundary/validator) was gated on `error is None and
   final_text is not None`. A turn whose Runner ends on a structured/
   tool-call event with no accompanying prose (`_extract_final_text`
   correctly returns `None` for such an event -- the model's ordinary
   behavior after a specialist delegation) skipped this ENTIRE block, even
   though `captured_troubleshooting_guidance`/`execution_decision` already
   held everything needed to produce a safe response. The turn then fell
   through to the generic "no final text at all" failure. FIX: the guard
   is now `error is None` alone -- every branch inside already tolerates
   a `None` starting `final_text`.

C. `presentation_team_manager` (agent.py, `tools=[]`) shared `team_
   manager_instruction_provider`, which falls back to the FULL `TEAM_
   MANAGER_INSTRUCTION` whenever there is no pending trusted specialist
   result -- exactly the `GENERAL_CONVERSATION` shape. That instruction
   unconditionally tells the model to call `record_request_contract`/
   `record_source_requirements` "for EVERY request, with no exception",
   but this agent structurally cannot call them (`tools=[]`). FIX: a new,
   dedicated `presentation_team_manager_instruction_provider` (case_
   context.py) falls back to `TEAM_MANAGER_CONVERSATIONAL_PRESENTATION_
   INSTRUCTION` instead -- a narrower instruction that never mentions
   those tools and explicitly forbids narrating any internal call.

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
    TroubleshootingOperationalEffect,
    TroubleshootingStep,
)
from backend.agents.team_manager.prompts import (
    TEAM_MANAGER_CONVERSATIONAL_PRESENTATION_INSTRUCTION,
    TEAM_MANAGER_INSTRUCTION,
)
from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    RequestClass,
    RequestContract,
    RequestIntent,
    RequestedOutput,
)
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_AAS_COMMAND = "accn FieldReplaceableUnit=AAS-1 restartunit 1 1 1"


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


def _no_final_text_event() -> FakeEvent:
    """The model's real, ordinary behavior after delegating to a
    specialist and letting a structured tool call carry the result: the
    turn's own final Runner event is `is_final_response()=True` but has NO
    accompanying text content -- `_extract_final_text` correctly returns
    `None` for this shape."""
    return FakeEvent(text=None, final=True)


async def _run_and_collect_completed(chat_service: Any, session_id: str, message: str) -> Optional[dict]:
    completed = None
    async for event in chat_service.execute_turn_events(session_id, message, "api-user"):
        if event.type.value == "message.completed":
            completed = event.data
    return completed


# =============================================================================
# A -- EXACT_COMMAND + NEEDS_INFORMATION + may_emit_command=False ->
#      non-empty safe clarification, even with no accompanying free text
#      and no structured troubleshooting_guidance at all.
# =============================================================================


@pytest.mark.asyncio
async def test_a_exact_command_needs_information_produces_safe_clarification() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            missing_context=["unit_id", "unit_type"],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        _no_final_text_event(),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = await _run_and_collect_completed(chat_service, session_id, "give me a command to restart an RRU")

    assert completed is not None
    content = completed["content"]
    assert content  # non-empty -- THE defect: this previously never arrived at all
    assert "unit" in content.lower()


# =============================================================================
# B/C -- ALLOW + PROCEDURE_TROUBLESHOOTING + may_emit_command=False, with a
#        cross-procedure-evidence-stripped command already removed from the
#        structured guidance -> the remaining safe troubleshooting content
#        still reaches the user, with no accompanying free text from the
#        Runner either.
# =============================================================================


@pytest.mark.asyncio
async def test_b_allow_procedure_command_withheld_still_produces_safe_response() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.PROCEDURE,
            requested_output=RequestedOutput.PROCEDURE_STEPS,
            subject="check alarms on Ericsson ENM",
            ambiguity=False,
            missing_context=[],
            request_class=RequestClass.PROCEDURE_TROUBLESHOOTING,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        # Mirrors the live grounding trace: cross-procedure-evidence already
        # stripped the command from this step BEFORE chat_service.py's own
        # policy layer ever runs -- the safe `action` text remains.
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
                full_procedure_steps=[
                    TroubleshootingStep(action="Open the ENM Alarm List application.", command=None),
                    TroubleshootingStep(action="Filter alarms by the affected node.", command=None),
                ],
                operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ,
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        _no_final_text_event(),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = await _run_and_collect_completed(chat_service, session_id, "how can i check alarms on ericsson enm ?")

    assert completed is not None
    content = completed["content"]
    assert content
    assert "Open the ENM Alarm List application." in content
    assert "Filter alarms by the affected node." in content
    assert _RRU_COMMAND not in content
    assert _AAS_COMMAND not in content


# =============================================================================
# D -- GENERAL_CONVERSATION presentation instruction never mentions the
#      tools this tools=[] agent cannot call, and explicitly forbids
#      narrating internal control-plane mechanics.
# =============================================================================


def test_d_conversational_presentation_instruction_omits_uncallable_tools() -> None:
    for forbidden in ("record_request_contract(", "record_source_requirements("):
        assert forbidden not in TEAM_MANAGER_CONVERSATIONAL_PRESENTATION_INSTRUCTION
    assert "never mention" in TEAM_MANAGER_CONVERSATIONAL_PRESENTATION_INSTRUCTION.lower()


def test_d_full_orchestration_instruction_still_mandates_the_tools_for_agents_that_have_them() -> None:
    """Non-regression: `team_manager`/`operational_team_manager` (which DO
    retain the relevant tools, or -- for `operational_team_manager` --
    have them removed for a different, already-covered reason) are
    untouched by this pass; only the dedicated `tools=[]` presentation
    provider changed."""
    assert "record_request_contract(" in TEAM_MANAGER_INSTRUCTION
    assert "record_source_requirements(" in TEAM_MANAGER_INSTRUCTION


@pytest.mark.asyncio
async def test_d_presentation_team_manager_uses_the_conversational_instruction_with_no_pending_result() -> None:
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.agents.readonly_context import ReadonlyContext
    from google.adk.sessions import InMemorySessionService

    from backend.agents.team_manager.agent import presentation_team_manager

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="t", user_id="alice", state={})
    ctx = ReadonlyContext(
        InvocationContext(
            session_service=session_service, invocation_id="test-inv", agent=presentation_team_manager, session=session
        )
    )
    rendered = await presentation_team_manager.canonical_instruction(ctx)

    assert "record_request_contract(" not in rendered[0]
    assert "record_source_requirements(" not in rendered[0]
    assert "never mention" in rendered[0].lower()


# =============================================================================
# E -- the final-output validator never leaves `final_text` empty/`None`
#      when it rejects a candidate.
# =============================================================================


def test_e_final_output_validator_never_produces_an_empty_success() -> None:
    from backend.agents.team_manager.authorized_response import build_authorized_response_context
    from backend.agents.team_manager.final_output_validator import validate_final_output
    from backend.agents.team_manager.request_execution_policy import RequestExecutionDecision, RequestExecutionStatus

    decision = RequestExecutionDecision(
        status=RequestExecutionStatus.ALLOW, request_class="exact_command", may_emit_command=False, reason="test"
    )
    context = build_authorized_response_context(decision, known_commands=[_RRU_COMMAND])
    rendered, ok = validate_final_output(f"Run:\n\n{_RRU_COMMAND}", decision, context)

    assert ok is False
    assert rendered is not None
    assert rendered != ""
    assert _RRU_COMMAND not in rendered


# =============================================================================
# F -- a successful turn always emits MESSAGE_COMPLETED with non-empty text.
# =============================================================================


@pytest.mark.asyncio
async def test_f_ordinary_general_conversation_turn_still_completes_normally() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject=None,
            ambiguity=False,
            missing_context=[],
            request_class=RequestClass.GENERAL_CONVERSATION,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="Hello! How can I help?", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = await _run_and_collect_completed(chat_service, session_id, "hello")

    assert completed is not None
    assert completed["content"] == "Hello! How can I help?"
