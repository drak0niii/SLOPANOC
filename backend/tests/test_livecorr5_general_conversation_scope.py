"""LIVE-CORR-5 -- General Conversation Must Not Trigger Operational Command
Gating.

CONFIRMED ROOT CAUSE: `derive_execution_decision`'s FIRST branch returned
`AMBIGUOUS` status whenever `RequestContract.ambiguity` was `True`, with
no check of whether the request was actually operationally shaped at all.
`prompts.py`'s own REQUEST CONTRACT guidance instructed the model to set
`ambiguity=true` whenever `subject` was unset ("leaving it unset with
`ambiguity=true` when you genuinely cannot [tell the subject]"), with no
distinction between "subject genuinely unclear for an operational
request" and "subject does not apply because this is not an operational
request at all" -- so a harmless request like "who are you and what can
you do?" (intent=information, requested_output=fact, subject=None) could
be given `ambiguity=True` by the model, producing `AMBIGUOUS` status,
which `requires_unstructured_response_backstop` then blocks
UNCONDITIONALLY (its own `_UNSTRUCTURED_RESPONSE_BLOCKING_STATUSES` never
checked `is_operationally_shaped_request` for AMBIGUOUS/NEEDS_INFORMATION,
unlike the ALLOW branch), replacing the assistant's natural response with
`command_suppression_fallback_text`'s `_NO_SUBJECT_FALLBACK_TEXT` -- "I
need to know which specific alarm or governed procedure you mean...".

NOTE: the SEPARATE, second AMBIGUOUS-producing branch further down
(`is_operationally_shaped_request(...) and not contract.subject`) was
ALREADY correctly scoped before this pass -- `test_livecorr2_request_
context_policy_correction.py`'s own `test_missing_subject_operational_
requests_still_clarify`/`test_intent_label_cannot_bypass_operational_
safety` construct contracts WITHOUT setting `ambiguity=True` at all and
still correctly land on `AMBIGUOUS`, via that second branch. Per section
10's own explicit instruction not to repeat DEF-0037's "test only the
perfect, hand-constructed contract" weakness, this file specifically
targets the shape those tests do NOT cover: `ambiguity=True` (the actual
buggy/model-driven signal) on a NON-operational contract.

Fix: `RequestScope`/`request_scope` (request_contract.py) -- a purely
deterministic derivation from already-validated `intent`/`requested_
output`/`action_requested`, reusing `is_operationally_shaped_request`
(never a new taxonomy, never a model-set field) -- gates the `contract.
ambiguity` branch in `derive_execution_decision`: it only produces
`AMBIGUOUS` status for `RequestScope.OPERATIONAL` contracts. A `GENERAL`
contract's `ambiguity` flag (however it got set) is no longer treated as
requiring operational clarification. `prompts.py` is also corrected so
the model is no longer instructed to conflate the two in the first place
-- but the deterministic gate does not rely on the model actually
following it (CLAUDE.md section 5: "Sensitive decisions must be
deterministic application behavior, not prompt-only rules").

Also corrects `command_suppression_fallback_text`'s `INVALID_CONTRACT`
branch (section 6): a missing/stale contract by itself is not evidence
the user asked about an alarm/procedure -- it now returns a neutral
`_INVALID_CONTRACT_FALLBACK_TEXT` instead of `_NO_SUBJECT_FALLBACK_TEXT`.
Wording-only; the underlying safety decision (command withheld) is
unaffected.

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    RequestContract,
    RequestIntent,
    RequestScope,
    RequestedOutput,
    request_scope,
    validate_and_persist_request_contract,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionDecision,
    RequestExecutionStatus,
    command_suppression_fallback_text,
    derive_execution_decision,
    requires_unstructured_response_backstop,
)
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RUN_ID = "livecorr5-run"


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


# =============================================================================
# `request_scope` -- pure derivation
# =============================================================================


@pytest.mark.parametrize(
    "intent,requested_output",
    [
        (RequestIntent.INFORMATION, RequestedOutput.FACT),
    ],
)
def test_information_fact_is_general_scope(intent: str, requested_output: str) -> None:
    assert request_scope(intent, requested_output) == RequestScope.GENERAL


@pytest.mark.parametrize(
    "intent,requested_output",
    [
        (RequestIntent.PROCEDURE, RequestedOutput.PROCEDURE_STEPS),
        (RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND),
        (RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP),
        (RequestIntent.INFORMATION, RequestedOutput.EXACT_COMMAND),  # mislabeled intent, command-shaped output
        (RequestIntent.COMMAND, RequestedOutput.FACT),  # operational intent, factual-shaped output
    ],
)
def test_operationally_shaped_combinations_are_operational_scope(intent: str, requested_output: str) -> None:
    assert request_scope(intent, requested_output) == RequestScope.OPERATIONAL


def test_action_intent_is_operational_scope_regardless_of_output() -> None:
    """ACTION's own separate, unconditional REQUIRES_APPROVAL branch must
    keep resolving exactly as before -- never demoted to GENERAL."""
    assert request_scope(RequestIntent.ACTION, RequestedOutput.FACT) == RequestScope.OPERATIONAL


def test_knowledge_inventory_intent_is_operational_scope_regardless_of_output() -> None:
    assert request_scope(RequestIntent.KNOWLEDGE_INVENTORY, RequestedOutput.FACT) == RequestScope.OPERATIONAL


def test_action_requested_flag_alone_is_operational_scope() -> None:
    assert request_scope(RequestIntent.INFORMATION, RequestedOutput.FACT, action_requested=True) == RequestScope.OPERATIONAL


# =============================================================================
# `derive_execution_decision` -- the actual reported defect shape
# =============================================================================


@pytest.mark.parametrize(
    "text,intent,requested_output",
    [
        ("who are you?", RequestIntent.INFORMATION, RequestedOutput.FACT),
        ("what can you do?", RequestIntent.INFORMATION, RequestedOutput.FACT),
        ("who are you and what can you do?", RequestIntent.INFORMATION, RequestedOutput.FACT),
        ("hello", RequestIntent.INFORMATION, RequestedOutput.FACT),
        ("help", RequestIntent.INFORMATION, RequestedOutput.FACT),
        ("explain how SLOPANOC works", RequestIntent.INFORMATION, RequestedOutput.FACT),
        ("what agents do you have?", RequestIntent.INFORMATION, RequestedOutput.FACT),
    ],
)
def test_general_conversation_with_model_set_ambiguity_true_still_allows(text: str, intent: str, requested_output: str) -> None:
    """THE reported live defect shape: the model (per the pre-fix prompt's
    own literal instruction) sets `ambiguity=True` purely because `subject`
    is unset, even though the request has no operational subject to begin
    with. Must still ALLOW a normal response -- no subject requirement, no
    `_NO_SUBJECT_FALLBACK_TEXT`.
    """
    contract = RequestContract(
        intent=intent,
        requested_output=requested_output,
        subject=None,
        missing_context=[],
        ambiguity=True,  # the buggy/model-driven signal -- deliberately NOT the "perfect" shape
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is False
    fallback = command_suppression_fallback_text(decision) if decision.status != RequestExecutionStatus.ALLOW else None
    assert fallback is None or "alarm or governed procedure" not in fallback


def test_general_conversation_does_not_gain_command_authority() -> None:
    """Success criterion 3: GENERAL scope never gains operational command
    permission, ambiguity flag notwithstanding."""
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject=None,
        ambiguity=True,
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.may_emit_command is False
    assert decision.may_execute_action is False


def test_general_conversation_backstop_does_not_fire() -> None:
    """`requires_unstructured_response_backstop` must not replace the
    natural response for the exact reported defect shape."""
    contract = RequestContract(
        intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, subject=None, ambiguity=True, run_id=_RUN_ID
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is False


# =============================================================================
# Operational missing-subject requests remain governed (non-regression)
# =============================================================================


@pytest.mark.parametrize(
    "intent,requested_output",
    [
        (RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND),  # "give me the command"
        (RequestIntent.PROCEDURE, RequestedOutput.PROCEDURE_STEPS),  # "show me the procedure"
    ],
)
def test_operational_missing_subject_requests_still_blocked(intent: str, requested_output: str) -> None:
    contract = RequestContract(intent=intent, requested_output=requested_output, subject=None, run_id=_RUN_ID)
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.AMBIGUOUS
    assert decision.may_emit_command is False
    assert command_suppression_fallback_text(decision) == (
        "I need to know which specific alarm or governed procedure you mean before I can give you a command. Please name it explicitly."
    )


def test_operational_ambiguity_true_still_blocks() -> None:
    """The pre-existing operational-ambiguity path (a genuinely
    operational request the model itself flags as ambiguous) is completely
    unaffected by this pass."""
    contract = RequestContract(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject=None,
        ambiguity=True,
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.AMBIGUOUS
    assert decision.may_emit_command is False


def test_action_ambiguity_true_still_requires_approval_not_allow() -> None:
    """Non-regression: ACTION's own AMBIGUOUS-before-REQUIRES_APPROVAL
    ordering is unaffected by the new scope gate."""
    contract = RequestContract(
        intent=RequestIntent.ACTION, requested_output=RequestedOutput.ACTION, action_requested=True, ambiguity=True, run_id=_RUN_ID
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.AMBIGUOUS
    assert decision.may_execute_action is False


# =============================================================================
# INVALID_CONTRACT wording correction (section 6)
# =============================================================================


def test_invalid_contract_no_longer_presumes_an_alarm_or_procedure() -> None:
    decision = RequestExecutionDecision(status=RequestExecutionStatus.INVALID_CONTRACT, missing_context=[])
    text = command_suppression_fallback_text(decision)
    assert "alarm or governed procedure" not in text
    assert text  # still a real, non-empty clarification


# =============================================================================
# Context isolation -- turn 2 (general) must not inherit turn 1's
# (operational) subject requirement. Drives the REAL `validate_and_
# persist_request_contract` persistence layer across two simulated turns,
# not a hand-built RequestContract, per section 10's explicit instruction.
# =============================================================================


class _FakeToolContext:
    def __init__(self, state: dict[str, Any], user_content_text: Optional[str] = None) -> None:
        self.state = state
        self.user_content = None
        if user_content_text is not None:
            class _Part:
                def __init__(self, text: str) -> None:
                    self.text = text

            class _Content:
                def __init__(self, parts: list[Any]) -> None:
                    self.parts = parts

            self.user_content = _Content([_Part(user_content_text)])


def test_turn2_general_conversation_does_not_inherit_turn1_operational_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.agents.team_manager import request_contract as request_contract_module

    session_state: dict[str, Any] = {}

    class _Tool:
        name = "record_request_contract"

    # Turn 1: a genuine operational troubleshooting request.
    monkeypatch.setattr(request_contract_module, "current_run_id", lambda: "turn-1")
    turn1_tool_response = RequestContract(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        continuation=False,
        run_id=None,
    ).model_dump(mode="json")
    validate_and_persist_request_contract(_Tool(), {}, _FakeToolContext(session_state), turn1_tool_response)
    persisted_turn1 = request_contract_module.RequestContract.model_validate(session_state[VALIDATED_REQUEST_CONTRACT_STATE_KEY])
    assert persisted_turn1.subject == "HW Partial Fault"

    # Turn 2: "who are you and what can you do?" -- an entirely new,
    # non-operational topic, NOT a continuation, carrying the same
    # buggy-but-possible ambiguity=True signal.
    monkeypatch.setattr(request_contract_module, "current_run_id", lambda: "turn-2")
    turn2_tool_response = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject=None,
        continuation=False,
        ambiguity=True,
        run_id=None,
    ).model_dump(mode="json")
    validate_and_persist_request_contract(_Tool(), {}, _FakeToolContext(session_state), turn2_tool_response)
    persisted_turn2 = request_contract_module.RequestContract.model_validate(session_state[VALIDATED_REQUEST_CONTRACT_STATE_KEY])

    assert persisted_turn2.subject is None  # never inherited turn 1's "HW Partial Fault"
    turn2_decision = derive_execution_decision(persisted_turn2, "turn-2")
    assert turn2_decision.status == RequestExecutionStatus.ALLOW
    assert turn2_decision.may_emit_command is False


# =============================================================================
# Full pipeline -- ChatService + FakeRunner (section 16-style rendering
# boundary proof)
# =============================================================================


@pytest.mark.asyncio
async def test_full_pipeline_general_conversation_reaches_user_unreplaced() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    natural_reply = "I'm SLOPANOC, your operational assistant for Teams-based incident collaboration."

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject=None,
            ambiguity=True,  # the reported buggy/model-driven signal
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text=natural_reply, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "who are you and what can you do?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert completed.data["content"] == natural_reply


@pytest.mark.asyncio
async def test_full_pipeline_missing_subject_command_request_still_blocked() -> None:
    """Companion positive control through the SAME real pipeline: an
    operational command request with no resolved subject remains blocked."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    unsafe_text = "Sure, run: accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject=None,
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text=unsafe_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "give me the command", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert completed.data["content"] != unsafe_text
    assert "RRU-9" not in completed.data["content"]
