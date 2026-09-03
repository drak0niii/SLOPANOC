"""Regression tests for the two live production incidents:

P0: `DatabaseSessionService` raised "The session has been modified in
storage since it was loaded" from `chat_service.py`'s own post-Runner
cleanup write -- AFTER a valid Teams summary had already streamed to the
user -- because that write reused the turn-start `Session` object instead
of reloading a fresh one.

P1: a resumed `SelectionCard` continuation still called `teams.listChats`
before `teams.getMessages`, even though the authoritative `chat_id` was
already known -- prompt-only enforcement (`{resolved_chat_id?}`) was
insufficient; the fix removes `teams_list_chats` from the tool schema for
this execution path entirely.

Uses a REAL `google.adk.sessions.DatabaseSessionService` (temp SQLite
file) for the P0 tests -- `InMemorySessionService` (used by every other
test file's default `ApiSessionService()`) does not implement the
storage-revision staleness check at all, so it could never reproduce (or
prove the fix for) this incident.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from google.adk.events import Event, EventActions
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from backend.agents.incident_manager.evidence import validate_evidence
from backend.agents.team_manager import read_continuation_execution as execution_module
from backend.agents.team_manager.read_continuation_presentation import (
    PENDING_SPECIALIST_RESULT_STATE_KEY,
    TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY,
    pop_current_run_specialist_result,
)
from backend.api import selection_service
from backend.api.chat_service import ChatService
from backend.api.session_service import APP_NAME, ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.gateway import power_automate_client as pac_module
from backend.selection.service import load_active_selection
from backend.tests._api_fakes import FakeRunner
from backend.tests._fakes import FakeResponse, chat, message
from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY, teams_get_messages
from backend.tools.teams.list_chats import teams_list_chats


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


def _sqlite_url(tmp_path: Path) -> str:
    db_file = tmp_path / "p0_p1_regression.db"
    return f"sqlite+aiosqlite:///{db_file.as_posix()}"


def _db_backed_session_service(tmp_path: Path) -> ApiSessionService:
    return ApiSessionService(adk_session_service=DatabaseSessionService(_sqlite_url(tmp_path)))


class _FakeEvent:
    def __init__(self, content: Any) -> None:
        self.content = content


class _RealRetrievalFakeIncidentManagerRunner:
    """Monkeypatched onto `execution_module.Runner` -- exercises the REAL
    `execute_read_continuation` machinery (session creation/deletion, the
    reduced-toolset agent, request-building) end to end, faking ONLY the
    actual Gemini call (no live credentials in this offline suite -- the
    same limitation every other prompt-dependent test in this codebase
    already documents). Simulates a CORRECTLY-behaving incident_manager
    by calling the REAL `teams_get_messages`/`validate_evidence` directly
    -- proving the real deterministic evidence chain -- using the
    `resolved_chat_id` this SAME production code seeds into the
    child session's state (never `teams_list_chats`, which the reduced
    agent variant does not even expose as a tool).

    R2 FIX (correctness-regression pass): a REAL ADK tool call's state
    writes (`tool_context.state[...] = ...`) reach the session through
    ADK's own event/state-delta machinery, applied automatically by the
    real `Runner`. Since this fake bypasses the real `Runner` entirely, it
    must replicate that ONE side effect explicitly -- persisting `known_
    message_ids` back into the REAL session via `append_event` (mirroring
    `backend/tests/_api_fakes.py`'s own `append_state_delta` fallback
    pattern) -- otherwise `execute_read_continuation`'s own new `_
    authoritative_retrieval_verified` check (which re-fetches the session
    fresh, exactly like P0's own "reload the canonical session" fix) would
    never see it and would incorrectly reject this legitimate retrieval.

    P4B: handles BOTH request shapes `execute_read_continuation` can now
    produce (see read_continuation_execution.py's own docstring) --
    `prefetched_evidence` present (the common, no-`requested_time_range`
    case: the REAL `teams_get_messages` already ran, via the application,
    BEFORE this fake model call was ever reached -- `KNOWN_MESSAGE_IDS_
    STATE_KEY` is therefore already correctly seeded in the session by
    production code itself; this fake only needs to synthesize FROM what
    was already retrieved, exactly like a real model would), or the OLD
    shape (`requested_time_range` was given -- this fake still calls
    retrieval itself, unchanged, since that is still the model-driven
    path in production too).
    """

    def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
        self._app_name = app_name
        self._session_service = session_service

    async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
        request = json.loads(new_message.parts[0].text)
        session = await self._session_service.get_session(
            app_name=self._app_name, user_id=user_id, session_id=session_id
        )

        if "prefetched_evidence" in request:
            prefetched = request["prefetched_evidence"]
            candidate_evidence = [
                {"message_id": m["message_id"], "author": m["author"], "sent_at": m["sent_at"]} for m in prefetched
            ]
            payload = {
                "outcome": "ok" if prefetched else "no_result",
                "chat_id": session.state.get("resolved_chat_id"),
                "chat_title": request.get("chat_topic"),
                "summary": "Resumed summary." if prefetched else None,
                "evidence": candidate_evidence,
            }
            yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))
            return

        resolved_chat_id = session.state.get("resolved_chat_id")
        assert resolved_chat_id is not None  # the continuation must have seeded this

        ctx = _Ctx(dict(session.state))
        retrieval = teams_get_messages(chat_id=resolved_chat_id, tool_context=ctx)
        candidate_evidence = [
            {"message_id": m["id"], "author": m["author"], "sent_at": m["sent_at"]} for m in retrieval["messages"]
        ]
        known_ids = set(ctx.state.get(KNOWN_MESSAGE_IDS_STATE_KEY, []))
        validated = validate_evidence(candidate_evidence, known_ids)

        # Persist exactly the one key `teams_get_messages` itself would
        # have written via a real ToolContext -- see this class's own
        # "R2 FIX" docstring above.
        state_delta_event = Event(
            author="incident_manager",
            invocation_id="test-invocation",
            actions=EventActions(state_delta={KNOWN_MESSAGE_IDS_STATE_KEY: ctx.state.get(KNOWN_MESSAGE_IDS_STATE_KEY, [])}),
        )
        await self._session_service.append_event(session, state_delta_event)

        payload = {
            "outcome": "ok",
            "chat_id": resolved_chat_id,
            "chat_title": request.get("chat_topic"),
            "summary": "Resumed summary.",
            "evidence": validated,
        }
        yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

    async def close(self) -> None:
        return None


def _counting_gateway(list_chats_payload: list, get_messages_by_chat_id: dict, call_counts: dict):
    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, list_chats_payload)
        if operation == "teams.getMessages":
            return FakeResponse(200, get_messages_by_chat_id.get(json.get("chatId"), []))
        if operation == "teams.getMembers":
            return FakeResponse(200, [])
        raise AssertionError(f"unexpected operation: {operation}")

    return fake_post


async def _seed_and_choose(service: ApiSessionService, session_id: str, query_topic: str, chat_title: str):
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(topic=query_topic, pending_operation="summarize", tool_context=ctx)
    await service.persist_state_delta(session, dict(ctx.state))
    pending = load_active_selection((await service.get_session(session_id)).state)
    chosen = next(o for o in pending.options if o.label == chat_title)
    return await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)


# ============================================================================
# P0 -- stale canonical session object
# ============================================================================


@pytest.mark.asyncio
async def test_p0_no_stale_session_error_after_a_successful_resumed_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the exact live incident against a REAL
    `DatabaseSessionService`: a resumed continuation succeeds, team_
    manager's own Runner call (via `FakeRunner`, which itself performs a
    real `get_session`+`persist_state_delta` round trip -- a SEPARATE
    session object from `chat_service.py`'s own turn-start one, exactly
    like the real ADK Runner) mutates the SAME canonical session row
    afterward. The turn must complete with a real MESSAGE_COMPLETED and
    NO error event -- the previous bug raised `ValueError` from the
    `finally` block's cleanup write, AFTER message.completed had already
    streamed.
    """
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway([chat("chat-real-1", "SLOPANOC Gateway Group Test")], {}, call_counts),
    )

    service = _db_backed_session_service(tmp_path)
    session_id = await service.create_session()
    choose_response = await _seed_and_choose(
        service, session_id, "SLOPANOC Gateway Group", "SLOPANOC Gateway Group Test"
    )

    async def fake_executor(*, user_id, continuation, **kwargs):
        return {
            "outcome": "ok",
            "chat_id": continuation.selected_chat_id,
            "chat_title": continuation.selected_chat_topic,
            "summary": "Resumed summary.",
            "evidence": [],
        }

    # `FakeRunner` performs its OWN `get_session`/`persist_state_delta`
    # round trip -- a genuinely separate Session object from chat_
    # service.py's own -- reproducing the real staleness precondition
    # against the real DatabaseSessionService backend.
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Resumed summary."),
        read_continuation_executor=fake_executor,
    )
    collected = [
        event
        async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    assert not any(e.type == StreamEventType.ERROR for e in collected)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Resumed summary."
    assert collected[-1].type == StreamEventType.RUN_COMPLETED
    assert collected[-1].data.get("outcome") == "ok"

    # Cleanup state was actually persisted (via the reloaded session).
    final = await service.get_session(session_id, "api-user")
    assert final.state.get(TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY) is None
    assert final.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY) is None


@pytest.mark.asyncio
async def test_p0_next_run_does_not_see_stale_trusted_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Directly addresses the second symptom from the live report
    ("trusted_specialist_result_rejected reason=stale_run_id" on the next
    run): with P0 fixed, cleanup succeeds normally, so the NEXT turn's own
    turn-start sweep finds nothing stale at all.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway([chat("chat-real-1", "SLOPANOC Gateway Group Test")], {}, {}),
    )
    service = _db_backed_session_service(tmp_path)
    session_id = await service.create_session()
    choose_response = await _seed_and_choose(
        service, session_id, "SLOPANOC Gateway Group", "SLOPANOC Gateway Group Test"
    )

    async def fake_executor(*, user_id, continuation, **kwargs):
        return {
            "outcome": "ok",
            "chat_id": continuation.selected_chat_id,
            "chat_title": continuation.selected_chat_topic,
            "summary": "Resumed summary.",
            "evidence": [],
        }

    chat_service = ChatService(
        service, runner=FakeRunner(service, respond=lambda t: "Resumed summary."), read_continuation_executor=fake_executor
    )
    async for _ in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user"):
        pass

    # A second, ordinary turn -- its own turn-start sweep must find
    # nothing left over from the first.
    session = await service.get_session(session_id, "api-user")
    result, was_present = pop_current_run_specialist_result(session.state, current_run_id="a-fresh-run-id")
    assert result is None
    assert was_present is False  # nothing was even THERE to reject


@pytest.mark.asyncio
async def test_p0_cleanup_reload_survives_a_turn_that_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exception cleanup: even when the specialist executor itself raises,
    the reload-based cleanup write must still succeed against the real
    DatabaseSessionService (no stale-session error masking the original
    failure), and the resulting error event must be the intended safe
    failure, not a stale-session ValueError.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway([chat("chat-real-1", "SLOPANOC Gateway Group Test")], {}, {}),
    )
    service = _db_backed_session_service(tmp_path)
    session_id = await service.create_session()
    choose_response = await _seed_and_choose(
        service, session_id, "SLOPANOC Gateway Group", "SLOPANOC Gateway Group Test"
    )

    async def failing_executor(*, user_id, continuation, **kwargs):
        raise RuntimeError("simulated specialist failure")

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "should never be presented"),
        read_continuation_executor=failing_executor,
    )
    collected = [
        event
        async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    assert any(e.type == StreamEventType.ERROR for e in collected)
    assert not any(e.type == StreamEventType.MESSAGE_COMPLETED for e in collected)
    error_event = next(e for e in collected if e.type == StreamEventType.ERROR)
    assert "session has been modified" not in error_event.data.get("message", "").lower()

    final = await service.get_session(session_id, "api-user")
    assert final.state.get(TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY) is None
    assert final.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY) is None


@pytest.mark.asyncio
async def test_p0_cleanup_reload_survives_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancellation cleanup: a run cancelled mid-Runner-call must still
    reach the `finally` block's reload-based cleanup without a stale-
    session error, against the real DatabaseSessionService.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway([chat("chat-real-1", "SLOPANOC Gateway Group Test")], {}, {}),
    )
    service = _db_backed_session_service(tmp_path)
    session_id = await service.create_session()
    choose_response = await _seed_and_choose(
        service, session_id, "SLOPANOC Gateway Group", "SLOPANOC Gateway Group Test"
    )

    async def fake_executor(*, user_id, continuation, **kwargs):
        return {
            "outcome": "ok",
            "chat_id": continuation.selected_chat_id,
            "chat_title": continuation.selected_chat_topic,
            "summary": "Resumed summary.",
            "evidence": [],
        }

    class _CancellingRunner(FakeRunner):
        async def run_async(self, **kwargs):
            # Perform the same real session mutation a live Runner would,
            # then raise CancelledError -- mirrors this codebase's own
            # established cancellation-cleanup test pattern (test_chat_
            # service_turn_context_lifecycle.py's `_RecordingRunner(...,
            # raise_after=asyncio.CancelledError())`): `execute_turn_
            # events` runs the turn as a background task and never
            # forwards this exception directly to the SSE-consumer-facing
            # generator, so the correct assertion is the resulting STATE
            # (cleanup ran), not `pytest.raises` around the consumer loop.
            async for event in super().run_async(**kwargs):
                yield event
            raise asyncio.CancelledError()

    chat_service = ChatService(
        service, runner=_CancellingRunner(service, respond=lambda t: "partial"), read_continuation_executor=fake_executor
    )

    async for _ in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user"):
        pass

    # Cleanup still ran and succeeded despite the cancellation -- no
    # stale-session error, and the trusted state is gone.
    final = await service.get_session(session_id, "api-user")
    assert final.state.get(TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY) is None
    assert final.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY) is None


# ============================================================================
# P1 -- zero teams.listChats after choose
# ============================================================================


@pytest.mark.asyncio
async def test_p1_zero_listchats_after_choose_via_the_real_execution_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact live path: ambiguous "Chat B" -> SelectionCard -> choose
    B2 -> resumed execution. Exercises the REAL `execute_read_continuation`
    (only the innermost ADK `Runner` is faked -- see `_RealRetrievalFake
    IncidentManagerRunner`), not a `read_continuation_executor` shortcut,
    so this proves the production reduced-toolset fix actually works
    end to end, not just in isolation.
    """
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway(
            [chat("chat-a", "Chat A"), chat("chat-b2-id", "Chat B2"), chat("chat-b3-id", "Chat B3")],
            {"chat-a": [message("a1", "Someone", "Chat A content -- must never be retrieved here.", "2026-08-01T09:00:00Z")],
             "chat-b2-id": [
                 message("b1", "Alex", "Chat B2 discussion.", "2026-08-20T09:00:00Z"),
                 message("b2", "Priya", "Chat B2 follow-up.", "2026-08-20T09:05:00Z"),
             ]},
            call_counts,
        ),
    )
    monkeypatch.setattr(execution_module, "Runner", _RealRetrievalFakeIncidentManagerRunner)

    service = ApiSessionService()
    session_id = await service.create_session()

    # Chat A already selected from earlier, unrelated work.
    session = await service.get_session(session_id)
    await service.persist_state_delta(
        session, {"selected_teams_chat_id": "chat-a", "selected_teams_chat_topic": "Chat A"}
    )

    listchats_calls_before_choose = call_counts.get("teams.listChats", 0)
    choose_response = await _seed_and_choose(service, session_id, "Chat B", "Chat B2")
    listchats_calls_at_discovery = call_counts.get("teams.listChats", 0)
    assert listchats_calls_at_discovery == listchats_calls_before_choose + 1  # the one legitimate discovery call

    after_choose = await service.get_session(session_id)
    assert after_choose.state["selected_teams_chat_id"] == "chat-b2-id"  # switched from Chat A

    chat_service = ChatService(service, runner=FakeRunner(service, respond=lambda t: "Resumed summary."))
    collected = [
        event
        async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    # --- THE hard acceptance criterion: zero listChats after choose.
    assert call_counts.get("teams.listChats", 0) == listchats_calls_at_discovery
    # --- getMessages called exactly once, for the authoritative chat.
    assert call_counts.get("teams.getMessages", 0) == 1

    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" in completed.data
    assert completed.data["source"]["message_count"] == 2

    final = await service.get_session(session_id, "api-user")
    assert final.state["selected_teams_chat_id"] == "chat-b2-id"


@pytest.mark.asyncio
async def test_p1_operation_focus_and_time_range_preserved_through_the_real_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway(
            [chat("chat-b2-id", "Chat B2")],
            {"chat-b2-id": [message("b1", "Alex", "We decided X.", "2026-08-20T09:00:00Z")]},
            call_counts,
        ),
    )
    monkeypatch.setattr(execution_module, "Runner", _RealRetrievalFakeIncidentManagerRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(
        topic="Chat B",
        pending_question="decisions from the last 7 days",
        pending_time_range="the last 7 days",
        pending_operation="summarize",
        tool_context=ctx,
    )
    await service.persist_state_delta(session, dict(ctx.state))
    pending = load_active_selection((await service.get_session(session_id)).state)
    chosen = next(o for o in pending.options if o.label == "Chat B2")
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    chat_service = ChatService(service, runner=FakeRunner(service, respond=lambda t: "We decided X."))
    async for _ in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user"):
        pass

    assert call_counts.get("teams.listChats", 0) == 1  # only the original discovery call
    assert call_counts.get("teams.getMessages", 0) == 1


# ============================================================================
# Combined P0 + P1 interaction
# ============================================================================


@pytest.mark.asyncio
async def test_p0_and_p1_combined_full_live_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduces the exact combined live failure end to end, against a
    real `DatabaseSessionService`, using the real `execute_read_
    continuation` (only the innermost model call is faked): ambiguous
    request -> choose -> deterministic getMessages -> Incident Manager
    synthesis -> Team Manager presentation -> trusted-state cleanup.
    Asserts BOTH zero listChats post-selection AND no stale-session
    error, then starts a new turn and asserts no stale specialist
    envelope/result remains and the selected Teams state is still valid.
    """
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway(
            [chat("chat-real-1", "SLOPANOC Gateway Group Test")],
            {"chat-real-1": [message("m1", "Alex", "We should migrate the gateway.", "2026-08-20T09:00:00Z")]},
            call_counts,
        ),
    )
    monkeypatch.setattr(execution_module, "Runner", _RealRetrievalFakeIncidentManagerRunner)

    service = _db_backed_session_service(tmp_path)
    session_id = await service.create_session()
    choose_response = await _seed_and_choose(
        service, session_id, "SLOPANOC Gateway Group", "SLOPANOC Gateway Group Test"
    )
    listchats_after_discovery = call_counts.get("teams.listChats", 0)

    # `FakeRunner` performs its own real session mutation, exactly like
    # team_manager's real Runner would, reproducing the P0 precondition
    # WHILE this turn also exercises the real P1 deterministic path.
    chat_service = ChatService(service, runner=FakeRunner(service, respond=lambda t: "The team discussed migrating the gateway."))
    collected = [
        event
        async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    # 1. Zero listChats post-selection.
    assert call_counts.get("teams.listChats", 0) == listchats_after_discovery
    assert call_counts.get("teams.getMessages", 0) == 1
    # 2. No stale-session error.
    assert not any(e.type == StreamEventType.ERROR for e in collected)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" in completed.data

    # New turn: no stale envelope/result remains; selected Teams state
    # (a genuinely different concern from the single-use continuation)
    # remains valid; the consumed continuation cannot replay.
    from backend.selection.service import pop_read_continuation

    session = await service.get_session(session_id, "api-user")
    assert pop_read_continuation(dict(session.state)) is None
    result, was_present = pop_current_run_specialist_result(session.state, current_run_id="another-fresh-run")
    assert result is None
    assert was_present is False
    assert session.state["selected_teams_chat_id"] == "chat-real-1"
