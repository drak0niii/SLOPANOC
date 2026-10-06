"""Acquisition continuity: a server-known governed acquisition survives model omission.

Live regression (session 1aea9eea, runs 725c2672 / e26bd1da / 4b9a4ba7):
    turn 1  "how can i troubleshoot ESS Service Unavailable ?" -> applicability UNKNOWN; the governed
            read `alt` is blocked; EvidenceRequirement + governed AcquisitionCandidate recorded
    turn 2  "4g, Ericsson" -> fresh retrieval, MATCH, the SAME ProcedureAction in the catalog -- but
            the live model proposed a different step (it read a MOP example transcript as an
            observation) without the action -> the controller rejected it and re-presented the step
            WITHOUT its command: "please provide the output listing all active alarms"
    turn 3  "what's the cmd to list all active alarms on the node?" -> no governed search,
            explicit empty selection, catalog 0 -> governed_fail_closed

Invariant under test: evidence continuity (EvidenceRequirement) and acquisition continuity (its
governed AcquisitionCandidate / blocked action identity) are linked; a model omission never removes
the known method. Continuity is STRUCTURAL; authority is FRESH: the known identity is only a lookup
key into THIS run's catalog (built from THIS run's SELECTED, approved, applicability-MATCH evidence),
then the normal resolver + Command Authority decide. The scripted specialist is the production TAE
agent (tools + callbacks) with only the model replaced, and it is adversarial: it omits the method.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types
from sqlalchemy import update

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.acquisition_continuity import (
    AcquisitionBinding,
    ContinuationRule,
    KnownGovernedAcquisition,
    bind_acquisition_request,
    effective_rule,
    is_definitive_rejection,
    known_governed_acquisition,
    pre_run_rule,
    reconstruct_known_action,
)
from backend.agents.technical_authority_engineer.agent_tool import SELECTION_CONTRACT_RECORD_KEY, _reconcile_known_acquisition
from backend.agents.technical_authority_engineer.evidence_acquisition import (
    AcquisitionOutcome,
    DiscoveryState,
    GovernedActionState,
    decide_acquisition,
)
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.cases.evidence_identity import EvidenceIdentity
from backend.cases.evidence_model import (
    AcquisitionCandidate,
    AcquisitionType,
    AuthorityStatus,
    CandidateAvailability,
    CandidateValidation,
    EvidenceKind,
    EvidenceRequirement,
    GapReason,
)
from backend.cases.troubleshooting_progression import (
    BlockedGovernedAction,
    ClarificationReason,
    ClarificationStatus,
    FaultProgression,
    OpenQuestion,
    StepStatus,
    TroubleshootingProgression,
    TroubleshootingStep,
)
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_applicability_blocked_governed_action import (
    CATALOG,
    DOC,
    SEARCH,
    TURN1_TEXT,
    _blocked_turn,
    _governed,
    _progression_control,
    _trace,
    conversation,  # noqa: F401 (fixture)
)
from backend.tests.test_clarification_continuity import _fc
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tools.knowledge.diagnostic_trace import format_diagnostic_trace

ALT_ID = DOC.action_id("alt")
DOC_IDENTITY = EvidenceIdentity(knowledge_id=DOC.kid, version_label=DOC.ver, section_id=DOC.section)
LIVE_TURN2 = "4g, Ericsson"
LIVE_TURN3 = "okie, what;s the cmd to list all active alarms on the node ?"
TM_TURN2_ARGS = {"problem_statement": "Troubleshoot ESS Service Unavailable", "known_applicability_facts": {"vendor": ["ericsson"], "technology": ["4g"]}}
SELECT_NONE = _fc("knowledge_select_evidence", {"selections": []})


def _text(payload: dict[str, Any]) -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps(payload))]


def _different_step() -> list[types.Part]:
    """The live turn-2 shape: a DIFFERENT observation step, no command, no action id, and an
    interpretation that treats procedure example output as an observation."""
    return _text({
        "outcome": "recommended",
        "technical_interpretation": "An external link failure was observed; the neighbour link interfaces must be checked.",
        "verified_evidence_citations": [],
        "diagnostic_step": {
            "action": "Determine the operational status of the neighbour link interfaces.",
            "reason": "x", "expected_evidence": "The operational status of the neighbour link interfaces (UP/DOWN).",
            "command": None, "command_source": None, "procedure_action_id": None, "restrictions": [],
        },
    })


def _need_output_only() -> list[types.Part]:
    """Omission: the evidence need restated, no method at all."""
    return _text({
        "outcome": "insufficient_evidence",
        "technical_interpretation": "The active alarm output of the node is needed before progressing.",
        "verified_evidence_citations": [],
        "missing_information": [],
        "required_evidence": [{"kind": "diagnostic_result", "description": "Active alarm output of the node"}],
    })


def _live_turn3_payload() -> list[types.Part]:
    """The exact live turn-3 specialist output (run 4b9a4ba7)."""
    return _text({
        "outcome": "insufficient_evidence",
        "technical_interpretation": "The user is requesting the command to list all active alarms on the node.",
        "verified_evidence_citations": [],
        "missing_information": ["The specific command to list all active alarms on the node, as provided by an approved governed procedure."],
        "required_evidence": [],
    })


def _plain_insufficient(text: str = "Nothing applicable was selected.") -> list[types.Part]:
    return _text({"outcome": "insufficient_evidence", "technical_interpretation": text, "verified_evidence_citations": [], "missing_information": []})


def _events(turn: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return [e for e in _trace(turn).get("operational_events", []) if e.get("stage") == stage]


def _continuity(turn: dict[str, Any]) -> dict[str, Any]:
    (event,) = _events(turn, "acquisition_continuity")
    return event


def _authorized(turn: dict[str, Any], command: str) -> list[dict[str, Any]]:
    return [c for c in _trace(turn).get("command_authority", []) if c["decision"] == "authorized" and c["command"] == command]


def _requirement(turn: dict[str, Any], requirement_id: str) -> EvidenceRequirement:
    requirement = turn["progression"].requirement(requirement_id)
    assert requirement is not None
    return requirement


def _governed_candidates(requirement: EvidenceRequirement) -> list[AcquisitionCandidate]:
    return [c for c in requirement.acquisition_candidates if c.acquisition_type is AcquisitionType.GOVERNED_ACTION]


async def _turn1(conv: Any) -> dict[str, Any]:
    """Live turn 1 + the linkage it must leave behind (IDs returned for continuity checks)."""
    t1 = await _blocked_turn(conv)
    (step,) = t1["progression"].steps
    requirement = _requirement(t1, step.evidence_requirement_id)
    (candidate,) = _governed_candidates(requirement)
    assert (candidate.procedure_action_id, candidate.command_template, candidate.canonical_source_id) == (ALT_ID, "alt", DOC.canonical)
    assert candidate.availability is CandidateAvailability.BLOCKED and candidate.blocking_reason is GapReason.APPLICABILITY_UNRESOLVED
    assert candidate.validation is CandidateValidation.BLOCKED_APPLICABILITY
    assert requirement.kind is EvidenceKind.DIAGNOSTIC_RESULT and requirement.selected_acquisition_id == candidate.acquisition_id
    assert step.blocked_candidate.procedure_action_id == ALT_ID
    return {"turn": t1, "step_id": step.step_id, "requirement_id": requirement.requirement_id, "candidate_id": candidate.acquisition_id,
            "question_id": step.blocked_candidate.clarification_id}


def _assert_alt_continued(turn: dict[str, Any], ids: dict[str, Any], rule: str) -> None:
    """Same step, same requirement, same candidate; `alt` authorized freshly in THIS run only."""
    record = turn["records"][-1]
    assert record["diagnostic_step"]["command"] == "alt" and record["diagnostic_step"]["command_source"] == DOC.canonical
    assert record["diagnostic_step"]["procedure_action_id"] == ALT_ID
    assert [(c["command"], c["source_id"]) for c in record["approved_commands_catalog"]] == [("alt", DOC.canonical)]
    assert _authorized(turn, "alt"), "Command Authority authorized alt in THIS run"
    (step,) = turn["progression"].steps
    assert step.step_id == ids["step_id"] and step.status is StepStatus.PRESENTED and step.command == "alt"
    requirement = _requirement(turn, ids["requirement_id"])
    assert step.evidence_requirement_id == ids["requirement_id"]
    (candidate,) = _governed_candidates(requirement)
    assert candidate.acquisition_id == ids["candidate_id"], "the same structural acquisition keeps its identity"
    assert candidate.validation is CandidateValidation.AUTHORIZED_CURRENT_RUN and candidate.validated_run_id == _trace(turn)["run_id"]
    assert requirement.last_authority_decision.status is AuthorityStatus.AUTHORIZED
    assert requirement.last_authority_decision.run_id == _trace(turn)["run_id"]
    event = _continuity(turn)
    assert event["rule"] == rule and event["requirement_id"] == ids["requirement_id"]
    assert event["acquisition_candidate_id"] == ids["candidate_id"] and event["procedure_action_id"] == ALT_ID
    assert event["governed_search_performed"] is True and DOC.canonical in event["selected_sources"]
    assert event["known_source_applicability"] == "match" and event["authority_decision"] == "authorized"
    assert record[SELECTION_CONTRACT_RECORD_KEY]["governed_search_performed"] is True


# =============================================================================================
# 15. Exact live regression (turns 1 -> 2 -> 3)
# =============================================================================================


@pytest.mark.asyncio
async def test_exact_live_regression_sequence(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    ids = await _turn1(conv)
    assert ids["turn"]["completed"]["knowledge_sources"][0]["applicability_outcome"] == "unknown"  # Candidate Source

    # Turn 2: the live adversarial shape -- a different step, the known action omitted.
    t2 = await conv.turn(
        LIVE_TURN2, [DOC.select(), _different_step()],
        "Run `alt`. This is a read-only check. Please provide the output of this command.", tae_args=TM_TURN2_ARGS,
    )
    (resumption,) = _events(t2, "retrieval_resumption")
    assert resumption["status"] == "searched" and resumption["clarification_id"] == ids["question_id"]
    assert t2["progression"].open_questions[0].status is ClarificationStatus.RESOLVED
    _assert_alt_continued(t2, ids, ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED.value)
    assert _continuity(t2)["outcome"] == "server_reconciled" and _continuity(t2)["blocked_action"]["procedure_action_id"] == ALT_ID
    assert any(e["event"] == "blocked_step_continued" for e in _progression_control(t2)["events"])
    (step,) = t2["progression"].steps
    assert step.blocked_candidate.released_by_action_id == ALT_ID
    record = t2["records"][-1]
    assert "neighbour link" not in json.dumps(record["diagnostic_step"]) and "external link" not in str(record["technical_interpretation"]).lower()
    assert "`alt`" in t2["final"]
    source = t2["completed"]["knowledge_sources"][0]
    assert source["applicability_outcome"] == "match" and source["support_role"] == "supporting"  # Source chip

    # Turn 3 (before executing): the exact live specialist behaviour -- no search of its own, explicit
    # empty selection, the command reported as "missing".
    t3 = await conv.turn(LIVE_TURN3, [SELECT_NONE, _live_turn3_payload(), DOC.select(), _live_turn3_payload()], "Run `alt`.")
    (discovery,) = _events(t3, "acquisition_discovery")
    assert discovery["reason"] == "acquisition_request" and discovery["status"] == "searched"
    assert discovery["available"][0]["applicability_outcome"] == "match"
    contract = t3["records"][-1][SELECTION_CONTRACT_RECORD_KEY]
    assert contract["governed_search_performed"] is True, "never governed_search_performed=False on a governed acquisition request"
    _assert_alt_continued(t3, ids, ContinuationRule.ACQUISITION_REQUEST.value)
    assert _continuity(t3)["remediation"] == "selection" and _continuity(t3)["outcome"] == "server_reconciled"
    assert "`alt`" in t3["final"] and "could not be validated" not in t3["final"]


# =============================================================================================
# A. Model omits the known command
# =============================================================================================


@pytest.mark.asyncio
async def test_a_model_omits_known_command_server_recovers_it(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    ids = await _turn1(conv)
    t2 = await conv.turn(LIVE_TURN2, [DOC.select(), _need_output_only()], "Run `alt`.", tae_args=TM_TURN2_ARGS)
    _assert_alt_continued(t2, ids, ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED.value)
    assert _continuity(t2)["model_proposal"] == {"outcome": "insufficient_evidence", "procedure_action_id": None, "had_command": False}


@pytest.mark.asyncio
async def test_a_omission_on_an_unrelated_turn_is_recovered_via_model_omission_rule(conversation) -> None:  # noqa: F811
    """No acquisition request, no clarification: the specialist restates the pending need without a
    method and performed NO governed search (D) -> the server runs discovery first, then recovers."""
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    ids = await _turn1(conv)
    await conv.turn(LIVE_TURN2, [DOC.select(), CATALOG, _governed(ALT_ID, "Check active alarms on the node.")], "Run `alt`.", tae_args=TM_TURN2_ARGS)
    t3 = await conv.turn("the node sits on a macro site in the north region", [_need_output_only(), DOC.select(), _need_output_only()], "Run `alt`.")
    (discovery,) = _events(t3, "acquisition_discovery")
    assert discovery["reason"] == "governed_discovery_required"
    _assert_alt_continued(t3, ids, ContinuationRule.MODEL_OMISSION.value)
    assert _continuity(t3)["remediation"] == "discovery"


# =============================================================================================
# B / C. Applicability continuation; acquisition question on the regressed state
# =============================================================================================


@pytest.mark.asyncio
async def test_b_c_declined_selection_never_authorizes_then_acquisition_question_continues(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    ids = await _turn1(conv)
    # The specialist declines the resumed evidence twice (bounded remediation): AVAILABLE != SELECTED.
    t2 = await conv.turn(LIVE_TURN2, [SELECT_NONE, _plain_insufficient(), SELECT_NONE, _plain_insufficient()], "Okay.", tae_args=TM_TURN2_ARGS)
    record = t2["records"][-1]
    assert not (record.get("diagnostic_step") or {}).get("command") and record["approved_commands_catalog"] == []
    assert not _authorized(t2, "alt")
    event = _continuity(t2)
    assert (event["outcome"], event["reconstruction"], event["remediation"]) == ("not_reconstructable", "source_not_selected_in_this_run", "selection")
    (step,) = t2["progression"].steps
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION and step.command is None
    (candidate,) = _governed_candidates(_requirement(t2, ids["requirement_id"]))
    assert candidate.acquisition_id == ids["candidate_id"], "never erased"
    assert "alt" not in t2["final"].split() and "`alt`" not in t2["final"]

    # C: "what command do I use?" -> same requirement, same candidate, fresh retrieval, fresh authority.
    t3 = await conv.turn("what command do I use?", [DOC.select(), _need_output_only()], "Run `alt`.")
    (discovery,) = _events(t3, "acquisition_discovery")
    assert discovery["reason"] == "acquisition_request"
    _assert_alt_continued(t3, ids, ContinuationRule.ACQUISITION_REQUEST.value)
    assert _continuity(t3)["binding"] == {"requirement_id": ids["requirement_id"], "rule": "pending_step"}


# =============================================================================================
# E. Current retrieval no longer supports the candidate
# =============================================================================================


async def _revise_section_in_place(repo: Any, content: str) -> None:
    from backend.knowledge.repository.sqlalchemy import KnowledgeObjectRecord

    revised = DOC.knowledge()
    revised.sections[0].content = content
    async with repo._session_factory() as session:
        await session.execute(
            update(KnowledgeObjectRecord)
            .where(KnowledgeObjectRecord.knowledge_id == DOC.kid, KnowledgeObjectRecord.version_label == DOC.ver)
            .values(payload=revised.model_dump_json())
        )
        await session.commit()


@pytest.mark.asyncio
async def test_e_candidate_not_supported_by_current_evidence_is_never_authorized(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    ids = await _turn1(conv)
    await _revise_section_in_place(repo, "ESS Service Unavailable | Restart the affected radio(RRU)\nAlarm check before any reset.\n")
    t2 = await conv.turn(LIVE_TURN2, [DOC.select(), _need_output_only()], "Okay.", tae_args=TM_TURN2_ARGS)
    record = t2["records"][-1]
    assert not (record.get("diagnostic_step") or {}).get("command") and not _authorized(t2, "alt")
    event = _continuity(t2)
    assert (event["outcome"], event["reconstruction"]) == ("not_reconstructable", "action_not_derivable_from_current_source")
    requirement = _requirement(t2, ids["requirement_id"])
    (candidate,) = _governed_candidates(requirement)
    assert candidate.acquisition_id == ids["candidate_id"] and candidate.validation is CandidateValidation.REJECTED_CURRENT_RUN
    assert candidate.availability is CandidateAvailability.BLOCKED
    # Discovery completed and no governed method is supported any more: a genuine governed gap.
    (gap,) = t2["progression"].acquisition_gaps
    assert gap.requirement_id == ids["requirement_id"] and gap.gap_reason is GapReason.NO_APPROVED_ACQUISITION_ACTION
    assert "alt" not in t2["final"].split() and "`alt`" not in t2["final"]


# =============================================================================================
# F. Applicability still UNKNOWN
# =============================================================================================


@pytest.mark.asyncio
async def test_f_unknown_applicability_stays_a_blocked_clarification(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    ids = await _turn1(conv)
    for text in ("what's the cmd to list all active alarms on the node?", "Ericsson"):  # follow-up / partial answer
        t = await conv.turn(text, [SEARCH, DOC.select(), CATALOG, _need_output_only()], "I still need the technology.")
        for record in t["records"]:
            assert not (record.get("diagnostic_step") or {}).get("command") and not record.get("approved_commands_catalog")
        assert not _authorized(t, "alt") and _events(t, "acquisition_discovery") == []
        assert [e["outcome"] for e in _events(t, "acquisition_continuity")] in ([], ["awaiting_applicability_clarification"])
        question = t["progression"].pending_clarification(t["progression"].active_fault_id)
        assert question is not None and question.question_id == ids["question_id"] and "technology" in question.unresolved_fields
        (step,) = t["progression"].steps
        assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION and step.command is None
        assert "`alt`" not in t["final"]


# =============================================================================================
# G. Operator proposes a command
# =============================================================================================


@pytest.mark.asyncio
async def test_g_operator_proposed_command_is_a_hint_only(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    ids = await _turn1(conv)
    # While applicability is unresolved the operator names the command itself: hint only, no authority.
    t2 = await conv.turn("I normally use `alt`", [SEARCH, DOC.select(), _need_output_only()], "Okay.")
    assert not _authorized(t2, "alt") and not (t2["records"][-1].get("diagnostic_step") or {}).get("command")
    requirement = _requirement(t2, ids["requirement_id"])
    assert [h.value for h in requirement.acquisition_hints] == ["alt"] and requirement.acquisition_hints[0].authority == "none"
    assert [c.acquisition_id for c in _governed_candidates(requirement)] == [ids["candidate_id"]], "a hint never becomes a candidate"
    # After applicability resolves, an operator-invented command stays unauthorized; the known
    # governed method is continued only from this run's selected evidence.
    t3 = await conv.turn(LIVE_TURN2, [DOC.select(), _need_output_only()], "Run `alt`.", tae_args=TM_TURN2_ARGS)
    _assert_alt_continued(t3, ids, ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED.value)
    t4 = await conv.turn("I normally use showalarms", [SEARCH, DOC.select(), _text({
        "outcome": "recommended", "technical_interpretation": "Use the operator's command.", "verified_evidence_citations": [],
        "diagnostic_step": {"action": "List the alarms", "reason": "x", "expected_evidence": "Alarm list", "command": "showalarms",
                            "command_source": DOC.filename, "restrictions": []},
    })], "Okay.")
    assert not _authorized(t4, "showalarms") and "showalarms" not in json.dumps(t4["records"][-1]["approved_commands_catalog"])
    assert (t4["records"][-1].get("diagnostic_step") or {}).get("command") != "showalarms"
    assert _events(t4, "acquisition_continuity") == [], "the specialist proposed its own method: not an omission"


# =============================================================================================
# H. Genuinely no acquisition candidate -> governed acquisition gap (unchanged)
# =============================================================================================


def _req(description: str = "Node synchronization status") -> EvidenceRequirement:
    return EvidenceRequirement(fault_id="F-1", kind=EvidenceKind.DIAGNOSTIC_RESULT, description=description)


def _discovery(outcome: str = "match") -> DiscoveryState:
    return DiscoveryState(governed_search_performed=True, searches=[{"status": "ok", "query_text": "q"}], available=[(DOC.canonical, outcome)],
                          available_identities=[(DOC_IDENTITY, outcome)])


def _known_candidate(requirement: EvidenceRequirement, **kw: Any) -> AcquisitionCandidate:
    fields: dict[str, Any] = dict(
        requirement_id=requirement.requirement_id, acquisition_type=AcquisitionType.GOVERNED_ACTION, procedure_action_id=ALT_ID,
        command_template="alt", canonical_source_id=DOC.canonical, source_identity=DOC_IDENTITY, availability=CandidateAvailability.BLOCKED,
        blocking_reason=GapReason.APPLICABILITY_UNRESOLVED,
    )
    fields.update(kw)
    return AcquisitionCandidate(**fields)


@pytest.mark.asyncio
async def test_h_no_candidate_still_produces_the_governed_acquisition_gap() -> None:
    requirement = _req()
    decision = await decide_acquisition(requirement, progression=None, action=GovernedActionState(), discovery=_discovery(), run_id="run-h")
    assert decision.outcome == AcquisitionOutcome.GAP and decision.gap.gap_reason is GapReason.NO_APPROVED_ACQUISITION_ACTION
    assert decision.response_text.startswith("No approved governed procedure or available operational capability")


@pytest.mark.asyncio
async def test_omission_never_erases_a_known_candidate_nor_fabricates_a_gap() -> None:
    """Investigation case B: same requirement, method omitted, MATCH source available but unselected."""
    requirement = _req("Active alarm list")
    known = _known_candidate(requirement)
    requirement.acquisition_candidates = [known]
    requirement.selected_acquisition_id = known.acquisition_id
    decision = await decide_acquisition(requirement, progression=None, action=GovernedActionState(), discovery=_discovery(), run_id="run-b")
    assert decision.outcome == AcquisitionOutcome.BLOCKED and decision.gap is None
    (kept,) = requirement.acquisition_candidates
    assert kept.acquisition_id == known.acquisition_id and kept.procedure_action_id == ALT_ID
    assert kept.validation is CandidateValidation.AVAILABLE_CURRENT_RUN and kept.validated_run_id == "run-b"
    assert kept.availability is CandidateAvailability.BLOCKED, "known != authorized"
    assert requirement.selected_acquisition_id == known.acquisition_id
    # No governed search at all: nothing concluded, still not erased.
    decision = await decide_acquisition(requirement, progression=None, action=GovernedActionState(), discovery=DiscoveryState(), run_id="run-c")
    assert decision.outcome == AcquisitionOutcome.BLOCKED and [c.acquisition_id for c in requirement.acquisition_candidates] == [known.acquisition_id]
    assert requirement.acquisition_candidates[0].validation is CandidateValidation.KNOWN_STRUCTURAL
    # Current evidence positively no longer supports it -> rejected for this run -> genuine gap.
    rejected = _discovery()
    rejected.rejected_actions[ALT_ID] = "action_not_derivable_from_current_source"
    decision = await decide_acquisition(requirement, progression=None, action=GovernedActionState(), discovery=rejected, run_id="run-d")
    assert decision.outcome == AcquisitionOutcome.GAP and requirement.acquisition_candidates[0].validation is CandidateValidation.REJECTED_CURRENT_RUN


@pytest.mark.asyncio
async def test_revalidated_candidate_keeps_its_identity_and_authority_is_per_run() -> None:
    requirement = _req("Active alarm list")
    known = _known_candidate(requirement)
    requirement.acquisition_candidates = [known]
    action = GovernedActionState(procedure_action_id=ALT_ID, command_template="alt", canonical_source_id=DOC.canonical,
                                 applicability="match", authorized_command="alt")
    decision = await decide_acquisition(requirement, progression=None, action=action, discovery=_discovery(), run_id="run-x")
    (candidate,) = requirement.acquisition_candidates
    assert decision.outcome == AcquisitionOutcome.PRESENT and candidate.acquisition_id == known.acquisition_id
    assert candidate.validation is CandidateValidation.AUTHORIZED_CURRENT_RUN
    assert candidate.effective_validation("run-x") is CandidateValidation.AUTHORIZED_CURRENT_RUN
    assert candidate.effective_validation("run-y") is CandidateValidation.KNOWN_STRUCTURAL, "never reusable authority"


# =============================================================================================
# I. Multiple open requirements: bind to the right one, never "the most recent"
# =============================================================================================


@pytest.mark.asyncio
async def test_i_acquisition_request_binds_to_the_named_requirement_not_the_most_recent(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    ids = await _turn1(conv)
    await conv.turn(LIVE_TURN2, [DOC.select(), CATALOG, _governed(ALT_ID, "Check active alarms on the node.")], "Run `alt`.", tae_args=TM_TURN2_ARGS)
    sync_need = _text({"outcome": "insufficient_evidence", "technical_interpretation": "Synchronization evidence is needed.", "verified_evidence_citations": [],
                       "missing_information": [], "required_evidence": [{"kind": "diagnostic_result", "description": "Node synchronization status"}]})
    t3 = await conv.turn("is there a way to see the synchronization status too", [SEARCH, SELECT_NONE, sync_need], "Okay.")
    sync = next(r for r in t3["progression"].evidence_requirements if r.requirement_id != ids["requirement_id"])
    assert t3["progression"].open_requirements(sync.fault_id)[0].requirement_id == sync.requirement_id, "the sync need is the most recent"
    assert _events(t3, "acquisition_continuity") == []

    t4 = await conv.turn("what command do I use to get the synchronization status?", [SEARCH, SELECT_NONE, sync_need], "Okay.")
    event = _continuity(t4)
    assert event["binding"] == {"requirement_id": sync.requirement_id, "rule": "content_match"} and event["outcome"] == "not_continued"
    assert not _authorized(t4, "alt") and _events(t4, "acquisition_discovery") == []
    assert t4["records"][-1]["evidence_acquisition"]["requirement"]["requirement_id"] == sync.requirement_id

    t5 = await conv.turn("what command do I use?", [DOC.select(), _need_output_only()], "Run `alt`.")
    _assert_alt_continued(t5, ids, ContinuationRule.ACQUISITION_REQUEST.value)
    assert _continuity(t5)["binding"] == {"requirement_id": ids["requirement_id"], "rule": "pending_step"}


def _progression_with(*requirements: EvidenceRequirement, pending_requirement: Optional[str] = None) -> tuple[TroubleshootingProgression, TroubleshootingStep]:
    progression = TroubleshootingProgression()
    progression.add_fault(FaultProgression(fault_id="F-1", symptom_summary="fault"))
    for requirement in requirements:
        progression.add_requirement(requirement)
    step = TroubleshootingStep(fault_id="F-1", objective="check", evidence_requirement_id=pending_requirement)
    return progression, step


def test_i_binding_rules_unit() -> None:
    alarms, sync = _req("Active alarm list"), _req("Node synchronization status")
    progression, step = _progression_with(alarms, sync, pending_requirement=alarms.requirement_id)
    assert bind_acquisition_request(progression, "F-1", "what command do I use?", step) == AcquisitionBinding(alarms.requirement_id, "pending_step", True)
    assert bind_acquisition_request(progression, "F-1", "how do I get the synchronization status?", step).requirement_id == sync.requirement_id
    assert bind_acquisition_request(progression, "F-1", "cmd to list the alarms?", step).requirement_id == alarms.requirement_id
    assert bind_acquisition_request(progression, "F-1", "what command do I use?", None) is None, "two open needs, none named: ambiguous"
    assert bind_acquisition_request(progression, "F-2", "what command do I use?", None) is None, "never another fault's requirement"


# =============================================================================================
# J. Unrelated question is never forced into acquisition continuation
# =============================================================================================


@pytest.mark.asyncio
async def test_j_unrelated_question_is_not_forced_into_continuation(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _turn1(conv)
    await conv.turn(LIVE_TURN2, [DOC.select(), CATALOG, _governed(ALT_ID, "Check active alarms on the node.")], "Run `alt`.", tae_args=TM_TURN2_ARGS)
    t3 = await conv.turn("what does ESS stand for?", [_plain_insufficient("ESS is a spectrum sharing feature.")], "It is a spectrum sharing feature.")
    assert t3["tae_calls"] == 1, "no remediation, no forced discovery"
    assert _events(t3, "acquisition_continuity") == [] and _events(t3, "acquisition_discovery") == []
    assert not _authorized(t3, "alt")


# =============================================================================================
# Authority: no new authorization path
# =============================================================================================


def _known(**kw: Any) -> KnownGovernedAcquisition:
    base = dict(step_id="s-1", procedure_action_id=ALT_ID, source_id=DOC.canonical, normalized_template="alt", origin="blocked_action",
                requirement_id=None, blocked=True, identity=DOC_IDENTITY)
    base.update(kw)
    return KnownGovernedAcquisition(**base)


def _evidence(applicability: str = "match", content: Optional[str] = None, lifecycle: str = "approved") -> EvidenceReference:
    return EvidenceReference(
        source_id=DOC.canonical, source_type="governed_knowledge", title="t", content_snippet=content if content is not None else DOC.knowledge().sections[0].content,
        metadata={"knowledge_id": DOC.kid, "version_label": DOC.ver, "section_id": DOC.section, "lifecycle_status": lifecycle,
                  "applicability_outcome": applicability, "document_type": "mop"},
    )


def test_reconstruction_requires_current_selected_match_evidence() -> None:
    assert reconstruct_known_action(_known(), [])[1] == "source_not_selected_in_this_run"
    assert reconstruct_known_action(_known(), [_evidence("unknown")])[1] == "source_applicability_unknown_in_this_run"
    assert reconstruct_known_action(_known(), [_evidence(lifecycle="candidate")])[1] == "source_not_authoritative_in_this_run"
    assert reconstruct_known_action(_known(), [_evidence(content="no commands here")])[1] == "action_not_derivable_from_current_source"
    assert reconstruct_known_action(_known(procedure_action_id="pa-fabricated"), [_evidence()])[1] == "action_not_derivable_from_current_source"
    assert reconstruct_known_action(_known(normalized_template="st ru"), [_evidence()])[1] == "template_identity_mismatch"
    action, reason = reconstruct_known_action(_known(), [_evidence()])
    assert action is not None and action.action_id == ALT_ID and reason == "reconstructed_from_current_selected_evidence"
    restart = next(a for a in pa.build_procedure_action_catalog([_evidence(content="Reset:\nacc FieldReplaceableUnit=xxxx restartunit\n")])[0])
    assert reconstruct_known_action(_known(procedure_action_id=restart.action_id, normalized_template=None),
                                    [_evidence(content="Reset:\nacc FieldReplaceableUnit=xxxx restartunit\n")])[1] == "not_a_diagnostic_read"
    assert not is_definitive_rejection("source_not_selected_in_this_run") and not is_definitive_rejection("source_applicability_unknown_in_this_run")
    assert is_definitive_rejection("action_not_derivable_from_current_source")
    assert not is_definitive_rejection("not_a_diagnostic_read"), "a valid state change is never turned into a gap"


def test_reconciliation_never_carries_a_command_or_earlier_authority() -> None:
    """The reconciled proposal carries only the action id; with no current selected evidence it is
    not even produced. A persisted 'authorized' candidate of an earlier run changes nothing."""
    alarms = _req("Active alarm list")
    alarms.acquisition_candidates = [_known_candidate(alarms, availability=CandidateAvailability.AVAILABLE, blocking_reason=None,
                                                      validation=CandidateValidation.AUTHORIZED_CURRENT_RUN, validated_run_id="old-run")]
    progression, step = _progression_with(alarms, pending_requirement=alarms.requirement_id)
    step.procedure_action_id, step.command, step.command_source_id = ALT_ID, "alt", DOC.canonical
    step.command_source_identity = DOC_IDENTITY
    known = known_governed_acquisition(progression, "F-1", step)
    assert known is not None and known.origin == "pending_step_action"
    result = {"outcome": "insufficient_evidence", "required_evidence": [{"kind": "diagnostic_result", "description": "Active alarm list"}]}
    out, trace = _reconcile_known_acquisition(result, known, ContinuationRule.ACQUISITION_REQUEST, run_id=None, progression=progression,
                                              fault_id="F-1", pending=step, evidence=[], remediation=None)
    assert out is result and trace["outcome"] == "not_reconstructable" and trace["reconstruction"] == "source_not_selected_in_this_run"
    out, trace = _reconcile_known_acquisition(result, known, ContinuationRule.ACQUISITION_REQUEST, run_id=None, progression=progression,
                                              fault_id="F-1", pending=step, evidence=[_evidence()], remediation=None)
    assert trace["outcome"] == "server_reconciled"
    assert out["diagnostic_step"]["procedure_action_id"] == ALT_ID
    assert out["diagnostic_step"]["command"] is None and out["diagnostic_step"]["command_source"] is None, "authority is decided later, freshly"
    assert out["verified_evidence_citations"] == [DOC.canonical]


def test_rules_never_preempt_escalation_or_a_proposed_method() -> None:
    alarms = _req("Active alarm list")
    progression, step = _progression_with(alarms, pending_requirement=alarms.requirement_id)
    known = _known(requirement_id=alarms.requirement_id)
    omitted = {"outcome": "insufficient_evidence", "required_evidence": [{"kind": "diagnostic_result", "description": "Active alarm output"}]}
    assert effective_rule(None, omitted, progression, "F-1", known) is ContinuationRule.MODEL_OMISSION
    escalation = {"outcome": "escalation_required", "escalation_reason": "x"}
    assert effective_rule(ContinuationRule.ACQUISITION_REQUEST, escalation, progression, "F-1", known) is None
    own_method = {"outcome": "recommended", "diagnostic_step": {"action": "Alarm list", "expected_evidence": "Active alarm output", "command": "showalarms"}}
    assert effective_rule(None, own_method, progression, "F-1", known) is None
    stripped = {"outcome": "recommended", "diagnostic_step": {"action": "Alarm list", "expected_evidence": "Active alarm output", "command": None}}
    assert effective_rule(None, stripped, progression, "F-1", known, [own_method]) is None, "judged on the raw proposal too"
    unrelated = {"outcome": "insufficient_evidence", "required_evidence": [{"kind": "diagnostic_result", "description": "Fan speed readings"}]}
    assert effective_rule(None, unrelated, progression, "F-1", known) is None
    open_known = _known(requirement_id=alarms.requirement_id, clarification_open=True)
    assert pre_run_rule(open_known, clarification_answered=True, command_follow_up=True, binding=None) is None


# =============================================================================================
# 16 / 17. Diagnostics and backward compatibility
# =============================================================================================


@pytest.mark.asyncio
async def test_diagnostics_prove_the_continuity_chain(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    ids = await _turn1(conv)
    t2 = await conv.turn(LIVE_TURN2, [DOC.select(), _different_step()], "Run `alt`.", tae_args=TM_TURN2_ARGS)
    event = _continuity(t2)
    for key in ("requirement_id", "acquisition_candidate_id", "procedure_action_id", "blocked_action", "governed_search_performed",
                "selected_sources", "known_source_applicability", "authority_decision", "rule", "outcome", "reconstruction"):
        assert key in event, key
    assert event["blocked_action"] == {"step_id": ids["step_id"], "procedure_action_id": ALT_ID, "source_id": DOC.canonical,
                                       "clarification_id": ids["question_id"], "clarification_open": False}
    rendered = format_diagnostic_trace(_trace(t2))
    assert f"ACQUISITION rule=applicability_clarification_resolved requirement={ids['requirement_id']} candidate={ids['candidate_id']}" in rendered
    assert "OUTCOME server_reconciled reconstruction=reconstructed_from_current_selected_evidence authority=authorized" in rendered
    assert t2["records"][-1]["diagnostic_step"]["command"] == "alt"
    assert "ESS Service Unavailable" not in json.dumps(event), "identifiers only, no user content"


def test_legacy_payloads_load_and_continue_structurally() -> None:
    """Pre-existing persisted shapes (no candidate lifecycle; step without a requirement) load."""
    legacy_candidate = {"acquisition_id": "acq-legacy", "requirement_id": "req-legacy", "acquisition_type": "governed_action",
                        "procedure_action_id": ALT_ID, "command_template": "alt", "canonical_source_id": DOC.canonical,
                        "availability": "blocked", "blocking_reason": "applicability_unresolved"}
    candidate = AcquisitionCandidate.model_validate(legacy_candidate)
    assert candidate.validation is CandidateValidation.KNOWN_STRUCTURAL and candidate.validated_run_id is None
    assert candidate.effective_validation("any-run") is CandidateValidation.KNOWN_STRUCTURAL
    progression = TroubleshootingProgression()
    progression.add_fault(FaultProgression(fault_id="F-1", symptom_summary="fault"))
    question = OpenQuestion(fault_id="F-1", text="?", reason=ClarificationReason.APPLICABILITY, requested_fields=["technology"],
                            status=ClarificationStatus.RESOLVED, resolved_values={"technology": ["4G"]})
    progression.open_questions.append(question)
    step = TroubleshootingStep(fault_id="F-1", objective="Check active alarms", status=StepStatus.BLOCKED_BY_CLARIFICATION,
                               blocked_candidate=BlockedGovernedAction(
                                   procedure_action_id=ALT_ID, normalized_command="alt", source_id=DOC.canonical, knowledge_id=DOC.kid,
                                   version_label=DOC.ver, blocking_reason="applicability_unknown", clarification_id=question.question_id))
    reloaded = TroubleshootingProgression.model_validate(json.loads(progression.model_dump_json()))
    known = known_governed_acquisition(reloaded, "F-1", TroubleshootingStep.model_validate(json.loads(step.model_dump_json())))
    assert known is not None and known.origin == "blocked_action" and known.requirement_id is None and not known.clarification_open
    # A legacy record has no structured section: its selection key is never parsed out of `source_id`;
    # it continues structurally through its ProcedureAction id (a digest of the exact source tuple).
    assert known.identity is None and known.selection_key is None
    assert pre_run_rule(known, clarification_answered=False, command_follow_up=True, binding=None) is ContinuationRule.ACQUISITION_REQUEST
    assert reconstruct_known_action(known, [_evidence()])[0].action_id == ALT_ID
    # A record persisted with structured identity round-trips it exactly.
    current = step.model_copy(update={"blocked_candidate": step.blocked_candidate.model_copy(update={"section_id": DOC.section})})
    known = known_governed_acquisition(reloaded, "F-1", TroubleshootingStep.model_validate(json.loads(current.model_dump_json())))
    assert known.identity == DOC_IDENTITY and known.selection_key == {"knowledge_id": DOC.kid, "version_label": DOC.ver, "section_id": DOC.section}
