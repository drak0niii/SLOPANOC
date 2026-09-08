"""HTTP-level tests for POST-5.1 B4B's three new routes:
`GET /api/sessions`, `GET /api/sessions/{id}/history`,
`PATCH /api/sessions/{id}` -- via FastAPI's `TestClient`, mirroring
`test_api_attachment_endpoints.py`/`test_api_case_endpoints.py`'s own
dependency-override + `X-SLOPANOC-DEV-USER` multi-identity pattern. No
real Cloud SQL/GCS here -- in-memory ADK sessions + in-memory SQLite
attachment repository, exactly like every other offline HTTP test in
this suite.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.identity import DEV_USER_HEADER_NAME
from backend.api.session_service import ApiSessionService, get_session_service
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService, get_attachment_service
from backend.tests._api_fakes import FakeRunner, append_user_turn

ALICE = {DEV_USER_HEADER_NAME: "alice"}
MALLORY = {DEV_USER_HEADER_NAME: "mallory"}


@pytest.fixture()
def session_service() -> ApiSessionService:
    return ApiSessionService()


@pytest.fixture()
def attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


@pytest.fixture()
def client(session_service: ApiSessionService, attachment_service: AttachmentService):
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_attachment_service] = lambda: attachment_service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(session_service, runner=FakeRunner(session_service))
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_session(client: TestClient, headers: dict) -> str:
    response = client.post("/api/sessions", headers=headers)
    assert response.status_code == 201
    return response.json()["session_id"]


def _send_message(client: TestClient, session_id: str, text: str, headers: dict) -> None:
    response = client.post(f"/api/sessions/{session_id}/messages", json={"message": text}, headers=headers)
    assert response.status_code == 200


async def _seed_real_turn(
    session_service: ApiSessionService,
    session_id: str,
    user_id: str,
    invocation_id: str,
    user_text: str,
    assistant_text: str,
) -> None:
    """`FakeRunner` (used for the plain send-message/list tests above)
    deliberately never appends REAL conversational content events -- only
    one empty-delta bookkeeping event (see its own docstring: "mirroring
    the fact that a real Runner turn always records at least one Event").
    History-projection tests need REAL `content.role`/`invocation_id`
    shaped events, so they seed them directly via the same
    `append_user_turn` helper `test_session_history_service.py`/
    `test_chat_service_rewind.py` already use -- exercising the actual
    history endpoint's projection logic against real ADK event shapes,
    independent of whichever Runner double the send-message route itself
    uses in this file.
    """
    session = await session_service.get_session(session_id, user_id)
    session = await append_user_turn(session_service, session, invocation_id, user_text, assistant_text)
    from backend.api.session_state_keys import record_user_turn_activity

    session = await session_service.get_session(session_id, user_id)
    await record_user_turn_activity(session_service, session, user_text, invocation_id)


# --- GET /api/sessions -------------------------------------------------


def test_list_sessions_excludes_a_brand_new_empty_session(client: TestClient) -> None:
    _create_session(client, ALICE)

    response = client.get("/api/sessions", headers=ALICE)
    assert response.status_code == 200
    assert response.json()["sessions"] == []


def test_list_sessions_includes_a_session_after_a_real_message(client: TestClient) -> None:
    session_id = _create_session(client, ALICE)
    _send_message(client, session_id, "hello there", ALICE)

    response = client.get("/api/sessions", headers=ALICE)
    body = response.json()
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["session_id"] == session_id
    assert body["sessions"][0]["title"] == "hello there"
    assert set(body["sessions"][0].keys()) == {"session_id", "title", "updated_at"}


def test_list_sessions_never_shows_another_users_sessions(client: TestClient) -> None:
    session_id = _create_session(client, ALICE)
    _send_message(client, session_id, "alice's private message", ALICE)

    response = client.get("/api/sessions", headers=MALLORY)
    assert response.json()["sessions"] == []


# --- GET /api/sessions/{id}/history --------------------------------------


@pytest.mark.asyncio
async def test_get_history_returns_user_and_assistant_text(client: TestClient, session_service: ApiSessionService) -> None:
    session_id = _create_session(client, ALICE)
    await _seed_real_turn(session_service, session_id, "alice", "inv-1", "hello", "hi there")

    response = client.get(f"/api/sessions/{session_id}/history", headers=ALICE)
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["text"] == "hello"
    assert body["messages"][1]["text"] == "hi there"


@pytest.mark.asyncio
async def test_get_history_message_ids_are_role_suffixed_and_share_a_turn_id(
    client: TestClient, session_service: ApiSessionService
) -> None:
    session_id = _create_session(client, ALICE)
    await _seed_real_turn(session_service, session_id, "alice", "inv-1", "hello", "hi there")

    body = client.get(f"/api/sessions/{session_id}/history", headers=ALICE).json()
    user_msg, assistant_msg = body["messages"]
    assert user_msg["turn_id"] == assistant_msg["turn_id"]
    assert user_msg["message_id"] == f"{user_msg['turn_id']}:user"
    assert assistant_msg["message_id"] == f"{assistant_msg['turn_id']}:assistant"
    assert user_msg["message_id"] != assistant_msg["message_id"]


def test_get_history_denies_another_users_session_like_not_found(client: TestClient) -> None:
    session_id = _create_session(client, ALICE)
    _send_message(client, session_id, "private", ALICE)

    response = client.get(f"/api/sessions/{session_id}/history", headers=MALLORY)
    assert response.status_code == 404
    body = response.json()
    assert set(body.keys()) == {"errorCode", "userMessage", "retryable", "correlationId"}


def test_get_history_denies_unknown_session(client: TestClient) -> None:
    response = client.get("/api/sessions/does-not-exist/history", headers=ALICE)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_history_response_never_leaks_raw_state_or_internal_fields(
    client: TestClient, session_service: ApiSessionService
) -> None:
    session_id = _create_session(client, ALICE)
    await _seed_real_turn(session_service, session_id, "alice", "inv-1", "hello", "hi there")

    body = client.get(f"/api/sessions/{session_id}/history", headers=ALICE).json()
    assert set(body.keys()) == {"session_id", "messages"}
    assert len(body["messages"]) == 2
    for message in body["messages"]:
        # B7 corrective pass -- `source`/`knowledge_sources` are new,
        # intentional additions (durable, turn-owned provenance -- see
        # backend/api/turn_source_references.py); this turn produced
        # neither, so `source` serializes as `null` and `knowledge_sources`
        # as `[]`, proving the allow-list-widening itself introduces no
        # fabricated evidence for a turn that never had any.
        assert set(message.keys()) == {
            "message_id",
            "turn_id",
            "role",
            "text",
            "created_at",
            "attachments",
            "source",
            "knowledge_sources",
        }
        assert message["source"] is None
        assert message["knowledge_sources"] == []


# --- PATCH /api/sessions/{id} -------------------------------------------


def test_rename_session_persists_and_is_reflected_in_the_list(client: TestClient) -> None:
    session_id = _create_session(client, ALICE)
    _send_message(client, session_id, "hello", ALICE)

    response = client.patch(f"/api/sessions/{session_id}", json={"title": "My renamed chat"}, headers=ALICE)
    assert response.status_code == 200
    assert response.json()["title"] == "My renamed chat"

    listed = client.get("/api/sessions", headers=ALICE).json()["sessions"]
    assert listed[0]["title"] == "My renamed chat"


def test_rename_rejects_empty_title(client: TestClient) -> None:
    session_id = _create_session(client, ALICE)

    response = client.patch(f"/api/sessions/{session_id}", json={"title": "   "}, headers=ALICE)
    assert response.status_code in (400, 422)


def test_rename_of_another_users_session_is_denied(client: TestClient) -> None:
    session_id = _create_session(client, ALICE)

    response = client.patch(f"/api/sessions/{session_id}", json={"title": "hijacked"}, headers=MALLORY)
    assert response.status_code == 404


def test_rename_request_schema_has_exactly_one_field(client: TestClient) -> None:
    from backend.api.schemas import RenameSessionRequest

    assert set(RenameSessionRequest.model_fields) == {"title"}
