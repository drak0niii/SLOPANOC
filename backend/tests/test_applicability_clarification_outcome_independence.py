"""Deterministic applicability clarification continuity, independent of the specialist's outcome label.

Live defect (Prompt 4 acceptance, session 1a2fffbb-6f05-4a53-a690-2f6d1a5fddfa):
    turn 1  "how can i troubleshoot ESS Service Unavailable ?"  governed evidence SELECTED, applicability
            UNKNOWN, technology + vendor required -- the specialist labelled its outcome
            INSUFFICIENT_EVIDENCE (not RECOMMENDED + refused command), so no pending step was created
    turn 2  "4g, Ericsson"  applicability MATCH, 7 valid actions in the catalog -- but
            known_governed_acquisition=None -> pre_run_rule=None -> no deterministic continuation; the
            specialist returned INSUFFICIENT_EVIDENCE again and the operator got "Thanks for that."

Invariants under test:
    * the applicability clarification is server-owned state on its own (OpenQuestion): with or without a
      pending step, RECOMMENDED-blocked and INSUFFICIENT_EVIDENCE turn 1 shapes converge on turn 2 to the
      same deterministic path (applicability_clarification_resolved -> fresh discovery -> explicit current
      selection -> MATCH -> fresh ProcedureAction derivation/issuance -> Command Authority -> egress)
    * no stale selection, issuance or authority crosses turns; partial / unrelated answers resume nothing
    * a MATCH clears stale `applicability_unresolved` blockers
    * a continued, actionable investigation never ends with a vacuous answer
    * while applicability is unresolved the operator is asked only for the server-evaluated dimensions
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.team_manager.governed_knowledge_completion import SAFE_COMPLETION_FAILURE_TEXT
from backend.agents.technical_authority_engineer.acquisition_continuity import (
    ContinuationKind,
    ContinuationRule,
    KnownGovernedAcquisition,
    pre_run_rule,
)
from backend.agents.technical_authority_engineer.agent_tool import RESPONSE_COMPLETENESS_KEY
from backend.agents.technical_authority_engineer.synthesis_boundary import (
    RESPONSE_INCOMPLETE_TEXT,
    enforce_response_completeness,
    render_applicability_clarification_response,
)
from backend.cases.evidence_identity import EvidenceIdentity
from backend.cases.evidence_model import (
    AcquisitionCandidate,
    AcquisitionType,
    CandidateValidation,
    EvidenceKind,
    EvidenceRequirement,
    GapReason,
)
from backend.cases.troubleshooting_progression import (
    ClarificationReason,
    ClarificationStatus,
    FaultProgression,
    ProgressionPhase,
    StepStatus,
    TroubleshootingProgression,
    stale_applicability_blockers,
)
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_applicability_blocked_governed_action import (
    CATALOG,
    DOC,
    SEARCH,
    _governed,
    _legacy_alt,
    _payload,
    _progression_control,
    _trace,
    use_production_specialist,
)
from backend.tests.test_clarification_continuity import _Conversation, _fc
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)

TURN1 = "how can i troubleshoot ESS Service Unavailable ?"
TURN2 = "4g, Ericsson"
TM_TURN1_ARGS = {"problem_statement": "ESS Service Unavailable"}
TM_TURN2_ARGS = {
    "problem_statement": "ESS Service Unavailable",
    "verified_symptoms": ["ESS Service Unavailable"],
    "known_applicability_facts": {"vendor": ["ericsson"], "technology": ["4g"]},
}
ALT_ID = DOC.action_id("alt")
# As live (turn 1): the synthesis adds a target request no validated state needs yet.
TM_TURN1_TEXT = (
    "To help me troubleshoot \"ESS Service Unavailable,\" I need a bit more information:\n"
    "* **Technology** (e.g., 4G, 5G)\n* **Vendor** (e.g., Ericsson, Nokia)\n"
    "* The **identity of the affected Radio Unit (RRU)**."
)
VACUOUS = "Thanks for that."


@pytest.fixture
def conversation(isolated_km_repo, monkeypatch):  # noqa: F811
    use_production_specialist(monkeypatch)
    return isolated_km_repo, _Conversation(monkeypatch)


def _insufficient(missing: Optional[list[str]] = None, required: Optional[list[dict[str, Any]]] = None) -> list[types.Part]:
    payload: dict[str, Any] = {
        "outcome": "insufficient_evidence",
        "technical_interpretation": "The governed procedure needs the technology and vendor before it can be applied.",
        "verified_evidence_citations": [],
        "missing_information": missing if missing is not None else [],
    }
    if required is not None:
        payload["required_evidence"] = required
    return [types.Part.from_text(text=json.dumps(payload))]


# Live turn-1 shape B: technology / vendor plus a premature target request; a typed evidence need.
SHAPE_B_TURN1 = _insufficient(
    ["Technology of the affected system", "Vendor of the affected equipment", "Identity of the affected Radio Unit (RRU)"],
    [{"kind": "diagnostic_result", "description": "Current active alarm list of the node"}],
)
ALT_CHOSEN = _governed(ALT_ID, "Check active alarms on the node")
# Live turn-1 shape C (session 1f637999): RECOMMENDED with a non-governed command that is refused and has
# no derivable governed identity -> a command-less PRESENTED step, worded unlike any later action.
SHAPE_C_TURN1 = _payload({
    "action": "Check the operational state of all Radio Units (RRUs) and PlugInUnits.", "reason": "r",
    "expected_evidence": "Operational state of each unit", "command": "st rru", "command_source": DOC.filename, "restrictions": [],
})
SHAPES = ["recommended_blocked", "insufficient_evidence", "recommended_unexecutable"]


def _events(turn: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return [e for e in _trace(turn).get("operational_events", []) if e.get("stage") == stage]


def _continuation(turn: dict[str, Any]) -> dict[str, Any]:
    (event,) = _events(turn, "continuation")
    return event


def _authorized(turn: dict[str, Any], command: str) -> bool:
    return any(c["decision"] == "authorized" and c["command"] == command for c in _trace(turn).get("command_authority", []))


async def _turn1(conv: _Conversation, shape: str) -> dict[str, Any]:
    if shape == "recommended_blocked":
        script = [SEARCH, DOC.select(), CATALOG, _legacy_alt()]
    elif shape == "recommended_unexecutable":
        script = [SEARCH, DOC.select(), SHAPE_C_TURN1]
    else:
        script = [SEARCH, DOC.select(), SHAPE_B_TURN1]
    t1 = await conv.turn(TURN1, script, TM_TURN1_TEXT, tae_args=TM_TURN1_ARGS)
    record = t1["records"][-1]
    # Same server facts in both shapes: SELECTED evidence, applicability UNKNOWN, nothing authorized.
    assert record["applicability_clarification"]["missing_dimensions"] == ["technology", "vendor"]
    assert record["approved_commands_catalog"] == [] and not (record.get("diagnostic_step") or {}).get("command")
    assert not [c for c in _trace(t1).get("command_authority", []) if c["decision"] == "authorized"]
    assert all(not a.get("actions") for a in _trace(t1).get("action_catalogs", []))
    return t1


def _clarification(turn: dict[str, Any]):
    progression = turn["progression"]
    return progression.latest_clarification(progression.active_fault_id, ClarificationReason.APPLICABILITY)


# =============================================================================================
# A / B / C / 11. Turn-1 outcome independence: both shapes converge on the same server path
# =============================================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
async def test_turn1_both_shapes_persist_a_resumable_applicability_clarification(conversation, shape: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _turn1(conv, shape)
    progression = t1["progression"]
    fault = progression.faults[progression.active_fault_id]
    question = progression.pending_clarification(fault.fault_id, ClarificationReason.APPLICABILITY)
    # The dependency is server-owned whatever the outcome label: fields, sources, run, requirement.
    assert question is not None and question.status is ClarificationStatus.OPEN
    assert question.requested_fields == ["technology", "vendor"]
    assert question.source_identities == [EvidenceIdentity(knowledge_id=DOC.kid, version_label=DOC.ver, section_id=DOC.section)]
    assert question.created_run_id == t1["records"][-1]["run_id"]
    if shape == "recommended_blocked":
        assert fault.phase is ProgressionPhase.BLOCKED_MISSING_INFORMATION
        (step,) = progression.steps
        assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION and step.blocked_candidate.clarification_id == question.question_id
    elif shape == "recommended_unexecutable":
        # Existing contract: a command-less step is PRESENTED (wording-resolved); it is linked to the
        # clarification so the answer resumes it by structure.
        (step,) = progression.steps
        assert step.status is StepStatus.PRESENTED and (step.command, step.procedure_action_id, step.blocked_candidate) == (None, None, None)
        assert question.originating_step_id == step.step_id
    else:
        assert fault.phase is ProgressionPhase.BLOCKED_MISSING_INFORMATION
        # C: no pending operational step at all -- only the clarification.
        assert progression.steps == []
        assert question.requirement_id is not None
        requirement = progression.requirement(question.requirement_id)
        assert requirement.blocking_reason is GapReason.APPLICABILITY_UNRESOLVED
    (recorded,) = [e for e in _events(t1, "applicability_clarification") if e["event"] == "recorded"]
    assert recorded["clarification_id"] == question.question_id and recorded["missing_dimensions"] == ["technology", "vendor"]


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("turn2_model", ["chooses_action", "omits_step", "legacy_command"])
async def test_turn2_converges_to_the_same_deterministic_governed_progression(conversation, shape: str, turn2_model: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _turn1(conv, shape)
    question_id = _clarification(t1).question_id
    if turn2_model == "chooses_action":
        script = [DOC.select(), CATALOG, ALT_CHOSEN]
    elif turn2_model == "legacy_command":
        # As live (session 03dda9c3): the specialist writes the command text instead of the action id.
        script = [DOC.select(), _legacy_alt()]
    else:
        # As live: selects, then returns INSUFFICIENT_EVIDENCE with no step; chooses only when the server
        # lists this run's valid selected actions (shape B). Shape A reconciles its known blocked action.
        script = [DOC.select(), _insufficient(), ALT_CHOSEN]
    t2 = await conv.turn(TURN2, script, VACUOUS, tae_args=TM_TURN2_ARGS)
    trace = _trace(t2)

    continuation = _continuation(t2)
    assert continuation["kind"] == ContinuationKind.CLARIFICATION_ANSWER.value
    assert continuation["rule"] == ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED.value
    assert continuation["discovery"] == "clarification_resumption"
    assert continuation["source"] == ("pending_step" if shape == "recommended_blocked" else "open_applicability_clarification")
    assert _progression_control(t2)["decision"] == ("new_step" if shape == "insufficient_evidence" else "pending_step_resolved")
    if shape == "recommended_unexecutable":
        assert any(e["event"] == "unexecutable_step_continued_after_clarification" for e in _progression_control(t2)["events"])
    assert continuation["open_applicability_clarification"]["clarification_id"] == question_id
    # Fresh discovery under the confirmed context, explicit current-run selection, MATCH.
    (resumption,) = _events(t2, "retrieval_resumption")
    assert resumption["clarification_id"] == question_id and resumption["status"] == "searched"
    (search,) = trace["searches"]
    assert {k: [v.casefold() for v in vals] for k, vals in search["applicability_context"].items()} == {"vendor": ["ericsson"], "technology": ["4g"]}
    assert trace["selected"] == [{"knowledge_id": DOC.kid, "version_label": DOC.ver, "section_id": DOC.section}]
    # Fresh ProcedureAction derivation + Command Authority + egress in THIS run.
    assert trace["action_resolutions"][-1]["action_id"] == ALT_ID
    assert trace["action_resolutions"][-1]["command_authority"] == "authorized"
    assert _authorized(t2, "alt")
    record = t2["records"][-1]
    assert record["diagnostic_step"]["command"] == "alt" and record["diagnostic_step"]["command_source"] == DOC.canonical
    assert any(c.get("decision") == "kept" and c.get("candidate") == "alt" for c in (trace.get("command_egress") or {}).get("candidates", []))
    # Operator sees the governed step -- never the vacuous synthesis.
    assert "`alt`" in t2["final"] and t2["final"] != VACUOUS
    assert record[RESPONSE_COMPLETENESS_KEY]["satisfied"] is True and "next_step" in record[RESPONSE_COMPLETENESS_KEY]["elements"]
    # Progression: exactly one presented step awaiting its observation, same for both shapes.
    progression = t2["progression"]
    fault = progression.faults[progression.active_fault_id]
    (step,) = progression.steps
    assert step.status is StepStatus.PRESENTED and step.command == "alt" and step.procedure_action_id == ALT_ID
    assert fault.phase is ProgressionPhase.AWAITING_OBSERVATION
    question = progression.latest_clarification(fault.fault_id, ClarificationReason.APPLICABILITY)
    assert question.question_id == question_id and question.status is ClarificationStatus.RESOLVED
    assert question.resulting_applicability == "match" and question.resolved_run_id == record["run_id"]
    # G: no contradictory blocker survives a MATCH.
    assert stale_applicability_blockers(progression, fault.fault_id) == []
    assert all(r.blocking_reason is not GapReason.APPLICABILITY_UNRESOLVED for r in progression.open_requirements(fault.fault_id))
    if shape == "insufficient_evidence" and turn2_model == "omits_step":
        (choice,) = _events(t2, "resume_completeness")
        assert ALT_ID in choice["offered"] and choice["chosen"] == ALT_ID
    if shape == "insufficient_evidence" and turn2_model == "legacy_command":
        # The model's own command text is re-expressed as this run's ProcedureAction; the resolver renders it.
        (converted,) = _events(t2, "resume_procedure_action")
        assert converted["procedure_action_id"] == ALT_ID
        assert trace["action_resolutions"][-1].get("model_command_ignored") in (None, "")


# =============================================================================================
# D. Partial clarification / E. unrelated answer / F. genuine new objective
# =============================================================================================


_FORBIDDEN = [_fc("knowledge_search", {"query_text": "must not run"}), _insufficient()]


@pytest.mark.asyncio
async def test_d_partial_clarification_keeps_it_open_with_no_authority(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _turn1(conv, "insufficient_evidence")
    t2 = await conv.turn("4g", _FORBIDDEN, VACUOUS, tae_args={"problem_statement": "ESS Service Unavailable", "known_applicability_facts": {"technology": ["4g"]}})
    assert t2["tae_calls"] == 0  # nothing restarts: no specialist, no retrieval, no catalog
    question = _clarification(t2)
    assert question.question_id == _clarification(t1).question_id and question.status is ClarificationStatus.OPEN
    assert question.unresolved_fields == ["vendor"] and [v.casefold() for v in question.resolved_values["technology"]] == ["4g"]
    trace = _trace(t2)
    assert not trace.get("searches") and not trace.get("action_catalogs") and not trace.get("action_resolutions")
    assert not [c for c in trace.get("command_authority", []) if c["decision"] == "authorized"]
    assert "vendor" in t2["final"] and "alt" not in t2["final"].split()
    assert [e["event"] for e in _events(t2, "applicability_clarification")] == ["answered_partially"]


@pytest.mark.asyncio
async def test_e_unrelated_message_is_not_an_applicability_answer(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _turn1(conv, "insufficient_evidence")
    t2 = await conv.turn("yes", [SEARCH, DOC.select(), _insufficient()], VACUOUS)
    continuation = _continuation(t2)
    assert continuation["kind"] != ContinuationKind.CLARIFICATION_ANSWER.value and continuation["rule"] is None
    assert not _events(t2, "retrieval_resumption")
    question = _clarification(t2)
    assert question.question_id == _clarification(t1).question_id
    assert question.status is ClarificationStatus.OPEN and question.resolved_values == {}
    assert not _authorized(t2, "alt") and not t2["progression"].steps
    # Still unresolved: the server-recorded request is restated, never the synthesis.
    assert t2["final"] == t2["records"][-1]["applicability_clarification"]["text"]


@pytest.mark.asyncio
async def test_f_genuine_new_objective_switches_without_consuming_the_clarification(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _turn1(conv, "insufficient_evidence")
    fault_a = t1["progression"].active_fault_id
    text = "now troubleshoot the fan unit overheating on Ericsson 4G instead"
    t2 = await conv.turn(
        text, [_insufficient()], "Looking at the fan unit.",
        tae_args={"problem_statement": text, "current_request": {"subject_component": "fan unit", "continues_active_objective": False}},
    )
    assert t2["turn_requests"][-1]["troubleshooting_thread"]["decision"] == "created"
    assert t2["progression"].active_fault_id != fault_a
    assert _continuation(t2)["rule"] is None and not _events(t2, "retrieval_resumption")
    question_a = t2["progression"].pending_clarification(fault_a, ClarificationReason.APPLICABILITY)
    assert question_a is not None and question_a.resolved_values == {}
    assert not _authorized(t2, "alt")


# =============================================================================================
# H. Applicability MISMATCH after the answer
# =============================================================================================


def _three_g_doc() -> KnowledgeObject:
    """Another approved procedure that makes '3G' a governed technology value (vocabulary)."""
    kid = "FAN-GOV-MOP"
    return KnowledgeObject(
        knowledge_id=kid, document_type=KnowledgeDocumentType.MOP, title="Fan Unit Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={"technology": ["3G"], "vendor": ["Ericsson"]}),
        source=KnowledgeSource(source_system="test", source_id="MOP_Fan.docx", display_name=kid),
        sections=[KnowledgeSection(section_id=f"{kid}:v1:section-0000", knowledge_id=kid, heading=None, sequence=0,
                                   content="Fan unit overheating | inspect fan speed\nst fan\n", source_locator="l")],
    )


@pytest.mark.asyncio
async def test_h_mismatch_issues_no_action_and_does_not_continue_the_procedure(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await repo.add(_three_g_doc())
    t1 = await _turn1(conv, "insufficient_evidence")
    t2 = await conv.turn("3g, Ericsson", [DOC.select(), _insufficient(), ALT_CHOSEN], VACUOUS,
                         tae_args={"problem_statement": "ESS Service Unavailable", "known_applicability_facts": {"vendor": ["ericsson"], "technology": ["3g"]}})
    assert _continuation(t2)["rule"] == ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED.value
    (resumption,) = _events(t2, "retrieval_resumption")
    assert all(a["applicability_outcome"] != "match" for a in resumption["available"] if a["section_id"] == DOC.section)
    trace = _trace(t2)
    assert all(a["action_id"] != ALT_ID for c in trace.get("action_catalogs", []) for a in c.get("actions", []))
    assert not _authorized(t2, "alt") and "`alt`" not in t2["final"]
    assert not _events(t2, "resume_completeness") or _events(t2, "resume_completeness")[0]["offered"] == []
    assert not [s for s in t2["progression"].steps if s.command]
    question = t2["progression"].latest_clarification(t2["progression"].active_fault_id, ClarificationReason.APPLICABILITY)
    assert question.question_id == _clarification(t1).question_id and question.resulting_applicability != "match"


# =============================================================================================
# I / J. No stale selection, no stale issuance or authority
# =============================================================================================


@pytest.mark.asyncio
async def test_i_prior_run_selection_never_becomes_current_selection(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _turn1(conv, "insufficient_evidence")
    # Turn 2: the specialist selects NOTHING (it only answers twice); turn 1 selected DOC.
    t2 = await conv.turn(TURN2, [_insufficient(), _insufficient(), _insufficient()], VACUOUS, tae_args=TM_TURN2_ARGS)
    trace = _trace(t2)
    assert _continuation(t2)["rule"] == ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED.value
    assert trace.get("selected") == [] and not trace.get("action_resolutions")
    assert not _authorized(t2, "alt") and "alt" not in t2["final"].split()
    assert not _events(t2, "resume_completeness") or _events(t2, "resume_completeness")[0]["offered"] == []
    record = t2["records"][-1]
    assert record["approved_commands_catalog"] == [] and not (record.get("diagnostic_step") or {}).get("command")
    # The clarification answer is a server-routed operational turn: it fails closed via completeness.
    assert t2["final"] in (RESPONSE_INCOMPLETE_TEXT, SAFE_COMPLETION_FAILURE_TEXT)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
async def test_j_action_id_from_an_earlier_turn_is_reissued_only_from_current_selection(conversation, shape: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _turn1(conv, shape)
    # The specialist names the action id WITHOUT selecting its source in this run: no authority.
    t2 = await conv.turn(TURN2, [ALT_CHOSEN, ALT_CHOSEN, ALT_CHOSEN], VACUOUS, tae_args=TM_TURN2_ARGS)
    assert not _authorized(t2, "alt") and "`alt`" not in t2["final"]
    assert all(r.get("command_authority") != "authorized" for r in _trace(t2).get("action_resolutions", []))
    assert t1["records"][-1]["run_id"] != t2["records"][-1]["run_id"]
    # Selected in THIS run: the same deterministic id is re-derived, re-issued and re-authorized now.
    t3 = await conv.turn("ok, which command then?", [SEARCH, DOC.select(), CATALOG, ALT_CHOSEN], VACUOUS)
    resolution = _trace(t3)["action_resolutions"][-1]
    assert resolution["action_id"] == ALT_ID and resolution["command_authority"] == "authorized"
    assert "`alt`" in t3["final"]


# =============================================================================================
# K. Empty / non-actionable synthesis
# =============================================================================================


@pytest.mark.asyncio
async def test_k_vacuous_answer_never_terminates_an_actionable_continued_investigation(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _turn1(conv, "insufficient_evidence")
    requirement_id = _clarification(t1).requirement_id
    # The specialist selects (MATCH) but never proposes a step, even when offered the valid actions.
    t2 = await conv.turn(TURN2, [DOC.select(), _insufficient(), _insufficient()], VACUOUS, tae_args=TM_TURN2_ARGS)
    record = t2["records"][-1]
    completeness = record[RESPONSE_COMPLETENESS_KEY]
    assert completeness["required"] is True and completeness["satisfied"] is False and ALT_ID in completeness["valid_actions"]
    (choice,) = _events(t2, "resume_completeness")
    assert ALT_ID in choice["offered"] and choice["chosen"] is None
    assert t2["final"] == RESPONSE_INCOMPLETE_TEXT and t2["final"] != VACUOUS
    assert not _authorized(t2, "alt") and "`alt`" not in t2["final"]  # nothing generated by the server
    # G (live shape): the requirement the model left untouched loses its stale applicability blocker.
    progression = t2["progression"]
    assert progression.requirement(requirement_id).blocking_reason is not GapReason.APPLICABILITY_UNRESOLVED
    assert stale_applicability_blockers(progression, progression.active_fault_id) == []
    (settled,) = [e for e in _events(t2, "applicability_clarification") if e["event"] == "settled"]
    assert settled["resulting_applicability"] == "match" and requirement_id in settled["released_requirements"]


def _record(**overrides: Any) -> dict[str, Any]:
    base = {
        "outcome": "recommended",
        "diagnostic_step": {"action": "Check active alarms on the node.", "command": "alt", "command_source": DOC.canonical,
                            "expected_evidence": "Active alarm list"},
        "verified_evidence": [],
    }
    base.update(overrides)
    return base


def test_k_completeness_boundary_units() -> None:
    # A validated step whose authorized command the synthesis dropped is projected from the record.
    projected = enforce_response_completeness(VACUOUS, _record())
    assert "`alt`" in projected and projected != VACUOUS
    assert enforce_response_completeness("Run `alt` and share the output.", _record()) == "Run `alt` and share the output."
    # Markdown-highlighted parameters still count as presenting the command.
    state_change = _record(diagnostic_step={"action": "Restart", "command": "acc FieldReplaceableUnit=RRU-2 restartunit"})
    assert enforce_response_completeness("Run `acc FieldReplaceableUnit=`**`RRU-2`**` restartunit`.", state_change).startswith("Run")
    # Actionable continued investigation with nothing to present: fail closed, never vacuous.
    incomplete = {"outcome": "insufficient_evidence", "diagnostic_step": None,
                  RESPONSE_COMPLETENESS_KEY: {"required": True, "satisfied": False, "valid_actions": [ALT_ID]}}
    assert enforce_response_completeness(VACUOUS, incomplete) == RESPONSE_INCOMPLETE_TEXT
    # Not required (no actionable state) / no record: unchanged.
    assert enforce_response_completeness(VACUOUS, {"outcome": "insufficient_evidence", RESPONSE_COMPLETENESS_KEY: {"required": False, "satisfied": True}}) == VACUOUS
    assert enforce_response_completeness(VACUOUS, None) == VACUOUS


# =============================================================================================
# L. Premature target clarification
# =============================================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
async def test_l_only_server_evaluated_dimensions_are_requested_while_applicability_is_unresolved(conversation, shape: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _turn1(conv, shape)
    record = t1["records"][-1]
    # The synthesis asked for a Radio Unit identity; no target-specific action is selected, so the
    # operator is asked only for what the validated state requires.
    assert t1["final"] == record["applicability_clarification"]["text"]
    assert "technology" in t1["final"] and "vendor" in t1["final"]
    assert "Radio Unit" not in t1["final"] and "RRU" not in t1["final"]


def test_l_renderer_never_overrides_a_presented_command_or_escalation() -> None:
    clarification = {"missing_dimensions": ["technology"], "text": "To validate the applicable governed procedure I still need: technology."}
    assert render_applicability_clarification_response({"outcome": "insufficient_evidence", "applicability_clarification": clarification}) == clarification["text"]
    assert render_applicability_clarification_response(_record(applicability_clarification=clarification)) is None
    assert render_applicability_clarification_response({"outcome": "escalation_required", "applicability_clarification": clarification}) is None
    assert render_applicability_clarification_response({"outcome": "insufficient_evidence"}) is None


# =============================================================================================
# Deterministic rule + lifecycle units
# =============================================================================================


def _known(**overrides: Any) -> KnownGovernedAcquisition:
    values: dict[str, Any] = {"step_id": "step-1", "procedure_action_id": ALT_ID, "source_id": DOC.canonical, "normalized_template": "alt",
                              "origin": "blocked_action", "blocked": True, "clarification_open": False}
    values.update(overrides)
    return KnownGovernedAcquisition(**values)


def test_pre_run_rule_owns_clarification_continuity_without_a_pending_step() -> None:
    kind = ContinuationKind.CLARIFICATION_ANSWER
    resolved = ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED
    # No pending step, clarification answered in full: resumes.
    assert pre_run_rule(None, clarification_answered=True, command_follow_up=False, binding=None, continuation=kind,
                        applicability_clarification_resolved=True) is resolved
    # No pending step and nothing resolved: nothing to continue.
    assert pre_run_rule(None, clarification_answered=True, command_follow_up=False, binding=None, continuation=kind) is None
    assert pre_run_rule(None, clarification_answered=False, command_follow_up=False, binding=None, continuation=ContinuationKind.NONE) is None
    # Pending blocked step: unchanged behaviour (resumes once its clarification is answered, never while open).
    assert pre_run_rule(_known(), clarification_answered=True, command_follow_up=False, binding=None, continuation=kind,
                        applicability_clarification_resolved=True) is resolved
    assert pre_run_rule(_known(clarification_open=True), clarification_answered=True, command_follow_up=False, binding=None,
                        continuation=kind, applicability_clarification_resolved=True) is None


def _progression_with_blocked_requirement() -> tuple[TroubleshootingProgression, str]:
    progression = TroubleshootingProgression()
    progression.add_fault(FaultProgression(fault_id="F-1", symptom_summary="ESS Service Unavailable"))
    requirement = EvidenceRequirement(fault_id="F-1", kind=EvidenceKind.DIAGNOSTIC_RESULT, description="Current active alarm list")
    requirement.blocking_reason = GapReason.APPLICABILITY_UNRESOLVED
    requirement.acquisition_candidates = [
        AcquisitionCandidate(requirement_id=requirement.requirement_id, acquisition_type=AcquisitionType.GOVERNED_ACTION,
                             procedure_action_id=ALT_ID, blocking_reason=GapReason.APPLICABILITY_UNRESOLVED,
                             validation=CandidateValidation.BLOCKED_APPLICABILITY, validated_run_id="run-1"),
    ]
    progression.add_requirement(requirement)
    return progression, requirement.requirement_id


def test_g_stale_applicability_blocker_invariant_and_release() -> None:
    progression, requirement_id = _progression_with_blocked_requirement()
    question = progression.record_clarification(
        "F-1", ClarificationReason.APPLICABILITY, ["technology", "vendor"], text="need", requirement_id=requirement_id, run_id="run-1",
        source_identities=[EvidenceIdentity(knowledge_id=DOC.kid, version_label=DOC.ver, section_id=DOC.section)],
    )
    assert (question.requirement_id, question.created_run_id) == (requirement_id, "run-1")
    assert stale_applicability_blockers(progression, "F-1") == []  # still OPEN: the blocker is current
    progression.resolve_clarification_fields(question.question_id, {"technology": ["4g"], "vendor": ["ericsson"]})
    progression.record_clarification_outcome(question.question_id, "match", "run-2")
    # Contradictory state: MATCH recorded, blocker still applicability_unresolved -> detected.
    assert stale_applicability_blockers(progression, "F-1") == [requirement_id]
    assert progression.release_applicability_blockers("F-1", "applicability resolved to MATCH") == [requirement_id]
    assert stale_applicability_blockers(progression, "F-1") == []
    requirement = progression.requirement(requirement_id)
    (candidate,) = requirement.acquisition_candidates
    assert requirement.blocking_reason is None and candidate.blocking_reason is None
    assert candidate.validation is CandidateValidation.KNOWN_STRUCTURAL  # identity only; no authority


def test_g_mismatch_or_unknown_outcome_keeps_the_blocker() -> None:
    progression, requirement_id = _progression_with_blocked_requirement()
    question = progression.record_clarification("F-1", ClarificationReason.APPLICABILITY, ["technology"], text="need")
    progression.resolve_clarification_fields(question.question_id, {"technology": ["3g"]})
    progression.record_clarification_outcome(question.question_id, "mismatch", "run-2")
    assert stale_applicability_blockers(progression, "F-1") == []
    assert progression.requirement(requirement_id).blocking_reason is GapReason.APPLICABILITY_UNRESOLVED


def test_legacy_open_question_without_dependency_fields_loads() -> None:
    progression = TroubleshootingProgression.model_validate({
        "faults": {"F-1": {"fault_id": "F-1", "symptom_summary": "x"}},
        "open_questions": [{"question_id": "q-1", "fault_id": "F-1", "text": "t", "reason": "applicability",
                            "requested_fields": ["technology"], "source_identities": ["not-a-structured-identity"]}],
    })
    (question,) = progression.open_questions
    assert question.requirement_id is None and question.source_identities == [] and question.resulting_applicability is None
