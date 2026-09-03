"""Production hardening pass #4: hard-process-crash stale trusted
specialist result protection, and the lightweight orphan internal-
specialist-session cleanup helper.

Covers: a `TrustedSpecialistResult` envelope surviving a simulated crash
(persisted, never cleared) must never be exposed to team_manager on a
later run; malformed/wrong-source/invalid-schema envelopes fail closed
the same way; a same-run envelope is accepted and presented normally;
normal `finally` cleanup is unaffected; a stale result can never produce
a SourceReference; and `cleanup_orphaned_internal_specialist_sessions`
only ever targets its own dedicated namespace, respects an age
threshold, and never touches normal user sessions.
"""
from __future__ import annotations

import time
from typing import Any

import pytest
from google.adk.sessions import InMemorySessionService

from backend.agents.incident_manager.schemas import IncidentManagerResponse
from backend.agents.team_manager.read_continuation_execution import (
    _INTERNAL_SPECIALIST_APP_NAME,
    cleanup_orphaned_internal_specialist_sessions,
)
from backend.agents.team_manager.read_continuation_presentation import (
    PENDING_SPECIALIST_RESULT_STATE_KEY,
    TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY,
    TrustedSpecialistResult,
    build_trusted_specialist_result_envelope,
    pop_current_run_specialist_result,
    validate_trusted_envelope_for_run,
)
from backend.api import selection_service
from backend.api.chat_service import ChatService
from backend.api.session_service import APP_NAME, ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.gateway import power_automate_client as pac_module
from backend.selection.service import load_active_selection
from backend.tests._api_fakes import FakeRunner
from backend.tests._fakes import FakeResponse, chat
from backend.tools.teams.list_chats import teams_list_chats

_VALID_RESULT = {
    "outcome": "ok",
    "chat_id": "chat-real-1",
    "chat_title": "SLOPANOC Gateway Group Test",
    "summary": "The team discussed migrating the gateway this sprint.",
    "evidence": [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-20T09:00:00Z"}],
}


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


def _counting_gateway(list_chats_payload: list, call_counts: dict):
    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, list_chats_payload)
        if operation == "teams.getMessages":
            return FakeResponse(200, [])
        if operation == "teams.getMembers":
            return FakeResponse(200, [])
        raise AssertionError(f"unexpected operation: {operation}")

    return fake_post


# --- Unit-level: build/validate/pop -------------------------------------


def test_build_envelope_binds_the_given_run_id_and_closed_source() -> None:
    envelope = build_trusted_specialist_result_envelope("run-A", _VALID_RESULT)
    assert envelope is not None
    assert envelope["run_id"] == "run-A"
    assert envelope["source"] == "incident_manager"
    assert envelope["result"]["chat_id"] == "chat-real-1"
    assert envelope["created_at"]  # server-set, non-empty


def test_build_envelope_fails_closed_for_an_invalid_result() -> None:
    assert build_trusted_specialist_result_envelope("run-A", {"outcome": "not_a_real_outcome"}) is None
    assert build_trusted_specialist_result_envelope("run-A", {}) is None


def test_validate_accepts_same_run_envelope() -> None:
    envelope = build_trusted_specialist_result_envelope("run-A", _VALID_RESULT)
    result = validate_trusted_envelope_for_run(envelope, current_run_id="run-A")
    assert result is not None
    assert result["chat_id"] == "chat-real-1"
    assert result["summary"] == _VALID_RESULT["summary"]


def test_validate_rejects_a_different_run_id() -> None:
    envelope = build_trusted_specialist_result_envelope("run-A", _VALID_RESULT)
    assert validate_trusted_envelope_for_run(envelope, current_run_id="run-B") is None


def test_validate_rejects_a_malformed_envelope() -> None:
    assert validate_trusted_envelope_for_run({"not": "an envelope"}, current_run_id="run-A") is None
    assert validate_trusted_envelope_for_run("not even a dict", current_run_id="run-A") is None
    assert validate_trusted_envelope_for_run(None, current_run_id="run-A") is None
    assert validate_trusted_envelope_for_run(123, current_run_id="run-A") is None


def test_validate_rejects_missing_run_id() -> None:
    raw = {"source": "incident_manager", "result": _VALID_RESULT}
    assert validate_trusted_envelope_for_run(raw, current_run_id="run-A") is None


def test_validate_rejects_wrong_source() -> None:
    raw = {"run_id": "run-A", "source": "some_other_agent", "result": _VALID_RESULT}
    assert validate_trusted_envelope_for_run(raw, current_run_id="run-A") is None


def test_validate_rejects_an_invalid_result_schema_even_with_a_matching_run_id() -> None:
    raw = {"run_id": "run-A", "source": "incident_manager", "result": {"outcome": "definitely_not_valid"}}
    assert validate_trusted_envelope_for_run(raw, current_run_id="run-A") is None


def test_pop_current_run_specialist_result_pops_and_reports_presence() -> None:
    state: dict[str, Any] = {}
    result, was_present = pop_current_run_specialist_result(state, current_run_id="run-A")
    assert result is None
    assert was_present is False

    state[TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY] = build_trusted_specialist_result_envelope(
        "run-A", _VALID_RESULT
    )
    result, was_present = pop_current_run_specialist_result(state, current_run_id="run-A")
    assert result is not None
    assert was_present is True
    assert TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY not in state  # popped


def test_pop_current_run_specialist_result_stale_still_pops_but_returns_none() -> None:
    state: dict[str, Any] = {
        TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY: build_trusted_specialist_result_envelope(
            "run-A", _VALID_RESULT
        )
    }
    result, was_present = pop_current_run_specialist_result(state, current_run_id="run-B")
    assert result is None
    assert was_present is True
    assert TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY not in state  # still removed


def test_source_field_is_a_closed_value() -> None:
    with pytest.raises(Exception):
        TrustedSpecialistResult(
            run_id="run-A", source="attacker_controlled", result=IncidentManagerResponse.model_validate(_VALID_RESULT)
        )


# --- THE key acceptance test: crash-recovery regression ------------------


@pytest.mark.asyncio
async def test_stale_run_a_envelope_is_never_exposed_to_run_b_and_is_cleared() -> None:
    """Run A: a trusted specialist result envelope exists in persistent
    session state (`run_id="run-A"`), simulating a process crash right
    after it was persisted but before `finally` could clear it. Run B
    starts (a completely ordinary, non-continuation turn): the stale
    Run A result must never be made available to team_manager, must be
    cleared from state, Run B must proceed normally (its own plain reply,
    no fabricated SourceReference), and no Run A data must appear
    anywhere in Run B's user-visible output.
    """
    service = ApiSessionService()
    session_id = await service.create_session()

    # Simulate: Run A wrote both the durable envelope AND the derived
    # presentation key, then the process died before `finally` ran.
    session = await service.get_session(session_id)
    stale_envelope = build_trusted_specialist_result_envelope("run-A-crashed", _VALID_RESULT)
    await service.persist_state_delta(
        session,
        {
            TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY: stale_envelope,
            PENDING_SPECIALIST_RESULT_STATE_KEY: _VALID_RESULT,
        },
    )

    received_messages: list[str] = []
    state_seen_by_team_manager: list[Any] = []

    async def side_effect(session_service, session, text):
        received_messages.append(text)
        state_seen_by_team_manager.append(session.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY))

    async def must_not_be_called(*, user_id, continuation, **kwargs):
        raise AssertionError("no continuation exists for run B -- the executor must never be invoked")

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, side_effect=side_effect, respond=lambda t: "Sure, happy to help with that."),
        read_continuation_executor=must_not_be_called,
    )
    collected = [
        event async for event in chat_service.execute_turn_events(session_id, "hi there, unrelated question", "api-user")
    ]

    # Run B never saw Run A's stale data.
    assert state_seen_by_team_manager == [None]
    for text in received_messages:
        assert "SLOPANOC Gateway Group Test" not in text
        assert "migrate the gateway" not in text

    # No fabricated SourceReference from stale data.
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Sure, happy to help with that."
    assert "source" not in completed.data
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)

    # Stale state is gone afterward.
    final = await service.get_session(session_id, "api-user")
    assert final.state.get(TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY) is None
    assert final.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY) is None


@pytest.mark.asyncio
async def test_same_run_envelope_is_accepted_and_normal_cleanup_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legitimate, same-run continuation execution still works end to
    end -- the trusted result is presented, and normal `finally` cleanup
    clears both state keys afterward (unaffected by the hard-crash
    protection added around it).
    """
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway([chat("chat-real-1", "SLOPANOC Gateway Group Test")], call_counts),
    )
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(topic="SLOPANOC Gateway Group", pending_operation="summarize", tool_context=ctx)
    await service.persist_state_delta(session, dict(ctx.state))
    pending = load_active_selection((await service.get_session(session_id)).state)
    chosen = next(o for o in pending.options if o.label == "SLOPANOC Gateway Group Test")
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    state_seen_by_team_manager: list[Any] = []

    async def side_effect(session_service, session, text):
        state_seen_by_team_manager.append(session.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY))

    async def fake_executor(*, user_id, continuation, **kwargs):
        return {
            "outcome": "ok",
            "chat_id": continuation.selected_chat_id,
            "chat_title": continuation.selected_chat_topic,
            "summary": "Summary.",
            "evidence": [],
        }

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, side_effect=side_effect, respond=lambda t: "Summary."),
        read_continuation_executor=fake_executor,
    )
    async for _ in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user"):
        pass

    assert len(state_seen_by_team_manager) == 1
    assert state_seen_by_team_manager[0] is not None
    assert state_seen_by_team_manager[0]["chat_id"] == "chat-real-1"

    final = await service.get_session(session_id, "api-user")
    assert final.state.get(TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY) is None
    assert final.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY) is None


@pytest.mark.asyncio
async def test_malformed_persisted_envelope_at_turn_start_is_cleared_and_ignored() -> None:
    """Section 9: a persisted value that doesn't even match the envelope
    shape (not just a run_id mismatch) must also fail closed at turn
    start -- never repaired, never exposed.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await service.persist_state_delta(
        session, {TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY: {"garbage": "not an envelope at all"}}
    )

    state_seen_by_team_manager: list[Any] = []

    async def side_effect(session_service, session, text):
        state_seen_by_team_manager.append(session.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY))

    chat_service = ChatService(
        service, runner=FakeRunner(service, side_effect=side_effect, respond=lambda t: "ok")
    )
    async for _ in chat_service.execute_turn_events(session_id, "hello", "api-user"):
        pass

    assert state_seen_by_team_manager == [None]
    final = await service.get_session(session_id, "api-user")
    assert final.state.get(TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY) is None


# --- User input cannot influence run_id/source ---------------------------


def test_no_public_api_accepts_a_client_supplied_run_id_or_source() -> None:
    """Structural guard: `build_trusted_specialist_result_envelope`'s only
    parameters are `run_id` (always `sequencer.run_id`, server-generated
    per turn) and `result` (the executor's own validated return value) --
    there is no request-JSON/user-text-shaped input path into either
    field. `source` is not even a parameter at all -- it is a fixed
    default on `TrustedSpecialistResult` itself, enforced by a validator.
    """
    import inspect

    from backend.agents.team_manager.read_continuation_presentation import (
        build_trusted_specialist_result_envelope as builder,
    )

    params = list(inspect.signature(builder).parameters)
    assert params == ["run_id", "result"]
    assert "source" not in params


# --- Orphan internal-specialist-session cleanup helper --------------------


@pytest.mark.asyncio
async def test_cleanup_only_deletes_sessions_older_than_the_age_threshold() -> None:
    canonical = InMemorySessionService()
    now = time.time()

    old_session = await canonical.create_session(
        app_name=_INTERNAL_SPECIALIST_APP_NAME, user_id="api-user", session_id="parent::specialist::old-run"
    )
    fresh_session = await canonical.create_session(
        app_name=_INTERNAL_SPECIALIST_APP_NAME, user_id="api-user", session_id="parent::specialist::fresh-run"
    )

    deleted_count = await cleanup_orphaned_internal_specialist_sessions(
        canonical,
        max_age_seconds=3600.0,
        now=now,
    )
    # Both sessions were "created now" in this test -- neither is old
    # enough yet, so nothing should be deleted.
    assert deleted_count == 0

    # Now simulate time passing far beyond the threshold for everything.
    deleted_count = await cleanup_orphaned_internal_specialist_sessions(
        canonical,
        max_age_seconds=1.0,
        now=now + 7200,
    )
    assert deleted_count == 2
    remaining = await canonical.list_sessions(app_name=_INTERNAL_SPECIALIST_APP_NAME)
    assert remaining.sessions == []


@pytest.mark.asyncio
async def test_cleanup_never_touches_normal_user_sessions() -> None:
    canonical = InMemorySessionService()
    now = time.time()

    await canonical.create_session(app_name=APP_NAME, user_id="api-user", session_id="real-user-session-1")
    await canonical.create_session(
        app_name=_INTERNAL_SPECIALIST_APP_NAME, user_id="api-user", session_id="parent::specialist::old-run"
    )

    deleted_count = await cleanup_orphaned_internal_specialist_sessions(
        canonical, max_age_seconds=1.0, now=now + 7200
    )
    assert deleted_count == 1  # only the internal-namespace session

    # The real user session is completely untouched -- different app_name,
    # never even listed by this helper (it only ever calls list_sessions
    # with app_name=_INTERNAL_SPECIALIST_APP_NAME).
    real_sessions = await canonical.list_sessions(app_name=APP_NAME, user_id="api-user")
    assert {s.id for s in real_sessions.sessions} == {"real-user-session-1"}


def test_cleanup_helper_source_never_references_the_user_facing_app_name() -> None:
    """Structural guard: the cleanup helper's own source must only ever
    call `list_sessions`/`delete_session` scoped to the dedicated internal
    namespace constant -- never the bare `APP_NAME` (which would risk
    scanning/deleting normal user sessions).
    """
    import inspect

    from backend.agents.team_manager import read_continuation_execution as execution_module

    source = inspect.getsource(execution_module.cleanup_orphaned_internal_specialist_sessions)
    assert "_INTERNAL_SPECIALIST_APP_NAME" in source
    assert "app_name=APP_NAME" not in source


def test_cleanup_helper_is_not_wired_into_any_scheduler_in_this_codebase() -> None:
    """Instruction section 14: no background daemon/scheduler/cron may be
    introduced by this pass -- confirmed here by checking the function is
    never referenced from any startup/app-wiring module.
    """
    import inspect

    import backend.api.app as app_module

    source = inspect.getsource(app_module)
    assert "cleanup_orphaned_internal_specialist_sessions" not in source
