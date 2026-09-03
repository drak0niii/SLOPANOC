"""HTTP-level integration tests for backend/api/app.py, via FastAPI's
`TestClient` (no real network/server process, no real Gemini call --
`get_chat_service` is overridden with a `ChatService` bound to
`FakeRunner`/error-injecting fakes through FastAPI's own dependency
override mechanism).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.session_service import ApiSessionService, get_session_service
from backend.tests._api_fakes import FakeRunner, RaisingRunner, simulate_proposal


@pytest.fixture()
def session_service() -> ApiSessionService:
    return ApiSessionService()


@pytest.fixture()
def client(session_service: ApiSessionService) -> TestClient:
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(
        session_service, runner=FakeRunner(session_service)
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_health_endpoint() -> None:
    with TestClient(app) as c:
        response = c.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_endpoint_exposes_no_infrastructure_detail() -> None:
    with TestClient(app) as c:
        response = c.get("/health")
    body = response.json()
    assert set(body) == {"status"}


def test_create_session_returns_a_session_id(client: TestClient) -> None:
    response = client.post("/api/sessions")
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"session_id"}
    assert isinstance(body["session_id"], str) and body["session_id"]


def test_create_session_rejects_a_client_supplied_body(client: TestClient) -> None:
    """The endpoint takes no request body -- even if a client sends one
    (e.g. attempting to inject state), it is simply ignored by FastAPI's
    routing (no request-body parameter is declared for this route), never
    interpreted as initial ADK state.
    """
    response = client.post("/api/sessions", json={"state": {"selected_teams_chat_id": "c1"}})
    assert response.status_code == 201
    session_id = response.json()["session_id"]

    chat_response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hi"})
    assert chat_response.json()["pending_action"] is None


def test_chat_endpoint_returns_the_assistant_message(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]

    response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["message"] == {"role": "assistant", "content": "echo: hello"}
    assert body["pending_action"] is None


def test_same_session_reused_across_two_http_turns(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]

    client.post(f"/api/sessions/{session_id}/messages", json={"message": "turn one"})
    second = client.post(f"/api/sessions/{session_id}/messages", json={"message": "turn two"})

    assert second.json()["session_id"] == session_id
    assert second.json()["message"]["content"] == "echo: turn two"


def test_unknown_session_returns_a_safe_404(client: TestClient) -> None:
    response = client.post("/api/sessions/does-not-exist/messages", json={"message": "hi"})

    assert response.status_code == 404
    body = response.json()
    assert body["errorCode"] == "not_found"
    assert set(body) == {"errorCode", "userMessage", "retryable", "correlationId"}


def test_malformed_request_missing_message_field_returns_a_safe_400(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]

    response = client.post(f"/api/sessions/{session_id}/messages", json={})

    assert response.status_code == 400
    body = response.json()
    assert body["errorCode"] == "validation_error"
    assert set(body) == {"errorCode", "userMessage", "retryable", "correlationId"}


def test_malformed_request_empty_message_returns_a_safe_400(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]

    response = client.post(f"/api/sessions/{session_id}/messages", json={"message": ""})

    assert response.status_code == 400
    assert response.json()["errorCode"] == "validation_error"


def test_internal_exception_is_converted_to_a_safe_error(session_service: ApiSessionService) -> None:
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(
        session_service, runner=RaisingRunner(RuntimeError("boom: secret detail leaked here"))
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            session_id = c.post("/api/sessions").json()["session_id"]
            response = c.post(f"/api/sessions/{session_id}/messages", json={"message": "hi"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code in (500, 502)
    body = response.json()
    assert "boom" not in response.text
    assert "secret detail" not in response.text
    assert "Traceback" not in response.text
    assert set(body) == {"errorCode", "userMessage", "retryable", "correlationId"}


def test_pending_action_is_null_when_nothing_is_proposed(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]

    response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "just chatting"})

    assert response.json()["pending_action"] is None


def test_pending_action_is_populated_when_a_proposal_exists(session_service: ApiSessionService) -> None:
    async def side_effect(svc, session, text):
        await simulate_proposal(svc, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(
        session_service, runner=FakeRunner(session_service, side_effect=side_effect)
    )
    try:
        with TestClient(app) as c:
            session_id = c.post("/api/sessions").json()["session_id"]
            response = c.post(f"/api/sessions/{session_id}/messages", json={"message": "send hi to c1"})
    finally:
        app.dependency_overrides.clear()

    pending = response.json()["pending_action"]
    assert pending is not None
    assert pending["operation"] == "teams.sendMessage"
    assert pending["chat_id"] == "c1"
    assert pending["message"] == "Hi"
    assert "payload_hash" not in pending
