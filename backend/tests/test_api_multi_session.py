"""Tests for multiple independent sessions per user (instruction section
12) -- conversation history, selected chat, evidence, and pending
proposals must never leak between sessions, even for the SAME user.
"""
from __future__ import annotations

import pytest

from backend.api import approval_service
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY
from backend.gateway.safe_error import SafeErrorException
from backend.tests._api_fakes import FakeRunner, append_state_delta, simulate_proposal

ALICE = "alice"
BOB = "bob"


@pytest.mark.asyncio
async def test_one_user_can_create_multiple_independent_sessions() -> None:
    service = ApiSessionService()
    a1 = await service.create_session(ALICE)
    a2 = await service.create_session(ALICE)
    a3 = await service.create_session(ALICE)

    assert len({a1, a2, a3}) == 3


@pytest.mark.asyncio
async def test_sessions_have_independent_selected_teams_chat() -> None:
    service = ApiSessionService()
    a1 = await service.create_session(ALICE)
    a2 = await service.create_session(ALICE)

    session_a1 = await service.get_session(a1, ALICE)
    await append_state_delta(
        service,
        session_a1,
        {"selected_teams_chat_id": "c1", "selected_teams_chat_topic": "Ops Bridge"},
    )

    refreshed_a1 = await service.get_session(a1, ALICE)
    refreshed_a2 = await service.get_session(a2, ALICE)

    assert refreshed_a1.state["selected_teams_chat_id"] == "c1"
    assert refreshed_a1.state["selected_teams_chat_topic"] == "Ops Bridge"
    assert "selected_teams_chat_id" not in refreshed_a2.state
    assert "selected_teams_chat_topic" not in refreshed_a2.state


@pytest.mark.asyncio
async def test_sessions_have_independent_last_teams_evidence() -> None:
    service = ApiSessionService()
    a1 = await service.create_session(ALICE)
    a2 = await service.create_session(ALICE)

    session_a1 = await service.get_session(a1, ALICE)
    evidence = [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"}]
    await append_state_delta(service, session_a1, {"last_teams_evidence": evidence})

    refreshed_a1 = await service.get_session(a1, ALICE)
    refreshed_a2 = await service.get_session(a2, ALICE)

    assert refreshed_a1.state["last_teams_evidence"] == evidence
    assert "last_teams_evidence" not in refreshed_a2.state


@pytest.mark.asyncio
async def test_pending_proposal_in_one_session_does_not_appear_in_another() -> None:
    service = ApiSessionService()
    a1 = await service.create_session(ALICE)
    a2 = await service.create_session(ALICE)

    session_a1 = await service.get_session(a1, ALICE)
    await simulate_proposal(service, session_a1, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    refreshed_a1 = await service.get_session(a1, ALICE)
    refreshed_a2 = await service.get_session(a2, ALICE)

    assert PENDING_ACTION_PROPOSAL_STATE_KEY in refreshed_a1.state
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in refreshed_a2.state


@pytest.mark.asyncio
async def test_approval_state_in_one_session_cannot_authorize_another() -> None:
    service = ApiSessionService()
    a1 = await service.create_session(ALICE)
    a2 = await service.create_session(ALICE)

    session_a1 = await service.get_session(a1, ALICE)
    await simulate_proposal(service, session_a1, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    refreshed_a1 = await service.get_session(a1, ALICE)
    proposal_id = refreshed_a1.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

    await approval_service.approve(service, a1, proposal_id, ALICE)

    # The exact same proposal id does not exist in a2 at all -- approving
    # it there must fail.
    with pytest.raises(SafeErrorException) as exc_info:
        await approval_service.approve(service, a2, proposal_id, ALICE)
    assert exc_info.value.safe_error.error_code == "action_failure"


@pytest.mark.asyncio
async def test_a1_and_a2_have_independent_conversation_history() -> None:
    service = ApiSessionService()
    a1 = await service.create_session(ALICE)
    a2 = await service.create_session(ALICE)
    chat_service = ChatService(service, runner=FakeRunner(service))

    await chat_service.run_turn(a1, "message only in a1", ALICE)

    session_a1 = await service.get_session(a1, ALICE)
    session_a2 = await service.get_session(a2, ALICE)

    assert len(session_a1.events) >= 1
    assert len(session_a2.events) == 0


@pytest.mark.asyncio
async def test_user_b_sessions_remain_completely_separate_from_user_a() -> None:
    service = ApiSessionService()
    a1 = await service.create_session(ALICE)
    b1 = await service.create_session(BOB)

    session_a1 = await service.get_session(a1, ALICE)
    await simulate_proposal(service, session_a1, "teams.sendMessage", {"chatId": "c1", "message": "Alice's"})

    refreshed_b1 = await service.get_session(b1, BOB)
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in refreshed_b1.state

    # And Bob cannot even look up Alice's session id directly.
    with pytest.raises(SafeErrorException):
        await service.get_session(a1, BOB)
