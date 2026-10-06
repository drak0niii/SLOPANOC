"""Troubleshooting Progression: authoritative, server-owned investigation model (no runtime wiring)."""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from backend.agents.technical_authority_engineer.troubleshooting_threads import save_threads
from backend.cases.troubleshooting_progression import (
    PROGRESSION_STATE_KEY,
    FaultProgression,
    Hypothesis,
    HypothesisState,
    ProgressionPhase,
    ResultSource,
    StepStatus,
    TroubleshootingProgression,
    TroubleshootingStep,
    load_or_adapt_progression,
    load_progression,
    progression_from_legacy_state,
    project_troubleshooting_state,
    save_progression,
)
from backend.cases.troubleshooting_state import CheckLifecycleStatus, TroubleshootingState, TroubleshootingStatus


def _progression() -> TroubleshootingProgression:
    p = TroubleshootingProgression(case_id="CASE-1", session_ids=["sess-1"])
    p.add_fault(FaultProgression(fault_id="F-BB", symptom_summary="Baseband unit fault alarm", subject_component="baseband"))
    return p


# --- creation ---------------------------------------------------------------------------------------
def test_progression_creation_and_versioning() -> None:
    p = _progression()
    assert (p.case_id, p.active_fault_id, p.current_phase) == ("CASE-1", "F-BB", ProgressionPhase.NEW)
    assert p.version == 1 and [e.event for e in p.events] == ["fault_added"]
    p.set_phase("F-BB", ProgressionPhase.CONTEXT_BUILDING, reason="alarm received")
    assert p.version == 2 and p.current_phase is ProgressionPhase.CONTEXT_BUILDING
    q = p.add_open_question("F-BB", "Which technology is the node?")
    assert p.open_questions == [q] and not q.answered
    with pytest.raises(ValueError):
        p.add_fault(FaultProgression(fault_id="F-BB", symptom_summary="dup"))


def test_all_required_phases_statuses_and_hypothesis_states_exist() -> None:
    assert {ph.name for ph in ProgressionPhase} == {
        "NEW", "CONTEXT_BUILDING", "READY_FOR_DIAGNOSIS", "EVIDENCE_SELECTED", "STEP_PROPOSED", "STEP_VALIDATED",
        "AWAITING_OBSERVATION", "RESULT_RECEIVED", "RESULT_VALIDATED", "RESULT_VERIFIED", "REASSESS", "REMEDIATION_CANDIDATE", "REMEDIATION",
        "ACTION_EXECUTED", "POST_ACTION_VERIFICATION_REQUIRED", "RESOLVED", "ESCALATION_REQUIRED", "BLOCKED_MISSING_INFORMATION",
    }
    assert {s.name for s in StepStatus} == {
        "PROPOSED", "VALIDATED", "PRESENTED", "EXECUTED", "OBSERVED", "VERIFIED", "COMPLETED", "REJECTED", "SKIPPED", "FAILED",
        "CANCELLED", "SUPERSEDED", "BLOCKED_BY_CLARIFICATION", "BLOCKED_GOVERNED_ACQUISITION_GAP"
    }
    assert {h.name for h in HypothesisState} == {"ACTIVE", "SUPPORTED", "WEAKENED", "REJECTED", "CONFIRMED"}


# --- step lifecycle -----------------------------------------------------------------------------------
def test_step_lifecycle_representation() -> None:
    p = _progression()
    step = p.append_step(TroubleshootingStep(
        fault_id="F-BB", objective="Check field replaceable unit state", procedure_action_id="pa-1", command="st fieldr",
        command_source_id="KID:v1:sec", target={"target_type": "node", "canonical_identifier": "NODE-1"},
        selected_evidence_ids=["KID:v1:sec"], applicability_snapshot={"outcome": "match", "dimensions": {"vendor": ["ericsson"]}},
        prerequisites=["Logged in to the node"], expected_evidence="FRU state table", completion_criteria=["FRU state reported"],
        safety_constraints=["Read-only diagnostic."],
    ))
    assert (step.sequence, p.current_step.step_id) == (1, step.step_id)
    for status in (StepStatus.VALIDATED, StepStatus.PRESENTED):
        p.set_step_status(step.step_id, status)
    result = p.record_step_result(step.step_id, "FieldReplaceableUnit=4 DISABLED", ResultSource.OPERATOR_MESSAGE)
    assert step.status is StepStatus.OBSERVED and step.result_source is ResultSource.OPERATOR_MESSAGE and result.result_id.startswith("obs-")
    p.set_step_status(step.step_id, StepStatus.VERIFIED)
    p.set_step_status(step.step_id, StepStatus.COMPLETED)
    assert [c.to_status for c in step.status_history] == [
        StepStatus.PROPOSED, StepStatus.VALIDATED, StepStatus.PRESENTED, StepStatus.OBSERVED, StepStatus.VERIFIED, StepStatus.COMPLETED
    ]
    second = p.append_step(TroubleshootingStep(fault_id="F-BB", objective="Recover the unit"))
    assert second.sequence == 2 and p.current_step.step_id == second.step_id
    with pytest.raises(KeyError):
        p.append_step(TroubleshootingStep(fault_id="F-UNKNOWN", objective="x"))


# --- fault-thread isolation ------------------------------------------------------------------------------
def test_fault_threads_are_isolated_and_switching_keeps_history() -> None:
    p = _progression()
    bb = p.append_step(TroubleshootingStep(fault_id="F-BB", objective="Check baseband"))
    p.add_fault(FaultProgression(fault_id="F-RRU", symptom_summary="what about resetting the radio unit", subject_component="rru"))
    rru = p.append_step(TroubleshootingStep(fault_id="F-RRU", objective="Check radio port"))
    p.add_hypothesis(Hypothesis(fault_id="F-BB", statement="Baseband hardware fault"))
    p.add_hypothesis(Hypothesis(fault_id="F-RRU", statement="Radio unit hung"))
    assert p.active_fault_id == "F-RRU"
    assert [s.step_id for s in p.steps_for("F-BB")] == [bb.step_id]
    assert [s.step_id for s in p.steps_for("F-RRU")] == [rru.step_id]
    rru_view = project_troubleshooting_state(p)
    assert [c.action for c in rru_view.diagnostic_history] == ["Check radio port"]
    assert rru_view.working_hypothesis == "Radio unit hung" and rru_view.competing_hypotheses == []
    p.activate_fault("F-BB")
    bb_view = project_troubleshooting_state(p)
    assert (bb_view.fault_id, [c.action for c in bb_view.diagnostic_history]) == ("F-BB", ["Check baseband"])
    assert len(p.steps) == 2, "switching fault focus never removes history"


# --- runtime projection -----------------------------------------------------------------------------------
def test_runtime_projection_is_bounded_and_honours_result_provenance() -> None:
    p = _progression()
    for i in range(5):
        p.append_step(TroubleshootingStep(fault_id="F-BB", objective=f"step {i}", command=f"st x{i}", command_source_id="KID:v1:sec",
                                          command_source_identity={"knowledge_id": "KID", "version_label": "v1", "section_id": "sec"}))
    op = p.steps[3]
    p.record_step_result(op.step_id, "FieldReplaceableUnit=4 DISABLED", ResultSource.OPERATOR_MESSAGE)
    ad = p.steps[4]
    p.record_step_result(ad.step_id, "Board=4 LOCKED", ResultSource.EXECUTION_ADAPTER, execution_id="exec-1", status=StepStatus.COMPLETED)
    p.set_phase("F-BB", ProgressionPhase.AWAITING_OBSERVATION)
    view = project_troubleshooting_state(p, max_steps=3)
    assert isinstance(view, TroubleshootingState)
    assert [c.action for c in view.diagnostic_history] == ["step 2", "step 3", "step 4"], "bounded to the latest steps"
    assert view.status is TroubleshootingStatus.TESTING_NEXT_STEP and view.case_id == "CASE-1" and view.session_id == "sess-1"
    op_check, ad_check = view.diagnostic_history[1], view.diagnostic_history[2]
    assert (op_check.status, op_check.operator_observation, op_check.observed_result_source) == (
        CheckLifecycleStatus.EXECUTED, "FieldReplaceableUnit=4 DISABLED", None
    )
    assert (ad_check.status, ad_check.observed_result, ad_check.observed_result_source, ad_check.execution_ids) == (
        CheckLifecycleStatus.COMPLETED, "Board=4 LOCKED", "execution_adapter", ["exec-1"]
    )
    assert view.trusted_observation_texts() == ["Board=4 LOCKED", "FieldReplaceableUnit=4 DISABLED"]
    assert view.applicable_procedure_ids == ["KID"]


def test_caller_summary_is_never_projected_as_trusted_evidence() -> None:
    p = _progression()
    step = p.append_step(TroubleshootingStep(fault_id="F-BB", objective="check"))
    p.record_step_result(step.step_id, "Board **4** is disabled", ResultSource.CALLER_SUMMARY)
    view = project_troubleshooting_state(p)
    assert view.diagnostic_history[0].observed_result == "Board **4** is disabled"
    assert view.trusted_observation_texts() == [] and view.verified_evidence_ids == []


# --- hypotheses / evidence --------------------------------------------------------------------------------
def test_hypothesis_transitions_must_reference_evidence() -> None:
    p = _progression()
    step = p.append_step(TroubleshootingStep(fault_id="F-BB", objective="Check FRU state"))
    obs = p.record_step_result(step.step_id, "FieldReplaceableUnit=4 DISABLED", ResultSource.OPERATOR_MESSAGE)
    hw = p.add_hypothesis(Hypothesis(fault_id="F-BB", statement="Unit 4 hardware fault"))
    sw = p.add_hypothesis(Hypothesis(fault_id="F-BB", statement="Software hang"))
    with pytest.raises(ValueError):
        p.transition_hypothesis(hw.hypothesis_id, HypothesisState.SUPPORTED, evidence_ids=[])
    p.transition_hypothesis(hw.hypothesis_id, HypothesisState.SUPPORTED, evidence_ids=[obs.result_id], step_id=step.step_id, reason="unit disabled")
    p.transition_hypothesis(sw.hypothesis_id, HypothesisState.WEAKENED, evidence_ids=[obs.result_id])
    last = hw.transitions[-1]
    assert (last.from_state, last.to_state, last.evidence_ids, last.step_id) == (HypothesisState.ACTIVE, HypothesisState.SUPPORTED, [obs.result_id], step.step_id)
    view = project_troubleshooting_state(p)
    assert view.working_hypothesis == "Unit 4 hardware fault" and view.competing_hypotheses == []
    p.transition_hypothesis(hw.hypothesis_id, HypothesisState.CONFIRMED, evidence_ids=[obs.result_id])
    assert [t.to_state for t in hw.transitions] == [HypothesisState.ACTIVE, HypothesisState.SUPPORTED, HypothesisState.CONFIRMED]


# --- backwards compatibility --------------------------------------------------------------------------------
def _legacy_state() -> dict[str, Any]:
    bb = TroubleshootingState(fault_id="F-BB", symptom_summary="Baseband unit fault alarm", subject_component="baseband",
                              session_id="sess-1", working_hypothesis="Baseband hardware fault")
    bb.record_recommended_check(action="Check FRU state", rationale="r", expected_observation="FRU table", grounded_command="st fieldr",
                                command_source_id="KID:v1:sec", check_id="chk-bb1", procedure_action_id="pa-fieldr")
    bb.record_user_execution(check_id="chk-bb1", observed_result="FRU 4 is disabled (summary)", operator_observation="FieldReplaceableUnit=4 DISABLED")
    rru = TroubleshootingState(fault_id="F-RRU", symptom_summary="what about resetting rru ?", subject_component="rru", session_id="sess-1")
    rru.record_recommended_check(action="Read radio port reference", rationale="r", expected_observation="port table",
                                 grounded_command="hget near Rfportref", check_id="chk-rru1")
    rru.record_adapter_execution(check_id="chk-rru1", execution_id="exec-7", observed_result="Rfportref=A OK", succeeded=True)
    rru.record_recommended_check(action="Recover the radio unit", rationale="r", expected_observation="unit recovers", check_id="chk-rru2")
    state: dict[str, Any] = {}
    save_threads(state, {"F-BB": bb, "F-RRU": rru}, "F-RRU")
    return state


def test_legacy_fault_thread_state_is_adapted_without_modification() -> None:
    state = _legacy_state()
    before = copy.deepcopy(state)
    p = progression_from_legacy_state(state, session_id="sess-1", case_id="CASE-9")
    assert state == before, "adaptation never writes to the legacy state"
    assert (p.case_id, p.active_fault_id, sorted(p.faults)) == ("CASE-9", "F-RRU", ["F-BB", "F-RRU"])
    assert [(s.fault_id, s.check_id, s.status) for s in p.steps] == [
        ("F-BB", "chk-bb1", StepStatus.OBSERVED),
        ("F-RRU", "chk-rru1", StepStatus.COMPLETED),
        ("F-RRU", "chk-rru2", StepStatus.PRESENTED),
    ]
    bb_step, adapter_step = p.steps[0], p.steps[1]
    assert (bb_step.result.source, bb_step.result.text, bb_step.observation_summary) == (
        ResultSource.OPERATOR_MESSAGE, "FieldReplaceableUnit=4 DISABLED", "FRU 4 is disabled (summary)"
    )
    assert (adapter_step.result.source, adapter_step.result.execution_id) == (ResultSource.EXECUTION_ADAPTER, "exec-7")
    assert p.faults["F-BB"].phase is ProgressionPhase.RESULT_RECEIVED
    assert p.faults["F-RRU"].phase is ProgressionPhase.AWAITING_OBSERVATION
    assert [h.statement for h in p.hypotheses_for("F-BB")] == ["Baseband hardware fault"]


@pytest.mark.parametrize("fault_id", ["F-BB", "F-RRU"])
def test_projection_round_trips_the_legacy_runtime_view(fault_id: str) -> None:
    state = _legacy_state()
    legacy = TroubleshootingState.model_validate(state["troubleshooting_threads"][fault_id])
    projected = project_troubleshooting_state(progression_from_legacy_state(state), fault_id=fault_id)
    key = lambda c: (c.check_id, c.action, c.grounded_command, c.command_source_id, c.procedure_action_id, c.status,  # noqa: E731
                     c.observed_result, c.observed_result_source, c.operator_observation, c.execution_ids)
    assert [key(c) for c in projected.diagnostic_history] == [key(c) for c in legacy.diagnostic_history]
    assert projected.trusted_observation_texts() == legacy.trusted_observation_texts()
    assert projected.get_prior_steps_summary() == legacy.get_prior_steps_summary()
    assert (projected.fault_id, projected.symptom_summary, projected.subject_component, projected.working_hypothesis) == (
        legacy.fault_id, legacy.symptom_summary, legacy.subject_component, legacy.working_hypothesis
    )


def test_pre_thread_session_with_only_active_state_is_adapted() -> None:
    legacy = TroubleshootingState(fault_id="F-OLD", symptom_summary="old session")
    p = progression_from_legacy_state({"troubleshooting_state": legacy.model_dump(mode="json")})
    assert (p.active_fault_id, list(p.faults), p.steps) == ("F-OLD", ["F-OLD"], [])
    assert p.faults["F-OLD"].phase is ProgressionPhase.CONTEXT_BUILDING
    assert progression_from_legacy_state({}) is None


def test_storage_round_trip_prefers_the_stored_progression() -> None:
    state = _legacy_state()
    adapted = load_or_adapt_progression(state)
    assert PROGRESSION_STATE_KEY not in state, "load_or_adapt never writes"
    adapted.add_open_question("F-RRU", "Which unit id?")
    save_progression(state, adapted)
    json.dumps(state[PROGRESSION_STATE_KEY])
    stored = load_progression(state)
    assert stored == adapted and load_or_adapt_progression(state) == adapted
    assert "troubleshooting_state" in state and "troubleshooting_threads" in state, "legacy runtime keys remain intact"
