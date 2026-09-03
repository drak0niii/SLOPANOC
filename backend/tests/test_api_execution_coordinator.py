"""Tests for backend/api/execution_coordinator.py (instruction section
20/33) -- the dedicated abstraction `ApiSessionService.lock_for` delegates
to, so `chat_service.py`/`approval_service.py` never depend on a raw
`asyncio.Lock` map directly.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from backend.api.execution_coordinator import SessionExecutionCoordinator
from backend.api.session_service import ApiSessionService


def test_lock_for_returns_the_same_lock_for_the_same_user_and_session() -> None:
    coordinator = SessionExecutionCoordinator()
    assert coordinator.lock_for("alice", "s1") is coordinator.lock_for("alice", "s1")


def test_lock_for_returns_different_locks_for_different_sessions_same_user() -> None:
    coordinator = SessionExecutionCoordinator()
    assert coordinator.lock_for("alice", "s1") is not coordinator.lock_for("alice", "s2")


def test_lock_for_returns_different_locks_for_different_users_same_session_id() -> None:
    """Different users' sessions remain independent even in the (already
    astronomically unlikely, since session ids are server-generated
    UUID4s) case of an identical session id string.
    """
    coordinator = SessionExecutionCoordinator()
    assert coordinator.lock_for("alice", "s1") is not coordinator.lock_for("bob", "s1")


def test_lock_for_returns_a_real_asyncio_lock() -> None:
    coordinator = SessionExecutionCoordinator()
    assert isinstance(coordinator.lock_for("alice", "s1"), asyncio.Lock)


@pytest.mark.asyncio
async def test_different_users_sessions_execute_independently() -> None:
    coordinator = SessionExecutionCoordinator()
    order: list[str] = []

    async def hold_alice():
        async with coordinator.lock_for("alice", "s1"):
            order.append("alice-start")
            await asyncio.sleep(0.05)
            order.append("alice-end")

    async def hold_bob():
        async with coordinator.lock_for("bob", "s1"):
            order.append("bob-start")
            order.append("bob-end")

    await asyncio.gather(hold_alice(), hold_bob())

    # bob's fast, independently-locked critical section completes while
    # alice's slow one is still in flight.
    assert order.index("bob-end") < order.index("alice-end")


def test_api_session_service_composes_a_coordinator_not_a_raw_lock_map() -> None:
    """`ApiSessionService` itself holds no `dict[..., asyncio.Lock]` --
    that implementation detail lives exclusively in
    `SessionExecutionCoordinator` (instruction: "The API should not
    directly depend on implementation-specific asyncio.Lock maps.").
    """
    source = inspect.getsource(ApiSessionService)
    assert "asyncio.Lock" not in source
    assert "SessionExecutionCoordinator" in source


def test_api_session_service_lock_for_delegates_to_the_coordinator() -> None:
    service = ApiSessionService()
    lock_via_service = service.lock_for("s1", "alice")
    lock_via_coordinator = service._coordinator.lock_for("alice", "s1")
    assert lock_via_service is lock_via_coordinator


def test_execution_coordinator_has_no_persistence_logic() -> None:
    """The coordinator only ever manages locks -- it must never call into
    ADK session storage itself (instruction section 33: "persistence
    backend is not bypassed by lock implementation" -- i.e. locking and
    persistence are two orthogonal concerns, neither one substituting for
    the other).
    """
    source = inspect.getsource(SessionExecutionCoordinator)
    for forbidden in ("append_event", "get_session", "create_session", "DatabaseSessionService", "InMemorySessionService"):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_coordinator_is_shared_by_chat_and_approval_paths() -> None:
    """`ChatService` and `approval_service` both go through
    `session_service.lock_for(...)`, which is backed by ONE
    `SessionExecutionCoordinator` instance per `ApiSessionService` -- not
    two separate lock maps that could disagree.
    """
    from backend.api import approval_service
    from backend.api.chat_service import ChatService
    from backend.tests._api_fakes import FakeRunner, simulate_proposal

    service = ApiSessionService()
    session_id = await service.create_session("alice")
    session = await service.get_session(session_id, "alice")
    await simulate_proposal(service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    refreshed = await service.get_session(session_id, "alice")
    proposal_id = refreshed.state["pending_action_proposal"]["proposal_id"]

    order: list[str] = []

    async def slow_chat_side_effect(session_service, session, text):
        order.append("chat-start")
        await asyncio.sleep(0.05)
        order.append("chat-end")

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=slow_chat_side_effect))

    original_persist = service.persist_state_delta

    async def instrumented_persist(session, delta):
        order.append("approve-start")
        await original_persist(session, delta)
        order.append("approve-end")

    service.persist_state_delta = instrumented_persist
    try:
        await asyncio.gather(
            chat_service.run_turn(session_id, "hi", "alice"),
            approval_service.approve(service, session_id, proposal_id, "alice"),
        )
    finally:
        service.persist_state_delta = original_persist

    # If chat and approve used two SEPARATE lock maps, their critical
    # sections could interleave; they don't.
    assert order[0].split("-")[0] == order[1].split("-")[0]
    assert order[2].split("-")[0] == order[3].split("-")[0]
