"""Tests for real server-side run cancellation (pre-4H refinement) --
`ChatService.cancel_run` and its `POST /api/sessions/{id}/runs/{id}/cancel`
route (app.py).

Reuses the SAME `_GatedRunner`/`asyncio.Event` pattern already established
in test_api_streaming_endpoint.py for deterministic, non-sleep-flaky
observation of "the background turn is still genuinely in flight" at a
precise point -- never a real-time race.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from backend.api.app import app
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.identity import DEV_USER_HEADER_NAME
from backend.api.session_service import ApiSessionService, get_session_service
from backend.api.streaming_events import StreamEventType
from backend.tests._api_fakes import FakeEvent, FakeRunner

ALICE = "alice"
BOB = "bob"


class _GatedRunner:
    """Mirrors test_api_streaming_endpoint.py's own `_GatedRunner` exactly
    -- yields one partial chunk, then blocks on an externally-controlled
    `asyncio.Event` before yielding its final event and mutating
    `self.finished`/`self.mutated`.
    """

    def __init__(self, session_service, resume: asyncio.Event) -> None:
        self._session_service = session_service
        self._resume = resume
        self.finished = False
        self.mutated = False

    async def run_async(self, *, user_id, session_id, new_message, run_config=None):
        session = await self._session_service.get_session(session_id, user_id)
        await self._session_service.persist_state_delta(session, {})
        yield FakeEvent(text="a", final=False, partial=True)
        await self._resume.wait()
        # Only reached if cancellation did NOT actually stop this task --
        # a real, observable state mutation the tests below prove never
        # happens for a genuinely cancelled run.
        self.mutated = True
        yield FakeEvent(text="ab", final=True, partial=False)
        self.finished = True


async def _start_gated_run(chat_service: ChatService, session_id: str, message: str = "hi", user_id: str = "api-user"):
    """Consumes exactly the two deterministic events that exist before
    the background task can have been created/registered (mirrors
    test_api_streaming_endpoint.py's own "seen >= 2" pattern) -- returns
    the agen, the run_id (from run.started), and the background task.
    """
    agen = chat_service.execute_turn_events(session_id, message, user_id)
    run_id = None
    seen = 0
    async for event in agen:
        seen += 1
        if event.type == StreamEventType.RUN_STARTED:
            run_id = event.run_id
        if seen >= 2:
            break
    assert run_id is not None
    assert len(chat_service._background_turns) == 1
    task = next(iter(chat_service._background_turns))
    return agen, run_id, task


# --- ChatService.cancel_run -- unit level -----------------------------------


@pytest.mark.asyncio
async def test_cancel_run_stops_a_genuinely_in_flight_run() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    resume = asyncio.Event()
    runner = _GatedRunner(service, resume)
    chat_service = ChatService(service, runner=runner)

    agen, run_id, task = await _start_gated_run(chat_service, session_id)
    await agen.aclose()  # consumer disconnects, as any real SSE client would after clicking Stop

    cancelled = await chat_service.cancel_run(session_id, run_id)
    assert cancelled is True

    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    # The gated portion of the run (past `resume.wait()`) never executed --
    # no further model-continuation/tool-result processing, no mutation,
    # no final response from the cancelled run.
    resume.set()  # if the task somehow survived, let it run so the assertion below is meaningful
    await asyncio.sleep(0)
    assert runner.mutated is False
    assert runner.finished is False


@pytest.mark.asyncio
async def test_cancel_run_releases_the_session_lock() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    resume = asyncio.Event()
    runner = _GatedRunner(service, resume)
    chat_service = ChatService(service, runner=runner)

    agen, run_id, task = await _start_gated_run(chat_service, session_id)
    await agen.aclose()
    assert service.lock_for(session_id).locked()

    await chat_service.cancel_run(session_id, run_id)
    with pytest.raises(asyncio.CancelledError):
        await task

    assert service.lock_for(session_id).locked() is False


@pytest.mark.asyncio
async def test_a_subsequent_run_starts_normally_after_a_cancellation() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    resume = asyncio.Event()
    runner = _GatedRunner(service, resume)
    chat_service = ChatService(service, runner=runner)

    agen, run_id, task = await _start_gated_run(chat_service, session_id)
    await agen.aclose()
    await chat_service.cancel_run(session_id, run_id)
    with pytest.raises(asyncio.CancelledError):
        await task

    normal_chat_service = ChatService(service, runner=FakeRunner(service))
    response = await normal_chat_service.run_turn(session_id, "second message")
    assert response.message.content == "echo: second message"


@pytest.mark.asyncio
async def test_cancel_run_is_idempotent_for_an_already_finished_run() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    collected = [event async for event in chat_service.execute_turn_events(session_id, "hi")]
    run_id = next(e.run_id for e in collected if e.type == StreamEventType.RUN_STARTED)

    cancelled = await chat_service.cancel_run(session_id, run_id)
    assert cancelled is False  # already finished -- a safe no-op, never an error


@pytest.mark.asyncio
async def test_cancel_run_is_safe_for_a_run_id_that_never_existed() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    cancelled = await chat_service.cancel_run(session_id, "not-a-real-run-id")
    assert cancelled is False


@pytest.mark.asyncio
async def test_a_stale_run_id_cannot_cancel_a_newer_run_on_the_same_session() -> None:
    """The exact security requirement: a run_id from an earlier, already-
    finished run must never be able to cancel a DIFFERENT, currently
    in-flight run that happens to share the same session_id.
    """
    service = ApiSessionService()
    session_id = await service.create_session()

    # First run completes normally, its own run_id is now stale.
    first_chat_service = ChatService(service, runner=FakeRunner(service))
    first_collected = [event async for event in first_chat_service.execute_turn_events(session_id, "first")]
    stale_run_id = next(e.run_id for e in first_collected if e.type == StreamEventType.RUN_STARTED)

    # Second run starts fresh, on the SAME session, and is gated in flight.
    resume = asyncio.Event()
    runner = _GatedRunner(service, resume)
    second_chat_service = ChatService(service, runner=runner)
    agen, new_run_id, task = await _start_gated_run(second_chat_service, session_id)
    await agen.aclose()
    assert new_run_id != stale_run_id

    cancelled = await second_chat_service.cancel_run(session_id, stale_run_id)
    assert cancelled is False  # the stale id matches nothing tracked
    assert not task.done()  # the genuinely current run is completely unaffected

    resume.set()  # deterministic cleanup -- let the still-live run finish
    await task
    assert runner.finished is True


@pytest.mark.asyncio
async def test_cancel_run_raises_not_found_for_an_unknown_session() -> None:
    from backend.gateway.safe_error import SafeErrorException

    service = ApiSessionService()
    chat_service = ChatService(service, runner=FakeRunner(service))

    with pytest.raises(SafeErrorException):
        await chat_service.cancel_run("no-such-session", "no-such-run")


# --- POST /api/sessions/{id}/runs/{id}/cancel -- route level ---------------


@pytest.fixture()
def session_service() -> ApiSessionService:
    return ApiSessionService()


@pytest.fixture(autouse=True)
def _override_dependencies(session_service: ApiSessionService):
    app.dependency_overrides[get_session_service] = lambda: session_service
    yield
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app)


@pytest.mark.asyncio
async def test_cancel_endpoint_returns_false_for_a_finished_run(session_service: ApiSessionService) -> None:
    chat_service = ChatService(session_service, runner=FakeRunner(session_service))
    app.dependency_overrides[get_chat_service] = lambda: chat_service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: ALICE})
        session_id = created.json()["session_id"]
        collected = [
            event
            async for event in chat_service.execute_turn_events(session_id, "hi", ALICE)
        ]
        run_id = next(e.run_id for e in collected if e.type == StreamEventType.RUN_STARTED)

        response = await client.post(
            f"/api/sessions/{session_id}/runs/{run_id}/cancel", headers={DEV_USER_HEADER_NAME: ALICE}
        )
        assert response.status_code == 200
        body = response.json()
        assert body == {"session_id": session_id, "run_id": run_id, "cancelled": False}


@pytest.mark.asyncio
async def test_cancel_endpoint_returns_404_for_a_foreign_session(session_service: ApiSessionService) -> None:
    chat_service = ChatService(session_service, runner=FakeRunner(session_service))
    app.dependency_overrides[get_chat_service] = lambda: chat_service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: ALICE})
        session_id = created.json()["session_id"]

        response = await client.post(
            f"/api/sessions/{session_id}/runs/anything/cancel", headers={DEV_USER_HEADER_NAME: BOB}
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_endpoint_returns_404_for_an_unknown_session(session_service: ApiSessionService) -> None:
    chat_service = ChatService(session_service, runner=FakeRunner(session_service))
    app.dependency_overrides[get_chat_service] = lambda: chat_service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/sessions/no-such-session/runs/no-such-run/cancel", headers={DEV_USER_HEADER_NAME: ALICE}
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_endpoint_actually_cancels_a_genuinely_in_flight_run(session_service: ApiSessionService) -> None:
    resume = asyncio.Event()
    runner = _GatedRunner(session_service, resume)
    chat_service = ChatService(session_service, runner=runner)
    app.dependency_overrides[get_chat_service] = lambda: chat_service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: ALICE})
        session_id = created.json()["session_id"]

        agen, run_id, task = await _start_gated_run(chat_service, session_id, user_id=ALICE)
        await agen.aclose()

        response = await client.post(
            f"/api/sessions/{session_id}/runs/{run_id}/cancel", headers={DEV_USER_HEADER_NAME: ALICE}
        )
        assert response.status_code == 200
        assert response.json()["cancelled"] is True

        with pytest.raises(asyncio.CancelledError):
            await task
        resume.set()
        await asyncio.sleep(0)
        assert runner.mutated is False
