"""Regression: historical troubleshooting context must not override the explicit current request.

Live defect: after a Baseband-status check, the operator asked "what about reseting rru ?" and the
TAE searched 'Ericsson 4G Baseband Processing Unit status'. Invariant: the EXPLICIT CURRENT-TURN
REQUEST takes precedence over the historical troubleshooting objective; history may only enrich it.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer.turn_request import build_turn_request_contract
from backend.tests.test_ess_service_unavailable_e2e_verification import (  # noqa: F401 (fixture)
    _S4_SEARCH,
    _S4_SELECT,
    _S4Session,
    _s4_mop,
    _s4_response,
    _tae_request_text,
    isolated_km_repo,
)

_ACTIVE = SimpleNamespace(
    symptom_summary="Resource Timeout alarm on the node",
    diagnostic_history=[SimpleNamespace(action="Check Baseband Processing Unit status")],
    working_hypothesis=None,
)
_FACTS = {"vendor": ["ericsson"], "technology": ["4g"]}
_LIVE = "what about reseting rru ?"


def test_explicit_new_subject_and_operation_define_the_objective() -> None:
    contract = build_turn_request_contract(
        {"subject_component": "RRU", "requested_operation": "reset", "continues_active_objective": True}, _LIVE, _ACTIVE, _FACTS
    )
    assert contract.focus == "switch", "a caller 'continue' claim never overrides an explicit change of subject"
    assert (contract.subject_component.value, contract.subject_component.source) == ("RRU", "current_message")
    assert contract.requested_operation.value == "reset"
    assert contract.diagnostic_objective.startswith("reset RRU")
    assert "baseband" not in contract.diagnostic_objective.lower()
    assert (contract.vendor.source, contract.technology.source) == ("session_confirmed", "session_confirmed"), "history only enriches"
    assert contract.user_request_text == _LIVE


def test_historical_values_cannot_redefine_the_current_request() -> None:
    contract = build_turn_request_contract(
        {"subject_component": "Baseband Processing Unit", "requested_operation": "status check", "intent": "check baseband status"},
        _LIVE,
        _ACTIVE,
        _FACTS,
    )
    assert contract.subject_component is None and contract.requested_operation is None and contract.intent is None
    assert {d["field"] for d in contract.discarded_fields} == {"subject_component", "requested_operation", "intent"}
    assert contract.focus == "switch"
    assert "baseband" not in contract.diagnostic_objective.lower()
    assert _LIVE in contract.diagnostic_objective


@pytest.mark.parametrize("text", ["yes", "ok done, what next?", "I ran it, here is the output"])
def test_implicit_follow_up_continues_the_active_objective(text: str) -> None:
    contract = build_turn_request_contract({}, text, _ACTIVE, _FACTS)
    assert contract.focus == "continue"
    assert "Check Baseband Processing Unit status" in contract.diagnostic_objective


def test_explicit_request_on_the_same_subject_continues() -> None:
    contract = build_turn_request_contract({"subject_component": "baseband"}, "baseband shows DISABLED", _ACTIVE, _FACTS)
    assert contract.focus == "continue" and contract.subject_component.value == "baseband"


def test_no_active_investigation_is_a_new_request() -> None:
    contract = build_turn_request_contract({"subject_component": "RRU"}, _LIVE, None, {})
    assert contract.focus == "new" and contract.active_investigation_objective is None


@pytest.mark.asyncio
async def test_tae_receives_the_current_request_not_the_historical_objective(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_s4_mop())
    conv = _S4Session(monkeypatch)
    await conv.turn([_S4_SEARCH, _S4_SELECT, _s4_response("alt", "MOP_S4 Governed Alarm Procedure.docx")], "Run alt.")

    followup = await conv.turn(
        [[types.Part.from_text(text=json.dumps({"outcome": "insufficient_evidence", "technical_interpretation": "t", "missing_information": []}))]],
        "Noted.",
        user_text=_LIVE,
        tae_args={
            # A caller that (wrongly) carries the old objective forward:
            "problem_statement": "Check active alarms on the node",
            "current_request": {"subject_component": "active alarms", "requested_operation": "check", "explicit_target": "rru", "continues_active_objective": True},
        },
    )
    request: dict[str, Any] = json.loads(_tae_request_text(followup["tae_llm"]))
    contract = request["turn_request_contract"]
    assert contract["user_request_text"] == _LIVE
    assert contract["focus"] == "switch"
    assert contract.get("subject_component") is None and contract.get("requested_operation") is None
    assert "active alarms" not in contract["diagnostic_objective"].lower()
    assert not contract.get("discarded_fields"), "discarded historical values never reach the specialist"
    assert request.get("current_request") is None
    # The explicit new subject opens its own fault thread: the ESS thread's history is not this
    # thread's background (see test_troubleshooting_fault_threads.py).
    assert "active_investigation_context" not in request
    assert "[Active troubleshooting context" not in request["problem_statement"]
