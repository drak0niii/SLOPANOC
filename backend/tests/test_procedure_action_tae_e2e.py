"""Tranche 2 end-to-end: real ChatService + real TechnicalAuthorityAgentTool with scripted LLMs.

knowledge_search -> knowledge_select_evidence -> procedure_action_catalog -> TAE chooses
procedure_action_id -> server resolves template + trusted parameters -> EXISTING Command
Authority -> final response / troubleshooting history / persisted diagnostic trace.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from backend.agents.technical_authority_engineer import agent_tool as tae_agent_tool
from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityRequest, TechnicalAuthorityResponse
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.cases.service import CaseService
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_ess_service_unavailable_e2e_verification import (  # noqa: F401 (fixture)
    _fc,
    _ScriptedCallLlm,
    isolated_km_repo,
)
from backend.tools.knowledge.diagnostic_trace import RETRIEVAL_DIAGNOSTICS_STATE_KEY, format_diagnostic_trace
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence

_KID = "KID-NODE-DIAG"
_SEC = "sec-0001"
_CANONICAL = f"{_KID}:v1:{_SEC}"
_CONTENT = (
    "Check the node alarm state: `show foo <target>`\n"
    "Do not run `restart foo` during business hours.\n"
)
_ACTION_ID = next(
    a.action_id
    for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label="v1", section_id=_SEC, content=_CONTENT)[0]
    if a.command_template == "show foo <target>"
)
_USER = "Node NODE-1 raised a node alarm. What should I check next?"

_SEARCH = _fc("knowledge_search", {"query_text": "node alarm state"})
_SELECT = _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": _KID, "version_label": "v1", "section_id": _SEC}]})
_CATALOG = _fc("procedure_action_catalog", {})


def _mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID,
        document_type=KnowledgeDocumentType.MOP,
        title="Node Diagnostics Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id="MOP_Node.docx", display_name="Node MOP"),
        sections=[
            KnowledgeSection(
                section_id=_SEC, knowledge_id=_KID, heading="Diagnostics", sequence=0, content=_CONTENT, source_locator="lines:1-2"
            )
        ],
    )


def _recommend(
    action_id: Optional[str] = _ACTION_ID,
    params: Optional[list[dict[str, str]]] = None,
    command: Optional[str] = None,
    command_source: Optional[str] = None,
) -> list[types.Part]:
    payload = {
        "outcome": "recommended",
        "technical_interpretation": "A node alarm is active; its state must be inspected first.",
        "verified_evidence_citations": [],
        "diagnostic_step": {
            "action": "Check the node alarm state",
            "reason": "Establish the current alarm state before any further action.",
            "expected_evidence": "Alarm state output",
            "command": command,
            "command_source": command_source,
            "restrictions": [],
            "diagnostic_intent": "confirm the node alarm state",
            "procedure_action_id": action_id,
            "parameter_values": params if params is not None else [{"name": "target", "value": "NODE-1"}],
        },
    }
    return [types.Part.from_text(text=json.dumps(payload))]


class _Session:
    def __init__(self, monkeypatch: Any) -> None:
        self.records: list[dict[str, Any]] = []
        original = tae_agent_tool.record_technical_authority_execution

        def _capture(run_id: str, record: dict[str, Any]) -> None:
            self.records.append(record)
            original(run_id, record)

        monkeypatch.setattr(tae_agent_tool, "record_technical_authority_execution", _capture)
        self.session_service = ApiSessionService(adk_session_service=InMemorySessionService())
        self.session_id: Optional[str] = None

    async def turn(self, tae_calls: list[list[types.Part]], tm_final_text: str, user_text: str = _USER, problem: Optional[str] = None) -> dict[str, Any]:
        if self.session_id is None:
            self.session_id = await self.session_service.create_session(user_id="eng")
        before = len(self.records)
        tae_llm = _ScriptedCallLlm(model="scripted-tae", parts_by_call=tae_calls)
        specialist = Agent(
            name="technical_authority_engineer",
            model=tae_llm,
            tools=[knowledge_search, knowledge_select_evidence, pa.procedure_action_catalog],
            input_schema=TechnicalAuthorityRequest,
            output_schema=TechnicalAuthorityResponse,
        )
        from backend.agents.team_manager.agent import team_manager
        from backend.agents.team_manager.source_requirements import record_source_requirements
        from backend.api.session_service import APP_NAME

        tm_llm = _ScriptedCallLlm(
            model="scripted-tm",
            parts_by_call=[
                _fc("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
                _fc("technical_authority_engineer", {"problem_statement": problem or user_text}),
                [types.Part.from_text(text=tm_final_text)],
            ],
        )
        outer = team_manager.model_copy(update={"model": tm_llm, "tools": [record_source_requirements, TechnicalAuthorityAgentTool(agent=specialist)]})
        runner = Runner(app_name=APP_NAME, agent=outer, session_service=self.session_service.adk_session_service)
        chat = ChatService(session_service=self.session_service, runner=runner, case_service=CaseService())
        final: Optional[str] = None
        async for event in chat.execute_turn_events(session_id=self.session_id, message_text=user_text, user_id="eng"):
            if event.type == StreamEventType.MESSAGE_COMPLETED:
                final = event.data.get("content")
        session = await self.session_service.get_session(self.session_id, "eng")
        return {
            "final": final or "",
            "records": self.records[before:],
            "state": dict(session.state),
            "tae_llm": tae_llm,
        }


def _last_turn_trace(state: dict[str, Any]) -> dict[str, Any]:
    persisted = state.get(RETRIEVAL_DIAGNOSTICS_STATE_KEY) or {}
    return list(persisted.values())[-1]


def _history(state: dict[str, Any]) -> list[dict[str, Any]]:
    return (state.get("troubleshooting_state") or {}).get("diagnostic_history", [])


# --- M: full TAE path ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_m_selected_action_resolves_and_existing_authority_authorizes(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    result = await _Session(monkeypatch).turn(
        [_SEARCH, _SELECT, _CATALOG, _recommend()],
        "Check the node alarm state. Run show foo NODE-1 and report the alarm state output.",
    )
    record = result["records"][-1]
    step = record["diagnostic_step"]
    assert step["command"] == "show foo NODE-1"
    assert step["command_source"] == _CANONICAL
    assert step["procedure_action_id"] == _ACTION_ID
    assert [(c["command"], c["source_id"]) for c in record["approved_commands_catalog"]] == [("show foo NODE-1", _CANONICAL)]
    resolution = record[pa.PROCEDURE_ACTION_RESOLUTION_KEY]
    assert resolution["status"] == "resolved" and resolution["command_authority"] == "authorized"
    assert "show foo NODE-1" in result["final"]
    sources = [
        ks["knowledge_id"]
        for ref in (result["state"].get("turn_source_references") or {}).values()
        for ks in ref.get("knowledge_sources", [])
    ]
    assert sources == [_KID]
    assert result["state"][pa.CONFIRMED_PROCEDURE_PARAMETERS_STATE_KEY] == {"target": "NODE-1"}


# --- G: fabricated action id ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g_fabricated_action_id_fails_closed_for_command_resolution(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    result = await _Session(monkeypatch).turn(
        [_SEARCH, _SELECT, _CATALOG, _recommend(action_id="pa-00000000000000000000")],
        "Check the node alarm state. Run show foo NODE-1.",
    )
    record = result["records"][-1]
    assert record["diagnostic_step"]["command"] is None
    assert record["diagnostic_step"]["procedure_action_id"] is None
    assert record["approved_commands_catalog"] == []
    assert record[pa.PROCEDURE_ACTION_RESOLUTION_KEY]["status"] == "unknown_action"
    assert "show foo NODE-1" not in result["final"]
    assert any(pa.NO_APPROVED_COMMAND_FOR_ACTION_TEXT in r for r in record["diagnostic_step"]["restrictions"])
    assert _history(result["state"])[-1]["procedure_action_id"] is None


@pytest.mark.asyncio
async def test_action_id_without_catalog_call_is_unknown(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    result = await _Session(monkeypatch).turn([_SEARCH, _SELECT, _recommend()], "Run show foo NODE-1.")
    assert result["records"][-1][pa.PROCEDURE_ACTION_RESOLUTION_KEY]["status"] == "unknown_action"
    assert "show foo NODE-1" not in result["final"]


# --- N: model command cannot override the server-rendered command -------------------------------
@pytest.mark.asyncio
async def test_n_free_form_model_command_is_ignored_when_action_selected(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    result = await _Session(monkeypatch).turn(
        [_SEARCH, _SELECT, _CATALOG, _recommend(command="restart foo", command_source=_CANONICAL)],
        "Run show foo NODE-1. Then run restart foo.",
    )
    record = result["records"][-1]
    assert record["diagnostic_step"]["command"] == "show foo NODE-1"
    assert record[pa.PROCEDURE_ACTION_RESOLUTION_KEY]["model_command_ignored"] == "restart foo"
    assert "restart foo" not in result["final"]
    assert "show foo NODE-1" in result["final"]


# --- I (e2e): missing parameter -> clarification, no command ---------------------------------------
@pytest.mark.asyncio
async def test_missing_parameter_returns_clarification_and_no_command(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    result = await _Session(monkeypatch).turn(
        [_SEARCH, _SELECT, _CATALOG, _recommend(params=[{"name": "target", "value": "NODE-7"}])],
        "Run show foo NODE-7.",
        user_text="A node raised a node alarm. What should I check next?",
    )
    record = result["records"][-1]
    assert record["diagnostic_step"]["command"] is None
    assert record["parameter_clarification"]["missing_parameters"] == ["target"]
    assert record[pa.PROCEDURE_ACTION_RESOLUTION_KEY]["parameters"][0]["state"] == "missing"
    assert "NODE-7" not in result["final"] and "<target>" not in result["final"]
    assert pa.CONFIRMED_PROCEDURE_PARAMETERS_STATE_KEY not in result["state"]


# --- O: troubleshooting continuity ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_o_resolved_action_recorded_and_user_result_advances_lifecycle(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    session = _Session(monkeypatch)
    first = await session.turn([_SEARCH, _SELECT, _CATALOG, _recommend()], "Run show foo NODE-1.")
    rec = _history(first["state"])[-1]
    assert (rec["status"], rec["procedure_action_id"], rec["grounded_command"], rec["command_source_id"]) == (
        "recommended",
        _ACTION_ID,
        "show foo NODE-1",
        _CANONICAL,
    )
    assert rec["expected_observation"] == "Alarm state output"

    report = "I ran show foo NODE-1: alarm state CLEARED."
    second = await session.turn(
        [_SEARCH, _SELECT, _CATALOG, _recommend(params=[])],  # no new statement: session-confirmed value binds
        "The alarm is cleared. Run show foo NODE-1 again only if it returns.",
        user_text=report,
        problem=report,
    )
    history = _history(second["state"])
    assert history[0]["status"] == "executed"
    assert history[0]["procedure_action_id"] == _ACTION_ID
    assert "alarm state CLEARED" in (history[0]["observed_result"] or "")
    params = second["records"][-1][pa.PROCEDURE_ACTION_RESOLUTION_KEY]["parameters"]
    assert params == [{"name": "target", "state": "verified", "value": "NODE-1", "detail": "confirmed earlier in session"}]


# --- P: observability --------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_p_trace_shows_catalog_selection_binding_render_and_authority(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    result = await _Session(monkeypatch).turn([_SEARCH, _SELECT, _CATALOG, _recommend()], "Run show foo NODE-1.")
    trace = _last_turn_trace(result["state"])
    catalog = trace["action_catalogs"][0]["actions"]
    assert [(a["action_id"], a["command_template"], a["parameters"], a["source"]) for a in catalog] == [
        (_ACTION_ID, "show foo <target>", ["target"], _CANONICAL)
    ]
    resolution = trace["action_resolutions"][0]
    assert resolution["action_id"] == _ACTION_ID and resolution["status"] == "resolved"
    assert resolution["parameters"][0]["state"] == "verified"
    assert resolution["rendered_command"] == "show foo NODE-1" and resolution["command_authority"] == "authorized"
    assert trace["technical_authority"]["procedure_action_id"] == _ACTION_ID
    assert any(c["decision"] == "authorized" and c["command"] == "show foo NODE-1" for c in trace["command_authority"])
    text = format_diagnostic_trace(trace)
    for expected in (
        "ACTION CATALOG 1 action(s)",
        f"ACTION SELECTED id={_ACTION_ID}",
        "RESOLUTION status=resolved",
        "PARAMETER target=VERIFIED(NODE-1)",
        "COMMAND RENDERED 'show foo NODE-1'",
        "AUTHORITY AUTHORIZED",
    ):
        assert expected in text
    assert "Do not run" not in json.dumps(trace), "no section body text in the trace"
