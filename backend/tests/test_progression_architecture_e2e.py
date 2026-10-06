"""Full-architecture regression of the live failure sequence (real ChatService + real
TechnicalAuthorityAgentTool + real approval / execution services + controlled read execution
through a TEST adapter; scripted LLMs):

    alarm -> vendor/technology -> RU check -> RU result -> Cell check -> Cell result
    -> dependency (plug-in unit) check -> "what is next cmd?" (model drifts: alt / RU again / cmd1; cmd2)
    -> plug-in unit result -> remediation candidate -> target confirmation -> HITL -> READY_FOR_EXECUTION
    -> execution refused (state changes are never executed) -> simulated controlled execution
    -> post-action verification (controlled read) -> RESOLVED only when the criteria are met

The TAE proposes; the server progresses. Every model drift is scripted to prove it is contained.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.progression_controller import ProgressionController
from backend.agents.technical_authority_engineer.troubleshooting_threads import load_active_thread
from backend.api import approval_service, execution_service, operational_execution_service
from backend.approval.service import load_active_proposal
from backend.cases.troubleshooting_progression import (
    HypothesisState,
    ProgressionPhase,
    RemediationState,
    ResolutionState,
    ResultSource,
    StepPurpose,
    StepStatus,
    load_progression,
)
from backend.gateway.safe_error import SafeErrorException
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.operations import control_plane as cp
from backend.operations.signing import text_hash
from backend.operations.execution import AdapterOutput, AuthorizedReadAction, ExecutionAdapter, ExecutionContext, get_execution_adapter_registry
from backend.tests.test_ess_service_unavailable_e2e_verification import _fc, isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _CATALOG, _Conversation, _search

_KID, _VER, _SEC = "KID-RADIO-DEPENDENCY", "v1", "sec-0000"
_SOURCE = f"{_KID}:{_VER}:{_SEC}"
_CONTENT = (
    "Alarm validation: `alt`\n"
    "Radio unit state check: `st ru`\n"
    "Cell state check: `st cell`\n"
    "Before any recovery, the plug-in unit state must be checked: `st pluginunit`\n"
    "Plug-in unit recovery: `acc PlugInUnit=xxxx restart`\n"
    "After the recovery, verify with `st pluginunit` that the plug-in unit reports OPER=ENABLED.\n"
    "Combined unit check: `st pluginunit; st fieldr`\n"
)
_IDS = {a.command_template: a.action_id for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label=_VER, section_id=_SEC, content=_CONTENT)[0]}
_ALT, _RU, _CELL, _PIU, _RESTART = (_IDS[t] for t in ("alt", "st ru", "st cell", "st pluginunit", "acc PlugInUnit=xxxx restart"))
assert "st pluginunit; st fieldr" not in _IDS, "a combined governed line is never a ProcedureAction"
_FACTS = {"known_applicability_facts": {"vendor": ["Ericsson"], "technology": ["5G"]}}
_HYPOTHESIS = "The cell is disabled because its plug-in unit dependency failed"
_CRITERIA = [
    {"kind": "original_condition", "description": "PlugInUnit=1 no longer reports OPER=DISABLED",
     "check_procedure_action_id": _PIU, "scope": "PlugInUnit=1", "must_exclude": ["OPER=DISABLED"]},
    {"kind": "operational_state", "description": "PlugInUnit=1 reports OPER=ENABLED",
     "check_procedure_action_id": _PIU, "scope": "PlugInUnit=1", "must_include": ["OPER=ENABLED"]},
]
_RU_OUT = "st ru\nRadioUnit=1 OPER=ENABLED AVAIL="
_CELL_OUT = "st cell\nNRCellDU=1 OPER=DISABLED AVAIL=DEPENDENCY_FAILED"
_PIU_OUT = "st pluginunit\nPlugInUnit=1 OPER=DISABLED AVAIL=FAILED"


def _mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID, document_type=KnowledgeDocumentType.MOP, title="Radio Dependency Fault Procedure",
        version=KnowledgeVersion(label=_VER, effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={"vendor": ["ericsson"], "technology": ["5g"]}),
        source=KnowledgeSource(source_system="test", source_id="MOP_RadioDependency.docx", display_name="Radio Dependency MOP"),
        sections=[KnowledgeSection(section_id=_SEC, knowledge_id=_KID, heading=None, sequence=0, content=_CONTENT, source_locator="lines:1-7")],
    )


def _tae(outcome: str = "recommended", action_id: Optional[str] = None, action: str = "", **extra: Any) -> list[types.Part]:
    payload: dict[str, Any] = {"outcome": outcome, "technical_interpretation": "Interpretation.", "missing_information": [],
                               "hypothesis_updates": extra.pop("hypothesis_updates", [])}
    if outcome == "recommended":
        payload["diagnostic_step"] = {"action": action, "reason": "Governed step.", "expected_evidence": "Command output", "command": extra.pop("command", None),
                                      "command_source": None, "restrictions": [], "procedure_action_id": action_id, **extra}
    return [types.Part.from_text(text=json.dumps(payload))]


def _governed(*tail: list[types.Part]) -> list[list[types.Part]]:
    return [_search("radio unit fault dependency"), _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": _KID, "version_label": _VER, "section_id": _SEC}]}),
            _CATALOG, *tail]


def _event(turn: dict[str, Any]) -> dict[str, Any]:
    return [e for e in turn["trace"]["operational_events"] if e.get("stage") == "progression"][-1]


def _steps(state: dict[str, Any]) -> list:
    progression = load_progression(state)
    return progression.steps_for(progression.active_fault_id)


def _pending(state: dict[str, Any]) -> list:
    return [s for s in _steps(state) if s.status in (StepStatus.PROPOSED, StepStatus.VALIDATED, StepStatus.PRESENTED)]


class _ScriptedReadAdapter(ExecutionAdapter):
    adapter_type = "test-scripted-read"

    def __init__(self) -> None:
        self.output = ""
        self.calls: list[str] = []

    async def _execute_read(self, action: AuthorizedReadAction, context: ExecutionContext) -> AdapterOutput:
        self.calls.append(action.command)
        return AdapterOutput(stdout=self.output)


@pytest.fixture
def read_adapter(monkeypatch):
    registry = get_execution_adapter_registry()
    adapter = _ScriptedReadAdapter()
    registry.register("test-sandbox", adapter)
    monkeypatch.setenv("SLOPANOC_DIAGNOSTIC_EXECUTION_CONTEXT", "test-sandbox")
    yield adapter
    registry.unregister("test-sandbox")


async def _simulate_controlled_state_change_execution(conv: _Conversation, output: str) -> dict[str, Any]:
    """TEST-ONLY stand-in for a controlled state-change executor. None exists in production
    (`execute` refuses with state_change_execution_not_enabled). It records the execution exactly as
    the controlled READ path does: execution on the control record + adapter observation on the
    check -> server progression sync (which validates the binding)."""
    session = await conv.sessions.get_session(conv.session_id, "eng")
    state = session.state
    thread = load_active_thread(state)
    check = next(c for c in thread.diagnostic_history if c.procedure_action_id == _RESTART)
    control = state[cp.OPERATIONAL_CONTROLS_STATE_KEY][check.control_id]
    control["executions"] = [*control.get("executions", []), {"execution_id": "sim-exec-1", "check_id": check.check_id, "status": "succeeded",
                                                             "output_hash": text_hash(output)}]
    controller = ProgressionController(state, thread, activate=False)
    assert controller.record_controlled_execution(check.check_id, "sim-exec-1", output, True, check.control_id, "executed")
    controller.save()
    await conv.sessions.persist_state_delta(session, {k: state[k] for k in cp.PERSISTED_STATE_KEYS if k in state})
    return dict((await conv.sessions.get_session(conv.session_id, "eng")).state)


@pytest.mark.asyncio
@pytest.mark.parametrize(("verification_output", "resolved"), [("PlugInUnit=1 OPER=ENABLED AVAIL=", True), ("PlugInUnit=1 OPER=DISABLED AVAIL=FAILED", False)])
async def test_live_failure_sequence_through_verified_resolution(isolated_km_repo, monkeypatch, read_adapter, verification_output, resolved) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)

    # Alarm received: applicability UNKNOWN until vendor/technology are supplied (never assumed).
    t1 = await conv.turn("ALARM: Radio unit fault on node N1. What should I check?", [_search("radio unit fault"), _tae(outcome="insufficient_evidence")],
                         "Which vendor and technology is this?", {"subject_component": "radio unit"})
    assert "To validate the applicable governed procedure I still need" in t1["final"]

    # Vendor/technology supplied -> RU status check: ONE selected governed action.
    t2 = await conv.turn("Ericsson 5G", _governed(_tae(action_id=_RU, action="Check the radio unit state")), "Run st ru.",
                         {"vendor": "Ericsson", "technology": "5G", "continues_active_objective": True}, _FACTS)
    assert t2["record"]["diagnostic_step"]["command"] == "st ru" and _event(t2)["decision"] == "new_step"
    assert [s["status"] for s in t2["trace"]["selections"]] == ["accepted"]
    assert [r["selection_state"] for r in t2["trace"]["searches"][0]["results"]] == ["SELECTED"], "AVAILABLE != SELECTED: only the explicit selection"
    fault_id = t2["state"]["troubleshooting_state"]["fault_id"]

    # RU result binds to the RU step; the next step (Cell) derives from the updated state.
    t3 = await conv.turn(_RU_OUT, _governed(_tae(action_id=_CELL, action="Check the cell state")), "Run st cell.")
    assert (_event(t3)["turn_kind"], _event(t3)["decision"]) == ("result", "new_step")
    assert t3["tae_request"].get("pending_step") is None, "the RU step was closed by its result before the specialist ran"
    assert t3["record"]["diagnostic_step"]["command"] == "st cell"

    # Cell result -> dependency fault identified -> hardware/dependency check proposed (plug-in unit).
    t4 = await conv.turn(_CELL_OUT, _governed(_tae(action_id=_PIU, action="Check the plug-in unit the cell depends on",
                                                   hypothesis_updates=[{"statement": _HYPOTHESIS, "proposed_state": "supported"}])), "Run st pluginunit.")
    assert t4["record"]["diagnostic_step"]["command"] == "st pluginunit" and _event(t4)["decision"] == "new_step"
    progression = load_progression(t4["state"])
    assert [(h.statement, h.state) for h in progression.hypotheses] == [(_HYPOTHESIS, HypothesisState.SUPPORTED)]
    cell_step = _steps(t4["state"])[1]
    assert progression.hypotheses[0].transitions[-1].evidence_ids == [cell_step.result.result_id], "supported by the bound cell output"
    piu_check = t4["state"]["troubleshooting_state"]["diagnostic_history"][-1]["check_id"]

    # "what is next cmd?" x3 with a drifting specialist: alt, RU again, cmd1; cmd2 -- all contained. The
    # drift is never accepted or presented; acquisition continuity answers the command request with the
    # PENDING step's own governed action, re-derived from this run's selected evidence and re-authorized.
    for drift, label in ((_tae(action_id=_ALT, action="Validate the alarms"), "alt"),
                         (_tae(action_id=_RU, action="Check the radio unit state"), "st ru"),
                         (_tae(action_id=None, action="Check the units", command="st pluginunit; st fieldr",
                               command_source=_SOURCE), "st pluginunit; st fieldr")):
        t = await conv.turn("what is next cmd?", _governed(drift), f"Run {label}.")
        request = t["tae_request"]
        assert request["pending_step"]["procedure_action_id"] == _PIU and request["pending_step"]["turn_kind"] == "command_follow_up"
        assert request["turn_request_contract"]["diagnostic_objective"] == "Resolve the pending diagnostic step: Check the plug-in unit the cell depends on"
        assert (_event(t)["decision"], t["record"]["diagnostic_step"]["command"]) == ("pending_step_resolved", "st pluginunit")
        (continuity,) = [e for e in t["trace"]["operational_events"] if e.get("stage") == "acquisition_continuity"]
        assert continuity["outcome"] == "server_reconciled" and continuity["authority_decision"] == "authorized"
        assert label not in t["final"].replace("`st pluginunit`", "")
        assert [s.procedure_action_id for s in _pending(t["state"])] == [_PIU], "no loss of the pending diagnostic objective"
        assert not any(c["command"] in ("alt", "st ru", "st pluginunit; st fieldr") and c["decision"] == "authorized"
                       for c in t["trace"]["command_authority"]), "a drifted command is never authorized"

    t5 = await conv.turn("what is next cmd?", _governed(_tae(action_id=_PIU, action="Check the plug-in unit the cell depends on")), "Run st pluginunit.")
    assert (_event(t5)["decision"], t5["record"]["diagnostic_step"]["command"]) == ("pending_step_resolved", "st pluginunit")
    assert [(s.procedure_action_id, s.status) for s in _steps(t5["state"])] == [
        (_RU, StepStatus.COMPLETED), (_CELL, StepStatus.COMPLETED), (_PIU, StepStatus.PRESENTED)
    ], "no alt, no repeated RU, one action per step"

    # Plug-in unit result binds to THAT step; the dependency is isolated -> remediation candidate.
    t6 = await conv.turn(_PIU_OUT, _governed(_tae(action_id=_RESTART, action="Recover the plug-in unit", tests_hypothesis=_HYPOTHESIS,
                                                  parameter_values=[{"name": "PlugInUnit", "value": "1"}], verification_criteria=_CRITERIA)),
                         "Run acc PlugInUnit=1 restart.")
    piu_step = _steps(t6["state"])[2]
    assert (piu_step.check_id, piu_step.result.text, piu_step.result.source) == (piu_check, _PIU_OUT, ResultSource.OPERATOR_MESSAGE)
    event = _event(t6)
    assert event["decision"] == "new_step" and all(v["met"] for v in event["remediation_gate"].values())
    # The prerequisite came from the STRUCTURED action graph (extraction-time, with provenance).
    assert event["remediation_gate"]["prerequisites"]["relationships"] == [
        {"action_id": _PIU, "type": "mandatory_prerequisite", "enforced": True, "rule": "explicit mandatory dependency language",
         "line": 4, "source_locator": "lines:1-7"}
    ]
    assert (event["lifecycle_stage"], event["remediation"]["state"], [c["status"] for c in event["remediation"]["criteria"]]) == (
        "remediation", "candidate", ["pending", "pending"]
    )
    assert t6["tae_request"]["investigation_state"]["hypotheses"] == [{"statement": _HYPOTHESIS, "state": "supported"}]
    # ProcedureAction != authorization; parameter binding != target confirmation; authority != policy != approval.
    resolution = t6["record"]["procedure_action_resolution"]
    assert (resolution["status"], resolution["rendered_command"]) == ("resolved", "acc PlugInUnit=1 restart")
    binding = resolution["parameters"][0]
    assert (binding["name"], binding["state"], binding["value"]) == ("PlugInUnit", "verified", "1")
    # Bound only from the current-case target fact observed in the validated plug-in unit output.
    assert binding["detail"].startswith("current-case target fact"), "trusted case evidence only"
    (target,) = resolution["target_validation"]["targets"]
    assert (target["key"], target["value"], target["status"]) == ("PlugInUnit", "1", "validated")
    assert target["provenance"][0]["source"] == "operator_message"
    assert t6["record"]["diagnostic_step"]["command"] is None
    assert any(c["decision"] == "preview_authorized_if_target_confirmed" for c in t6["trace"]["command_authority"])
    assert not any(c["decision"] == "authorized" and "restart" in c["command"] for c in t6["trace"]["command_authority"])
    control = t6["record"]["operational_control"]
    assert (control["control_stage"], control["policy_decision"], control["reason_codes"]) == (
        "awaiting_confirmation", "clarification_required", ["STATE_CHANGE", "TARGET_NOT_CONFIRMED"]
    )
    assert "acc PlugInUnit=1 restart" not in t6["final"]

    # Target confirmation -> HITL approval -> READY_FOR_EXECUTION -> execution fails safely.
    confirm = load_active_proposal(t6["state"])
    approval = (await approval_service.approve(conv.sessions, conv.session_id, confirm.proposal_id, "eng")).pending_action
    assert (approval.operational.kind, approval.operational.command) == ("approval", "acc PlugInUnit=1 restart")
    approved = await approval_service.approve(conv.sessions, conv.session_id, approval.proposal_id, "eng")
    assert approved.pending_action.operational.control_stage == "ready_for_execution"
    with pytest.raises(SafeErrorException) as refused:
        await execution_service.execute(conv.sessions, conv.session_id, approval.proposal_id, "eng")
    assert refused.value.safe_error.reason == "state_change_execution_not_enabled"
    state = dict((await conv.sessions.get_session(conv.session_id, "eng")).state)
    fault = load_progression(state).faults[fault_id]
    assert (fault.active_remediation.state, fault.resolution) == (RemediationState.CANDIDATE, ResolutionState.UNRESOLVED), "approval != execution"
    assert read_adapter.calls == []

    # Simulated controlled execution succeeds: ACTION_EXECUTED -> POST_ACTION_VERIFICATION_REQUIRED, NOT resolved
    # (even though the action's own output shows the expected state).
    state = await _simulate_controlled_state_change_execution(conv, "acc PlugInUnit=1 restart\nPlugInUnit=1 OPER=ENABLED")
    fault = load_progression(state).faults[fault_id]
    assert (fault.phase, fault.resolution, fault.active_remediation.state) == (
        ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED, ResolutionState.UNRESOLVED, RemediationState.VERIFICATION_REQUIRED
    )
    assert state["troubleshooting_state"]["status"] != "resolved"

    # Post-action verification: the governed re-check (recorded reason) through the controlled read path.
    t7 = await conv.turn("what next?", _governed(_tae(action_id=_PIU, action="Verify the plug-in unit after recovery")), "Run st pluginunit.")
    event = _event(t7)
    assert (event["decision"], event["recheck"]["reason"]) == ("recheck_permitted", "governed_post_action_verification")
    verify = _steps(t7["state"])[-1]
    assert (verify.purpose, verify.recheck_of) == (StepPurpose.VERIFICATION, piu_step.step_id)
    control = t7["record"]["operational_control"]
    assert (control["control_stage"], control["policy_decision"]) == ("ready_for_execution", "allowed")
    read_adapter.output = verification_output
    execution = await operational_execution_service.execute_read(conv.sessions, conv.session_id, control["check_id"], "eng")
    assert execution.execution.status == "succeeded" and read_adapter.calls == ["st pluginunit"]

    state = dict((await conv.sessions.get_session(conv.session_id, "eng")).state)
    progression = load_progression(state)
    fault = progression.faults[fault_id]
    verify = progression.step(verify.step_id)
    assert verify.result.source is ResultSource.EXECUTION_ADAPTER and verify.status is StepStatus.COMPLETED
    hypothesis = progression.hypotheses[0]
    if resolved:
        assert (fault.phase, fault.resolution, fault.active_remediation.state) == (ProgressionPhase.RESOLVED, ResolutionState.RESOLVED, RemediationState.VERIFIED)
        assert state["troubleshooting_state"]["status"] == "resolved"
        assert hypothesis.state is HypothesisState.CONFIRMED and hypothesis.transitions[-1].evidence_ids == [verify.result.result_id]
        # Resolved faults do not progress on a model proposal; only the operator reopens them.
        t8 = await conv.turn("anything else?", _governed(_tae(action_id=_CELL, action="Check the cell state")), "Run st cell.")
        assert _event(t8)["decision"] == "rejected_fault_closed" and "st cell" not in t8["final"]
        t9 = await conv.turn("the alarm came back", _governed(_tae(action_id=_CELL, action="Check the cell state")), "Run st cell.")
        assert [e["event"] for e in _event(t9)["events"]][:1] == ["fault_reopened"]
        assert (_event(t9)["decision"], _event(t9)["recheck"]["reason"]) == ("recheck_permitted", "result_stale_after_reopen")
    else:
        assert (fault.phase, fault.resolution, fault.active_remediation.state) == (
            ProgressionPhase.REASSESS, ResolutionState.UNRESOLVED, RemediationState.VERIFICATION_FAILED
        )
        assert state["troubleshooting_state"]["status"] != "resolved"
        assert hypothesis.state is HypothesisState.SUPPORTED, "interpreting the failure is the agent's call, not the server's"
