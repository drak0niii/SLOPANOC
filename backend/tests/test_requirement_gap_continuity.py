"""Requirement continuity, gap continuity, operator acquisition hints, turn-failure observability.

Live defect (session b5374a6e): one unresolved need ("node synchronization state") became three
EvidenceRequirements (random ids, one per follow-up) and three AcquisitionGaps; "I normally use
syncstatus" became a NEW requirement whose description named a command; a failed Team Manager turn
was swallowed without any logged cause.

Now: the server continues the OPEN requirement of the same fault (identity is server-owned: same
fault + open + compatible kind + normalized capability / content, a validated specialist reference,
or a mechanism-only follow-up when exactly one acquirable need is outstanding); a requirement has at
most one OPEN gap and further discovery passes are attempts on it; an operator's "I use X" is an
untrusted AcquisitionHint on the open requirement (search input only, never authority); a failed
turn is logged with its identifiers, stage and stack trace while the operator still sees only the
safe message.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.genai import types

from backend.agents.technical_authority_engineer.evidence_acquisition import (
    AcquisitionOutcome,
    DiscoveryState,
    GovernedActionState,
    decide_acquisition,
    detect_acquisition_hint,
    evidence_description,
    hint_target,
    make_hint,
    match_open_requirement,
    refine_requirement,
    requirement_from_proposal,
    semantic_tokens,
)
from backend.agents.technical_authority_engineer.progression_controller import ProgressionController  # noqa: F401 (import check)
from backend.api.chat_service import ChatService, _runner_stage_after
from backend.api.streaming_events import StreamEventType
from backend.cases.evidence_identity import EvidenceIdentity
from backend.cases.evidence_model import (
    AcquisitionGap,
    AcquisitionType,
    AuthorityStatus,
    CandidateAvailability,
    DiscoveryAttempt,
    EvidenceKind,
    EvidenceRequirement,
    GapReason,
    GapStatus,
    HintType,
    RequirementStatus,
)
from backend.cases.service import CaseService
from backend.cases.troubleshooting_progression import FaultProgression, StepStatus, TroubleshootingProgression
from backend.tests.test_applicability_blocked_governed_action import CATALOG, DOC, _Doc, _trace
from backend.tests.test_clarification_continuity import _Conversation, _fc
from backend.tests.test_evidence_acquisition_architecture import ALARM_OUTPUT, COMMAND_QUESTION, _insufficient, _to_alt_output
from backend.tests.test_evidence_acquisition_architecture import conversation  # noqa: F401 (fixture)
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)

SYNC_SEARCH = _fc("knowledge_search", {"query_text": "ESS Service Unavailable synchronization"})
HINT_TEXT = "I normally use syncstatus"
# Exact live shapes (session b5374a6e) of the three follow-up proposals.
LIVE_T3_REQUIRED = {
    "kind": "diagnostic_result",
    "description": "Output showing the current synchronization status of the node, including details of active clock sources and their health.",
    "capability": "node_sync_status",
}
LIVE_T4_REQUIRED = {"kind": "diagnostic_result", "description": "the synchronization source for an Ericsson 4G node"}
LIVE_T5_REQUIRED = {
    "kind": "diagnostic_result",
    "description": "Output of the node synchronization status check command, including the exact command used and its complete output.",
    "capability": "node_sync_status",
}


class _SyncDoc(_Doc):
    """A second approved MOP that independently documents a read-only sync status action."""

    def knowledge(self):
        knowledge = super().knowledge()
        section = knowledge.sections[0].model_copy(update={"content": "Synchronization health check\nHC Commands:\nst sync\n"})
        return knowledge.model_copy(update={"title": "Synchronization Health Check", "sections": [section]})


SYNC_DOC = _SyncDoc("SYNC-GOV-MOP", "MOP_Synchronization Health.docx")
SYNC_ACTION_ID = SYNC_DOC.action_id("st sync")


def _fault_progression(*fault_ids: str) -> TroubleshootingProgression:
    progression = TroubleshootingProgression()
    for fault_id in fault_ids:
        progression.add_fault(FaultProgression(fault_id=fault_id, symptom_summary=fault_id))
    return progression


def _open_requirement(progression: TroubleshootingProgression, fault_id: str, proposal: dict[str, Any], *, gap: bool = True) -> EvidenceRequirement:
    requirement = requirement_from_proposal(proposal, fault_id)
    requirement.blocking_reason = GapReason.NO_APPROVED_ACQUISITION_ACTION
    progression.add_requirement(requirement)
    if gap:
        progression.record_acquisition_gap(AcquisitionGap(
            fault_id=fault_id, requirement_id=requirement.requirement_id, requirement_description=requirement.description,
            gap_reason=GapReason.NO_APPROVED_ACQUISITION_ACTION,
            discovery_attempts=[DiscoveryAttempt(outcome=GapReason.NO_APPROVED_ACQUISITION_ACTION.value)],
        ))
    return requirement


def _searched(section: str = "s") -> DiscoveryState:
    identity = EvidenceIdentity(knowledge_id="K", version_label="v1", section_id=section)
    return DiscoveryState(governed_search_performed=True, searches=[{"query": "q", "status": "ok", "results": 1}],
                          available=[(identity.canonical, "match")], available_identities=[(identity, "match")], selected=[])


# =============================================================================================
# A-F, L: server-owned requirement identity
# =============================================================================================


def test_a_same_need_across_wording_variations_reuses_the_open_requirement() -> None:
    progression = _fault_progression("F1")
    original = _open_requirement(progression, "F1", LIVE_T3_REQUIRED)
    for proposal in (LIVE_T4_REQUIRED, LIVE_T5_REQUIRED, {"kind": "diagnostic_result", "description": "Node clock source and synchronization health"}):
        candidate = requirement_from_proposal(proposal, "F1")
        matched, rule = match_open_requirement(progression, candidate)
        assert matched is original, proposal
        assert rule in ("same_capability", "shared_content")


def test_b_genuinely_new_need_gets_a_new_requirement() -> None:
    progression = _fault_progression("F1")
    _open_requirement(progression, "F1", LIVE_T3_REQUIRED)
    candidate = requirement_from_proposal({"kind": "diagnostic_result", "description": "Radio unit operational and availability state", "capability": "radio_unit_state"}, "F1")
    assert match_open_requirement(progression, candidate) == (None, None)
    # Even as a mechanism follow-up, an explicit different capability is a different need when content differs
    # (the follow-up rule only applies to requirements without matching content -- see test_e).
    assert match_open_requirement(progression, candidate, mechanism_followup=False)[0] is None


def test_c_same_need_on_a_different_fault_is_a_different_requirement() -> None:
    progression = _fault_progression("F1", "F2")
    first = _open_requirement(progression, "F1", LIVE_T3_REQUIRED)
    candidate = requirement_from_proposal(LIVE_T3_REQUIRED, "F2")
    matched, _ = match_open_requirement(progression, candidate, mechanism_followup=True)
    assert matched is None and candidate.requirement_id != first.requirement_id
    # A reference to the other fault's requirement is not accepted either.
    referencing = requirement_from_proposal({**LIVE_T3_REQUIRED, "requirement_ref": first.requirement_id}, "F2")
    assert match_open_requirement(progression, referencing)[0] is None


def test_d_refinement_changes_wording_not_identity() -> None:
    progression = _fault_progression("F1")
    original = _open_requirement(progression, "F1", {"kind": "diagnostic_result", "description": "Node synchronization status"})
    requirement_id = original.requirement_id
    refined = requirement_from_proposal({"kind": "diagnostic_result", "description": "Node synchronization source, lock state and clock health"}, "F1")
    matched, _ = match_open_requirement(progression, refined)
    assert matched is original
    continued = refine_requirement(matched, refined, "run-2")
    assert continued.requirement_id == requirement_id
    assert continued.description == "Node synchronization source, lock state and clock health"  # strict refinement adopted
    assert [r.description for r in continued.description_history] == ["Node synchronization source, lock state and clock health"]
    # A narrower / different wording is kept in history only.
    narrower = requirement_from_proposal(LIVE_T4_REQUIRED, "F1")
    refine_requirement(continued, narrower, "run-3")
    assert continued.description == "Node synchronization source, lock state and clock health"
    assert continued.description_history[-1].description == LIVE_T4_REQUIRED["description"]
    assert len(progression.evidence_requirements) == 1


def test_e_missing_capability_follow_up_reuses_the_single_open_need() -> None:
    progression = _fault_progression("F1")
    original = _open_requirement(progression, "F1", LIVE_T3_REQUIRED)
    # A mechanism-derived requirement with no capability and little shared wording.
    candidate = requirement_from_proposal({"kind": "diagnostic_result", "description": "status of the reference"}, "F1")
    assert match_open_requirement(progression, candidate)[0] is None
    assert match_open_requirement(progression, candidate, mechanism_followup=True) == (original, "single_open_need_followup")
    # Never when several acquirable needs are outstanding (ambiguous): fail conservatively.
    _open_requirement(progression, "F1", {"kind": "diagnostic_result", "description": "Transport link packet loss counters"})
    assert match_open_requirement(progression, candidate, mechanism_followup=True)[0] is None


def test_f_reference_to_an_unrelated_requirement_is_rejected() -> None:
    progression = _fault_progression("F1")
    sync = _open_requirement(progression, "F1", LIVE_T3_REQUIRED)
    transport = _open_requirement(progression, "F1", {"kind": "diagnostic_result", "description": "Transport link packet loss counters", "capability": "transport_loss"})
    # The specialist references the transport requirement for a synchronization need.
    wrong = requirement_from_proposal({**LIVE_T4_REQUIRED, "requirement_ref": transport.requirement_id}, "F1")
    matched, rule = match_open_requirement(progression, wrong)
    assert matched is sync and rule != "validated_reference"
    # A reference to an unknown / satisfied requirement is ignored too.
    unknown = requirement_from_proposal({"kind": "diagnostic_result", "description": "Radio unit state", "requirement_ref": "req-doesnotexist"}, "F1")
    assert match_open_requirement(progression, unknown)[0] is None
    progression.satisfy_requirement(transport.requirement_id, [])
    stale = requirement_from_proposal({"kind": "diagnostic_result", "description": "Transport link packet loss counters", "requirement_ref": transport.requirement_id}, "F1")
    assert match_open_requirement(progression, stale)[0] is None
    # A compatible reference is accepted.
    right = requirement_from_proposal({"kind": "diagnostic_result", "description": "sync source", "requirement_ref": sync.requirement_id, "capability": "node_sync_status"}, "F1")
    assert match_open_requirement(progression, right) == (sync, "validated_reference")


def test_l_mechanism_wording_never_becomes_the_evidence_identity() -> None:
    requirement = requirement_from_proposal(LIVE_T5_REQUIRED, "F1")
    assert requirement.description == "node synchronization status"
    assert "command" not in requirement.description.lower() and "command" not in requirement.semantic_tokens
    assert evidence_description("Output of command `xyzzy --all`") == "Output of command"
    assert "xyzzy" not in semantic_tokens(evidence_description("Output of command `xyzzy --all`"))
    assert evidence_description("Node synchronization source and status") == "Node synchronization source and status"
    # Wording without mechanism words is unchanged.
    assert evidence_description(LIVE_T3_REQUIRED["description"]) == LIVE_T3_REQUIRED["description"]


# =============================================================================================
# G-H: one open gap, many discovery attempts; resolution keeps history
# =============================================================================================


@pytest.mark.asyncio
async def test_g_repeated_failed_discovery_updates_one_gap() -> None:
    progression = _fault_progression("F1")
    first = await decide_acquisition(requirement_from_proposal(LIVE_T3_REQUIRED, "F1"), progression=progression,
                                     action=GovernedActionState(), discovery=_searched(), run_id="run-1")
    assert first.outcome == AcquisitionOutcome.GAP and first.gap_is_new
    progression.add_requirement(first.requirement)
    progression.record_acquisition_gap(first.gap)
    # The same need re-proposed with nothing materially new: the gap is reused, no attempt, no retry.
    candidate = requirement_from_proposal(LIVE_T4_REQUIRED, "F1")
    matched, _ = match_open_requirement(progression, candidate)
    repeat = await decide_acquisition(refine_requirement(matched, candidate, "run-2"), progression=progression,
                                      action=GovernedActionState(), discovery=_searched(), run_id="run-2")
    assert repeat.outcome == AcquisitionOutcome.GAP and repeat.repeated and repeat.attempt is None and repeat.gap is first.gap
    # Each pass that consults a governed source no earlier attempt consulted is a new discovery attempt.
    for run in ("run-3", "run-4"):
        candidate = requirement_from_proposal(LIVE_T4_REQUIRED, "F1")
        matched, _ = match_open_requirement(progression, candidate)
        decision = await decide_acquisition(refine_requirement(matched, candidate, run), progression=progression,
                                            action=GovernedActionState(), discovery=_searched(f"sec-{run}"), run_id=run)
        assert decision.outcome == AcquisitionOutcome.GAP and not decision.gap_is_new and not decision.repeated
        assert decision.gap.gap_id == first.gap.gap_id
        progression.record_gap_attempt(decision.gap, decision.attempt, GapReason.NO_APPROVED_ACQUISITION_ACTION)
        progression.add_requirement(decision.requirement)
        progression.record_acquisition_gap(decision.gap)
    (gap,) = progression.acquisition_gaps
    assert gap.status is GapStatus.OPEN and [a.run_id for a in gap.discovery_attempts] == ["run-1", "run-3", "run-4"]
    assert len(progression.evidence_requirements) == 1


def test_h_resolution_closes_the_open_gap_and_keeps_its_history() -> None:
    progression = _fault_progression("F1")
    requirement = _open_requirement(progression, "F1", LIVE_T3_REQUIRED)
    gap = progression.open_gap(requirement.requirement_id)
    progression.close_gaps(requirement.requirement_id, GapStatus.RESOLVED, "acquisition method available: governed_action")
    assert progression.open_gap(requirement.requirement_id) is None
    assert gap.status is GapStatus.RESOLVED and gap.resolved_at is not None and gap.discovery_attempts
    assert progression.acquisition_gaps == [gap]  # never deleted
    # Satisfying the requirement also resolves any open gap.
    other = _open_requirement(progression, "F1", {"kind": "diagnostic_result", "description": "Transport link packet loss counters"})
    progression.satisfy_requirement(other.requirement_id, [])
    assert progression.open_gap(other.requirement_id) is None


# =============================================================================================
# I-J (unit): operator acquisition hints
# =============================================================================================


def test_i_operator_hint_detection_is_generic_and_bounded() -> None:
    assert detect_acquisition_hint(HINT_TEXT) == ("syncstatus", HintType.COMMAND_OR_METHOD)
    assert detect_acquisition_hint("we usually run st sync") == ("st sync", HintType.COMMAND_OR_METHOD)
    assert detect_acquisition_hint("the command is get sync status") == ("get sync status", HintType.COMMAND_OR_METHOD)
    assert detect_acquisition_hint("can you check OneFM instead?") == ("OneFM", HintType.TOOL_OR_SOURCE)
    for text in (COMMAND_QUESTION, "what command do I run?", "how do I get that information?", "I will run it now", ALARM_OUTPUT):
        assert detect_acquisition_hint(text) is None, text


def test_i_hint_attaches_to_the_open_requirement_of_the_fault_only() -> None:
    progression = _fault_progression("F1", "F2")
    requirement = _open_requirement(progression, "F1", LIVE_T3_REQUIRED)
    assert hint_target(progression, "F2") is None
    target = hint_target(progression, "F1")
    assert target is requirement
    hint = progression.add_acquisition_hint(target.requirement_id, make_hint(target, "syncstatus", HintType.COMMAND_OR_METHOD, "run-5"))
    assert hint.requirement_id == requirement.requirement_id and hint.authority == "none" and hint.source == "operator"
    # Same value recorded once.
    assert progression.add_acquisition_hint(target.requirement_id, make_hint(target, "SyncStatus", HintType.COMMAND_OR_METHOD, "r")) is hint
    assert len(requirement.acquisition_hints) == 1
    # Never on a satisfied requirement.
    progression.satisfy_requirement(requirement.requirement_id, [])
    assert progression.add_acquisition_hint(requirement.requirement_id, make_hint(requirement, "x", HintType.COMMAND_OR_METHOD, "r")) is None


@pytest.mark.asyncio
async def test_j_hint_never_becomes_an_acquisition_candidate_or_authority() -> None:
    progression = _fault_progression("F1")
    requirement = _open_requirement(progression, "F1", LIVE_T3_REQUIRED)
    progression.add_acquisition_hint(requirement.requirement_id, make_hint(requirement, "syncstatus", HintType.COMMAND_OR_METHOD, "r"))
    decision = await decide_acquisition(requirement, progression=progression, action=GovernedActionState(), discovery=_searched(), run_id="r2")
    assert decision.outcome == AcquisitionOutcome.GAP and decision.requirement.acquisition_candidates == []
    assert decision.requirement.last_authority_decision is None
    assert decision.attempt.hints_considered == ["syncstatus"]
    assert "used only to search governed knowledge" in decision.response_text and "not an approved method" in decision.response_text


# =============================================================================================
# Exact live sequence (23) + K (hint -> discovery -> governed action) + J (end to end)
# =============================================================================================


async def _alarm_output_gap(conv: _Conversation) -> dict[str, Any]:
    """Live step 3: the alarm output, then the specialist needs sync evidence it has no method for."""
    return await conv.turn(
        ALARM_OUTPUT,
        [SYNC_SEARCH, DOC.select(), _insufficient(["The synchronization status of the node."], [LIVE_T3_REQUIRED])],
        "Please provide the synchronization status.",
        tae_args={"problem_statement": "ESS Service Unavailable"},
    )


def _open(progression: TroubleshootingProgression) -> list[EvidenceRequirement]:
    return [r for r in progression.evidence_requirements if r.status is RequirementStatus.UNSATISFIED]


def _open_gaps(progression: TroubleshootingProgression) -> list[AcquisitionGap]:
    return [g for g in progression.acquisition_gaps if g.status is GapStatus.OPEN]


@pytest.mark.asyncio
async def test_exact_live_sequence_one_requirement_one_gap_many_attempts(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _to_alt_output(conv)  # steps 1-2 (clarification, `alt` authorized) -- existing ALT flow (N)

    t3 = await _alarm_output_gap(conv)
    (req,) = _open(t3["progression"])
    (gap,) = _open_gaps(t3["progression"])
    assert req.semantic_key == "cap:node_sync_status" and req.kind is EvidenceKind.DIAGNOSTIC_RESULT
    assert gap.requirement_id == req.requirement_id and gap.gap_reason is GapReason.NO_APPROVED_ACQUISITION_ACTION
    assert len(gap.discovery_attempts) == 1 and gap.discovery_attempts[0].run_id == _trace(t3)["run_id"]

    # Step 4: "what is the command ..." -- live shape: no capability key, different wording.
    t4 = await conv.turn(
        COMMAND_QUESTION,
        [_fc("knowledge_search", {"query_text": "Ericsson 4G synchronization source command"}), DOC.select(),
         _insufficient(["The synchronization source for an Ericsson 4G node."], [LIVE_T4_REQUIRED])],
        "Please provide the command.",
        tae_args={"problem_statement": "ESS Service Unavailable"},
    )
    progression = t4["progression"]
    assert [r.requirement_id for r in _open(progression)] == [req.requirement_id]
    assert [g.gap_id for g in progression.acquisition_gaps] == [gap.gap_id]
    # Same consulted source, no new result or hint: the gap is reused without another attempt.
    assert len(progression.acquisition_gaps[0].discovery_attempts) == 1
    assert t4["records"][-1]["evidence_acquisition"]["requirement_continuity"] == "shared_content"
    assert t4["records"][-1]["evidence_acquisition"]["gap_repeated"] is True
    assert "already found to have no governed acquisition method" in t4["final"] and "provide the command" not in t4["final"].lower()

    # Step 5: "I normally use syncstatus" -- an untrusted hint on the SAME requirement.
    t5 = await conv.turn(
        HINT_TEXT,
        [_fc("knowledge_search", {"query_text": "Ericsson 4G NodeSynch syncstatus"}), DOC.select(),
         _insufficient(["The output of the syncstatus command.", "The exact syncstatus command syntax for this node."], [LIVE_T5_REQUIRED])],
        "Please run syncstatus.",
        tae_args={"problem_statement": "ESS Service Unavailable", "current_request": {"free_text": "syncstatus"}},
    )
    progression = t5["progression"]
    (same,) = _open(progression)
    assert same.requirement_id == req.requirement_id and "command" not in same.description.lower()
    (hint,) = same.acquisition_hints
    assert (hint.value, hint.hint_type, hint.authority, hint.run_id) == ("syncstatus", HintType.COMMAND_OR_METHOD, "none", _trace(t5)["run_id"])
    (only_gap,) = progression.acquisition_gaps
    assert only_gap.gap_id == gap.gap_id and only_gap.status is GapStatus.OPEN
    # The operator's (untrusted) hint is new context: discovery legitimately runs again for it.
    assert [a.run_id for a in only_gap.discovery_attempts] == [_trace(t3)["run_id"], _trace(t5)["run_id"]]
    assert only_gap.discovery_attempts[-1].hints_considered == ["syncstatus"]
    assert only_gap.discovery_attempts[-1].searches[0]["query_text"] == "Ericsson 4G NodeSynch syncstatus"
    # No authority from the hint (D).
    assert not [c for c in _trace(t5).get("command_authority", []) if c.get("decision") == "authorized"]
    assert same.acquisition_candidates == [] and same.last_authority_decision is None
    assert "syncstatus" in t5["final"] and "not an approved method" in t5["final"]
    assert "run syncstatus" not in t5["final"].lower()
    # The specialist sees the open requirement (with its gap and hint) as server-built context.
    view = _view_for(progression, req.fault_id)
    assert view[0]["requirement_id"] == req.requirement_id and view[0]["acquisition_gap"]["gap_id"] == gap.gap_id
    assert view[0]["acquisition_hints"][0]["value"] == "syncstatus" and "untrusted" in view[0]["acquisition_hints"][0]["authority"]
    # Steps: the completed `alt` step only -- no extra steps for the follow-ups.
    assert [s.status for s in progression.steps] == [StepStatus.COMPLETED]


def _view_for(progression: TroubleshootingProgression, fault_id: str) -> list[dict[str, Any]]:
    from types import SimpleNamespace

    return ProgressionController.open_requirements_view(SimpleNamespace(progression=progression, fault_id=fault_id))


@pytest.mark.asyncio
async def test_open_requirements_are_handed_to_the_specialist(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _to_alt_output(conv)
    t3 = await _alarm_output_gap(conv)
    (req,) = _open(t3["progression"])
    seen: list[dict[str, Any]] = []
    original = ProgressionController.investigation_context

    def _spy(self):
        context = original(self)
        seen.append(context)
        return context

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ProgressionController, "investigation_context", _spy)
        t4 = await conv.turn(COMMAND_QUESTION, [SYNC_SEARCH, DOC.select(), _insufficient([], [{**LIVE_T4_REQUIRED, "requirement_ref": req.requirement_id}])],
                             "x", tae_args={"problem_statement": "ESS Service Unavailable"})
    (open_view,) = seen[-1]["open_evidence_requirements"]
    assert open_view["requirement_id"] == req.requirement_id and open_view["status"] == "unsatisfied"
    assert open_view["kind"] == "diagnostic_result" and open_view["semantic_key"] == "cap:node_sync_status"
    # The validated reference continues the same requirement.
    assert t4["records"][-1]["evidence_acquisition"]["requirement_continuity"] == "validated_reference"
    assert [r.requirement_id for r in _open(t4["progression"])] == [req.requirement_id]


@pytest.mark.asyncio
async def test_k_hint_steers_discovery_to_an_independently_approved_action(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await repo.add(SYNC_DOC.knowledge())
    await _to_alt_output(conv)
    t3 = await _alarm_output_gap(conv)
    (req,) = _open(t3["progression"])
    (gap,) = _open_gaps(t3["progression"])

    governed_sync_step = [types.Part.from_text(text=json.dumps({
        "outcome": "recommended", "technical_interpretation": "Synchronization reference loss is indicated.",
        "verified_evidence_citations": [],
        "diagnostic_step": {
            "action": "Check the node synchronization status", "reason": "Confirm the reference loss.",
            "expected_evidence": "Synchronization source and lock state", "procedure_action_id": SYNC_ACTION_ID,
            "command": None, "command_source": None, "restrictions": [],
            "evidence_requirement": {"kind": "diagnostic_result", "description": "Node synchronization source and status",
                                     "capability": "node_sync_status", "requirement_ref": req.requirement_id},
            "acquisition": "governed_action",
        },
    }))]
    t4 = await conv.turn(
        "I normally use st sync",
        [_fc("knowledge_search", {"query_text": "st sync synchronization status"}), SYNC_DOC.select(), CATALOG, governed_sync_step],
        "Run `st sync`.",
        tae_args={"problem_statement": "ESS Service Unavailable"},
    )
    progression = t4["progression"]
    # hint -> search -> governed action -> fresh Command Authority evaluation.
    assert t4["records"][-1]["diagnostic_step"]["command"] == "st sync"
    continued = progression.requirement(req.requirement_id)
    assert continued.acquisition_hints[0].value == "st sync"
    assert continued.selected_acquisition.acquisition_type is AcquisitionType.GOVERNED_ACTION
    assert continued.selected_acquisition.procedure_action_id == SYNC_ACTION_ID
    assert continued.last_authority_decision.status is AuthorityStatus.AUTHORIZED
    assert continued.last_authority_decision.run_id == _trace(t4)["run_id"]
    # (H) the existing gap is resolved; no duplicate open gap; history kept.
    (resolved,) = progression.acquisition_gaps
    assert resolved.gap_id == gap.gap_id and resolved.status is GapStatus.RESOLVED and resolved.discovery_attempts
    assert _open_gaps(progression) == [] and [r.requirement_id for r in _open(progression)] == [req.requirement_id]
    assert progression.steps[-1].evidence_requirement_id == req.requirement_id and progression.steps[-1].command == "st sync"


@pytest.mark.asyncio
async def test_j_operator_command_never_enters_the_authorized_catalog(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _to_alt_output(conv)
    await _alarm_output_gap(conv)
    legacy_syncstatus = [types.Part.from_text(text=json.dumps({
        "outcome": "recommended", "technical_interpretation": "Check sync.", "verified_evidence_citations": [],
        "diagnostic_step": {"action": "Check sync", "reason": "r", "expected_evidence": "Sync status", "command": "syncstatus",
                            "command_source": DOC.filename, "restrictions": []},
    }))]
    t4 = await conv.turn(
        HINT_TEXT,
        [SYNC_SEARCH, DOC.select(), legacy_syncstatus],
        "Run syncstatus.",
        tae_args={"problem_statement": "ESS Service Unavailable",
                  "approved_commands_catalog": [{"command": "syncstatus", "source_id": DOC.filename, "operation_type": "read_only_diagnostic"}]},
    )
    record = t4["records"][-1]
    assert (record.get("diagnostic_step") or {}).get("command") in (None, "")
    assert not [c for c in _trace(t4).get("command_authority", []) if c.get("decision") == "authorized"]
    assert "`syncstatus`" not in t4["final"]
    (req,) = _open(t4["progression"])
    assert req.acquisition_hints[0].value == "syncstatus" and req.acquisition_hints[0].authority == "none"
    # The refused command is audited as NOT authorized; it never becomes an available method and
    # the gap condition (no approved acquisition action) still holds.
    assert all(c.availability is not CandidateAvailability.AVAILABLE for c in req.acquisition_candidates)
    assert req.last_authority_decision is None or req.last_authority_decision.status is not AuthorityStatus.AUTHORIZED
    (gap,) = t4["progression"].acquisition_gaps
    assert gap.status is GapStatus.OPEN


# =============================================================================================
# M: failed-turn observability; no partial progression
# =============================================================================================


class _FailingLlm(BaseLlm):
    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        raise RuntimeError("injected model failure")
        yield  # pragma: no cover


async def _failing_turn(conv: _Conversation, text: str) -> list[Any]:
    from backend.agents.team_manager.agent import team_manager
    from backend.api.session_service import APP_NAME

    outer = team_manager.model_copy(update={"model": _FailingLlm(model="failing-tm")})
    runner = Runner(app_name=APP_NAME, agent=outer, session_service=conv.session_service.adk_session_service)
    chat = ChatService(session_service=conv.session_service, runner=runner, case_service=CaseService())
    return [e async for e in chat.execute_turn_events(session_id=conv.session_id, message_text=text, user_id="test-engineer")]


@pytest.mark.asyncio
async def test_m_team_manager_failure_is_logged_with_identifiers_and_creates_no_partial_state(conversation, caplog) -> None:  # noqa: F811
    from backend.cases.troubleshooting_progression import PROGRESSION_STATE_KEY

    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _to_alt_output(conv)
    t3 = await _alarm_output_gap(conv)
    before = t3["state"][PROGRESSION_STATE_KEY]

    caplog.set_level(logging.ERROR, logger="backend.api.chat_service")
    events = await _failing_turn(conv, ALARM_OUTPUT)

    errors = [e for e in events if e.type == StreamEventType.ERROR]
    assert [e.data["message"] for e in errors] == ["The assistant could not complete this request. Please try again."]
    assert "injected model failure" not in json.dumps([e.data for e in events], default=str)
    (record,) = [r for r in caplog.records if "turn failed" in r.getMessage()]
    message = record.getMessage()
    run_id = events[0].data.get("run_id") or events[0].run_id
    assert record.exc_info is not None and "injected model failure" in str(record.exc_info[1])
    assert "stage=team_manager_model" in message and f"session_id={conv.session_id}" in message and f"run_id={run_id}" in message
    assert "invocation_id=" in message and "case_id=" in message
    assert ALARM_OUTPUT[:40] not in message  # no message body in the log line

    session = await conv.session_service.get_session(conv.session_id, "test-engineer")
    after = dict(session.state)[PROGRESSION_STATE_KEY]
    assert after == before  # no partial requirement / gap / step / authority state
    progression = TroubleshootingProgression.model_validate(after)
    assert len(progression.evidence_requirements) == 2 and len(progression.acquisition_gaps) == 1 and len(progression.steps) == 1


def test_m_stage_attribution_follows_runner_events() -> None:
    call = types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="technical_authority_engineer", args={}))])

    class _E:
        def __init__(self, calls):
            self._calls = calls

        def get_function_calls(self):
            return self._calls

    assert _runner_stage_after(_E(call.parts and [call.parts[0].function_call]), "team_manager_model") == "tool:technical_authority_engineer"
    assert _runner_stage_after(_E([]), "tool:technical_authority_engineer") == "team_manager_model"
    assert _runner_stage_after(object(), "team_manager_model") == "team_manager_model"


# =============================================================================================
# Backward compatibility
# =============================================================================================


def _legacy_payload() -> dict[str, Any]:
    """A progression persisted by the previous tranche: no semantic identity, hints, gap status or attempts."""
    progression = _fault_progression("F1")
    requirement = EvidenceRequirement(fault_id="F1", kind=EvidenceKind.DIAGNOSTIC_RESULT, description=LIVE_T3_REQUIRED["description"], capability="node_sync_status")
    unrelated = EvidenceRequirement(fault_id="F1", kind=EvidenceKind.DIAGNOSTIC_RESULT, description="Transport link packet loss counters")
    progression.evidence_requirements = [requirement, unrelated]
    progression.acquisition_gaps = [
        AcquisitionGap(fault_id="F1", requirement_id=r.requirement_id, requirement_description=r.description,
                       gap_reason=GapReason.NO_APPROVED_ACQUISITION_ACTION, run_id=f"legacy-{i}",
                       sources_searched=[{"query": "q", "status": "ok"}], recorded_at=datetime(2026, 9, 30, tzinfo=timezone.utc))
        for i, r in enumerate([requirement, requirement, unrelated])
    ]
    payload = progression.model_dump(mode="json")
    for r in payload["evidence_requirements"]:
        for key in ("semantic_key", "semantic_tokens", "description_history", "acquisition_hints"):
            r.pop(key)
    for g in payload["acquisition_gaps"]:
        for key in ("status", "discovery_attempts", "updated_at", "resolved_at", "resolution"):
            g.pop(key)
    return payload


@pytest.mark.asyncio
async def test_legacy_progression_loads_and_continues_conservatively() -> None:
    progression = TroubleshootingProgression.model_validate(_legacy_payload())
    sync, transport = progression.evidence_requirements
    assert sync.semantic_key is None and sync.acquisition_hints == [] and sync.semantic_tokens == []
    assert all(g.status is GapStatus.OPEN and g.discovery_attempts == [] for g in progression.acquisition_gaps)
    # A legacy gap without attempts exposes its own first discovery pass.
    assert [a.run_id for a in progression.acquisition_gaps[0].all_attempts()] == ["legacy-0"]
    # Legacy duplicate open gaps: the most recent one is continued; nothing is silently merged.
    assert progression.open_gap(sync.requirement_id).run_id == "legacy-1"
    candidate = requirement_from_proposal(LIVE_T4_REQUIRED, "F1")
    matched, _ = match_open_requirement(progression, candidate)
    assert matched is sync and sync.semantic_tokens == []  # derived on the fly, not written back
    decision = await decide_acquisition(refine_requirement(matched, candidate, "r"), progression=progression,
                                        action=GovernedActionState(), discovery=_searched(), run_id="r")
    # A legacy attempt recorded no consulted identities: this pass's source counts as new context once.
    assert decision.gap.run_id == "legacy-1" and not decision.gap_is_new and not decision.repeated
    progression.record_gap_attempt(decision.gap, decision.attempt, GapReason.NO_APPROVED_ACQUISITION_ACTION)
    assert [a.run_id for a in decision.gap.discovery_attempts] == ["legacy-1", "r"]
    again = await decide_acquisition(refine_requirement(matched, candidate, "r2"), progression=progression,
                                     action=GovernedActionState(), discovery=_searched(), run_id="r2")
    assert again.repeated and again.attempt is None, "then nothing new: reused without another attempt"
    assert sync.semantic_key == "cap:node_sync_status"  # derived when first revisited
    # The unrelated legacy requirement is untouched.
    assert transport.semantic_key is None and transport.description_history == []
    TroubleshootingProgression.model_validate(progression.model_dump(mode="json"))  # round-trips
