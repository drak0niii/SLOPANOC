"""Applicability-blocked governed action: identity survives, authority does not.

Live defect (session b5b3949e, turns e-f402 / e-d449):
    turn 1  applicability UNKNOWN -> `alt` stripped by Command Authority (correct) -> the step was
            persisted as a generic observation (`obs:` identity, no ProcedureAction)
    turn 2  "is Ericsson, 4G ." -> MATCH -> ProcedureAction `alt` resolved AND authorized -> the
            Progression Controller saw `pa:...:alt` != `obs:...` -> REJECTED_PENDING_AWAITS_RESULT ->
            `_reject` swapped the authorized step for the stale command-less one -> no `alt`, and the
            UI kept "Please provide the output of this command."

Invariant under test:
    A governed action blocked ONLY by unresolved applicability keeps its structural identity
    (ProcedureAction id + canonical source + template) but carries ZERO execution authority until
    applicability is MATCH and the normal chain re-resolves and re-authorizes it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.progression_controller import (
    ProgressionController,
    ProposalDecision,
    withdraw_rejected_command,
)
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.agents.technical_authority_engineer.synthesis_boundary import enforce_technical_authority_synthesis_boundary
from backend.agents.technical_authority_engineer.validation import repair_removed_command_references
from backend.cases.troubleshooting_progression import (
    BlockedGovernedAction,
    ClarificationStatus,
    StepStatus,
    TroubleshootingProgression,
    TroubleshootingStep,
)
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_clarification_continuity import _Conversation, _fc
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tools.knowledge.diagnostic_trace import RETRIEVAL_DIAGNOSTICS_STATE_KEY

_DIMS = {"technology": ["4G", "5G"], "vendor": ["Ericsson"]}
_CONTENT = (
    "ESS Service Unavailable | Restart the affected radio(RRU)\n"
    "Alarm check if alarm present on node then go do the reset\n"
    "HC Commands:\n"
    "alt\n"
    "st pluginunit\n"
    "st fieldr\n"
)


class _Doc:
    def __init__(self, kid: str, filename: str) -> None:
        self.kid, self.ver, self.filename = kid, "v1", filename
        self.section = f"{kid}:v1:section-0000"
        self.canonical = f"{kid}:v1:{self.section}"

    def knowledge(self) -> KnowledgeObject:
        return KnowledgeObject(
            knowledge_id=self.kid, document_type=KnowledgeDocumentType.MOP, title=f"{self.kid} Alarm Procedure",
            version=KnowledgeVersion(label=self.ver, effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
            lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions=_DIMS),
            source=KnowledgeSource(source_system="test", source_id=self.filename, display_name=self.kid),
            sections=[KnowledgeSection(section_id=self.section, knowledge_id=self.kid, heading=None, sequence=0, content=_CONTENT, source_locator="l")],
        )

    def select(self) -> list[types.Part]:
        return _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": self.kid, "version_label": self.ver, "section_id": self.section}]})

    def action_id(self, template: str) -> str:
        return pa._action_id(self.kid, self.ver, self.section, template)


DOC = _Doc("ESS-GOV-MOP", "MOP_ESS Alarms Resolution.docx")
OTHER = _Doc("ESS-OTHER-MOP", "MOP_Other Alarms Resolution.docx")
SEARCH = _fc("knowledge_search", {"query_text": "ESS Service Unavailable"})
CATALOG = _fc("procedure_action_catalog", {})
TURN1_TEXT = "how can i troubleshoot ESS Service Unavailable ?"
TURN2_TEXT = "is Ericsson, 4G ."
LIVE_EXPECTED = "The output of the 'alt' command, showing any active alarms, their severity, specific problem, and affected MO."


def _payload(step: dict[str, Any], interpretation: str = "ESS Service Unavailable: check active alarms first.") -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "recommended", "technical_interpretation": interpretation, "verified_evidence_citations": [], "diagnostic_step": step,
    }))]


def _legacy_alt(source: str = DOC.filename, command: str = "alt", action: str = "Check active alarms on the node.") -> list[types.Part]:
    return _payload({"action": action, "reason": "Rule out other HW/SW alarms.", "expected_evidence": LIVE_EXPECTED,
                     "command": command, "command_source": source, "restrictions": []})


def _governed(action_id: str, action: str, interpretation: Optional[str] = None) -> list[types.Part]:
    return _payload(
        {"action": action, "reason": "Rule out other HW/SW alarms.", "expected_evidence": "Active alarm list",
         "procedure_action_id": action_id, "command": None, "command_source": None, "restrictions": []},
        interpretation or "Active alarms are listed by the governed health-check read.",
    )


def use_production_specialist(monkeypatch: Any) -> None:
    """The scripted specialist is the PRODUCTION technical_authority_engineer agent (its tools and
    its before/after callbacks, including the integrity callback that sanitizes its output) with
    only the model replaced."""
    import backend.tests.test_clarification_continuity as tcc
    from google.adk.agents import Agent
    from backend.agents.technical_authority_engineer.agent import technical_authority_engineer

    def _agent(**kw: Any) -> Agent:
        if kw.get("name") == "technical_authority_engineer":
            return technical_authority_engineer.model_copy(update={"model": kw["model"]})
        return Agent(**kw)

    monkeypatch.setattr(tcc, "Agent", _agent)


@pytest.fixture
def conversation(isolated_km_repo, monkeypatch):  # noqa: F811
    use_production_specialist(monkeypatch)
    return isolated_km_repo, _Conversation(monkeypatch)


def _trace(turn: dict[str, Any]) -> dict[str, Any]:
    diagnostics = list((turn["state"].get(RETRIEVAL_DIAGNOSTICS_STATE_KEY) or {}).values())
    return diagnostics[-1] if diagnostics else {}


def _progression_control(turn: dict[str, Any]) -> dict[str, Any]:
    """The controller summary of this turn (recorded in the run's diagnostic trace)."""
    events = [e for e in _trace(turn).get("operational_events", []) if e.get("stage") == "progression"]
    return events[-1] if events else {}


def _decision(turn: dict[str, Any]) -> Optional[str]:
    return _progression_control(turn).get("decision")


async def _blocked_turn(conv: _Conversation) -> dict[str, Any]:
    """Live turn 1: UNKNOWN applicability; `alt` refused; blocked identity preserved."""
    t1 = await conv.turn(
        TURN1_TEXT, [SEARCH, DOC.select(), _legacy_alt()],
        "To troubleshoot, check active alarms on the node. Please provide the technology and vendor.",
    )
    record = t1["records"][-1]
    # No authority of any kind.
    assert record["approved_commands_catalog"] == []
    assert record["diagnostic_step"].get("command") is None and record["diagnostic_step"].get("command_source") is None
    assert not [c for c in _trace(t1).get("command_authority", []) if c["decision"] == "authorized"]
    assert record["applicability_clarification"]["missing_dimensions"] == ["technology", "vendor"]
    assert t1["completed"]["knowledge_sources"][0]["applicability_outcome"] == "unknown"  # Candidate source
    assert "alt" not in t1["final"].split()
    # Corrupted prose fixed: the removed command leaves no empty quotes behind.
    assert "''" not in record["diagnostic_step"]["expected_evidence"]
    assert record["diagnostic_step"]["expected_evidence"].startswith("The output of the check")
    # Structural identity preserved, authority absent.
    (step,) = t1["progression"].steps
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION
    assert (step.command, step.command_source_id, step.procedure_action_id) == (None, None, None)
    blocked = step.blocked_candidate
    assert blocked is not None
    assert blocked.procedure_action_id == DOC.action_id("alt")
    assert (blocked.source_id, blocked.normalized_command, blocked.blocking_reason) == (DOC.canonical, "alt", "applicability_unknown")
    question = t1["progression"].pending_clarification(step.fault_id)
    assert question is not None and blocked.clarification_id == question.question_id
    assert question.originating_step_id == step.step_id
    assert _progression_control(t1)["pending_step"]["status"] == "blocked_by_clarification"
    assert [h.to_status for h in step.status_history] == [StepStatus.PROPOSED, StepStatus.VALIDATED, StepStatus.BLOCKED_BY_CLARIFICATION]
    return t1


# =============================================================================================
# 9 / 11. Required live-sequence regression + lifecycle
# =============================================================================================


@pytest.mark.asyncio
async def test_live_sequence_blocked_alt_continues_and_is_freshly_authorized_on_match(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _blocked_turn(conv)
    (blocked_step,) = t1["progression"].steps

    # Turn 2: the TAE words the step differently (as live) -- structure, not wording, decides.
    t2 = await conv.turn(
        TURN2_TEXT,
        [SEARCH, DOC.select(), CATALOG, _governed(DOC.action_id("alt"), "Check the active alarms on the Ericsson 4G node with the governed health-check read")],
        "The next diagnostic step is to check active alarms on the node. Run `alt`. This is a read-only check. Please provide the output of this command.",
    )
    trace = _trace(t2)
    # Clarification resolved; retrieval re-run under the confirmed context; same section, MATCH.
    question = t2["progression"].open_questions[0]
    assert question.status is ClarificationStatus.RESOLVED and question.resolved_values == {"vendor": ["Ericsson"], "technology": ["4G"]}
    assert trace["searches"][-1]["applicability_context"] == {"vendor": ["Ericsson"], "technology": ["4G"]}
    assert [(r["section_id"], r["applicability_outcome"]) for r in trace["searches"][-1]["results"] if r["selection_state"] == "SELECTED"] == [(DOC.section, "match")]
    # The normal chain authorized `alt` freshly in THIS turn.
    assert trace["action_resolutions"][-1]["rendered_command"] == "alt" and trace["action_resolutions"][-1]["command_authority"] == "authorized"
    assert any(c["decision"] == "authorized" and c["command"] == "alt" and c["source_id"] == DOC.canonical for c in trace["command_authority"])
    record = t2["records"][-1]
    assert [(c["command"], c["source_id"]) for c in record["approved_commands_catalog"]] == [("alt", DOC.canonical)]
    # The blocked step CONTINUES -- not REJECTED_PENDING_AWAITS_RESULT.
    assert _decision(t2) == ProposalDecision.PENDING_STEP_RESOLVED.value
    assert any(e["event"] == "blocked_step_continued" for e in _progression_control(t2)["events"])
    assert record["diagnostic_step"]["command"] == "alt"
    assert record["diagnostic_step"]["command_source"] == DOC.canonical
    # One logical step, no duplicate; BLOCKED -> VALIDATED -> PRESENTED with the governed identity.
    (step,) = t2["progression"].steps
    assert step.step_id == blocked_step.step_id
    assert step.status is StepStatus.PRESENTED and step.command == "alt" and step.command_source_id == DOC.canonical
    assert step.procedure_action_id == DOC.action_id("alt") and step.identity.action_key == f"pa:{DOC.kid}:alt"
    assert step.blocked_candidate.released_by_action_id == DOC.action_id("alt") and step.blocked_candidate.released_at is not None
    assert [h.to_status for h in step.status_history][-3:] == [StepStatus.BLOCKED_BY_CLARIFICATION, StepStatus.VALIDATED, StepStatus.PRESENTED]
    assert step.expected_evidence == "Active alarm list"
    # UI: the command is present, the source is applicable.
    assert "`alt`" in t2["final"] and "output of this command" in t2["final"]
    assert t2["completed"]["knowledge_sources"][0]["applicability_outcome"] == "match"


@pytest.mark.asyncio
async def test_legacy_authorized_command_continues_blocked_step_by_structure(conversation) -> None:
    """Legacy path (model-written command, grounded and authorized on MATCH): continuation needs
    normalized command + canonical source + the linked clarification -- not wording."""
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _blocked_turn(conv)
    t2 = await conv.turn(TURN2_TEXT, [SEARCH, DOC.select(), _legacy_alt(action="List the alarms")], "Run `alt`.")
    assert _decision(t2) == ProposalDecision.PENDING_STEP_RESOLVED.value
    assert t2["records"][-1]["diagnostic_step"]["command"] == "alt"
    (step,) = t2["progression"].steps
    assert step.status is StepStatus.PRESENTED and step.command == "alt"


@pytest.mark.asyncio
async def test_asking_again_while_still_unknown_folds_into_the_blocked_step(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _blocked_turn(conv)
    t2 = await conv.turn(
        "what is the cmd i need to run to check the active alarms on the node?",
        [SEARCH, DOC.select(), _legacy_alt(action="Run the alarm listing")], "Run `alt`. Please provide the output of this command.",
    )
    record = t2["records"][-1]
    assert _decision(t2) == ProposalDecision.PENDING_STEP_RESOLVED.value
    assert record["approved_commands_catalog"] == [] and record["diagnostic_step"].get("command") is None
    (step,) = t2["progression"].steps
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION and step.command is None
    assert "alt" not in t2["final"].split() and "`alt`" not in t2["final"]
    assert "output of this command" not in t2["final"]


# =============================================================================================
# 10. Required negative tests
# =============================================================================================


@pytest.mark.asyncio
async def test_c_different_action_after_match_never_replaces_the_blocked_step(conversation) -> None:
    """Even with the SAME wording, a different ProcedureAction never resolves or replaces the blocked
    step, and it is never presented. Once the clarification is answered the SERVER continues the
    blocked action itself (acquisition continuity): re-derived from THIS run's selected MATCH
    evidence and freshly authorized by Command Authority. (Previously the step was re-presented
    without its command -- the live regression of session 1aea9eea.)"""
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _blocked_turn(conv)
    (blocked_step,) = t1["progression"].steps
    t2 = await conv.turn(
        TURN2_TEXT,
        [SEARCH, DOC.select(), CATALOG, _governed(
            DOC.action_id("st pluginunit"), "Check active alarms on the node.",
            interpretation="The plug-in units are inspected first. Run `st pluginunit` to list them.",
        )],
        "Run `alt`. This is a read-only check. Please provide the output of this command.",
    )
    record = t2["records"][-1]
    assert _decision(t2) == ProposalDecision.PENDING_STEP_RESOLVED.value
    assert any(e["event"] == "blocked_step_continued" for e in _progression_control(t2)["events"])
    (step,) = t2["progression"].steps
    assert step.step_id == blocked_step.step_id
    assert step.status is StepStatus.PRESENTED and step.procedure_action_id == DOC.action_id("alt") and step.command == "alt"
    assert record["diagnostic_step"]["command"] == "alt" and record["diagnostic_step"]["procedure_action_id"] == DOC.action_id("alt")
    # The model's different action is never presented (envelope, interpretation, resolution).
    assert "st pluginunit" not in json.dumps({k: record.get(k) for k in ("diagnostic_step", "technical_interpretation", "procedure_action_resolution")})
    assert "''" not in record["diagnostic_step"]["expected_evidence"]
    (continuity,) = [e for e in _trace(t2)["operational_events"] if e.get("stage") == "acquisition_continuity"]
    assert continuity["rule"] == "applicability_clarification_resolved" and continuity["outcome"] == "server_reconciled"
    assert continuity["model_proposal"]["procedure_action_id"] == DOC.action_id("st pluginunit")
    # Fresh authority in THIS run only.
    assert any(c["decision"] == "authorized" and c["command"] == "alt" and c["source_id"] == DOC.canonical for c in _trace(t2)["command_authority"])
    assert "st pluginunit" not in t2["final"] and "`alt`" in t2["final"]


@pytest.mark.asyncio
async def test_d_same_command_from_a_different_source_does_not_resolve_the_blocked_step(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await repo.add(OTHER.knowledge())
    await _blocked_turn(conv)
    t2 = await conv.turn(
        TURN2_TEXT, [SEARCH, OTHER.select(), CATALOG, _governed(OTHER.action_id("alt"), "Check active alarms on the node.")],
        "Run `alt`. Please provide the output of this command.",
    )
    assert _decision(t2) == ProposalDecision.REJECTED_PENDING_AWAITS_RESULT.value
    assert t2["records"][-1]["diagnostic_step"].get("command") is None
    (step,) = t2["progression"].steps
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION and step.blocked_candidate.source_id == DOC.canonical
    assert "`alt`" not in t2["final"] and "this command" not in t2["final"]


@pytest.mark.asyncio
async def test_e_fabricated_command_gets_no_identity_and_no_authority(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await conv.turn(TURN1_TEXT, [SEARCH, DOC.select(), _legacy_alt(command="show everything-now")], "Checking.")
    record = t1["records"][-1]
    assert record["diagnostic_step"].get("command") is None and record["approved_commands_catalog"] == []
    (step,) = t1["progression"].steps
    assert step.blocked_candidate is None and step.status is StepStatus.PRESENTED

    # After MATCH, a model-written `alt` citing a source that is not the selected section stays refused
    # and continues nothing.
    t2 = await conv.turn(TURN2_TEXT, [SEARCH, DOC.select(), _legacy_alt(source="Unrelated Notes.docx")], "Run `alt`.")
    assert t2["records"][-1]["diagnostic_step"].get("command") is None
    assert "`alt`" not in t2["final"]


@pytest.mark.asyncio
async def test_h_plain_observation_step_behaves_exactly_as_before(conversation) -> None:
    """A command-less observation (no governed action refused) is a normal PRESENTED `obs:` step and
    keeps the existing wording-based resolution by a governed action."""
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await conv.turn(
        TURN1_TEXT,
        [SEARCH, DOC.select(), _payload({"action": "Check active alarms on the node.", "reason": "r", "expected_evidence": "Active alarm list",
                                         "command": None, "command_source": None, "restrictions": []})],
        "Check active alarms on the node.",
    )
    (step,) = t1["progression"].steps
    assert step.status is StepStatus.PRESENTED and step.blocked_candidate is None and step.identity.action_key.startswith("obs:")
    t2 = await conv.turn(TURN2_TEXT, [SEARCH, DOC.select(), CATALOG, _governed(DOC.action_id("alt"), "Check active alarms on the node.")], "Run `alt`.")
    assert _decision(t2) == ProposalDecision.PENDING_STEP_RESOLVED.value
    assert t2["records"][-1]["diagnostic_step"]["command"] == "alt"


# --- unit level ---------------------------------------------------------------------------------


def _evidence(doc: _Doc, applicability: str, lifecycle: str = "approved") -> EvidenceReference:
    return EvidenceReference(
        source_id=doc.canonical, source_type="governed_knowledge", title=doc.kid, content_snippet=_CONTENT,
        metadata={"knowledge_id": doc.kid, "version_label": doc.ver, "section_id": doc.section, "source_id": doc.filename,
                  "lifecycle_status": lifecycle, "applicability_outcome": applicability,
                  "unresolved_applicability_dimensions": ["technology", "vendor"] if applicability != "match" else []},
    )


def test_a_blocked_identity_derivation_is_structural_and_fail_closed() -> None:
    unknown = _evidence(DOC, "unknown")
    blocked = pa.applicability_blocked_action("alt", DOC.filename, [unknown])
    assert blocked is not None and blocked.procedure_action_id == DOC.action_id("alt") and blocked.source_id == DOC.canonical
    assert pa.applicability_blocked_action("ALT ", DOC.canonical, [unknown]).procedure_action_id == DOC.action_id("alt")
    # MATCH is authorized normally; NOT_APPLICABLE / unapproved are not applicability blocks.
    assert pa.applicability_blocked_action("alt", DOC.filename, [_evidence(DOC, "match")]) is None
    assert pa.applicability_blocked_action("alt", DOC.filename, [_evidence(DOC, "not_applicable")]) is None
    assert pa.applicability_blocked_action("alt", DOC.filename, [_evidence(DOC, "unknown", lifecycle="draft")]) is None
    # Fabricated command, unselected source, no selected evidence, state change: no identity.
    assert pa.applicability_blocked_action("show everything-now", DOC.filename, [unknown]) is None
    assert pa.applicability_blocked_action("alt", "Unrelated Notes.docx", [unknown]) is None
    assert pa.applicability_blocked_action("alt", DOC.filename, []) is None
    restart = EvidenceReference(**{**unknown.model_dump(), "content_snippet": "Restart the unit:\nacc FieldReplaceableUnit=xxxx restartunit\n"})
    assert pa.applicability_blocked_action("acc FieldReplaceableUnit=RRU-1 restartunit", DOC.filename, [restart]) is None


def _blocked_progression() -> tuple[ProgressionController, TroubleshootingStep]:
    progression = TroubleshootingProgression()
    controller = ProgressionController({}, TroubleshootingState(fault_id="F-1", symptom_summary="ess"), progression=progression)
    blocked = pa.applicability_blocked_action("alt", DOC.filename, [_evidence(DOC, "unknown")])
    result = {"outcome": "recommended", "diagnostic_step": {"action": "Check active alarms on the node.", "reason": "r",
                                                            "expected_evidence": "e", "command": None, "command_source": None}}
    decision, out = controller.evaluate_proposal(result, None, [_evidence(DOC, "unknown")], proposed_command="alt", blocked_action=blocked)
    step = controller.record(decision, out, "chk-1", selected_evidence_ids=[DOC.canonical], applicability={DOC.canonical: "unknown"}, blocked_candidate=blocked)
    return controller, step


def test_b_continuation_requires_the_same_action_on_match_evidence() -> None:
    controller, step = _blocked_progression()
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION
    match_ev = [_evidence(DOC, "match")]
    alt = next(a for a in pa.actions_for_evidence(match_ev[0], match_ev[0].metadata)[0] if a.command_template == "alt")
    resolution = type("R", (), {"action": alt, "candidate": None, "bindings": []})()
    proposal = {"outcome": "recommended", "diagnostic_step": {"action": "something else entirely", "command": "alt", "command_source": DOC.canonical,
                                                              "procedure_action_id": alt.action_id, "expected_evidence": "Active alarm list"}}
    # Same action but the source is still UNKNOWN this turn -> not continued (no authority to carry).
    decision, _ = controller.evaluate_proposal(dict(proposal), resolution, [_evidence(DOC, "unknown")])
    assert decision is ProposalDecision.REJECTED_PENDING_AWAITS_RESULT
    decision, _ = controller.evaluate_proposal(dict(proposal), resolution, match_ev)
    assert decision is ProposalDecision.PENDING_STEP_RESOLVED
    controller.record(decision, proposal, "chk-1", selected_evidence_ids=[DOC.canonical], applicability={DOC.canonical: "match"})
    assert step.status is StepStatus.PRESENTED and step.command == "alt" and len(controller.progression.steps) == 1


def test_blocked_metadata_never_supplies_a_command() -> None:
    controller, step = _blocked_progression()
    context = controller.pending_context()
    assert context["blocked_by_clarification"] is True and context["command"] is None
    assert "alt" not in json.dumps(context) and "alt" not in json.dumps(controller.summary(ProposalDecision.NONE)["pending_step"])
    projected = controller.thread.diagnostic_history[-1]
    assert projected.grounded_command is None and projected.procedure_action_id is None and projected.command_source_id is None


def test_f_withdraw_rejected_command_keeps_audit_ids_but_no_actionable_command() -> None:
    result = {
        "diagnostic_step": {"command": "alt"},
        "technical_interpretation": "Alarms matter. The `alt` command lists them. Report anything unusual.",
        "procedure_action_resolution": {"action_id": "pa-1", "status": "resolved", "source": "S", "intent": "read_only_diagnostic:alt",
                                        "command_template": "alt", "rendered_command": "alt", "command_authority": "authorized", "parameters": []},
    }
    out = withdraw_rejected_command(result)
    assert out["technical_interpretation"] == "Alarms matter. Report anything unusual."
    assert out["procedure_action_resolution"]["action_id"] == "pa-1"
    assert "alt" not in json.dumps(out["procedure_action_resolution"])


@pytest.mark.parametrize(
    "text",
    [
        "Check active alarms on the node. Please provide the output of this command.",
        "Check active alarms on the node. Run it and paste the result.",
        "Check active alarms on the node.\n- Execute the command on the node.",
    ],
)
def test_g_dangling_command_references_are_removed_when_no_command_survives(text: str) -> None:
    execution = {"diagnostic_step": {"action": "Check active alarms on the node.", "command": None}, "verified_evidence": [], "approved_commands_catalog": []}
    final = enforce_technical_authority_synthesis_boundary(text, execution)
    assert final.startswith("Check active alarms on the node.")
    for dangling in ("this command", "Run it", "Execute the command"):
        assert dangling not in final
    authorized = {"diagnostic_step": {"action": "Check active alarms on the node.", "command": "alt"}, "verified_evidence": [], "approved_commands_catalog": []}
    kept = enforce_technical_authority_synthesis_boundary("Run `alt`. Please provide the output of this command.", authorized)
    assert "output of this command" in kept and "alt" in kept


def test_corrupted_removed_command_text_is_repaired() -> None:
    assert repair_removed_command_references("The output of the '' command, showing alarms.") == "The output of the check, showing alarms."
    assert repair_removed_command_references("Keep \"quoted\" words and the command name.") == "Keep \"quoted\" words and the command name."


def test_old_progression_payload_without_blocked_fields_loads() -> None:
    legacy = TroubleshootingStep(fault_id="F", objective="o").model_dump(mode="json")
    legacy.pop("blocked_candidate")
    assert TroubleshootingStep.model_validate(legacy).blocked_candidate is None
    blocked = BlockedGovernedAction(procedure_action_id="pa-1", normalized_command="alt", source_id="K:v1:S", knowledge_id="K",
                                    version_label="v1", blocking_reason="applicability_unknown")
    roundtrip = TroubleshootingStep(fault_id="F", objective="o", blocked_candidate=blocked).model_dump(mode="json")
    assert TroubleshootingStep.model_validate(roundtrip).blocked_candidate == blocked
