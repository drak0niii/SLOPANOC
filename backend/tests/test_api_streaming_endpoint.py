"""Tests for the SSE streaming endpoint (`POST /api/sessions/{id}/messages
/stream`, app.py) -- instruction sections 53-55: ownership, concurrency,
and client-disconnect/cancellation behavior.

Concurrency tests operate directly on `ChatService.execute_turn_events`/
`approval_service.approve`/`.reject` with markers recorded from INSIDE
each operation's critical section, exactly mirroring the established,
deterministic (non-sleep-flaky-timing) pattern already used in
test_api_chat_service.py's and test_api_approval_endpoints.py's own
concurrency tests -- `asyncio.sleep` is used only to give the scheduler a
deterministic yield point inside an already-lock-held critical section,
never to race against real time.

Disconnect tests operate at the generator level (calling `.aclose()` on
the `execute_turn_events` async generator mid-stream) rather than
simulating a real dropped TCP connection, per instruction section 55's
own explicit guidance ("preferring generator-level cancellation testing
over brittle real-networking disconnect simulation").
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from backend.api import approval_service
from backend.api.app import app
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.identity import DEV_USER_HEADER_NAME
from backend.api.session_service import ApiSessionService, get_session_service
from backend.tests._api_fakes import FakeEvent, FakeRunner, simulate_proposal

ALICE = "alice"
BOB = "bob"


def _parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        data_line = next(line for line in block.splitlines() if line.startswith("data: "))
        events.append(json.loads(data_line[len("data: ") :]))
    return events


@pytest.fixture()
def session_service() -> ApiSessionService:
    return ApiSessionService()


@pytest.fixture()
def chat_service(session_service: ApiSessionService) -> ChatService:
    return ChatService(session_service, runner=FakeRunner(session_service))


@pytest.fixture()
def client(session_service: ApiSessionService, chat_service: ChatService) -> TestClient:
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_chat_service] = lambda: chat_service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --- SSE endpoint contract (instruction section 34/35) ----------------------


def test_stream_endpoint_returns_event_stream_content_type(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]

    with client.stream("POST", f"/api/sessions/{session_id}/messages/stream", json={"message": "hi"}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    events = _parse_sse(body)
    assert events[0]["type"] == "run.started"
    assert events[-1]["type"] == "run.completed"


def test_stream_wire_format_uses_sse_event_and_data_lines(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]

    with client.stream("POST", f"/api/sessions/{session_id}/messages/stream", json={"message": "hi"}) as response:
        body = "".join(response.iter_text())

    assert "event: run.started\n" in body
    assert "event: run.completed\n" in body
    assert "StreamEvent(" not in body


def test_stream_sequence_numbers_are_monotonic_across_the_whole_stream(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]

    with client.stream("POST", f"/api/sessions/{session_id}/messages/stream", json={"message": "hi"}) as response:
        body = "".join(response.iter_text())

    events = _parse_sse(body)
    sequences = [e["sequence"] for e in events]
    assert sequences == sorted(sequences)
    assert sequences == list(range(1, len(sequences) + 1))


def test_stream_events_all_share_one_run_id_and_the_request_session_id(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]

    with client.stream("POST", f"/api/sessions/{session_id}/messages/stream", json={"message": "hi"}) as response:
        body = "".join(response.iter_text())

    events = _parse_sse(body)
    assert {e["session_id"] for e in events} == {session_id}
    assert len({e["run_id"] for e in events}) == 1


def test_sync_endpoint_still_works_unchanged(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]

    response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hi"})

    assert response.status_code == 200
    assert response.json()["message"]["content"] == "echo: hi"


# --- Ownership (instruction section 36/53) -----------------------------------


def test_user_a_can_stream_their_own_session(client: TestClient) -> None:
    session_id = client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: ALICE}).json()["session_id"]

    with client.stream(
        "POST",
        f"/api/sessions/{session_id}/messages/stream",
        json={"message": "hi"},
        headers={DEV_USER_HEADER_NAME: ALICE},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert _parse_sse(body)[-1]["type"] == "run.completed"


def test_user_b_cannot_stream_user_a_session(client: TestClient) -> None:
    session_id = client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: ALICE}).json()["session_id"]

    response = client.post(
        f"/api/sessions/{session_id}/messages/stream",
        json={"message": "hi"},
        headers={DEV_USER_HEADER_NAME: BOB},
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_foreign_session_stream_attempt_is_indistinguishable_from_unknown(client: TestClient) -> None:
    session_id = client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: ALICE}).json()["session_id"]

    foreign = client.post(
        f"/api/sessions/{session_id}/messages/stream",
        json={"message": "hi"},
        headers={DEV_USER_HEADER_NAME: BOB},
    )
    unknown = client.post(
        "/api/sessions/does-not-exist/messages/stream",
        json={"message": "hi"},
        headers={DEV_USER_HEADER_NAME: BOB},
    )

    assert foreign.status_code == unknown.status_code == 404
    foreign_body = foreign.json()
    unknown_body = unknown.json()
    # `correlationId` is a fresh per-request id -- everything else about
    # the error (code, message) must be identical, the actual anti-
    # enumeration guarantee.
    foreign_body.pop("correlationId", None)
    unknown_body.pop("correlationId", None)
    assert foreign_body == unknown_body


def test_ownership_denial_never_opens_an_sse_stream_containing_an_error_event(client: TestClient) -> None:
    """A rejected stream attempt is a normal HTTP 404 JSON error, not a
    200 SSE stream carrying an `error` event -- ownership must be checked
    BEFORE the response is opened (instruction section 36).
    """
    session_id = client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: ALICE}).json()["session_id"]

    response = client.post(
        f"/api/sessions/{session_id}/messages/stream",
        json={"message": "hi"},
        headers={DEV_USER_HEADER_NAME: BOB},
    )

    assert not response.headers["content-type"].startswith("text/event-stream")


@pytest.mark.asyncio
async def test_independent_users_stream_independent_sessions_concurrently(session_service: ApiSessionService) -> None:
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(session_service, runner=FakeRunner(session_service))
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            alice_session = (
                await async_client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: ALICE})
            ).json()["session_id"]
            bob_session = (
                await async_client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: BOB})
            ).json()["session_id"]

            async def stream_for(user: str, session_id: str) -> list[dict]:
                async with async_client.stream(
                    "POST",
                    f"/api/sessions/{session_id}/messages/stream",
                    json={"message": f"hi from {user}"},
                    headers={DEV_USER_HEADER_NAME: user},
                ) as response:
                    text = ""
                    async for chunk in response.aiter_text():
                        text += chunk
                return _parse_sse(text)

            alice_events, bob_events = await asyncio.gather(
                stream_for(ALICE, alice_session), stream_for(BOB, bob_session)
            )
    finally:
        app.dependency_overrides.clear()

    assert {e["session_id"] for e in alice_events} == {alice_session}
    assert {e["session_id"] for e in bob_events} == {bob_session}
    assert alice_events[-1]["type"] == "run.completed"
    assert bob_events[-1]["type"] == "run.completed"


# --- Concurrency (instruction section 38/54) ---------------------------------


@pytest.mark.asyncio
async def test_stream_and_sync_for_the_same_session_serialize() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    order: list[str] = []

    async def slow_side_effect(session_service, session, text):
        order.append("stream-start")
        await asyncio.sleep(0.05)
        order.append("stream-end")

    async def fast_side_effect(session_service, session, text):
        order.append("sync-start")
        order.append("sync-end")

    streaming_service = ChatService(service, runner=FakeRunner(service, side_effect=slow_side_effect))
    sync_service = ChatService(service, runner=FakeRunner(service, side_effect=fast_side_effect))

    async def drain_stream() -> None:
        async for _event in streaming_service.execute_turn_events(session_id, "first"):
            pass

    await asyncio.gather(drain_stream(), sync_service.run_turn(session_id, "second"))

    assert order == ["stream-start", "stream-end", "sync-start", "sync-end"]


@pytest.mark.asyncio
async def test_stream_and_approve_for_the_same_session_serialize() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await simulate_proposal(service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    refreshed = await service.get_session(session_id)
    from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY

    proposal_id = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]
    order: list[str] = []

    async def slow_side_effect(session_service, session, text):
        order.append("stream-start")
        await asyncio.sleep(0.05)
        order.append("stream-end")

    streaming_service = ChatService(service, runner=FakeRunner(service, side_effect=slow_side_effect))

    original_persist = service.persist_state_delta

    async def instrumented_persist(session, delta):
        order.append("approve-start")
        await original_persist(session, delta)
        order.append("approve-end")

    service.persist_state_delta = instrumented_persist
    try:
        async def drain_stream() -> None:
            async for _event in streaming_service.execute_turn_events(session_id, "hi"):
                pass

        await asyncio.gather(drain_stream(), approval_service.approve(service, session_id, proposal_id))
    finally:
        service.persist_state_delta = original_persist

    assert order[0].endswith("-start")
    assert order[1].endswith("-end")
    assert order[2].endswith("-start")
    assert order[3].endswith("-end")
    assert order[0].split("-")[0] == order[1].split("-")[0]


@pytest.mark.asyncio
async def test_stream_and_reject_for_the_same_session_serialize() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await simulate_proposal(service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    refreshed = await service.get_session(session_id)
    from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY

    proposal_id = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]
    order: list[str] = []

    async def slow_side_effect(session_service, session, text):
        order.append("stream-start")
        await asyncio.sleep(0.05)
        order.append("stream-end")

    streaming_service = ChatService(service, runner=FakeRunner(service, side_effect=slow_side_effect))

    original_persist = service.persist_state_delta

    async def instrumented_persist(session, delta):
        order.append("reject-start")
        await original_persist(session, delta)
        order.append("reject-end")

    service.persist_state_delta = instrumented_persist
    try:
        async def drain_stream() -> None:
            async for _event in streaming_service.execute_turn_events(session_id, "hi"):
                pass

        await asyncio.gather(drain_stream(), approval_service.reject(service, session_id, proposal_id))
    finally:
        service.persist_state_delta = original_persist

    assert order[0].endswith("-start")
    assert order[1].endswith("-end")


@pytest.mark.asyncio
async def test_streams_for_different_sessions_proceed_independently() -> None:
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

    service_a = ChatService(service, runner=FakeRunner(service, side_effect=slow_side_effect))
    service_b = ChatService(service, runner=FakeRunner(service, side_effect=fast_side_effect))

    async def drain(chat_service: ChatService, session_id: str, text: str) -> None:
        async for _event in chat_service.execute_turn_events(session_id, text):
            pass

    await asyncio.gather(drain(service_a, session_a, "first"), drain(service_b, session_b, "second"))

    assert order == ["first-start", "second-start", "second-end", "first-end"]


@pytest.mark.asyncio
async def test_lock_releases_after_normal_stream_completion() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    async for _event in chat_service.execute_turn_events(session_id, "hi"):
        pass

    assert not service.lock_for(session_id).locked()


# --- Disconnect / cancellation (instruction section 39/55) -------------------


class _GatedRunner:
    """Yields one partial chunk, then blocks on an externally-controlled
    `asyncio.Event` before yielding its final event -- lets a test
    observe "the background turn is still genuinely in flight" at a
    precise, deterministic point, independent of whatever the HTTP-facing
    consumer does (no sleep/timing races).
    """

    def __init__(self, session_service, resume: asyncio.Event) -> None:
        self._session_service = session_service
        self._resume = resume
        self.finished = False

    async def run_async(self, *, user_id, session_id, new_message, run_config=None):
        session = await self._session_service.get_session(session_id, user_id)
        await self._session_service.persist_state_delta(session, {})
        yield FakeEvent(text="a", final=False, partial=True)
        await self._resume.wait()
        yield FakeEvent(text="ab", final=True, partial=False)
        self.finished = True


@pytest.mark.asyncio
async def test_disconnecting_the_consumer_does_not_stop_the_background_turn() -> None:
    """The hardening-pass invariant: an HTTP disconnect (modeled here as
    the SSE consumer abandoning/closing the generator) must never cut a
    still-in-flight logical turn short, because the turn -- not the HTTP
    layer -- is what may still be mutating the session.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    resume = asyncio.Event()
    runner = _GatedRunner(service, resume)
    chat_service = ChatService(service, runner=runner)

    agen = chat_service.execute_turn_events(session_id, "hi")
    seen = 0
    async for _event in agen:
        seen += 1
        # run.started, then the immediate "processing" status -- by the
        # second item, the background task is guaranteed to already exist
        # (it's created, and the lock acquired, before that item can be
        # produced at all).
        if seen >= 2:
            break

    assert len(chat_service._background_turns) == 1
    task = next(iter(chat_service._background_turns))

    await agen.aclose()  # simulate the HTTP consumer disconnecting

    # The background turn must still be running, still holding the lock --
    # disconnecting the consumer must not have cancelled it.
    assert not task.done()
    assert service.lock_for(session_id).locked()
    assert runner.finished is False

    resume.set()
    await task  # deterministic: wait for the turn's own true completion

    assert service.lock_for(session_id).locked() is False
    assert runner.finished is True
    assert task not in chat_service._background_turns  # cleaned up via its done-callback


@pytest.mark.asyncio
async def test_a_second_same_session_operation_blocks_until_the_disconnected_turn_truly_finishes() -> None:
    """Direct proof of the required invariant: SESSION LOCK MAY BE
    RELEASED ONLY WHEN THE OLD TURN CAN NO LONGER MUTATE THAT SESSION.
    A brand new operation attempted against the same session, started
    right after the first consumer disconnects, must not be able to
    proceed while the first (still in-flight) turn could still mutate
    state.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    resume = asyncio.Event()
    runner = _GatedRunner(service, resume)
    first_chat_service = ChatService(service, runner=runner)

    agen = first_chat_service.execute_turn_events(session_id, "first")
    seen = 0
    async for _event in agen:
        seen += 1
        if seen >= 2:
            break
    assert len(first_chat_service._background_turns) == 1
    first_task = next(iter(first_chat_service._background_turns))
    await agen.aclose()  # first caller disconnects; the turn keeps running

    second_chat_service = ChatService(service, runner=FakeRunner(service))
    second_task = asyncio.create_task(second_chat_service.run_turn(session_id, "second"))

    # The second operation must NOT complete while the first turn is still
    # gated -- proven deterministically (no sleep/timing guess) via a
    # short, generous timeout that would only be hit if it were blocked.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(second_task), timeout=0.05)

    resume.set()  # let the first (disconnected) turn actually finish
    response = await second_task
    await first_task  # deterministic cleanup -- never leave it dangling past this test

    assert response.message.content == "echo: second"


@pytest.mark.asyncio
async def test_already_persisted_state_remains_valid_after_disconnect() -> None:
    """A proposal created before the disconnect point must still be
    intact and readable afterward -- disconnecting must never corrupt or
    roll back already-committed session state.
    """
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    events = [
        FakeEvent(text="a", final=False, partial=True),
        FakeEvent(text="b", final=False, partial=True),
        FakeEvent(text="ab", final=True, partial=False),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect, events=events))

    agen = chat_service.execute_turn_events(session_id, "propose it")
    seen = 0
    async for _event in agen:
        seen += 1
        # run.started, the immediate "processing" status, and status.clear
        # (which is only reachable once the runner -- and so `side_effect`
        # -- has actually run) -- by 3 events in, the proposal is
        # guaranteed to already be persisted.
        if seen >= 3:
            break
    pending_tasks = list(chat_service._background_turns)
    await agen.aclose()
    if pending_tasks:
        # Deterministic cleanup -- this fixed, ungated event list finishes
        # almost immediately regardless of consumer disconnect, but never
        # leave it unawaited past this test either way.
        await asyncio.gather(*pending_tasks, return_exceptions=True)

    from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY

    refreshed = await service.get_session(session_id)
    assert PENDING_ACTION_PROPOSAL_STATE_KEY in refreshed.state
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "pending"


@pytest.mark.asyncio
async def test_background_turn_task_is_explicitly_tracked_and_cleaned_up() -> None:
    """The background turn task (hardening pass) is never orphaned: it is
    held in `ChatService._background_turns` for its entire real lifetime
    (a strong reference, so it cannot be silently garbage-collected
    mid-turn) and removed via its own `add_done_callback` the moment it
    finishes -- proven directly, not inferred.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    assert chat_service._background_turns == set()

    async for _event in chat_service.execute_turn_events(session_id, "hi"):
        pass

    # `add_done_callback` callbacks run via `loop.call_soon` -- one more
    # trip through the event loop lets the already-finished task's
    # cleanup callback actually fire before we assert on it.
    await asyncio.sleep(0)
    assert chat_service._background_turns == set()


@pytest.mark.asyncio
async def test_background_turn_task_exception_is_retrieved_not_silently_dropped() -> None:
    """A genuinely unexpected failure inside the background task (not the
    normal `_run_turn_events`-catches-everything path) must still have
    its exception retrieved via the task's own done-callback -- otherwise
    asyncio logs an "exception was never retrieved" warning because
    nothing else ever awaits this task directly.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    def _broken_lock_for(*_args, **_kwargs):
        raise RuntimeError("simulated coordinator failure")

    service.lock_for = _broken_lock_for  # forces an exception outside _run_turn_events's own try/except

    async for _event in chat_service.execute_turn_events(session_id, "hi"):
        pass  # the generator must still terminate normally (via the sentinel), not hang or raise here

    await asyncio.sleep(0)  # let the done-callback actually run (see note above)
    assert chat_service._background_turns == set()


# NOTE: an earlier version of this hardening pass included an HTTP/
# TestClient-level "early disconnect" smoke test here (closing a
# `client.stream(...)` response body early and checking a follow-up
# request still works). It was removed: `starlette.testclient.TestClient`
# gives each un-contextmanaged call its OWN short-lived `anyio` blocking
# portal (a dedicated background thread + event loop, torn down the
# moment that call's `with` block exits -- see
# `TestClient._portal_factory`), which is a portal-teardown mechanism
# entirely unrelated to the real Uvicorn/ASGI disconnect path this
# module's other tests verify from source. Exercising it introduced
# genuine test-infra flakiness (a background turn task's `asyncio.Task`
# racing that portal thread's own loop shutdown, surfacing as
# intermittent aiosqlite "Event loop is closed" warnings/failures in
# unrelated tests) without adding any coverage beyond what the
# deterministic generator-level tests above already prove. This matches
# the original instruction's own guidance to prefer generator-level
# cancellation testing over brittle real-networking disconnect
# simulation.
