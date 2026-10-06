"""Duplicate / loop prevention: server-owned canonical step identity.

    candidate -> canonicalize -> compare with current / completed / attempted steps -> decision

A repeat is permitted only for an explicit, recorded reason (operator re-check request, governed
post-action verification, result stale after a state change, changed governed context). Unit
cases drive the controller with REAL ProcedureAction resolutions; the live regressions drive the
real ChatService + TechnicalAuthorityAgentTool with scripted LLMs.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import pytest

from backend.cases.target_facts import case_target_facts
from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.progression_controller import ProgressionController, ProposalDecision, TurnKind
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.agents.technical_authority_engineer.step_identity import candidate_identity, step_identity
from backend.cases.troubleshooting_progression import StepStatus, load_progression
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _Conversation
from backend.tests.test_progression_controller import (
    _ALT,
    _CELL,
    _CELL_STEP,
    _RU,
    _RU_STEP,
    _checks,
    _decision,
    _mop,
    _pending,
    _propose,
)

D = ProposalDecision
_READ, _RESTART = "hget Unit=<unit>", "acc Unit=<unit> restart"
_WITH_VERIFICATION = (
    "Unit state check: `hget Unit=<unit>`\n"
    "Unit recovery: `acc Unit=<unit> restart`\n"
    "After the recovery, verify the unit state again: `hget Unit=<unit>`\n"
)
_WITHOUT_VERIFICATION = "Unit state check: `hget Unit=<unit>`\nUnit recovery: `acc Unit=<unit> restart`\n"


def _ev(content: str = _WITH_VERIFICATION, kid: str = "KID-UNIT", version: str = "v1", sec: str = "sec-unit") -> EvidenceReference:
    return EvidenceReference(
        source_id=f"{kid}:{version}:{sec}", source_type="governed_knowledge", title="Unit Procedure", content_snippet=content,
        metadata={"knowledge_id": kid, "version_label": version, "section_id": sec, "source_locator": "lines:1-3", "title": "Unit Procedure",
                  "heading": None, "lifecycle_status": "approved", "applicability_outcome": "match"},
    )


class _Flow:
    """One fault thread; each `turn` rebuilds the controller from session state, like the TAE tool."""

    def __init__(self, fault_id: str = "FAULT-1", state: Optional[dict[str, Any]] = None) -> None:
        self.state = state if state is not None else {}
        self.thread = TroubleshootingState(fault_id=fault_id, symptom_summary="unit fault")
        self.controller: ProgressionController

    def operator(self, text: str) -> TurnKind:
        self.controller = ProgressionController(self.state, self.thread, session_id="s1")
        kind = self.controller.classify_turn(text)
        self.controller.apply_operator_turn(text)
        self.controller.save()
        return kind

    def propose(self, template: str, value: Optional[str] = None, ev: Optional[EvidenceReference] = None, action: str = "Check the unit") -> tuple[D, dict[str, Any]]:
        ev = ev or _ev()
        governed = next(a for a in pa.actions_for_evidence(ev, ev.metadata)[0] if a.command_template == template)
        resolution, _ = pa.resolve_procedure_action(
            governed.action_id, issued_ids={governed.action_id}, selected_evidence=[ev],
            proposals=[{"name": "unit", "value": value}] if value else [], operator_text=f"the unit is {value}" if value else "",
            # As in production: state-change targets only from THIS fault's trusted results.
            target_facts=case_target_facts(self.controller.progression, self.thread.fault_id), fault_id=self.thread.fault_id,
        )
        command = resolution.candidate.command if resolution.candidate else None
        proposal = {
            "outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
            "diagnostic_step": {"action": action, "reason": "r", "expected_evidence": "Unit state", "command": command,
                                "command_source": ev.source_id if command else None, "restrictions": [], "procedure_action_id": governed.action_id},
        }
        decision, result = self.controller.evaluate_proposal(proposal, resolution, [ev])
        check_id = self.controller.check_id_for(decision, f"chk-{len(self.thread.diagnostic_history)}")
        if decision in (D.NEW_STEP, D.RECHECK_PERMITTED):
            step = result["diagnostic_step"]
            self.thread.record_recommended_check(
                action=step["action"], rationale="r", expected_observation="e", grounded_command=step["command"],
                procedure_action_id=step["procedure_action_id"], check_id=check_id,
            )
        self.controller.record(decision, result, check_id, selected_evidence_ids=[ev.source_id], applicability={ev.source_id: "match"})
        self.controller.save()
        return decision, result

    def steps(self) -> list:
        return self.controller.progression.steps_for(self.thread.fault_id)


def _completed(flow: _Flow, template: str, value: Optional[str], output: str, ev: Optional[EvidenceReference] = None) -> None:
    flow.operator("")
    assert flow.propose(template, value, ev)[0] is D.NEW_STEP
    assert flow.operator(output) is TurnKind.RESULT


# exact duplicate ------------------------------------------------------------------------------------------
def test_exact_duplicate_is_rejected_and_shares_the_canonical_identity() -> None:
    flow = _Flow()
    _completed(flow, _READ, "4", "hget Unit=4\nUnit=4 OPER=ENABLED")
    decision, result = flow.propose(_READ, "4")
    assert decision is D.REJECTED_REPEATED_STEP
    assert result["outcome"] == "insufficient_evidence" and result["diagnostic_step"] is None
    assert "already performed" in result["missing_information"][-1] and "explicit reason" in result["missing_information"][-1]
    first = step_identity(flow.steps()[0])
    assert flow.controller._candidate.key == first.key
    assert (first.action_key, first.target, first.result_key) == ("pa:KID-UNIT:hget unit=<unit>", "unit=4", "hget unit=4")
    assert flow.controller.summary(decision)["duplicate"] == {"kind": "completed", "of_step_id": flow.steps()[0].step_id, "identity": first.key}
    assert len(flow.steps()) == 1 and len(flow.thread.diagnostic_history) == 1


# same action + same target ---------------------------------------------------------------------------------
def test_same_action_same_target_is_a_duplicate_regardless_of_wording() -> None:
    flow = _Flow()
    _completed(flow, _READ, "4", "hget Unit=4\nUnit=4 OPER=ENABLED")
    assert flow.propose(_READ, "4", action="Verify whether the unit is operational")[0] is D.REJECTED_REPEATED_STEP


# different target / different fault thread are NOT duplicates -------------------------------------------------
def test_different_target_is_not_a_duplicate() -> None:
    flow = _Flow()
    _completed(flow, _READ, "4", "hget Unit=4\nUnit=4 OPER=ENABLED")
    decision, result = flow.propose(_READ, "5")
    assert decision is D.NEW_STEP and result["diagnostic_step"]["command"] == "hget Unit=5"
    assert [step_identity(s).target for s in flow.steps()] == ["unit=4", "unit=5"]


def test_different_fault_thread_is_not_a_duplicate() -> None:
    state: dict[str, Any] = {}
    first = _Flow("FAULT-1", state)
    _completed(first, _READ, "4", "hget Unit=4\nUnit=4 OPER=ENABLED")
    second = _Flow("FAULT-2", state)
    second.operator("")
    assert second.propose(_READ, "4")[0] is D.NEW_STEP, "same action + target on another fault thread is a new step"
    first.operator("")
    assert first.propose(_READ, "4")[0] is D.REJECTED_REPEATED_STEP, "still a duplicate on its own thread"
    progression = load_progression(state)
    assert {s.fault_id for s in progression.steps} == {"FAULT-1", "FAULT-2"}


# pending duplicate ----------------------------------------------------------------------------------------
def test_pending_duplicate_is_suppressed_into_the_pending_step() -> None:
    flow = _Flow()
    flow.operator("")
    flow.propose(_READ, "4")
    assert flow.operator("what command?") is TurnKind.COMMAND_FOLLOW_UP
    assert flow.propose(_READ, "4")[0] is D.PENDING_STEP_RESOLVED
    assert {"event": "duplicate_of_pending_suppressed", "step_id": flow.steps()[0].step_id} in flow.controller.events
    assert len(flow.steps()) == 1 and len(flow.thread.diagnostic_history) == 1

    # Same action, different target the operator never stated: a different step, rejected.
    flow.operator("ok")
    assert flow.propose(_READ, "5")[0] is D.REJECTED_PENDING_AWAITS_RESULT
    # The operator re-targets the pending (unexecuted) step explicitly: resolved in place.
    flow.operator("sorry, it is unit 5 not 4")
    assert flow.propose(_READ, "5")[0] is D.PENDING_STEP_RESOLVED
    assert [(s.status, step_identity(s).target, s.command) for s in flow.steps()] == [(StepStatus.PRESENTED, "unit=5", "hget Unit=5")]


def test_a_different_step_while_one_is_pending_is_rejected_for_continuity_not_order() -> None:
    flow = _Flow()
    flow.operator("")
    flow.propose(_READ, "4")
    flow.operator("ok")
    decision, result = flow.propose(_RESTART, "4", action="Recover the unit")
    assert decision is D.REJECTED_PENDING_AWAITS_RESULT, "the pending step awaits its result (never a document-order rule)"
    assert result["diagnostic_step"]["action"] == "Check the unit" and result["diagnostic_step"]["command"] is None
    assert len(flow.steps()) == 1


# known-result duplicate --------------------------------------------------------------------------------------
def test_known_result_from_another_document_is_not_requested_again() -> None:
    doc_a = _ev("Radio unit state: `st ru`\n", kid="KID-A", sec="sec-a")
    doc_b = _ev("Check the radio unit: `st ru`\n", kid="KID-B", sec="sec-b")
    flow = _Flow()
    _completed(flow, "st ru", None, "st ru\nRadioUnit=1 OPER=ENABLED", ev=doc_a)
    decision, _ = flow.propose("st ru", None, ev=doc_b)
    assert decision is D.REJECTED_KNOWN_RESULT
    assert step_identity(flow.steps()[0]).action_key != flow.controller._candidate.action_key, "a different governed action..."
    assert step_identity(flow.steps()[0]).result_key == flow.controller._candidate.result_key == "st ru", "...producing the same observation"


# failed / attempted duplicate --------------------------------------------------------------------------------
def test_failed_step_is_not_repeated_until_context_changes() -> None:
    flow = _Flow()
    flow.operator("")
    flow.propose(_READ, "4")
    flow.operator("hget Unit=4\nERROR: unknown command")
    assert flow.steps()[0].status is StepStatus.FAILED
    assert flow.propose(_READ, "4")[0] is D.REJECTED_FAILED_UNCHANGED

    flow.operator("")
    decision, _ = flow.propose(_READ, "4", ev=_ev(version="v2"))
    assert decision is D.RECHECK_PERMITTED
    retry = flow.steps()[-1]
    assert (retry.recheck_of, retry.recheck_reason) == (flow.steps()[0].step_id, "context_changed: governed source version v1 -> v2")


def test_blocked_step_is_not_re_proposed_without_a_reason() -> None:
    flow = _Flow()
    flow.operator("")
    flow.propose(_READ, "4")
    assert flow.operator("I cannot run it, no access") is TurnKind.BLOCKED
    assert flow.propose(_READ, "4")[0] is D.REJECTED_FAILED_UNCHANGED
    assert flow.controller.summary(D.REJECTED_FAILED_UNCHANGED)["duplicate"]["kind"] == "skipped"


# legitimate explicit re-check ---------------------------------------------------------------------------------
@pytest.mark.parametrize("request_text", ["please re-check the unit", "run it again", "can you revalidate that?", "double-check the unit state"])
def test_operator_requested_recheck_is_permitted_and_recorded(request_text: str) -> None:
    flow = _Flow()
    _completed(flow, _READ, "4", "hget Unit=4\nUnit=4 OPER=ENABLED")
    flow.operator(request_text)
    decision, result = flow.propose(_READ, "4")
    assert decision is D.RECHECK_PERMITTED and result["diagnostic_step"]["command"] == "hget Unit=4"
    first, again = flow.steps()
    assert again.recheck_of == first.step_id
    assert again.recheck_reason == f"operator_requested_recheck: operator asked to re-check {first.step_id} ({first.objective}): {request_text}"
    assert again.status is StepStatus.PRESENTED and first.status is StepStatus.COMPLETED
    assert flow.controller.summary(decision)["recheck"]["reason"] == "operator_requested_recheck"


@pytest.mark.parametrize("text", ["the alarm came back again", "what next?", "is it repeating?"])
def test_non_recheck_wording_does_not_unlock_a_repeat(text: str) -> None:
    flow = _Flow()
    _completed(flow, _READ, "4", "hget Unit=4\nUnit=4 OPER=ENABLED")
    flow.operator(text)
    assert flow.propose(_READ, "4")[0] is D.REJECTED_REPEATED_STEP


# post-remediation verification re-check ------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("content", "reason"),
    [(_WITH_VERIFICATION, "governed_post_action_verification"), (_WITHOUT_VERIFICATION, "result_stale_after_state_change")],
)
def test_post_remediation_verification_recheck(content: str, reason: str) -> None:
    ev = _ev(content)
    flow = _Flow()
    _completed(flow, _READ, "4", "hget Unit=4\nUnit=4 OPER=DISABLED", ev=ev)
    flow.operator("")
    assert flow.propose(_RESTART, "4", ev=ev, action="Recover the unit")[0] is D.NEW_STEP

    # While the state change is pending (no result), no verification re-check is accepted.
    flow.operator("ok")
    assert flow.propose(_READ, "4", ev=ev)[0] is D.REJECTED_PENDING_AWAITS_RESULT

    assert flow.operator("acc Unit=4 restart\nrestart ordered, unit back in service") is TurnKind.RESULT
    decision, result = flow.propose(_READ, "4", ev=ev, action="Verify the unit after recovery")
    assert decision is D.RECHECK_PERMITTED and result["diagnostic_step"]["command"] == "hget Unit=4"
    check, restart, verify = flow.steps()
    assert verify.recheck_of == check.step_id and verify.recheck_reason.startswith(f"{reason}: after {restart.step_id}")

    # The verification itself is not repeated again without a new reason.
    flow.operator("hget Unit=4\nUnit=4 OPER=ENABLED")
    assert flow.propose(_READ, "4", ev=ev)[0] is D.REJECTED_REPEATED_STEP


def test_identity_is_structured_not_model_wording() -> None:
    ev = _ev()
    governed = next(a for a in pa.actions_for_evidence(ev, ev.metadata)[0] if a.command_template == _READ)
    resolution, _ = pa.resolve_procedure_action(governed.action_id, issued_ids={governed.action_id}, selected_evidence=[ev],
                                                proposals=[{"name": "unit", "value": "4"}], operator_text="unit 4")
    one = candidate_identity("F", {"action": "Check unit", "command": "hget Unit=999"}, resolution)
    two = candidate_identity("F", {"action": "Something else entirely", "command": None}, resolution)
    assert one.key == two.key and one.result_key == "hget unit=4", "model wording / model command never shape a resolved identity"
    assert candidate_identity("G", {"action": "Check unit"}, resolution).key != one.key


# Required live regressions -------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_live_st_ru_is_not_recommended_again_after_its_result(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await conv.turn("Ericsson 5G: radio unit fault alarm on the node. What should I check?", _propose(_RU, _RU_STEP), "Run st ru.",
                    {"subject_component": "radio unit"})
    t2 = await conv.turn("st ru output:\nRadioUnit=1 OPER=ENABLED", _propose(_CELL, _CELL_STEP), "Run st cell.")
    assert t2["record"]["diagnostic_step"]["command"] == "st cell"

    t3 = await conv.turn("st cell output:\nNRCellDU=1 OPER=DISABLED", _propose(_RU, _RU_STEP), "Run st ru again.")
    assert _decision(t3)["decision"] == "rejected_repeated_step"
    assert t3["record"]["diagnostic_step"] is None and "st ru" not in t3["final"]
    assert _checks(t3) == [(_RU, "executed"), (_CELL, "executed")]

    t4 = await conv.turn("what next?", _propose(_RU, _RU_STEP), "Run st ru.")
    assert (_decision(t4)["turn_kind"], _decision(t4)["decision"]) == ("no_pending", "rejected_repeated_step")
    assert "st ru" not in t4["final"] and _checks(t4) == [(_RU, "executed"), (_CELL, "executed")]

    # Explicit operator revalidation: permitted, recorded, and still governed by Command Authority.
    t5 = await conv.turn("please re-check the radio unit", _propose(_RU, _RU_STEP), "Run st ru.")
    assert _decision(t5)["decision"] == "recheck_permitted"
    assert t5["record"]["diagnostic_step"]["command"] == "st ru" and "st ru" in t5["final"]
    assert _checks(t5) == [(_RU, "executed"), (_CELL, "executed"), (_RU, "recommended")]
    progression = load_progression(t5["state"])
    first_ru, _, again = progression.steps_for(progression.active_fault_id)
    assert again.recheck_of == first_ru.step_id and again.recheck_reason.startswith("operator_requested_recheck")
    assert [s.step_id for s in _pending(t5)] == [again.step_id]


@pytest.mark.asyncio
async def test_live_alt_is_not_recommended_again_on_a_generic_what_next(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    t1 = await conv.turn("Ericsson 5G: radio unit fault alarm on the node. What should I check?", _propose(_ALT, "Validate the active alarms"),
                         "Run alt.", {"subject_component": "radio unit"})
    assert t1["record"]["diagnostic_step"]["command"] == "alt"
    t2 = await conv.turn("alt output:\nMajor RadioUnit fault since 10:02", _propose(_RU, _RU_STEP), "Run st ru.")
    assert _decision(t2)["decision"] == "new_step"
    t3 = await conv.turn("st ru output:\nRadioUnit=1 OPER=DISABLED", _propose(_ALT, "Validate the active alarms"), "Run alt.")
    assert _decision(t3)["decision"] == "rejected_repeated_step"
    t4 = await conv.turn("what next?", _propose(_ALT, "Validate the active alarms"), "Next, run alt.")
    assert (_decision(t4)["turn_kind"], _decision(t4)["decision"]) == ("no_pending", "rejected_repeated_step")
    for turn in (t3, t4):
        assert turn["record"]["diagnostic_step"] is None and "operational_control" not in turn["record"]
        assert not re.search(r"\balt\b", turn["final"])
    assert _checks(t4) == [(_ALT, "executed"), (_RU, "executed")]
