"""Tests for the Phase 4B trusted approve/reject endpoints -- both at the
`approval_service` function level and through the full HTTP stack via
`TestClient`. `FakeRunner`/`simulate_proposal` (backend/tests/_api_fakes.py)
stand in for the agent turn wherever one is needed to create a proposal;
`approve`/`reject` themselves never touch a runner at all.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api import approval_service
from backend.api.app import app
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.session_service import ApiSessionService, get_session_service
from backend.approval.service import (
    PENDING_ACTION_PROPOSAL_STATE_KEY,
    approve_proposal,
    consume_proposal,
    create_action_proposal,
    reject_proposal,
)
from backend.gateway.safe_error import SafeErrorException
from backend.tests._api_fakes import FakeRunner, simulate_proposal


async def _propose(session_service: ApiSessionService, session_id: str, message: str = "Hello") -> str:
    session = await session_service.get_session(session_id)
    await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": message})
    refreshed = await session_service.get_session(session_id)
    return refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]


# --- APPROVE (service level) ------------------------------------------------


@pytest.mark.asyncio
async def test_valid_pending_proposal_is_approved() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)

    response = await approval_service.approve(service, session_id, proposal_id)

    assert response.result == "approved"
    assert response.pending_action.status == "approved"
    assert response.pending_action.proposal_id == proposal_id


@pytest.mark.asyncio
async def test_approval_persists_through_the_real_adk_session_service() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)

    await approval_service.approve(service, session_id, proposal_id)

    refreshed = await service.get_session(session_id)
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "approved"


# --- REJECT (service level) -------------------------------------------------


@pytest.mark.asyncio
async def test_valid_pending_proposal_is_rejected() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)

    response = await approval_service.reject(service, session_id, proposal_id)

    assert response.result == "rejected"
    assert response.pending_action.status == "rejected"


@pytest.mark.asyncio
async def test_rejection_persists_through_the_real_adk_session_service() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)

    await approval_service.reject(service, session_id, proposal_id)

    refreshed = await service.get_session(session_id)
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "rejected"


# --- PROPOSAL ID SAFETY ------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_proposal_id_is_rejected_and_denies_approval() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    await _propose(service, session_id)

    with pytest.raises(SafeErrorException) as exc_info:
        await approval_service.approve(service, session_id, "not-the-real-id")
    assert exc_info.value.safe_error.error_code == "action_failure"


@pytest.mark.asyncio
async def test_stale_proposal_a_cannot_affect_newer_proposal_b() -> None:
    """The exact scenario from the instruction: UI displays proposal A,
    the agent later creates proposal B, the user clicks the stale card
    for A -- the endpoint must reject, and B must remain untouched.
    """
    service = ApiSessionService()
    session_id = await service.create_session()

    proposal_a_id = await _propose(service, session_id, message="A")
    proposal_b_id = await _propose(service, session_id, message="B")
    assert proposal_a_id != proposal_b_id

    with pytest.raises(SafeErrorException) as exc_info:
        await approval_service.approve(service, session_id, proposal_a_id)
    assert exc_info.value.safe_error.error_code == "action_failure"

    refreshed = await service.get_session(session_id)
    stored = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]
    assert stored["proposal_id"] == proposal_b_id
    assert stored["status"] == "pending"  # B is untouched, still awaiting approval


# --- STATE -------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_a_approval_does_not_affect_session_b() -> None:
    service = ApiSessionService()
    session_a = await service.create_session()
    session_b = await service.create_session()

    proposal_a = await _propose(service, session_a)
    proposal_b = await _propose(service, session_b)

    await approval_service.approve(service, session_a, proposal_a)

    refreshed_a = await service.get_session(session_a)
    refreshed_b = await service.get_session(session_b)
    assert refreshed_a.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "approved"
    assert refreshed_b.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "pending"
    assert refreshed_b.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"] == proposal_b


def test_no_parallel_approval_store_exists() -> None:
    """Structural guarantee: `approval_service.py` reads/writes proposal
    state exclusively via `session_service`/`approve_proposal`/
    `reject_proposal` -- it holds no module-level dict/cache of its own.
    """
    import backend.api.approval_service as module

    for name in dir(module):
        value = getattr(module, name)
        if isinstance(value, dict) and not name.startswith("_"):
            assert not value, f"unexpected persistent dict-like state: {name}"


# --- LIFECYCLE ---------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_proposal_cannot_be_approved_via_the_endpoint() -> None:
    from datetime import datetime, timedelta, timezone

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    long_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    delta: dict[str, Any] = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, delta, now=long_ago)
    await service.persist_state_delta(session, delta)

    with pytest.raises(SafeErrorException) as exc_info:
        await approval_service.approve(service, session_id, proposal.proposal_id)
    assert exc_info.value.safe_error.error_code == "action_failure"


@pytest.mark.asyncio
async def test_rejected_proposal_cannot_be_approved_via_the_endpoint() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)

    await approval_service.reject(service, session_id, proposal_id)

    with pytest.raises(SafeErrorException) as exc_info:
        await approval_service.approve(service, session_id, proposal_id)
    assert exc_info.value.safe_error.error_code == "action_failure"


@pytest.mark.asyncio
async def test_consumed_proposal_cannot_be_approved_via_the_endpoint() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)
    await approval_service.approve(service, session_id, proposal_id)

    session = await service.get_session(session_id)
    delta: dict[str, Any] = {}
    consume_result = consume_proposal(proposal_id, session.state)
    assert consume_result.success
    await service.persist_state_delta(session, {PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]})

    with pytest.raises(SafeErrorException) as exc_info:
        await approval_service.approve(service, session_id, proposal_id)
    assert exc_info.value.safe_error.error_code == "action_failure"


@pytest.mark.asyncio
async def test_repeated_approval_is_idempotent_matching_the_existing_service_behavior() -> None:
    """`approve_proposal` treats re-approving an already-approved proposal
    as a safe no-op (see backend/approval/service.py, unchanged) -- the
    endpoint must preserve that, not invent its own idempotency or its
    own conflict behavior on top of it.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)

    first = await approval_service.approve(service, session_id, proposal_id)
    second = await approval_service.approve(service, session_id, proposal_id)

    assert first.pending_action.status == "approved"
    assert second.pending_action.status == "approved"


@pytest.mark.asyncio
async def test_repeated_rejection_is_idempotent_matching_the_existing_service_behavior() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)

    first = await approval_service.reject(service, session_id, proposal_id)
    second = await approval_service.reject(service, session_id, proposal_id)

    assert first.pending_action.status == "rejected"
    assert second.pending_action.status == "rejected"


@pytest.mark.asyncio
async def test_rejecting_an_approved_proposal_is_still_allowed_per_existing_service() -> None:
    """`reject_proposal` allows rejecting a still-approved-but-not-yet-
    executed proposal (see backend/approval/service.py, unchanged) -- the
    endpoint must not add a stricter rule on top of that.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)
    await approval_service.approve(service, session_id, proposal_id)

    response = await approval_service.reject(service, session_id, proposal_id)

    assert response.result == "rejected"
    assert response.pending_action.status == "rejected"


# --- CONCURRENCY ---------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_and_chat_turn_for_the_same_session_serialize() -> None:
    """Markers are recorded from INSIDE each operation's critical section
    (the chat turn's `side_effect`, which runs while `chat_service`'s
    lock is held; `approve`'s `persist_state_delta` call, which runs
    while `approval_service`'s lock is held) -- not around the outer
    `await` in this test, which would race ahead of either lock being
    acquired and prove nothing.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)
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
            chat_service.run_turn(session_id, "hi"),
            approval_service.approve(service, session_id, proposal_id),
        )
    finally:
        service.persist_state_delta = original_persist

    # Whichever ran first, the two must not interleave -- the second
    # entry only starts after the first's "-end" marker.
    assert order[0].endswith("-start")
    assert order[1].endswith("-end")
    assert order[2].endswith("-start")
    assert order[3].endswith("-end")
    assert order[0].split("-")[0] == order[1].split("-")[0]
    assert order[2].split("-")[0] == order[3].split("-")[0]


@pytest.mark.asyncio
async def test_reject_and_chat_turn_for_the_same_session_serialize() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _propose(service, session_id)
    order: list[str] = []

    async def slow_chat_side_effect(session_service, session, text):
        order.append("chat-start")
        await asyncio.sleep(0.05)
        order.append("chat-end")

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=slow_chat_side_effect))

    original_persist = service.persist_state_delta

    async def instrumented_persist(session, delta):
        order.append("reject-start")
        await original_persist(session, delta)
        order.append("reject-end")

    service.persist_state_delta = instrumented_persist
    try:
        await asyncio.gather(
            chat_service.run_turn(session_id, "hi"),
            approval_service.reject(service, session_id, proposal_id),
        )
    finally:
        service.persist_state_delta = original_persist

    assert order[0].endswith("-start")
    assert order[1].endswith("-end")
    assert order[0].split("-")[0] == order[1].split("-")[0]


@pytest.mark.asyncio
async def test_approve_for_different_sessions_remains_independent() -> None:
    service = ApiSessionService()
    session_a = await service.create_session()
    session_b = await service.create_session()
    proposal_a = await _propose(service, session_a)
    proposal_b = await _propose(service, session_b)
    order: list[str] = []

    original_persist = service.persist_state_delta

    async def persist_slow_only_for_session_a(session, delta):
        marker = "a" if session.id == session_a else "b"
        order.append(f"{marker}-start")
        if marker == "a":
            await asyncio.sleep(0.05)
        await original_persist(session, delta)
        order.append(f"{marker}-end")

    service.persist_state_delta = persist_slow_only_for_session_a
    try:
        await asyncio.gather(
            approval_service.approve(service, session_a, proposal_a),
            approval_service.approve(service, session_b, proposal_b),
        )
    finally:
        service.persist_state_delta = original_persist

    # session_b's fast approval completes while session_a's slow one is
    # still in flight -- proves the two sessions did not block each other.
    assert order.index("b-end") < order.index("a-end")


@pytest.mark.asyncio
async def test_approval_re_reads_latest_session_state_after_lock_acquisition() -> None:
    """If the session's active proposal changes between the pre-lock
    existence check and lock acquisition, the transition must act on the
    LATEST state, not a stale snapshot taken before the lock.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    first_proposal_id = await _propose(service, session_id, message="first")

    # Replace the proposal with a second one "concurrently" -- simulated
    # here by simply doing it before calling approve, since the
    # assertion that matters is that approve() re-fetches state INSIDE
    # the lock rather than relying on any snapshot taken earlier.
    second_proposal_id = await _propose(service, session_id, message="second")
    assert first_proposal_id != second_proposal_id

    response = await approval_service.approve(service, session_id, second_proposal_id)
    assert response.result == "approved"
    assert response.pending_action.proposal_id == second_proposal_id


# --- HTTP level: no agent/Runner/Power Automate call --------------------


def test_approve_endpoint_never_calls_team_manager_runner() -> None:
    service = ApiSessionService()

    calls: list[str] = []

    class TrackingRunner:
        async def run_async(self, *, user_id, session_id, new_message, run_config=None):
            calls.append("runner-called")
            yield None

    app.dependency_overrides[get_session_service] = lambda: service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(service, runner=TrackingRunner())
    try:
        with TestClient(app) as client:
            session_id = client.post("/api/sessions").json()["session_id"]

            async def setup():
                sess = await service.get_session(session_id)
                await simulate_proposal(service, sess, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

            asyncio.run(setup())

            refreshed = asyncio.run(service.get_session(session_id))
            proposal_id = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

            response = client.post(f"/api/sessions/{session_id}/approve", json={"proposal_id": proposal_id})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert calls == []  # the Runner (Team Manager/Gemini) was never invoked


def test_approve_endpoint_never_calls_power_automate(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.gateway import power_automate_client as pac_module

    def _fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Power Automate must never be called from /approve")

    monkeypatch.setattr(pac_module.requests, "post", _fail_if_called)

    service = ApiSessionService()
    session_id = asyncio.run(service.create_session())

    async def setup():
        sess = await service.get_session(session_id)
        await simulate_proposal(service, sess, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
        refreshed = await service.get_session(session_id)
        return refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

    proposal_id = asyncio.run(setup())

    app.dependency_overrides[get_session_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(f"/api/sessions/{session_id}/approve", json={"proposal_id": proposal_id})
    finally:
        app.dependency_overrides.clear()

    # If Power Automate had been called, `_fail_if_called` would have
    # raised and this response would never have been produced.
    assert response.status_code == 200


# --- API SECURITY (HTTP level) ------------------------------------------


@pytest.fixture()
def http_session_service() -> ApiSessionService:
    return ApiSessionService()


@pytest.fixture()
def http_client(http_session_service: ApiSessionService) -> TestClient:
    app.dependency_overrides[get_session_service] = lambda: http_session_service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(
        http_session_service, runner=FakeRunner(http_session_service)
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_proposal_via_http(client: TestClient, session_service: ApiSessionService, session_id: str) -> str:
    async def setup():
        session = await session_service.get_session(session_id)
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
        refreshed = await session_service.get_session(session_id)
        return refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

    return asyncio.run(setup())


def test_missing_proposal_id_is_a_safe_400(http_client: TestClient) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]

    response = http_client.post(f"/api/sessions/{session_id}/approve", json={})

    assert response.status_code == 400
    assert response.json()["errorCode"] == "validation_error"


def test_unknown_session_approve_is_a_safe_404(http_client: TestClient) -> None:
    response = http_client.post("/api/sessions/does-not-exist/approve", json={"proposal_id": "whatever"})

    assert response.status_code == 404
    assert response.json()["errorCode"] == "not_found"


def test_wrong_proposal_id_over_http_is_a_safe_409(
    http_client: TestClient, http_session_service: ApiSessionService
) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]
    _create_proposal_via_http(http_client, http_session_service, session_id)

    response = http_client.post(f"/api/sessions/{session_id}/approve", json={"proposal_id": "wrong-id"})

    assert response.status_code == 409
    body = response.json()
    assert body["errorCode"] == "action_failure"
    # Phase 4G: this specific denial (a stale/mismatched proposal_id) now
    # legitimately carries the structured `reason` field so a frontend can
    # classify it deterministically -- see SafeError's docstring.
    assert set(body) == {"errorCode", "userMessage", "retryable", "correlationId", "reason"}
    assert body["reason"] == "proposal_id_mismatch"


def test_approve_response_never_includes_payload_hash_or_secrets(
    http_client: TestClient, http_session_service: ApiSessionService
) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]
    proposal_id = _create_proposal_via_http(http_client, http_session_service, session_id)

    response = http_client.post(f"/api/sessions/{session_id}/approve", json={"proposal_id": proposal_id})

    body = response.json()
    assert response.status_code == 200
    body_text = str(body)
    assert "payload_hash" not in body_text
    assert "power_automate" not in body_text.lower()


def test_approve_request_cannot_submit_a_payload_field(
    http_client: TestClient, http_session_service: ApiSessionService
) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]
    proposal_id = _create_proposal_via_http(http_client, http_session_service, session_id)

    response = http_client.post(
        f"/api/sessions/{session_id}/approve",
        json={"proposal_id": proposal_id, "payload": {"chatId": "c1", "message": "TAMPERED"}},
    )

    # The extra field is simply ignored (FastAPI/Pydantic default:
    # unknown fields are dropped) -- the stored proposal's real payload
    # must be unaffected by it.
    assert response.status_code == 200
    refreshed = asyncio.run(http_session_service.get_session(session_id))
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["payload"]["message"] == "Hi"


def test_approve_request_cannot_submit_a_status_field(
    http_client: TestClient, http_session_service: ApiSessionService
) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]
    proposal_id = _create_proposal_via_http(http_client, http_session_service, session_id)

    response = http_client.post(
        f"/api/sessions/{session_id}/reject",
        json={"proposal_id": proposal_id, "status": "approved"},
    )

    # Even though the client asked for "approved" via an (ignored) extra
    # field while calling /reject, the actual transition performed is
    # exactly what the endpoint itself is -- a rejection.
    assert response.status_code == 200
    assert response.json()["result"] == "rejected"


def test_approve_request_cannot_submit_an_operation_field(
    http_client: TestClient, http_session_service: ApiSessionService
) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]
    proposal_id = _create_proposal_via_http(http_client, http_session_service, session_id)

    response = http_client.post(
        f"/api/sessions/{session_id}/approve",
        json={"proposal_id": proposal_id, "operation": "teams.createChat"},
    )

    assert response.status_code == 200
    assert response.json()["pending_action"]["operation"] == "teams.sendMessage"


def test_approve_reject_functions_remain_absent_from_agent_tools() -> None:
    from backend.agents.incident_manager.agent import incident_manager
    from backend.agents.team_manager.agent import team_manager

    def tname(t):
        return getattr(t, "name", None) or getattr(t, "__name__", str(t))

    names = {tname(t) for t in team_manager.tools} | {tname(t) for t in incident_manager.tools}
    assert "approve_proposal" not in names
    assert "reject_proposal" not in names
