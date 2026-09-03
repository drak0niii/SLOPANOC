"""Tests for backend/api/session_service.py -- ADK session lifecycle and
per-session concurrency locking. Uses a fresh `ApiSessionService()`
instance per test (not the process-wide singleton) so tests never share
state with each other.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY
from backend.gateway.safe_error import SafeErrorException
from backend.api.session_service import ApiSessionService, get_session_service


@pytest.mark.asyncio
async def test_create_session_returns_a_generated_id() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    assert isinstance(session_id, str)
    assert len(session_id) > 0


@pytest.mark.asyncio
async def test_created_session_starts_with_empty_state() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    assert dict(session.state) == {}


@pytest.mark.asyncio
async def test_repeated_session_creation_yields_unique_ids() -> None:
    service = ApiSessionService()
    ids = {await service.create_session() for _ in range(5)}
    assert len(ids) == 5


@pytest.mark.asyncio
async def test_separate_sessions_are_isolated() -> None:
    service = ApiSessionService()
    session_a = await service.create_session()
    session_b = await service.create_session()

    from backend.approval.service import create_action_proposal
    from backend.tests._api_fakes import append_state_delta

    session_obj_a = await service.get_session(session_a)
    delta: dict = {}
    create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, delta)
    await append_state_delta(service, session_obj_a, delta)

    refreshed_a = await service.get_session(session_a)
    refreshed_b = await service.get_session(session_b)
    assert PENDING_ACTION_PROPOSAL_STATE_KEY in refreshed_a.state
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in refreshed_b.state


@pytest.mark.asyncio
async def test_unknown_session_id_is_rejected() -> None:
    service = ApiSessionService()
    with pytest.raises(SafeErrorException) as exc_info:
        await service.get_session("does-not-exist")
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_session_exists_reports_false_for_unknown_id() -> None:
    service = ApiSessionService()
    assert await service.session_exists("does-not-exist") is False


@pytest.mark.asyncio
async def test_session_exists_reports_true_for_a_created_session() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    assert await service.session_exists(session_id) is True


def test_create_session_accepts_no_client_supplied_state() -> None:
    """Structural guarantee behind "client cannot inject arbitrary ADK
    state during creation": `create_session` takes only `user_id` (Phase
    4C -- the resolved owner) besides `self` -- there is no `state`
    parameter, and so no argument through which a caller could supply
    initial ADK state.
    """
    params = list(inspect.signature(ApiSessionService.create_session).parameters)
    assert params == ["self", "user_id"]
    assert "state" not in params


@pytest.mark.asyncio
async def test_lock_for_returns_the_same_lock_for_the_same_session() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    assert service.lock_for(session_id) is service.lock_for(session_id)


@pytest.mark.asyncio
async def test_lock_for_returns_different_locks_for_different_sessions() -> None:
    service = ApiSessionService()
    session_a = await service.create_session()
    session_b = await service.create_session()
    assert service.lock_for(session_a) is not service.lock_for(session_b)


@pytest.mark.asyncio
async def test_lock_for_returns_a_real_asyncio_lock() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    lock = service.lock_for(session_id)
    assert isinstance(lock, asyncio.Lock)


def test_get_session_service_is_a_process_wide_singleton() -> None:
    assert get_session_service() is get_session_service()
