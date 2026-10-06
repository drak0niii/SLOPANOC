"""Live-sequence regression (real ChatService + real TechnicalAuthorityAgentTool, scripted LLMs):

    Baseband alarm investigation -> `st fieldr` result -> "what about reseting rru ?"
    -> target supplied (restart still premature: no RRU diagnosis) -> "what about reseting rru ?"
    again (governed prerequisite read) -> its result -> restart passes the remediation gate
    -> target confirmation -> approval

Proves: the explicit current request beats the stale Baseband objective; the session keeps both
fault threads; evidence selection stays explicit; no fabricated command reaches the user; the
server-owned progression keeps the pending step; the state-changing restart still needs the full governed
path + policy + target confirmation + human approval, and is never executed.
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
from backend.agents.technical_authority_engineer.troubleshooting_threads import THREADS_STATE_KEY
from backend.api import approval_service, execution_service
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.approval.service import load_active_proposal
from backend.cases.service import CaseService
from backend.gateway.safe_error import SafeErrorException
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.operations import control_plane as cp
from backend.tests.test_ess_service_unavailable_e2e_verification import _fc, _ScriptedCallLlm, isolated_km_repo  # noqa: F401
from backend.tools.knowledge.diagnostic_trace import RETRIEVAL_DIAGNOSTICS_STATE_KEY, format_diagnostic_trace
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence

_BB, _RRU = "KID-BASEBAND", "KID-RRU"
_BB_SEC, _RRU_SEC = "sec-bb", "sec-rru"
_BB_CONTENT = "Check the baseband field replaceable unit state: `st fieldr`\n"
_RRU_CONTENT = (
    "Before any recovery, check the radio port reference: `hget near Rfportref`\n"
    "Radio unit recovery: `acc <mo> restart`\n"
)


def _ids(kid: str, sec: str, content: str) -> dict[str, str]:
    return {a.command_template: a.action_id for a in pa.extract_procedure_actions(knowledge_id=kid, version_label="v1", section_id=sec, content=content)[0]}


_FIELDR = _ids(_BB, _BB_SEC, _BB_CONTENT)["st fieldr"]
_RRU_ACTIONS = _ids(_RRU, _RRU_SEC, _RRU_CONTENT)
_HGET, _RESTART = _RRU_ACTIONS["hget near Rfportref"], _RRU_ACTIONS["acc <mo> restart"]


def _mop(kid: str, title: str, sec: str, content: str) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=kid,
        document_type=KnowledgeDocumentType.MOP,
        title=title,
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id=f"{kid}.docx", display_name=title),
        sections=[KnowledgeSection(section_id=sec, knowledge_id=kid, heading=title, sequence=0, content=content, source_locator="lines:1-2")],
    )


def _search(q: str) -> list[types.Part]:
    return _fc("knowledge_search", {"query_text": q})


def _select(kid: str, sec: str) -> list[types.Part]:
    return _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": kid, "version_label": "v1", "section_id": sec}]})


_CATALOG = _fc("procedure_action_catalog", {})


def _respond(outcome: str = "recommended", action_id: Optional[str] = None, params: Optional[list[dict[str, str]]] = None, action: str = "Perform the governed step") -> list[types.Part]:
    step = None
    if outcome == "recommended":
        step = {
            "action": action, "reason": "Governed procedure step for the current request.", "expected_evidence": "Command output",
            "command": None, "command_source": None, "restrictions": [], "procedure_action_id": action_id, "parameter_values": params or [],
        }
    return [types.Part.from_text(text=json.dumps({"outcome": outcome, "technical_interpretation": "Interpretation.", "missing_information": [], "diagnostic_step": step}))]


class _Conversation:
    def __init__(self, monkeypatch: Any) -> None:
        self.records: list[dict[str, Any]] = []
        original = tae_agent_tool.record_technical_authority_execution

        def _capture(run_id: str, record: dict[str, Any]) -> None:
            self.records.append(record)
            original(run_id, record)

        monkeypatch.setattr(tae_agent_tool, "record_technical_authority_execution", _capture)
        self.sessions = ApiSessionService(adk_session_service=InMemorySessionService())
        self.session_id: Optional[str] = None

    async def turn(
        self,
        user_text: str,
        tae_calls: list[list[types.Part]],
        tm_final: str,
        current_request: Optional[dict[str, Any]] = None,
        extra_args: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if self.session_id is None:
            self.session_id = await self.sessions.create_session(user_id="eng")
        tae_llm = _ScriptedCallLlm(model="tae", parts_by_call=tae_calls)
        specialist = Agent(
            name="technical_authority_engineer", model=tae_llm,
            tools=[knowledge_search, knowledge_select_evidence, pa.procedure_action_catalog],
            input_schema=TechnicalAuthorityRequest, output_schema=TechnicalAuthorityResponse,
        )
        from backend.agents.team_manager.agent import team_manager
        from backend.agents.team_manager.source_requirements import record_source_requirements
        from backend.api.session_service import APP_NAME

        args: dict[str, Any] = {"problem_statement": user_text}
        if current_request is not None:
            args["current_request"] = current_request
        args.update(extra_args or {})
        tm_llm = _ScriptedCallLlm(model="tm", parts_by_call=[
            _fc("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
            _fc("technical_authority_engineer", args),
            [types.Part.from_text(text=tm_final)],
        ])
        outer = team_manager.model_copy(update={"model": tm_llm, "tools": [record_source_requirements, TechnicalAuthorityAgentTool(agent=specialist)]})
        runner = Runner(app_name=APP_NAME, agent=outer, session_service=self.sessions.adk_session_service)
        chat = ChatService(session_service=self.sessions, runner=runner, case_service=CaseService())
        before = len(self.records)
        final = ""
        async for event in chat.execute_turn_events(session_id=self.session_id, message_text=user_text, user_id="eng"):
            if event.type == StreamEventType.MESSAGE_COMPLETED:
                final = event.data.get("content") or ""
        state = dict((await self.sessions.get_session(self.session_id, "eng")).state)
        request_text = " ".join(p.text for c in tae_llm._requests[0].contents for p in (c.parts or []) if getattr(p, "text", None)) if tae_llm._requests else ""
        trace = list((state.get(RETRIEVAL_DIAGNOSTICS_STATE_KEY) or {}).values())[-1]
        return {"final": final, "record": self.records[before:][-1], "state": state, "tae_request": json.loads(request_text), "trace": trace}


def _active(state: dict[str, Any]) -> dict[str, Any]:
    return state["troubleshooting_state"]


def _progression(turn: dict[str, Any]) -> dict[str, Any]:
    return [e for e in turn["trace"]["operational_events"] if e.get("stage") == "progression"][-1]


@pytest.mark.asyncio
async def test_live_sequence_follows_the_rru_objective_without_weakening_governance(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop(_BB, "Baseband Unit Fault Procedure", _BB_SEC, _BB_CONTENT))
    await isolated_km_repo.add(_mop(_RRU, "Radio Unit Recovery Procedure", _RRU_SEC, _RRU_CONTENT))
    conv = _Conversation(monkeypatch)

    # 1. Baseband alarm investigation: grounded read `st fieldr` authorized through the action path.
    t1 = await conv.turn(
        "Ericsson 4G: baseband unit fault alarm on the node. What should I check?",
        [_search("baseband unit fault"), _select(_BB, _BB_SEC), _CATALOG, _respond(action_id=_FIELDR, action="Check the baseband field replaceable unit state")],
        "Check the baseband unit. Run st fieldr.",
        {"subject_component": "baseband unit"},
    )
    assert t1["record"]["diagnostic_step"]["command"] == "st fieldr"
    baseband_fault = _active(t1["state"])["fault_id"]

    # 2. `st fieldr` result: continues the Baseband thread; the result is recorded on its check.
    t2 = await conv.turn(
        "st fieldr result: FieldReplaceableUnit=BB-1 OPER=DISABLED",
        [_search("baseband unit fault"), _select(_BB, _BB_SEC), _respond(outcome="insufficient_evidence")],
        "The baseband unit is disabled.",
    )
    assert _active(t2["state"])["fault_id"] == baseband_fault
    assert t2["tae_request"]["turn_request_contract"]["focus"] == "continue"
    bb_check = _active(t2["state"])["diagnostic_history"][0]
    assert bb_check["status"] == "executed" and "OPER=DISABLED" in bb_check["observed_result"]

    # 3. "what about reseting rru ?" -- the TM wrongly claims "continue"; the explicit request wins.
    t3 = await conv.turn(
        "what about reseting rru ?",
        [_search("radio unit recovery reset"), _select(_RRU, _RRU_SEC), _CATALOG, _respond(action_id=_RESTART, action="Recover the radio unit")],
        "Reset the RRU with acc RRU-1 restart now; the baseband is fine.",
        {"subject_component": "rru", "requested_operation": "reset", "continues_active_objective": True},
    )
    contract = t3["tae_request"]["turn_request_contract"]
    assert contract["focus"] == "switch"
    assert contract["diagnostic_objective"].lower().startswith("reset rru")
    assert "baseband" not in contract["diagnostic_objective"].lower()
    assert "active_investigation_context" not in t3["tae_request"], "the Baseband thread is not this turn's background"
    assert not t3["tae_request"].get("prior_steps_taken")
    rru_fault = _active(t3["state"])["fault_id"]
    assert rru_fault != baseband_fault and set(t3["state"][THREADS_STATE_KEY]) == {baseband_fault, rru_fault}
    search = t3["trace"]["searches"][0]
    assert search["query_text"] == "radio unit recovery reset"
    assert [(r["knowledge_id"], r["selection_state"]) for r in search["results"] if r["selection_state"] == "SELECTED"] == [(_RRU, "SELECTED")]
    assert all(r["selection_state"] == "AVAILABLE" for r in search["results"] if r["knowledge_id"] == _BB), "AVAILABLE != SELECTED"
    assert t3["trace"]["selections"][0]["status"] == "accepted"
    # Nothing on the RRU thread is diagnosed yet: the restart is premature (server-evaluated gate).
    assert _progression(t3)["decision"] == "rejected_premature_remediation"
    # "Before any recovery, check ..." is a RECOMMENDED order (no obligation wording): advisory only.
    assert {k for k, v in _progression(t3)["remediation_gate"].items() if not v["met"]} == {"diagnostic_evidence", "target_isolated"}
    assert _progression(t3)["remediation_gate"]["prerequisites"]["relationships"][0]["type"] == "recommended_before"
    assert t3["record"]["diagnostic_step"] is None and t3["record"]["outcome"] == "insufficient_evidence"
    assert "acc RRU-1 restart" not in t3["final"] and "restart" not in t3["final"].lower(), "no fabricated command"
    assert "THREAD created" in format_diagnostic_trace(t3["trace"])

    # 4. Target supplied by the operator: continues the RRU thread. An operator-typed target is a
    #    REQUEST, never case authority: nothing in this fault's trusted evidence shows RRU-2 yet, so
    #    the target is not isolated (and the restart is still premature): no control is planned.
    t4 = await conv.turn(
        "the target is RRU-2",
        [_search("radio unit recovery"), _select(_RRU, _RRU_SEC), _CATALOG, _respond(action_id=_RESTART, params=[{"name": "mo", "value": "RRU-2"}], action="Recover the radio unit")],
        "Run acc RRU-2 restart.",
        {"subject_component": "RRU", "explicit_target": "RRU-2", "continues_active_objective": True},
    )
    assert _active(t4["state"])["fault_id"] == rru_fault, "supplying a target never opens a new thread"
    assert _progression(t4)["decision"] == "rejected_premature_remediation"
    assert {k for k, v in _progression(t4)["remediation_gate"].items() if not v["met"]} == {"diagnostic_evidence", "target_isolated"}
    (gate,) = [e for e in t4["trace"]["operational_events"] if e.get("stage") == "target_gate"]
    assert gate["passed"] is False and gate["targets"][0]["status"] == "not_observed" and gate["targets"][0]["requested"] == ["RRU-2"]
    assert "operational_control" not in t4["record"] and load_active_proposal(t4["state"]) is None
    assert "acc RRU-2 restart" not in t4["final"]

    # 5. "what about reseting rru ?" again: same RRU thread; the governed prerequisite read passes.
    t5 = await conv.turn(
        "what about reseting rru ?",
        [_search("radio unit recovery reset"), _select(_RRU, _RRU_SEC), _CATALOG, _respond(action_id=_HGET, action="Check the radio port reference before any recovery")],
        "Before any reset, run hget near Rfportref.",
        {"subject_component": "rru", "requested_operation": "reset"},
    )
    assert _active(t5["state"])["fault_id"] == rru_fault
    assert t5["tae_request"]["turn_request_contract"]["focus"] == "continue"
    assert t5["record"]["diagnostic_step"]["command"] == "hget near Rfportref"
    assert "hget near Rfportref" in t5["final"]
    assert t5["record"]["operational_control"]["policy_decision"] == "allowed"

    # 6. Prerequisite result recorded -- it shows RfPortRef=RRU-2, so RRU-2 is now a trusted target of
    #    THIS fault (the procedure's `<mo>` slot is untyped: matched by exact identifier). The
    #    remediation gate passes on server state; the restart still needs target confirmation.
    t6 = await conv.turn(
        "hget near Rfportref\nRfPortRef=RRU-2 reference OK",
        [_search("radio unit recovery"), _select(_RRU, _RRU_SEC), _CATALOG, _respond(action_id=_RESTART, params=[{"name": "mo", "value": "RRU-2"}], action="Recover the radio unit")],
        "Run acc RRU-2 restart.",
    )
    assert _progression(t6)["decision"] == "new_step" and all(v["met"] for v in _progression(t6)["remediation_gate"].values())
    assert (_progression(t6)["lifecycle_stage"], _progression(t6)["remediation"]["state"]) == ("remediation", "candidate")
    assert t6["record"]["diagnostic_step"]["command"] is None, "a state change is never an in-turn command"
    control = t6["record"]["operational_control"]
    assert (control["control_stage"], control["policy_decision"], control["reason_codes"]) == (
        "awaiting_confirmation", "clarification_required", ["STATE_CHANGE", "TARGET_NOT_CONFIRMED"]
    )
    assert "acc RRU-2 restart" not in t6["final"]
    confirm_card = load_active_proposal(t6["state"])
    assert confirm_card.operation.value == "operational.confirmTarget"

    # Baseband history preserved throughout.
    threads = t6["state"][THREADS_STATE_KEY]
    assert threads[baseband_fault]["diagnostic_history"][0]["grounded_command"] == "st fieldr"
    assert threads[baseband_fault]["diagnostic_history"][0]["status"] == "executed"
    rru_history = threads[rru_fault]["diagnostic_history"]
    assert [(r["procedure_action_id"], r["status"]) for r in rru_history] == [(_HGET, "executed"), (_RESTART, "recommended")]

    # Full HITL for the restart: explicit confirmation, then human approval, ends READY_FOR_EXECUTION.
    confirmed = await approval_service.approve(conv.sessions, conv.session_id, confirm_card.proposal_id, "eng")
    approval = confirmed.pending_action
    assert approval.operational.kind == "approval" and approval.operational.command == "acc RRU-2 restart"
    approved = await approval_service.approve(conv.sessions, conv.session_id, approval.proposal_id, "eng")
    assert approved.pending_action.operational.control_stage == "ready_for_execution"
    with pytest.raises(SafeErrorException) as refused:
        await execution_service.execute(conv.sessions, conv.session_id, approval.proposal_id, "eng")
    assert refused.value.safe_error.reason == "state_change_execution_not_enabled"
    state = dict((await conv.sessions.get_session(conv.session_id, "eng")).state)
    restart_check = next(
        c for c in state[THREADS_STATE_KEY][rru_fault]["diagnostic_history"] if c.get("procedure_action_id") == _RESTART and c.get("control_id")
    )
    assert restart_check["control_stage"] == "ready_for_execution", "HITL state lands on the RRU thread"
    assert state["troubleshooting_state"]["fault_id"] == rru_fault
    control_record = cp.OperationalControlRecord.model_validate(state[cp.OPERATIONAL_CONTROLS_STATE_KEY][restart_check["control_id"]])
    assert [e.event for e in control_record.audit][-2:] == ["approved", "execution_not_started"]
    assert control_record.approved_by == "eng" and control_record.context.target.canonical_identifier == "RRU-2"
