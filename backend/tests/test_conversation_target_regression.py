"""Regression test for the live semantic-scope bug (pre-4H):

  1. User asks to summarize Teams Chat A -> summarized.
  2. User asks to summarize Teams Chat B -> summarized (now selected).
  3. User asks "now give me a summary of what was discussed in this chat
     window" -> was WRONGLY re-summarizing Chat B; must now summarize the
     SLOPANOC conversation itself.

Since no live model is available in this offline suite (the same
constraint every other prompt-contract/routing test in this repo already
works under -- see test_followup_routing_contract.py's own docstring),
these tests prove the DETERMINISTIC half: when team_manager correctly
follows the new instruction (declares `current_thread` via
`record_conversation_target` and answers directly, never calling
`incident_manager`), the surrounding architecture behaves correctly --
no Teams tool call, no Source reference, `selected_teams_chat_id` present
but not controlling, and the observable target is exactly
`current_thread`. The semantic judgment itself is covered by
test_conversation_target_prompt_contract.py's pinned instruction text.

Uses only synthetic chat names/content -- never real Teams data.
"""
from __future__ import annotations

import logging

import pytest

from backend.agents.team_manager.state_sync import sync_incident_manager_result_to_state
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.tests._api_fakes import FakeEvent, FakeFunctionCall, FakeFunctionResponse, FakeRunner


async def _collect(chat_service: ChatService, session_id: str, message: str, user_id: str = "api-user") -> list:
    return [event async for event in chat_service.execute_turn_events(session_id, message, user_id)]


class _SyncTool:
    name = "incident_manager"


class _SyncCtx:
    def __init__(self, state: dict) -> None:
        self.state = state


def _sync_side_effect(chat_id: str, chat_title: str, summary: str, evidence: list):
    """Mirrors test_selected_chat_state_continuity.py's own established
    pattern: `FakeRunner` never runs real ADK tool-calling machinery, so
    `after_tool_callback=sync_incident_manager_result_to_state` (which
    only fires for a REAL incident_manager call) must be simulated
    explicitly to make `selected_teams_chat_*` state advance the same way
    a real turn would.
    """

    async def side_effect(session_service, session, text):
        ctx = _SyncCtx(dict(session.state))
        sync_incident_manager_result_to_state(
            tool=_SyncTool(),
            args={},
            tool_context=ctx,
            tool_response={
                "outcome": "ok",
                "chat_id": chat_id,
                "chat_title": chat_title,
                "summary": summary,
                "evidence": evidence,
            },
        )
        await session_service.persist_state_delta(session, dict(ctx.state))

    return side_effect


def _external_conversation_events(target: str, chat_title: str, evidence: list, final_text: str) -> list:
    return [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("record_conversation_target", {"target": target})]),
        FakeEvent(
            final=False,
            function_responses=[FakeFunctionResponse("record_conversation_target", {"target": target})],
        ),
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager", {"chat_topic": chat_title})]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "ok", "chat_title": chat_title, "evidence": evidence},
                )
            ],
        ),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("record_source_requirements", {"requires_teams": True, "requires_governed_knowledge": False})
            ],
        ),
        FakeEvent(text=final_text, final=True),
    ]


def _current_thread_events(final_text: str) -> list:
    """The CORRECT behavior for a current_thread request: `record_
    conversation_target` is still never called (P4A: that call has "no
    other side effect and does not by itself change what you do next" per
    conversation_target_capture.py's own docstring, so the instruction
    tells team_manager to skip it entirely for this target). FIFTH
    correction pass: `record_source_requirements` is NOT exempted the same
    way -- it is mandatory for every substantive turn, including a
    current_thread/history-recall one (both false here, since nothing
    about this request needs current Teams content or governed
    knowledge) -- so this fixture now includes exactly that one
    declaration, still zero OTHER tool calls.
    """
    return [
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False})
            ],
        ),
        FakeEvent(text=final_text, final=True),
    ]


def _parallel_external_conversation_events(target: str, chat_title: str, evidence: list, final_text: str) -> list:
    """P4A: the round-trip-eliminated shape for selected_external_
    conversation/explicit_external_conversation -- `record_conversation_
    target` and the `incident_manager` delegation are issued TOGETHER, in
    the SAME model response (ADK's own documented parallel-function-call
    mechanism, `handle_function_calls_async`/`merge_parallel_function_
    response_events` -- see prompts.py's own P4A docstring section), so
    both function_calls/function_responses appear on ONE event pair
    instead of two, saving the sequential round trip `_external_
    conversation_events` above still exercises (a model is still free to
    call them sequentially -- that shape remains valid too, unchanged).
    """
    return [
        FakeEvent(
            final=False,
            function_calls=[
                FakeFunctionCall("record_conversation_target", {"target": target}),
                FakeFunctionCall("incident_manager", {"chat_topic": chat_title}),
            ],
        ),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("record_conversation_target", {"target": target}),
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "ok", "chat_title": chat_title, "evidence": evidence},
                ),
            ],
        ),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("record_source_requirements", {"requires_teams": True, "requires_governed_knowledge": False})
            ],
        ),
        FakeEvent(text=final_text, final=True),
    ]


# --- The exact regression scenario ------------------------------------------


@pytest.mark.asyncio
async def test_chat_a_then_chat_b_then_current_thread_summary(caplog: pytest.LogCaptureFixture) -> None:
    service = ApiSessionService()
    session_id = await service.create_session()

    # Turn 1: summarize Chat A.
    chat_service_1 = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=_external_conversation_events(
                "explicit_external_conversation",
                "Chat A",
                [{"message_id": "a1", "author": "Alex", "sent_at": "2026-08-20T09:00:00Z"}],
                "Chat A discussed a database migration and decided to proceed next sprint.",
            ),
            side_effect=_sync_side_effect(
                "chat-a-id",
                "Chat A",
                "Chat A discussed a database migration.",
                [{"message_id": "a1", "author": "Alex", "sent_at": "2026-08-20T09:00:00Z"}],
            ),
        ),
    )
    collected_1 = await _collect(chat_service_1, session_id, "summarize Teams Chat A")
    completed_1 = next(e for e in collected_1 if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "Chat A" in completed_1.data["content"]

    # Turn 2: summarize Chat B -- Chat B becomes the selected chat.
    chat_service_2 = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=_external_conversation_events(
                "explicit_external_conversation",
                "Chat B",
                [{"message_id": "b1", "author": "Priya", "sent_at": "2026-08-21T09:00:00Z"}],
                "Chat B discussed a Power Automate outage and identified a fix.",
            ),
            side_effect=_sync_side_effect(
                "chat-b-id",
                "Chat B",
                "Chat B discussed a Power Automate outage.",
                [{"message_id": "b1", "author": "Priya", "sent_at": "2026-08-21T09:00:00Z"}],
            ),
        ),
    )
    collected_2 = await _collect(chat_service_2, session_id, "summarize Teams Chat B")
    completed_2 = next(e for e in collected_2 if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "Chat B" in completed_2.data["content"]

    refreshed = await service.get_session(session_id, "api-user")
    assert refreshed.state["selected_teams_chat_id"] == "chat-b-id"
    assert refreshed.state["selected_teams_chat_topic"] == "Chat B"

    # Turn 3: "now give me a summary of what was discussed in this chat
    # window" -- the CORRECT behavior: current_thread, no Teams call.
    final_summary = (
        "In this conversation, you asked me to summarize two Teams conversations: "
        "Chat A, which discussed a database migration and decided to proceed next sprint, "
        "and Chat B, which discussed a Power Automate outage and identified a fix."
    )
    chat_service_3 = ChatService(
        service, runner=FakeRunner(service, events=_current_thread_events(final_summary))
    )

    with caplog.at_level(logging.INFO, logger="backend.api.chat_service"):
        collected_3 = await _collect(
            chat_service_3, session_id, "now give me a summary of what was discussed in this chat window"
        )

    # 1. P4A: current_thread no longer calls `record_conversation_target`
    #    at all (see `_current_thread_events`'s own docstring), so there is
    #    no captured target to log this turn -- the diagnostic line is
    #    simply absent, never a stale/incorrect value.
    assert not any("conversation_target=" in r.getMessage() for r in caplog.records)

    # 2. No Teams tool call happened this turn -- `RunTraceTranslator`
    #    only ever emits a `TraceCategory.TEAMS` ("teams") step when an
    #    `incident_manager` function call/response is actually observed
    #    (see activity_translator.py) -- its absence is a direct,
    #    structural proof no Teams tool was invoked this turn.
    trace_steps_3 = [e for e in collected_3 if e.type == StreamEventType.TRACE_STEP]
    assert not any(s.data["category"] == "teams" for s in trace_steps_3)

    # 3. selected_teams_chat_id may still point to Chat B, but did not
    #    control this request -- unchanged, but irrelevant to the answer.
    refreshed_after = await service.get_session(session_id, "api-user")
    assert refreshed_after.state["selected_teams_chat_id"] == "chat-b-id"

    # 4. The final response summarizes the SLOPANOC interaction covering
    #    BOTH chats -- not simply Chat B again.
    completed_3 = next(e for e in collected_3 if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "Chat A" in completed_3.data["content"]
    assert "Chat B" in completed_3.data["content"]

    # 5. No Teams SourceReference is emitted for this response.
    assert "source" not in completed_3.data


# --- Semantic variation: multiple phrasings, same architecture proof -------
#
# The routing decision lives entirely in team_manager's own reasoning
# (declared via `record_conversation_target`), never in code that inspects
# the user's wording -- these tests use several different, semantically-
# equivalent phrasings for the SAME scenario to prove the architecture
# treats them identically (it never branches on the literal text at all;
# see test_conversation_target_prompt_contract.py's structural proof).


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrasing",
    [
        "summarize what we've discussed here",
        "give me a recap of our conversation",
        "what happened in this chat so far?",
        "what did we talk about so far?",
    ],
)
async def test_current_thread_variations_all_skip_teams_and_carry_no_source(phrasing: str) -> None:
    service = ApiSessionService()
    session_id = await service.create_session()

    # A Teams chat is already selected from an earlier turn -- present as
    # context, must not bias this request.
    chat_service_setup = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=_external_conversation_events(
                "explicit_external_conversation", "Ops Bridge", [], "Ops Bridge summary."
            ),
            side_effect=_sync_side_effect("chat-ops-id", "Ops Bridge", "Ops Bridge summary.", []),
        ),
    )
    await _collect(chat_service_setup, session_id, "summarize Ops Bridge")

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, events=_current_thread_events("Here is what we discussed in this session.")),
    )
    collected = await _collect(chat_service, session_id, phrasing)

    trace_steps = [e for e in collected if e.type == StreamEventType.TRACE_STEP]
    assert not any(s.data["category"] == "teams" for s in trace_steps)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" not in completed.data

    # The previously selected chat is untouched by this current_thread turn.
    refreshed = await service.get_session(session_id, "api-user")
    assert refreshed.state["selected_teams_chat_id"] == "chat-ops-id"


# --- External targets: still resolve correctly, unchanged behavior ---------


@pytest.mark.asyncio
async def test_explicit_named_teams_chat_resolves_to_explicit_external_conversation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=_external_conversation_events(
                "explicit_external_conversation",
                "Knowledge Management Daily Sync up",
                [{"message_id": "k1", "author": "Sam", "sent_at": "2026-08-22T09:00:00Z"}],
                "Here is the Knowledge Management Daily Sync up summary.",
            ),
        ),
    )

    with caplog.at_level(logging.INFO, logger="backend.api.chat_service"):
        collected = await _collect(chat_service, session_id, "Summarize Knowledge Management Daily Sync up")

    assert any("conversation_target=explicit_external_conversation" in r.getMessage() for r in caplog.records)
    trace_steps = [e for e in collected if e.type == StreamEventType.TRACE_STEP]
    assert any(s.data["category"] == "teams" for s in trace_steps)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" in completed.data


@pytest.mark.asyncio
async def test_contextual_reference_to_selected_chat_resolves_to_selected_external_conversation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = ApiSessionService()
    session_id = await service.create_session()

    setup = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=_external_conversation_events(
                "explicit_external_conversation", "Ops Bridge", [], "Ops Bridge summary."
            ),
            side_effect=_sync_side_effect("chat-ops-id", "Ops Bridge", "Ops Bridge summary.", []),
        ),
    )
    await _collect(setup, session_id, "summarize Ops Bridge")

    chat_service = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=_external_conversation_events(
                "selected_external_conversation",
                "Ops Bridge",
                [{"message_id": "o2", "author": "Alex", "sent_at": "2026-08-23T09:00:00Z"}],
                "Here is the Teams chat we just looked at, summarized again.",
            ),
        ),
    )

    with caplog.at_level(logging.INFO, logger="backend.api.chat_service"):
        collected = await _collect(chat_service, session_id, "Summarize the Teams chat we just looked at")

    assert any("conversation_target=selected_external_conversation" in r.getMessage() for r in caplog.records)
    trace_steps = [e for e in collected if e.type == StreamEventType.TRACE_STEP]
    assert any(s.data["category"] == "teams" for s in trace_steps)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" in completed.data


# --- P4A: round-trip-eliminated shape -- parallel record_conversation_target
# + incident_manager function calls in a single model response ------------


@pytest.mark.asyncio
async def test_parallel_conversation_target_and_delegation_calls_resolve_identically(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The P4A-preferred shape: `record_conversation_target` and
    `incident_manager` arrive as TWO function calls on the SAME event (ADK's
    own parallel-function-call mechanism -- see prompts.py's own P4A
    docstring section), rather than as two separate sequential round trips
    (`_external_conversation_events` above still covers that still-valid
    shape). The surrounding deterministic architecture -- state sync,
    conversation-target capture/logging, Source reference -- must behave
    identically either way, since none of it depends on how many model
    round trips produced these events.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=_parallel_external_conversation_events(
                "explicit_external_conversation",
                "Production Bridge",
                [{"message_id": "p1", "author": "Jo", "sent_at": "2026-08-24T09:00:00Z"}],
                "Here is the Production Bridge summary.",
            ),
            side_effect=_sync_side_effect(
                "chat-prod-id", "Production Bridge", "Production Bridge summary.", []
            ),
        ),
    )

    with caplog.at_level(logging.INFO, logger="backend.api.chat_service"):
        collected = await _collect(chat_service, session_id, "summarize Production Bridge")

    assert any("conversation_target=explicit_external_conversation" in r.getMessage() for r in caplog.records)
    trace_steps = [e for e in collected if e.type == StreamEventType.TRACE_STEP]
    assert any(s.data["category"] == "teams" for s in trace_steps)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" in completed.data
    refreshed = await service.get_session(session_id, "api-user")
    assert refreshed.state["selected_teams_chat_id"] == "chat-prod-id"
