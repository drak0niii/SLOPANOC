"""Structured progression policy (final hardening tranche):

  Part 1  governed document -> extraction -> STRUCTURED action graph -> runtime progression
          (no prose parsing at runtime; ambiguous dependency = UNKNOWN, never invented)
  Part 2  TAE escalation_required = PROPOSAL -> deterministic escalation gate -> server transition
  Part 3  escalation thresholds / re-check / reopen / staleness from a server-owned policy object
  Part 4  explicit, audited PRESENTED -> CANCELLED / SUPERSEDED (never on "what next?")
  Part 5  a re-check request resolves to ONE earlier step, or grants nothing
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer import resolution_gates as gates
from backend.agents.technical_authority_engineer.progression_controller import (
    ProgressionController,
    ProposalDecision,
    TurnKind,
    explicit_objective_change,
    resolve_recheck_target,
)
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.agents.technical_authority_engineer.step_identity import step_identity
from backend.cases.progression_policy import SYSTEM_DEFAULT_POLICY, TroubleshootingProgressionPolicy
from backend.cases.troubleshooting_progression import (
    ProgressionPhase,
    RemediationState,
    ResolutionState,
    StepStatus,
    load_progression,
)
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_progression_resolution_gates import (
    _CRITERIA,
    _DISABLED,
    _ENABLED,
    _HGET,
    _LGET,
    _RESTART,
    _Flow,
    _diagnosed,
    _ev,
    _executed,
    _remediated,
)

D, RT = ProposalDecision, pa.RelationshipType


def _extract(content: str) -> dict[str, pa.ProcedureAction]:
    actions, _ = pa.extract_procedure_actions(knowledge_id="K", version_label="v1", section_id="s", content=content, source_locator="lines:1-9")
    return {a.command_template: a for a in actions}


def _rels(action: pa.ProcedureAction, actions: dict[str, pa.ProcedureAction]) -> list[tuple[str, str, int]]:
    by_id = {a.action_id: t for t, a in actions.items()}
    return [(r.relationship_type.value, by_id[r.related_action_id], r.line) for r in action.relationships]


# =====================================================================================================
# Part 1 -- structured action relationships (MANDATORY / RECOMMENDED / VERIFICATION / UNKNOWN)
# =====================================================================================================
def test_explicit_mandatory_dependency_becomes_a_mandatory_relationship_with_provenance() -> None:
    actions = _extract(
        "Unit state check: `hget Unit=<unit>`\n"
        "Before any recovery, the unit links must be checked: `lget Link=<unit>`\n"
        "Unit recovery: `acc Unit=<unit> restart`\n"
        "After the recovery, verify the unit state again: `hget Unit=<unit>`\n"
    )
    restart = actions["acc Unit=<unit> restart"]
    assert _rels(restart, actions) == [("mandatory_prerequisite", "lget Link=<unit>", 2), ("post_action_verification", "hget Unit=<unit>", 4)]
    mandatory = restart.related(RT.MANDATORY_PREREQUISITE)[0]
    assert (mandatory.rule, mandatory.source_locator, mandatory.text) == (
        "explicit mandatory dependency language", "lines:1-9", "Before any recovery, the unit links must be checked: `lget Link=<unit>`"
    )
    assert restart.mandatory_prerequisite_ids == [actions["lget Link=<unit>"].action_id]
    assert [a.sequence for a in actions.values()] == [0, 1, 2]
    entry = restart.catalog_entry()
    assert (entry["mandatory_prerequisite_action_ids"], entry["recommended_before_action_ids"]) == (restart.mandatory_prerequisite_ids, [])


def test_document_order_alone_never_creates_a_prerequisite() -> None:
    actions = _extract("HC commands:\nalt\nst ru\nst fieldr\nRecovery:\n`acc FieldReplaceableUnit=xxxx restartunit`\n")
    assert actions["acc FieldReplaceableUnit=xxxx restartunit"].relationships == []
    assert actions["acc FieldReplaceableUnit=xxxx restartunit"].sequence > actions["st ru"].sequence, "order is recorded, not required"


@pytest.mark.parametrize(
    ("content", "expected_type", "rule"),
    [
        ("Before any recovery, check the unit links: `lget Link=<unit>`\nRecovery: `acc Unit=<unit> restart`\n",
         RT.RECOMMENDED_BEFORE, "temporal order without obligation"),
        ("You may check the links before the recovery: `lget Link=<unit>`\nRecovery: `acc Unit=<unit> restart`\n",
         RT.RECOMMENDED_BEFORE, "advisory dependency language"),
        ("If required, run `lget Link=<unit>` before recovery\nRecovery: `acc Unit=<unit> restart`\n",
         RT.RECOMMENDED_BEFORE, "advisory dependency language"),
        ("Run `lget Link=<unit>` before `acc Unit=<unit> restart`\n",
         RT.RECOMMENDED_BEFORE, "temporal order without obligation ('<A> before <B>')"),
        ("The links must be checked before restarting: `lget Link=<unit>`\nBoard recovery: `acc Unit=<unit> restart`\nShelf recovery: `acc Shelf=<unit> restart`\n",
         RT.UNKNOWN, "mandatory language does not identify which later action"),
        ("Prerequisite: `lget Link=<unit>`, `hget Unit=<unit>` and `acc Unit=<unit> restart`\n",
         RT.UNKNOWN, "several actions on one dependency line"),
    ],
    ids=["plain-temporal", "may", "if-required", "two-actions-temporal", "which-later-action", "three-actions"],
)
def test_non_mandatory_or_ambiguous_dependencies_are_never_mandatory(content: str, expected_type: pa.RelationshipType, rule: str) -> None:
    actions = _extract(content)
    assert all(a.mandatory_prerequisite_ids == [] for a in actions.values()), "only explicit obligation is ever MANDATORY"
    relationships = [r for a in actions.values() for r in a.related(RT.RECOMMENDED_BEFORE, RT.UNKNOWN)]
    assert relationships and {(r.relationship_type, r.rule) for r in relationships} == {(expected_type, rule)}


def test_mandatory_dependency_between_two_reads_on_one_line() -> None:
    actions = _extract("Cell state check: `st cell`\n`st cell` must be completed before `st pluginunit`.\nPlug-in unit check: `st pluginunit`\n")
    assert _rels(actions["st pluginunit"], actions) == [("mandatory_prerequisite", "st cell", 2)]
    actions = _extract("`st pluginunit` requires `st cell` first\n")
    assert _rels(actions["st pluginunit"], actions) == [("mandatory_prerequisite", "st cell", 1)]
    assert _extract("Run the following checks: `st cell` `st pluginunit`\n")["st pluginunit"].relationships == [], "no connector, no relationship"


def test_runtime_gate_consumes_structure_and_never_reparses_prose(monkeypatch) -> None:
    import backend.agents.technical_authority_engineer.progression_controller as controller_module

    # 1) The section TEXT handed to the runtime gate is blanked: the gate still blocks, from the
    #    structured MANDATORY relationship extracted when the governed evidence was read.
    real_sections = controller_module._governed_sections
    monkeypatch.setattr(controller_module, "_governed_sections", lambda ev: {k: ("", app) for k, (_, app) in real_sections(ev).items()})
    flow = _Flow()
    _diagnosed(flow, prerequisite=False)
    assert flow.propose(_RESTART, action="Recover the unit")[0] is D.REJECTED_PREMATURE_REMEDIATION
    [relationship] = flow.c._gate["prerequisites"]["relationships"]
    assert {k: relationship[k] for k in ("type", "enforced", "rule", "line", "source_locator")} == {
        "type": "mandatory_prerequisite", "enforced": True, "rule": "explicit mandatory dependency language", "line": 2, "source_locator": "lines:1-4"
    }
    # 2) The mandatory wording is still in the governed text but NO relationship was extracted:
    #    the gate does not re-read the prose, so nothing is required.
    monkeypatch.setattr(controller_module, "_governed_sections", real_sections)
    monkeypatch.setattr(pa, "derive_action_relationships", lambda actions, content, source_locator=None: None)
    flow = _Flow()
    _diagnosed(flow, prerequisite=False)
    assert flow.propose(_RESTART, action="Recover the unit")[0] is D.NEW_STEP
    assert flow.c._gate["prerequisites"] == {"met": True, "detail": "no mandatory prerequisite declared", "relationships": []}


@pytest.mark.parametrize(
    "line",
    ["You may check the links before the recovery: `lget Link=<unit>`", "Before any recovery, check the unit links: `lget Link=<unit>`"],
    ids=["unknown-or-advisory", "recommended"],
)
def test_recommended_or_unknown_relationships_never_block_remediation(line: str) -> None:
    import backend.tests.test_progression_resolution_gates as g

    ev = _ev().model_copy(update={"content_snippet": f"Unit state check: `hget Unit=<unit>`\n{line}\nUnit recovery: `acc Unit=<unit> restart`\n"})
    ids = {a.command_template: a.action_id for a in pa.actions_for_evidence(ev, ev.metadata)[0]}
    original = dict(g._IDS)
    g._IDS.update(ids)
    try:
        flow = _Flow()
        flow.operator("unit 4 is down")
        flow.propose(_HGET, ev=ev, action="Check the unit state")
        flow.operator(_DISABLED)
        assert flow.propose(_RESTART, ev=ev, action="Recover the unit")[0] is D.NEW_STEP, "the agent may skip an advisory step"
        prerequisites = flow.c._gate["prerequisites"]
        assert prerequisites["met"] and [r["enforced"] for r in prerequisites["relationships"]] == [False]
        assert prerequisites["detail"] == "no mandatory prerequisite declared (recommended/unknown relationships are advisory, not enforced)"
    finally:
        g._IDS.clear()
        g._IDS.update(original)


# =====================================================================================================
# Part 2 + 3 -- escalation gate and policy
# =====================================================================================================
def _escalation(reason: str = "The TAE thinks this needs Tier 2") -> dict[str, Any]:
    return {"outcome": "escalation_required", "technical_interpretation": "t", "missing_information": [], "diagnostic_step": None, "escalation_reason": reason}


def test_model_escalation_without_recorded_evidence_is_not_a_transition() -> None:
    flow = _Flow()
    flow.operator("unit 4 is down")
    flow.propose(_HGET, action="Check the unit state")  # pending, nothing observed yet
    flow.operator("what next?")
    decision, result = flow.c.evaluate_escalation(_escalation(), [_ev()])
    assert decision == "rejected" and result["outcome"] == "insufficient_evidence" and result["escalation_reason"] is None
    assert "no recorded evidence supports escalation yet" in result["missing_information"][-1]
    assert (flow.fault.phase, flow.fault.resolution, flow.fault.escalations) == (ProgressionPhase.AWAITING_OBSERVATION, ResolutionState.UNRESOLVED, [])
    assert flow.c.pending_step() is not None, "a rejected proposal changes nothing"


def test_evidence_backed_escalation_is_the_agents_call_even_with_untried_diagnostics() -> None:
    """The server does not decide escalation is premature because other governed diagnostics remain
    untried: with recorded evidence and a permitting policy, the agent's proposal is accepted."""
    flow = _Flow()
    _diagnosed(flow, prerequisite=False)  # hget observed; lget (and the verification read) untried
    flow.propose(_LGET, action="Check the unit links")  # a pending step the agent now abandons
    decision, result = flow.c.evaluate_escalation(_escalation("Hardware fault confirmed on site, needs field team"), [_ev()])
    assert decision == "accepted" and result["outcome"] == "escalation_required"
    assert (flow.fault.phase, flow.fault.resolution) == (ProgressionPhase.ESCALATION_REQUIRED, ResolutionState.ESCALATED)
    record = flow.fault.escalations[-1]
    hget = flow.c.progression.steps_for("FAULT-U4")[0]
    assert (record.rule, record.proposer, record.proposed_reason) == (
        "evidence_backed_proposal", "technical_authority_engineer", "Hardware fault confirmed on site, needs field team"
    )
    assert record.evidence_step_ids == [hget.step_id] and record.evidence_result_ids == [hget.result.result_id]
    assert record.policy == {"policy_id": "slopanoc-system-default", "version": "2", "rule": "evidence_backed_proposal"} and record.at is not None
    # The pending step never silently disappears: it is SUPERSEDED with the escalation as reason.
    lget = flow.c.progression.steps_for("FAULT-U4")[1]
    assert lget.status is StepStatus.SUPERSEDED and lget.status_history[-1].reason == "superseded by accepted escalation (evidence_backed_proposal)"


def test_policy_can_refuse_agent_escalation_proposals(monkeypatch) -> None:
    policy = TroubleshootingProgressionPolicy(policy_id="no-agent-escalation", allow_escalation_proposals=False)
    monkeypatch.setattr("backend.agents.technical_authority_engineer.progression_controller.get_progression_policy", lambda: policy)
    flow = _Flow()
    _diagnosed(flow)
    decision, result = flow.c.evaluate_escalation(_escalation(), [_ev()])
    assert decision == "rejected" and "does not accept agent escalation proposals" in result["missing_information"][-1]


def test_knowledge_gap_escalation_requires_server_side_search_and_negative_selection() -> None:
    flow = _Flow()
    flow.operator("unit 4 is down")
    assert flow.c.evaluate_escalation(_escalation(), [], knowledge_gap=False)[0] == "rejected"
    assert flow.c.evaluate_escalation(_escalation(), [], knowledge_gap=True)[0] == "accepted"
    assert flow.fault.escalations[-1].rule == "no_applicable_governed_procedure"


@pytest.mark.parametrize(("threshold", "phase"), [(1, ProgressionPhase.ESCALATION_REQUIRED), (2, ProgressionPhase.REASSESS)])
def test_failed_verification_escalation_threshold_comes_from_policy(threshold: int, phase: ProgressionPhase, monkeypatch) -> None:
    policy = TroubleshootingProgressionPolicy(policy_id="customer-x", version="7", max_failed_verifications=threshold)
    monkeypatch.setattr("backend.agents.technical_authority_engineer.progression_controller.get_progression_policy", lambda: policy)
    flow = _Flow()
    _remediated(flow)
    _executed(flow)
    flow.operator("")
    flow.propose(_HGET, action="Verify the unit state")
    flow.operator(_DISABLED)
    assert flow.fault.phase is phase
    if phase is ProgressionPhase.ESCALATION_REQUIRED:
        record = flow.fault.escalations[-1]
        assert (record.rule, record.proposer, record.policy) == (
            "failed_verifications_reached", "server", {"policy_id": "customer-x", "version": "7", "rule": "failed_verifications_reached"}
        )


def test_policy_has_a_documented_default_and_is_immutable() -> None:
    assert SYSTEM_DEFAULT_POLICY.model_dump() == {
        "policy_id": "slopanoc-system-default", "version": "2", "max_failed_verifications": 2, "allow_operator_requested_recheck": True,
        "allow_reopen_after_resolution": True, "result_staleness": "state_change_or_reopen", "allow_escalation_proposals": True,
        "escalation_requires_recorded_evidence": True,
    }
    with pytest.raises(Exception):
        SYSTEM_DEFAULT_POLICY.max_failed_verifications = 5  # type: ignore[misc]


def test_policy_switches_are_applied_deterministically(monkeypatch) -> None:
    strict = TroubleshootingProgressionPolicy(policy_id="strict", allow_operator_requested_recheck=False, allow_reopen_after_resolution=False,
                                              result_staleness="never")
    monkeypatch.setattr("backend.agents.technical_authority_engineer.progression_controller.get_progression_policy", lambda: strict)
    flow = _Flow()
    _diagnosed(flow)
    flow.operator("please recheck the unit state")
    assert flow.propose(_HGET, action="Check the unit state")[0] is D.REJECTED_REPEATED_STEP, "operator re-check disabled by policy"
    flow.operator("")
    flow.c.evaluate_escalation(_escalation(), [_ev()])
    flow.c.save()
    assert flow.fault.resolution is ResolutionState.ESCALATED
    flow.operator("the alarm came back")
    assert flow.fault.resolution is ResolutionState.ESCALATED and {"event": "reopen_refused", "policy": strict.ref("allow_reopen_after_resolution")} in flow.c.events


# =====================================================================================================
# Part 4 -- explicit cancel / supersede
# =====================================================================================================
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("forget that check, let's investigate the sync alarm", "superseded"),
        ("don't run that restart, check the transport fault instead", "superseded"),
        ("cancel that check and investigate the sync fault", "superseded"),
        ("cancel that check", "cancelled"),
        ("never mind", "cancelled"),
        ("what next?", None),
        ("should I cancel that check?", None),
        ("I cannot run that, no access", None),
        ("check the radio unit", None),
    ],
)
def test_objective_change_is_recognised_only_from_explicit_words(text: str, expected: Optional[str]) -> None:
    assert explicit_objective_change(text) == expected


def test_explicit_supersession_is_an_audited_server_transition_not_a_result() -> None:
    flow = _Flow()
    flow.operator("unit 4 is down")
    flow.propose(_HGET, action="Check the unit state")
    step = flow.steps()[0] if hasattr(flow, "steps") else flow.c.progression.steps_for("FAULT-U4")[0]
    assert flow.operator("forget that check, let's investigate the sync alarm") is TurnKind.OBJECTIVE_CHANGE
    step = flow.c.progression.step(step.step_id)
    assert step.status is StepStatus.SUPERSEDED and step.result is None and step.candidate_observations == []
    assert step.status_history[-1].reason == "operator: forget that check, let's investigate the sync alarm"
    assert flow.c.pending_step() is None and flow.fault.phase is ProgressionPhase.REASSESS
    check = flow.thread.diagnostic_history[0]
    assert (check.status.value, check.observed_result) == ("skipped", "Superseded: forget that check, let's investigate the sync alarm")
    assert flow.thread.trusted_observation_texts() == []
    # A superseded step is not "already performed": it may be proposed again later.
    flow.operator("")
    assert flow.propose(_HGET, action="Check the unit state")[0] is D.NEW_STEP


def test_cancelling_a_pending_remediation_marks_it_not_performed() -> None:
    flow = _Flow()
    _remediated(flow)
    assert flow.operator("don't run that restart, check the transport fault instead") is TurnKind.OBJECTIVE_CHANGE
    assert flow.c.progression.step(flow.remediation.step_id).status is StepStatus.SUPERSEDED
    assert flow.remediation.state is RemediationState.NOT_PERFORMED


def test_vague_follow_up_never_cancels_the_pending_step() -> None:
    flow = _Flow()
    flow.operator("unit 4 is down")
    flow.propose(_HGET, action="Check the unit state")
    for text in ("what next?", "ok", "what's next cmd?"):
        assert flow.operator(text) is not TurnKind.OBJECTIVE_CHANGE
        assert flow.c.pending_step() is not None and flow.c.pending_step().status is StepStatus.PRESENTED


def test_cancel_targets_the_previous_thread_and_leaves_other_threads_intact() -> None:
    state: dict[str, Any] = {}
    radio = TroubleshootingState(fault_id="FAULT-RADIO", symptom_summary="radio")
    sync = TroubleshootingState(fault_id="FAULT-SYNC", symptom_summary="sync")
    for thread, template in ((sync, _LGET), (radio, _HGET)):
        c = ProgressionController(state, thread, session_id="s1")
        c.classify_turn("")
        resolution, _ = pa.resolve_procedure_action(_IDS_ALL[template], issued_ids={_IDS_ALL[template]}, selected_evidence=[_ev()],
                                                    proposals=[{"name": "unit", "value": "4"}], operator_text="unit 4")
        proposal = {"outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
                    "diagnostic_step": {"action": f"Check {thread.fault_id}", "reason": "r", "expected_evidence": "e", "command": resolution.candidate.command,
                                        "command_source": _ev().source_id, "restrictions": [], "procedure_action_id": _IDS_ALL[template]}}
        decision, result = c.evaluate_proposal(proposal, resolution, [_ev()])
        c.record(decision, result, c.check_id_for(decision, f"chk-{thread.fault_id}"), selected_evidence_ids=[], applicability={})
        c.save()
    # Switching to the sync thread WITHOUT a cancel leaves the radio step pending...
    c = ProgressionController(state, sync, session_id="s1")
    c.classify_turn("let's look at the sync fault")
    c.apply_operator_turn("let's look at the sync fault", previous_fault_id="FAULT-RADIO")
    progression = c.progression
    assert [s.status for s in progression.steps_for("FAULT-RADIO")] == [StepStatus.PRESENTED]
    # ...an explicit cancel while switching supersedes ONLY the radio step; sync keeps its own.
    c.classify_turn("forget that check, let's investigate the sync fault")
    c.apply_operator_turn("forget that check, let's investigate the sync fault", previous_fault_id="FAULT-RADIO")
    assert [s.status for s in progression.steps_for("FAULT-RADIO")] == [StepStatus.SUPERSEDED]
    assert [s.status for s in progression.steps_for("FAULT-SYNC")] == [StepStatus.PRESENTED]


# =====================================================================================================
# Part 5 -- targeted re-check
# =====================================================================================================
def _three_checks() -> _Flow:
    flow = _Flow()
    _diagnosed(flow)  # hget (unit state) + lget (unit links), both completed
    return flow


def test_generic_recheck_grants_nothing_and_asks_which_check() -> None:
    flow = _three_checks()
    flow.operator("recheck")
    decision, result = flow.propose(_HGET, action="Check the unit state")
    assert decision is D.REJECTED_REPEATED_STEP
    assert "does not identify one earlier check" in result["missing_information"][-1]
    assert flow.c.summary(decision)["recheck_request"]["status"] == "unresolved"


def test_specific_recheck_resolves_the_exact_prior_step_and_records_it() -> None:
    flow = _three_checks()
    hget = flow.c.progression.steps_for("FAULT-U4")[0]
    flow.operator("recheck the unit state")
    decision, _ = flow.propose(_HGET, action="Check the unit state")
    assert decision is D.RECHECK_PERMITTED
    again = flow.c.progression.steps_for("FAULT-U4")[-1]
    assert again.recheck_of == hget.step_id and again.recheck_reason.startswith(f"operator_requested_recheck: operator asked to re-check {hget.step_id}")
    assert flow.c.summary(decision)["recheck_request"]["target"] == hget.step_id


def test_recheck_of_one_step_never_unlocks_another() -> None:
    flow = _three_checks()
    flow.operator("recheck the unit state")
    decision, result = flow.propose(_LGET, action="Check the unit links")
    assert decision is D.REJECTED_REPEATED_STEP and "refers to 'Check the unit state'" in result["missing_information"][-1]


def test_ambiguous_recheck_target_is_not_resolved() -> None:
    flow = _three_checks()
    request = resolve_recheck_target("recheck the unit", flow.c.progression.steps_for("FAULT-U4"))
    assert request["status"] == "ambiguous" and request["target"] is None
    flow.operator("recheck the unit")
    assert flow.propose(_HGET, action="Check the unit state")[0] is D.REJECTED_REPEATED_STEP


def test_pronoun_recheck_means_only_the_most_recent_step() -> None:
    flow = _three_checks()
    flow.operator("run it again")
    assert flow.propose(_HGET, action="Check the unit state")[0] is D.REJECTED_REPEATED_STEP, "hget is not the most recent step"
    flow.operator("run it again")
    assert flow.propose(_LGET, action="Check the unit links")[0] is D.RECHECK_PERMITTED


# =====================================================================================================
# End to end through the real TAE tool
# =====================================================================================================
from backend.tests.test_progression_resolution_gates import _IDS as _IDS_ALL  # noqa: E402
from backend.tests.test_live_sequence_baseband_then_rru_reset import _Conversation  # noqa: E402
from backend.tests.test_progression_controller import _ALT, _CATALOG, _RU, _RU_STEP, _mop, _search, _select  # noqa: E402


def _tae(outcome: str, action_id: Optional[str] = None, action: str = "", reason: Optional[str] = None) -> list[types.Part]:
    step = None if outcome != "recommended" else {"action": action, "reason": "r", "expected_evidence": "Output", "command": None,
                                                   "command_source": None, "restrictions": [], "procedure_action_id": action_id, "parameter_values": []}
    return [types.Part.from_text(text=json.dumps({"outcome": outcome, "technical_interpretation": "t", "missing_information": [],
                                                  "diagnostic_step": step, "escalation_reason": reason}))]


def _event(turn: dict[str, Any]) -> dict[str, Any]:
    return [e for e in turn["trace"]["operational_events"] if e.get("stage") == "progression"][-1]


def _governed(*tail: list[types.Part]) -> list[list[types.Part]]:
    return [_search("radio unit fault"), _select("KID-RADIO-FAULT", "sec-checks"), _CATALOG, *tail]


@pytest.mark.asyncio
async def test_e2e_model_escalation_is_gated_and_supersession_switches_objective(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    t1 = await conv.turn("Ericsson 5G: radio unit fault alarm on the node. What should I check?", _governed(_tae("recommended", _RU, _RU_STEP)),
                         "Run st ru.", {"subject_component": "radio unit"})
    radio_fault = t1["state"]["troubleshooting_state"]["fault_id"]

    # The TAE proposes escalation while the RU check is pending: no server transition.
    t2 = await conv.turn("what next?", _governed(_tae("escalation_required", reason="Escalate to Tier 2 now")), "Escalate to Tier 2 now.")
    assert t2["record"]["outcome"] == "insufficient_evidence" and _event(t2)["escalation"]["accepted"] is False
    fault = load_progression(t2["state"]).faults[radio_fault]
    assert (fault.resolution, fault.escalations) == (ResolutionState.UNRESOLVED, [])
    assert "Escalation is not yet justified" in t2["record"]["missing_information"][-1]

    # Explicit supersession while switching subject: the RU step is SUPERSEDED (audited), no fake result.
    t3 = await conv.turn("cancel that check and investigate the synchronization problem", _governed(_tae("recommended", _ALT, "Validate the synchronization alarms")),
                         "Run alt.", {"subject_component": "synchronization"})
    progression = load_progression(t3["state"])
    ru = progression.steps_for(radio_fault)[0]
    assert ru.status is StepStatus.SUPERSEDED and ru.result is None
    assert ru.status_history[-1].reason == "operator: cancel that check and investigate the synchronization problem"
    sync_fault = t3["state"]["troubleshooting_state"]["fault_id"]
    assert sync_fault != radio_fault and [s.command for s in progression.steps_for(sync_fault)] == ["alt"]
    assert _event(t3)["objective_change"]["event"] == "pending_step_superseded"
