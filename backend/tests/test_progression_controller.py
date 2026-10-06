"""Progression Controller + pending-step continuity (TAE proposes progression; the server owns it).

    ONE VALIDATED STEP -> OBSERVE RESULT -> VERIFY / INTERPRET -> REASSESS -> NEXT STEP
    NO RESULT = NO PROGRESSION (unless the operator reports the step cannot be performed)

End-to-end cases drive the real ChatService + real TechnicalAuthorityAgentTool with scripted
LLMs; unit cases drive the controller directly. The controller governs sequencing only: evidence
selection, ProcedureAction resolution, Command Authority, policy and HITL are unchanged.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.progression_controller import (
    PENDING_STATUSES,
    ProgressionController,
    ProposalDecision,
    TurnKind,
    is_multi_action_command,
)
from backend.agents.technical_authority_engineer.troubleshooting_threads import THREADS_STATE_KEY
from backend.cases.troubleshooting_progression import ProgressionPhase, StepStatus, load_progression
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _CATALOG, _Conversation, _respond, _search, _select

_KID, _SEC = "KID-RADIO-FAULT", "sec-checks"
_CONTENT = (
    "Alarm validation: `alt`\n"
    "Radio unit state check: `st ru`\n"
    "Cell state check: `st cell`\n"
    "Plug-in unit state check: `st pluginunit`\n"
    "Combined unit state check: `st pluginunit; st fieldr`\n"
)
_IDS = {
    a.command_template: a.action_id
    for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label="v1", section_id=_SEC, content=_CONTENT)[0]
}
_ALT, _RU, _CELL, _PIU = (_IDS[c] for c in ("alt", "st ru", "st cell", "st pluginunit"))
_CHAIN = "pa-chained-action"  # the combined governed line is never extracted as a ProcedureAction

_RU_STEP, _CELL_STEP, _PIU_STEP = "Check the radio unit state", "Check the cell state", "Check the plug-in unit state"


def _mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID, document_type=KnowledgeDocumentType.MOP, title="Radio Unit Fault Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id=f"{_KID}.docx", display_name="Radio Unit Fault Procedure"),
        sections=[KnowledgeSection(section_id=_SEC, knowledge_id=_KID, heading="Radio Unit Fault Procedure", sequence=0, content=_CONTENT, source_locator="lines:1-5")],
    )


def _propose(action_id: str, action: str) -> list:
    return [_search("radio unit fault"), _select(_KID, _SEC), _CATALOG, _respond(action_id=action_id, action=action)]


def _decision(turn: dict[str, Any]) -> dict[str, Any]:
    return [e for e in turn["trace"]["operational_events"] if e.get("stage") == "progression"][-1]


def _checks(turn: dict[str, Any]) -> list[tuple[Optional[str], str]]:
    return [(c["procedure_action_id"], c["status"]) for c in turn["state"]["troubleshooting_state"]["diagnostic_history"]]


def _pending(turn: dict[str, Any]) -> list:
    progression = load_progression(turn["state"])
    return [s for s in progression.steps_for(progression.active_fault_id) if s.status in PENDING_STATUSES]


async def _start(conv: _Conversation) -> dict[str, Any]:
    t1 = await conv.turn(
        "Ericsson 5G: radio unit fault alarm on the node. What should I check?",
        _propose(_RU, _RU_STEP), "Run st ru.", {"subject_component": "radio unit"},
    )
    assert t1["record"]["diagnostic_step"]["command"] == "st ru"
    assert _decision(t1)["decision"] == "new_step"
    return t1


# 1. The live sequence: RU -> RU output -> Cell -> cell output -> PlugInUnit -> "what is next cmd?" -------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("specialist_step", "tm_final", "resolves"),
    [
        (_propose(_ALT, "Validate the active alarms"), "Next, run alt.", False),  # drifts back to alarm validation
        (_propose(_RU, _RU_STEP), "Next, run st ru.", False),  # drifts back to the completed RU check
        (_propose(_PIU, _PIU_STEP), "Run st pluginunit.", True),  # resolves the pending step
    ],
    ids=["drift-to-alarm-validation", "drift-to-ru-check", "resolves-pending"],
)
async def test_what_is_next_cmd_stays_on_the_pending_pluginunit_step(isolated_km_repo, monkeypatch, specialist_step, tm_final, resolves) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _start(conv)

    t2 = await conv.turn("st ru output:\nRadioUnit=1 OPER=ENABLED AVAIL=", _propose(_CELL, _CELL_STEP), "Run st cell.")
    assert t2["tae_request"].get("pending_step") is None, "the RU result closed the RU step before the specialist ran"
    assert t2["record"]["diagnostic_step"]["command"] == "st cell"

    t3 = await conv.turn("st cell result: NRCellDU=1 OPER=DISABLED", _propose(_PIU, _PIU_STEP), "Run st pluginunit.")
    assert t3["record"]["diagnostic_step"]["command"] == "st pluginunit"
    piu_check_id = t3["state"]["troubleshooting_state"]["diagnostic_history"][-1]["check_id"]

    t4 = await conv.turn("what is next cmd?", specialist_step, tm_final)
    request = t4["tae_request"]
    assert request["pending_step"]["procedure_action_id"] == _PIU and request["pending_step"]["turn_kind"] == "command_follow_up"
    assert request["turn_request_contract"]["focus"] == "continue"
    assert request["turn_request_contract"]["diagnostic_objective"] == f"Resolve the pending diagnostic step: {_PIU_STEP}"

    step = t4["record"]["diagnostic_step"]
    if resolves:
        assert _decision(t4)["decision"] == "pending_step_resolved"
        assert step["command"] == "st pluginunit" and "st pluginunit" in t4["final"]
    else:
        # Acquisition continuity: the drifting proposal is never accepted or presented; the operator's
        # command request is answered with the PENDING step's own governed action, re-derived from this
        # run's selected evidence and freshly authorized (previously: the step without its command).
        assert _decision(t4)["decision"] == "pending_step_resolved"
        assert step["action"] == _PIU_STEP and step["command"] == "st pluginunit" and step["procedure_action_id"] == _PIU
        (continuity,) = [e for e in t4["trace"]["operational_events"] if e.get("stage") == "acquisition_continuity"]
        assert (continuity["rule"], continuity["outcome"], continuity["authority_decision"]) == ("acquisition_request", "server_reconciled", "authorized")
        assert continuity["model_proposal"]["procedure_action_id"] != _PIU and continuity["governed_search_performed"] is True
        assert t4["final"].startswith(_PIU_STEP) and "`st pluginunit`" in t4["final"], "the operator is answered with the pending step"
        assert not re.search(r"\balt\b", t4["final"]) and "st ru" not in t4["final"]

    # Never jumps back: three checks, RU and Cell carry their results, PlugInUnit is the only pending step.
    assert _checks(t4) == [(_RU, "executed"), (_CELL, "executed"), (_PIU, "recommended")]
    assert t4["state"]["troubleshooting_state"]["diagnostic_history"][-1]["check_id"] == piu_check_id
    progression = load_progression(t4["state"])
    ru, cell, piu = progression.steps_for(progression.active_fault_id)
    assert (ru.status, cell.status, piu.status) == (StepStatus.COMPLETED, StepStatus.COMPLETED, StepStatus.PRESENTED)
    assert "RadioUnit=1" in ru.result.text and "NRCellDU=1" in cell.result.text
    assert [s.step_id for s in _pending(t4)] == [piu.step_id]
    assert progression.current_phase is ProgressionPhase.AWAITING_OBSERVATION
    assert [s.procedure_action_id for s in (ru, cell, piu)] == [_RU, _CELL, _PIU], "one ProcedureAction per step"


# 2. No progression without a result ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_progression_without_a_result(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _start(conv)

    t2 = await conv.turn("ok, noted. The customer is asking for an update.", _propose(_CELL, _CELL_STEP), "Run st cell.")
    assert _decision(t2)["turn_kind"] == "other"
    assert _decision(t2)["decision"] == "rejected_pending_awaits_result", "continuity: the RU step awaits its result"
    assert t2["record"]["diagnostic_step"]["action"] == _RU_STEP and t2["record"]["diagnostic_step"]["command"] is None
    assert "st cell" not in t2["final"]
    assert _checks(t2) == [(_RU, "recommended")]
    assert [s.procedure_action_id for s in _pending(t2)] == [_RU]

    # The result arrives: RU is bound + completed, and only then is the next step accepted.
    t3 = await conv.turn("st ru output:\nRadioUnit=1 OPER=ENABLED", _propose(_CELL, _CELL_STEP), "Run st cell.")
    assert (_decision(t3)["turn_kind"], _decision(t3)["decision"]) == ("result", "new_step")
    assert _checks(t3) == [(_RU, "executed"), (_CELL, "recommended")]

    # A completed check is never proposed again (even with no pending step in between).
    t4 = await conv.turn("st cell output:\nNRCellDU=1 OPER=ENABLED", _propose(_RU, _RU_STEP), "Run st ru again.")
    assert _decision(t4)["decision"] == "rejected_repeated_step"
    assert t4["record"]["outcome"] == "insufficient_evidence" and t4["record"]["diagnostic_step"] is None
    assert "st ru" not in t4["final"]
    assert _checks(t4) == [(_RU, "executed"), (_CELL, "executed")]


# 3. Skipped / blocked step ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_blocked_step_is_recorded_skipped_then_an_alternate_path_is_accepted(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _start(conv)

    t2 = await conv.turn("I can't run that, no CLI access to the node right now", _propose(_CELL, _CELL_STEP), "Run st cell.")
    assert (_decision(t2)["turn_kind"], _decision(t2)["decision"]) == ("blocked", "new_step")
    history = t2["state"]["troubleshooting_state"]["diagnostic_history"]
    assert [(c["procedure_action_id"], c["status"]) for c in history] == [(_RU, "skipped"), (_CELL, "recommended")]
    assert history[0]["observed_result"].startswith("Not performed: I can't run that")
    progression = load_progression(t2["state"])
    ru = progression.steps_for(progression.active_fault_id)[0]
    assert ru.status is StepStatus.SKIPPED and ru.result is None, "a skip is never a result"
    assert ru.status_history[-1].reason.startswith("operator: I can't run that")
    assert [s.procedure_action_id for s in _pending(t2)] == [_CELL]


# 4. Bare follow-up continuity ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("follow_up", ["what's next cmd?", "what command?", "how do I check that?", "give me the command", "what do I run?"])
async def test_bare_follow_up_resolves_the_pending_step(isolated_km_repo, monkeypatch, follow_up) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    t1 = await _start(conv)
    check_id = t1["state"]["troubleshooting_state"]["diagnostic_history"][0]["check_id"]

    t2 = await conv.turn(follow_up, _propose(_RU, _RU_STEP), "Run st ru.")
    assert t2["tae_request"]["pending_step"]["procedure_action_id"] == _RU
    contract = t2["tae_request"]["turn_request_contract"]
    assert (contract["focus"], contract["diagnostic_objective"]) == ("continue", f"Resolve the pending diagnostic step: {_RU_STEP}")
    assert (_decision(t2)["turn_kind"], _decision(t2)["decision"]) == ("command_follow_up", "pending_step_resolved")
    assert t2["record"]["diagnostic_step"]["command"] == "st ru"
    history = t2["state"]["troubleshooting_state"]["diagnostic_history"]
    assert [(c["check_id"], c["status"]) for c in history] == [(check_id, "recommended")], "no new step, no result recorded"
    assert len(_pending(t2)) == 1


# 5. Exactly one active pending step / 6. one ProcedureAction per step -----------------------------------
def _controller(state: Optional[dict[str, Any]] = None) -> tuple[dict[str, Any], ProgressionController]:
    state = state if state is not None else {}
    thread = TroubleshootingState(fault_id="FAULT-1", symptom_summary="unit fault")
    return state, ProgressionController(state, thread, session_id="s1")


def _proposal(action: str, command: Optional[str], action_id: Optional[str]) -> dict[str, Any]:
    return {
        "outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
        "diagnostic_step": {"action": action, "reason": "r", "expected_evidence": "e", "command": command,
                            "command_source": f"{_KID}:v1:{_SEC}" if command else None, "restrictions": [], "procedure_action_id": action_id},
    }


def _accept(controller: ProgressionController, proposal: dict[str, Any]) -> ProposalDecision:
    decision, result = controller.evaluate_proposal(proposal)
    check_id = controller.check_id_for(decision, f"chk-{len(controller.thread.diagnostic_history)}")
    if decision is ProposalDecision.NEW_STEP:
        step = result["diagnostic_step"]
        controller.thread.record_recommended_check(
            action=step["action"], rationale="r", expected_observation="e", grounded_command=step["command"],
            procedure_action_id=step["procedure_action_id"], check_id=check_id,
        )
    controller.record(decision, result, check_id, selected_evidence_ids=[f"{_KID}:v1:{_SEC}"], applicability={f"{_KID}:v1:{_SEC}": "match"})
    return decision


def test_exactly_one_pending_step_and_no_step_before_its_result() -> None:
    state, controller = _controller()
    assert _accept(controller, _proposal(_RU_STEP, "st ru", _RU)) is ProposalDecision.NEW_STEP
    assert _accept(controller, _proposal(_CELL_STEP, "st cell", _CELL)) is ProposalDecision.REJECTED_PENDING_AWAITS_RESULT
    assert _accept(controller, _proposal(_RU_STEP, "st ru", _RU)) is ProposalDecision.PENDING_STEP_RESOLVED
    controller.save()
    steps = controller.progression.steps_for("FAULT-1")
    assert [(s.procedure_action_id, s.status) for s in steps] == [(_RU, StepStatus.PRESENTED)]
    assert [(c.procedure_action_id, c.status.value) for c in controller.thread.diagnostic_history] == [(_RU, "recommended")]

    # Next turn (reloaded from session state): a question is not a result, the pending step keeps waiting.
    controller = ProgressionController(state, controller.thread, session_id="s1")
    assert controller.classify_turn("why does that matter?") is TurnKind.OTHER
    controller.apply_operator_turn("why does that matter?")
    assert controller.pending_step() is not None and controller.pending_step().status is StepStatus.PRESENTED

    # The result binds to the exact pending step; only now is ONE new step accepted.
    assert controller.classify_turn("RadioUnit=1 OPER=ENABLED") is TurnKind.RESULT
    controller.apply_operator_turn("RadioUnit=1 OPER=ENABLED")
    ru = controller.progression.steps_for("FAULT-1")[0]
    assert ru.status is StepStatus.COMPLETED and ru.result.text == "RadioUnit=1 OPER=ENABLED"
    assert [c.to_status for c in ru.status_history][-3:] == [StepStatus.OBSERVED, StepStatus.VERIFIED, StepStatus.COMPLETED]
    assert controller.progression.current_phase is ProgressionPhase.REASSESS
    assert _accept(controller, _proposal(_CELL_STEP, "st cell", _CELL)) is ProposalDecision.NEW_STEP
    assert len([s for s in controller.progression.steps_for("FAULT-1") if s.status in PENDING_STATUSES]) == 1


def test_command_failure_output_marks_the_step_failed_not_completed() -> None:
    _, controller = _controller()
    _accept(controller, _proposal(_RU_STEP, "st ru", _RU))
    assert controller.classify_turn("st ru\nERROR: unknown command") is TurnKind.RESULT
    controller.apply_operator_turn("st ru\nERROR: unknown command")
    step = controller.progression.steps_for("FAULT-1")[0]
    assert step.status is StepStatus.FAILED and step.result is not None
    assert controller.progression.current_phase is ProgressionPhase.REASSESS


@pytest.mark.parametrize("command", ["st pluginunit; st fieldr", "st pluginunit && st fieldr", "st pluginunit | grep x", "st pluginunit\nst fieldr"])
def test_several_actions_in_one_step_are_rejected(command: str) -> None:
    assert is_multi_action_command(command)
    _, controller = _controller()
    decision, result = controller.evaluate_proposal(_proposal("Check the units", command, _CHAIN))
    assert decision is ProposalDecision.REJECTED_MULTIPLE_ACTIONS
    assert result["outcome"] == "insufficient_evidence" and result["diagnostic_step"] is None
    assert controller.check_id_for(decision, "chk-x") is None
    assert controller.record(decision, result, None, selected_evidence_ids=[], applicability={}) is None
    assert controller.progression.steps_for("FAULT-1") == []


@pytest.mark.asyncio
async def test_combined_governed_command_is_not_presented_as_one_step(isolated_km_repo, monkeypatch) -> None:
    """The combined line is in the governed section, yet: it is never extracted as a ProcedureAction,
    Command Authority refuses the model's free-form copy of it, and the controller independently
    refuses the step: nothing is recorded, planned or shown."""
    import json

    from google.genai import types

    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    legacy = [types.Part.from_text(text=json.dumps({
        "outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
        "diagnostic_step": {"action": "Check the plug-in and field units", "reason": "r", "expected_evidence": "e",
                            "command": "st pluginunit; st fieldr", "command_source": f"{_KID}:v1:{_SEC}", "restrictions": []},
    }))]
    t1 = await conv.turn(
        "Ericsson 5G: radio unit fault alarm on the node. What should I check?",
        [_search("radio unit fault"), _select(_KID, _SEC), _CATALOG, legacy], "Run st pluginunit; st fieldr.", {"subject_component": "radio unit"},
    )
    catalog = t1["trace"]["action_catalogs"][0]
    assert "st pluginunit; st fieldr" not in [a["command_template"] for a in catalog["actions"]]
    assert any(c["command"] == "st pluginunit; st fieldr" and c["decision"] == "rejected" and c["reason"] == "not a single command invocation: command_separator"
               for c in t1["trace"]["command_authority"])
    assert _decision(t1)["decision"] == "rejected_multiple_actions", "the controller rejects the step independently"
    assert t1["record"]["diagnostic_step"] is None and t1["record"]["outcome"] == "insufficient_evidence"
    assert "operational_control" not in t1["record"]
    assert "st pluginunit; st fieldr" not in t1["final"]
    assert _checks(t1) == [] and _pending(t1) == []
    assert set(t1["state"][THREADS_STATE_KEY]) == {t1["state"]["troubleshooting_state"]["fault_id"]}
