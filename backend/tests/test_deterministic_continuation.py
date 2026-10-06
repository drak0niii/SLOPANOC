"""Server-driven troubleshooting continuation ("what next?") and fresh discovery.

The server no longer depends on the specialist remembering to search when the operator asks to go
on with a pending step. Each turn is classified deterministically against server state
(`ContinuationKind`): RESULT_PROVIDED, NEW_OBJECTIVE, CLARIFICATION_ANSWER, COMMAND_FOLLOW_UP,
GENERIC_CONTINUATION or NONE. Only when the state proves there is something valid to continue --
a pending step awaiting its result, a known governed acquisition not blocked by an open
clarification, its source not AVAILABLE in the current run -- does the server force a fresh
governed discovery:

    fresh knowledge_search -> AVAILABLE -> the specialist explicitly selects -> SELECTED
    -> ProcedureAction reconstructed -> resolved -> current Command Authority

The server may force discovery; it never forces selection.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from google.genai import types
from sqlalchemy import delete

from backend.agents.technical_authority_engineer.acquisition_continuity import (
    ContinuationKind,
    ContinuationRule,
    continuation_kind,
    is_acquisition_request,
    pre_run_rule,
)
from backend.agents.technical_authority_engineer.agent_tool import _known_source_available
from backend.agents.technical_authority_engineer.progression_controller import ProgressionController, TurnKind
from backend.cases.evidence_identity import EvidenceIdentity
from backend.cases.troubleshooting_progression import StepStatus, TroubleshootingProgression, TroubleshootingStep
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.tests.test_acquisition_continuity import _known
from backend.tests.test_applicability_blocked_governed_action import (
    DOC,
    TURN2_TEXT,
    _blocked_turn,
    _fc,
    _payload,
    _trace,
    conversation,  # noqa: F401 (fixture)
)
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_governed_continuity_runtime_blockers import (
    RU_CANONICAL,
    RU_KID,
    RU_SEARCH,
    RU_SECTION,
    RU_SELECT,
    ST_RU_ID,
    _legacy_st_ru,
    _ru_mop,
)

RU_IDENTITY = EvidenceIdentity(knowledge_id=RU_KID, version_label="v1", section_id=RU_SECTION)
RU_PRESENTATION = "Please check the Radio Unit using the command `st ru`."
SELECT_NONE = _fc("knowledge_select_evidence", {"selections": []})


def _events(turn: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return [e for e in _trace(turn).get("operational_events", []) if e.get("stage") == stage]


def _continuation(turn: dict[str, Any]) -> dict[str, Any]:
    (event,) = _events(turn, "continuation")
    return event


def _authorized(turn: dict[str, Any], command: str) -> list[dict[str, Any]]:
    return [c for c in _trace(turn).get("command_authority", []) if c["decision"] == "authorized" and c["command"] == command]


def _st_ru_step(action_id: Any = ST_RU_ID) -> list[Any]:
    return _payload(
        {"action": "Check the Radio Unit state.", "reason": "r", "expected_evidence": "Radio unit operational state",
         "procedure_action_id": action_id, "command": None, "command_source": None, "restrictions": []},
        "The radio unit state is still needed.",
    )


def _need_ru_state() -> list[Any]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "insufficient_evidence", "technical_interpretation": "The radio unit state is still needed.",
        "verified_evidence_citations": [], "missing_information": [],
        "required_evidence": [{"kind": "diagnostic_result", "description": "Radio unit operational state"}],
    }))]


def _plain(text: str = "Noted.") -> list[Any]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "insufficient_evidence", "technical_interpretation": text, "verified_evidence_citations": [], "missing_information": [],
    }))]


async def _st_ru_presented(conv: Any) -> dict[str, Any]:
    """Turn 1: `st ru` authorized (MATCH) and presented; the step keeps its governed identity."""
    t1 = await conv.turn("how do I troubleshoot the radio unit fault?", [RU_SEARCH, RU_SELECT, _legacy_st_ru()], RU_PRESENTATION)
    (step,) = t1["progression"].steps
    assert step.status is StepStatus.PRESENTED and step.procedure_action_id == ST_RU_ID and step.selected_evidence == [RU_IDENTITY]
    return t1


def _selection_states(turn: dict[str, Any]) -> dict[str, str]:
    return {r["section_id"]: r["selection_state"] for r in _trace(turn)["searches"][-1]["results"]}


# =============================================================================================
# "what next?" with a known governed acquisition -> fresh discovery, explicit selection
# =============================================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["what next?", "what should I do now?", "continue", "okay, next?"])
async def test_what_next_with_known_governed_acquisition_forces_fresh_discovery(conversation, message: str) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_ru_mop())
    t1 = await _st_ru_presented(conv)
    # The specialist never searches by itself here: discovery is the server's.
    t2 = await conv.turn(message, [RU_SELECT, _st_ru_step()], RU_PRESENTATION)
    continuation = _continuation(t2)
    assert (continuation["kind"], continuation["rule"]) == ("generic_continuation", "generic_continuation")
    assert continuation["known_procedure_action_id"] == ST_RU_ID and continuation["known_source_available_in_run"] is False
    (discovery,) = _events(t2, "acquisition_discovery")
    assert discovery["reason"] == "generic_continuation" and discovery["status"] == "searched"
    assert [(a["section_id"], a["applicability_outcome"]) for a in discovery["available"]] == [(RU_SECTION, "match")]
    assert _selection_states(t2)[RU_SECTION] == "SELECTED", "selected by the specialist's own call"
    resolution = _trace(t2)["action_resolutions"][-1]
    assert (resolution["action_id"], resolution["status"], resolution["command_authority"]) == (ST_RU_ID, "resolved", "authorized")
    (step,) = t2["progression"].steps
    assert step.step_id == t1["progression"].steps[0].step_id and step.status is StepStatus.PRESENTED and step.command == "st ru"
    assert "`st ru`" in t2["final"]


@pytest.mark.asyncio
async def test_search_returns_source_but_selection_stays_explicit(conversation) -> None:  # noqa: F811
    """Discovery is forced; selection is not. The specialist reuses the known id without selecting:
    nothing is issued, the source stays AVAILABLE, no authority."""
    repo, conv = conversation
    await repo.add(_ru_mop())
    await _st_ru_presented(conv)
    t2 = await conv.turn("what next?", [_st_ru_step(), _st_ru_step()], RU_PRESENTATION)
    (discovery,) = _events(t2, "acquisition_discovery")
    assert discovery["reason"] == "generic_continuation" and discovery["status"] == "searched"
    assert _selection_states(t2)[RU_SECTION] == "AVAILABLE", "AVAILABLE is never promoted"
    assert t2["tae_calls"] == 2, "one bounded selection remediation"
    assert not _authorized(t2, "st ru") and not (t2["records"][-1].get("diagnostic_step") or {}).get("command")
    assert "`st ru`" not in t2["final"]
    # An explicit selection in the next turn re-derives and re-authorizes it (fresh discovery again).
    t3 = await conv.turn("continue", [RU_SELECT, _st_ru_step()], RU_PRESENTATION)
    assert _events(t3, "acquisition_discovery")[0]["reason"] == "generic_continuation"
    assert _authorized(t3, "st ru") and "`st ru`" in t3["final"]


@pytest.mark.asyncio
async def test_prior_run_selected_evidence_does_not_count_as_available(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_ru_mop())
    t1 = await _st_ru_presented(conv)
    assert t1["progression"].steps[0].selected_evidence == [RU_IDENTITY], "selected in the PREVIOUS run"
    t2 = await conv.turn("okay, next?", [RU_SELECT, _st_ru_step()], RU_PRESENTATION)
    assert _continuation(t2)["known_source_available_in_run"] is False
    assert [e["reason"] for e in _events(t2, "acquisition_discovery")] == ["generic_continuation"]
    # Unit: an earlier run's selection is never this run's AVAILABLE evidence.
    assert _known_source_available("run-never-searched", _known(identity=RU_IDENTITY)) is False
    assert _known_source_available("run-never-searched", _known(identity=None)) is False


@pytest.mark.asyncio
async def test_fresh_search_returning_nothing_yields_a_governed_acquisition_gap(conversation) -> None:  # noqa: F811
    from backend.knowledge.repository.sqlalchemy import KnowledgeObjectRecord

    repo, conv = conversation
    await repo.add(_ru_mop())
    await _st_ru_presented(conv)
    async with repo._session_factory() as session:
        await session.execute(delete(KnowledgeObjectRecord).where(KnowledgeObjectRecord.knowledge_id == RU_KID))
        await session.commit()
    t2 = await conv.turn("what next?", [_need_ru_state()], "Okay.")
    (discovery,) = _events(t2, "acquisition_discovery")
    assert discovery["reason"] == "generic_continuation" and discovery["status"] == "searched" and discovery["available"] == []
    acquisition = t2["records"][-1]["evidence_acquisition"]
    assert acquisition["outcome"] == "gap" and acquisition["gap_reason"] == "no_relevant_evidence_found"
    (candidate,) = [c for c in acquisition["candidates"] if c["acquisition_type"] == "governed_action"]
    assert candidate["procedure_action_id"] == ST_RU_ID and candidate["validation"] == "rejected_current_run", "kept, never erased"
    assert not _authorized(t2, "st ru") and "st ru" not in t2["final"]


# =============================================================================================
# Nothing valid to continue -> nothing forced, nothing invented
# =============================================================================================


@pytest.mark.asyncio
async def test_what_next_without_known_acquisition_invents_no_method(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_ru_mop())
    # A fabricated command is refused: the pending step has no governed identity (nothing known).
    t1 = await conv.turn("how do I troubleshoot the radio unit fault?", [RU_SEARCH, RU_SELECT, _legacy_st_ru(command="show everything-now")], "Checking.")
    (step,) = t1["progression"].steps
    assert step.status is StepStatus.PRESENTED and step.procedure_action_id is None and step.command is None
    t2 = await conv.turn("what next?", [_need_ru_state()], "Okay.")
    continuation = _continuation(t2)
    assert (continuation["kind"], continuation["rule"], continuation["known_procedure_action_id"]) == ("generic_continuation", None, None)
    assert _events(t2, "acquisition_discovery") == []
    assert [e["outcome"] for e in _events(t2, "acquisition_continuity")] == ["no_known_governed_acquisition"]
    assert t2["tae_calls"] == 1
    assert not [c for c in _trace(t2).get("command_authority", []) if c["decision"] == "authorized"]
    assert not (t2["records"][-1].get("diagnostic_step") or {}).get("command")


@pytest.mark.asyncio
async def test_new_objective_is_never_hijacked_by_continuation(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_ru_mop())
    await _st_ru_presented(conv)
    t2 = await conv.turn("forget that check, investigate the synchronization fault instead", [_plain("Synchronization first.")], "Okay.")
    assert _continuation(t2)["kind"] == "new_objective" and _continuation(t2)["rule"] is None
    assert _events(t2, "acquisition_discovery") == []
    assert [s.status for s in t2["progression"].steps] == [StepStatus.SUPERSEDED]
    assert not _authorized(t2, "st ru") and "st ru" not in t2["final"]


@pytest.mark.asyncio
async def test_pasted_result_takes_the_result_path_not_continuation(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_ru_mop())
    await _st_ru_presented(conv)
    t2 = await conv.turn("st ru output:\nRadioUnit=1 OPER=ENABLED AVAIL=", [_plain("The radio unit is enabled.")], "The radio unit is enabled.")
    assert _continuation(t2)["kind"] == "result_provided" and _continuation(t2)["rule"] is None
    assert _events(t2, "acquisition_discovery") == []
    (step,) = t2["progression"].steps
    assert step.status in (StepStatus.OBSERVED, StepStatus.VERIFIED, StepStatus.COMPLETED) and step.result is not None


@pytest.mark.asyncio
async def test_command_follow_up_keeps_the_existing_command_flow(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_ru_mop())
    await _st_ru_presented(conv)
    t2 = await conv.turn("what command do I use?", [RU_SELECT, _st_ru_step()], RU_PRESENTATION)
    assert (_continuation(t2)["kind"], _continuation(t2)["rule"]) == ("command_follow_up", "acquisition_request")
    assert [e["reason"] for e in _events(t2, "acquisition_discovery")] == ["acquisition_request"]
    assert _authorized(t2, "st ru") and "`st ru`" in t2["final"]


@pytest.mark.asyncio
async def test_clarification_answer_keeps_the_existing_clarification_flow(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _blocked_turn(conv)
    t2 = await conv.turn(TURN2_TEXT, [DOC.select(), _plain()], "Run `alt`.")
    assert (_continuation(t2)["kind"], _continuation(t2)["rule"]) == ("clarification_answer", "applicability_clarification_resolved")
    assert [e["reason"] for e in _events(t2, "retrieval_resumption")] == ["applicability_clarification_resolved"]
    assert _events(t2, "acquisition_discovery") == []


# =============================================================================================
# Selection remediation: governed search first when nothing is AVAILABLE in this run
# =============================================================================================


@pytest.mark.asyncio
async def test_selection_remediation_searches_first_when_nothing_is_available(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_ru_mop())
    # The specialist recommends a governed step without searching or selecting anything.
    t1 = await conv.turn("how do I troubleshoot the radio unit fault?", [_legacy_st_ru(), RU_SELECT, _legacy_st_ru()], RU_PRESENTATION)
    (discovery,) = _events(t1, "selection_discovery")
    assert discovery["status"] == "searched" and [a["section_id"] for a in discovery["available"]] == [RU_SECTION]
    assert _selection_states(t1)[RU_SECTION] == "SELECTED", "selected by the specialist after the server's search"
    assert t1["records"][-1]["diagnostic_step"]["command"] == "st ru" and t1["records"][-1]["diagnostic_step"]["command_source"] == RU_CANONICAL


@pytest.mark.asyncio
async def test_selection_remediation_never_selects_on_the_specialists_behalf(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_ru_mop())
    t1 = await conv.turn("how do I troubleshoot the radio unit fault?", [_legacy_st_ru(), _legacy_st_ru()], RU_PRESENTATION)
    assert _events(t1, "selection_discovery")[0]["status"] == "searched"
    assert _selection_states(t1)[RU_SECTION] == "AVAILABLE"
    assert not (t1["records"][-1].get("diagnostic_step") or {}).get("command") and "`st ru`" not in t1["final"]


@pytest.mark.asyncio
async def test_selection_remediation_with_available_evidence_does_not_search_again(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_ru_mop())
    t1 = await conv.turn("how do I troubleshoot the radio unit fault?", [RU_SEARCH, _legacy_st_ru(), RU_SELECT, _legacy_st_ru()], RU_PRESENTATION)
    assert _events(t1, "selection_discovery") == []
    assert t1["records"][-1]["diagnostic_step"]["command"] == "st ru"


# =============================================================================================
# Deterministic classification (unit)
# =============================================================================================


@pytest.mark.parametrize(
    ("turn_kind", "kw", "expected"),
    [
        ("command_follow_up", {}, ContinuationKind.GENERIC_CONTINUATION),
        ("command_follow_up", {"mechanism_requested": True}, ContinuationKind.COMMAND_FOLLOW_UP),
        ("result", {}, ContinuationKind.RESULT_PROVIDED),
        ("unvalidated_result", {}, ContinuationKind.RESULT_PROVIDED),
        ("objective_change", {}, ContinuationKind.NEW_OBJECTIVE),
        ("command_follow_up", {"new_objective": True}, ContinuationKind.NEW_OBJECTIVE),
        ("command_follow_up", {"clarification_answered": True}, ContinuationKind.CLARIFICATION_ANSWER),
        ("other", {}, ContinuationKind.NONE),
        ("blocked", {}, ContinuationKind.NONE),
        ("no_pending", {}, ContinuationKind.NONE),
    ],
)
def test_continuation_kind_is_derived_from_state(turn_kind: str, kw: dict[str, bool], expected: ContinuationKind) -> None:
    args = {"clarification_answered": False, "new_objective": False, "mechanism_requested": False, **kw}
    assert continuation_kind(turn_kind, **args) is expected


def test_generic_continuation_rule_needs_a_known_acquisition_free_of_open_clarification() -> None:
    generic = ContinuationKind.GENERIC_CONTINUATION
    assert pre_run_rule(_known(), clarification_answered=False, command_follow_up=True, binding=None, continuation=generic) is ContinuationRule.GENERIC_CONTINUATION
    assert pre_run_rule(None, clarification_answered=False, command_follow_up=True, binding=None, continuation=generic) is None
    assert pre_run_rule(_known(clarification_open=True), clarification_answered=False, command_follow_up=True, binding=None, continuation=generic) is None
    for kind in (ContinuationKind.RESULT_PROVIDED, ContinuationKind.NEW_OBJECTIVE, ContinuationKind.NONE):
        assert pre_run_rule(_known(), clarification_answered=False, command_follow_up=True, binding=None, continuation=kind) is None
    assert pre_run_rule(_known(), clarification_answered=False, command_follow_up=True, binding=None,
                        continuation=ContinuationKind.COMMAND_FOLLOW_UP) is ContinuationRule.ACQUISITION_REQUEST


def _pending_controller() -> ProgressionController:
    progression = TroubleshootingProgression()
    controller = ProgressionController({}, TroubleshootingState(fault_id="F-1", symptom_summary="ru"), progression=progression)
    step = TroubleshootingStep(fault_id="F-1", objective="Check the Radio Unit state.", command="st ru", expected_evidence="Radio unit operational state")
    progression.append_step(step)
    progression.set_step_status(step.step_id, StepStatus.VALIDATED)
    progression.set_step_status(step.step_id, StepStatus.PRESENTED)
    return controller


@pytest.mark.parametrize(
    ("text", "turn_kind", "mechanism"),
    [
        ("what next?", TurnKind.COMMAND_FOLLOW_UP, False),
        ("what should I do now?", TurnKind.COMMAND_FOLLOW_UP, False),
        ("continue", TurnKind.COMMAND_FOLLOW_UP, False),
        ("okay, next?", TurnKind.COMMAND_FOLLOW_UP, False),
        ("go ahead", TurnKind.COMMAND_FOLLOW_UP, False),
        ("what command do I use?", TurnKind.COMMAND_FOLLOW_UP, True),
        ("how do I check that?", TurnKind.COMMAND_FOLLOW_UP, True),
        ("st ru?", TurnKind.COMMAND_FOLLOW_UP, True),
        ("the customer called again about the outage", TurnKind.OTHER, False),
        ("what does ESS stand for?", TurnKind.OTHER, False),
    ],
)
def test_turn_classification_separates_method_requests_from_continuation(text: str, turn_kind: TurnKind, mechanism: bool) -> None:
    controller = _pending_controller()
    assert controller.classify_turn(text) is turn_kind
    assert controller.mechanism_requested is mechanism
    assert is_acquisition_request(text) is (mechanism and text != "st ru?")
