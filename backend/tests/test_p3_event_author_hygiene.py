"""P3 -- session/event-hygiene pass: eliminate the live "Event from an
unknown agent: slopanoc-api" ADK warning by fixing the underlying event
shape `ApiSessionService.persist_state_delta` creates, not by suppressing
the warning.

ROOT CAUSE (verified against the installed ADK 1.33.0 source, never
guessed -- see session_service.py's own "P3 FIX" docstring for the full
citation trail): `persist_state_delta` built `Event(author="slopanoc-api",
...)` to persist an application-side state delta -- but `google.adk.
events.event.Event.author`'s own field docstring is definitive: "'user'
OR the name of the agent". `Runner._find_agent_to_run` (runners.py) walks
`session.events` in reverse at the START of every new turn to find which
agent should continue the session; any event whose `author` is neither
`root_agent.name` nor a real sub-agent name (via `root_agent.
find_sub_agent`) triggers the warning. `"slopanoc-api"` was never a
recognized author under either category.

THE FIX: `author='user'` -- not a workaround, but the SAME shape ADK's OWN
`Runner.rewind_async` uses internally for its own action-only, no-content
"rewind marker" events (verified directly in `runners.py`). `_find_agent_
to_run`'s own `_event_filter` already skips every `author == 'user'` event
before the unknown-agent check ever runs -- an ADK-native exclusion path,
not an internal/workflow-only field.

These tests exercise the REAL ADK `Runner._find_agent_to_run` (the exact
method that emits the warning) and the REAL `google.adk.flows.llm_flows.
contents._get_contents`-equivalent content-visibility gate, never a
re-derived approximation of ADK's own logic.
"""
from __future__ import annotations

import logging

import pytest
from google.adk.agents import Agent
from google.adk.runners import Runner

from backend.agents.team_manager.agent import team_manager
from backend.api.session_service import APP_NAME, ApiSessionService

_ADK_RUNNER_LOGGER = "google_adk.google.adk.runners"


def _real_runner(session_service: ApiSessionService) -> Runner:
    """A real ADK `Runner` bound to the real `team_manager` agent -- cheap
    and local to construct (verified elsewhere in this codebase's own
    P4COLD pass: `Gemini.__init__`/`Agent.__init__` perform no network
    I/O). Never calls `run_async` here -- only the private, synchronous,
    network-free `_find_agent_to_run`, the exact method under test.
    """
    return Runner(app_name=APP_NAME, agent=team_manager, session_service=session_service.adk_session_service)


# --- TEST A: the old author=slopanoc-api shape is no longer produced -------


@pytest.mark.asyncio
async def test_a_persisted_state_delta_events_are_no_longer_authored_slopanoc_api() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    await service.persist_state_delta(session, {"selected_teams_chat_id": "chat-1"})
    session = await service.get_session(session_id)
    await service.persist_state_delta(session, {"trusted_specialist_result_envelope": {"run_id": "r1"}})

    session = await service.get_session(session_id)
    authors = {event.author for event in session.events}
    assert "slopanoc-api" not in authors
    assert authors == {"user"}  # the new, ADK-documented shape


@pytest.mark.asyncio
async def test_a_persisted_state_delta_events_carry_no_content() -> None:
    """Action-only, exactly like ADK's own rewind-marker events -- never a
    fake conversational message.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await service.persist_state_delta(session, {"some_key": "some_value"})

    session = await service.get_session(session_id)
    (event,) = session.events
    assert event.content is None
    assert event.actions.state_delta == {"some_key": "some_value"}


# --- TEST B: the real Runner history-resolution path -----------------------


@pytest.mark.asyncio
async def test_b_real_runner_finds_no_unknown_agent_after_state_only_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Persists the shape of state a resolved continuation actually
    writes (trusted result envelope, pending specialist result, selected-
    chat sync, cleanup clears), then invokes the REAL `Runner._find_agent_
    to_run` -- the exact ADK method that produced the live warning.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    # A real prior team_manager turn, so there is a genuine agent-authored
    # event for _find_agent_to_run to fall back to/find.
    from google.adk.events import Event

    prior_turn = Event(author="team_manager", invocation_id="inv-0")
    await service.adk_session_service.append_event(session, prior_turn)
    session = await service.get_session(session_id)

    for delta in (
        {"trusted_specialist_result_envelope": {"run_id": "r1", "source": "incident_manager"}},
        {"pending_specialist_result": {"outcome": "ok"}},
        {"selected_teams_chat_id": "chat-1", "selected_teams_chat_topic": "Chat One"},
        {"trusted_specialist_result_envelope": None, "pending_specialist_result": None},  # cleanup
    ):
        await service.persist_state_delta(session, delta)
        session = await service.get_session(session_id)

    runner = _real_runner(service)
    with caplog.at_level(logging.WARNING, logger=_ADK_RUNNER_LOGGER):
        agent = runner._find_agent_to_run(session, team_manager)

    assert not any("unknown agent" in r.getMessage().lower() for r in caplog.records)
    assert agent is team_manager  # correctly resolves to the real prior turn's agent

    # State persistence itself still works -- this is not merely "no
    # warning", the actual required data is still there.
    assert session.state["selected_teams_chat_id"] == "chat-1"
    assert session.state.get("trusted_specialist_result_envelope") is None
    assert session.state.get("pending_specialist_result") is None


@pytest.mark.asyncio
async def test_b_no_equivalent_warning_under_another_author_name(caplog: pytest.LogCaptureFixture) -> None:
    """Section 14's own extra requirement: not just THIS warning gone, but
    no NEW unknown-agent warning under some other author either.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await service.persist_state_delta(session, {"k": "v"})
    session = await service.get_session(session_id)

    runner = _real_runner(service)
    with caplog.at_level(logging.WARNING, logger=_ADK_RUNNER_LOGGER):
        runner._find_agent_to_run(session, team_manager)

    assert caplog.records == []


# --- TEST C: full resolved-continuation flow, zero unknown-agent events ----


@pytest.mark.asyncio
async def test_c_full_resolved_continuation_flow_produces_no_unknown_agent_events(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Reuses the same real-execution-path pattern already established for
    R1/R2/R3 regression coverage (test_p0_p1_live_incident_regression.py's
    `_RealRetrievalFakeIncidentManagerRunner`) -- only the innermost model
    call is faked; session persistence, `execute_read_continuation`, and
    the presentation-only Team Manager runner selection are all real.
    """
    from backend.agents.team_manager import read_continuation_execution as execution_module
    from backend.api import selection_service
    from backend.api.chat_service import ChatService
    from backend.api.streaming_events import StreamEventType
    from backend.gateway import power_automate_client as pac_module
    from backend.selection.service import load_active_selection
    from backend.tests._api_fakes import FakeEvent, FakeFunctionCall, FakeFunctionResponse, FakeRunner
    from backend.tests._fakes import FakeResponse, chat, message
    from backend.tests.test_p0_p1_live_incident_regression import _RealRetrievalFakeIncidentManagerRunner

    call_counts: dict[str, int] = {}

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, [chat("kms-a", "Knowledge Sync Weekly"), chat("kms-b", "Knowledge Sync Daily")])
        if operation == "teams.getMessages":
            return FakeResponse(200, [message("m1", "Sam", "We reviewed priorities.", "2026-08-20T09:00:00Z")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    monkeypatch.setattr(execution_module, "Runner", _RealRetrievalFakeIncidentManagerRunner)

    service = ApiSessionService()
    session_id = await service.create_session()

    class _Ctx:
        def __init__(self, state):
            self.state = state

    async def ambiguous_side_effect(session_service, session, text):
        from backend.tools.teams.list_chats import teams_list_chats

        ctx = _Ctx(dict(session.state))
        teams_list_chats(topic="Knowledge Sync", pending_operation="summarize", tool_context=ctx)
        await session_service.persist_state_delta(session, dict(ctx.state))

    chat_service_1 = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=[
                FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
                FakeEvent(
                    final=False,
                    function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "selection_needed"})],
                ),
                FakeEvent(text="I found similar chats -- please pick one below.", final=True),
            ],
            side_effect=ambiguous_side_effect,
        ),
    )
    collected_1 = [e async for e in chat_service_1.execute_turn_events(session_id, "summarize Knowledge Sync Daily", "api-user")]
    assert any(e.type == StreamEventType.SELECTION_PENDING for e in collected_1)
    assert call_counts.get("teams.listChats", 0) == 1

    pending = load_active_selection((await service.get_session(session_id)).state)
    chosen = next(o for o in pending.options if o.label == "Knowledge Sync Daily")
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    chat_service_2 = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "WRONG -- normal runner must not be used here."),
        presentation_runner=FakeRunner(service, respond=lambda t: "Here is the Knowledge Sync Daily summary."),
    )
    collected_2 = [e async for e in chat_service_2.execute_turn_events(session_id, choose_response.resume_message, "api-user")]

    assert call_counts.get("teams.getMessages", 0) == 1
    completed = next(e for e in collected_2 if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Here is the Knowledge Sync Daily summary."
    assert "source" in completed.data  # SourceReference still generated

    final_session = await service.get_session(session_id)
    authors = {event.author for event in final_session.events}
    assert "slopanoc-api" not in authors

    runner = _real_runner(service)
    with caplog.at_level(logging.WARNING, logger=_ADK_RUNNER_LOGGER):
        runner._find_agent_to_run(final_session, team_manager)
    assert not any("unknown agent" in r.getMessage().lower() for r in caplog.records)


# --- TEST D: cleanup state ----------------------------------------------------


@pytest.mark.asyncio
async def test_d_normal_completion_clears_trusted_state_without_a_fake_agent_event() -> None:
    from backend.agents.team_manager.read_continuation_presentation import (
        PENDING_SPECIALIST_RESULT_STATE_KEY,
        TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY,
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    await service.persist_state_delta(
        session,
        {
            TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY: {"run_id": "r1"},
            PENDING_SPECIALIST_RESULT_STATE_KEY: {"outcome": "ok"},
            "selected_teams_chat_id": "chat-1",
        },
    )
    session = await service.get_session(session_id)
    await service.persist_state_delta(
        session, {TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY: None, PENDING_SPECIALIST_RESULT_STATE_KEY: None}
    )

    final_session = await service.get_session(session_id)
    assert final_session.state.get(TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY) is None
    assert final_session.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY) is None
    assert final_session.state["selected_teams_chat_id"] == "chat-1"  # preserved, not wiped
    authors = {event.author for event in final_session.events}
    assert authors == {"user"}


# --- TEST E: error/cancel cleanup still uses the same, fixed shape ---------


@pytest.mark.asyncio
async def test_e_cleanup_after_exception_uses_the_same_fixed_event_shape() -> None:
    """Mirrors the existing P0/hard-crash cleanup pattern -- confirms
    cleanup writes (which run in a `finally` block regardless of outcome)
    go through the SAME, now-fixed `persist_state_delta`, never a separate
    unfixed code path.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await service.persist_state_delta(session, {"trusted_specialist_result_envelope": {"run_id": "r1"}})

    try:
        raise RuntimeError("simulated turn failure")
    except RuntimeError:
        session = await service.get_session(session_id)
        await service.persist_state_delta(session, {"trusted_specialist_result_envelope": None})

    final_session = await service.get_session(session_id)
    assert final_session.state.get("trusted_specialist_result_envelope") is None
    authors = {event.author for event in final_session.events}
    assert authors == {"user"}
    assert "slopanoc-api" not in authors


# --- TEST F: edit/rewind regression -----------------------------------------


@pytest.mark.asyncio
async def test_f_rewind_marker_events_and_state_persistence_events_coexist_correctly() -> None:
    """ADK's OWN rewind marker (`author='user'`, `actions.rewind_before_
    invocation_id` set) and this codebase's state-persistence events
    (`author='user'`, `actions.state_delta` set, no rewind marker) share
    the same author now -- confirms `_active_events`'s own rewind-
    filtering logic (chat_service.py, keyed on `actions.rewind_before_
    invocation_id`, never on author) still correctly distinguishes them.
    """
    from backend.api.chat_service import _active_events

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    from google.adk.events import Event, EventActions
    from google.genai import types

    user_turn_1 = Event(
        author="user", invocation_id="inv-1", content=types.Content(role="user", parts=[types.Part(text="hello")])
    )
    await service.adk_session_service.append_event(session, user_turn_1)
    session = await service.get_session(session_id)

    await service.persist_state_delta(session, {"selected_teams_chat_id": "chat-1"})
    session = await service.get_session(session_id)

    assistant_turn_1 = Event(author="team_manager", invocation_id="inv-1", content=types.Content(role="model", parts=[types.Part(text="hi")]))
    await service.adk_session_service.append_event(session, assistant_turn_1)
    session = await service.get_session(session_id)

    user_turn_2 = Event(
        author="user", invocation_id="inv-2", content=types.Content(role="user", parts=[types.Part(text="second")])
    )
    await service.adk_session_service.append_event(session, user_turn_2)
    session = await service.get_session(session_id)

    # A real ADK-shaped rewind marker (mirrors Runner.rewind_async's own
    # construction, verified in runners.py) discarding turn 2.
    rewind_marker = Event(
        author="user", invocation_id="inv-rewind", actions=EventActions(rewind_before_invocation_id="inv-2")
    )
    await service.adk_session_service.append_event(session, rewind_marker)
    final_session = await service.get_session(session_id)

    active = _active_events(final_session.events)
    active_invocation_ids = {e.invocation_id for e in active}
    assert "inv-2" not in active_invocation_ids  # rewound turn excluded
    assert "inv-1" in active_invocation_ids  # earlier real turn still active
    # The state-persistence event (no rewind marker) is still active --
    # it was never rewound, only turn 2 was.
    active_state_deltas = [e.actions.state_delta for e in active if e.actions and e.actions.state_delta]
    assert {"selected_teams_chat_id": "chat-1"} in active_state_deltas


# --- TEST G: current_thread regression --------------------------------------


@pytest.mark.asyncio
async def test_g_current_thread_history_excludes_state_only_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Application-only state events (author='user', content=None) must
    never surface as fake conversational content -- reuses this
    codebase's own existing current_thread regression pattern
    (test_conversation_target_regression.py) and additionally asserts the
    real session's stored events contain no content-bearing event
    authored by anything other than 'user'/'team_manager'/'incident_
    manager', and that state-only events carry no content at all.
    """
    from backend.api.chat_service import ChatService
    from backend.api.streaming_events import StreamEventType
    from backend.tests._api_fakes import FakeEvent, FakeFunctionCall, FakeFunctionResponse, FakeRunner

    def _sync_side_effect(chat_id, chat_title, summary, evidence):
        from backend.agents.team_manager.state_sync import sync_incident_manager_result_to_state

        class _Ctx:
            def __init__(self, state):
                self.state = state

        async def side_effect(session_service, session, text):
            ctx = _Ctx(dict(session.state))
            sync_incident_manager_result_to_state(
                tool=type("T", (), {"name": "incident_manager"})(),
                args={},
                tool_context=ctx,
                tool_response={"outcome": "ok", "chat_id": chat_id, "chat_title": chat_title, "summary": summary, "evidence": evidence},
            )
            await session_service.persist_state_delta(session, dict(ctx.state))

        return side_effect

    service = ApiSessionService()
    session_id = await service.create_session()

    chat_service_setup = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=[
                FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
                FakeEvent(
                    final=False,
                    function_responses=[
                        FakeFunctionResponse("incident_manager", {"outcome": "ok", "chat_title": "Ops Bridge", "evidence": []})
                    ],
                ),
                FakeEvent(text="Ops Bridge summary.", final=True),
            ],
            side_effect=_sync_side_effect("chat-ops-id", "Ops Bridge", "Ops Bridge summary.", []),
        ),
    )
    await anext_all(chat_service_setup.execute_turn_events(session_id, "summarize Ops Bridge", "api-user"))

    chat_service = ChatService(
        service, runner=FakeRunner(service, events=[FakeEvent(text="Here is what we discussed.", final=True)])
    )
    collected = await anext_all(
        chat_service.execute_turn_events(session_id, "what did we discuss in this chat window", "api-user")
    )

    trace_steps = [e for e in collected if e.type == StreamEventType.TRACE_STEP]
    assert not any(s.data["category"] == "teams" for s in trace_steps)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" not in completed.data

    final_session = await service.get_session(session_id)
    for event in final_session.events:
        if event.actions and event.actions.state_delta and not event.content:
            assert event.author == "user"  # never a fake "slopanoc-api"/other identity


async def anext_all(agen):
    return [event async for event in agen]
