"""Phase 6A.14 FINAL live-acceptance corrective pass.

Closes the two root causes a dedicated audit found in the first 6A.14
live acceptance attempt:

  ROOT CAUSE A (free-form output bypass): `incident_manager` answered
  "how do i handle HW Partial Fault?" via its own free-form `summary`
  field, never populating `TroubleshootingGuidance` at all -- the
  response text had no "Run:\n\n" marker and no numbered steps, the
  unmistakable signature of `render_troubleshooting_guidance`'s own
  deterministic output NEVER having run. DEF-0024/0027's grounding and
  the first 6A.14 pass's `enforce_execution_decision_on_guidance` both
  examine `TroubleshootingGuidance` exclusively, so neither ever saw
  this response at all. Two real, grounded commands (`RRU-9`, `AAS-1`)
  reached the user completely unvalidated.

  ROOT CAUSE B (intent-scope bypass): even when `TroubleshootingGuidance`
  IS populated, the first 6A.14 pass's missing-context gate only applied
  to `COMMAND`/`TROUBLESHOOTING` intents -- a request classified
  `PROCEDURE` or `INFORMATION` (both plausible classifications for "how
  do i handle X") bypassed the gate entirely regardless of `missing_
  context`.

Fixes, all additive to the existing 6A.13/6A.14 architecture (nothing
reverted, no regex/generic command parser, no specialist-routing
change):

  1. `_TARGET_SPECIFIC_INTENTS` widened to include PROCEDURE/INFORMATION
     (closes Root Cause B).
  2. `enforce_execution_decision_on_guidance` now suppresses the ENTIRE
     `TroubleshootingGuidance` -- `interpretation`/`next_action`/
     `evidence_requested`/every `TroubleshootingStep.action` -- not only
     `command`/`step.command` -- since a command could otherwise still be
     embedded in free-form narrative fields while `command` itself was
     left correctly unset.
  3. `requires_unstructured_response_backstop` + its `chat_service.py`
     wiring -- a NEW, purely decision-driven (never text-parsing)
     completion-boundary backstop: when this turn's own validated,
     CURRENT contract positively shows unresolved target/condition
     context (`NEEDS_INFORMATION`/`AMBIGUOUS`) and NO structured
     `TroubleshootingGuidance` exists to enforce against, `final_text` is
     unconditionally replaced with a deterministic clarification --
     closing Root Cause A directly.
  4. Prompt strengthening (`incident_manager`'s own "mandatory for any
     command-bearing answer" paragraph; team_manager's own `missing_
     context` guidance) -- advisory only; the deterministic mechanisms
     above are the real, authoritative safety boundary regardless of
     whether the model complies.

Covers the milestone's own required test list (section 19).
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
    RequestExecutionStatus,
    command_suppression_fallback_text,
    derive_execution_decision,
    enforce_execution_decision_on_guidance,
    load_current_turn_contract,
    requires_unstructured_response_backstop,
)
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_AAS_COMMAND = "accn FieldReplaceableUnit=AAS-1 restartunit 1 1 1"
_RUN_ID = "run-6a14-final"


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


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


# =============================================================================
# 4/5/6: widened intent scope -- PROCEDURE/INFORMATION are now gated too
# =============================================================================


@pytest.mark.parametrize("intent", [RequestIntent.PROCEDURE, RequestIntent.INFORMATION, RequestIntent.TROUBLESHOOTING, RequestIntent.COMMAND])
def test_missing_context_blocks_commands_regardless_of_intent_classification(intent: str) -> None:
    """Section 3's own governing principle: intent must never be usable
    as a bypass around command/target safety."""
    contract = _contract(intent=intent, missing_context=["unit_type", "unit_id"])
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False


@pytest.mark.parametrize("intent", [RequestIntent.PROCEDURE, RequestIntent.INFORMATION])
def test_procedure_and_information_intents_previously_bypassed_the_gate(intent: str) -> None:
    """Direct regression proof for Root Cause B: this exact scenario
    (missing_context non-empty, intent=PROCEDURE/INFORMATION) previously
    fell through to ALLOW -- now correctly gated."""
    contract = _contract(intent=intent, requested_output=RequestedOutput.PROCEDURE_STEPS, missing_context=["unit_type", "unit_id"])
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status != RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is False


# =============================================================================
# 7/8/9/10: whole-guidance suppression -- narrative fields cannot bypass
# =============================================================================


def test_free_form_summary_cannot_bypass_via_missing_guidance() -> None:
    """§7: no `TroubleshootingGuidance` exists at all -- the NEW backstop
    fires purely from the decision, never from scanning text."""
    contract = _contract(missing_context=["unit_type", "unit_id"])
    decision = derive_execution_decision(contract, _RUN_ID)
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is True


def test_interpretation_cannot_bypass_may_emit_command_false() -> None:
    """§8."""
    contract = _contract(missing_context=["unit_id"])
    decision = derive_execution_decision(contract, _RUN_ID)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation=f"Use the command {_RRU_COMMAND} to restart the RRU.",
        next_action="Restart it.",
        command=None,  # model correctly left this unset, but smuggled the command into interpretation
        evidence_requested="Confirm.",
    )
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is True
    assert corrected.interpretation is None
    assert _RRU_COMMAND not in (corrected.interpretation or "")


def test_next_action_cannot_bypass_may_emit_command_false() -> None:
    """§9."""
    contract = _contract(missing_context=["unit_id"])
    decision = derive_execution_decision(contract, _RUN_ID)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="x",
        next_action=f"Restart RRU-9 using {_RRU_COMMAND}.",
        command=None,
        evidence_requested="z",
    )
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is True
    assert corrected.next_action is None


def test_step_action_cannot_bypass_may_emit_command_false() -> None:
    """§10 -- LIVE-CORR-3A ownership update: a step with NO `command` at
    all (free text possibly hiding one) is, in the REAL pipeline, ALREADY
    caught EARLIER by `evidence.py`'s `enforce_structural_operational_
    integrity` (an unclassified step defaults to `STATE_CHANGE_
    RECOMMENDATION`, which requires a real `command` -- see `backend/
    tests/test_livecorr1_diagnostics.py::test_def_0040_embedded_command_
    bypasses_grounding` for the full pipeline proof) -- BEFORE `enforce_
    execution_decision_on_guidance` (this function, tested here in
    isolation) ever runs. This function's own, narrower job is now
    strictly TARGET-CONFIRMATION gating: a step with no `command` to
    begin with has nothing for this function to strip -- correctly a
    no-op here, since the structural layer already owns that case."""
    contract = _contract(missing_context=["unit_id"])
    decision = derive_execution_decision(contract, _RUN_ID)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[TroubleshootingStep(action=f"Execute: {_RRU_COMMAND}", command=None)],
    )
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is False
    assert corrected.full_procedure_steps[0].command is None


def test_evidence_requested_is_also_suppressed_for_consistency() -> None:
    contract = _contract(missing_context=["unit_id"])
    decision = derive_execution_decision(contract, _RUN_ID)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="x",
        next_action="y",
        command=None,
        evidence_requested="z",
    )
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is True
    assert corrected.evidence_requested is None


def test_allowed_guidance_is_never_touched() -> None:
    """Non-regression: a fully-resolved, ALLOW-status turn's guidance is
    completely unaffected -- narrative fields survive intact."""
    contract = _contract(missing_context=[])
    decision = derive_execution_decision(contract, _RUN_ID)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Interpretation stands.",
        next_action="Do the real next step.",
        command=_RRU_COMMAND,
        evidence_requested="Confirm the outcome.",
    )
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is False
    assert corrected.interpretation == "Interpretation stands."
    assert corrected.next_action == "Do the real next step."
    assert corrected.command == _RRU_COMMAND
    assert corrected.evidence_requested == "Confirm the outcome."


# =============================================================================
# requires_unstructured_response_backstop -- precise trigger conditions
# =============================================================================


def test_backstop_does_not_fire_when_guidance_is_present() -> None:
    decision = derive_execution_decision(_contract(missing_context=["unit_id"]), _RUN_ID)
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=True) is False


def test_backstop_does_not_fire_for_allow_decisions() -> None:
    decision = derive_execution_decision(_contract(missing_context=[]), _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is False


def test_backstop_does_not_fire_for_absent_contract() -> None:
    """Deliberately narrow: an ABSENT contract (INVALID_CONTRACT) is a
    materially different, lower-confidence signal than a contract that
    POSITIVELY shows unresolved context -- this backstop only fires on
    the latter, to avoid over-blocking every ordinary governed-knowledge
    turn merely because `record_request_contract` was not called."""
    decision = derive_execution_decision(None, _RUN_ID)
    assert decision.status == RequestExecutionStatus.INVALID_CONTRACT
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is False


def test_backstop_fires_for_ambiguous_decisions_too() -> None:
    decision = derive_execution_decision(_contract(ambiguity=True), _RUN_ID)
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is True


def test_backstop_does_not_fire_for_requires_approval() -> None:
    decision = derive_execution_decision(
        _contract(intent=RequestIntent.ACTION, requested_output=RequestedOutput.ACTION, action_requested=True), _RUN_ID
    )
    assert decision.status == RequestExecutionStatus.REQUIRES_APPROVAL
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is False


# =============================================================================
# 19/20: safe description remains possible (non-regression)
# =============================================================================


def test_procedure_can_still_describe_branches_when_fully_resolved() -> None:
    """A PROCEDURE request with NOTHING missing (e.g. only asking to
    describe the branches conceptually, not execute one) is unaffected."""
    contract = _contract(intent=RequestIntent.PROCEDURE, requested_output=RequestedOutput.PROCEDURE_STEPS, missing_context=[])
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_operational_steps is True


def test_information_can_still_answer_facts_when_fully_resolved() -> None:
    contract = _contract(intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, subject="VSWR", missing_context=[])
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW


# =============================================================================
# Full chat_service.py integration -- the exact HW Partial Fault scenario
# =============================================================================


@pytest.mark.asyncio
async def test_hw_partial_fault_first_turn_no_guidance_no_contract_missing_context_blocks_both_commands() -> None:
    """THE exact reported live defect, reproduced structurally: incident_
    manager answers via free-form `summary` prose containing BOTH real
    commands, `troubleshooting_guidance` is never populated, but the
    validated contract correctly shows the unit branch as unresolved."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    live_defect_text = (
        "To handle a HW Partial Fault, a restart is allowed only on the RRU. Use the command: "
        f"{_RRU_COMMAND}. If SupportUnit is '---', then no restart. For AAS units, use: {_AAS_COMMAND}. "
        "Please note that the applicability of this information is currently unknown."
    )

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _contract(
            intent=RequestIntent.PROCEDURE,
            requested_output=RequestedOutput.PROCEDURE_STEPS,
            continuation=False,
            missing_context=["unit_type", "unit_id"],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        # troubleshooting_guidance is DELIBERATELY never registered -- this reproduces Root Cause A exactly.

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text=live_defect_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "how do i handle HW Partial Fault?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    content = completed.data["content"]
    assert "RRU-9" not in content
    assert "AAS-1" not in content
    assert "restartunit" not in content
    assert content != live_defect_text


@pytest.mark.asyncio
async def test_hw_partial_fault_first_turn_asks_for_unit_context() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _contract(
            intent=RequestIntent.TROUBLESHOOTING, continuation=False, missing_context=["unit_type", "unit_id"], run_id=run_id
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="whatever the model said, unused", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "how do i handle HW Partial Fault?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    # LIVE-CORR-2 -- DEF-0043: raw internal key names are never shown
    # verbatim -- rendered instead as safe, generic labels.
    assert "unit type" in completed.data["content"] or "unit identifier" in completed.data["content"]


@pytest.mark.asyncio
async def test_hw_partial_fault_safety_holds_for_information_intent_with_embedded_command_in_guidance() -> None:
    """§6: intent=INFORMATION, but the (structured, this time) guidance
    still attempts an executable command -- must still be blocked."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _contract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            continuation=False,
            missing_context=["unit_type", "unit_id"],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                interpretation="x",
                next_action=f"Run {_RRU_COMMAND}.",
                command=_RRU_COMMAND,
                evidence_requested="z",
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "what does the HW Partial Fault procedure say?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert "RRU-9" not in completed.data["content"]


@pytest.mark.asyncio
async def test_rru9_still_permitted_once_user_supplied_end_to_end() -> None:
    """Non-regression: the legitimate ALLOW path still works after all
    these corrections."""
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
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                interpretation="RRU confirmed.",
                next_action="Restart the RRU.",
                command=_RRU_COMMAND,
                evidence_requested="Confirm the restart completed.",
            ),
        )

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
async def test_ordinary_information_turn_with_no_missing_context_is_unaffected() -> None:
    """Non-regression: an ordinary, fully-resolved INFORMATION turn (no
    missing_context at all) is not touched by any of these new gates,
    even though INFORMATION is now in the target-specific intent set."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, subject="VSWR", missing_context=[], run_id=run_id
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="VSWR stands for Voltage Standing Wave Ratio.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "what is VSWR?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert completed.data["content"] == "VSWR stands for Voltage Standing Wave Ratio."


# =============================================================================
# 21: no later rewrite can reintroduce a blocked command
# =============================================================================


def test_command_suppression_fallback_text_never_contains_the_withheld_command() -> None:
    decision = derive_execution_decision(_contract(missing_context=["unit_id"]), _RUN_ID)
    fallback = command_suppression_fallback_text(decision)
    assert "RRU-9" not in fallback
    assert "AAS-1" not in fallback
    assert "accn" not in fallback


# =============================================================================
# AAS full accept flow (mirrors the RRU flow above -- both real, distinct
# governed commands from the same conditional procedure)
# =============================================================================


@pytest.mark.asyncio
async def test_aas1_permitted_once_user_supplied_end_to_end() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _contract(
            provided_context=[
                RequestParameter(name="unit_type", value="AAS", provenance=ParameterProvenance.USER),
                RequestParameter(name="unit_id", value="AAS-1", provenance=ParameterProvenance.USER),
            ],
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                interpretation="AAS confirmed.",
                next_action="Restart the AAS.",
                command=_AAS_COMMAND,
                evidence_requested="Confirm the restart completed.",
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "AAS-1", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert _AAS_COMMAND in completed.data["content"]
    # The sibling RRU command -- never asked for, never confirmed this turn -- must not leak in either.
    assert _RRU_COMMAND not in completed.data["content"]


# =============================================================================
# Knowledge-only identifier cannot become a live target merely by being
# the SUBJECT of a governed answer -- it must actually arrive via
# validated, provenance-checked USER-provided context (6A.13's own
# safety-critical `provided_context` re-verification, unchanged by this
# pass, exercised here end to end through the widened intent gate).
# =============================================================================


def test_knowledge_example_identifier_alone_never_satisfies_missing_context() -> None:
    """RRU-9/AAS-1 exist in the governed document purely as EXAMPLE unit
    identifiers for two conditional branches -- naming them in `subject`
    or leaving them as the ONLY context (never a real `provided_context`
    entry) must never resolve `missing_context` on its own."""
    contract = _contract(subject="HW Partial Fault (RRU-9 / AAS-1 examples)", provided_context=[], missing_context=["unit_type", "unit_id"])
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False


# =============================================================================
# Malformed / stale contract fails closed (never grants more than an
# explicit gate would)
# =============================================================================


def test_malformed_contract_state_fails_closed_to_none() -> None:
    """A corrupted/wrong-shaped session-state value must never be
    coerced into a trusted contract -- `load_current_turn_contract`
    returns `None`, and `derive_execution_decision(None, ...)` resolves
    to `INVALID_CONTRACT`, which never grants `may_emit_command`."""
    malformed_raw = {"intent": "TROUBLESHOOTING", "requested_output": "NOT_A_REAL_ENUM_VALUE"}
    parsed = load_current_turn_contract(malformed_raw, _RUN_ID)
    assert parsed is None
    decision = derive_execution_decision(parsed, _RUN_ID)
    assert decision.status == RequestExecutionStatus.INVALID_CONTRACT
    assert decision.may_emit_command is False


def test_non_dict_contract_state_fails_closed_to_none() -> None:
    parsed = load_current_turn_contract("not-a-dict-at-all", _RUN_ID)
    assert parsed is None


def test_stale_contract_run_id_fails_closed() -> None:
    """A contract stamped for a PRIOR turn's run_id must never authorize
    the CURRENT turn's command emission."""
    contract = _contract(missing_context=[], run_id="a-previous-turn-run-id")
    decision = derive_execution_decision(contract, "the-current-turns-run-id")
    assert decision.status == RequestExecutionStatus.INVALID_CONTRACT
    assert decision.may_emit_command is False


# =============================================================================
# SupportUnit / VSWR non-regression pointer: DEF-0027's own dedicated
# suites (test_def_0027_conditional_command_safety.py,
# test_def_0027_final_corrective_pass.py -- both unmodified by this pass,
# both re-run green as part of this milestone's own regression) already
# cover the SupportUnit no-restart branch and the VSWR flow exhaustively.
# This pass adds nothing new to that coverage; it is reconfirmed, not
# duplicated, per this milestone's own regression requirement.
# =============================================================================
