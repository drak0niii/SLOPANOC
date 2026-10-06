"""Applicability clarification resolved -> governed retrieval resumes server-side.

Live defect (session 97f73a9e):
    run c06107fa  "how can i troubleshoot ESS Service Unavailable ?"  search + selection, applicability
                  UNKNOWN, `alt` refused, technology + vendor requested -- but the specialist's own
                  integrity callback had already stripped `alt` from its output, so the blocked governed
                  identity was never derived: the step was persisted as a plain observation
    run 43d6f0c6  "Ericsson, 4G"  clarification RESOLVED, facts persisted -- then NO knowledge_search
                  and NO knowledge_select_evidence in the run (governed_search_performed=False); the
                  specialist asked for the pending step's output; chat_service correctly failed closed

Invariant under test:
    clarification_answer + every applicability field resolved + active governed objective
      -> the server runs a FRESH governed search in the current run under the confirmed context
      -> selection stays explicit, applicability is recomputed, authority is re-derived from scratch
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from backend.agents.team_manager.governed_knowledge_completion import SAFE_COMPLETION_FAILURE_TEXT
from backend.agents.technical_authority_engineer.synthesis_boundary import NO_GOVERNED_PROCEDURE_SELECTED_TEXT
from backend.cases.troubleshooting_progression import ClarificationStatus, StepStatus
from backend.knowledge.domain.applicability import Applicability
from backend.tests.test_applicability_blocked_governed_action import (
    CATALOG,
    DOC,
    SEARCH,
    _Doc,
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
TURN2 = "Ericsson, 4G"
# As live: Team Manager restates the problem and passes the facts in lower case.
TM_TURN2_ARGS = {
    "problem_statement": "ESS Service Unavailable",
    "verified_symptoms": ["ESS Service Unavailable"],
    "known_applicability_facts": {"vendor": ["ericsson"], "technology": ["4g"]},
}
TM_TURN1_ARGS = {"problem_statement": "ESS Service Unavailable"}
ALT_ID = DOC.action_id("alt")


@pytest.fixture
def conversation(isolated_km_repo, monkeypatch):  # noqa: F811
    use_production_specialist(monkeypatch)
    return isolated_km_repo, _Conversation(monkeypatch)


def _events(turn: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return [e for e in _trace(turn).get("operational_events", []) if e.get("stage") == stage]


async def _turn1(conv: _Conversation) -> dict[str, Any]:
    t1 = await conv.turn(
        TURN1, [SEARCH, DOC.select(), CATALOG, _legacy_alt()],
        "The first step is to check for active alarms on the node. Please provide the technology and vendor.",
        tae_args=TM_TURN1_ARGS,
    )
    trace = _trace(t1)
    assert len(trace["searches"]) == 1 and trace["searches"][0]["applicability_context"] == {}
    assert t1["records"][-1]["applicability_clarification"]["missing_dimensions"] == ["technology", "vendor"]
    assert t1["completed"]["knowledge_sources"][0]["applicability_outcome"] == "unknown"  # Candidate source
    assert not [c for c in trace["command_authority"] if c["decision"] == "authorized"]
    assert "alt" not in t1["final"].split()
    # Production specialist (integrity callback strips `alt` from its own output): the blocked
    # governed identity is still derived -- the live turn-1 divergence.
    (step,) = t1["progression"].steps
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION
    assert step.blocked_candidate is not None and step.blocked_candidate.procedure_action_id == ALT_ID
    return t1


# =============================================================================================
# 8. Exact live sequence through ChatService + Team Manager + production TAE agent
# =============================================================================================


@pytest.mark.asyncio
async def test_live_sequence_resolved_clarification_resumes_governed_retrieval(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _turn1(conv)
    (blocked_step,) = t1["progression"].steps

    # Turn 2: the scripted specialist does NOT search on its own (as live) -- it only selects from the
    # server's resumed retrieval, reads the catalog and chooses the governed action.
    t2 = await conv.turn(
        TURN2,
        [DOC.select(), CATALOG, _governed(ALT_ID, "Check for active alarms on the affected Ericsson 4G node")],
        "The next diagnostic step is to check for active alarms on the node. Run `alt`. Please provide the output of this command.",
        tae_args=TM_TURN2_ARGS,
    )
    trace = _trace(t2)
    request = trace["turn_requests"][-1]
    assert (request["request_kind"], request["focus"]) == ("clarification_answer", "continue")
    question = t2["progression"].open_questions[0]
    assert question.status is ClarificationStatus.RESOLVED
    assert {k: [v.casefold() for v in vals] for k, vals in t2["state"]["confirmed_applicability_facts"].items()} == {
        "vendor": ["ericsson"], "technology": ["4g"]
    }

    # Server-side resumption: ONE fresh governed search in this run under the confirmed context.
    (resumption,) = _events(t2, "retrieval_resumption")
    assert resumption["status"] == "searched" and resumption["clarification_id"] == question.question_id
    assert resumption["previous_selected_sources"] == [DOC.canonical]
    assert [(a["section_id"], a["applicability_outcome"]) for a in resumption["available"] if a["section_id"] == DOC.section] == [(DOC.section, "match")]
    (search,) = trace["searches"]
    assert {k: [v.casefold() for v in vals] for k, vals in search["applicability_context"].items()} == {"vendor": ["ericsson"], "technology": ["4g"]}
    # Fresh, explicit selection; recomputed applicability MATCH; contract reflects real runtime.
    assert trace["selections"][-1]["status"] == "accepted" and trace["selected"] == [
        {"knowledge_id": DOC.kid, "version_label": DOC.ver, "section_id": DOC.section}
    ]
    assert trace["technical_authority"]["selection_contract"]["governed_search_performed"] is True
    (outcome,) = _events(t2, "retrieval_resumption_outcome")
    assert outcome["governed_search_performed"] is True and outcome["selected"] == [{"source_id": DOC.canonical, "applicability_outcome": "match"}]
    # Normal authority chain, freshly in this run.
    assert trace["action_catalogs"][-1]["actions"][0]["action_id"] == ALT_ID
    assert trace["action_resolutions"][-1]["command_authority"] == "authorized"
    assert any(c["decision"] == "authorized" and c["command"] == "alt" and c["source_id"] == DOC.canonical for c in trace["command_authority"])
    record = t2["records"][-1]
    assert [(c["command"], c["source_id"]) for c in record["approved_commands_catalog"]] == [("alt", DOC.canonical)]
    assert record["diagnostic_step"]["command"] == "alt" and record["diagnostic_step"]["command_source"] == DOC.canonical
    # Blocked step continued (same step, no duplicate, not rejected).
    control = _progression_control(t2)
    assert control["decision"] == "pending_step_resolved"
    assert any(e["event"] == "blocked_step_continued" for e in control["events"])
    (step,) = t2["progression"].steps
    assert step.step_id == blocked_step.step_id and step.status is StepStatus.PRESENTED and step.command == "alt"
    # Final response: not fail-closed, command present, Source (MATCH).
    assert trace["final_response_kind"] != "governed_fail_closed"
    assert t2["final"] != SAFE_COMPLETION_FAILURE_TEXT and "`alt`" in t2["final"]
    assert t2["completed"]["knowledge_sources"][0]["applicability_outcome"] == "match"


# =============================================================================================
# 9. Required negative tests
# =============================================================================================


@pytest.mark.asyncio
async def test_a_partial_answer_does_not_resume_retrieval(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _turn1(conv)
    t2 = await conv.turn("Ericsson", [SEARCH, DOC.select(), _legacy_alt()], "Noted.", tae_args={"problem_statement": "Ericsson"})
    assert t2["tae_calls"] == 0
    assert _events(t2, "retrieval_resumption") == [] and _trace(t2).get("searches", []) == []
    assert t2["final"] == "Recorded: vendor = Ericsson.\nTo confirm which governed procedure applies, I still need:\n- technology"
    (question,) = t2["progression"].open_questions
    assert question.status is ClarificationStatus.OPEN and question.unresolved_fields == ["technology"]
    (step,) = t2["progression"].steps
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION and step.command is None
    assert t2["records"][-1].get("diagnostic_step") is None


@pytest.mark.asyncio
async def test_b_resolved_but_nothing_applicable_still_fails_closed_on_fresh_state(conversation) -> None:
    repo, conv = conversation
    other = _Doc("OTHER-VENDOR-MOP", "MOP_Other Vendor.docx")
    other_ko = other.knowledge().model_copy(update={"applicability": Applicability(dimensions={"vendor": ["Nokia"], "technology": ["3G"]})})
    await repo.add(DOC.knowledge())
    await repo.add(other_ko)
    await _turn1(conv)
    t2 = await conv.turn(
        "Nokia, 3G",
        [_fc("knowledge_select_evidence", {"selections": []}), _payload_insufficient()],
        "Run `alt`. Please provide the output of this command.",
        tae_args={"problem_statement": "ESS Service Unavailable", "known_applicability_facts": {"vendor": ["Nokia"], "technology": ["3G"]}},
    )
    (resumption,) = _events(t2, "retrieval_resumption")
    assert resumption["status"] == "searched"
    assert all(a["applicability_outcome"] != "match" for a in resumption["available"] if a["section_id"] == DOC.section)
    contract = _trace(t2)["technical_authority"]["selection_contract"]
    assert contract["governed_search_performed"] is True and contract["explicit_negative_selection"] is True
    record = t2["records"][-1]
    assert record["approved_commands_catalog"] == [] and not (record.get("diagnostic_step") or {}).get("command")
    assert t2["final"].startswith(NO_GOVERNED_PROCEDURE_SELECTED_TEXT)
    assert "alt" not in t2["final"].split() and "`alt`" not in t2["final"] and "this command" not in t2["final"]
    (step,) = t2["progression"].steps
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION and step.command is None


def _payload_insufficient() -> list:
    from google.genai import types

    return [types.Part.from_text(text=json.dumps({
        "outcome": "insufficient_evidence", "technical_interpretation": "No applicable governed procedure for this context.",
        "missing_information": [],
    }))]


@pytest.mark.asyncio
async def test_c_search_failure_never_reuses_prior_turn_evidence(conversation, monkeypatch) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _turn1(conv)

    from backend.knowledge.tools import service as tool_service_module  # noqa: F401
    from backend.tools.knowledge import tools as knowledge_tools
    from backend.tools.knowledge.runtime import KnowledgeRuntimeError

    class _Broken:
        async def search(self, *args: Any, **kwargs: Any) -> Any:
            raise KnowledgeRuntimeError("simulated retrieval failure")

    monkeypatch.setattr(knowledge_tools, "get_knowledge_tool_service", lambda: _Broken())
    t2 = await conv.turn(
        TURN2,
        [DOC.select(), _governed(ALT_ID, "Check for active alarms on the node."), _payload_insufficient()],
        "Run `alt`. Please provide the output of this command.",
        tae_args=TM_TURN2_ARGS,
    )
    (resumption,) = _events(t2, "retrieval_resumption")
    assert resumption["status"] == "search_failed" and resumption["available"] == []
    trace = _trace(t2)
    assert trace["selected"] == [] and all(s["status"] != "accepted" for s in trace["selections"])
    assert not [c for c in trace["command_authority"] if c["decision"] == "authorized"]
    record = t2["records"][-1]
    assert record["approved_commands_catalog"] == [] and not (record.get("diagnostic_step") or {}).get("command")
    assert "`alt`" not in t2["final"] and "this command" not in t2["final"]
    (step,) = t2["progression"].steps
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION and step.command is None


@pytest.mark.asyncio
async def test_d_resolving_on_another_fault_never_resumes_fault_a(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _turn1(conv)
    fault_a = t1["progression"].active_fault_id
    text_b = "now troubleshoot the fan unit overheating on Ericsson 4G"
    t2 = await conv.turn(
        text_b, [_payload_insufficient()], "Looking at the fan unit.",
        tae_args={"problem_statement": text_b, "current_request": {"subject_component": "fan unit", "continues_active_objective": False}},
    )
    assert t2["progression"].active_fault_id != fault_a
    assert _events(t2, "retrieval_resumption") == [] and _trace(t2).get("searches", []) == []
    question_a = t2["progression"].pending_clarification(fault_a)
    assert question_a is not None and question_a.status is ClarificationStatus.OPEN and question_a.resolved_values == {}
    step_a = next(s for s in t2["progression"].steps if s.fault_id == fault_a)
    assert step_a.status is StepStatus.BLOCKED_BY_CLARIFICATION


@pytest.mark.asyncio
async def test_e_values_without_a_pending_clarification_force_nothing(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await conv.turn(TURN2, [_payload_insufficient()], "Could you describe the fault?", tae_args={"problem_statement": TURN2})
    assert _trace(t1)["turn_requests"][-1]["request_kind"] == "operational"
    assert _events(t1, "retrieval_resumption") == [] and _trace(t1).get("searches", []) == []


@pytest.mark.asyncio
async def test_f_meta_question_renders_from_state_without_retrieval(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _turn1(conv)
    t2 = await conv.turn("what details should i provide?", [SEARCH, _payload_insufficient()], "The node name.")
    assert t2["tae_calls"] == 0
    assert _events(t2, "retrieval_resumption") == [] and _trace(t2).get("searches", []) == []
    assert t2["final"] == "To confirm which governed procedure applies, I still need:\n- technology\n- vendor"


@pytest.mark.asyncio
async def test_g_resumed_alt_is_one_step_and_never_rejected(conversation) -> None:
    """The specialist may also search on its own after the resumption; continuity is unchanged."""
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _turn1(conv)
    t2 = await conv.turn(
        TURN2, [SEARCH, DOC.select(), CATALOG, _governed(ALT_ID, "List the active alarms")], "Run `alt`.", tae_args=TM_TURN2_ARGS,
    )
    assert len(_trace(t2)["searches"]) == 2  # server resumption + the specialist's own search
    control = _progression_control(t2)
    assert control["decision"] == "pending_step_resolved" and control["decision"] != "rejected_pending_awaits_result"
    assert len(t2["progression"].steps) == 1 and t2["records"][-1]["diagnostic_step"]["command"] == "alt"


def test_resumed_retrieval_is_server_owned_in_the_request_contract() -> None:
    from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityRequest

    assert "resumed_governed_retrieval" in TechnicalAuthorityRequest.model_fields
