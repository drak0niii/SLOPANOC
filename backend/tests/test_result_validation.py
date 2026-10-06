"""Trusted result validation: operator message -> candidate observation -> RESULT VALIDATION ->
trusted result -> step completion. Operator text is trusted as what the operator said, not
automatically as evidence satisfying the pending check. Deterministic; no model involved.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.cases.target_facts import case_target_facts
from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.progression_controller import ProgressionController, ProposalDecision, TurnKind
from backend.agents.technical_authority_engineer.result_validation import ResultValidationStatus
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.cases.troubleshooting_progression import ProgressionPhase, ResultSource, StepStatus, load_progression
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.operations.signing import text_hash

_KID, _SEC = "KID-RESULTS", "sec-0000"
_CONTENT = (
    "Radio unit state check: `st ru`\n"
    "Cell state check: `st cell`\n"
    "Board state check: `st board`\n"
    "Board recovery: `acc Board=xxxx restartboard`\n"
)
_EV = EvidenceReference(
    source_id=f"{_KID}:v1:{_SEC}", source_type="governed_knowledge", title="Results MOP", content_snippet=_CONTENT,
    metadata={"knowledge_id": _KID, "version_label": "v1", "section_id": _SEC, "source_locator": "lines:1-4", "title": "Results MOP",
              "heading": None, "lifecycle_status": "approved", "applicability_outcome": "match"},
)
_ACTIONS = {a.command_template: a for a in pa.actions_for_evidence(_EV, _EV.metadata)[0]}
_OBJECTIVES = {"st ru": "Check the radio unit state", "st cell": "Check the cell state", "st board": "Check the board state"}


class _Thread:
    """One fault thread; every operator turn rebuilds the controller from session state (like the TAE tool)."""

    def __init__(self, state: dict[str, Any], fault_id: str = "FAULT-A") -> None:
        self.state = state
        self.ts = TroubleshootingState(fault_id=fault_id, symptom_summary="radio fault")
        self.c = ProgressionController(state, self.ts, session_id="s1")

    def turn(self, text: str) -> TurnKind:
        self.c = ProgressionController(self.state, self.ts, session_id="s1")
        kind = self.c.classify_turn(text)
        self.c.apply_operator_turn(text)
        self.c.save()
        return kind

    def present(self, command: str) -> str:
        action = _ACTIONS[command]
        resolution, _ = pa.resolve_procedure_action(action.action_id, issued_ids={action.action_id}, selected_evidence=[_EV])
        proposal = {"outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
                    "diagnostic_step": {"action": _OBJECTIVES[command], "reason": "r", "expected_evidence": f"{_OBJECTIVES[command][10:]} output",
                                        "command": command, "command_source": _EV.source_id, "restrictions": [], "procedure_action_id": action.action_id}}
        decision, result = self.c.evaluate_proposal(proposal, resolution, [_EV])
        assert decision in (ProposalDecision.NEW_STEP, ProposalDecision.RECHECK_PERMITTED)
        check_id = self.c.check_id_for(decision, f"chk-{self.ts.fault_id}-{command.replace(' ', '-')}-{len(self.ts.diagnostic_history)}")
        self.ts.record_recommended_check(action=_OBJECTIVES[command], rationale="r", expected_observation="e", grounded_command=command,
                                         procedure_action_id=action.action_id, check_id=check_id)
        self.c.record(decision, result, check_id, selected_evidence_ids=[_EV.source_id], applicability={_EV.source_id: "match"})
        self.c.save()
        return check_id

    @property
    def step(self):
        progression = load_progression(self.state)
        return progression.steps_for(self.ts.fault_id)[-1]

    def check(self, check_id: str):
        return next(c for c in self.ts.diagnostic_history if c.check_id == check_id)


def _pending(thread: _Thread, check_id: str) -> None:
    assert thread.step.status is StepStatus.PRESENTED and thread.step.result is None
    assert thread.check(check_id).status.value == "recommended" and thread.check(check_id).operator_observation is None
    assert thread.ts.trusted_observation_texts() == []


# question containing "=" -------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["is Unit=4 the one you mean?", "what does OPER=ENABLED mean?", "Is RadioUnit=1 ENABLED the expected value?"])
def test_question_containing_equals_does_not_complete(text: str) -> None:
    thread = _Thread({})
    check_id = thread.present("st ru")
    assert thread.turn(text) in (TurnKind.OTHER, TurnKind.COMMAND_FOLLOW_UP)
    assert thread.c.validation.status is ResultValidationStatus.NOT_A_RESULT
    _pending(thread, check_id)
    assert thread.step.candidate_observations == [], "a question is not even a candidate observation"


# command question ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["should I run st ru?", "do I run st ru now?", "st ru?", "can you explain what st ru shows?"])
def test_command_question_does_not_complete(text: str) -> None:
    thread = _Thread({})
    check_id = thread.present("st ru")
    assert thread.turn(text) is TurnKind.COMMAND_FOLLOW_UP
    _pending(thread, check_id)


# plain prose / incompatible output ------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["the customer called again about the outage", "ok thanks, give me a minute", "it looks fine I think"])
def test_prose_unrelated_to_the_expected_evidence_does_not_complete(text: str) -> None:
    thread = _Thread({})
    check_id = thread.present("st ru")
    assert thread.turn(text) in (TurnKind.OTHER, TurnKind.COMMAND_FOLLOW_UP)
    _pending(thread, check_id)


@pytest.mark.parametrize("text", ["I ran st ru but got nothing", "st ru is fine"])
def test_command_mentioned_without_its_output_does_not_complete(text: str) -> None:
    thread = _Thread({})
    check_id = thread.present("st ru")
    assert thread.turn(text) is TurnKind.UNVALIDATED_RESULT
    _pending(thread, check_id)


def test_output_that_cannot_be_attributed_fails_closed_and_requests_the_output() -> None:
    thread = _Thread({})
    check_id = thread.present("st ru")
    assert thread.turn("NRCellDU=1 OPER=DISABLED AVAIL=FAILED") is TurnKind.UNVALIDATED_RESULT
    _pending(thread, check_id)
    candidate = thread.step.candidate_observations[-1]
    assert (candidate.source, candidate.validation["status"]) == (ResultSource.OPERATOR_MESSAGE, "ambiguous")
    assert load_progression(thread.state).faults["FAULT-A"].phase is ProgressionPhase.AWAITING_OBSERVATION
    context = thread.c.pending_context()
    assert context["required_output"] == "The output of `st ru` for this step" and context["last_candidate"]["status"] == "ambiguous"
    assert thread.c.continuity_objective() == "Resolve the pending diagnostic step: Check the radio unit state"


# valid pasted output ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "$ st ru\nRadioUnit=1 ENABLED UNLOCKED",
        "NODE1> st ru\nRadioUnit=1 OPER=ENABLED\nRadioUnit=2 OPER=ENABLED",
        "st ru output: RadioUnit=1 ENABLED UNLOCKED",
        "RadioUnit=1 ENABLED UNLOCKED",  # no echo: structured AND attributable (radio + unit)
        "here is what I got\n$ st ru\nRadioUnit=1 ENABLED UNLOCKED\ndoes that look ok?",  # prose/question lines ignored
        "I ran st ru: RadioUnit=1 ENABLED UNLOCKED",  # explicit run report with the output inline
    ],
)
def test_valid_pasted_command_output_completes(text: str) -> None:
    thread = _Thread({})
    check_id = thread.present("st ru")
    assert thread.turn(text) is TurnKind.RESULT
    step = thread.step
    assert step.status is StepStatus.COMPLETED and step.result.text == text and step.result.validation["status"] == "validated"
    assert [c.to_status for c in step.status_history][-3:] == [StepStatus.OBSERVED, StepStatus.VERIFIED, StepStatus.COMPLETED]
    phases = [e.details["phase"] for e in load_progression(thread.state).events if e.event == "phase_changed"]
    assert phases[-3:] == ["result_received", "result_validated", "reassess"]
    assert thread.check(check_id).operator_observation == text and thread.ts.trusted_observation_texts() == [text]


# explicit command error ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["$ st ru\nERROR: unknown command", "st ru\nsyntax error near 'ru'", "Permission denied"])
def test_explicit_command_error_marks_the_step_failed(text: str) -> None:
    thread = _Thread({})
    check_id = thread.present("st ru")
    assert thread.turn(text) is TurnKind.RESULT
    assert thread.step.status is StepStatus.FAILED and thread.step.result.validation["status"] == "command_failed"
    assert thread.ts.trusted_observation_texts() == [], "an error is the step's outcome, never evidence"
    assert thread.check(check_id).status.value == "executed"


# result for another step / fault thread --------------------------------------------------------------------------
def test_output_of_another_step_does_not_bind_to_the_pending_step() -> None:
    thread = _Thread({})
    thread.present("st ru")
    assert thread.turn("$ st ru\nRadioUnit=1 ENABLED") is TurnKind.RESULT
    cell_check = thread.present("st cell")
    assert thread.turn("$ st ru\nRadioUnit=1 DISABLED") is TurnKind.UNVALIDATED_RESULT
    assert thread.step.candidate_observations[-1].validation["status"] == "foreign"
    assert thread.step.status is StepStatus.PRESENTED and thread.check(cell_check).status.value == "recommended"
    # A command error of another step does not fail the pending one either.
    assert thread.turn("$ st ru\nERROR: unknown command") is TurnKind.UNVALIDATED_RESULT and thread.step.status is StepStatus.PRESENTED


def test_result_for_another_fault_thread_does_not_bind() -> None:
    state: dict[str, Any] = {}
    thread_b = _Thread(state, "FAULT-B")
    cell_check_b = thread_b.present("st cell")
    thread_a = _Thread(state, "FAULT-A")
    ru_check_a = thread_a.present("st ru")
    assert thread_a.turn("$ st cell\nNRCellDU=1 OPER=DISABLED") is TurnKind.UNVALIDATED_RESULT
    assert thread_a.step.status is StepStatus.PRESENTED and thread_a.check(ru_check_a).status.value == "recommended"
    step_b = load_progression(state).steps_for("FAULT-B")[-1]
    assert step_b.status is StepStatus.PRESENTED and step_b.result is None, "a message in thread A never completes thread B"
    assert thread_b.check(cell_check_b).status.value == "recommended"


# controlled adapter output -------------------------------------------------------------------------------------------
_OUTPUT = "RadioUnit=1 OPER=ENABLED"


def _execute(thread: _Thread, check_id: str, execution_id: str = "exec-1", output: str = _OUTPUT) -> bool:
    """A controlled execution reported for `check_id` -- recorded progression-first (the only path)."""
    thread.c = ProgressionController(thread.state, thread.ts, session_id="s1", activate=False)
    bound = thread.c.record_controlled_execution(check_id, execution_id, output, True)
    thread.c.save()
    return bound


def _control(state: dict[str, Any], check_id: str, execution_id: str = "exec-1", status: str = "succeeded", output: str = _OUTPUT) -> None:
    state.setdefault("operational_action_controls", {})[f"ctl-{check_id}"] = {
        "context": {"check_id": check_id}, "executions": [{"execution_id": execution_id, "check_id": check_id, "status": status, "output_hash": text_hash(output)}],
    }


def test_controlled_adapter_output_completes_its_bound_step() -> None:
    state: dict[str, Any] = {}
    thread = _Thread(state)
    check_id = thread.present("st ru")
    _control(state, check_id)
    assert _execute(thread, check_id)
    step = thread.step
    assert (step.status, step.result.source, step.result.execution_id) == (StepStatus.COMPLETED, ResultSource.EXECUTION_ADAPTER, "exec-1")
    assert step.result.validation["reasons"] == ["controlled execution bound to this check"]


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda state, check_id: None, "no controlled execution of this check is on record"),  # forged thread entry
        (lambda state, check_id: _control(state, "chk-other"), "no controlled execution of this check is on record"),
        (lambda state, check_id: _control(state, check_id, execution_id="exec-9"), "no controlled execution of this check is on record"),
        (lambda state, check_id: _control(state, check_id, status="failed"), "the controlled execution did not succeed"),
        (lambda state, check_id: _control(state, check_id, output="RadioUnit=1 OPER=DISABLED"), "recorded output does not match the executed output"),
    ],
    ids=["no-control-record", "other-check", "other-execution", "failed-execution", "tampered-output"],
)
def test_adapter_output_without_an_exact_execution_binding_does_not_complete(setup, reason: str) -> None:
    state: dict[str, Any] = {}
    thread = _Thread(state)
    check_id = thread.present("st ru")
    setup(state, check_id)
    assert not _execute(thread, check_id)
    assert not _execute(thread, check_id)  # reported again: the rejected candidate is recorded once
    step = thread.step
    assert step.status is StepStatus.PRESENTED and step.result is None
    assert [c.validation["reasons"] for c in step.candidate_observations] == [[reason]]


def test_adapter_output_of_another_thread_does_not_complete_this_thread() -> None:
    state: dict[str, Any] = {}
    thread_b = _Thread(state, "FAULT-B")
    check_b = thread_b.present("st ru")
    thread_a = _Thread(state, "FAULT-A")
    thread_a.present("st ru")
    _control(state, check_b)
    assert not _execute(thread_a, check_b), "thread A has no step for thread B's check"
    assert thread_a.step.status is StepStatus.PRESENTED and thread_a.step.result is None
    assert _execute(thread_b, check_b)
    assert load_progression(state).steps_for("FAULT-B")[-1].status is StepStatus.COMPLETED


# parameter extraction only sees trusted observations -------------------------------------------------------------------
def _board_binding(thread: _Thread) -> Optional[str]:
    action = _ACTIONS["acc Board=xxxx restartboard"]
    resolution, _ = pa.resolve_procedure_action(
        action.action_id, issued_ids={action.action_id}, selected_evidence=[_EV], operator_text="",
        observed_texts=thread.ts.trusted_observation_texts(),
        # State-change targets come only from THIS fault's trusted, validated results.
        target_facts=case_target_facts(thread.c.progression, thread.ts.fault_id), fault_id=thread.ts.fault_id,
    )
    return next((b.value for b in resolution.bindings if b.name == "Board" and b.state.value == "verified"), None)


def test_parameter_extraction_only_sees_validated_observations() -> None:
    thread = _Thread({})
    thread.present("st board")
    for text in ("is Board=9 the faulty one?", "Board=9 OPER=DISABLED", "$ st board\nERROR: permission denied Board=9"):
        assert thread.turn(text) is not TurnKind.RESULT or thread.step.status is StepStatus.FAILED
        assert "Board=9" not in " ".join(thread.ts.trusted_observation_texts())
        assert _board_binding(thread) is None, text
    assert thread.step.status is StepStatus.FAILED
    thread.c = ProgressionController(thread.state, thread.ts, session_id="s1")
    thread.turn("please re-check the board")
    thread.present("st board")
    assert thread.turn("$ st board\nBoard=4 OPER=DISABLED") is TurnKind.RESULT
    assert _board_binding(thread) == "4", "only the validated observation feeds parameter binding"
