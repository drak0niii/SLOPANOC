"""Server-owned operational continuation routing.

Live defect (Prompt 4 acceptance, session 0847703a-fbcb-40ae-b8c1-46b89c3b7a47, turn 4):
    turn 3 bound the `alt` result, recorded a non-terminal gap, presented the governed `st cell` step
    turn 4 "okie, and now what you suggest i do ?" -> the Team Manager answered from conversation
    history; the Technical Authority Engineer was never called (no search, no selection, no
    ProcedureAction, no Command Authority); its prose repeated `st cell`, the universal egress removed
    it, and the operator received "This will allow us to proceed with the troubleshooting."

Invariant under test:
    ACTIVE OPERATIONAL FAULT + UNFINISHED INVESTIGATION + OPERATIONAL CONTINUATION
        -> the SERVER routes the turn through the governed operational pipeline; the Team Manager
           cannot answer before the specialist ran (request-level function-calling constraint)
    Historical command text is never current authority; non-operational / new-objective / social turns
    keep normal routing; a forced turn never ends vacuous and fails closed if the specialist fails.

The Team Manager model is emulated by `_RoutingAwareLlm`, which honours `tool_config` exactly as the
Gemini API does (ANY + allowed names -> only those function calls; NONE -> no function call), while
its script tries to answer directly from history -- the live behaviour.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.genai import types

from backend.agents.team_manager import operational_routing as routing
from backend.agents.team_manager.operational_routing import (
    OPERATIONAL_ROUTE_UNAVAILABLE_TEXT,
    OperationalRoute,
    OperationalTurnKind,
    classify_operational_turn,
    decide_operational_route,
    unfinished_operational_work,
)
from backend.agents.technical_authority_engineer import agent_tool as tae_agent_tool
from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityRequest, TechnicalAuthorityResponse
from backend.api.chat_service import ChatService
from backend.api.streaming_events import StreamEventType
from backend.cases.evidence_model import AcquisitionGap, EvidenceKind, EvidenceRequirement, GapReason
from backend.cases.service import CaseService
from backend.cases.troubleshooting_progression import (
    PROGRESSION_STATE_KEY,
    FaultProgression,
    ProgressionPhase,
    ResolutionState,
    StepStatus,
    TroubleshootingProgression,
    TroubleshootingStep,
)
from backend.tests.test_applicability_blocked_governed_action import (
    CATALOG,
    DOC,
    SEARCH,
    _governed,
    _trace,
    use_production_specialist,
)
from backend.tests.test_clarification_continuity import _Conversation, _fc
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tools.knowledge.diagnostic_trace import RETRIEVAL_DIAGNOSTICS_STATE_KEY, format_diagnostic_trace
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence

ALT_ID = DOC.action_id("alt")
PLUGIN_ID = DOC.action_id("st pluginunit")
TURN1 = "how can i troubleshoot ESS Service Unavailable on Ericsson 4G?"
TURN1_ARGS = {"problem_statement": "ESS Service Unavailable", "known_applicability_facts": {"vendor": ["ericsson"], "technology": ["4g"]}}
LIVE_TURN4 = "okie, and now what you suggest i do ?"
# As live: the Team Manager answers from history, repeating an earlier command.
HISTORY_ANSWER = "This will allow us to proceed with the troubleshooting. Run `alt` again and share the output."
ALT_RESULT = (
    "alt\n"
    "Date & Time (Local) S Specific Problem                    MO (Cause/AdditionalInfo)\n"
    "2026-10-04 08:03:12 M Service Unavailable                 ENodeBFunction=1,EUtranCellFDD=CELL_2 (Cell is unable to provide service)\n"
    "2026-10-04 08:02:44 M Link Failure                        Equipment=1,FieldReplaceableUnit=UNIT-2,RfPort=A (Link Failure)\n"
    ">>> Total: 2 Alarms (2 Major)\n"
)


def _insufficient(missing: Optional[list[str]] = None) -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "insufficient_evidence", "technical_interpretation": "Awaiting the result of the current check.",
        "verified_evidence_citations": [], "missing_information": missing or [],
    }))]


class _RoutingAwareLlm(BaseLlm):
    """Scripted Team Manager that honours `tool_config` like the Gemini API: under ANY only an allowed
    function call can be produced (a non-allowed scripted step is replaced by a call to the allowed
    function and NOT consumed); under NONE no function call can be produced (scripted calls are
    skipped). Records every function-calling mode it was given."""

    def __init__(
        self, model: str, parts_by_call: list[list[types.Part]], parallel_calls: int = 1,
        extra_calls: tuple[str, ...] = (), ignore_tool_config: bool = False, **kwargs: Any,
    ) -> None:
        super().__init__(model=model, **kwargs)
        self._script = list(parts_by_call)
        self._modes: list[Optional[str]] = []
        self._parallel = parallel_calls
        self._extra = extra_calls
        self._ignore = ignore_tool_config

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        config = getattr(getattr(llm_request.config, "tool_config", None), "function_calling_config", None)
        mode = getattr(getattr(config, "mode", None), "value", None)
        self._modes.append(mode)
        if self._ignore:
            mode = None  # a non-compliant model: answers from its script whatever the request says
        if mode == "ANY":
            allowed = list(config.allowed_function_names or [])
            head = self._script[0] if self._script else None
            name = getattr(getattr(head[0], "function_call", None), "name", None) if head else None
            if name in allowed:
                parts = self._script.pop(0)
            else:
                user_text = next(
                    (p.text for c in reversed(llm_request.contents or []) if c.role == "user" for p in (c.parts or []) if p.text), ""
                )
                # Live (Gemini under ANY): sometimes several parallel calls in ONE response.
                parts = [p for _ in range(self._parallel) for p in _fc(allowed[0], {"problem_statement": user_text})]
                # Live: degenerate extra calls to tools that do not exist.
                parts = [p for name in self._extra for p in _fc(name, {})] + parts
        elif mode == "NONE":
            while self._script and getattr(self._script[0][0], "function_call", None) is not None:
                self._script.pop(0)
            parts = self._script.pop(0) if self._script else [types.Part.from_text(text="")]
        else:
            parts = self._script.pop(0) if self._script else [types.Part.from_text(text="")]
        yield LlmResponse(content=types.Content(role="model", parts=parts), partial=False)


class _RoutedConversation(_Conversation):
    """`_Conversation` with a Team Manager model that obeys request-level function-calling constraints."""

    async def turn(  # type: ignore[override]
        self,
        user_text: str,
        tae_calls: list[list[types.Part]],
        tm_final_text: str,
        *,
        tae_args: Optional[dict[str, Any]] = None,
        delegate: bool = True,
        requires_governed_knowledge: bool = True,
        parallel_calls: int = 1,
        extra_calls: tuple[str, ...] = (),
        ignore_tool_config: bool = False,
        presentation_text: Optional[str] = None,
        script_override: Optional[list[list[types.Part]]] = None,
    ) -> dict[str, Any]:
        import backend.tests.test_clarification_continuity as tcc
        from backend.agents.team_manager.agent import team_manager
        from backend.agents.team_manager.source_requirements import record_source_requirements
        from backend.api.session_service import APP_NAME

        if self.session_id is None:
            self.session_id = await self.session_service.create_session(user_id="test-engineer")
        before = len(self.records)
        tae_llm = tcc._ScriptedLlm(model="scripted-tae", parts_by_call=tae_calls)
        specialist = tcc.Agent(
            name="technical_authority_engineer", model=tae_llm, tools=[knowledge_search, knowledge_select_evidence],
            input_schema=TechnicalAuthorityRequest, output_schema=TechnicalAuthorityResponse,
        )
        tae_tool = TechnicalAuthorityAgentTool(agent=specialist)
        script = [_fc("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": requires_governed_knowledge})]
        if delegate:
            script.append(_fc("technical_authority_engineer", tae_args or {"problem_statement": user_text}))
        script.append([types.Part.from_text(text=tm_final_text)])
        if presentation_text is not None:
            script.append([types.Part.from_text(text=presentation_text)])
        if script_override is not None:
            script = list(script_override)
        tm_llm = _RoutingAwareLlm(
            model="scripted-tm", parts_by_call=script, parallel_calls=parallel_calls, extra_calls=extra_calls,
            ignore_tool_config=ignore_tool_config,
        )
        outer = team_manager.model_copy(update={"model": tm_llm, "tools": [record_source_requirements, tae_tool]})
        runner = Runner(app_name=APP_NAME, agent=outer, session_service=self.session_service.adk_session_service)
        chat = ChatService(session_service=self.session_service, runner=runner, case_service=CaseService())
        final, completed = None, {}
        async for event in chat.execute_turn_events(session_id=self.session_id, message_text=user_text, user_id="test-engineer"):
            if event.type == StreamEventType.MESSAGE_COMPLETED:
                final, completed = event.data.get("content"), dict(event.data)
        session = await self.session_service.get_session(self.session_id, "test-engineer")
        state = dict(session.state)
        progression = TroubleshootingProgression.model_validate(state[PROGRESSION_STATE_KEY]) if state.get(PROGRESSION_STATE_KEY) else None
        return {
            "final": final or "", "completed": completed, "tae_calls": tae_llm._call_count, "tm_modes": tm_llm._modes,
            "records": self.records[before:], "state": state, "progression": progression,
        }


@pytest.fixture
def conversation(isolated_km_repo, monkeypatch):  # noqa: F811
    use_production_specialist(monkeypatch)
    return isolated_km_repo, _RoutedConversation(monkeypatch)


def _events(turn: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return [e for e in _trace(turn).get("operational_events", []) if e.get("stage") == stage]


def _routing(turn: dict[str, Any]) -> dict[str, Any]:
    (event,) = _events(turn, "routing")
    return event


def _authorized(turn: dict[str, Any], command: str) -> bool:
    return any(c["decision"] == "authorized" and c["command"] == command for c in _trace(turn).get("command_authority", []))


async def _alt_presented(conv: _RoutedConversation) -> dict[str, Any]:
    """Turn 1: applicability MATCH in the operator's own words -> governed `alt` presented."""
    t1 = await conv.turn(TURN1, [SEARCH, DOC.select(), CATALOG, _governed(ALT_ID, "Check active alarms on the node")], "Run `alt`.",
                         tae_args=TURN1_ARGS)
    assert _routing(t1)["route_required"] is False  # no active investigation yet: normal routing
    (step,) = t1["progression"].steps
    assert step.status is StepStatus.PRESENTED and step.command == "alt"
    return t1


def _assert_forced(turn: dict[str, Any], kind: OperationalTurnKind) -> None:
    routing_event = _routing(turn)
    assert routing_event["turn_kind"] == kind.value
    assert routing_event["active_operational_investigation"] is True
    assert routing_event["route_required"] is True and routing_event["selected_route"] == OperationalRoute.GOVERNED_OPERATIONAL.value
    (invocation,) = _events(turn, "specialist_invocation")
    assert invocation["specialist"] == "technical_authority_engineer" and invocation["forced_by_server"] is True
    # The server invoked the specialist without asking the Team Manager model; the model was only
    # called afterwards, with function calling disabled, to present the validated result.
    modes = [e["mode"] for e in _events(turn, "route_enforcement")]
    assert modes[0] == "server_invoked" and "NONE" in modes
    assert turn["tm_modes"] and all(m == "NONE" for m in turn["tm_modes"])
    assert turn["tae_calls"] >= 1 and turn["records"], "the governed specialist must run"


# =============================================================================================
# A-D. Generic continuations on an active investigation cannot bypass the governed pipeline
# =============================================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [LIVE_TURN4, "what next?", "continue", "go ahead"])
async def test_a_to_d_continuation_is_routed_through_the_governed_pipeline(conversation, text: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    # The Team Manager's own script would answer from history without delegating (live).
    t2 = await conv.turn(text, [DOC.select(), _insufficient()], HISTORY_ANSWER, delegate=False)
    _assert_forced(t2, OperationalTurnKind.GENERIC_CONTINUATION)
    trace = _trace(t2)
    # The pending step stays the current step: re-derived from THIS run's selection and re-authorized.
    (continuation,) = _events(t2, "continuation")
    assert continuation["rule"] == "generic_continuation" and continuation["pending_step"]["status"] == "presented"
    assert trace["searches"] and trace["selected"] == [{"knowledge_id": DOC.kid, "version_label": DOC.ver, "section_id": DOC.section}]
    assert trace["action_resolutions"][-1]["action_id"] == ALT_ID and _authorized(t2, "alt")
    assert "`alt`" in t2["final"] and t2["final"] != "This will allow us to proceed with the troubleshooting."
    (step,) = t2["progression"].steps  # nothing assumed executed, no duplicate step
    assert step.status is StepStatus.PRESENTED and step.result is None


# =============================================================================================
# E / J. Pending result required; historical command text is not current authority
# =============================================================================================


@pytest.mark.asyncio
async def test_e_j_pending_result_is_requested_and_history_never_authorizes(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    # The specialist selects nothing this run: the pending step's command has NO current authority.
    t2 = await conv.turn("what next?", [_insufficient(), _insufficient()], HISTORY_ANSWER, delegate=False)
    _assert_forced(t2, OperationalTurnKind.GENERIC_CONTINUATION)
    assert not _authorized(t2, "alt") and not _trace(t2).get("action_resolutions")
    # The Team Manager's replay of the historical command never reaches the operator ...
    assert "`alt`" not in t2["final"] and "Run `alt`" not in t2["final"]
    egress = _trace(t2).get("command_egress") or {}
    assert all(c.get("decision") != "kept" for c in egress.get("candidates", []))
    # ... the pending step's RESULT is requested from server state (nothing assumed executed).
    assert t2["final"].startswith("The current diagnostic step is still awaiting its result: Check active alarms on the node")
    (step,) = t2["progression"].steps
    assert step.status is StepStatus.PRESENTED and step.result is None
    completeness = t2["records"][-1][tae_agent_tool.RESPONSE_COMPLETENESS_KEY]
    assert completeness["forced_route"] is True and completeness["pending_step"]["step_id"] == step.step_id


# =============================================================================================
# F. Result provided / K. continuation after a result (no pending step)
# =============================================================================================


@pytest.mark.asyncio
async def test_f_k_result_is_bound_and_later_continuation_reenters_governed_progression(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    t2 = await conv.turn(ALT_RESULT, [_insufficient(["Next diagnostic decision"])], HISTORY_ANSWER, delegate=False)
    _assert_forced(t2, OperationalTurnKind.RESULT_PROVIDED)
    (step,) = t2["progression"].steps
    assert step.status in (StepStatus.OBSERVED, StepStatus.COMPLETED, StepStatus.VERIFIED) and step.result is not None
    assert [e["kind"] for e in _events(t2, "continuation")] == ["result_provided"]
    # No pending step now; the investigation is unfinished (validated result awaiting the next step).
    t3 = await conv.turn("what next?", [SEARCH, DOC.select(), CATALOG, _governed(PLUGIN_ID, "Check plug-in unit states")],
                         "Thanks for that.", delegate=False)
    _assert_forced(t3, OperationalTurnKind.GENERIC_CONTINUATION)
    assert _routing(t3)["pending_step"] is None
    assert _trace(t3)["action_resolutions"][-1]["action_id"] == PLUGIN_ID and _authorized(t3, "st pluginunit")
    assert "`st pluginunit`" in t3["final"] and t3["final"] != "Thanks for that."


# =============================================================================================
# G. Clarification answer keeps the applicability clarification path
# =============================================================================================


@pytest.mark.asyncio
async def test_g_clarification_answer_takes_the_applicability_clarification_path(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await conv.turn("how can i troubleshoot ESS Service Unavailable ?", [SEARCH, DOC.select(), _insufficient(["technology", "vendor"])],
                         "Which technology and vendor?", tae_args={"problem_statement": "ESS Service Unavailable"})
    assert _routing(t1)["route_required"] is False
    t2 = await conv.turn("4g, Ericsson", [DOC.select(), CATALOG, _governed(ALT_ID, "Check active alarms on the node")], "Okay.",
                         delegate=False)
    _assert_forced(t2, OperationalTurnKind.CLARIFICATION_ANSWER)
    (continuation,) = _events(t2, "continuation")
    assert continuation["kind"] == "clarification_answer" and continuation["rule"] == "applicability_clarification_resolved"
    assert _authorized(t2, "alt") and "`alt`" in t2["final"]


# =============================================================================================
# H. New objective / I. non-operational: normal routing, no forced specialist
# =============================================================================================


@pytest.mark.asyncio
async def test_h_new_objective_is_not_forced_into_the_old_fault(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await _alt_presented(conv)
    text = "now troubleshoot the fan unit overheating instead"
    t2 = await conv.turn(text, [_insufficient()], "Looking at the fan unit.",
                         tae_args={"problem_statement": text, "current_request": {"subject_component": "fan unit", "continues_active_objective": False}})
    routing_event = _routing(t2)
    assert routing_event["turn_kind"] == OperationalTurnKind.NEW_OBJECTIVE.value and routing_event["route_required"] is False
    assert "NONE" not in t2["tm_modes"] and not _events(t2, "route_enforcement")
    (invocation,) = _events(t2, "specialist_invocation")
    assert invocation["forced_by_server"] is False  # delegated by the Team Manager, not forced
    assert t2["progression"].active_fault_id != t1["progression"].active_fault_id


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["what can you do?", "hello", "thanks", "explain what an alarm means generally"])
async def test_i_non_operational_turn_keeps_normal_routing(conversation, text: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    t2 = await conv.turn(text, [_insufficient()], "I can help troubleshoot network faults.", delegate=False, requires_governed_knowledge=False)
    assert _routing(t2)["route_required"] is False and _routing(t2)["selected_route"] == OperationalRoute.TEAM_MANAGER.value
    assert t2["tae_calls"] == 0 and not _events(t2, "specialist_invocation")
    assert "NONE" not in t2["tm_modes"] and not _events(t2, "route_enforcement")
    assert t2["final"] == "I can help troubleshoot network faults."


# =============================================================================================
# M. Specialist failure on a forced turn fails closed (no Team Manager operational advice)
# =============================================================================================


@pytest.mark.asyncio
async def test_m_specialist_failure_fails_closed(conversation, monkeypatch) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("specialist unavailable")

    monkeypatch.setattr(tae_agent_tool, "_run_specialist_message", _boom)
    t2 = await conv.turn("what next?", [_insufficient()], HISTORY_ANSWER, delegate=False)
    assert _routing(t2)["route_required"] is True
    assert t2["final"] == OPERATIONAL_ROUTE_UNAVAILABLE_TEXT
    assert "`alt`" not in t2["final"]


# =============================================================================================
# 18. Mutation tests: the live-failure regression depends on each server routing protection
# =============================================================================================


def _never_required(original):
    def _decide(*args: Any, **kwargs: Any):
        return dataclasses.replace(original(*args, **kwargs), route_required=False, selected_route=OperationalRoute.TEAM_MANAGER)
    return _decide


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["forced_route_decision", "active_investigation_predicate", "generic_continuation_classification"])
async def test_mutation_removing_a_routing_protection_reproduces_the_live_bypass(conversation, monkeypatch, mutation: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    if mutation == "forced_route_decision":
        monkeypatch.setattr(routing, "decide_operational_route", _never_required(routing.decide_operational_route))
    elif mutation == "active_investigation_predicate":
        monkeypatch.setattr(routing, "unfinished_operational_work", lambda progression, fault_id: [])
    else:
        original = routing.classify_operational_turn
        monkeypatch.setattr(
            routing, "classify_operational_turn",
            lambda text, *a, **k: OperationalTurnKind.NON_OPERATIONAL if text == LIVE_TURN4 else original(text, *a, **k),
        )
    # As live: the Team Manager declares no governed requirement and answers from history.
    t2 = await conv.turn(LIVE_TURN4, [DOC.select(), _insufficient()], HISTORY_ANSWER, delegate=False, requires_governed_knowledge=False)
    # Without the protection the live failure returns: no specialist, Team Manager answers from history.
    assert t2["tae_calls"] == 0 and not t2["records"]
    assert _routing(t2)["route_required"] is False
    assert "NONE" not in t2["tm_modes"] and not _events(t2, "route_enforcement")
    assert "`alt`" not in t2["final"]  # egress still strips the historical command: a dead end
    assert t2["final"].startswith("This will allow us to proceed")


# =============================================================================================
# Unit level: classification, active-investigation predicate (K closed gaps / L closed fault)
# =============================================================================================


def _progression(phase: ProgressionPhase = ProgressionPhase.AWAITING_OBSERVATION, resolution: ResolutionState = ResolutionState.UNRESOLVED) -> TroubleshootingProgression:
    progression = TroubleshootingProgression()
    progression.add_fault(FaultProgression(fault_id="F-1", symptom_summary="symptom", phase=phase, resolution=resolution))
    return progression


def _with_pending(progression: TroubleshootingProgression) -> TroubleshootingStep:
    step = progression.append_step(TroubleshootingStep(fault_id="F-1", objective="Check active alarms", command="alt", expected_evidence="alarm list"))
    progression.set_step_status(step.step_id, StepStatus.PRESENTED)
    return step


@pytest.mark.parametrize(
    "text,kind",
    [
        (LIVE_TURN4, OperationalTurnKind.GENERIC_CONTINUATION),
        ("what next?", OperationalTurnKind.GENERIC_CONTINUATION),
        ("continue", OperationalTurnKind.GENERIC_CONTINUATION),
        ("go ahead", OperationalTurnKind.GENERIC_CONTINUATION),
        ("okay, next?", OperationalTurnKind.GENERIC_CONTINUATION),
        ("and now?", OperationalTurnKind.GENERIC_CONTINUATION),
        ("what should I do now?", OperationalTurnKind.GENERIC_CONTINUATION),
        ("what is the command to run for that?", OperationalTurnKind.COMMAND_FOLLOW_UP),
        ("which command do I use?", OperationalTurnKind.COMMAND_FOLLOW_UP),
        ("what is the command to restart a unit?", OperationalTurnKind.OPERATION_REQUEST),
        ("please restart the affected unit", OperationalTurnKind.OPERATION_REQUEST),
        ("restart it", OperationalTurnKind.OPERATION_REQUEST),
        ("restart the router in the other building", OperationalTurnKind.SUBJECT_REQUEST),
        ("give me an example of the restart syntax", OperationalTurnKind.SUBJECT_REQUEST),
        (ALT_RESULT, OperationalTurnKind.RESULT_PROVIDED),
        ("now troubleshoot the TimeSync alarm instead", OperationalTurnKind.NEW_OBJECTIVE),
        ("check another node", OperationalTurnKind.NEW_OBJECTIVE),
        ("help me with External Link Failure", OperationalTurnKind.SUBJECT_REQUEST),
        ("open incident INC123", OperationalTurnKind.SUBJECT_REQUEST),
        ("summarize this incident", OperationalTurnKind.SUBJECT_REQUEST),
        ("what can you do?", OperationalTurnKind.NON_OPERATIONAL),
        ("thanks", OperationalTurnKind.NON_OPERATIONAL),
        ("hello", OperationalTurnKind.SUBJECT_REQUEST),
    ],
)
def test_turn_classification(text: str, kind: OperationalTurnKind) -> None:
    progression = _progression()
    _with_pending(progression)
    assert classify_operational_turn(text, progression, "F-1") is kind


def test_routing_requires_an_active_investigation_from_server_state() -> None:
    # Pending presented step: active.
    progression = _progression()
    _with_pending(progression)
    decision = decide_operational_route("what next?", progression=progression, fault_id="F-1", specialist_available=True)
    assert decision.route_required and decision.active_operational_investigation and "pending_step:presented" in decision.unfinished_work
    # Specialist unavailable: never forced.
    assert not decide_operational_route("what next?", progression=progression, fault_id="F-1", specialist_available=False).route_required
    # No fault at all (history text alone never creates an investigation).
    assert not decide_operational_route("what next?", progression=TroubleshootingProgression(), fault_id=None, specialist_available=True).route_required
    # A fresh fault with nothing done: not an investigation to continue.
    assert unfinished_operational_work(_progression(ProgressionPhase.NEW), "F-1") == []


def test_k_open_non_terminal_gap_without_pending_step_is_an_active_investigation() -> None:
    progression = _progression(ProgressionPhase.RESULT_VALIDATED)
    requirement = progression.add_requirement(EvidenceRequirement(fault_id="F-1", kind=EvidenceKind.DIAGNOSTIC_RESULT, description="sync status"))
    progression.record_acquisition_gap(AcquisitionGap(
        fault_id="F-1", requirement_id=requirement.requirement_id, requirement_description="sync status",
        gap_reason=GapReason.NO_APPROVED_ACQUISITION_ACTION,
    ))
    work = unfinished_operational_work(progression, "F-1")
    assert {"open_requirement", "open_gap"} <= set(work)
    decision = decide_operational_route(LIVE_TURN4, progression=progression, fault_id="F-1", specialist_available=True)
    assert decision.route_required and decision.pending_step is None


@pytest.mark.parametrize("resolution", [ResolutionState.RESOLVED, ResolutionState.ESCALATED])
def test_l_closed_fault_does_not_force_the_specialist(resolution: ResolutionState) -> None:
    progression = _progression(ProgressionPhase.RESOLVED, resolution)
    _with_pending(progression)
    decision = decide_operational_route("what next?", progression=progression, fault_id="F-1", specialist_available=True)
    assert not decision.route_required and not decision.active_operational_investigation


def test_routing_trace_renders_the_decision() -> None:
    progression = _progression()
    _with_pending(progression)
    decision = decide_operational_route(LIVE_TURN4, progression=progression, fault_id="F-1", specialist_available=True)
    rendered = format_diagnostic_trace({
        "run_id": "r", "operational_events": [
            decision.trace_view(),
            {"stage": "route_enforcement", "mode": "ANY", "allowed": ["technical_authority_engineer"]},
            {"stage": "specialist_invocation", "specialist": "technical_authority_engineer", "forced_by_server": True, "reason": decision.reason},
        ],
    })
    assert "ROUTING active_fault=F-1 active_operational_investigation=True turn_kind=generic_continuation" in rendered
    assert "route_required=True selected_route=governed_operational" in rendered
    assert "SPECIALIST INVOCATION specialist=technical_authority_engineer forced_by_server=True" in rendered
    assert "ROUTE ENFORCEMENT function_calling=ANY" in rendered


class _FakeRequest:
    def __init__(self, contents: list[Any]) -> None:
        self.contents = contents
        self.tools_dict = {"technical_authority_engineer": object()}
        self.config = None


def test_enforcement_uses_the_server_side_invocation_fact_not_request_contents(monkeypatch) -> None:
    """Live: contents are re-projected for multi-turn sessions, hiding the specialist's response; the
    route must still release to NONE once the specialist ran -- never force a second invocation."""
    from backend.api.turn_context import bind_run_id, reset_run_id

    progression = _progression()
    _with_pending(progression)
    decision = decide_operational_route("what next?", progression=progression, fault_id="F-1", specialist_available=True)
    token = bind_run_id("run-x")
    try:
        routing.record_operational_route("run-x", decision)
        user_only = [types.Content(role="user", parts=[types.Part.from_text(text="what next?")])]
        request = _FakeRequest(user_only)
        response = routing.enforce_operational_route(None, request)
        assert response.content.parts[0].function_call.name == "technical_authority_engineer"  # model call skipped
        routing.record_specialist_invocation("technical_authority_engineer", "run-x")
        request = _FakeRequest(user_only)  # contents still show no specialist response
        routing.enforce_operational_route(None, request)
        assert request.config.tool_config.function_calling_config.mode.value == "NONE"
    finally:
        routing.discard_operational_route("run-x")
        reset_run_id(token)
    assert not routing.specialist_invoked("run-x") and routing.get_operational_route("run-x") is None


@pytest.mark.asyncio
async def test_duplicate_and_hallucinated_calls_after_the_specialist_ran_never_run_it_again(conversation) -> None:
    """Live (r4 sessions d9775463 / d814b9d7): a model under a forced mode emitted dozens of parallel
    specialist calls and a call to a non-existent `google:python_interpreter` (ADK ValueError ->
    run_failure). A model that ignores the post-specialist constraint and calls again is reduced:
    non-existent tools dropped, further specialist calls suppressed -- exactly one evaluation."""
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    repeat = [p for _ in range(3) for p in _fc("technical_authority_engineer", {"problem_statement": "what next?"})]
    repeat = _fc("google:python_interpreter", {}) + repeat
    # Bounded: without the protections this scenario errors inside the run (non-existent tool) and must
    # fail the test, never hang it.
    t2 = await asyncio.wait_for(conv.turn(
        "what next?", [DOC.select(), _insufficient()], "", delegate=False, ignore_tool_config=True,
        script_override=[repeat, [types.Part.from_text(text="Next: run `alt` and share the output.")]],
    ), timeout=30)
    (invocation,) = _events(t2, "specialist_invocation")
    assert invocation["forced_by_server"] is True and len(t2["records"]) == 1
    enforcement = _events(t2, "route_enforcement")
    assert [name for e in enforcement if e["mode"] == "response_restricted" for name in e["dropped"]] == ["google:python_interpreter"]
    assert len([e for e in enforcement if e["mode"] == "duplicate_call_suppressed"]) == 3
    assert len(_events(t2, "continuation")) == 1 and len(_events(t2, "progression")) == 1
    assert _authorized(t2, "alt") and "`alt`" in t2["final"]


@pytest.mark.asyncio
async def test_the_team_manager_model_is_not_consulted_before_the_specialist(conversation) -> None:
    """Even a model that would answer from history in text never gets the chance: the server invokes
    the specialist before any Team Manager model call (no model latency, no model routing choice)."""
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    t2 = await conv.turn(LIVE_TURN4, [DOC.select(), _insufficient()], "This will allow us to proceed with the troubleshooting.",
                         delegate=False, ignore_tool_config=True, script_override=[[types.Part.from_text(text="Next: run `alt` and share the output.")]])
    (invocation,) = _events(t2, "specialist_invocation")
    assert invocation["forced_by_server"] is True and len(t2["records"]) == 1
    assert len(t2["tm_modes"]) == 1  # the only Team Manager model call is the presentation after the specialist
    assert _authorized(t2, "alt") and "`alt`" in t2["final"]


def test_forced_turn_with_no_applicable_procedure_renders_the_governed_outcome() -> None:
    """Live (r4 session 9d4ae889, turn 4): explicit empty selection on a forced turn fell back to the generic
    "Governed knowledge could not be validated" text. The governed outcome is rendered from state."""
    from backend.agents.technical_authority_engineer.synthesis_boundary import (
        NO_GOVERNED_PROCEDURE_SELECTED_TEXT,
        enforce_response_completeness,
    )
    from backend.agents.team_manager.governed_knowledge_completion import SAFE_COMPLETION_FAILURE_TEXT

    record = {"outcome": "insufficient_evidence", "diagnostic_step": None, tae_agent_tool.RESPONSE_COMPLETENESS_KEY: {
        "forced_route": True, "required": True, "satisfied": True, "elements": ["no_applicable_governed_procedure"],
        "open_gaps": ["Node synchronization source and status"],
    }}
    text = enforce_response_completeness(SAFE_COMPLETION_FAILURE_TEXT, record)
    assert text.startswith(NO_GOVERNED_PROCEDURE_SELECTED_TEXT)
    assert "- Node synchronization source and status" in text and "escalate" in text
    # Not forced: unchanged.
    record[tae_agent_tool.RESPONSE_COMPLETENESS_KEY]["forced_route"] = False
    assert enforce_response_completeness(SAFE_COMPLETION_FAILURE_TEXT, record) == SAFE_COMPLETION_FAILURE_TEXT


def test_explicit_redirect_stating_clarification_values_is_a_new_objective() -> None:
    from backend.cases.troubleshooting_progression import ClarificationReason

    progression = _progression(ProgressionPhase.BLOCKED_MISSING_INFORMATION)
    progression.record_clarification("F-1", ClarificationReason.APPLICABILITY, ["technology", "vendor"], text="need")
    vocabulary = {"technology": {"TECH-X"}, "vendor": {"VENDORCO"}}
    redirect = "now troubleshoot the fan unit overheating on VENDORCO TECH-X instead"
    assert classify_operational_turn(redirect, progression, "F-1", vocabulary=vocabulary) is OperationalTurnKind.NEW_OBJECTIVE
    assert classify_operational_turn("VENDORCO, TECH-X", progression, "F-1", vocabulary=vocabulary) is OperationalTurnKind.CLARIFICATION_ANSWER
    # Answer values inside a message with its own subject: not a pure answer (normal routing).
    switch = "now troubleshoot the fan unit overheating on VENDORCO TECH-X"
    assert classify_operational_turn(switch, progression, "F-1", vocabulary=vocabulary) is OperationalTurnKind.NEW_OBJECTIVE


def test_pending_result_request_never_repeats_the_historical_command() -> None:
    """Live (Prompt 4 rerun, session c0fb14f4, turn 4): the server-rendered pending-result request quoted
    "the output of the 'hget near Rfportref' command" -- command text with no current-turn authority."""
    from backend.agents.technical_authority_engineer.agent_tool import _without_step_commands
    from backend.agents.technical_authority_engineer.synthesis_boundary import render_pending_result_request

    step = TroubleshootingStep(
        fault_id="F-1", objective="Check the RF port reference near the affected unit.", command="hget near Rfportref",
        expected_evidence="The output of the 'hget near Rfportref' command, specifically looking for 'AntennaUnitGroup'.",
    )
    pending = {"objective": _without_step_commands(step.objective, step), "expected_evidence": _without_step_commands(step.expected_evidence, step)}
    text = render_pending_result_request(pending)
    assert "hget" not in text and "Rfportref" not in text
    assert text.startswith("The current diagnostic step is still awaiting its result: Check the RF port reference near the affected unit.")
    assert "The output of the check" in text


def test_operation_request_on_a_trusted_target_is_routed_and_a_foreign_target_is_not() -> None:
    """Live (Prompt 4 rerun, session cb83654c, turn 7): "please restart the affected unit" was answered by the
    Team Manager without the governed pipeline (no target / condition rules). An operational action on the
    investigation's own subject -- its referents, or a target its TRUSTED results established -- is routed."""
    progression = _progression()
    step = _with_pending(progression)
    from backend.cases.target_facts import case_target_facts
    from backend.cases.troubleshooting_progression import ResultSource

    progression.record_step_result(step.step_id, "Equipment=1,FieldReplaceableUnit=UNIT-2 (Link Failure)", ResultSource.OPERATOR_MESSAGE)
    assert "FieldReplaceableUnit=UNIT-2" in {f.identity for f in case_target_facts(progression, "F-1")}
    for text in ("please restart the affected unit", "restart UNIT-2"):
        decision = decide_operational_route(text, progression=progression, fault_id="F-1", specialist_available=True)
        assert decision.turn_kind is OperationalTurnKind.OPERATION_REQUEST and decision.route_required, text
    foreign = decide_operational_route("restart UNIT-9 on the other site", progression=progression, fault_id="F-1", specialist_available=True)
    assert foreign.turn_kind is not OperationalTurnKind.OPERATION_REQUEST and not foreign.route_required
