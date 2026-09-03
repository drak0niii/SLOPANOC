"""Tests for backend/api/chat_service.py -- the one chat-execution path.
Uses `FakeRunner`/`simulate_proposal` (backend/tests/_api_fakes.py)
instead of a real Gemini call, so these are fully deterministic/offline,
per this task's test strategy.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.gateway.safe_error import SafeErrorException
from backend.tests._api_fakes import FakeEvent, FakeRunner, NoFinalTextRunner, RaisingRunner, simulate_proposal


@pytest.mark.asyncio
async def test_message_reaches_the_runner_and_response_is_returned() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    response = await chat_service.run_turn(session_id, "hello")

    assert response.session_id == session_id
    assert response.message.role == "assistant"
    assert response.message.content == "echo: hello"


@pytest.mark.asyncio
async def test_unknown_session_is_rejected_before_any_runner_call() -> None:
    service = ApiSessionService()
    calls: list[str] = []

    class TrackingRunner:
        async def run_async(self, *, user_id, session_id, new_message, run_config=None):
            calls.append(session_id)
            yield FakeEvent(text="should never run")

    chat_service = ChatService(service, runner=TrackingRunner())

    with pytest.raises(SafeErrorException) as exc_info:
        await chat_service.run_turn("does-not-exist", "hello")

    assert exc_info.value.safe_error.error_code == "not_found"
    assert calls == []


@pytest.mark.asyncio
async def test_same_session_is_reused_across_turns() -> None:
    """Session state set up by one turn (via a real state-delta append,
    the same mechanism a live tool call uses) must be visible to the next
    turn against the same session id.
    """
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))

    first = await chat_service.run_turn(session_id, "propose it")
    assert first.pending_action is not None

    # A second turn against the SAME session, with a plain runner this
    # time, should still see the proposal created by the first turn.
    chat_service_plain = ChatService(service, runner=FakeRunner(service))
    second = await chat_service_plain.run_turn(session_id, "what's pending?")
    assert second.pending_action is not None
    assert second.pending_action.proposal_id == first.pending_action.proposal_id


@pytest.mark.asyncio
async def test_separate_sessions_maintain_separate_state() -> None:
    service = ApiSessionService()
    session_a = await service.create_session()
    session_b = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    await chat_service.run_turn(session_a, "propose it")

    plain_chat_service = ChatService(service, runner=FakeRunner(service))
    response_b = await plain_chat_service.run_turn(session_b, "anything pending?")

    assert response_b.pending_action is None


@pytest.mark.asyncio
async def test_no_pending_action_when_none_exists() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    response = await chat_service.run_turn(session_id, "just chatting")

    assert response.pending_action is None


@pytest.mark.asyncio
async def test_only_final_response_text_becomes_the_public_message() -> None:
    """A turn's event stream may include intermediate/tool-call-shaped
    events -- only the actual final-response text (per
    `Event.is_final_response()`) may become `message.content`.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text=None, final=False, function_calls=[object()]),
        FakeEvent(text=None, final=False, function_responses=[object()]),
        FakeEvent(text="the real final answer", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    response = await chat_service.run_turn(session_id, "hello")

    assert response.message.content == "the real final answer"


@pytest.mark.asyncio
async def test_raw_function_call_objects_never_leak_into_the_response() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text=None, final=False, function_calls=[{"name": "incident_manager", "args": {"secret": "x"}}]),
        FakeEvent(text="all good", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    response = await chat_service.run_turn(session_id, "hello")

    dumped = response.model_dump_json()
    assert "incident_manager" not in dumped
    assert "secret" not in dumped


@pytest.mark.asyncio
async def test_runner_exception_becomes_a_safe_error_not_a_raw_exception() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError("raw internal detail, gateway url, etc")))

    with pytest.raises(SafeErrorException) as exc_info:
        await chat_service.run_turn(session_id, "hello")

    safe_error = exc_info.value.safe_error
    assert safe_error.error_code == "run_failure"
    assert "raw internal detail" not in safe_error.user_message
    assert "gateway url" not in safe_error.user_message


@pytest.mark.asyncio
async def test_no_final_text_produced_becomes_a_safe_error() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=NoFinalTextRunner())

    with pytest.raises(SafeErrorException) as exc_info:
        await chat_service.run_turn(session_id, "hello")

    assert exc_info.value.safe_error.error_code == "run_failure"


# --- Concurrency ---------------------------------------------------------


@pytest.mark.asyncio
async def test_same_session_execution_is_serialized() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    order: list[str] = []

    async def slow_side_effect(session_service, session, text):
        order.append(f"{text}-start")
        await asyncio.sleep(0.05)
        order.append(f"{text}-end")

    async def fast_side_effect(session_service, session, text):
        order.append(f"{text}-start")
        order.append(f"{text}-end")

    chat_service_1 = ChatService(service, runner=FakeRunner(service, side_effect=slow_side_effect))
    chat_service_2 = ChatService(service, runner=FakeRunner(service, side_effect=fast_side_effect))

    await asyncio.gather(
        chat_service_1.run_turn(session_id, "first"),
        chat_service_2.run_turn(session_id, "second"),
    )

    # "second" must never start until "first" has fully finished --
    # proves the same session's two turns were serialized, not
    # interleaved.
    assert order == ["first-start", "first-end", "second-start", "second-end"]


@pytest.mark.asyncio
async def test_different_sessions_execute_independently() -> None:
    service = ApiSessionService()
    session_a = await service.create_session()
    session_b = await service.create_session()
    order: list[str] = []

    async def slow_side_effect(session_service, session, text):
        order.append(f"{text}-start")
        await asyncio.sleep(0.05)
        order.append(f"{text}-end")

    async def fast_side_effect(session_service, session, text):
        order.append(f"{text}-start")
        order.append(f"{text}-end")

    chat_service_a = ChatService(service, runner=FakeRunner(service, side_effect=slow_side_effect))
    chat_service_b = ChatService(service, runner=FakeRunner(service, side_effect=fast_side_effect))

    await asyncio.gather(
        chat_service_a.run_turn(session_a, "first"),
        chat_service_b.run_turn(session_b, "second"),
    )

    # "second" (a different, unlocked session) finishes WHILE "first" is
    # still sleeping -- proves the two sessions did not block each other.
    assert order == ["first-start", "second-start", "second-end", "first-end"]
