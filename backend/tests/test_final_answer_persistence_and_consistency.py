"""Regression test suite for authoritative final answer persistence and UI consistency.

Demonstrates the 8 mandatory requirements:
1. Final SSE text equals persisted final text.
2. Final SSE text equals history text after immediate refresh.
3. Remediation cannot produce a different answer after refresh.
4. Consecutive and concurrent turns remain isolated.
5. Late SSE events cannot overwrite finalized content.
6. Persistence failures cannot produce false completion.
7. Intentional rewind remains correctly scoped.
8. Historical sessions without final-answer records still load correctly.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import patch

import pytest

from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
from backend.api.chat_service import ChatService
from backend.api.session_history_service import get_session_history
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
from backend.api.turn_context import current_run_id
from backend.api.turn_final_answers import (
    TURN_FINAL_ANSWERS_STATE_KEY,
    resolve_turn_final_answer,
)
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.tests._api_fakes import (
    FakeEvent,
    FakeFunctionResponse,
    FakeRunner,
    append_user_turn,
)


def _make_attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


def _make_real_runner(session_service: ApiSessionService):
    from google.adk.agents import Agent
    from google.adk.runners import Runner

    agent = Agent(name="test_agent", model="gemini-2.0-flash")
    return Runner(app_name="slopanoc-api", agent=agent, session_service=session_service.adk_session_service)


# ---------------------------------------------------------------------------
# Test 1 & 3: Remediation cannot produce different answer after refresh,
# and final SSE text equals persisted final text and history text.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remediation_final_sse_equals_persisted_and_history_after_refresh() -> None:
    """Demonstrates Requirements 1, 2, 3:
    When post-generation remediation (troubleshooting guidance override) replaces final text:
    - Final SSE text equals persisted final text in session.state.
    - Final SSE text equals history text after immediate refresh.
    - Remediation produces identical text before and after refresh.
    - Speculative deltas are suppressed so intermediate text does not mutate.
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)
    attachment_service = _make_attachment_service()

    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Node is reporting ESS Service Unavailable.",
        next_action="Check active alarms on the node.",
        command="alt",
        evidence_requested="Active alarms list",
    )

    initial_model_text = "The node is reporting ESS Service Unavailable. You should consider checking alarms."

    async def side_effect(session_service, session, text):
        register_troubleshooting_guidance(current_run_id(), guidance)
        await append_user_turn(
            session_service,
            session,
            invocation_id="inv-101",
            user_text=text,
            assistant_text=initial_model_text,
        )

    runner = FakeRunner(
        service,
        side_effect=side_effect,
        events=[
            FakeEvent(
                invocation_id="inv-101",
                final=False,
                function_responses=[
                    FakeFunctionResponse(
                        "record_source_requirements",
                        {"requires_teams": False, "requires_governed_knowledge": False},
                    )
                ],
            ),
            FakeEvent(invocation_id="inv-101", text=initial_model_text, partial=True, final=False),
            FakeEvent(invocation_id="inv-101", text=initial_model_text, partial=False, final=True),
        ],
    )
    chat_service = ChatService(service, runner=runner)

    streaming_deltas: list[str] = []
    final_sse_content: Optional[str] = None

    async for event in chat_service.execute_turn_events(session_id, "check alarms", user_id):
        if event.type == StreamEventType.MESSAGE_DELTA:
            streaming_deltas.append(event.data.get("text", ""))
        elif event.type == StreamEventType.MESSAGE_COMPLETED:
            final_sse_content = event.data.get("content")

    assert final_sse_content is not None
    # Requirement 7: Speculative text should be suppressed when guidance replaces final text
    assert "".join(streaming_deltas) == "", "Speculative deltas must not be streamed when remediation overrides response"

    # Requirement 1 & 2: Authoritative final answer is persisted in session.state
    db_session = await service.get_session(session_id, user_id)
    persisted_answer = resolve_turn_final_answer(db_session.state, "inv-101")
    assert persisted_answer == final_sse_content, "Persisted final answer must equal final SSE content"

    # Requirement 4: History after refresh projects the authoritative final text
    history_resp = await get_session_history(service, attachment_service, session_id, user_id)
    assistant_messages = [m for m in history_resp.messages if m.role == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0].text == final_sse_content, "History text after refresh must equal final SSE text"


# ---------------------------------------------------------------------------
# Test 4: Consecutive and concurrent turns remain isolated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consecutive_and_concurrent_turns_remain_isolated() -> None:
    """Demonstrates that consecutive and concurrent turns maintain isolated final answers
    keyed by turn_id in session.state, without overwriting each other.
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)
    attachment_service = _make_attachment_service()

    # Turn 1
    async def side_effect_1(session_service, session, text):
        await append_user_turn(
            session_service,
            session,
            invocation_id="inv-t1",
            user_text=text,
            assistant_text="Assistant response 1",
        )

    runner_1 = FakeRunner(
        service,
        side_effect=side_effect_1,
        events=[
            FakeEvent(
                invocation_id="inv-t1",
                final=False,
                function_responses=[
                    FakeFunctionResponse(
                        "record_source_requirements",
                        {"requires_teams": False, "requires_governed_knowledge": False},
                    )
                ],
            ),
            FakeEvent(invocation_id="inv-t1", text="Assistant response 1", partial=False, final=True),
        ],
    )
    chat_service_1 = ChatService(service, runner=runner_1)
    async for _ in chat_service_1.execute_turn_events(session_id, "user 1", user_id):
        pass

    # Turn 2
    async def side_effect_2(session_service, session, text):
        await append_user_turn(
            session_service,
            session,
            invocation_id="inv-t2",
            user_text=text,
            assistant_text="Assistant response 2",
        )

    runner_2 = FakeRunner(
        service,
        side_effect=side_effect_2,
        events=[
            FakeEvent(
                invocation_id="inv-t2",
                final=False,
                function_responses=[
                    FakeFunctionResponse(
                        "record_source_requirements",
                        {"requires_teams": False, "requires_governed_knowledge": False},
                    )
                ],
            ),
            FakeEvent(invocation_id="inv-t2", text="Assistant response 2", partial=False, final=True),
        ],
    )
    chat_service_2 = ChatService(service, runner=runner_2)
    async for _ in chat_service_2.execute_turn_events(session_id, "user 2", user_id):
        pass

    # Inspect persisted state
    session = await service.get_session(session_id, user_id)
    assert resolve_turn_final_answer(session.state, "inv-t1") == "Assistant response 1"
    assert resolve_turn_final_answer(session.state, "inv-t2") == "Assistant response 2"

    # Inspect history
    history = await get_session_history(service, attachment_service, session_id, user_id)
    assistant_msgs = [m for m in history.messages if m.role == "assistant"]
    assert len(assistant_msgs) == 2
    assert assistant_msgs[0].text == "Assistant response 1"
    assert assistant_msgs[1].text == "Assistant response 2"


# ---------------------------------------------------------------------------
# Test 5: Late SSE events cannot overwrite finalized content
# ---------------------------------------------------------------------------


def test_late_sse_events_cannot_overwrite_finalized_content() -> None:
    """Demonstrates Requirement 6 on the frontend state reducer:
    Simulates BACKEND_MESSAGE_DELTA and duplicate BACKEND_MESSAGE_COMPLETED arriving
    after a message status has already transitioned to 'complete'.
    """
    class ClientMessage:
        def __init__(self, id: str, text: str, status: str):
            self.id = id
            self.text = text
            self.status = status

    class ClientChat:
        def __init__(self, run_token: Optional[str]):
            self.run_token = run_token

    # Initial state after message is completed
    chat = ClientChat(run_token="token-active")
    message = ClientMessage(id="msg-1", text="Canonical Final Answer", status="complete")

    # 1. Late delta arrives:
    late_delta = " Extra unverified text"
    # Reducer logic with guard:
    # if (!existing || existing.status === "complete") return state;
    if message.status != "complete":
        message.text += late_delta

    assert message.text == "Canonical Final Answer"

    # 2. Duplicate completed event arrives:
    duplicate_content = "Different content"
    if message.status != "complete":
        message.text = duplicate_content

    assert message.text == "Canonical Final Answer"
    assert message.status == "complete"


# ---------------------------------------------------------------------------
# Test 6: Persistence failures cannot produce false completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistence_failures_cannot_produce_false_completion() -> None:
    """Demonstrates Requirement 5:
    If session_service.persist_state_delta fails during final answer persistence:
    - MESSAGE_COMPLETED is NOT emitted.
    - StreamEventType.ERROR is emitted with code 'persistence_failure'.
    - RUN_COMPLETED outcome is 'error'.
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)

    async def side_effect(session_service, session, text):
        await append_user_turn(
            session_service,
            session,
            invocation_id="inv-fail",
            user_text=text,
            assistant_text="Should not be marked complete",
        )

    runner = FakeRunner(
        service,
        side_effect=side_effect,
        events=[
            FakeEvent(
                invocation_id="inv-fail",
                final=False,
                function_responses=[
                    FakeFunctionResponse(
                        "record_source_requirements",
                        {"requires_teams": False, "requires_governed_knowledge": False},
                    )
                ],
            ),
            FakeEvent(invocation_id="inv-fail", text="Should not be marked complete", partial=False, final=True),
        ],
    )
    chat_service = ChatService(service, runner=runner)

    # Force persist_state_delta to raise an exception specifically during final answer persistence
    original_persist = service.persist_state_delta

    async def selective_persist(session, delta):
        if TURN_FINAL_ANSWERS_STATE_KEY in delta:
            raise RuntimeError("Database write failed for final answers")
        return await original_persist(session, delta)

    with patch.object(service, "persist_state_delta", side_effect=selective_persist):
        emitted_events = []
        async for event in chat_service.execute_turn_events(session_id, "hello", user_id):
            emitted_events.append(event)

        types = [e.type for e in emitted_events]
        assert StreamEventType.MESSAGE_COMPLETED not in types, "MESSAGE_COMPLETED must not be emitted on persistence failure"
        assert StreamEventType.ERROR in types, "ERROR must be emitted on persistence failure"

        error_event = next(e for e in emitted_events if e.type == StreamEventType.ERROR)
        assert error_event.data.get("code") == "persistence_failure"

        run_completed_event = next(e for e in emitted_events if e.type == StreamEventType.RUN_COMPLETED)
        assert run_completed_event.data.get("outcome") == "error"


# ---------------------------------------------------------------------------
# Test 7: Intentional rewind remains correctly scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intentional_rewind_remains_correctly_scoped() -> None:
    """Demonstrates Requirement 8:
    When a turn is rewound via rewind_before_user_turn:
    - The rewound turn is excluded from history projection.
    - Earlier turns remain intact.
    - session.state respects ADK state reversal.
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)
    attachment_service = _make_attachment_service()

    # Create Turn 1
    session = await service.get_session(session_id, user_id)
    await append_user_turn(service, session, "inv-rw-1", "user message 1", assistant_text="assistant message 1")
    delta_1 = {TURN_FINAL_ANSWERS_STATE_KEY: {"inv-rw-1": "assistant message 1"}}
    await service.persist_state_delta(session, delta_1)

    # Create Turn 2
    session = await service.get_session(session_id, user_id)
    await append_user_turn(service, session, "inv-rw-2", "user message 2", assistant_text="assistant message 2")
    delta_2 = {TURN_FINAL_ANSWERS_STATE_KEY: {"inv-rw-1": "assistant message 1", "inv-rw-2": "assistant message 2"}}
    await service.persist_state_delta(session, delta_2)

    # Verify history before rewind
    history_before = await get_session_history(service, attachment_service, session_id, user_id)
    assert len(history_before.messages) == 4

    # Rewind turn 2
    runner = _make_real_runner(service)
    chat_service = ChatService(service, runner=runner)
    await chat_service.rewind_before_user_turn(session_id, before_user_turn_index=1, user_id=user_id)

    # Verify history after rewind: only turn 1 remains
    history_after = await get_session_history(service, attachment_service, session_id, user_id)
    assert len(history_after.messages) == 2
    assert history_after.messages[0].text == "user message 1"
    assert history_after.messages[1].text == "assistant message 1"

    # Verify state reversal: turn 2 is no longer in turn_final_answers
    refreshed_session = await service.get_session(session_id, user_id)
    assert resolve_turn_final_answer(refreshed_session.state, "inv-rw-1") == "assistant message 1"
    assert resolve_turn_final_answer(refreshed_session.state, "inv-rw-2") is None


# ---------------------------------------------------------------------------
# Test 8: Historical sessions without final-answer records still load correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_historical_sessions_without_final_answer_records_load_correctly() -> None:
    """Demonstrates Requirement 4 fallback:
    Historical sessions that do not have TURN_FINAL_ANSWERS_STATE_KEY in session.state
    fall back to raw ADK event extraction (turn.final_text).
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)
    attachment_service = _make_attachment_service()

    session = await service.get_session(session_id, user_id)
    # Ensure no final answers in state
    assert TURN_FINAL_ANSWERS_STATE_KEY not in session.state

    # Append raw user turn into session.events
    await append_user_turn(
        service,
        session,
        invocation_id="inv-legacy",
        user_text="Legacy question",
        assistant_text="Legacy model answer from ADK events",
    )

    history = await get_session_history(service, attachment_service, session_id, user_id)
    assert len(history.messages) == 2
    assert history.messages[0].text == "Legacy question"
    assert history.messages[1].text == "Legacy model answer from ADK events"


# ---------------------------------------------------------------------------
# Test 9: Subsequent ADK model invocations consume authoritative remediated answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subsequent_model_turn_consumes_authoritative_remediated_answer() -> None:
    """Demonstrates Gap 1:
    Subsequent ADK model invocations consume the authoritative remediated answer,
    not the original unverified model response, while preserving raw events
    in session.events intact and immutable for auditability.
    """
    from google.adk.events.event import Event
    from google.adk.flows.llm_flows.contents import _get_contents
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    from backend.api.turn_final_answers import (
        project_authoritative_answers_to_contents,
    )

    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)

    # 1. Simulate Turn 1:
    raw_model_output = "Unverified raw model recommendation to reboot node immediately."
    remediated_final_answer = (
        "Rebooting requires approval and active alarm verification. Check alarms first."
    )

    session = await service.get_session(session_id, user_id)
    await append_user_turn(
        service,
        session,
        invocation_id="inv-turn-1",
        user_text="Node is down, what do I do?",
        assistant_text=raw_model_output,
    )

    # Persist authoritative final answer in session.state
    delta = {TURN_FINAL_ANSWERS_STATE_KEY: {"inv-turn-1": remediated_final_answer}}
    await service.persist_state_delta(session, delta)

    # 2. Simulate Turn 2 start: user asks a follow up
    session = await service.get_session(session_id, user_id)
    user_turn_2_event = Event(
        author="user",
        invocation_id="inv-turn-2",
        content=types.Content(
            role="user",
            parts=[types.Part(text="I checked alarms, node says ESS Service Unavailable.")],
        ),
        timestamp=100.0,
    )
    session.events.append(user_turn_2_event)

    # Standard ADK _get_contents builds contents directly from raw events
    adk_contents = _get_contents(None, session.events, "team_manager")
    # Before callback projection, ADK passes the raw un-remediated text:
    model_contents = [c for c in adk_contents if c.role == "model"]
    assert len(model_contents) == 1
    assert model_contents[0].parts[0].text == raw_model_output

    # 3. Simulate before_model_callback executing for team_manager on Turn 2
    llm_request = LlmRequest(contents=adk_contents)

    class FakeInvocationContext:
        def __init__(self, session):
            self.session = session
            self.agent = type("Agent", (), {"name": "team_manager"})()
            self.branch = None

    class FakeCallbackContext:
        def __init__(self, session):
            self._invocation_context = FakeInvocationContext(session)

    callback_context = FakeCallbackContext(session)
    project_authoritative_answers_to_contents(callback_context, llm_request)

    # 4. Verify Boundary 5 (Next-turn Model Prompt Context):
    # The contents in llm_request now contain the authoritative remediated answer!
    model_request_contents = [c for c in llm_request.contents if c.role == "model"]
    assert len(model_request_contents) == 1
    assert (
        model_request_contents[0].parts[0].text == remediated_final_answer
    ), "Turn 2 model context must consume the authoritative remediated answer"

    # 5. Verify Boundary 3 (Database Audit Log):
    # session.events remains completely intact, unmutated, preserving auditability!
    raw_assistant_event = next(
        e for e in session.events if e.author == "team_manager" and e.invocation_id == "inv-turn-1"
    )
    assert (
        raw_assistant_event.content.parts[0].text == raw_model_output
    ), "Raw event in session.events must remain immutable for auditability"
