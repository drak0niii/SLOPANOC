"""Deterministic pending-step result consumption.

Live defect (session 0bc8c833-c7b7-4f23-9b03-0ed280d281f4, turn "this is the output: $ st fieldr"):
    the governed `st fieldr` read was PRESENTED; the operator pasted its output behind a
    conversational lead-in ("this is the output: $ st fieldr" + the table)
    -> result validation did not recognise the echoed command after the lead-in and the table shares
       no words with the step's objective -> AMBIGUOUS -> UNVALIDATED_RESULT: nothing bound, the step
       stayed PRESENTED (first incorrect transition)
    -> the specialist interpreted the output anyway and proposed a next step; the controller rejected
       it (the pending step "awaits its result") and re-presented the pending step without a command
    -> the synthesis did not present that step, so the completeness boundary rendered
       "The current diagnostic step is still awaiting its result ... Please provide ..." -- asking for
       the output the operator had just supplied.

Invariants under test:
    PENDING GOVERNED STEP + OPERATOR PROVIDES ITS RESULT = RESULT CONSUMED EXACTLY ONCE
    bind -> record -> transition -> leave pending eligibility -> specialist on the UPDATED progression
    a step whose result THIS turn bound is never rendered as "still awaiting its result"
    (not by the pending-result renderer, not through a stale snapshot, not through a re-presented
    proposal); the final response projects the post-binding progression: the new validated step,
    a clarification, a governed gap, an escalation or a resolution.
Production code is generic: `st fieldr` / the table are fixtures only.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import agent_tool as tae_agent_tool
from backend.agents.technical_authority_engineer import synthesis_boundary as sb
from backend.agents.technical_authority_engineer.progression_controller import (
    ProgressionController,
    ProposalDecision,
    TurnKind,
    awaits_result,
)
from backend.agents.technical_authority_engineer.result_validation import (
    ResultValidationStatus,
    same_observation,
    validate_operator_observation,
)
from backend.agents.technical_authority_engineer.synthesis_boundary import (
    RESPONSE_INCOMPLETE_TEXT,
    enforce_response_completeness,
    render_pending_result_request,
)
from backend.agents.team_manager.operational_routing import OperationalTurnKind
from backend.cases.troubleshooting_progression import (
    FaultProgression,
    ResultSource,
    StepStatus,
    TroubleshootingProgression,
    TroubleshootingStep,
)
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.tests.test_applicability_blocked_governed_action import CATALOG, DOC, SEARCH, _governed, _trace, use_production_specialist
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_operational_continuation_routing import (
    ALT_RESULT,
    HISTORY_ANSWER,
    PLUGIN_ID,
    _alt_presented,
    _assert_forced,
    _authorized,
    _events,
    _insufficient,
    _RoutedConversation,
)
from backend.tools.knowledge.diagnostic_trace import format_diagnostic_trace

FIELDR_ID = DOC.action_id("st fieldr")
FIELDR_OBJECTIVE = "Check the status of FieldReplaceableUnits"
FIELDR_EXPECTED = "The output of the 'st fieldr' command, showing the status of all FieldReplaceableUnits."
FIELDR_TABLE = (
    "Proxy  OpmState    ProductNumber    SerialNumber   Revision  Description\n"
    "-----------------------------------------------------------------------------------------\n"
    " 412   ENABLED     KRC161623/1      BR58392019     R1A       Radio 2217 B3\n"
    " 413   DISABLED    KRC161844/2      BR74910242     R2B       Radio 4449 B1 B3\n"
    " 414   ENABLED     KRC161711/1      BR93021944     R1C       Radio 2219 B8\n"
    "-----------------------------------------------------------------------------------------\n"
    "Total: 3 MOs"
)
LIVE_RESULT = "this is the output: $ st fieldr\n\n" + FIELDR_TABLE
"""The exact live message (fixture only)."""
ECHOED_RESULT = "$ st fieldr\n" + FIELDR_TABLE
SECTOR_OUTPUT = (
    "this is the output: $ st sector\n"
    "Proxy  AdmState    OpState    SectorId\n"
    " 501   UNLOCKED    ENABLED    1\n"
    " 502   UNLOCKED    ENABLED    2\n"
)
HISTORICAL_ALT_OUTPUT = (
    "this is the output: $ alt\n"
    "Date & Time (Local) S Specific Problem                    MO (Cause/AdditionalInfo)\n"
    "2026-10-04 09:15:02 M Service Unavailable                 ENodeBFunction=1,EUtranCellFDD=CELL_2 (Cell is unable to provide service)\n"
    ">>> Total: 1 Alarms (1 Major)\n"
)
"""A NEW run of the earlier (completed) step's command -- not a re-submission of its recorded result."""
# As live: the synthesis interprets the output but does not present the validated next step.
OMITTING_SYNTHESIS = "The FieldReplaceableUnit output shows one radio unit in the DISABLED operational state."
AWAITING = "still awaiting its result"


def _fieldr_step() -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "recommended", "technical_interpretation": "The RF unit alarm needs the FieldReplaceableUnit states.",
        "verified_evidence_citations": [],
        "diagnostic_step": {
            "action": FIELDR_OBJECTIVE, "reason": "Identify the unit raising the hardware fault.", "expected_evidence": FIELDR_EXPECTED,
            "procedure_action_id": FIELDR_ID, "command": None, "command_source": None, "restrictions": [],
        },
    }))]


def _outcome(payload: dict[str, Any]) -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps({"verified_evidence_citations": [], **payload}))]


@pytest.fixture
def conversation(isolated_km_repo, monkeypatch):  # noqa: F811
    use_production_specialist(monkeypatch)
    return isolated_km_repo, _RoutedConversation(monkeypatch)


async def _fieldr_presented(conv: _RoutedConversation) -> TroubleshootingStep:
    """alt presented -> alt result bound -> governed `st fieldr` presented (the live state before the defect)."""
    await _alt_presented(conv)
    t2 = await conv.turn(ALT_RESULT, [SEARCH, DOC.select(), CATALOG, _fieldr_step()], "Run `st fieldr` and share the output.", delegate=False)
    _assert_forced(t2, OperationalTurnKind.RESULT_PROVIDED)
    assert _authorized(t2, "st fieldr")
    fieldr = _step(t2, FIELDR_ID)
    assert fieldr.status is StepStatus.PRESENTED and fieldr.command == "st fieldr" and fieldr.result is None
    return fieldr


def _step(turn: dict[str, Any], action_id: str) -> TroubleshootingStep:
    return next(s for s in turn["progression"].steps if s.procedure_action_id == action_id)


def _completeness(turn: dict[str, Any]) -> dict[str, Any]:
    return turn["records"][-1][tae_agent_tool.RESPONSE_COMPLETENESS_KEY]


def _assert_turn_state_valid(turn: dict[str, Any]) -> None:
    """INVALID TURN STATE: result_recorded(step_id) AND the final response requests result(step_id)."""
    for step in turn["progression"].steps:
        if step.result is None:
            continue
        objective = (step.objective or "").rstrip(".")
        assert f"awaiting its result: {objective}" not in turn["final"], f"INVALID TURN STATE: {step.step_id} recorded yet requested"
        assert f"matched to the current diagnostic step ({objective}" not in turn["final"], f"INVALID TURN STATE: {step.step_id}"
        assert not awaits_result(step)


async def _live_result_turn(conv: _RoutedConversation, tae_calls: list[list[types.Part]], synthesis: str = OMITTING_SYNTHESIS) -> dict[str, Any]:
    return await conv.turn(LIVE_RESULT, tae_calls, synthesis, delegate=False)


NEXT_STEP = [SEARCH, DOC.select(), CATALOG, _governed(PLUGIN_ID, "Check plug-in unit states")]


# =============================================================================================
# Exact live regression (A / B / G / M)
# =============================================================================================


@pytest.mark.asyncio
async def test_live_regression_supplied_result_is_consumed_and_never_requested_again(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    fieldr = await _fieldr_presented(conv)
    t3 = await _live_result_turn(conv, NEXT_STEP)
    _assert_forced(t3, OperationalTurnKind.RESULT_PROVIDED)

    # A. bound once to the exact pending step; consumed; no longer pending.
    bound = _step(t3, FIELDR_ID)
    assert bound.step_id == fieldr.step_id and bound.status is StepStatus.COMPLETED
    assert bound.result is not None and bound.result.text == LIVE_RESULT and bound.result.source is ResultSource.OPERATOR_MESSAGE
    assert not bound.candidate_observations and not awaits_result(bound)
    (binding,) = _events(t3, "result_binding")
    assert binding["binding"] == "matched" and binding["step_id"] == fieldr.step_id and binding["consumed"] is True
    assert binding["old_status"] == "presented" and binding["new_status"] == "completed" and binding["result_source"] == "operator_message"
    assert binding["pending_after_binding"] is None and binding["consumed_step_ids"] == [fieldr.step_id]

    # The specialist reasoned from the UPDATED progression: its next step was accepted as a NEW step.
    progression_event = _events(t3, "progression")[-1]
    assert progression_event["turn_kind"] == "result" and progression_event["decision"] == ProposalDecision.NEW_STEP.value
    plugin = _step(t3, PLUGIN_ID)
    assert plugin.status is StepStatus.PRESENTED and plugin.step_id != fieldr.step_id
    (state,) = _events(t3, "continuation_state")
    assert state["consumed_step_ids"] == [fieldr.step_id] and state["next_step_id"] == plugin.step_id

    # Completeness never offered the consumed step as a pending-result candidate.
    completeness = _completeness(t3)
    assert completeness["pending_step"] is None and completeness["consumed_step_ids"] == [fieldr.step_id]
    assert completeness["result_consumption"]["old_step_consumed"] is True

    # G / M. The synthesis omitted the validated new step: the server projects THAT step, never the old one.
    assert AWAITING not in t3["final"] and FIELDR_OBJECTIVE not in t3["final"]
    assert "`st pluginunit`" in t3["final"] and _authorized(t3, "st pluginunit")
    _assert_turn_state_valid(t3)
    trace = format_diagnostic_trace(_trace(t3))
    assert "RESULT BINDING" in trace and "binding=matched" in trace and "consumed=True" in trace
    assert "CONTINUATION STATE" in trace and "COMPLETENESS DECISION decision=project_validated_step" in trace


@pytest.mark.asyncio
async def test_b_result_with_the_command_echoed_at_line_start_is_bound(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    fieldr = await _fieldr_presented(conv)
    t3 = await conv.turn(ECHOED_RESULT, NEXT_STEP, "Run `st pluginunit`.", delegate=False)
    assert _step(t3, FIELDR_ID).status is StepStatus.COMPLETED and _step(t3, FIELDR_ID).step_id == fieldr.step_id
    assert "`st pluginunit`" in t3["final"] and AWAITING not in t3["final"]
    _assert_turn_state_valid(t3)


@pytest.mark.asyncio
async def test_m_manual_next_step_omitted_by_synthesis_is_projected_not_the_old_step(conversation) -> None:
    """The live specialist's own continuation: a command-less observation proposed after the result."""
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _fieldr_presented(conv)
    manual = _outcome({
        "outcome": "recommended", "technical_interpretation": "One radio unit is DISABLED.",
        "diagnostic_step": {"action": "Confirm whether the disabled radio unit is the one raising the RF unit alarm",
                            "reason": "Correlate the disabled unit with the alarm.", "expected_evidence": "The operator's confirmation",
                            "command": None, "command_source": None, "restrictions": []},
    })
    t3 = await _live_result_turn(conv, [SEARCH, DOC.select(), manual], synthesis="Thanks, noted.")
    assert _step(t3, FIELDR_ID).status is StepStatus.COMPLETED
    assert AWAITING not in t3["final"] and FIELDR_OBJECTIVE not in t3["final"]
    assert t3["final"] != "Thanks, noted."
    _assert_turn_state_valid(t3)


# =============================================================================================
# C / D / E. Binding only to the exact pending step
# =============================================================================================


def _pending(command: Optional[str] = "st fieldr", expected: str = FIELDR_EXPECTED) -> TroubleshootingStep:
    return TroubleshootingStep(fault_id="F-1", objective=FIELDR_OBJECTIVE, command=command, expected_evidence=expected, status=StepStatus.PRESENTED)


@pytest.mark.parametrize(
    "text",
    [
        LIVE_RESULT,
        ECHOED_RESULT,
        "here is the output of st fieldr:\n" + FIELDR_TABLE,
        "here is the `st fieldr` output:\n" + FIELDR_TABLE,
        "NODE01> st fieldr\n" + FIELDR_TABLE,
    ],
)
def test_b_echoed_command_with_output_validates(text: str) -> None:
    validation = validate_operator_observation(_pending(), text, ["alt"])
    assert validation.status is ResultValidationStatus.VALIDATED and validation.signals["echo"] is True


def test_c_unechoed_output_binds_only_when_its_structure_matches_the_expected_evidence() -> None:
    matching = _pending(expected="The OpmState and ProductNumber of every FieldReplaceableUnit.")
    assert validate_operator_observation(matching, FIELDR_TABLE, ["alt"]).status is ResultValidationStatus.VALIDATED
    # The same table against evidence it shares nothing with stays unattributed: fail closed.
    assert validate_operator_observation(_pending(), FIELDR_TABLE, ["alt"]).status is ResultValidationStatus.AMBIGUOUS


@pytest.mark.parametrize(
    "text, status",
    [
        (SECTOR_OUTPUT, ResultValidationStatus.AMBIGUOUS),  # D. another (unknown) command's output
        (HISTORICAL_ALT_OUTPUT, ResultValidationStatus.FOREIGN),  # E. an earlier step's command
        ("I don't have the output of st fieldr yet", ResultValidationStatus.NOT_A_RESULT),
        ("how do I get the output of st fieldr", ResultValidationStatus.NOT_A_RESULT),
        ("what does the output of st fieldr mean?", ResultValidationStatus.NOT_A_RESULT),
    ],
)
def test_d_e_output_of_another_command_or_a_mention_without_output_never_binds(text: str, status: ResultValidationStatus) -> None:
    validation = validate_operator_observation(_pending(), text, ["alt"])
    assert validation.status is status and not validation.bindable


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [SECTOR_OUTPUT, HISTORICAL_ALT_OUTPUT], ids=["d_wrong_command", "e_historical_step"])
async def test_d_e_unattributable_output_is_not_bound_and_is_requested_honestly(conversation, text: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    fieldr = await _fieldr_presented(conv)
    t3 = await conv.turn(text, [_insufficient(), _insufficient()], HISTORY_ANSWER, delegate=False)
    step = _step(t3, FIELDR_ID)
    assert step.step_id == fieldr.step_id and step.status is StepStatus.PRESENTED and step.result is None
    assert len(step.candidate_observations) == 1  # recorded as a candidate, never as the result
    alt = next(s for s in t3["progression"].steps if s.command == "alt")
    assert alt.result is not None and alt.result.text == ALT_RESULT.strip()  # the historical step is untouched
    (binding,) = _events(t3, "result_binding")
    assert binding["binding"] in ("foreign", "not_attributable") and binding["consumed"] is False
    # The step was NOT consumed, so its result may be requested -- stating that the output did not match it.
    assert t3["final"].startswith(f"The output in your message could not be matched to the current diagnostic step ({FIELDR_OBJECTIVE})")
    assert "`st fieldr`" not in t3["final"]


# =============================================================================================
# F. Duplicate result
# =============================================================================================


@pytest.mark.asyncio
async def test_f_duplicate_result_creates_nothing_and_reopens_nothing(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    fieldr = await _fieldr_presented(conv)
    t3 = await _live_result_turn(conv, NEXT_STEP)
    plugin = _step(t3, PLUGIN_ID)
    steps_before = [(s.step_id, s.status) for s in t3["progression"].steps]
    t4 = await _live_result_turn(conv, [_insufficient(), _insufficient()], synthesis=HISTORY_ANSWER)
    assert [(s.step_id, s.status) for s in t4["progression"].steps] == steps_before  # no new step, nothing reopened
    again = _step(t4, FIELDR_ID)
    assert again.step_id == fieldr.step_id and again.status is StepStatus.COMPLETED and again.result.text == LIVE_RESULT
    pending = _step(t4, PLUGIN_ID)
    assert pending.step_id == plugin.step_id and pending.status is StepStatus.PRESENTED and not pending.candidate_observations
    (binding,) = _events(t4, "result_binding")
    assert binding["binding"] == "known_result" and binding["known_result_step_id"] == fieldr.step_id and binding["consumed"] is False
    assert _events(t4, "progression")[-1]["turn_kind"] == TurnKind.KNOWN_RESULT.value
    # The current (plug-in) step may be requested; the consumed step never is.
    assert f"awaiting its result: {FIELDR_OBJECTIVE}" not in t4["final"]
    assert t4["final"].startswith(f"That output was already recorded as the result of an earlier step ({FIELDR_OBJECTIVE})")
    _assert_turn_state_valid(t4)


def _controller(*steps: TroubleshootingStep) -> ProgressionController:
    progression = TroubleshootingProgression()
    progression.add_fault(FaultProgression(fault_id="F-1", symptom_summary="ESS Service Unavailable"))
    for step in steps:
        progression.append_step(step)
    return ProgressionController({}, TroubleshootingState(fault_id="F-1", symptom_summary="ESS Service Unavailable"), progression=progression)


def test_f_duplicate_without_a_pending_step_is_a_known_result() -> None:
    step = _pending()
    controller = _controller(step)
    controller.classify_turn(LIVE_RESULT)
    controller.apply_operator_turn(LIVE_RESULT)
    assert controller.turn_kind is TurnKind.RESULT and step.status is StepStatus.COMPLETED
    again = _controller(step)
    assert again.classify_turn(LIVE_RESULT) is TurnKind.KNOWN_RESULT and again.known_result_step_id == step.step_id
    history = list(step.status_history)
    again.apply_operator_turn(LIVE_RESULT)
    assert step.status is StepStatus.COMPLETED and step.status_history == history and not step.candidate_observations
    assert again.consumed_step_ids == [] and again.result_consumption()["binding"] == "known_result"
    assert same_observation("again:\n" + FIELDR_TABLE, LIVE_RESULT) and not same_observation(SECTOR_OUTPUT, LIVE_RESULT)


def test_a_binding_is_exactly_once_and_the_bound_step_never_pends_again() -> None:
    step = _pending()
    controller = _controller(step)
    assert controller.classify_turn(LIVE_RESULT) is TurnKind.RESULT
    controller.apply_operator_turn(LIVE_RESULT)
    assert controller.consumed_step_ids == [step.step_id] and controller.pending_step() is None
    consumption = controller.result_consumption()
    assert consumption["result_provided"] and consumption["result_bound"] and consumption["result_recorded"]
    assert consumption["old_step_consumed"] and consumption["bound_step_id"] == step.step_id
    assert [h.to_status for h in step.status_history][-3:] == [StepStatus.OBSERVED, StepStatus.VERIFIED, StepStatus.COMPLETED]
    # Structural: a step holding a result can never return to a pending status.
    for status in (StepStatus.PRESENTED, StepStatus.VALIDATED, StepStatus.PROPOSED, StepStatus.BLOCKED_BY_CLARIFICATION):
        with pytest.raises(ValueError):
            controller.progression.set_step_status(step.step_id, status)
    # Even a corrupted status field cannot make it pending (the recorded result decides).
    step.status = StepStatus.PRESENTED
    assert not awaits_result(step) and controller.pending_step() is None


# =============================================================================================
# H / I / J / K / L. Completeness boundary
# =============================================================================================


def _record(completeness: dict[str, Any], **execution: Any) -> dict[str, Any]:
    return {"outcome": "insufficient_evidence", "diagnostic_step": None, tae_agent_tool.RESPONSE_COMPLETENESS_KEY: completeness, **execution}


STALE_PENDING = {"step_id": "step-fieldr", "objective": FIELDR_OBJECTIVE, "expected_evidence": "The status of all FieldReplaceableUnits"}


@pytest.mark.parametrize(
    "elements, execution",
    [
        (["governed_gap"], {}),  # H
        (["escalation"], {"outcome": "escalation_required", "escalation_reason": "Hardware replacement is outside the procedure."}),  # I
        (["resolved"], {}),  # J
    ],
    ids=["h_governed_gap", "i_escalation", "j_resolution"],
)
def test_h_i_j_bound_result_with_gap_escalation_or_resolution_never_requests_the_old_result(elements, execution) -> None:
    synthesis = "Rendered governed outcome."
    record = _record(
        {"forced_route": True, "required": True, "satisfied": True, "elements": elements,
         "pending_step": STALE_PENDING, "consumed_step_ids": ["step-fieldr"], "presented_pending_step_id": None},
        **execution,
    )
    decision: dict[str, Any] = {}
    assert enforce_response_completeness(synthesis, record, decision) == synthesis
    assert decision["candidate_step_id"] == "step-fieldr" and decision.get("consumed_step_rejected") == "step-fieldr"


def test_l_stale_pre_binding_snapshot_loses_to_the_post_binding_state() -> None:
    # The completeness record carries a pending snapshot taken BEFORE this turn's binding.
    record = _record({"forced_route": True, "required": True, "satisfied": False, "elements": [],
                      "pending_step": STALE_PENDING, "consumed_step_ids": ["step-fieldr"]})
    decision: dict[str, Any] = {}
    text = enforce_response_completeness("ok", record, decision)
    assert text == RESPONSE_INCOMPLETE_TEXT and AWAITING not in text
    assert decision["decision"] == "consumed_step_not_renderable"
    assert render_pending_result_request(STALE_PENDING, ["step-fieldr"]) is None
    # A validated record re-presenting the consumed step is not presentable either.
    re_presented = {"outcome": "recommended", "diagnostic_step": {"action": FIELDR_OBJECTIVE, "command": None},
                    tae_agent_tool.RESPONSE_COMPLETENESS_KEY: {"forced_route": True, "required": True, "satisfied": True,
                                                               "elements": ["next_step"], "pending_step": None,
                                                               "consumed_step_ids": ["step-fieldr"], "presents_consumed_step": True}}
    assert enforce_response_completeness("ok", re_presented) == RESPONSE_INCOMPLETE_TEXT


def test_l_controller_snapshot_before_binding_never_reaches_completeness(monkeypatch) -> None:
    step = _pending()
    controller = _controller(step)
    controller.classify_turn(LIVE_RESULT)
    snapshot = controller.pending_step()  # captured BEFORE binding
    assert snapshot is step
    controller.apply_operator_turn(LIVE_RESULT)
    monkeypatch.setattr(tae_agent_tool, "_server_forced_route", lambda run_id: True)
    record = tae_agent_tool._response_completeness({"outcome": "insufficient_evidence"}, rule=None, run_id=None,
                                                   progression=controller, fault_id="F-1")
    assert record["pending_step"] is None and record["consumed_step_ids"] == [step.step_id]


@pytest.mark.asyncio
async def test_k_genuine_pending_step_without_a_result_is_still_requested(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    fieldr = await _fieldr_presented(conv)
    t3 = await conv.turn("what next?", [_insufficient(), _insufficient()], HISTORY_ANSWER, delegate=False)
    _assert_forced(t3, OperationalTurnKind.GENERIC_CONTINUATION)
    assert _step(t3, FIELDR_ID).status is StepStatus.PRESENTED and _step(t3, FIELDR_ID).result is None
    assert t3["final"].startswith(f"The current diagnostic step is still awaiting its result: {FIELDR_OBJECTIVE}")
    assert _completeness(t3)["pending_step"]["step_id"] == fieldr.step_id and _completeness(t3)["consumed_step_ids"] == []


# =============================================================================================
# Mutations: each protection removed alone reproduces a stale "awaiting result" / lost next step
# =============================================================================================


def _mutate(monkeypatch: Any, mutation: str) -> None:
    if mutation == "result_consumption_guard":
        original = ProgressionController._complete_with_result

        def _unconsumed(self, step, text, source, execution_id, validation):  # result "bound" but never consumed
            original(self, step, text, source, execution_id, validation)
            step.result, step.status = None, StepStatus.PRESENTED
            self.consumed_step_ids.clear()

        monkeypatch.setattr(ProgressionController, "_complete_with_result", _unconsumed)
    elif mutation == "post_binding_refresh":
        original = ProgressionController.pending_step

        def _snapshot(self):  # the pre-binding pending snapshot reused after the binding
            if not hasattr(self, "_stale_pending"):
                self._stale_pending = original(self)
            return self._stale_pending

        monkeypatch.setattr(ProgressionController, "pending_step", _snapshot)
    elif mutation == "new_step_projection_priority":
        monkeypatch.setattr(sb, "_project_current_step", lambda execution, evidence: sb.RESPONSE_INCOMPLETE_TEXT)
    elif mutation == "lead_in_echo_recognition":
        import re

        from backend.agents.technical_authority_engineer import result_validation as rv

        never = re.compile(r"(?!x)x")
        for name in ("_LEAD_IN_PROMPT", "_LEAD_IN_ANNOUNCED", "_ANNOUNCED_AFTER"):
            monkeypatch.setattr(rv, name, never)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["lead_in_echo_recognition", "result_consumption_guard", "post_binding_refresh", "new_step_projection_priority"]
)
async def test_mutation_breaks_the_live_regression(conversation, monkeypatch, mutation: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _fieldr_presented(conv)
    _mutate(monkeypatch, mutation)
    t3 = await _live_result_turn(conv, NEXT_STEP)
    # Without the protection the live regression's acceptance fails: the validated next step is lost.
    assert "`st pluginunit`" not in t3["final"]
    if mutation == "result_consumption_guard":
        assert t3["final"].startswith(f"The current diagnostic step is still awaiting its result: {FIELDR_OBJECTIVE}")
    if mutation == "lead_in_echo_recognition":  # the live first incorrect transition: AMBIGUOUS, nothing bound
        assert _step(t3, FIELDR_ID).status is StepStatus.PRESENTED and _step(t3, FIELDR_ID).result is None


def test_mutation_completeness_consumed_step_exclusion_breaks_the_stale_snapshot_regression(monkeypatch) -> None:
    monkeypatch.setattr(sb, "_renderable_pending", lambda completeness, decision: completeness.get("pending_step"))
    original = sb.render_pending_result_request
    monkeypatch.setattr(sb, "render_pending_result_request", lambda pending, consumed=(): original(pending))
    record = _record({"forced_route": True, "required": True, "satisfied": False, "elements": [],
                      "pending_step": STALE_PENDING, "consumed_step_ids": ["step-fieldr"]})
    assert AWAITING in enforce_response_completeness("ok", record)
