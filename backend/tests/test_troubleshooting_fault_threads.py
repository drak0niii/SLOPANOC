"""Regression: one session may contain several fault threads (e.g. baseband, RRU, sync, battery).

current-turn request -> resolve active fault thread -> use THAT thread's history -> TAE.
Switching focus never deletes another thread; continuing never creates a new one.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer.troubleshooting_threads import (
    THREADS_STATE_KEY,
    ThreadDecision,
    find_thread_for_check,
    load_threads,
    resolve_active_thread,
    save_thread,
    save_threads,
)
from backend.agents.technical_authority_engineer.turn_request import build_turn_request_contract
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.tests.test_ess_service_unavailable_e2e_verification import (  # noqa: F401 (fixture)
    _S4_SEARCH,
    _S4_SELECT,
    _S4Session,
    _s4_mop,
    _s4_response,
    _tae_request_text,
    isolated_km_repo,
)


def _baseband_state() -> dict[str, Any]:
    ts = TroubleshootingState(fault_id="FAULT-BB", symptom_summary="Baseband unit fault alarm", subject_component="baseband")
    ts.record_recommended_check(action="Check Baseband Processing Unit status", rationale="r", expected_observation="e", check_id="chk-bb")
    return {"troubleshooting_state": ts.model_dump(mode="json")}


def _resolve(state: dict[str, Any], text: str, proposed: dict[str, Any] | None = None):
    active = TroubleshootingState.model_validate(state["troubleshooting_state"]) if "troubleshooting_state" in state else None
    contract = build_turn_request_contract(proposed or {}, text, active, {})
    ts, threads, decision, previous = resolve_active_thread(state, contract, problem_statement=text)
    save_threads(state, threads, ts.fault_id)
    return ts, decision, previous


def test_explicit_switch_creates_a_new_thread_and_keeps_the_old_one() -> None:
    state = _baseband_state()
    ts, decision, previous = _resolve(state, "what about reseting rru ?", {"subject_component": "RRU", "requested_operation": "reset"})
    assert decision is ThreadDecision.CREATED and previous == "FAULT-BB"
    assert ts.subject_component == "RRU" and ts.diagnostic_history == []
    threads = load_threads(state)
    assert set(threads) == {"FAULT-BB", ts.fault_id}
    assert [r.check_id for r in threads["FAULT-BB"].diagnostic_history] == ["chk-bb"], "baseband history preserved"
    assert state["troubleshooting_state"]["fault_id"] == ts.fault_id


def test_continuing_keeps_the_active_thread() -> None:
    state = _baseband_state()
    for text in ("yes", "ok done, what next?", "baseband shows DISABLED"):
        ts, decision, _ = _resolve(state, text)
        assert (ts.fault_id, decision) == ("FAULT-BB", ThreadDecision.CONTINUED)
    assert set(load_threads(state)) == {"FAULT-BB"}, "no thread created while continuing"


def test_returning_to_an_earlier_subject_reactivates_its_thread() -> None:
    state = _baseband_state()
    rru, _, _ = _resolve(state, "what about reseting rru ?", {"subject_component": "RRU"})
    back, decision, previous = _resolve(state, "back to the baseband issue", {"subject_component": "baseband"})
    assert (back.fault_id, decision, previous) == ("FAULT-BB", ThreadDecision.SWITCHED_TO_EXISTING, rru.fault_id)
    assert set(load_threads(state)) == {"FAULT-BB", rru.fault_id}


def test_legacy_single_thread_session_is_migrated_and_checks_found_in_any_thread() -> None:
    state = _baseband_state()
    assert THREADS_STATE_KEY not in state
    _resolve(state, "what about reseting rru ?", {"subject_component": "RRU"})
    owner = find_thread_for_check(state, "chk-bb")
    assert owner is not None and owner.fault_id == "FAULT-BB", "checks of a non-active thread stay reachable"
    owner.set_control_state("chk-bb", "ctl-1", "ready_for_execution")
    save_thread(state, owner)
    assert load_threads(state)["FAULT-BB"].diagnostic_history[0].control_stage == "ready_for_execution"
    assert state["troubleshooting_state"]["fault_id"] != "FAULT-BB", "saving another thread never changes the active one"


@pytest.mark.asyncio
async def test_tae_uses_only_the_resolved_threads_history(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_s4_mop())
    conv = _S4Session(monkeypatch)
    first = await conv.turn([_S4_SEARCH, _S4_SELECT, _s4_response("alt", "MOP_S4 Governed Alarm Procedure.docx")], "Run alt.")
    first_fault = first["state"]["troubleshooting_state"]["fault_id"]

    insufficient = [types.Part.from_text(text=json.dumps({"outcome": "insufficient_evidence", "technical_interpretation": "t", "missing_information": []}))]
    second = await conv.turn(
        [insufficient],
        "Noted.",
        user_text="what about reseting rru ?",
        tae_args={"problem_statement": "what about reseting rru ?", "current_request": {"subject_component": "rru", "requested_operation": "reset"}},
    )
    request = json.loads(_tae_request_text(second["tae_llm"]))
    assert "active_investigation_context" not in request, "a new thread has no inherited background"
    assert not request.get("prior_steps_taken"), "the other thread's checks are not this thread's history"
    state = second["state"]
    threads = state[THREADS_STATE_KEY]
    assert first_fault in threads and len(threads) == 2
    assert state["troubleshooting_state"]["fault_id"] != first_fault
    first_thread = threads[first_fault]
    assert first_thread["diagnostic_history"][0]["status"] == "recommended", "the RRU question is never recorded as the old check's result"


def test_supplying_a_compound_target_continues_the_thread_it_names() -> None:
    """Found during verification: `RRU-2` must relate to the `rru` thread, not open a new one."""
    state = _baseband_state()
    rru, _, _ = _resolve(state, "what about reseting rru ?", {"subject_component": "rru", "requested_operation": "reset"})
    ts, decision, _ = _resolve(state, "the target is RRU-2", {"subject_component": "RRU", "explicit_target": "RRU-2"})
    assert (ts.fault_id, decision) == (rru.fault_id, ThreadDecision.CONTINUED)
    assert len(load_threads(state)) == 2
