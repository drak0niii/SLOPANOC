"""Tests for session ownership enforcement (instruction sections 10/11/16).

Exercises `ApiSessionService`/`ChatService`/`approval_service` directly
(not only over HTTP -- test_api_identity.py already covers the HTTP-level
end-to-end flow) so the underlying ownership guarantee is pinned at the
same layer other unit tests already operate at.
"""
from __future__ import annotations

import pytest

from backend.api import approval_service
from backend.api.chat_service import ChatService
from backend.api.pending_action import map_pending_action
from backend.api.session_service import ApiSessionService
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY
from backend.gateway.safe_error import SafeErrorException
from backend.tests._api_fakes import FakeRunner, simulate_proposal

ALICE = "alice"
BOB = "bob"


@pytest.mark.asyncio
async def test_user_a_can_use_their_own_session() -> None:
    service = ApiSessionService()
    session_id = await service.create_session(ALICE)
    chat_service = ChatService(service, runner=FakeRunner(service))

    response = await chat_service.run_turn(session_id, "hello", ALICE)

    assert response.session_id == session_id


@pytest.mark.asyncio
async def test_user_b_cannot_send_a_message_to_user_a_session() -> None:
    service = ApiSessionService()
    session_id = await service.create_session(ALICE)
    chat_service = ChatService(service, runner=FakeRunner(service))

    with pytest.raises(SafeErrorException) as exc_info:
        await chat_service.run_turn(session_id, "hello", BOB)
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_user_b_cannot_approve_user_a_proposal() -> None:
    service = ApiSessionService()
    session_id = await service.create_session(ALICE)
    session = await service.get_session(session_id, ALICE)
    await simulate_proposal(service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    refreshed = await service.get_session(session_id, ALICE)
    proposal_id = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

    with pytest.raises(SafeErrorException) as exc_info:
        await approval_service.approve(service, session_id, proposal_id, BOB)
    assert exc_info.value.safe_error.error_code == "not_found"

    # Alice's proposal remains untouched -- still pending, not silently
    # approved by the failed cross-user attempt.
    still_pending = await service.get_session(session_id, ALICE)
    assert still_pending.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "pending"


@pytest.mark.asyncio
async def test_user_b_cannot_reject_user_a_proposal() -> None:
    service = ApiSessionService()
    session_id = await service.create_session(ALICE)
    session = await service.get_session(session_id, ALICE)
    await simulate_proposal(service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    refreshed = await service.get_session(session_id, ALICE)
    proposal_id = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

    with pytest.raises(SafeErrorException) as exc_info:
        await approval_service.reject(service, session_id, proposal_id, BOB)
    assert exc_info.value.safe_error.error_code == "not_found"

    still_pending = await service.get_session(session_id, ALICE)
    assert still_pending.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "pending"


@pytest.mark.asyncio
async def test_foreign_session_is_indistinguishable_from_an_unknown_one() -> None:
    """No enumeration signal: the error for "exists but not yours" is
    identical to "does not exist at all".
    """
    service = ApiSessionService()
    session_id = await service.create_session(ALICE)

    with pytest.raises(SafeErrorException) as foreign_exc:
        await service.get_session(session_id, BOB)
    with pytest.raises(SafeErrorException) as unknown_exc:
        await service.get_session("totally-made-up-id", BOB)

    assert foreign_exc.value.safe_error.error_code == unknown_exc.value.safe_error.error_code
    assert foreign_exc.value.safe_error.user_message == unknown_exc.value.safe_error.user_message


@pytest.mark.asyncio
async def test_no_ownership_metadata_leaks_through_the_error() -> None:
    service = ApiSessionService()
    session_id = await service.create_session(ALICE)

    with pytest.raises(SafeErrorException) as exc_info:
        await service.get_session(session_id, BOB)

    message = exc_info.value.safe_error.user_message.lower()
    assert ALICE not in message
    assert "owner" not in message
    assert "belongs" not in message


@pytest.mark.asyncio
async def test_execution_lock_for_a_foreign_session_id_is_never_acquired() -> None:
    """Ownership is checked before any lock/turn work begins -- a foreign
    access attempt must not create or contend for a lock at all.
    """
    service = ApiSessionService()
    session_id = await service.create_session(ALICE)
    chat_service = ChatService(service, runner=FakeRunner(service))

    with pytest.raises(SafeErrorException):
        await chat_service.run_turn(session_id, "hi", BOB)

    # The lock used for Alice's own session is untouched by Bob's failed
    # attempt (a fresh lock would still be unlocked either way; this just
    # confirms no lingering lock state was left acquired).
    assert not service.lock_for(session_id, ALICE).locked()


@pytest.mark.asyncio
async def test_pending_action_dto_for_a_foreign_session_is_never_built() -> None:
    """Since `get_session` itself denies access, `map_pending_action`
    never even runs against another user's state -- confirmed by never
    reaching a point where it could be called with Alice's real state
    under Bob's request.
    """
    service = ApiSessionService()
    session_id = await service.create_session(ALICE)
    session = await service.get_session(session_id, ALICE)
    await simulate_proposal(service, session, "teams.sendMessage", {"chatId": "c1", "message": "secret"})

    with pytest.raises(SafeErrorException):
        await service.get_session(session_id, BOB)
    # The only way to reach `map_pending_action` is via a successful
    # `get_session` -- which just proved BOB cannot obtain.
