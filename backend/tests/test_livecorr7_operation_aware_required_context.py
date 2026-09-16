"""LIVE-CORR-7 -- Exact Commands Request Irrelevant Generic Context.

CONFIRMED LIVE DEFECT: "what is the cmd i need to run the check and list
alarms present?" -> "no, i just need a cmd to run a check on alarms for
ericsson" -- a request whose contract correctly showed `provided_context_
keys=["system_name"]` still required `unit_id`/`unit_type` (plus a model-
declared `alarm_type`), even though a generic "list currently present
alarms" operation never concerns one specific RRU/AAS unit at all.

PROVEN ROOT CAUSE: `required_target_parameter_gaps` (request_contract.py)
had exactly ONE fallback for "no `unit_type` was ever provided, and this
is an `EXACT_COMMAND` request": unconditionally require BOTH `unit_type`
AND `unit_id`, regardless of what the ACTUAL grounded operation is --
that function's own DEF-0038 docstring already named this precisely:
"this function has NO deterministic signal... for whether the specific
governed operation actually selected this turn requires a target at
all... a future milestone with access to the selected governed
operation's own target-cardinality classification can safely relax
this." This is that milestone.

FIX: `required_target_parameter_gaps` gained one new, optional, additive
parameter -- `grounded_command_candidate` -- THIS turn's own real,
already-grounded `TroubleshootingGuidance.command` text, when one
already exists. Reuses `extract_canonical_identifiers` (the SAME
deterministic identifier recognizer already used for provided-context
normalization -- never a new taxonomy, never text/keyword matching of
the user's own words) against the CANDIDATE's own verbatim content: no
recognized unit-class identifier at all in a real candidate positively
proves the operation does not concern one specific unit -- no gap.
Absence of a candidate fails CLOSED to the original, unchanged blanket
rule. `derive_execution_decision` (request_execution_policy.py) forwards
this through from `chat_service.py`'s own already-captured guidance (the
same one LIVE-CORR-6 fixed the retrieval of), and now sources `unit_type`/
`unit_id` specifically (for `EXACT_COMMAND` requests only) EXCLUSIVELY
from the fresh, grounded-aware call -- never from a stale, ungrounded
value already baked into the persisted `RequestContract.missing_context`.

NOT deterministically fixed in this pass, and reported honestly rather
than guessed at: the live report's third field, `alarm_type`, has NO
deterministic backing anywhere in this codebase (confirmed by exhaustive
grep) -- it is a purely model-declared `missing_context` entry (rule A of
`reconcile_missing_context`), not something this pass's deterministic
`required_target_parameter_gaps` fix can remove without either inventing
a new mechanism to second-guess every model-declared key (a materially
larger change than this surgical pass) or weakening the EXISTING,
deliberate "CONDITIONAL COMMAND HANDLING" prompt mechanism genuinely
different, non-target-specific conditions rely on.

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
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    required_target_parameter_gaps,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionStatus,
    derive_execution_decision,
)
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RUN_ID = "livecorr7-run"
_ALARM_LISTING_COMMAND = "get alarms"
_RRU_EXAMPLE_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


# =============================================================================
# A -- alarm listing: no unit_id/unit_type unless the grounded command needs one
# =============================================================================


def test_a_alarm_listing_with_no_unit_reference_in_grounded_command_requires_nothing() -> None:
    """"give me the command to list current alarms on Ericsson" -- the
    real, grounded candidate is a generic, system-wide listing command
    with no RRU/AAS reference at all. No unit_id, no unit_type."""
    gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND,
        RequestedOutput.EXACT_COMMAND,
        [RequestParameter(name="system_name", value="Ericsson", provenance=ParameterProvenance.USER)],
        grounded_command_candidate=_ALARM_LISTING_COMMAND,
    )
    assert gaps == []


def test_a_alarm_listing_end_to_end_via_derive_execution_decision() -> None:
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="Alarm listing",
        provided_context=[RequestParameter(name="system_name", value="Ericsson", provenance=ParameterProvenance.USER)],
        missing_context=[],
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID, grounded_command_candidate=_ALARM_LISTING_COMMAND)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert "unit_id" not in decision.missing_context
    assert "unit_type" not in decision.missing_context
    # Command remains governed by every OTHER existing safeguard --
    # this test only proves the GENERIC gate is no longer irrelevant;
    # evidence.py's own grounding is a completely separate, unchanged
    # layer that still must independently agree before a command
    # actually reaches the user.


# =============================================================================
# B -- RRU restart: genuine target requirements remain mandatory
# =============================================================================


def test_b_rru_restart_with_no_context_at_all_still_requires_both() -> None:
    """No grounded candidate yet (e.g. the very first turn, before any
    target is even discussed) fails CLOSED to the original, unchanged
    blanket rule -- exactly as before this pass."""
    gaps = required_target_parameter_gaps(RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND, [])
    assert gaps == ["unit_id", "unit_type"]


def test_b_rru_restart_with_a_grounded_identifier_bearing_candidate_still_requires_both() -> None:
    """A real grounded candidate that DOES reference a unit-class
    identifier (e.g. the governed procedure's own example, "RRU-9")
    positively proves the operation concerns a specific unit -- the
    blanket requirement is not relaxed."""
    gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND, [], grounded_command_candidate=_RRU_EXAMPLE_COMMAND
    )
    assert gaps == ["unit_id", "unit_type"]


def test_b_command_cannot_be_emitted_prematurely() -> None:
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="RRU restart",
        missing_context=[],
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID, grounded_command_candidate=_RRU_EXAMPLE_COMMAND)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False
    assert sorted(decision.missing_context) == ["unit_id", "unit_type"]


# =============================================================================
# C -- incremental RRU context across turns
# =============================================================================


def test_c_turn1_missing_both_turn2_unit_type_satisfied_only_unit_id_remains() -> None:
    # Turn 1: nothing provided yet.
    turn1_gaps = required_target_parameter_gaps(RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND, [])
    assert sorted(turn1_gaps) == ["unit_id", "unit_type"]

    # Turn 2: "its an rru" -- unit_type now verified.
    turn2_gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND,
        RequestedOutput.EXACT_COMMAND,
        [RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
        grounded_command_candidate=_RRU_EXAMPLE_COMMAND,
    )
    assert turn2_gaps == ["unit_id"]


def test_c_end_to_end_incremental_context_via_derive_execution_decision() -> None:
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="RRU restart",
        provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
        missing_context=[],
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID, grounded_command_candidate=_RRU_EXAMPLE_COMMAND)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False
    assert decision.missing_context == ["unit_id"]


# =============================================================================
# D -- different operations deterministically produce different requirements
# =============================================================================


def test_d_two_exact_command_operations_produce_different_required_context() -> None:
    """THE critical proof: identical `intent`/`requested_output`
    (`COMMAND`/`EXACT_COMMAND`) and identical (empty) `provided_context` --
    the ONLY thing that differs is the grounded command candidate's own
    content -- yet the two required-context sets are genuinely different."""
    alarm_listing_gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND, [], grounded_command_candidate=_ALARM_LISTING_COMMAND
    )
    rru_restart_gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND, [], grounded_command_candidate=_RRU_EXAMPLE_COMMAND
    )
    assert alarm_listing_gaps == []
    assert rru_restart_gaps == ["unit_id", "unit_type"]
    assert alarm_listing_gaps != rru_restart_gaps


# =============================================================================
# E -- genuine incomplete command still blocks (non-regression)
# =============================================================================


def test_e_genuinely_incomplete_command_still_blocks() -> None:
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="RRU restart",
        provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
        missing_context=[],
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID, grounded_command_candidate=_RRU_EXAMPLE_COMMAND)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False


def test_e_model_declared_condition_for_non_exact_command_output_is_unaffected() -> None:
    """Non-regression (the exact shape that broke during this pass' own
    first implementation attempt): a model-declared `unit_type`/`unit_id`
    condition for a NON-`EXACT_COMMAND` output (e.g. `PROCEDURE_STEPS`)
    is a genuine, deliberate signal (prompts.py's own "CONDITIONAL COMMAND
    HANDLING") -- `required_target_parameter_gaps`'s own EXACT_COMMAND-only
    blanket rule never applies here, so this pass' `EXACT_COMMAND`-scoped
    exclusion must never touch it either."""
    contract = RequestContract(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        missing_context=["unit_type", "unit_id"],
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID, grounded_command_candidate=_ALARM_LISTING_COMMAND)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert sorted(decision.missing_context) == ["unit_id", "unit_type"]


# =============================================================================
# Full pipeline -- ChatService + FakeRunner, proving the actual rendering
# boundary uses the newly-narrowed, operation-aware requirement.
# =============================================================================


async def _run_turn(monkeypatch: Any, contract_kwargs: dict[str, Any], guidance_command: Optional[str], user_text: str) -> Any:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(run_id=run_id, **contract_kwargs)
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        if guidance_command is not None:
            register_troubleshooting_guidance(
                run_id,
                TroubleshootingGuidance(
                    interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                    next_action="Run the command below.",
                    command=guidance_command,
                    source_section_id=None,
                ),
            )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, user_text, "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    return completed


@pytest.mark.asyncio
async def test_full_pipeline_alarm_listing_command_not_blocked_on_irrelevant_context(monkeypatch: Any) -> None:
    completed = await _run_turn(
        monkeypatch,
        contract_kwargs=dict(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="Alarm listing",
            provided_context=[RequestParameter(name="system_name", value="Ericsson", provenance=ParameterProvenance.USER)],
            missing_context=[],
        ),
        guidance_command=_ALARM_LISTING_COMMAND,
        user_text="no, i just need a cmd to run a check on alarms for ericsson",
    )
    assert _ALARM_LISTING_COMMAND in completed.data["content"]


@pytest.mark.asyncio
async def test_full_pipeline_rru_restart_still_withheld_and_clarified(monkeypatch: Any) -> None:
    completed = await _run_turn(
        monkeypatch,
        contract_kwargs=dict(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="RRU restart",
            missing_context=[],
        ),
        guidance_command=_RRU_EXAMPLE_COMMAND,
        user_text="give me the command to restart an RRU",
    )
    assert _RRU_EXAMPLE_COMMAND not in completed.data["content"]
    assert "unit" in completed.data["content"].lower()
