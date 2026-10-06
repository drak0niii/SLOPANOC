"""Clarification continuity: an information request the server made survives across turns.

Live defect (semantic shape, generic values):
    how do I troubleshoot <fault>?            -> needs field A + field B (applicability UNKNOWN)
    what command do I run for that check?     -> cannot authorize yet: A/B unresolved
    what details should I provide?            -> WAS: fresh investigation, nothing selected,
                                                  governed_fail_closed ("could not be validated")
                                                 NOW: "I still need: A, B" from the server record

The pending clarification is the fault's OPEN OpenQuestion (requested_fields) on the Case-owned
TroubleshootingProgression -- one server-owned record. Command Authority, applicability, policy and
HITL are unchanged: nothing here authorizes a command.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

import pytest
import pytest_asyncio
from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from backend.agents.team_manager.governed_knowledge_completion import SAFE_COMPLETION_FAILURE_TEXT
from backend.agents.technical_authority_engineer import agent_tool as tae_agent_tool
from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
from backend.agents.technical_authority_engineer.applicability_context import (
    CONFIRMED_APPLICABILITY_FACTS_STATE_KEY,
    build_governed_vocabulary,
)
from backend.agents.technical_authority_engineer.clarification_continuity import (
    CLARIFICATION_CONTINUITY_KEY,
    NO_PENDING_CLARIFICATION_TEXT,
    bind_applicability_answer,
    is_clarification_follow_up,
    render_clarification_meta_response,
    render_clarification_request,
)
from backend.agents.technical_authority_engineer.progression_repository import ProgressionRepository
from backend.agents.technical_authority_engineer.schemas import (
    TechnicalAuthorityRequest,
    TechnicalAuthorityResponse,
)
from backend.agents.technical_authority_engineer.turn_request import build_turn_request_contract
from backend.api.chat_service import ChatService
from backend.api.knowledge_source_reference import build_knowledge_source_references
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.cases.db import CaseDatabase
from backend.cases.progression_store import CaseProgressionStore
from backend.cases.service import CaseService
from backend.cases.troubleshooting_progression import (
    PROGRESSION_STATE_KEY,
    ClarificationReason,
    ClarificationStatus,
    FaultProgression,
    OpenQuestion,
    TroubleshootingProgression,
    project_troubleshooting_state,
)
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tools.knowledge.diagnostic_trace import RETRIEVAL_DIAGNOSTICS_STATE_KEY
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence

# ---------------------------------------------------------------------------------------------
# Generic governed fixture: field A = technology, field B = vendor (values are synthetic)
# ---------------------------------------------------------------------------------------------
_KID = "CC-GOVERNED-PROC"
_SECTION = f"{_KID}:v1:section-0000"
_CANONICAL = f"{_KID}:v1:{_SECTION}"
_FILENAME = "CC Governed Alarm Procedure.docx"
_COMMAND = "show active-alarms"
_DIMS = {"technology": ["TECH-X", "TECH-Y"], "vendor": ["VENDORCO"]}
_CONTENT = (
    "Signal loss alarm handling.\n"
    "Health check commands:\n"
    f"{_COMMAND}\n"
    "Check the active alarm list before any operational action.\n"
)
_FOLLOW_UP_TEXT = "To confirm which governed procedure applies, I still need:\n- technology\n- vendor"


def _mop(dimensions: Optional[dict[str, list[str]]] = None) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID,
        document_type=KnowledgeDocumentType.MOP,
        title="CC Governed Alarm Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        applicability=Applicability(dimensions=_DIMS if dimensions is None else dimensions),
        source=KnowledgeSource(source_system="test", source_id=_FILENAME, display_name="CC MOP"),
        sections=[
            KnowledgeSection(
                section_id=_SECTION, knowledge_id=_KID, heading="Alarm handling", sequence=0,
                content=_CONTENT, source_locator="lines:1-4",
            )
        ],
    )


class _ScriptedLlm(BaseLlm):
    def __init__(self, model: str, parts_by_call: list[list[types.Part]], **kwargs: Any) -> None:
        super().__init__(model=model, **kwargs)
        self._parts_by_call = parts_by_call
        self._call_count = 0

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        if self._call_count >= len(self._parts_by_call):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="")]), partial=False)
            return
        parts = self._parts_by_call[self._call_count]
        self._call_count += 1
        yield LlmResponse(content=types.Content(role="model", parts=parts), partial=False)


def _fc(name: str, args: dict[str, Any]) -> list[types.Part]:
    return [types.Part(function_call=types.FunctionCall(name=name, args=args))]


_SEARCH = _fc("knowledge_search", {"query_text": "signal loss alarm"})
_SELECT = _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": _KID, "version_label": "v1", "section_id": _SECTION}]})


def _recommend(command: Optional[str] = _COMMAND, action: str = "Check active alarms on the node") -> list[types.Part]:
    payload = {
        "outcome": "recommended",
        "technical_interpretation": "A signal loss alarm is reported; the active alarm list is checked first.",
        "verified_evidence_citations": [],
        "diagnostic_step": {
            "action": action,
            "reason": "Establish the active alarm picture before any operational action.",
            "expected_evidence": "Active alarm list",
            "command": command,
            "command_source": _FILENAME if command else None,
            "restrictions": [],
        },
    }
    return [types.Part.from_text(text=json.dumps(payload))]


def _insufficient() -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "insufficient_evidence",
        "technical_interpretation": "No governed procedure could be validated for this request.",
        "missing_information": [],
    }))]


# A specialist script that MUST NOT run on a clarification follow-up / partial answer: it would
# start a fresh investigation and fabricate missing fields.
_FORBIDDEN_TAE = [_fc("knowledge_search", {"query_text": "what details should i provide"}), _insufficient()]


class _Conversation:
    """One real ChatService session (scripted Team Manager + scripted TAE) across turns."""

    def __init__(self, monkeypatch: Any) -> None:
        self.records: list[dict[str, Any]] = []
        original = tae_agent_tool.record_technical_authority_execution

        def _capture(run_id: str, record: dict[str, Any]) -> None:
            self.records.append(record)
            original(run_id, record)

        monkeypatch.setattr(tae_agent_tool, "record_technical_authority_execution", _capture)
        self.session_service = ApiSessionService(adk_session_service=InMemorySessionService())
        self.session_id: Optional[str] = None

    async def turn(
        self,
        user_text: str,
        tae_calls: list[list[types.Part]],
        tm_final_text: str,
        *,
        tae_args: Optional[dict[str, Any]] = None,
        delegate: bool = True,
        requires_governed_knowledge: bool = True,
    ) -> dict[str, Any]:
        if self.session_id is None:
            self.session_id = await self.session_service.create_session(user_id="test-engineer")
        before = len(self.records)
        tae_llm = _ScriptedLlm(model="scripted-tae", parts_by_call=tae_calls)
        specialist = Agent(
            name="technical_authority_engineer",
            model=tae_llm,
            tools=[knowledge_search, knowledge_select_evidence],
            input_schema=TechnicalAuthorityRequest,
            output_schema=TechnicalAuthorityResponse,
        )
        tae_tool = TechnicalAuthorityAgentTool(agent=specialist)

        from backend.agents.team_manager.agent import team_manager
        from backend.agents.team_manager.source_requirements import record_source_requirements
        from backend.api.session_service import APP_NAME

        script = [_fc("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": requires_governed_knowledge})]
        if delegate:
            script.append(_fc("technical_authority_engineer", tae_args or {"problem_statement": user_text}))
        script.append([types.Part.from_text(text=tm_final_text)])
        tm_llm = _ScriptedLlm(model="scripted-tm", parts_by_call=script)
        outer = team_manager.model_copy(update={"model": tm_llm, "tools": [record_source_requirements, tae_tool]})
        runner = Runner(app_name=APP_NAME, agent=outer, session_service=self.session_service.adk_session_service)
        chat = ChatService(session_service=self.session_service, runner=runner, case_service=CaseService())

        final: Optional[str] = None
        completed: dict[str, Any] = {}
        async for event in chat.execute_turn_events(session_id=self.session_id, message_text=user_text, user_id="test-engineer"):
            if event.type == StreamEventType.MESSAGE_COMPLETED:
                final = event.data.get("content")
                completed = dict(event.data)
        session = await self.session_service.get_session(self.session_id, "test-engineer")
        state = dict(session.state)
        progression = TroubleshootingProgression.model_validate(state[PROGRESSION_STATE_KEY]) if state.get(PROGRESSION_STATE_KEY) else None
        diagnostics = list((state.get(RETRIEVAL_DIAGNOSTICS_STATE_KEY) or {}).values())
        return {
            "final": final or "",
            "completed": completed,
            "tae_calls": tae_llm._call_count,
            "records": self.records[before:],
            "state": state,
            "progression": progression,
            "turn_requests": (diagnostics[-1].get("turn_requests") if diagnostics else []) or [],
        }


def _clarifications(progression: TroubleshootingProgression) -> list[OpenQuestion]:
    return [q for q in progression.open_questions if q.is_clarification]


def _signature(progression: TroubleshootingProgression) -> tuple[Any, ...]:
    """Operational + clarification state that a clarification follow-up must leave untouched."""
    return (
        [(s.step_id, s.status.value, s.command) for s in progression.steps],
        [(q.question_id, q.status.value, tuple(q.requested_fields), tuple(sorted(q.resolved_values.items()))) for q in progression.open_questions],
        {fid: (f.phase.value, f.current_step_id) for fid, f in progression.faults.items()},
        progression.active_fault_id,
    )


async def _establish_pending_clarification(conv: _Conversation) -> dict[str, Any]:
    """Turns 1 + 2 of the live sequence: A + B requested; command not authorized."""
    t1 = await conv.turn(
        "how can i troubleshoot the signal loss alarm?", [_SEARCH, _SELECT, _recommend()], "I need the technology and the vendor."
    )
    r1 = t1["records"][-1]
    assert r1["applicability_clarification"]["missing_dimensions"] == ["technology", "vendor"]
    assert r1["approved_commands_catalog"] == []
    assert not (r1.get("diagnostic_step") or {}).get("command")
    pending = _clarifications(t1["progression"])
    assert len(pending) == 1 and pending[0].status is ClarificationStatus.OPEN
    assert pending[0].reason is ClarificationReason.APPLICABILITY
    assert pending[0].requested_fields == ["technology", "vendor"]
    assert pending[0].fault_id == t1["progression"].active_fault_id

    t2 = await conv.turn(
        "what is the cmd i need to run to check the active alarms on the node?",
        [_SEARCH, _SELECT, _recommend()],
        "The command cannot yet be authorized; more information is needed.",
    )
    r2 = t2["records"][-1]
    assert r2["approved_commands_catalog"] == [] and not (r2.get("diagnostic_step") or {}).get("command")
    assert _COMMAND not in t2["final"]
    again = _clarifications(t2["progression"])
    assert [q.question_id for q in again] == [pending[0].question_id], "the same request is refreshed, never duplicated"
    assert again[0].unresolved_fields == ["technology", "vendor"]
    return t2


# =============================================================================================
# 11. REQUIRED LIVE-SEQUENCE REGRESSION
# =============================================================================================


@pytest.mark.asyncio
async def test_live_sequence_clarification_survives_follow_up_and_partial_answers(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    t2 = await _establish_pending_clarification(conv)
    before = _signature(t2["progression"])

    # Turn 3: "what details should i provide?" -> the exact requested fields, from server state.
    t3 = await conv.turn(
        "what details should i provide ?",
        _FORBIDDEN_TAE,
        "Please provide the node name, the software release and the alarm timestamp.",  # model prose: never used
    )
    assert t3["final"] == _FOLLOW_UP_TEXT
    assert t3["final"] != SAFE_COMPLETION_FAILURE_TEXT
    assert t3["tae_calls"] == 0, "no specialist call, no governed retrieval, no new troubleshooting decision"
    record = t3["records"][-1]
    assert record[CLARIFICATION_CONTINUITY_KEY]["kind"] == "clarification_follow_up"
    assert record.get("diagnostic_step") is None and record["approved_commands_catalog"] == [] and record["verified_evidence"] == []
    assert _signature(t3["progression"]) == before, "no new step, no transition, pending clarification unchanged"
    assert "knowledge_sources" not in t3["completed"], "a meta response is not derived from a governed source"
    request = t3["turn_requests"][-1]
    assert request["focus"] == "continue" and request["request_kind"] == "clarification_follow_up"
    assert request["troubleshooting_thread"]["decision"] == "continued"
    for invented in ("node name", "software release", "timestamp"):
        assert invented not in t3["final"]

    # Turn 4: value for field B only -> B resolved, only A asked for; nothing restarts.
    t4 = await conv.turn("VENDORCO", _FORBIDDEN_TAE, "Thanks.")
    assert t4["tae_calls"] == 0
    assert t4["final"] == "Recorded: vendor = VENDORCO.\nTo confirm which governed procedure applies, I still need:\n- technology"
    (question,) = _clarifications(t4["progression"])
    assert question.status is ClarificationStatus.OPEN
    assert question.resolved_values == {"vendor": ["VENDORCO"]} and question.unresolved_fields == ["technology"]
    assert t4["state"][CONFIRMED_APPLICABILITY_FACTS_STATE_KEY] == {"vendor": ["VENDORCO"]}
    assert [s.step_id for s in t4["progression"].steps] == [s[0] for s in before[0]], "no new diagnostic step"
    assert t4["turn_requests"][-1]["request_kind"] == "clarification_answer"
    assert t4["turn_requests"][-1]["focus"] == "continue"

    # Follow-up again: only the still-unresolved field.
    t4b = await conv.turn("what else do you need?", _FORBIDDEN_TAE, "Anything else.")
    assert t4b["tae_calls"] == 0
    assert t4b["final"] == "Already provided: vendor = VENDORCO.\nTo confirm which governed procedure applies, I still need:\n- technology"

    # Turn 5: value for field A -> clarification resolved, applicability recomputed (MATCH),
    # normal troubleshooting continues: the governed read command passes the UNCHANGED authority.
    t5 = await conv.turn(
        "TECH-X", [_SEARCH, _SELECT, _recommend()], f"Check active alarms on the node. Run {_COMMAND}."
    )
    assert t5["tae_calls"] == 3, "normal troubleshooting continues"
    (question,) = _clarifications(t5["progression"])
    assert question.status is ClarificationStatus.RESOLVED and question.answered
    assert question.resolved_values == {"vendor": ["VENDORCO"], "technology": ["TECH-X"]}
    r5 = t5["records"][-1]
    ev = [e for e in r5["verified_evidence"] if e["source_id"] == _CANONICAL][0]
    assert ev["metadata"]["applicability_outcome"] == "match"
    assert r5["diagnostic_step"]["command"] == _COMMAND
    assert "applicability_clarification" not in r5 and CLARIFICATION_CONTINUITY_KEY not in r5
    assert _COMMAND in t5["final"]
    assert t5["state"][CONFIRMED_APPLICABILITY_FACTS_STATE_KEY] == {"vendor": ["VENDORCO"], "technology": ["TECH-X"]}
    assert t5["completed"]["knowledge_sources"][0]["applicability_outcome"] == "match"


@pytest.mark.asyncio
async def test_both_fields_in_one_answer_resolve_together(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _establish_pending_clarification(conv)
    t3 = await conv.turn("VENDORCO TECH-Y", [_SEARCH, _SELECT, _recommend()], f"Run {_COMMAND}.")
    (question,) = _clarifications(t3["progression"])
    assert question.status is ClarificationStatus.RESOLVED
    assert question.resolved_values == {"technology": ["TECH-Y"], "vendor": ["VENDORCO"]}
    assert t3["records"][-1]["diagnostic_step"]["command"] == _COMMAND


@pytest.mark.asyncio
async def test_follow_up_answered_from_server_state_when_specialist_not_consulted(isolated_km_repo, monkeypatch) -> None:
    """Team Manager answers without delegating: the outstanding request still comes from the
    authoritative progression, never from the model's own list."""
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    t2 = await _establish_pending_clarification(conv)
    t3 = await conv.turn("what information do you need?", [], "Please share the node name.", delegate=False)
    assert t3["final"] == _FOLLOW_UP_TEXT
    assert _signature(t3["progression"]) == _signature(t2["progression"])
    # Same with the governed-knowledge declaration: no forced governed completion, no fail-closed.
    t4 = await conv.turn("what is missing?", [], "Please share the node name.", delegate=False, requires_governed_knowledge=True)
    assert t4["final"] == _FOLLOW_UP_TEXT


# =============================================================================================
# 12. REQUIRED NEGATIVE TESTS
# =============================================================================================


@pytest.mark.asyncio
async def test_no_pending_clarification_invents_no_fields(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    t1 = await conv.turn("what details should i provide?", _FORBIDDEN_TAE, "Provide the node name and vendor.")
    assert t1["tae_calls"] == 0
    assert t1["final"] == NO_PENDING_CLARIFICATION_TEXT
    assert t1["final"] != SAFE_COMPLETION_FAILURE_TEXT
    record = t1["records"][-1]
    assert record["missing_information"] == [] and record[CLARIFICATION_CONTINUITY_KEY]["clarification"] is None
    assert t1["progression"] is None or not t1["progression"].faults, "no fault thread is opened by a meta question"


@pytest.mark.asyncio
async def test_different_fault_never_consumes_the_clarification(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    t2 = await _establish_pending_clarification(conv)
    (question_a,) = _clarifications(t2["progression"])
    fault_a = question_a.fault_id

    # Explicit switch to fault B, stating values that would answer fault A's request.
    text_b = "now troubleshoot the fan unit overheating on VENDORCO TECH-X instead"
    t3 = await conv.turn(
        text_b,
        [_insufficient()],
        "Looking at the fan unit.",
        tae_args={
            "problem_statement": text_b,
            "current_request": {"subject_component": "fan unit", "continues_active_objective": False},
        },
    )
    fault_b = t3["progression"].active_fault_id
    assert fault_b != fault_a
    assert t3["turn_requests"][-1]["troubleshooting_thread"]["decision"] == "created"
    (question_a_after,) = [q for q in _clarifications(t3["progression"]) if q.fault_id == fault_a]
    assert question_a_after.status is ClarificationStatus.OPEN
    assert question_a_after.resolved_values == {} and question_a_after.unresolved_fields == ["technology", "vendor"]
    assert t3["progression"].pending_clarification(fault_b) is None

    # A follow-up on fault B never renders fault A's request.
    t4 = await conv.turn("what details should i provide?", _FORBIDDEN_TAE, "Vendor and technology.")
    assert t4["tae_calls"] == 0
    assert t4["final"] == NO_PENDING_CLARIFICATION_TEXT
    assert "technology" not in t4["final"] and "vendor" not in t4["final"]
    assert _clarifications(t4["progression"])[0].resolved_values == {}


@pytest.mark.asyncio
async def test_random_answer_resolves_nothing(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _establish_pending_clarification(conv)
    t3 = await conv.turn("yes", [_insufficient()], "Okay.")
    (question,) = _clarifications(t3["progression"])
    assert question.status is ClarificationStatus.OPEN
    assert question.resolved_values == {} and question.unresolved_fields == ["technology", "vendor"]
    assert t3["turn_requests"][-1]["request_kind"] == "operational"
    assert CONFIRMED_APPLICABILITY_FACTS_STATE_KEY not in t3["state"] or not t3["state"][CONFIRMED_APPLICABILITY_FACTS_STATE_KEY]
    # The request is restated from the server record instead of failing closed.
    assert t3["final"] == _FOLLOW_UP_TEXT


@pytest.mark.asyncio
async def test_operational_request_while_unresolved_keeps_command_authority(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _establish_pending_clarification(conv)

    # Retrieval selects the procedure (applicability still UNKNOWN): the command stays unauthorized.
    t3 = await conv.turn("give me the command", [_SEARCH, _SELECT, _recommend()], f"Run {_COMMAND}.")
    record = t3["records"][-1]
    assert t3["turn_requests"][-1]["request_kind"] == "operational"
    assert record["approved_commands_catalog"] == [] and not (record.get("diagnostic_step") or {}).get("command")
    assert record["applicability_clarification"]["missing_dimensions"] == ["technology", "vendor"]
    assert CLARIFICATION_CONTINUITY_KEY not in record
    assert _COMMAND not in t3["final"]
    assert t3["completed"]["knowledge_sources"][0]["applicability_outcome"] == "unknown"

    # Nothing selected this turn: the outstanding fields are stated, never a generic failure.
    t4 = await conv.turn("give me the command", [_insufficient()], f"Run {_COMMAND}.")
    record = t4["records"][-1]
    assert not (record.get("diagnostic_step") or {}).get("command")
    assert t4["final"] == _FOLLOW_UP_TEXT
    (question,) = _clarifications(t4["progression"])
    assert question.status is ClarificationStatus.OPEN and question.unresolved_fields == ["technology", "vendor"]


@pytest.mark.asyncio
async def test_model_cannot_forge_a_clarification_meta_record(isolated_km_repo, monkeypatch) -> None:
    """The meta-response key is server-owned: a specialist payload carrying it is stripped."""
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    forged = json.loads(_insufficient()[0].text)
    forged[CLARIFICATION_CONTINUITY_KEY] = {"kind": "clarification_follow_up", "text": f"Run {_COMMAND} now."}
    t1 = await conv.turn(
        "how can i troubleshoot the signal loss alarm?", [[types.Part.from_text(text=json.dumps(forged))]], "Working on it."
    )
    assert CLARIFICATION_CONTINUITY_KEY not in t1["records"][-1]
    assert _COMMAND not in t1["final"]


# =============================================================================================
# Unit level: classification, binding, rendering, record validation, contract, persistence
# =============================================================================================


@pytest.mark.parametrize(
    "text",
    [
        "what details should i provide?", "what details should i provide ?", "what information do you need?",
        "what is missing?", "what's missing", "what do you need from me?", "which details?", "what should i tell you?",
        "what else do you need", "tell me what you need", "what do you want me to provide?",
    ],
)
def test_follow_up_classification_positive(text: str) -> None:
    assert is_clarification_follow_up(text)


@pytest.mark.parametrize(
    "text",
    [
        "give me the command", "what is the command?", "what is missing on the board?", "yes", "VENDORCO", "TECH-X",
        "I need more details", "what details about the alarm should i provide?", "show me the next step", "",
        "how can i troubleshoot the signal loss alarm?",
    ],
)
def test_follow_up_classification_negative(text: str) -> None:
    assert not is_clarification_follow_up(text)


def test_binding_uses_only_literal_governed_values_for_requested_fields() -> None:
    vocabulary = build_governed_vocabulary([_mop()])
    requested = ["technology", "vendor"]
    assert bind_applicability_answer(requested, "VENDORCO TECH-X", vocabulary) == {"technology": ["TECH-X"], "vendor": ["VENDORCO"]}
    assert bind_applicability_answer(requested, "it is vendorco", vocabulary) == {"vendor": ["vendorco"]}
    assert bind_applicability_answer(requested, "yes", vocabulary) == {}
    assert bind_applicability_answer(requested, "OTHERVENDOR", vocabulary) == {}, "unknown value is never asserted"
    assert bind_applicability_answer(["technology"], "VENDORCO", vocabulary) == {}, "only requested fields bind"
    assert bind_applicability_answer(requested, "VENDORCO", None) == {}, "no governed vocabulary -> fail closed"
    assert bind_applicability_answer(requested, "TECH-X and TECH-Y", vocabulary) == {"technology": ["TECH-X", "TECH-Y"]}
    # A value governed metadata knows for SEVERAL requested dimensions is ambiguous.
    ambiguous = build_governed_vocabulary([_mop({"technology": ["SHARED"], "vendor": ["SHARED"]})])
    assert bind_applicability_answer(requested, "SHARED", ambiguous) == {}
    # A value inside a longer stated value is not bound separately.
    overlapping = build_governed_vocabulary([_mop({"technology": ["NR", "NR SA"]})])
    assert bind_applicability_answer(["technology"], "it is NR SA", overlapping) == {"technology": ["NR SA"]}


def _progression_with_fault(*fault_ids: str) -> TroubleshootingProgression:
    progression = TroubleshootingProgression()
    for fid in fault_ids:
        progression.add_fault(FaultProgression(fault_id=fid, symptom_summary=f"fault {fid}"))
    return progression


def test_progression_clarification_lifecycle_and_fault_isolation() -> None:
    progression = _progression_with_fault("F-A", "F-B")
    question = progression.record_clarification("F-A", ClarificationReason.APPLICABILITY, ["technology", "vendor"], text="t")
    assert progression.pending_clarification("F-A") is question
    assert progression.pending_clarification("F-B") is None
    # Refresh from a later evaluation: same record, no duplicate.
    assert progression.record_clarification("F-A", ClarificationReason.APPLICABILITY, ["technology", "vendor"], text="t2") is question
    assert len(progression.open_questions) == 1
    # Fields not requested are ignored; partial answer keeps it open.
    progression.resolve_clarification_fields(question.question_id, {"vendor": ["V1"], "colour": ["blue"]})
    assert question.resolved_values == {"vendor": ["V1"]} and question.status is ClarificationStatus.OPEN
    progression.resolve_clarification_fields(question.question_id, {"technology": ["T1"]})
    assert question.status is ClarificationStatus.RESOLVED and question.answered and question.resolved_at is not None
    assert progression.pending_clarification("F-A") is None
    events = [e.event for e in progression.events]
    assert {"clarification_requested", "clarification_answered", "clarification_resolved"} <= set(events)


def test_later_evaluation_drops_or_resolves_fields_it_no_longer_needs() -> None:
    progression = _progression_with_fault("F-A")
    question = progression.record_clarification("F-A", ClarificationReason.APPLICABILITY, ["customer", "technology", "vendor"], text="t")
    progression.record_clarification(
        "F-A", ClarificationReason.APPLICABILITY, ["vendor"], text="t2", known_values={"customer": ["C1"]}
    )
    assert question.resolved_values == {"customer": ["C1"]}
    assert question.requested_fields == ["customer", "vendor"], "technology no longer required and unknown -> dropped"
    assert question.unresolved_fields == ["vendor"]


def test_legacy_open_question_payload_loads_and_is_not_a_clarification() -> None:
    legacy = {"question_id": "q-1", "fault_id": "F-A", "text": "No explicit verification criteria", "answered": False}
    question = OpenQuestion.model_validate(legacy)
    assert not question.is_clarification and question.status is ClarificationStatus.OPEN
    progression = _progression_with_fault("F-A")
    progression.open_questions.append(question)
    assert progression.pending_clarification("F-A") is None


def test_projection_exposes_pending_clarification_read_only() -> None:
    progression = _progression_with_fault("F-A")
    progression.record_clarification("F-A", ClarificationReason.APPLICABILITY, ["technology", "vendor"], text="t")
    projected = project_troubleshooting_state(progression, "F-A")
    assert projected.pending_clarification["unresolved_fields"] == ["technology", "vendor"]
    assert projected.pending_clarification["reason"] == "applicability"
    assert TroubleshootingState.model_validate(projected.model_dump(mode="json")).pending_clarification == projected.pending_clarification


def test_render_lists_only_unresolved_fields_from_the_record() -> None:
    progression = _progression_with_fault("F-A")
    question = progression.record_clarification("F-A", ClarificationReason.MISSING_PARAMETER, ["unit_id"], text="t")
    assert render_clarification_request(question) == "To complete the governed command, I still need:\n- unit_id"


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": "recommended"},
        {"diagnostic_step": {"action": "x", "command": _COMMAND}},
        {"approved_commands_catalog": [{"command": _COMMAND, "source_id": _CANONICAL}]},
        {"verified_evidence": [{"source_type": "governed_knowledge", "source_id": _CANONICAL}]},
        {CLARIFICATION_CONTINUITY_KEY: {"kind": "clarification_follow_up", "text": "  "}},
        {CLARIFICATION_CONTINUITY_KEY: None},
    ],
)
def test_meta_record_with_operational_content_is_rejected(overrides: dict[str, Any]) -> None:
    record = {
        "outcome": "insufficient_evidence",
        "diagnostic_step": None,
        "approved_commands_catalog": [],
        "verified_evidence": [],
        CLARIFICATION_CONTINUITY_KEY: {"kind": "clarification_follow_up", "text": _FOLLOW_UP_TEXT},
    }
    assert render_clarification_meta_response(record) == _FOLLOW_UP_TEXT
    record.update(overrides)
    assert render_clarification_meta_response(record) is None


def test_contract_follow_up_is_not_a_focus_switch() -> None:
    ts = TroubleshootingState(fault_id="F-A", symptom_summary="signal loss alarm on the node")
    proposed = {"requested_operation": "missing information", "subject_component": "details"}
    plain = build_turn_request_contract(proposed, "what details should i provide ?", ts)
    assert plain.focus == "switch" and plain.request_kind == "operational"  # the defect's classification
    contract = build_turn_request_contract(proposed, "what details should i provide ?", ts, request_kind="clarification_follow_up")
    assert contract.focus == "continue" and contract.request_kind == "clarification_follow_up"
    assert contract.subject_component is None and not contract.explicit_in_current_message
    assert contract.diagnostic_objective.startswith("Continue the active investigation")
    answer = build_turn_request_contract(
        {"subject_component": "VENDORCO"}, "VENDORCO", ts, request_kind="clarification_answer", answer_values=["VENDORCO"]
    )
    assert answer.focus == "continue" and answer.subject_component is None
    assert any(d["reason"].startswith("clarification answer value") for d in answer.discarded_fields)


@pytest_asyncio.fixture
async def cases():
    database = CaseDatabase(database_url="sqlite+aiosqlite:///:memory:")
    yield CaseService(database), CaseProgressionStore(database)
    await database.close()


@pytest.mark.asyncio
async def test_clarification_persists_with_the_case_across_sessions(cases) -> None:
    service, store = cases
    case_id = (await service.create_case("eng", "Signal loss", "signal loss alarm")).case_id
    state_a: dict[str, Any] = {"active_case_id": case_id}
    await service.link_session("eng", case_id, "session-a", "eng")
    repo_a = ProgressionRepository(state_a, session_id="session-a", store=store)
    progression = await repo_a.load()
    progression.add_fault(FaultProgression(fault_id="F-A", symptom_summary="signal loss alarm"))
    progression.record_clarification("F-A", ClarificationReason.APPLICABILITY, ["technology", "vendor"], text="t")
    await repo_a.save(progression, "F-A")

    state_b: dict[str, Any] = {"active_case_id": case_id}
    await service.link_session("eng", case_id, "session-b", "eng")
    loaded = await ProgressionRepository(state_b, session_id="session-b", store=store).load()
    question = loaded.pending_clarification("F-A")
    assert question is not None and question.unresolved_fields == ["technology", "vendor"]
    assert state_a["troubleshooting_state"]["pending_clarification"]["unresolved_fields"] == ["technology", "vendor"]


def test_knowledge_source_reference_carries_applicability_outcome() -> None:
    from backend.tests.test_p5_1j_knowledge_source_reference import _item

    item = _item()
    identity = (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)
    assert build_knowledge_source_references([item], {identity: "unknown"})[0].applicability_outcome == "unknown"
    assert build_knowledge_source_references([item], {identity: "match"})[0].applicability_outcome == "match"
    assert build_knowledge_source_references([item])[0].applicability_outcome is None, "no evaluation recorded"


def test_parameter_clarification_is_recorded_and_resolved_by_an_authorized_command() -> None:
    from backend.agents.technical_authority_engineer.agent_tool import _record_fault_clarifications

    progression = _progression_with_fault("F-A")
    asked = {"outcome": "recommended", "diagnostic_step": {"action": "Check unit", "command": None},
             "parameter_clarification": {"missing_parameters": ["unit_id"], "ambiguous_parameters": [], "conflicting_parameters": [], "text": "t"}}
    _record_fault_clarifications(progression, "F-A", asked, [], {}, "step-1")
    question = progression.pending_clarification("F-A", ClarificationReason.MISSING_PARAMETER)
    assert question is not None and question.unresolved_fields == ["unit_id"] and question.originating_step_id == "step-1"

    # A run that evaluated nothing leaves it untouched.
    _record_fault_clarifications(progression, "F-A", {"outcome": "insufficient_evidence"}, [], {}, "step-1")
    assert question.status is ClarificationStatus.OPEN

    authorized = {"outcome": "recommended", "diagnostic_step": {"action": "Check unit", "command": "show unit U-7"},
                  "procedure_action_resolution": {"parameters": [{"name": "unit_id", "state": "bound", "value": "U-7"}]}}
    _record_fault_clarifications(progression, "F-A", authorized, [], {}, "step-1")
    assert question.status is ClarificationStatus.RESOLVED and question.resolved_values == {"unit_id": ["U-7"]}


def test_applicability_clarification_closes_only_on_an_evaluated_match() -> None:
    from backend.agents.technical_authority_engineer.agent_tool import _record_fault_clarifications

    progression = _progression_with_fault("F-A")
    unknown = {"source_type": "governed_knowledge", "source_id": _CANONICAL, "metadata": {"applicability_outcome": "unknown"}}
    match = {"source_type": "governed_knowledge", "source_id": _CANONICAL, "metadata": {"applicability_outcome": "match"}}
    asked = {"outcome": "insufficient_evidence", "applicability_clarification": {"missing_dimensions": ["technology", "vendor"], "text": "t"}}
    _record_fault_clarifications(progression, "F-A", asked, [unknown], {}, None)
    question = progression.pending_clarification("F-A")
    assert question.unresolved_fields == ["technology", "vendor"]
    # No evaluation this run (nothing retrieved): unchanged -- the live defect's turn.
    _record_fault_clarifications(progression, "F-A", {"outcome": "insufficient_evidence"}, [], {}, None)
    assert question.status is ClarificationStatus.OPEN and question.unresolved_fields == ["technology", "vendor"]
    # Evaluated MATCH: resolved from confirmed context.
    _record_fault_clarifications(progression, "F-A", {"outcome": "recommended"}, [match], {"technology": ["T1"], "vendor": ["V1"]}, None)
    assert question.status is ClarificationStatus.RESOLVED
    assert question.resolved_values == {"technology": ["T1"], "vendor": ["V1"]}


def test_pending_clarification_is_not_restated_when_the_run_found_applicable_evidence() -> None:
    from backend.agents.technical_authority_engineer.agent_tool import _attach_pending_clarification, _record_fault_clarifications

    progression = _progression_with_fault("F-A")
    progression.record_clarification("F-A", ClarificationReason.APPLICABILITY, ["technology", "vendor"], text="t")
    no_command = {"outcome": "insufficient_evidence", "diagnostic_step": None}
    restated = _attach_pending_clarification(no_command, progression, "F-A", {})
    assert restated["applicability_clarification"]["missing_dimensions"] == ["technology", "vendor"]

    match = {"source_type": "governed_knowledge", "source_id": _CANONICAL, "metadata": {"applicability_outcome": "match"}}
    result = _attach_pending_clarification(no_command, progression, "F-A", {}, verified_evidence=[match])
    assert "applicability_clarification" not in result
    _record_fault_clarifications(progression, "F-A", result, [match], {"technology": ["T1"], "vendor": ["V1"]}, None)
    assert progression.pending_clarification("F-A") is None
