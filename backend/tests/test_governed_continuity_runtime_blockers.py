"""Deterministic governed troubleshooting continuity: three runtime blockers (session 715f13a4).

1. Known ProcedureAction issuance
       blocked action -> clarification resolved -> fresh source selected -> the specialist reuses the
       server-known procedure_action_id WITHOUT calling `procedure_action_catalog` -> the server
       assumed it "continued" -> the id was never issued in this run -> resolver: unknown_action.
   Now the server re-derives that exact action from THIS run's SELECTED evidence (same id, canonical
   source and template; approved, applicability MATCH, diagnostic read) and issues it through the
   existing run-catalog mechanism; the normal resolver + Command Authority then decide.

2. ProcedureAction identity of an authorized legacy (free-text) command
       the specialist writes `st ru` -> grounded in selected evidence -> Command Authority authorizes
       it -> the step is stored with procedure_action_id=None -> no known governed acquisition next turn.
   Now, when the authorized command is exactly one diagnostic-read ProcedureAction re-derived from
   THIS run's SELECTED evidence, its id is kept on the step and its acquisition candidate
   (continuity metadata; no authority).

3. Authorized-command synthesis false positive
       "Please check the Radio Unit using the command `st ru`." was removed as a request for the
       operator to supply a command, although `st ru` was this turn's authorized command.
   A phrase that presents a CURRENT-TURN authorized command is presentation; genuine requests for
   the operator's own mechanism stay blocked.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.acquisition_continuity import ContinuationRule, known_governed_acquisition
from backend.agents.technical_authority_engineer.agent_tool import _reconcile_known_acquisition
from backend.agents.technical_authority_engineer.procedure_actions import PROCEDURE_ACTION_RESOLUTION_KEY
from backend.agents.technical_authority_engineer.progression_controller import ProposalDecision
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.agents.technical_authority_engineer.synthesis_boundary import enforce_technical_authority_synthesis_boundary
from backend.cases.evidence_model import AcquisitionType, CandidateValidation
from backend.cases.troubleshooting_progression import ClarificationStatus, StepStatus
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_acquisition_continuity import _evidence, _known, _progression_with, _req
from backend.tests.test_applicability_blocked_governed_action import (
    CATALOG,
    DOC,
    OTHER,
    SEARCH,
    TURN2_TEXT,
    _blocked_turn,
    _decision,
    _fc,
    _governed,
    _payload,
    _progression_control,
    _trace,
    conversation,  # noqa: F401 (fixture)
)
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)

ALT_ID = DOC.action_id("alt")
ALT_PRESENTATION = "Please check the active alarms on the node using the command `alt`. Please provide the output of this command."


def _events(turn: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return [e for e in _trace(turn).get("operational_events", []) if e.get("stage") == stage]


def _authorized(turn: dict[str, Any], command: str) -> list[dict[str, Any]]:
    return [c for c in _trace(turn).get("command_authority", []) if c["decision"] == "authorized" and c["command"] == command]


# =============================================================================================
# 1. Known ProcedureAction issuance
# =============================================================================================


async def _continue_known_action(conv: Any, *, catalog_call: bool) -> dict[str, Any]:
    t2 = await conv.turn(
        TURN2_TEXT,
        [SEARCH, DOC.select(), *([CATALOG] if catalog_call else []), _governed(ALT_ID, "Check the active alarms on the node.")],
        ALT_PRESENTATION,
    )
    trace = _trace(t2)
    # Clarification resolved; the fresh source is SELECTED by the specialist, MATCH.
    assert t2["progression"].open_questions[0].status is ClarificationStatus.RESOLVED
    assert [(r["section_id"], r["applicability_outcome"]) for r in trace["searches"][-1]["results"] if r["selection_state"] == "SELECTED"] == [
        (DOC.section, "match")
    ]
    # Resolved by the normal resolver and authorized by Command Authority in THIS run.
    resolution = trace["action_resolutions"][-1]
    assert (resolution["action_id"], resolution["status"]) == (ALT_ID, "resolved")
    assert resolution["rendered_command"] == "alt" and resolution["command_authority"] == "authorized"
    assert _authorized(t2, "alt") and all(c["source_id"] == DOC.canonical for c in _authorized(t2, "alt"))
    (continuity,) = _events(t2, "acquisition_continuity")
    assert continuity["rule"] == ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED.value
    assert continuity["outcome"] == "model_continued" and continuity["issued"] is True
    assert continuity["reconstruction"] == "reconstructed_from_current_selected_evidence"
    assert continuity["authority_decision"] == "authorized"
    # The blocked step continues (no duplicate) and is presented with its governed identity.
    record = t2["records"][-1]
    assert record["diagnostic_step"]["command"] == "alt" and record["diagnostic_step"]["command_source"] == DOC.canonical
    assert record["diagnostic_step"]["procedure_action_id"] == ALT_ID
    assert [(c["command"], c["source_id"]) for c in record["approved_commands_catalog"]] == [("alt", DOC.canonical)]
    assert _decision(t2) == ProposalDecision.PENDING_STEP_RESOLVED.value
    assert any(e["event"] == "blocked_step_continued" for e in _progression_control(t2)["events"])
    (step,) = t2["progression"].steps
    assert step.status is StepStatus.PRESENTED and step.command == "alt" and step.procedure_action_id == ALT_ID
    assert step.blocked_candidate.released_by_action_id == ALT_ID
    # Shown: the presentation sentence of the authorized command survives synthesis and egress.
    assert "using the command `alt`" in t2["final"] and "output of this command" in t2["final"]
    return t2


@pytest.mark.asyncio
async def test_clarification_then_known_action_reused_without_catalog_call_is_issued_authorized_and_shown(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _blocked_turn(conv)
    t2 = await _continue_known_action(conv, catalog_call=False)
    assert _trace(t2).get("action_catalogs", []) == [], "the specialist never listed the catalog: the server issued the known action"


@pytest.mark.asyncio
async def test_same_flow_with_explicit_catalog_call_is_unchanged(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _blocked_turn(conv)
    t2 = await _continue_known_action(conv, catalog_call=True)
    (catalog,) = _trace(t2)["action_catalogs"]
    assert ALT_ID in [a["action_id"] for a in catalog["actions"]]


@pytest.mark.asyncio
async def test_known_action_whose_source_is_not_selected_in_this_run_is_blocked(conversation) -> None:  # noqa: F811
    """The specialist reuses the known id but SELECTS a different source: the known source is only
    AVAILABLE (MATCH) in this run. Nothing is issued, nothing is selected on its behalf, no authority."""
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await repo.add(OTHER.knowledge())
    await _blocked_turn(conv)
    t2 = await conv.turn(TURN2_TEXT, [SEARCH, OTHER.select(), _governed(ALT_ID, "Check the active alarms on the node.")], ALT_PRESENTATION)
    trace = _trace(t2)
    states = {r["section_id"]: r["selection_state"] for r in trace["searches"][-1]["results"]}
    assert states[OTHER.section] == "SELECTED" and states.get(DOC.section) != "SELECTED", "AVAILABLE is never promoted"
    (continuity,) = _events(t2, "acquisition_continuity")
    assert continuity["outcome"] == "model_continued" and continuity["issued"] is False
    assert continuity["reconstruction"] == "source_not_selected_in_this_run"
    assert DOC.canonical not in continuity["selected_sources"]
    assert trace["action_resolutions"][-1]["status"] == pa.ProcedureActionResolutionStatus.UNKNOWN_ACTION.value
    assert not _authorized(t2, "alt")
    record = t2["records"][-1]
    assert not (record.get("diagnostic_step") or {}).get("command") and record["approved_commands_catalog"] == []
    (step,) = t2["progression"].steps
    assert step.command is None and step.procedure_action_id is None and step.blocked_candidate.released_at is None
    assert "`alt`" not in t2["final"]


def _run_issued(run_id: str) -> frozenset[str]:
    try:
        return pa.issued_action_ids(run_id)
    finally:
        pa.discard_issued_actions(run_id)


def _chosen(action_id: str = ALT_ID, command: Optional[str] = None) -> dict[str, Any]:
    return {"outcome": "recommended", "diagnostic_step": {
        "action": "Check active alarms", "expected_evidence": "Active alarm list", "procedure_action_id": action_id, "command": command,
    }}


def test_known_action_issuance_is_server_reconstructed_and_fail_closed() -> None:
    alarms = _req("Active alarm list")
    progression, step = _progression_with(alarms, pending_requirement=alarms.requirement_id)
    known = _known(requirement_id=alarms.requirement_id)
    kw = dict(progression=progression, fault_id="F-1", pending=step, remediation=None)

    # Chosen + current SELECTED approved MATCH evidence -> issued (exactly the server-derived entry).
    out, trace = _reconcile_known_acquisition(_chosen(), known, ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED,
                                              run_id="run-issue-1", evidence=[_evidence()], **kw)
    assert _run_issued("run-issue-1") == frozenset({ALT_ID})
    assert out["diagnostic_step"]["procedure_action_id"] == ALT_ID and out["diagnostic_step"]["command"] is None
    assert trace["outcome"] == "model_continued" and trace["issued"] is True

    # No continuation rule applies (the model also wrote a command of its own): still issued, since the
    # id it chose is the server-known one; its command text is never a candidate on this path.
    out, trace = _reconcile_known_acquisition(_chosen(command="alt"), known, None, run_id="run-issue-2", evidence=[_evidence()], **kw)
    assert trace is None and _run_issued("run-issue-2") == frozenset({ALT_ID})

    # Fail closed: not selected / not MATCH / not approved / not derivable from the current section.
    for run_id, evidence in (
        ("run-issue-3", []),
        ("run-issue-4", [_evidence("unknown")]),
        ("run-issue-5", [_evidence(lifecycle="candidate")]),
        ("run-issue-6", [_evidence(content="no commands here")]),
    ):
        _, trace = _reconcile_known_acquisition(_chosen(), known, ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED,
                                                run_id=run_id, evidence=evidence, **kw)
        assert _run_issued(run_id) == frozenset() and trace["issued"] is False, run_id

    # A DIFFERENT id chosen by the specialist is never issued by continuity (only the catalog tool
    # issues it); with no continuation rule nothing is issued at all.
    other_id = DOC.action_id("st pluginunit")
    _reconcile_known_acquisition(_chosen(other_id), known, None, run_id="run-issue-7", evidence=[_evidence()], **kw)
    assert _run_issued("run-issue-7") == frozenset()
    _reconcile_known_acquisition(_chosen(other_id), known, ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED,
                                 run_id="run-issue-7b", evidence=[_evidence()], **kw)
    assert other_id not in _run_issued("run-issue-7b")

    # A known STATE CHANGE is never issued by continuity (the catalog / target gate path only).
    restart_content = "Reset:\nacc FieldReplaceableUnit=xxxx restartunit\n"
    restart = pa.build_procedure_action_catalog([_evidence(content=restart_content)])[0][0]
    known_restart = _known(procedure_action_id=restart.action_id, normalized_template=None, requirement_id=alarms.requirement_id)
    _, trace = _reconcile_known_acquisition(_chosen(restart.action_id), known_restart, ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED,
                                            run_id="run-issue-8", evidence=[_evidence(content=restart_content)], **kw)
    assert _run_issued("run-issue-8") == frozenset() and trace["reconstruction"] == "not_a_diagnostic_read"


# =============================================================================================
# 2. ProcedureAction identity of an authorized legacy (free-text) command
# =============================================================================================

RU_KID, RU_SECTION, RU_FILE = "RU-HEALTH-MOP", "RU-HEALTH-MOP:v1:section-0000", "MOP_Radio Unit Health.docx"
RU_CANONICAL = f"{RU_KID}:v1:{RU_SECTION}"
RU_CONTENT = "Radio Unit health check\nHC Commands:\nst ru\nalt\n"
ST_RU_ID = pa._action_id(RU_KID, "v1", RU_SECTION, "st ru")
RU_SEARCH = _fc("knowledge_search", {"query_text": "radio unit fault"})
RU_SELECT = _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": RU_KID, "version_label": "v1", "section_id": RU_SECTION}]})


def _ru_mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=RU_KID, document_type=KnowledgeDocumentType.MOP, title="Radio Unit Health Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id=RU_FILE, display_name=RU_KID),
        sections=[KnowledgeSection(section_id=RU_SECTION, knowledge_id=RU_KID, heading=None, sequence=0, content=RU_CONTENT, source_locator="l")],
    )


def _legacy_st_ru(command: str = "st ru", source: str = RU_FILE) -> list[types.Part]:
    return _payload(
        {"action": "Check the Radio Unit state.", "reason": "Rule out a disabled radio unit.", "expected_evidence": "Radio unit operational state",
         "command": command, "command_source": source, "restrictions": []},
        "The radio unit state is checked first.",
    )


@pytest.mark.asyncio
async def test_authorized_free_text_read_matching_current_procedure_action_persists_its_identity(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_ru_mop())
    t1 = await conv.turn(
        "how do I troubleshoot the radio unit fault?", [RU_SEARCH, RU_SELECT, _legacy_st_ru()],
        "Please check the Radio Unit using the command `st ru`. Please provide the output of this command.",
    )
    record = t1["records"][-1]
    # Legacy path: Command Authority authorized the model-written command; no resolver, no catalog.
    assert record["diagnostic_step"]["command"] == "st ru" and record["diagnostic_step"]["command_source"] == RU_CANONICAL
    assert [(c["command"], c["source_id"]) for c in record["approved_commands_catalog"]] == [("st ru", RU_CANONICAL)]
    assert PROCEDURE_ACTION_RESOLUTION_KEY not in record and _trace(t1).get("action_catalogs", []) == []
    assert _authorized(t1, "st ru")
    # Continuity identity on the step...
    (step,) = t1["progression"].steps
    assert step.status is StepStatus.PRESENTED and step.command == "st ru" and step.command_source_id == RU_CANONICAL
    assert step.procedure_action_id == ST_RU_ID
    # ...on its acquisition candidate...
    requirement = t1["progression"].requirement(step.evidence_requirement_id)
    (candidate,) = [c for c in requirement.acquisition_candidates if c.acquisition_type is AcquisitionType.GOVERNED_ACTION]
    assert (candidate.procedure_action_id, candidate.command_template, candidate.canonical_source_id) == (ST_RU_ID, "st ru", RU_CANONICAL)
    assert candidate.validation is CandidateValidation.AUTHORIZED_CURRENT_RUN and requirement.selected_acquisition_id == candidate.acquisition_id
    # ...and in the progression's pending-step context, so the next turn knows the governed method.
    known = known_governed_acquisition(t1["progression"], step.fault_id, step)
    assert known is not None and (known.procedure_action_id, known.source_id, known.normalized_template) == (ST_RU_ID, RU_CANONICAL, "st ru")
    assert known.acquisition_candidate_id == candidate.acquisition_id
    # Shown: the presentation sentence survives the synthesis boundary.
    assert "using the command `st ru`" in t1["final"]

    # Next turn ("what command do I use?"): the known identity is re-derived from THIS run's selected
    # evidence and freshly authorized -- the earlier authorization is never reused.
    t2 = await conv.turn("what is the cmd to check the radio unit?", [RU_SEARCH, RU_SELECT, _payload(
        {"action": "Check the Radio Unit state.", "reason": "r", "expected_evidence": "Radio unit operational state",
         "procedure_action_id": ST_RU_ID, "command": None, "command_source": None, "restrictions": []},
    )], "Please check the Radio Unit using the command `st ru`.")
    (continuity,) = _events(t2, "acquisition_continuity")
    assert continuity["rule"] == ContinuationRule.ACQUISITION_REQUEST.value and continuity["issued"] is True
    resolution = _trace(t2)["action_resolutions"][-1]
    assert (resolution["action_id"], resolution["status"], resolution["command_authority"]) == (ST_RU_ID, "resolved", "authorized")
    assert _authorized(t2, "st ru")
    (step2,) = t2["progression"].steps
    assert step2.step_id == step.step_id and step2.procedure_action_id == ST_RU_ID and step2.command == "st ru"
    assert "`st ru`" in t2["final"]


def _ru_evidence(applicability: str = "match", content: str = RU_CONTENT, lifecycle: str = "approved") -> EvidenceReference:
    return EvidenceReference(
        source_id=RU_CANONICAL, source_type="governed_knowledge", title="t", content_snippet=content,
        metadata={"knowledge_id": RU_KID, "version_label": "v1", "section_id": RU_SECTION, "source_id": RU_FILE,
                  "lifecycle_status": lifecycle, "applicability_outcome": applicability, "document_type": "mop"},
    )


def test_authorized_command_identity_is_derived_only_from_current_selected_match_evidence() -> None:
    match = [_ru_evidence()]
    assert pa.authorized_command_action("st ru", RU_CANONICAL, match).action_id == ST_RU_ID
    assert pa.authorized_command_action(" ST  RU ", RU_FILE, match).action_id == ST_RU_ID  # alias + whitespace/case
    # Fail closed (no identity): not selected, not MATCH, not approved, other source, not a governed action.
    assert pa.authorized_command_action("st ru", RU_CANONICAL, []) is None
    assert pa.authorized_command_action("st ru", RU_CANONICAL, [_ru_evidence("unknown")]) is None
    assert pa.authorized_command_action("st ru", RU_CANONICAL, [_ru_evidence(lifecycle="candidate")]) is None
    assert pa.authorized_command_action("st ru", "Unrelated Notes.docx", match) is None
    assert pa.authorized_command_action("st ru", None, match) is None
    assert pa.authorized_command_action("st ru extra", RU_CANONICAL, match) is None
    assert pa.authorized_command_action("show everything-now", RU_CANONICAL, match) is None
    # A state change never receives a continuity identity on the legacy path (target authority unchanged).
    restart = "Reset:\nacc FieldReplaceableUnit=xxxx restartunit\n"
    assert pa.authorized_command_action("acc FieldReplaceableUnit=RRU-1 restartunit", RU_CANONICAL, [_ru_evidence(content=restart)]) is None
    # A parameterized read: the rendered command corresponds to the template's action.
    param = "Read the port:\n`hget near <port>`\n"
    (action,) = pa.build_procedure_action_catalog([_ru_evidence(content=param)])[0]
    assert pa.authorized_command_action("hget near RfPort-1", RU_CANONICAL, [_ru_evidence(content=param)]).action_id == action.action_id


# =============================================================================================
# 3. Authorized-command synthesis false positive
# =============================================================================================


def _execution(command: Optional[str]) -> dict[str, Any]:
    return {"diagnostic_step": {"action": "Check the radio unit.", "command": command}, "verified_evidence": [], "approved_commands_catalog": []}


@pytest.mark.parametrize(
    ("command", "sentence"),
    [
        ("st ru", "Please check the Radio Unit using the command `st ru`."),
        ("alt", "Please list the active alarms on the node with the command `alt`."),
        ("st ru", "Could you please check the Radio Unit state with the `st ru` command?"),
        ("st ru", "Kindly check the Radio Unit using command st ru."),
    ],
)
def test_authorized_command_presentation_sentence_is_preserved(command: str, sentence: str) -> None:
    final = enforce_technical_authority_synthesis_boundary(f"{sentence} Please provide the output of this command.", _execution(command))
    assert sentence in final and "output of this command" in final


@pytest.mark.parametrize(
    "question",
    [
        "Please provide the command you normally use.",
        "What command do you use?",
        "Tell me the command.",
        "Could you share the command for this check?",
        "Which tool would you use to read the radio state?",
        "Please check the Radio Unit using `st ru`, or tell me the command you normally use.",
    ],
)
def test_operator_mechanism_questions_are_still_blocked(question: str) -> None:
    for execution in (_execution("st ru"), _execution(None)):
        final = enforce_technical_authority_synthesis_boundary(f"The radio unit state is needed. {question}", execution)
        assert question not in final and "radio unit state is needed" in final


def test_presentation_of_a_command_that_is_not_authorized_this_turn_is_not_preserved() -> None:
    sentence = "Please check the Radio Unit using the command `st ru`."
    for execution in (_execution(None), _execution("alt")):
        assert "st ru" not in enforce_technical_authority_synthesis_boundary(f"The radio unit state is needed. {sentence}", execution)


def test_issued_identity_never_reaches_egress_as_authority() -> None:
    """Prompt 1 egress boundary: an authorization still needs this run's authorized command; a
    procedure_action_id alone (continuity metadata) authorizes nothing."""
    from backend.api.command_egress import current_turn_command_authorizations

    record = {"run_id": "r1", "diagnostic_step": {"command": None, "procedure_action_id": ST_RU_ID}, "approved_commands_catalog": []}
    assert current_turn_command_authorizations("r1", record) == ([], [])
    record = {"run_id": "r1", "diagnostic_step": {"command": "st ru", "command_source": RU_CANONICAL, "procedure_action_id": ST_RU_ID},
              "approved_commands_catalog": []}
    valid, rejected = current_turn_command_authorizations("r1", record)
    assert valid == [] and rejected[0]["reason"] == "not_authorized_by_command_authority_this_run"
    assert "st ru" not in json.dumps(valid)
