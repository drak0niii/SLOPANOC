"""Diagnosis -> Remediation -> Verified Resolution (server-owned progression gates).

    DIAGNOSIS -> REMEDIATION_CANDIDATE -> REMEDIATION -> ACTION_EXECUTED
              -> POST_ACTION_VERIFICATION_REQUIRED -> RESOLVED / REASSESS / ESCALATION_REQUIRED

Unit cases drive the controller with REAL ProcedureAction resolutions; the end-to-end case drives
the real ChatService + TechnicalAuthorityAgentTool + approval/execution services (scripted LLMs).
Command Authority, policy, target confirmation and HITL are exercised unchanged.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.cases.target_facts import case_target_facts
from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.progression_controller import ProgressionController, ProposalDecision, TurnKind
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.api import approval_service, execution_service
from backend.approval.service import load_active_proposal
from backend.cases.troubleshooting_progression import (
    CriterionStatus,
    HypothesisState,
    ProgressionPhase,
    RemediationState,
    ResolutionState,
    StepPurpose,
    StepStatus,
    fault_stage,
    load_progression,
)
from backend.cases.troubleshooting_state import TroubleshootingState, TroubleshootingStatus
from backend.gateway.safe_error import SafeErrorException
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_ess_service_unavailable_e2e_verification import _fc, isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _CATALOG, _Conversation, _search

D = ProposalDecision
_KID, _VER, _SEC = "KID-UNIT-RECOVERY", "v1", "sec-unit"
_SOURCE = f"{_KID}:{_VER}:{_SEC}"
_CONTENT = (
    "Unit state check: `hget Unit=<unit>`\n"
    "Before any recovery, the unit links must be checked: `lget Link=<unit>`\n"
    "Unit recovery: `acc Unit=<unit> restart`\n"
    "After the recovery the unit must report operationalState=ENABLED.\n"
)
_HGET, _LGET, _RESTART = "hget Unit=<unit>", "lget Link=<unit>", "acc Unit=<unit> restart"
_IDS = {a.command_template: a.action_id for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label=_VER, section_id=_SEC, content=_CONTENT)[0]}
_DISABLED = "hget Unit=4\nUnit=4 operationalState=DISABLED availabilityStatus=FAILED"
_ENABLED = "hget Unit=4\nUnit=4 operationalState=ENABLED"
_CRITERIA = [
    {"kind": "original_condition", "description": "Unit 4 no longer reports operationalState=DISABLED",
     "check_procedure_action_id": _IDS[_HGET], "scope": "Unit=4", "must_exclude": ["operationalState=DISABLED"]},
    {"kind": "operational_state", "description": "Unit 4 reports operationalState=ENABLED",
     "check_procedure_action_id": _IDS[_HGET], "scope": "Unit=4", "must_include": ["operationalState=ENABLED"]},
]
_HYPOTHESIS = "Unit 4 is in a failed operational state that a restart clears"


def _ev(applicability: str = "match") -> EvidenceReference:
    return EvidenceReference(
        source_id=_SOURCE, source_type="governed_knowledge", title="Unit Recovery", content_snippet=_CONTENT,
        metadata={"knowledge_id": _KID, "version_label": _VER, "section_id": _SEC, "source_locator": "lines:1-4", "title": "Unit Recovery",
                  "heading": None, "lifecycle_status": "approved", "applicability_outcome": applicability},
    )


class _Flow:
    """One fault thread; each operator turn rebuilds the controller from session state (like the TAE tool)."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.thread = TroubleshootingState(fault_id="FAULT-U4", symptom_summary="unit 4 alarm")
        self.c: ProgressionController

    def operator(self, text: str) -> TurnKind:
        self.c = ProgressionController(self.state, self.thread, session_id="s1")
        kind = self.c.classify_turn(text)
        self.c.apply_operator_turn(text)
        self.c.save()
        return kind

    def propose(self, template: str, value: Optional[str] = "4", *, ev: Optional[EvidenceReference] = None, **step: Any) -> tuple[D, dict[str, Any]]:
        ev = ev or _ev()
        resolution, _ = pa.resolve_procedure_action(
            _IDS[template], issued_ids={_IDS[template]}, selected_evidence=[ev],
            proposals=[{"name": "unit", "value": value}] if value else [], operator_text=f"the unit is {value}" if value else "",
            # As in production: state-change targets only from THIS fault's trusted results.
            target_facts=case_target_facts(self.c.progression, self.thread.fault_id), fault_id=self.thread.fault_id,
        )
        command = resolution.candidate.command if resolution.candidate else None
        proposal = {
            "outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
            "diagnostic_step": {"action": step.pop("action", f"Perform {template}"), "reason": step.pop("reason", "r"), "expected_evidence": "Output",
                                "command": command, "command_source": ev.source_id if command else None, "restrictions": [],
                                "procedure_action_id": _IDS[template], **step},
        }
        decision, result = self.c.evaluate_proposal(proposal, resolution, [ev])
        check_id = self.c.check_id_for(decision, f"chk-{len(self.thread.diagnostic_history)}")
        if decision in (D.NEW_STEP, D.RECHECK_PERMITTED):
            s = result["diagnostic_step"]
            self.thread.record_recommended_check(action=s["action"], rationale="r", expected_observation="e", grounded_command=s["command"],
                                                 procedure_action_id=s["procedure_action_id"], check_id=check_id)
        self.c.record(decision, result, check_id, selected_evidence_ids=[ev.source_id], applicability={ev.source_id: "match"})
        self.c.save()
        return decision, result

    @property
    def fault(self):
        return self.c.progression.faults["FAULT-U4"]

    @property
    def remediation(self):
        return self.fault.active_remediation

    def gate_unmet(self) -> set[str]:
        return {k for k, v in (self.c._gate or {}).items() if not v["met"]}


def _diagnosed(flow: _Flow, *, prerequisite: bool = True) -> None:
    flow.operator("unit 4 is down")
    assert flow.propose(_HGET, action="Check the unit state")[0] is D.NEW_STEP
    assert flow.operator(_DISABLED) is TurnKind.RESULT
    if prerequisite:
        assert flow.propose(_LGET, action="Check the unit links")[0] is D.NEW_STEP
        assert flow.operator("lget Link=4\nLink=4 state=UP") is TurnKind.RESULT


def _remediated(flow: _Flow, criteria: Optional[list[dict[str, Any]]] = None, hypothesis: Optional[str] = _HYPOTHESIS) -> None:
    _diagnosed(flow)
    decision, _ = flow.propose(_RESTART, action="Recover the unit", verification_criteria=_CRITERIA if criteria is None else criteria,
                               **({"tests_hypothesis": hypothesis} if hypothesis else {}))
    assert decision is D.NEW_STEP


def _executed(flow: _Flow, output: str = "acc Unit=4 restart\nUnit=4 restart ordered\nUnit=4 operationalState=ENABLED") -> None:
    assert flow.operator(output) is TurnKind.RESULT


# 1. premature remediation ---------------------------------------------------------------------------------
def test_remediation_with_no_diagnosis_is_premature_even_when_the_model_claims_root_cause() -> None:
    flow = _Flow()
    flow.operator("unit 4 is down, restart it")
    decision, result = flow.propose(_RESTART, action="Recover the unit", reason="This is definitely the root cause; restart now.",
                                    tests_hypothesis=_HYPOTHESIS, verification_criteria=_CRITERIA)
    assert decision is D.REJECTED_PREMATURE_REMEDIATION
    # Nothing observed yet: no diagnosis, no prerequisite, and no target proven by trusted evidence.
    assert flow.gate_unmet() == {"diagnostic_evidence", "prerequisites", "target_isolated"}
    assert result["outcome"] == "insufficient_evidence" and result["diagnostic_step"] is None
    assert any("Remediation is not yet justified" in m and "diagnostic_evidence" in m for m in result["missing_information"])
    assert flow.c.progression.steps == [] and flow.fault.remediations == []
    assert flow.fault.phase is not ProgressionPhase.REMEDIATION_CANDIDATE and fault_stage(flow.fault) == "diagnosis"


def test_remediation_before_declared_prerequisite_is_premature() -> None:
    flow = _Flow()
    _diagnosed(flow, prerequisite=False)
    assert flow.propose(_RESTART, action="Recover the unit")[0] is D.REJECTED_PREMATURE_REMEDIATION
    assert flow.gate_unmet() == {"prerequisites"}
    assert "lget Link=<unit>" in flow.c._gate["prerequisites"]["detail"]


def test_remediation_without_an_isolated_target_is_premature() -> None:
    flow = _Flow()
    flow.operator("unit 4 is down")
    assert flow.propose(_HGET, action="Check the unit state")[0] is D.NEW_STEP
    # The trusted output shows TWO units: with none requested, the server never picks one.
    assert flow.operator("hget Unit=4\nUnit=4 operationalState=DISABLED\nUnit=7 operationalState=DISABLED") is TurnKind.RESULT
    assert flow.propose(_LGET, action="Check the unit links")[0] is D.NEW_STEP
    assert flow.operator("lget Link=4\nLink=4 state=UP") is TurnKind.RESULT
    assert flow.propose(_RESTART, None, action="Recover the unit")[0] is D.REJECTED_PREMATURE_REMEDIATION
    assert flow.gate_unmet() == {"target_isolated"}
    assert "ambiguous" in flow.c._gate["target_isolated"]["detail"]


def test_remediation_from_a_non_matching_procedure_is_premature() -> None:
    flow = _Flow()
    _diagnosed(flow)
    assert flow.propose(_RESTART, action="Recover the unit", ev=_ev("unknown"))[0] is D.REJECTED_PREMATURE_REMEDIATION
    assert "governed_remediation" in flow.gate_unmet()


# 2. remediation accepted --------------------------------------------------------------------------------------
def test_remediation_is_accepted_when_server_state_establishes_every_gate_condition() -> None:
    flow = _Flow()
    _remediated(flow)
    assert all(v["met"] for v in flow.c._gate.values()) and set(flow.c._gate) == {"diagnostic_evidence", "target_isolated", "governed_remediation", "prerequisites"}
    step = flow.c.progression.steps_for("FAULT-U4")[-1]
    assert (step.purpose, step.status, step.command) == (StepPurpose.REMEDIATION, StepStatus.PRESENTED, "acc Unit=4 restart")
    assert flow.fault.phase is ProgressionPhase.REMEDIATION and fault_stage(flow.fault) == "remediation"
    assert "remediation_candidate" in [e.details.get("phase") for e in flow.c.progression.events if e.event == "phase_changed"]
    record = flow.remediation
    assert (record.state, record.step_id, record.target, record.source_id) == (RemediationState.CANDIDATE, step.step_id, "unit=4", _SOURCE)
    assert [c.kind.value for c in record.criteria] == ["original_condition", "operational_state"] and record.rejected_criteria == []
    assert record.criteria[0].grounding[0].startswith("baseline:obs-") and record.criteria[1].grounding == [f"governed:{_SOURCE}"]
    assert flow.fault.resolution is ResolutionState.UNRESOLVED


def test_ungrounded_verification_criteria_are_discarded() -> None:
    flow = _Flow()
    _remediated(flow, criteria=[
        {"kind": "original_condition", "description": "x", "scope": "Unit=4", "must_exclude": ["alarmSeverity=CRITICAL"]},
        {"kind": "operational_state", "description": "y", "must_include": ["serviceState=IN_SERVICE"]},
        {"kind": "expected_evidence", "description": "z", "check_procedure_action_id": _IDS[_RESTART], "must_include": ["operationalState=ENABLED"]},
        {"kind": "expected_evidence", "description": "w", "check_procedure_action_id": "pa-made-up", "must_include": ["operationalState=ENABLED"]},
        {"kind": "dependent_condition", "description": "v"},
    ])
    assert flow.remediation.criteria == []
    assert [r["reason"] for r in flow.remediation.rejected_criteria] == [
        "'alarmSeverity=CRITICAL' was not observed before the action",
        "'serviceState=IN_SERVICE' is not stated by the selected governed procedure",
        "verification check must be a read-only diagnostic",
        "check is not a governed action of the selected evidence",
        "no observable expectation",
    ]


# 3. command success != resolution / missing verification ---------------------------------------------------
def test_successful_command_does_not_mark_the_incident_resolved() -> None:
    flow = _Flow()
    _remediated(flow)
    _executed(flow)  # the action's own output even contains the expected state
    remediation_step = flow.c.progression.step(flow.remediation.step_id)
    assert remediation_step.status is StepStatus.COMPLETED
    assert flow.remediation.state is RemediationState.VERIFICATION_REQUIRED and flow.remediation.criteria_frozen
    assert flow.fault.phase is ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED and fault_stage(flow.fault) == "post_action_verification"
    assert [e.details["phase"] for e in flow.c.progression.events if e.event == "phase_changed"][-2:] == ["action_executed", "post_action_verification_required"]
    assert flow.fault.resolution is ResolutionState.UNRESOLVED and flow.thread.status is not TroubleshootingStatus.RESOLVED
    assert all(c.status is CriterionStatus.PENDING for c in flow.remediation.criteria), "the remediation output never verifies itself"


@pytest.mark.parametrize("report", ["done", "restart executed on unit 4", "I have performed it"])
def test_operator_execution_report_counts_as_the_remediation_result(report: str) -> None:
    flow = _Flow()
    _remediated(flow)
    assert flow.operator(report) is TurnKind.RESULT
    assert flow.remediation.state is RemediationState.VERIFICATION_REQUIRED


def test_missing_verification_keeps_the_fault_unresolved() -> None:
    flow = _Flow()
    _remediated(flow)
    _executed(flow)
    flow.operator("looks fixed to me, the alarm is gone")  # no verification step, no bound observation
    assert flow.fault.phase is ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED and flow.fault.resolution is ResolutionState.UNRESOLVED

    # A verification observation that does not show the scoped target decides nothing.
    assert flow.propose(_HGET, action="Verify the unit state")[0] is D.RECHECK_PERMITTED
    flow.operator("hget Unit=4\nUnit=7 operationalState=ENABLED")
    assert {c.status for c in flow.remediation.criteria} == {CriterionStatus.PENDING}
    assert flow.fault.phase is ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED and flow.fault.resolution is ResolutionState.UNRESOLVED


def test_remediation_without_criteria_can_never_be_auto_resolved() -> None:
    flow = _Flow()
    _remediated(flow, criteria=[])
    _executed(flow)
    assert any("No explicit verification criteria" in q.text for q in flow.c.progression.open_questions)
    flow.propose(_HGET, action="Verify the unit state")
    flow.operator(_ENABLED)
    assert flow.fault.phase is ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED and flow.fault.resolution is ResolutionState.UNRESOLVED


def test_new_remediation_waits_for_post_action_verification() -> None:
    flow = _Flow()
    _remediated(flow)
    _executed(flow)
    flow.operator("please retry the restart")
    assert flow.propose(_RESTART, action="Recover the unit again")[0] is D.REJECTED_PREMATURE_REMEDIATION
    assert "awaits post-action verification" in flow.c._gate["diagnostic_evidence"]["detail"]


# 4. verification outcomes ------------------------------------------------------------------------------------
def test_post_action_verification_success_resolves_and_confirms_the_hypothesis() -> None:
    flow = _Flow()
    _remediated(flow)
    _executed(flow)
    flow.operator("")
    decision, _ = flow.propose(_HGET, action="Verify the unit state")
    assert decision is D.RECHECK_PERMITTED
    verify = flow.c.progression.steps_for("FAULT-U4")[-1]
    assert verify.purpose is StepPurpose.VERIFICATION and flow.fault.phase is ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED
    flow.operator(_ENABLED)
    verify = flow.c.progression.step(verify.step_id)
    assert [c.status for c in flow.remediation.criteria] == [CriterionStatus.MET, CriterionStatus.MET]
    assert flow.remediation.criteria[0].evidence_ids == [verify.result.result_id]
    assert flow.remediation.state is RemediationState.VERIFIED
    assert (flow.fault.phase, flow.fault.resolution) == (ProgressionPhase.RESOLVED, ResolutionState.RESOLVED)
    assert flow.thread.status is TroubleshootingStatus.RESOLVED
    hypothesis = flow.c.progression.hypotheses_for("FAULT-U4")[0]
    assert hypothesis.state is HypothesisState.CONFIRMED and hypothesis.transitions[-1].evidence_ids == [verify.result.result_id]


def test_post_action_verification_failure_reassesses_then_escalates() -> None:
    flow = _Flow()
    _remediated(flow)
    _executed(flow)
    flow.operator("")
    flow.propose(_HGET, action="Verify the unit state")
    flow.operator(_DISABLED)
    assert [c.status for c in flow.remediation.criteria] == [CriterionStatus.NOT_MET, CriterionStatus.NOT_MET]
    assert flow.remediation.state is RemediationState.VERIFICATION_FAILED
    assert (flow.fault.phase, flow.fault.resolution) == (ProgressionPhase.REASSESS, ResolutionState.UNRESOLVED)
    # What the failure means for the hypothesis is the AGENT's interpretation: the server does not
    # weaken it on its own; the agent may propose it, with the bound verification result as evidence.
    hypothesis = flow.c.progression.hypotheses_for("FAULT-U4")[0]
    assert hypothesis.state is HypothesisState.ACTIVE
    applied = flow.c.apply_hypothesis_updates([{"statement": _HYPOTHESIS, "proposed_state": "weakened"}])
    assert (applied[0]["status"], hypothesis.state) == ("applied", HypothesisState.WEAKENED)
    assert hypothesis.transitions[-1].evidence_ids == flow.c.bound_result_ids
    flow.c.save()

    # The agent may still judge a retry technically worthwhile: a weakened hypothesis never blocks
    # it on the server; the operator's explicit retry is the recorded re-check reason.
    flow.operator("please retry the restart")
    assert flow.propose(_RESTART, action="Recover the unit", tests_hypothesis=_HYPOTHESIS, verification_criteria=_CRITERIA)[0] is D.RECHECK_PERMITTED
    _executed(flow, "done")
    flow.operator("please re-check the unit state")
    assert flow.propose(_HGET, action="Verify the unit state")[0] is D.RECHECK_PERMITTED
    flow.operator(_DISABLED)
    assert (flow.fault.phase, flow.fault.resolution) == (ProgressionPhase.ESCALATION_REQUIRED, ResolutionState.ESCALATED)
    assert flow.thread.status is TroubleshootingStatus.ESCALATION_REQUIRED


# 5. hypotheses need evidence -----------------------------------------------------------------------------------
def test_hypothesis_state_changes_require_evidence_and_confirmed_requires_criteria() -> None:
    flow = _Flow()
    flow.operator("unit 4 is down")
    no_evidence = flow.c.apply_hypothesis_updates([{"statement": _HYPOTHESIS, "proposed_state": "supported"}])
    assert (no_evidence[0]["status"], no_evidence[0]["state"]) == ("rejected", "active")

    flow.propose(_HGET, action="Check the unit state")
    flow.operator(_DISABLED)
    bound = flow.c.bound_result_ids
    applied = flow.c.apply_hypothesis_updates([
        {"statement": _HYPOTHESIS, "proposed_state": "confirmed", "rationale": "the TAE is sure"},
        {"statement": "The unit links are down", "proposed_state": "rejected"},
    ])
    assert [(a["status"], a["state"]) for a in applied] == [("downgraded", "supported"), ("applied", "rejected")]
    main, links = flow.c.progression.hypotheses_for("FAULT-U4")
    assert main.state is HypothesisState.SUPPORTED and main.transitions[-1].evidence_ids == bound
    assert links.state is HypothesisState.REJECTED and links.transitions[-1].evidence_ids == bound
    assert all(h.state is not HypothesisState.CONFIRMED for h in flow.c.progression.hypotheses_for("FAULT-U4"))
    # Same statement (reworded order) maps to the same hypothesis; no duplicate is created.
    flow.c.apply_hypothesis_updates([{"statement": "a restart clears the failed operational state unit 4 is in", "proposed_state": "active"}])
    assert len(flow.c.progression.hypotheses_for("FAULT-U4")) == 2, "same content tokens -> same hypothesis"
    with pytest.raises(ValueError):
        flow.c.progression.transition_hypothesis(main.hypothesis_id, HypothesisState.CONFIRMED, [])


def test_confirmed_hypothesis_is_not_changed_by_a_proposal() -> None:
    flow = _Flow()
    _remediated(flow)
    _executed(flow)
    flow.operator("")
    flow.propose(_HGET, action="Verify the unit state")
    flow.operator(_ENABLED)
    applied = flow.c.apply_hypothesis_updates([{"statement": _HYPOTHESIS, "proposed_state": "rejected"}])
    assert applied[0]["status"] == "rejected" and flow.c.progression.hypotheses_for("FAULT-U4")[0].state is HypothesisState.CONFIRMED


# 6. end to end: target confirmation + HITL unchanged, then verified resolution ---------------------------------
def _mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID, document_type=KnowledgeDocumentType.MOP, title="Unit Recovery Procedure",
        version=KnowledgeVersion(label=_VER, effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id="MOP_Unit.docx", display_name="Unit MOP"),
        sections=[KnowledgeSection(section_id=_SEC, knowledge_id=_KID, heading="Unit recovery", sequence=0, content=_CONTENT, source_locator="lines:1-4")],
    )


def _tae(outcome: str = "recommended", template: Optional[str] = None, action: str = "", **step: Any) -> list[types.Part]:
    payload: dict[str, Any] = {"outcome": outcome, "technical_interpretation": "Interpretation.", "missing_information": [],
                               "hypothesis_updates": step.pop("hypothesis_updates", [])}
    if outcome == "recommended":
        payload["diagnostic_step"] = {"action": action, "reason": "r", "expected_evidence": "Output", "command": None, "command_source": None,
                                      "restrictions": [], "procedure_action_id": _IDS[template], "parameter_values": [{"name": "unit", "value": "4"}], **step}
    return [types.Part.from_text(text=json.dumps(payload))]


def _calls(*tail: list[types.Part]) -> list[list[types.Part]]:
    return [_search("unit recovery"), _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": _KID, "version_label": _VER, "section_id": _SEC}]}), _CATALOG, *tail]


def _progression_event(turn: dict[str, Any]) -> dict[str, Any]:
    return [e for e in turn["trace"]["operational_events"] if e.get("stage") == "progression"][-1]


@pytest.mark.asyncio
async def test_end_to_end_remediation_keeps_target_confirmation_and_hitl_and_resolves_only_on_verification(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await conv.turn("Unit 4 is down with an alarm. What should I check?", _calls(_tae(template=_HGET, action="Check the unit state")),
                    "Run hget Unit=4.", {"subject_component": "unit"})
    t2 = await conv.turn(_DISABLED, _calls(_tae(template=_RESTART, action="Recover the unit",
                                                hypothesis_updates=[{"statement": _HYPOTHESIS, "proposed_state": "confirmed"}])), "Restart it.")
    assert _progression_event(t2)["decision"] == "rejected_premature_remediation", "prerequisite link check not done yet"
    assert t2["record"]["diagnostic_step"] is None and "acc Unit=4 restart" not in t2["final"]
    # The TAE's CONFIRMED proposal is applied only as SUPPORTED (evidence: the output bound this turn).
    assert [(h.statement, h.state) for h in load_progression(t2["state"]).hypotheses] == [(_HYPOTHESIS, HypothesisState.SUPPORTED)]

    t3 = await conv.turn("what should I check before any recovery?", _calls(_tae(template=_LGET, action="Check the unit links")), "Run lget Link=4.")
    assert t3["record"]["diagnostic_step"]["command"] == "lget Link=4"
    t4 = await conv.turn("lget Link=4\nLink=4 state=UP", _calls(_tae(template=_RESTART, action="Recover the unit", tests_hypothesis=_HYPOTHESIS,
                                                                    verification_criteria=_CRITERIA)), "Run acc Unit=4 restart.")
    event = _progression_event(t4)
    assert event["decision"] == "new_step" and all(v["met"] for v in event["remediation_gate"].values())
    assert (event["lifecycle_stage"], event["remediation"]["state"], len(event["remediation"]["criteria"])) == ("remediation", "candidate", 2)
    # Existing controls unchanged: never an in-turn command; target confirmation, then HITL.
    assert t4["record"]["diagnostic_step"]["command"] is None
    control = t4["record"]["operational_control"]
    assert (control["control_stage"], control["policy_decision"], control["reason_codes"]) == (
        "awaiting_confirmation", "clarification_required", ["STATE_CHANGE", "TARGET_NOT_CONFIRMED"]
    )
    assert "acc Unit=4 restart" not in t4["final"]
    confirm = load_active_proposal(t4["state"])
    assert confirm.operation.value == "operational.confirmTarget"
    approval = (await approval_service.approve(conv.sessions, conv.session_id, confirm.proposal_id, "eng")).pending_action
    assert approval.operational.command == "acc Unit=4 restart"
    approved = await approval_service.approve(conv.sessions, conv.session_id, approval.proposal_id, "eng")
    assert approved.pending_action.operational.control_stage == "ready_for_execution"
    with pytest.raises(SafeErrorException):
        await execution_service.execute(conv.sessions, conv.session_id, approval.proposal_id, "eng")
    state = dict((await conv.sessions.get_session(conv.session_id, "eng")).state)
    progression = load_progression(state)
    fault = progression.faults[progression.active_fault_id]
    assert (fault.active_remediation.state, fault.resolution) == (RemediationState.CANDIDATE, ResolutionState.UNRESOLVED), "approval is not execution"

    # The operator executes it and reports: ACTION_EXECUTED -> POST_ACTION_VERIFICATION_REQUIRED, unresolved.
    t5 = await conv.turn("done, restart executed", _calls(_tae(template=_HGET, action="Verify the unit state after recovery")), "Run hget Unit=4.")
    event = _progression_event(t5)
    assert (event["turn_kind"], event["decision"], event["lifecycle_stage"], event["resolution"]) == (
        "result", "recheck_permitted", "post_action_verification", "unresolved"
    )
    assert t5["record"]["diagnostic_step"]["command"] == "hget Unit=4"
    assert t5["state"]["troubleshooting_state"]["status"] != "resolved"

    t6 = await conv.turn(_ENABLED, _calls(_tae(outcome="insufficient_evidence")), "The unit is back.")
    event = _progression_event(t6)
    assert (event["lifecycle_stage"], event["resolution"], event["remediation"]["state"]) == ("resolved", "resolved", "verified")
    assert [c["status"] for c in event["remediation"]["criteria"]] == ["met", "met"]
    assert t6["state"]["troubleshooting_state"]["status"] == "resolved"
    hypotheses = load_progression(t6["state"]).hypotheses
    assert [(h.statement, h.state) for h in hypotheses] == [(_HYPOTHESIS, HypothesisState.CONFIRMED)]
