"""LIVE-CORR-8 -- Request Class Must Be the Authoritative Governance
Boundary.

CONFIRMED LIVE DEFECT: "what is the cmd i need to run to check and list
alarms present?" produced `intent=procedure, requested_output=exact_
command`. `is_exact_command_response_permitted` required `intent==
RequestIntent.COMMAND` specifically, and `is_full_procedure_response_
permitted`/`is_next_step_response_permitted` both required a DIFFERENT
`requested_output` -- NONE of the three permission functions ever
authorized this exact, coherent combination, so the correctly-grounded
`EXACT_COMMAND`-shaped guidance was unconditionally discarded and
replaced with `FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT` ("A complete
procedure was generated, but this request was validated as needing only
the next diagnostic step...") -- a message about a procedure the user
never asked to see.

FIX: a new, authoritative, PURELY DETERMINISTIC `request_class`
dimension (`RequestClass`/`derive_request_class`, request_contract.py),
computed EXCLUSIVELY from the validated contract's own `intent`/
`requested_output`/`action_requested`/`subject` -- NEVER from selected
governed evidence, and NEVER trusted from the model (there is no
`record_request_contract` parameter for it at all; it is always server-
computed by `validate_and_persist_request_contract`, mirroring `run_id`
's own established "the model can never set or spoof it" pattern).
`is_exact_command_response_permitted` (the one function with a CONFIRMED
live defect) now checks `request_class==EXACT_COMMAND` instead of
`intent==COMMAND` -- `is_full_procedure_response_permitted`/`is_next_
step_response_permitted` are deliberately UNCHANGED (no confirmed live
defect for either, and an existing, explicit regression affirmatively
requires their own narrower intent-exact behavior).

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    RequestClass,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    derive_request_class,
    validate_and_persist_request_contract,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionStatus,
    derive_execution_decision,
    is_exact_command_response_permitted,
)
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RUN_ID = "livecorr8-run"


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


# =============================================================================
# A -- exact command class (the exact reported defect shape)
# =============================================================================


def test_a_alarm_listing_resolves_exact_command_class_regardless_of_procedure_intent() -> None:
    """"give me the exact command to list current alarms" -- the model
    classified `intent=procedure` (grounded in a governed procedure
    document), `requested_output=exact_command`. Must resolve `request_
    class=EXACT_COMMAND` -- NOT `PROCEDURE_TROUBLESHOOTING` merely because
    the governing evidence is procedural in nature."""
    request_class = derive_request_class(RequestIntent.PROCEDURE, RequestedOutput.EXACT_COMMAND, False, "list current alarms")
    assert request_class == RequestClass.EXACT_COMMAND
    assert request_class != RequestClass.PROCEDURE_TROUBLESHOOTING


def test_a_full_pipeline_alarm_listing_no_longer_rejected_as_incoherent() -> None:
    contract = RequestContract(
        intent=RequestIntent.PROCEDURE,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="list current alarms",
        provided_context=[RequestParameter(name="system_name", value="Ericsson", provenance=ParameterProvenance.USER)],
        missing_context=[],
        request_class=RequestClass.EXACT_COMMAND,
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID, grounded_command_candidate="get alarms")
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.may_emit_command is True
    assert is_exact_command_response_permitted(decision) is True


# =============================================================================
# B -- troubleshooting: evidence content must never influence request_class
# =============================================================================


def test_b_troubleshooting_resolves_procedure_troubleshooting_class() -> None:
    request_class = derive_request_class(
        RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, False, "ESS Service Unavailable"
    )
    assert request_class == RequestClass.PROCEDURE_TROUBLESHOOTING


def test_b_derive_request_class_never_reads_evidence_content() -> None:
    """Structural proof of section 12's own invariant: `derive_request_
    class`'s own signature accepts ONLY `intent`/`requested_output`/
    `action_requested`/`subject` -- there is no parameter through which
    selected governed evidence (or any command string it might contain)
    could possibly influence the result. Calling it twice with identical
    contract-level inputs always yields the identical class, proving the
    classification is a pure function of the CONTRACT alone."""
    import inspect

    params = list(inspect.signature(derive_request_class).parameters)
    assert params == ["intent", "requested_output", "action_requested", "subject"]
    first = derive_request_class(RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, False, "ESS Service Unavailable")
    second = derive_request_class(RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, False, "ESS Service Unavailable")
    assert first == second == RequestClass.PROCEDURE_TROUBLESHOOTING


# =============================================================================
# C -- general conversation
# =============================================================================


@pytest.mark.parametrize("subject", [None])
def test_c_general_conversation_resolves_general_conversation_class(subject: Optional[str]) -> None:
    request_class = derive_request_class(RequestIntent.INFORMATION, RequestedOutput.FACT, False, subject)
    assert request_class == RequestClass.GENERAL_CONVERSATION


def test_c_general_conversation_grants_no_operational_authority() -> None:
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject=None,
        request_class=RequestClass.GENERAL_CONVERSATION,
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is False
    assert decision.may_execute_action is False


# =============================================================================
# D -- operational information
# =============================================================================


def test_d_operational_information_resolves_from_resolved_subject() -> None:
    """"what does a VSWR alarm mean?" -- `intent=information, requested_
    output=fact` (the SAME shape as ordinary conversation), distinguished
    ONLY by a resolved `subject` -- mirrors `is_operationally_shaped_
    request`'s own already-established "resolved subject" signal, never a
    new one."""
    request_class = derive_request_class(RequestIntent.INFORMATION, RequestedOutput.FACT, False, "VSWR alarm meaning")
    assert request_class == RequestClass.OPERATIONAL_INFORMATION


def test_d_operational_information_grants_no_troubleshooting_or_command_authority() -> None:
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="VSWR alarm meaning",
        request_class=RequestClass.OPERATIONAL_INFORMATION,
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is False
    assert decision.may_emit_operational_steps is False


# =============================================================================
# E -- action
# =============================================================================


def test_e_action_resolves_action_class() -> None:
    request_class = derive_request_class(RequestIntent.ACTION, RequestedOutput.ACTION, True, "restart RRU-9")
    assert request_class == RequestClass.ACTION


def test_e_action_remains_gated_by_approval_regardless_of_request_class() -> None:
    contract = RequestContract(
        intent=RequestIntent.ACTION,
        requested_output=RequestedOutput.ACTION,
        subject="restart RRU-9",
        action_requested=True,
        request_class=RequestClass.ACTION,
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.REQUIRES_APPROVAL
    assert decision.may_execute_action is False
    assert decision.approval_required is True


# =============================================================================
# F -- invalid class/output combination is never allowed to persist
# =============================================================================


def test_f_general_conversation_plus_exact_command_output_is_structurally_unreachable() -> None:
    """`derive_request_class` can NEVER produce `GENERAL_CONVERSATION`
    when `requested_output=EXACT_COMMAND` -- the `EXACT_COMMAND` branch is
    checked before the `GENERAL_CONVERSATION` fallback, for every `intent`
    value in the closed vocabulary."""
    for intent in (
        RequestIntent.INFORMATION,
        RequestIntent.PROCEDURE,
        RequestIntent.COMMAND,
        RequestIntent.TROUBLESHOOTING,
        RequestIntent.KNOWLEDGE_INVENTORY,
    ):
        assert derive_request_class(intent, RequestedOutput.EXACT_COMMAND, False, None) == RequestClass.EXACT_COMMAND


class _FakeToolContext:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.user_content = None


class _FakeTool:
    name = "record_request_contract"


def test_f_model_declared_inconsistency_is_overridden_end_to_end() -> None:
    """Drives the REAL `validate_and_persist_request_contract` -- not a
    hand-built decision -- with a raw `tool_response` shaped as though a
    poorly-behaved model had somehow declared a `GENERAL_CONVERSATION`-
    style contract (`intent=information, requested_output=fact`) while
    ALSO asking for `exact_command` output (structurally impossible via
    `record_request_contract`'s own real parameter validation, but this
    proves the DOWNSTREAM persistence layer independently corrects the
    class regardless of how the raw dict got here -- defense in depth)."""
    tool_response = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="list current alarms",
        run_id=None,
    ).model_dump(mode="json")

    state: dict[str, Any] = {}
    import backend.agents.team_manager.request_contract as request_contract_module

    original_current_run_id = request_contract_module.current_run_id
    request_contract_module.current_run_id = lambda: "turn-f"
    try:
        validate_and_persist_request_contract(_FakeTool(), {}, _FakeToolContext(state), tool_response)
    finally:
        request_contract_module.current_run_id = original_current_run_id

    persisted = RequestContract.model_validate(state[VALIDATED_REQUEST_CONTRACT_STATE_KEY])
    assert persisted.request_class == RequestClass.EXACT_COMMAND
    assert persisted.request_class != RequestClass.GENERAL_CONVERSATION


# =============================================================================
# G -- alarm-list command pipeline (ChatService + FakeRunner)
# =============================================================================


@pytest.mark.asyncio
async def test_g_full_pipeline_alarm_listing_command_reaches_user() -> None:
    """Reproduces the closest practical pipeline equivalent of "what is
    the cmd i need to run to check and list alarms present?": the turn no
    longer fails because it was classified as a procedure while requesting
    an exact command."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.PROCEDURE,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="list current alarms",
            provided_context=[RequestParameter(name="system_name", value="Ericsson", provenance=ParameterProvenance.USER)],
            missing_context=[],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP, next_action="Run the command below.", command="get alarms"),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(
        session_id, "what is the cmd i need to run to check and list alarms present?", "api-user"
    ):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert "A complete procedure was generated" not in completed.data["content"]
    assert "get alarms" in completed.data["content"]


@pytest.mark.asyncio
async def test_g_full_pipeline_command_still_depends_on_grounding_and_required_parameters() -> None:
    """Companion negative control through the SAME pipeline: an
    EXACT_COMMAND-classified request whose grounded candidate DOES
    reference a unit-class identifier still requires target confirmation
    -- `request_class=EXACT_COMMAND` alone never bypasses LIVE-CORR-7's
    own operation-specific parameter requirements."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    rru_command = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            missing_context=[],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id, TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP, next_action="Run the command below.", command=rru_command)
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "give me the command to restart an RRU", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert rru_command not in completed.data["content"]
