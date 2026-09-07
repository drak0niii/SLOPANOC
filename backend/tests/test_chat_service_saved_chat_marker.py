"""Tests for POST-5.1 B4B's first-visible-turn marker write, wired into
`chat_service.py`'s `_run_turn_events` at the exact point it observes the
first event yielded by `Runner.run_async` (see `session_state_keys.py`'s
`record_user_turn_activity` docstring for the full B4A-correction-pass,
source-verified happens-before proof this depends on). Per-turn
`chat_activity_at` behavior is covered separately in
`test_session_activity_ordering.py`.

Uses the same `FakeRunner`/`NoFinalTextRunner`/`RaisingRunner` doubles
`test_api_chat_service.py` already established -- these test THIS
backend's own orchestration (did the marker get written at the right
point, for the right reason), never a reimplementation of ADK's own
Runner internals.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.session_state_keys import CHAT_TITLE_STATE_KEY, HAS_VISIBLE_MESSAGE_STATE_KEY
from backend.gateway.safe_error import SafeErrorException
from backend.tests._api_fakes import FakeRunner, NoFinalTextRunner, RaisingRunner, simulate_proposal


@pytest.mark.asyncio
async def test_successful_turn_marks_the_session_visible_with_a_derived_title() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    await chat_service.run_turn(session_id, "what's the incident status?")

    session = await service.get_session(session_id)
    assert session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True
    assert session.state.get(CHAT_TITLE_STATE_KEY) == "what's the incident status?"


@pytest.mark.asyncio
async def test_assistant_failure_after_a_first_event_still_marks_the_session_visible() -> None:
    """Required semantic (B4A correction pass): a real user message must
    never become invisible merely because the model's own turn failed to
    produce a final response. `NoFinalTextRunner` yields exactly one
    (non-final) event and no more -- `chat_service.py` itself then raises
    `run_failure` (no final text was ever produced), but the marker must
    already be written by the time that happens.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=NoFinalTextRunner())

    with pytest.raises(SafeErrorException) as exc_info:
        await chat_service.run_turn(session_id, "diagnose the fault")
    assert exc_info.value.safe_error.error_code == "run_failure"

    session = await service.get_session(session_id)
    assert session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True
    assert session.state.get(CHAT_TITLE_STATE_KEY) == "diagnose the fault"


@pytest.mark.asyncio
async def test_a_runner_that_raises_before_yielding_anything_leaves_the_session_unmarked() -> None:
    """The inverse case: if `run_async` never yields even one event (a
    real ADK Runner only does this if the user-content append itself
    never completed -- see the B4A correction pass's own happens-before
    proof), the marker must correctly stay unwritten -- never optimistically
    marked visible on the mere ATTEMPT of a turn.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError("boom")))

    with pytest.raises(SafeErrorException):
        await chat_service.run_turn(session_id, "this should never be marked visible")

    session = await service.get_session(session_id)
    assert session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is False
    assert session.state.get(CHAT_TITLE_STATE_KEY) is None


@pytest.mark.asyncio
async def test_a_second_turn_never_overwrites_the_first_turns_title() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    await chat_service.run_turn(session_id, "first ever message")
    await chat_service.run_turn(session_id, "a much later, unrelated message")

    session = await service.get_session(session_id)
    assert session.state.get(CHAT_TITLE_STATE_KEY) == "first ever message"


@pytest.mark.asyncio
async def test_marker_write_does_not_disturb_unrelated_session_state() -> None:
    """The marker write is one extra `persist_state_delta` call inside an
    already-real, already-tested orchestration path -- proves it composes
    cleanly with pre-existing state (a pending proposal, here) rather than
    clobbering the whole state dict.
    """
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service: Any, session: Any, text: str) -> None:
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    response = await chat_service.run_turn(session_id, "propose it")
    assert response.pending_action is not None

    session = await service.get_session(session_id)
    assert session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True
    assert session.state.get(CHAT_TITLE_STATE_KEY) == "propose it"
