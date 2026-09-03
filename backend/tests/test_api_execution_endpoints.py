"""Tests for the Phase 4G trusted execution-continuation endpoint
(`POST /api/sessions/{id}/execute`, backend/api/execution_service.py) --
both at the `execution_service` function level and through the full HTTP
stack via `TestClient`. Mirrors the structure and fixture conventions
already established in test_api_approval_endpoints.py and reuses
test_teams_execute_write.py's `_CountingGateway`/`FakeResponse` gateway-
mocking pattern for the Power Automate boundary.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api import execution_service
from backend.api.app import app
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.session_service import ApiSessionService, get_session_service
from backend.approval.service import (
    PENDING_ACTION_PROPOSAL_STATE_KEY,
    approve_proposal,
    create_action_proposal,
    load_active_proposal,
)
from backend.gateway import power_automate_client as pac_module
from backend.gateway.safe_error import SafeErrorException
from backend.tests._api_fakes import FakeRunner, simulate_proposal
from backend.tests._fakes import FakeResponse


class _CountingGateway:
    def __init__(self, response: FakeResponse) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response

    def __call__(self, url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        self.calls.append(json)
        return self._response


def _install_gateway(monkeypatch: pytest.MonkeyPatch, response: FakeResponse) -> _CountingGateway:
    spy = _CountingGateway(response)
    monkeypatch.setattr(pac_module.requests, "post", spy)
    return spy


async def _approved_create_chat(
    service: ApiSessionService,
    session_id: str,
    title: str = "Ops Bridge",
    members: list[str] | None = None,
    user_id: str = "api-user",
) -> str:
    session = await service.get_session(session_id, user_id)
    await simulate_proposal(
        service,
        session,
        "teams.createChat",
        {"title": title, "members": members or ["alice@example.com", "bob@example.com"]},
    )
    refreshed = await service.get_session(session_id, user_id)
    proposal_id = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]
    approve_proposal(proposal_id, refreshed.state)
    await service.persist_state_delta(
        refreshed, {PENDING_ACTION_PROPOSAL_STATE_KEY: refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
    )
    return proposal_id


async def _approved_send_message(
    service: ApiSessionService,
    session_id: str,
    chat_id: str = "c1",
    message: str = "Hello team",
    user_id: str = "api-user",
) -> str:
    session = await service.get_session(session_id, user_id)
    await simulate_proposal(service, session, "teams.sendMessage", {"chatId": chat_id, "message": message})
    refreshed = await service.get_session(session_id, user_id)
    proposal_id = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]
    approve_proposal(proposal_id, refreshed.state)
    await service.persist_state_delta(
        refreshed, {PENDING_ACTION_PROPOSAL_STATE_KEY: refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
    )
    return proposal_id


# --- Service-level: success ---------------------------------------------------


@pytest.mark.asyncio
async def test_execute_create_chat_success_consumes_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_gateway(monkeypatch, FakeResponse(200, {"id": "chat-123", "topic": "Ops Bridge", "webUrl": "https://teams/x"}))
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _approved_create_chat(service, session_id)

    response = await execution_service.execute(service, session_id, proposal_id)

    assert response.result == "executed"
    assert response.executed_action.chat_id == "chat-123"
    assert response.executed_action.title == "Ops Bridge"
    assert response.executed_action.web_url == "https://teams/x"
    assert response.pending_action.status == "consumed"

    refreshed = await service.get_session(session_id)
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "consumed"


@pytest.mark.asyncio
async def test_execute_send_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_gateway(monkeypatch, FakeResponse(200, {}))
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _approved_send_message(service, session_id, chat_id="c1", message="Hi team")

    response = await execution_service.execute(service, session_id, proposal_id)

    assert response.result == "executed"
    assert response.executed_action.chat_id == "c1"
    assert response.pending_action.status == "consumed"


# --- Service-level: denials ----------------------------------------------------


@pytest.mark.asyncio
async def test_execute_stale_proposal_id_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_a = await _approved_create_chat(service, session_id, title="First")
    # A second proposal supersedes the first -- proposal_a is no longer
    # the session's active proposal at all.
    session = await service.get_session(session_id)
    await simulate_proposal(service, session, "teams.createChat", {"title": "Second", "members": ["bob@example.com"]})

    with pytest.raises(SafeErrorException) as exc_info:
        await execution_service.execute(service, session_id, proposal_a)

    assert exc_info.value.safe_error.error_code == "action_failure"
    assert exc_info.value.safe_error.reason == "proposal_id_mismatch"
    assert spy.calls == []

    refreshed = await service.get_session(session_id)
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "pending"  # proposal B untouched


@pytest.mark.asyncio
async def test_execute_not_yet_approved_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await simulate_proposal(service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    refreshed = await service.get_session(session_id)
    proposal_id = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

    with pytest.raises(SafeErrorException) as exc_info:
        await execution_service.execute(service, session_id, proposal_id)

    assert exc_info.value.safe_error.reason == "proposal_not_approved"
    assert spy.calls == []


@pytest.mark.asyncio
async def test_execute_expired_denied_no_gateway_call(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    long_ago = datetime.now(timezone.utc) - timedelta(days=1)
    delta: dict[str, Any] = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, delta, now=long_ago)
    approve_proposal(proposal.proposal_id, delta)
    await service.persist_state_delta(session, delta)

    with pytest.raises(SafeErrorException) as exc_info:
        await execution_service.execute(service, session_id, proposal.proposal_id)

    assert exc_info.value.safe_error.reason == "proposal_expired"
    assert spy.calls == []


@pytest.mark.asyncio
async def test_execute_already_consumed_denies_double_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _approved_send_message(service, session_id)

    first = await execution_service.execute(service, session_id, proposal_id)
    assert first.result == "executed"
    assert len(spy.calls) == 1

    with pytest.raises(SafeErrorException) as exc_info:
        await execution_service.execute(service, session_id, proposal_id)

    assert exc_info.value.safe_error.reason == "proposal_consumed"
    assert len(spy.calls) == 1  # no second gateway call


@pytest.mark.asyncio
async def test_execute_no_pending_proposal_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    service = ApiSessionService()
    session_id = await service.create_session()

    with pytest.raises(SafeErrorException) as exc_info:
        await execution_service.execute(service, session_id, "does-not-exist")

    assert exc_info.value.safe_error.reason == "no_pending_proposal"
    assert spy.calls == []


# --- Service-level: gateway failure leaves the proposal re-executable --------


@pytest.mark.asyncio
async def test_execute_gateway_failure_leaves_proposal_approved_and_reexecutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Directly proves execute_write.py's own documented idempotency
    posture: a gateway failure never consumes the proposal, so the SAME
    proposal_id can be retried once the gateway is healthy again -- this
    module never invents its own retry, it just doesn't corrupt state on
    a failed attempt.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _approved_send_message(service, session_id)

    _install_gateway(monkeypatch, FakeResponse(500, {}))
    with pytest.raises(SafeErrorException) as exc_info:
        await execution_service.execute(service, session_id, proposal_id)
    assert exc_info.value.safe_error.error_code == "run_failure"

    refreshed = await service.get_session(session_id)
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "approved"  # NOT consumed

    _install_gateway(monkeypatch, FakeResponse(200, {}))
    second = await execution_service.execute(service, session_id, proposal_id)
    assert second.result == "executed"


@pytest.mark.asyncio
async def test_execute_timeout_is_also_a_run_failure_leaving_proposal_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests

    def _raise_timeout(*_args: Any, **_kwargs: Any) -> Any:
        raise requests.exceptions.Timeout("simulated timeout")

    monkeypatch.setattr(pac_module.requests, "post", _raise_timeout)
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _approved_send_message(service, session_id)

    with pytest.raises(SafeErrorException) as exc_info:
        await execution_service.execute(service, session_id, proposal_id)

    assert exc_info.value.safe_error.error_code == "run_failure"
    refreshed = await service.get_session(session_id)
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "approved"


# --- Ownership -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_foreign_session_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    service = ApiSessionService()
    session_id = await service.create_session("alice")
    proposal_id = await _approved_send_message(service, session_id, user_id="alice")

    with pytest.raises(SafeErrorException) as exc_info:
        await execution_service.execute(service, session_id, proposal_id, user_id="bob")

    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_execute_unknown_session_is_not_found() -> None:
    service = ApiSessionService()

    with pytest.raises(SafeErrorException) as exc_info:
        await execution_service.execute(service, "does-not-exist", "whatever")

    assert exc_info.value.safe_error.error_code == "not_found"


# --- Structural ------------------------------------------------------------


def test_execution_service_dispatches_via_run_in_threadpool() -> None:
    source = inspect.getsource(execution_service)
    assert "run_in_threadpool" in source


def test_execution_response_never_includes_payload_hash() -> None:
    source = inspect.getsource(execution_service)
    assert "payload_hash" not in source


# --- HTTP level ----------------------------------------------------------------


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


def _create_approved_proposal_via_http(client: TestClient, session_service: ApiSessionService, session_id: str) -> str:
    async def setup():
        session = await session_service.get_session(session_id)
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
        refreshed = await session_service.get_session(session_id)
        proposal_id = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]
        return proposal_id

    proposal_id = asyncio.run(setup())
    response = client.post(f"/api/sessions/{session_id}/approve", json={"proposal_id": proposal_id})
    assert response.status_code == 200
    return proposal_id


def test_execute_endpoint_never_calls_team_manager_runner(
    http_client: TestClient, http_session_service: ApiSessionService, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_gateway(monkeypatch, FakeResponse(200, {}))

    class TrackingRunner:
        called = False

        async def run_async(self, **_kwargs: Any):
            TrackingRunner.called = True
            if False:
                yield  # pragma: no cover -- makes this an async generator

    app.dependency_overrides[get_chat_service] = lambda: ChatService(http_session_service, runner=TrackingRunner())

    session_id = http_client.post("/api/sessions").json()["session_id"]
    proposal_id = _create_approved_proposal_via_http(http_client, http_session_service, session_id)

    response = http_client.post(f"/api/sessions/{session_id}/execute", json={"proposal_id": proposal_id})

    assert response.status_code == 200
    assert TrackingRunner.called is False


def test_execute_response_never_includes_payload_hash_or_secrets(
    http_client: TestClient, http_session_service: ApiSessionService, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1", "webUrl": "https://teams/x"}))
    session_id = http_client.post("/api/sessions").json()["session_id"]
    proposal_id = _create_approved_proposal_via_http(http_client, http_session_service, session_id)

    response = http_client.post(f"/api/sessions/{session_id}/execute", json={"proposal_id": proposal_id})

    body_text = response.text.lower()
    assert "payload_hash" not in body_text
    assert "power_automate" not in body_text
    assert "sas" not in body_text


def test_missing_proposal_id_execute_is_a_safe_400(http_client: TestClient) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]

    response = http_client.post(f"/api/sessions/{session_id}/execute", json={})

    assert response.status_code == 400
    assert response.json()["errorCode"] == "validation_error"


def test_unknown_session_execute_is_a_safe_404(http_client: TestClient) -> None:
    response = http_client.post("/api/sessions/does-not-exist/execute", json={"proposal_id": "whatever"})

    assert response.status_code == 404
    assert response.json()["errorCode"] == "not_found"


def test_execute_before_approve_is_a_safe_409_over_http(
    http_client: TestClient, http_session_service: ApiSessionService
) -> None:
    async def setup():
        session = await http_session_service.get_session(session_id)
        await simulate_proposal(http_session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
        refreshed = await http_session_service.get_session(session_id)
        return refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

    session_id = http_client.post("/api/sessions").json()["session_id"]
    proposal_id = asyncio.run(setup())

    response = http_client.post(f"/api/sessions/{session_id}/execute", json={"proposal_id": proposal_id})

    assert response.status_code == 409
    body = response.json()
    assert body["errorCode"] == "action_failure"
    assert body["reason"] == "proposal_not_approved"
