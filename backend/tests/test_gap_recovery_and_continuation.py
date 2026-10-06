"""Deterministic gap recovery + active-investigation continuation (live session 2632dab0).

Live: after `alt`, the specialist chose "synchronization status" as the next evidence requirement;
discovery found governed knowledge but no approved method -> governed acquisition GAP (correct) ->
the gap text replaced the whole answer -> "what next?" (classified focus=switch) -> the same
requirement and gap again -> no alternative governed progression although the selected procedure
still offered unperformed governed reads.

Invariants under test:
    a gap exhausts a BRANCH, not the investigation: the server determines the valid governed
    alternatives of the fault (never invents one, never chooses for the specialist); the gap stays
    recorded; it is terminal only when no valid alternative exists;
    the same gapped requirement re-proposed with nothing materially new reuses the gap (no new
    discovery attempt, no retry loop); new trusted evidence may reopen it;
    a message that introduces no technical subject continues the active fault (never a switch);
    an alternative passes exactly the same authority path (fresh AVAILABLE, explicit SELECTED,
    MATCH, ProcedureAction, Command Authority, egress).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.evidence_acquisition import (
    AcquisitionOutcome,
    DiscoveryState,
    GovernedActionState,
    decide_acquisition,
    materially_new_context,
    requirement_from_proposal,
)
from backend.agents.technical_authority_engineer.gap_recovery import (
    NON_TERMINAL_GAP_NOTE,
    proposed_branch_requirement,
    viable_governed_alternatives,
)
from backend.agents.technical_authority_engineer.progression_controller import ProgressionController, TurnKind
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.agents.technical_authority_engineer.turn_request import build_turn_request_contract, is_continuation_only
from backend.cases.evidence_identity import EvidenceIdentity
from backend.cases.evidence_model import (
    AcquisitionGap,
    AcquisitionHint,
    DiscoveryAttempt,
    EvidenceKind,
    EvidenceRequirement,
    GapReason,
    GapStatus,
    RequirementStatus,
)
from backend.cases.troubleshooting_progression import (
    FaultProgression,
    ResultSource,
    StepStatus,
    TroubleshootingProgression,
    TroubleshootingStep,
)
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_applicability_blocked_governed_action import _fc, _payload, _trace, conversation  # noqa: F401
from backend.tests.test_evidence_acquisition_architecture import ALARM_OUTPUT, SYNC_REQ
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tools.knowledge.diagnostic_trace import format_diagnostic_trace


class _Mop:
    def __init__(self, kid: str, content: str) -> None:
        self.kid, self.ver, self.content = kid, "v1", content
        self.section = f"{kid}:v1:section-0000"
        self.filename = f"{kid}.docx"
        self.identity = EvidenceIdentity(knowledge_id=kid, version_label="v1", section_id=self.section)

    def knowledge(self) -> KnowledgeObject:
        return KnowledgeObject(
            knowledge_id=self.kid, document_type=KnowledgeDocumentType.MOP, title=f"{self.kid} Health Procedure",
            version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
            lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
            source=KnowledgeSource(source_system="test", source_id=self.filename, display_name=self.kid),
            sections=[KnowledgeSection(section_id=self.section, knowledge_id=self.kid, heading=None, sequence=0, content=self.content, source_locator="l")],
        )

    def select(self) -> list[types.Part]:
        return _fc("knowledge_select_evidence", {"selections": [self.identity.selection_key()]})

    def action_id(self, template: str) -> str:
        return pa._action_id(self.kid, "v1", self.section, template)


FULL = _Mop("HEALTH-MOP", "Node health check\nHC Commands:\nalt\nst ru\n")
ALT_ONLY = _Mop("ALARM-MOP", "Node alarm check\nHC Commands:\nalt\n")
SEARCH = _fc("knowledge_search", {"query_text": "node health check"})
SELECT_NONE = _fc("knowledge_select_evidence", {"selections": []})
SYNC_GAP = [types.Part.from_text(text=json.dumps({
    "outcome": "insufficient_evidence", "technical_interpretation": "Synchronization evidence is needed next.",
    "verified_evidence_citations": [], "missing_information": [], "required_evidence": [SYNC_REQ],
}))]


def _alt_step(mop: _Mop) -> list[types.Part]:
    return _payload({"action": "Check active alarms on the node.", "reason": "r", "expected_evidence": "Active alarm list",
                     "command": "alt", "command_source": mop.filename, "restrictions": []})


def _governed(mop: _Mop, template: str) -> list[types.Part]:
    return _payload({"action": "Check the radio unit state.", "reason": "Link failure on a radio unit was reported.",
                     "expected_evidence": "Radio unit operational state", "procedure_action_id": mop.action_id(template),
                     "command": None, "command_source": None, "restrictions": []},
                    "The radio unit link failure is checked next.")


def _events(turn: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return [e for e in _trace(turn).get("operational_events", []) if e.get("stage") == stage]


def _authorized(turn: dict[str, Any], command: str) -> list[dict[str, Any]]:
    return [c for c in _trace(turn).get("command_authority", []) if c["decision"] == "authorized" and c["command"] == command]


def _sync_requirement(progression: TroubleshootingProgression) -> EvidenceRequirement:
    (requirement,) = [r for r in progression.evidence_requirements if r.capability == "node_sync_status"]
    return requirement


async def _alt_completed(conv: Any, mop: _Mop) -> None:
    t1 = await conv.turn("how do I troubleshoot this node fault?", [SEARCH, mop.select(), _alt_step(mop)], "Run `alt`.")
    assert t1["records"][-1]["diagnostic_step"]["command"] == "alt"


# =============================================================================================
# A + G. Gapped branch + a valid governed alternative -> the alternative progresses, fully governed
# =============================================================================================


@pytest.mark.asyncio
async def test_a_gapped_branch_progresses_to_a_governed_alternative(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(FULL.knowledge())
    await _alt_completed(conv, FULL)
    # The alarm output completes `alt`; the specialist asks for sync evidence that no procedure obtains.
    # The server offers the remaining governed branch; the specialist SELECTS its source and chooses it.
    t2 = await conv.turn(ALARM_OUTPUT, [SEARCH, SELECT_NONE, SYNC_GAP, FULL.select(), _governed(FULL, "st ru")], "Run `st ru`.")
    assert t2["tae_calls"] == 5, "one bounded recovery request"
    progression = t2["progression"]
    # A stays recorded: requirement unsatisfied, gap open (never hidden or deleted).
    sync = _sync_requirement(progression)
    assert sync.status is RequirementStatus.UNSATISFIED and sync.blocking_reason is GapReason.NO_APPROVED_ACQUISITION_ACTION
    (gap,) = [g for g in progression.acquisition_gaps if g.requirement_id == sync.requirement_id]
    assert gap.status is GapStatus.OPEN and len(gap.discovery_attempts) == 1
    # B progresses: re-derived from THIS run's SELECTED evidence, resolved, authorized, presented.
    record = t2["records"][-1]
    recovery = record["gap_recovery"]
    assert recovery["offered"] is True and recovery["chosen"] == FULL.action_id("st ru") and recovery["chosen_issued"] is True
    assert recovery["terminal"] is False and [a["procedure_action_id"] for a in recovery["alternatives"]] == [FULL.action_id("st ru")]
    resolution = _trace(t2)["action_resolutions"][-1]
    assert (resolution["action_id"], resolution["status"], resolution["command_authority"]) == (FULL.action_id("st ru"), "resolved", "authorized")
    assert _authorized(t2, "st ru") and record["diagnostic_step"]["command"] == "st ru"
    alt_step, ru_step = progression.steps
    assert alt_step.status is StepStatus.COMPLETED and ru_step.status is StepStatus.PRESENTED and ru_step.procedure_action_id == FULL.action_id("st ru")
    assert "`st ru`" in t2["final"] and "No approved governed procedure" not in t2["final"]
    # G: egress kept it only because it was authorized in THIS run.
    egress = _trace(t2).get("command_egress") or {}
    assert any(c.get("candidate") == "st ru" and c.get("decision") == "kept" for c in egress.get("candidates") or [])
    # Observability: the new CONTINUATION / PROGRESSION / GAP RECOVERY diagnostics.
    rendered = format_diagnostic_trace(_trace(t2))
    assert "CONTINUATION kind=result_provided" in rendered and f"PROGRESSION fault={progression.active_fault_id}" in rendered
    assert f"REQUIREMENT {sync.requirement_id} status=unsatisfied blocking=no_approved_acquisition_action gap={gap.gap_id}" in rendered
    assert f"GAP RECOVERY requirement={sync.requirement_id} gap={gap.gap_id} reason=no_approved_acquisition_action" in rendered
    assert f"NEXT chosen={FULL.action_id('st ru')} issued=True terminal=False" in rendered

    # E: "what do you suggest?" continues the SAME fault (never a focus switch) with the pending step.
    t3 = await conv.turn("what do you suggest?", [FULL.select(), _governed(FULL, "st ru")], "Run `st ru`.")
    (request,) = _trace(t3)["turn_requests"]
    assert request["focus"] == "continue" and request["troubleshooting_thread"]["decision"] == "continued"
    assert request["troubleshooting_thread"]["active_fault_id"] == progression.active_fault_id
    (continuation,) = _events(t3, "continuation")
    assert (continuation["kind"], continuation["rule"]) == ("generic_continuation", "generic_continuation")
    assert _authorized(t3, "st ru")


@pytest.mark.asyncio
async def test_g_an_offered_alternative_without_explicit_selection_gets_no_authority(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(FULL.knowledge())
    await _alt_completed(conv, FULL)
    t2 = await conv.turn(ALARM_OUTPUT, [SEARCH, SELECT_NONE, SYNC_GAP, _governed(FULL, "st ru")], "Run `st ru`.")
    recovery = t2["records"][-1]["gap_recovery"]
    assert recovery["chosen"] == FULL.action_id("st ru") and recovery["chosen_issued"] is False, "never issued from AVAILABLE"
    states = {r["section_id"]: r["selection_state"] for s in _trace(t2)["searches"] for r in s["results"]}
    assert states[FULL.section] == "AVAILABLE", "AVAILABLE is never promoted"
    assert not _authorized(t2, "st ru") and not (t2["records"][-1].get("diagnostic_step") or {}).get("command")
    assert "`st ru`" not in t2["final"]
    # The exhausted branch is still recorded.
    assert t2["progression"].open_gap(_sync_requirement(t2["progression"]).requirement_id) is not None


# =============================================================================================
# B + H. Gapped branch, no valid alternative -> the gap is the terminal limitation; nothing invented
# =============================================================================================


@pytest.mark.asyncio
async def test_b_h_no_alternative_makes_the_gap_terminal_without_inventing_a_method(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(ALT_ONLY.knowledge())
    await _alt_completed(conv, ALT_ONLY)
    t2 = await conv.turn(ALARM_OUTPUT, [SEARCH, SELECT_NONE, SYNC_GAP], "Okay.")
    assert t2["tae_calls"] == 3, "no recovery request: nothing valid to offer"
    record = t2["records"][-1]
    recovery = record["gap_recovery"]
    assert recovery["offered"] is False and recovery["alternatives"] == [] and recovery["terminal"] is True
    assert recovery["discovery"] == "searched", "one fresh governed search looked for another branch"
    assert record["evidence_acquisition"]["outcome"] == "gap" and record["evidence_acquisition"]["terminal"] is True
    assert t2["final"].startswith("No approved governed procedure") and NON_TERMINAL_GAP_NOTE not in t2["final"]
    assert not [c for c in _trace(t2).get("command_authority", []) if c["decision"] == "authorized"]
    assert not (record.get("diagnostic_step") or {}).get("command")


@pytest.mark.asyncio
async def test_declined_alternative_keeps_the_gap_non_terminal(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(FULL.knowledge())
    await _alt_completed(conv, FULL)
    t2 = await conv.turn(ALARM_OUTPUT, [SEARCH, SELECT_NONE, SYNC_GAP, SYNC_GAP], "Okay.")
    record = t2["records"][-1]
    assert record["gap_recovery"]["offered"] is True and record["gap_recovery"]["terminal"] is False
    assert record["evidence_acquisition"]["terminal"] is False and t2["final"].endswith(NON_TERMINAL_GAP_NOTE)
    sync = _sync_requirement(t2["progression"])
    (gap,) = t2["progression"].acquisition_gaps
    assert gap.requirement_id == sync.requirement_id and len(gap.discovery_attempts) == 1, "recorded once, not twice"


# =============================================================================================
# C. The same gapped requirement again -> gap reused, no new discovery attempt, alternative offered
# =============================================================================================


@pytest.mark.asyncio
async def test_c_repeated_gapped_requirement_reuses_the_gap_and_offers_the_alternative(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(FULL.knowledge())
    await _alt_completed(conv, FULL)
    t2 = await conv.turn(ALARM_OUTPUT, [SEARCH, SELECT_NONE, SYNC_GAP, SYNC_GAP], "Okay.")
    sync = _sync_requirement(t2["progression"])
    (gap,) = t2["progression"].acquisition_gaps
    # "what next?": the specialist proposes the SAME requirement again (no search of its own).
    t3 = await conv.turn("what next?", [SYNC_GAP, FULL.select(), _governed(FULL, "st ru")], "Run `st ru`.")
    recovery = t3["records"][-1]["gap_recovery"]
    assert recovery["repeated"] is True and recovery["requirement_id"] == sync.requirement_id and recovery["gap_id"] == gap.gap_id
    assert recovery["chosen"] == FULL.action_id("st ru") and _authorized(t3, "st ru")
    (same_gap,) = t3["progression"].acquisition_gaps
    assert same_gap.gap_id == gap.gap_id and len(same_gap.discovery_attempts) == 1, "no new discovery attempt, no retry loop"
    assert [r.requirement_id for r in t3["progression"].evidence_requirements if r.capability == "node_sync_status"] == [sync.requirement_id]
    assert "`st ru`" in t3["final"]


# =============================================================================================
# D. Materially new evidence reopens a gapped requirement (unit)
# =============================================================================================


def _gapped(at: datetime) -> tuple[TroubleshootingProgression, EvidenceRequirement, AcquisitionGap]:
    progression = TroubleshootingProgression()
    progression.add_fault(FaultProgression(fault_id="F1", symptom_summary="fault"))
    requirement = requirement_from_proposal(SYNC_REQ, "F1")
    progression.add_requirement(requirement)
    gap = AcquisitionGap(
        fault_id="F1", requirement_id=requirement.requirement_id, requirement_description=requirement.description,
        gap_reason=GapReason.NO_APPROVED_ACQUISITION_ACTION,
        discovery_attempts=[DiscoveryAttempt(run_id="run-1", outcome="no_approved_acquisition_action", at=at,
                                             consulted=[{"identity": FULL.identity, "applicability": "match"}])],
    )
    progression.record_acquisition_gap(gap)
    return progression, requirement, gap


def _discovery(*identities: tuple[EvidenceIdentity, str]) -> DiscoveryState:
    return DiscoveryState(governed_search_performed=True, searches=[{"status": "ok"}],
                          available=[(i.canonical, o) for i, o in identities], available_identities=list(identities))


@pytest.mark.asyncio
async def test_d_new_trusted_evidence_reopens_a_gapped_requirement() -> None:
    before = datetime.now(timezone.utc) - timedelta(minutes=5)
    progression, requirement, gap = _gapped(before)
    same = _discovery((FULL.identity, "match"))
    assert not materially_new_context(gap, requirement, same, progression, "run-2")
    repeat = await decide_acquisition(requirement, progression=progression, action=GovernedActionState(), discovery=same, run_id="run-2")
    assert repeat.outcome == AcquisitionOutcome.GAP and repeat.repeated and repeat.attempt is None

    # A new TRUSTED result for the fault after the last attempt -> reconsidered (a new attempt).
    step = progression.append_step(TroubleshootingStep(fault_id="F1", objective="o", expected_evidence="e"))
    progression.set_step_status(step.step_id, StepStatus.VALIDATED)
    progression.set_step_status(step.step_id, StepStatus.PRESENTED)
    progression.record_step_result(step.step_id, "Sync=1 LOCKED", ResultSource.OPERATOR_MESSAGE, status=StepStatus.OBSERVED)
    assert materially_new_context(gap, requirement, same, progression, "run-2")
    reopened = await decide_acquisition(requirement, progression=progression, action=GovernedActionState(), discovery=same, run_id="run-2")
    assert reopened.outcome == AcquisitionOutcome.GAP and not reopened.repeated and reopened.attempt is not None

    # A new consulted governed source (or a new applicability outcome) or a new operator hint also counts.
    fresh, requirement2, gap2 = _gapped(before)
    other = EvidenceIdentity(knowledge_id="OTHER", version_label="v2", section_id="s:9")
    assert materially_new_context(gap2, requirement2, _discovery((other, "match")), fresh, "run-2")
    assert materially_new_context(gap2, requirement2, _discovery((FULL.identity, "unknown")), fresh, "run-2")
    requirement2.acquisition_hints.append(AcquisitionHint(requirement_id=requirement2.requirement_id, value="syncstatus"))
    assert materially_new_context(gap2, requirement2, same, fresh, "run-2")
    # Never twice in the same run.
    assert not materially_new_context(gap2, requirement2, _discovery((other, "match")), fresh, "run-1")


# =============================================================================================
# E + F. Continuation vs genuine new objective (contract + turn classification)
# =============================================================================================

_ACTIVE = SimpleNamespace(symptom_summary="how can i troubleshoot ESS Service Unavailable ?",
                          diagnostic_history=[SimpleNamespace(action="Check for active alarms on the node.")], working_hypothesis=None)


@pytest.mark.parametrize("text", ["what next?", "what do you suggest?", "continue", "okay, continue", "and now?", "go ahead",
                                  "what should I do now?", "okie, and now what you suggest i do ?"])
def test_e_contentless_continuation_stays_in_the_active_fault(text: str) -> None:
    assert is_continuation_only(text)
    for caller in ({}, {"continues_active_objective": False}, {"requested_operation": "suggest"}):
        contract = build_turn_request_contract(caller, text, _ACTIVE)
        assert contract.focus == "continue" and contract.diagnostic_objective.startswith("Continue the active investigation"), (text, caller)
    claimed = build_turn_request_contract({"continues_active_objective": False}, text, _ACTIVE)
    assert {"field": "continues_active_objective", "value": "false", "reason": "the operator's latest message introduces no new subject"} in claimed.discarded_fields
    controller = _pending_controller()
    assert controller.classify_turn(text) is TurnKind.COMMAND_FOLLOW_UP and controller.mechanism_requested is False


@pytest.mark.parametrize("text", ["now troubleshoot the TimeSync alarm instead", "check another node",
                                  "help me with External Link Failure", "open incident INC123"])
def test_f_genuine_new_objective_is_preserved(text: str) -> None:
    assert not is_continuation_only(text)
    contract = build_turn_request_contract({"continues_active_objective": False}, text, _ACTIVE)
    assert contract.focus == "switch"
    assert not any(d.get("field") == "continues_active_objective" for d in contract.discarded_fields)


def _pending_controller() -> ProgressionController:
    progression = TroubleshootingProgression()
    controller = ProgressionController({}, TroubleshootingState(fault_id="F-1", symptom_summary="ru"), progression=progression)
    step = progression.append_step(TroubleshootingStep(fault_id="F-1", objective="Check the radio unit state.", command="st ru",
                                                       expected_evidence="Radio unit operational state"))
    progression.set_step_status(step.step_id, StepStatus.VALIDATED)
    progression.set_step_status(step.step_id, StepStatus.PRESENTED)
    return controller


# =============================================================================================
# Alternative candidate set: server-determined, never invented (unit)
# =============================================================================================


def _evidence(mop: _Mop, applicability: str = "match", lifecycle: str = "approved", content: Optional[str] = None) -> EvidenceReference:
    return EvidenceReference(
        source_id=mop.identity.canonical, source_type="governed_knowledge", title="t", content_snippet=content if content is not None else mop.content,
        metadata={**mop.identity.selection_key(), "lifecycle_status": lifecycle, "applicability_outcome": applicability, "document_type": "mop"},
    )


def test_alternatives_are_only_valid_governed_reads_this_fault_has_not_performed() -> None:
    progression = TroubleshootingProgression()
    progression.add_fault(FaultProgression(fault_id="F1", symptom_summary="fault"))
    alternatives, _ = viable_governed_alternatives([_evidence(FULL)], progression, "F1")
    assert [a.action.command_template for a in alternatives] == ["alt", "st ru"]
    assert all(set(a.model_view()) == {"procedure_action_id", "intent", "description", "procedure", "section", "selection_key", "required_parameters"}
               for a in alternatives), "identity and purpose only: no template, no command field"
    # Performed (by action id, or the same fixed command's known observation) -> excluded.
    done = progression.append_step(TroubleshootingStep(fault_id="F1", objective="alarms", command="alt", procedure_action_id=None))
    progression.set_step_status(done.step_id, StepStatus.VALIDATED)
    progression.set_step_status(done.step_id, StepStatus.PRESENTED)
    alternatives, excluded = viable_governed_alternatives([_evidence(FULL)], progression, "F1")
    assert [a.action.command_template for a in alternatives] == ["st ru"]
    assert {"procedure_action_id": FULL.action_id("alt"), "reason": "already_performed_or_known"} in excluded
    # Never from non-MATCH, unapproved or empty evidence; never a state change; nothing invented.
    for evidence in ([_evidence(FULL, "unknown")], [_evidence(FULL, "not_applicable")], [_evidence(FULL, lifecycle="candidate")], []):
        assert viable_governed_alternatives(evidence, progression, "F1")[0] == []
    restart = [_evidence(FULL, content="Reset:\nacc FieldReplaceableUnit=xxxx restartunit\n")]
    assert viable_governed_alternatives(restart, progression, "F1")[0] == []
    # A cancelled step's action is a valid branch again.
    progression.set_step_status(done.step_id, StepStatus.CANCELLED)
    assert [a.action.command_template for a in viable_governed_alternatives([_evidence(FULL)], progression, "F1")[0]] == ["alt", "st ru"]


def test_proposed_branch_requirement_only_for_method_less_acquirable_needs() -> None:
    requirement, from_mechanism = proposed_branch_requirement({"outcome": "insufficient_evidence", "required_evidence": [SYNC_REQ]}, "F1")
    assert requirement is not None and requirement.kind is EvidenceKind.DIAGNOSTIC_RESULT and not from_mechanism
    mechanism, from_mechanism = proposed_branch_requirement(
        {"outcome": "insufficient_evidence", "missing_information": ["The specific command to retrieve the node synchronization status."]}, "F1")
    assert mechanism is not None and from_mechanism
    assert proposed_branch_requirement({"outcome": "recommended", "diagnostic_step": {"action": "a", "command": "alt"}}, "F1") == (None, False)
    assert proposed_branch_requirement({"outcome": "recommended", "diagnostic_step": {"action": "a", "procedure_action_id": "pa-1"}}, "F1") == (None, False)
    assert proposed_branch_requirement({"outcome": "escalation_required"}, "F1") == (None, False)
    observation, _ = proposed_branch_requirement(
        {"outcome": "insufficient_evidence", "required_evidence": [{"kind": "observation", "description": "Cabinet LED colour"}]}, "F1")
    assert observation is None, "a manual observation is not a governed acquisition branch"
