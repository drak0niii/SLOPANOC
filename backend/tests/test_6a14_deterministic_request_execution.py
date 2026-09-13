"""Phase 6A.14 -- Deterministic Request Execution.

Proves `backend/agents/team_manager/request_execution_policy.py`'s
`derive_execution_decision`/`enforce_execution_decision_on_guidance`
close the exact live gap DEF-0027's own safety controls could not: a
command can be perfectly GROUNDED (DEF-0024/0027) while still being
parameterized with a live-target identifier the user never actually
supplied. This module makes runtime OUTPUT deterministically obey the
validated, CURRENT-TURN `RequestContract` (Phase 6A.13) on top of --
never instead of -- the existing grounding layer.

Covers, per the milestone's own required test list (section 17):
   1/2/3.  INFORMATION/PROCEDURE never imply action execution
   4/5/6.  COMMAND blocked by ambiguity / unresolved subject / missing
           context
   7/8/9/10. THE RRU case, end to end (both pure-decision and full
           chat_service integration)
   11/12/13. the AAS case
   14/15.  SupportUnit is never fabricated into RRU/AAS
   16.     TROUBLESHOOTING may still give a safe non-command step
   17.     KNOWLEDGE_INVENTORY never silently becomes semantic search
   18/19.  ACTION always requires approval; can never weaken it
   20.     malformed/absent contract fails closed
   21/22.  stale vs. current-turn contract freshness
   23.     explicit subject change still works
   28.     no cross-session/cross-run leakage (pure-function isolation)
Items 24-27 (DEF-0024/0026/0027 and Teams/approval security regressions)
are satisfied by re-running their own existing, unmodified test files --
not duplicated here; see this milestone's own closure report.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode, TroubleshootingStep
from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
)
from backend.agents.team_manager.request_execution_policy import (
    KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT,
    RequestExecutionStatus,
    command_suppression_fallback_text,
    derive_execution_decision,
    enforce_execution_decision_on_guidance,
)
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_AAS_COMMAND = "accn FieldReplaceableUnit=AAS-1 restartunit 1 1 1"

_RUN_ID = "run-6a14-fixed"


def _contract(**overrides: Any) -> RequestContract:
    defaults: dict[str, Any] = dict(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        continuation=True,
        run_id=_RUN_ID,
    )
    defaults.update(overrides)
    return RequestContract(**defaults)


def _guidance(command: Optional[str], **overrides: Any) -> TroubleshootingGuidance:
    defaults: dict[str, Any] = dict(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="x",
        next_action="y",
        command=command,
        evidence_requested="z",
    )
    defaults.update(overrides)
    return TroubleshootingGuidance(**defaults)


# --- 1/2/3: INFORMATION/PROCEDURE never imply action execution -------------


def test_information_intent_allows_factual_output() -> None:
    decision = derive_execution_decision(_contract(intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT), _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW


def test_information_intent_never_authorizes_action_execution() -> None:
    decision = derive_execution_decision(_contract(intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT), _RUN_ID)
    assert decision.may_execute_action is False


def test_procedure_intent_never_implies_action_approval() -> None:
    decision = derive_execution_decision(
        _contract(intent=RequestIntent.PROCEDURE, requested_output=RequestedOutput.PROCEDURE_STEPS, subject="VSWR Over Threshold"), _RUN_ID
    )
    assert decision.may_execute_action is False


def test_may_execute_action_is_always_false_from_this_policy_in_every_branch() -> None:
    """Actual write execution remains the existing, unchanged approval
    boundary's own job -- this policy never authorizes it, in ANY branch."""
    for intent in (
        RequestIntent.INFORMATION,
        RequestIntent.PROCEDURE,
        RequestIntent.COMMAND,
        RequestIntent.TROUBLESHOOTING,
        RequestIntent.KNOWLEDGE_INVENTORY,
        RequestIntent.ACTION,
    ):
        decision = derive_execution_decision(_contract(intent=intent, requested_output=RequestedOutput.FACT, action_requested=True), _RUN_ID)
        assert decision.may_execute_action is False, intent


# --- 4/5/6: COMMAND blocked by ambiguity / unresolved subject / missing ----


def test_command_with_ambiguity_true_emits_no_command() -> None:
    decision = derive_execution_decision(
        _contract(intent=RequestIntent.COMMAND, requested_output=RequestedOutput.EXACT_COMMAND, ambiguity=True), _RUN_ID
    )
    assert decision.status == RequestExecutionStatus.AMBIGUOUS
    assert decision.may_emit_command is False


def test_command_with_unresolved_subject_emits_no_command() -> None:
    decision = derive_execution_decision(
        _contract(intent=RequestIntent.COMMAND, requested_output=RequestedOutput.EXACT_COMMAND, subject=None), _RUN_ID
    )
    assert decision.status == RequestExecutionStatus.AMBIGUOUS
    assert decision.may_emit_command is False


def test_command_with_missing_target_context_emits_no_command() -> None:
    decision = derive_execution_decision(
        _contract(intent=RequestIntent.COMMAND, requested_output=RequestedOutput.EXACT_COMMAND, missing_context=["unit_id"]), _RUN_ID
    )
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False


# --- 7/8/9/10: THE RRU case --------------------------------------------


def test_rru_unit_type_without_unit_id_emits_no_rru9_command() -> None:
    contract = _contract(
        provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
        missing_context=["unit_id"],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    corrected, stripped = enforce_execution_decision_on_guidance(_guidance(_RRU_COMMAND), decision)
    assert stripped is True
    assert corrected.command is None
    assert "RRU-9" not in (corrected.command or "")


def test_rru_unit_type_without_unit_id_asks_for_unit_id() -> None:
    contract = _contract(
        provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
        missing_context=["unit_id"],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    fallback = command_suppression_fallback_text(decision)
    assert "unit_id" in fallback


def test_rru9_supplied_by_user_permits_grounded_rru9_command() -> None:
    contract = _contract(
        provided_context=[
            RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
            RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),
        ],
        missing_context=[],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    corrected, stripped = enforce_execution_decision_on_guidance(_guidance(_RRU_COMMAND), decision)
    assert stripped is False
    assert corrected.command == _RRU_COMMAND


def test_knowledge_only_rru9_cannot_become_live_target() -> None:
    """Even if 6A.13's own provenance filter somehow failed and the
    contract's own `missing_context` still (correctly) lists `unit_id`,
    6A.14's OWN independent gate blocks the command as a second layer --
    defense in depth, not a single point of failure."""
    contract = _contract(
        provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
        missing_context=["unit_id"],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    corrected, stripped = enforce_execution_decision_on_guidance(_guidance(_RRU_COMMAND), decision)
    assert stripped is True
    assert corrected.command is None


# --- 11/12/13: THE AAS case -------------------------------------------


def test_aas_unit_type_without_unit_id_emits_no_aas1_command() -> None:
    contract = _contract(
        provided_context=[RequestParameter(name="unit_type", value="AAS", provenance=ParameterProvenance.USER)],
        missing_context=["unit_id"],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    corrected, stripped = enforce_execution_decision_on_guidance(_guidance(_AAS_COMMAND), decision)
    assert stripped is True
    assert corrected.command is None


def test_aas_unit_type_without_unit_id_asks_for_unit_id() -> None:
    contract = _contract(
        provided_context=[RequestParameter(name="unit_type", value="AAS", provenance=ParameterProvenance.USER)],
        missing_context=["unit_id"],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert "unit_id" in command_suppression_fallback_text(decision)


def test_aas1_user_supplied_permits_grounded_aas1_command() -> None:
    contract = _contract(
        provided_context=[
            RequestParameter(name="unit_type", value="AAS", provenance=ParameterProvenance.USER),
            RequestParameter(name="unit_id", value="AAS-1", provenance=ParameterProvenance.USER),
        ],
        missing_context=[],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    corrected, stripped = enforce_execution_decision_on_guidance(_guidance(_AAS_COMMAND), decision)
    assert stripped is False
    assert corrected.command == _AAS_COMMAND


# --- 14/15: SupportUnit is never fabricated into RRU/AAS --------------


def test_supportunit_never_becomes_fabricated_rru_or_aas_target() -> None:
    contract = _contract(
        provided_context=[RequestParameter(name="unit_type", value="SupportUnit", provenance=ParameterProvenance.USER)],
        missing_context=[],  # no restart -> no identifier needed at all
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW  # nothing to withhold -- no command was ever proposed
    corrected, stripped = enforce_execution_decision_on_guidance(_guidance(None, next_action="No restart is permitted for a SupportUnit."), decision)
    assert stripped is False
    assert corrected.command is None


def test_supportunit_no_restart_remains_safe_even_if_a_command_is_wrongly_proposed() -> None:
    """Defense in depth: even if something upstream wrongly proposed an
    RRU command for a SupportUnit turn, this policy's own ALLOW state
    does not fabricate anything -- it is `evidence.py`'s own, separate,
    unchanged DEF-0024/0027 grounding that would reject an RRU command
    never grounded in a SupportUnit-scoped selection; this test proves
    only that 6A.14 itself introduces no new leak path."""
    contract = _contract(
        provided_context=[RequestParameter(name="unit_type", value="SupportUnit", provenance=ParameterProvenance.USER)], missing_context=[]
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.may_emit_command is True  # 6A.14 defers to evidence.py's own grounding for the SPECIFIC string


# --- 16: TROUBLESHOOTING may still give a safe non-command step -------


def test_troubleshooting_with_missing_context_still_allows_operational_steps_text() -> None:
    contract = _contract(missing_context=["unit_id"])
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False
    assert decision.may_emit_operational_steps is True  # a safe diagnostic/clarifying step remains allowed


# --- 17: KNOWLEDGE_INVENTORY never silently becomes semantic search ---


def test_knowledge_inventory_is_unsupported_capability_not_allow() -> None:
    decision = derive_execution_decision(
        _contract(intent=RequestIntent.KNOWLEDGE_INVENTORY, requested_output=RequestedOutput.KNOWLEDGE_LIST, subject=None), _RUN_ID
    )
    assert decision.status == RequestExecutionStatus.UNSUPPORTED_CAPABILITY
    assert decision.may_emit_command is False


# --- 18/19: ACTION always requires approval, can never weaken it -------


def test_action_requires_approval() -> None:
    decision = derive_execution_decision(
        _contract(intent=RequestIntent.ACTION, requested_output=RequestedOutput.ACTION, action_requested=True, approval_required=True), _RUN_ID
    )
    assert decision.status == RequestExecutionStatus.REQUIRES_APPROVAL
    assert decision.approval_required is True


def test_action_cannot_weaken_approval_even_if_contract_under_claims_it() -> None:
    """Defensive: even a (structurally impossible after 6A.13's own
    forcing, but defended against anyway) `approval_required=False`
    ACTION contract still yields `approval_required=True` here."""
    contract = _contract(intent=RequestIntent.ACTION, requested_output=RequestedOutput.ACTION, action_requested=True, approval_required=False)
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.approval_required is True
    assert decision.may_execute_action is False


# --- 20: malformed/absent contract fails closed -------------------------


def test_absent_contract_fails_closed() -> None:
    decision = derive_execution_decision(None, _RUN_ID)
    assert decision.status == RequestExecutionStatus.INVALID_CONTRACT
    assert decision.may_emit_command is False
    assert decision.may_execute_action is False
    assert decision.may_emit_operational_steps is False


def test_absent_contract_never_enables_more_capability_than_an_explicit_gate() -> None:
    absent_decision = derive_execution_decision(None, _RUN_ID)
    ambiguous_decision = derive_execution_decision(_contract(ambiguity=True), _RUN_ID)
    assert absent_decision.may_emit_command == ambiguous_decision.may_emit_command == False
    assert absent_decision.may_execute_action == ambiguous_decision.may_execute_action == False


# --- 21/22: stale vs. current-turn freshness -----------------------------


def test_stale_previous_turn_contract_cannot_authorize_current_turn_command() -> None:
    stale_contract = _contract(
        provided_context=[
            RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
            RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),
        ],
        missing_context=[],
        run_id="run-OLD-turn",
    )
    decision = derive_execution_decision(stale_contract, "run-CURRENT-turn")
    assert decision.status == RequestExecutionStatus.INVALID_CONTRACT
    assert decision.may_emit_command is False


def test_current_turn_contract_wins_over_a_prior_turn() -> None:
    fresh_contract = _contract(missing_context=[], run_id="run-CURRENT-turn")
    decision = derive_execution_decision(fresh_contract, "run-CURRENT-turn")
    assert decision.status == RequestExecutionStatus.ALLOW


# --- 23: explicit subject change still works -----------------------------


def test_explicit_subject_change_still_permits_a_fresh_valid_contract() -> None:
    contract = _contract(subject="Resource Activation Timeout", continuation=False, missing_context=[])
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.subject == "Resource Activation Timeout"


# --- 28: no cross-session/cross-run leakage (pure-function isolation) ---


def test_two_independent_runs_never_influence_each_other() -> None:
    contract_a = _contract(run_id="run-A", missing_context=["unit_id"])
    contract_b = _contract(run_id="run-B", missing_context=[])
    decision_a = derive_execution_decision(contract_a, "run-A")
    decision_b = derive_execution_decision(contract_b, "run-B")
    assert decision_a.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision_b.status == RequestExecutionStatus.ALLOW
    # cross-checking with the WRONG run_id must fail closed for each
    assert derive_execution_decision(contract_a, "run-B").status == RequestExecutionStatus.INVALID_CONTRACT
    assert derive_execution_decision(contract_b, "run-A").status == RequestExecutionStatus.INVALID_CONTRACT


# --- FULL_PROCEDURE mode coverage ----------------------------------------


def test_full_procedure_mode_all_commands_suppressed_when_missing_context() -> None:
    """6A.14 FINAL corrective pass: the ENTIRE guidance is suppressed --
    `full_procedure_steps` becomes empty, not merely each step's own
    `command` field -- since a step's own `action` text could otherwise
    still carry an unverified command in free-form prose (section 5)."""
    contract = _contract(missing_context=["unit_id"])
    decision = derive_execution_decision(contract, _RUN_ID)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[
            TroubleshootingStep(action="Confirm unit.", command=None),
            TroubleshootingStep(action="Restart it.", command=_RRU_COMMAND),
        ],
    )
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is True
    assert corrected.full_procedure_steps == []


def test_guidance_none_is_a_complete_noop() -> None:
    decision = derive_execution_decision(_contract(missing_context=["unit_id"]), _RUN_ID)
    corrected, stripped = enforce_execution_decision_on_guidance(None, decision)
    assert corrected is None
    assert stripped is False


# =============================================================================
# Full chat_service.py integration (proves the actual wiring, not only the
# pure functions) -- mirrors test_p5_1j_governed_completion_gate.py's own
# established `ApiSessionService` + `FakeRunner(side_effect=...)` pattern.
# =============================================================================


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


@pytest.mark.asyncio
async def test_integration_rru_missing_unit_id_suppresses_command_end_to_end() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _contract(
            provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
            missing_context=["unit_id"],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            _guidance(_RRU_COMMAND, next_action="Confirm the RRU identifier.", evidence_requested="What is the RRU ID?"),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused -- the deterministic guidance override always wins", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "it's an RRU", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert "RRU-9" not in completed.data["content"]
    assert "restartunit" not in completed.data["content"]


@pytest.mark.asyncio
async def test_integration_rru9_user_supplied_permits_command_end_to_end() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _contract(
            provided_context=[
                RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
                RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),
            ],
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(run_id, _guidance(_RRU_COMMAND))

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "RRU-9", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert _RRU_COMMAND in completed.data["content"]


@pytest.mark.asyncio
async def test_integration_stale_contract_from_a_different_run_cannot_authorize_command() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        # Deliberately stamp a DIFFERENT (stale) run_id -- simulates a
        # contract left over from an earlier turn that this turn's own
        # team_manager model failed to refresh.
        contract = _contract(
            provided_context=[
                RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
                RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),
            ],
            missing_context=[],
            run_id="a-completely-different-stale-run-id",
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(run_id, _guidance(_RRU_COMMAND))

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "RRU-9", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert "RRU-9" not in completed.data["content"]


@pytest.mark.asyncio
async def test_integration_knowledge_inventory_never_becomes_semantic_search_output() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.KNOWLEDGE_INVENTORY, requested_output=RequestedOutput.KNOWLEDGE_LIST, run_id=run_id
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="Here are some documents I found via ordinary semantic search: Document1, Rogers MOP...", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "what MOPs do you have?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert completed.data["content"] == KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT
    assert "Document1" not in completed.data["content"]


@pytest.mark.asyncio
async def test_integration_ordinary_turn_with_no_contract_and_no_command_is_unaffected() -> None:
    """A missing contract must never block ORDINARY, non-command
    presentation output -- only command/action-shaped output is gated."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="VSWR stands for Voltage Standing Wave Ratio.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "what is VSWR?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert completed.data["content"] == "VSWR stands for Voltage Standing Wave Ratio."
