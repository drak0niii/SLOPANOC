"""HTTP-level tests for the Case API (instruction sections 28-33, 42),
via FastAPI's `TestClient`. `get_session_service`/`get_case_service`/
`get_chat_service` are overridden with fresh, isolated instances per
test through FastAPI's dependency-override mechanism -- no real Gemini
call, no real database file.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.identity import DEV_USER_HEADER_NAME
from backend.api.session_service import ApiSessionService, get_session_service
from backend.cases.service import CaseService, get_case_service
from backend.tests._api_fakes import FakeRunner

ALICE_HEADERS = {DEV_USER_HEADER_NAME: "alice"}
BOB_HEADERS = {DEV_USER_HEADER_NAME: "bob"}
EVE_HEADERS = {DEV_USER_HEADER_NAME: "eve"}


@pytest.fixture()
def session_service() -> ApiSessionService:
    return ApiSessionService()


@pytest.fixture()
def case_service() -> CaseService:
    return CaseService()


@pytest.fixture()
def client(session_service: ApiSessionService, case_service: CaseService) -> TestClient:
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_case_service] = lambda: case_service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(
        session_service, runner=FakeRunner(session_service), case_service=case_service
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_case(client: TestClient, headers: dict) -> dict:
    response = client.post(
        "/api/cases",
        json={"title": "Packet loss", "problem_statement": "Users reporting packet loss."},
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()


# --- Case CRUD ---------------------------------------------------------


def test_create_case_returns_case_with_server_supplied_fields(client: TestClient) -> None:
    response = client.post(
        "/api/cases",
        json={"title": "Packet loss", "problem_statement": "Loss on core router", "external_reference": "TCK-1"},
        headers=ALICE_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Packet loss"
    assert body["status"] == "open"
    assert body["created_by_user_id"] == "alice"
    assert "case_id" in body and body["case_id"]


def test_client_cannot_supply_ownership_fields(client: TestClient) -> None:
    """Extra fields (`created_by_user_id`, `case_id`, `status`) in the
    request body are simply ignored -- the schema has no such fields.
    """
    response = client.post(
        "/api/cases",
        json={
            "title": "T",
            "problem_statement": "P",
            "case_id": "attacker-chosen-id",
            "created_by_user_id": "someone-else",
            "status": "resolved",
        },
        headers=ALICE_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["case_id"] != "attacker-chosen-id"
    assert body["created_by_user_id"] == "alice"
    assert body["status"] == "open"


def test_get_case_by_id(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    response = client.get(f"/api/cases/{case['case_id']}", headers=ALICE_HEADERS)
    assert response.status_code == 200
    assert response.json()["case_id"] == case["case_id"]


def test_list_cases_returns_only_my_cases(client: TestClient) -> None:
    my_case = _create_case(client, ALICE_HEADERS)
    _create_case(client, BOB_HEADERS)

    response = client.get("/api/cases", headers=ALICE_HEADERS)

    assert response.status_code == 200
    case_ids = [c["case_id"] for c in response.json()["cases"]]
    assert case_ids == [my_case["case_id"]]


def test_patch_case_updates_status(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    response = client.patch(f"/api/cases/{case['case_id']}", json={"status": "monitoring"}, headers=ALICE_HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "monitoring"


def test_patch_case_rejects_an_arbitrary_status_value(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    response = client.patch(f"/api/cases/{case['case_id']}", json={"status": "not-a-real-status"}, headers=ALICE_HEADERS)
    assert response.status_code == 400


# --- Ownership / access control -----------------------------------------


def test_foreign_case_returns_404_not_found(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    response = client.get(f"/api/cases/{case['case_id']}", headers=EVE_HEADERS)
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"errorCode", "userMessage", "retryable", "correlationId"}


def test_no_ownership_metadata_leaks_for_foreign_case(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    response = client.get(f"/api/cases/{case['case_id']}", headers=EVE_HEADERS)
    assert "alice" not in response.text.lower()


def test_unknown_case_id_also_returns_404(client: TestClient) -> None:
    response = client.get("/api/cases/does-not-exist", headers=ALICE_HEADERS)
    assert response.status_code == 404


def test_non_member_cannot_add_context(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    response = client.post(
        f"/api/cases/{case['case_id']}/context",
        json={"kind": "observation", "content": "sneaky"},
        headers=EVE_HEADERS,
    )
    assert response.status_code == 404


def test_non_member_cannot_list_context(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    response = client.get(f"/api/cases/{case['case_id']}/context", headers=EVE_HEADERS)
    assert response.status_code == 404


def test_non_owner_cannot_add_members(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    client.post(f"/api/cases/{case['case_id']}/members", json={"user_id": "bob"}, headers=ALICE_HEADERS)

    response = client.post(f"/api/cases/{case['case_id']}/members", json={"user_id": "eve"}, headers=BOB_HEADERS)

    assert response.status_code == 403


# --- Session linking (instruction sections 31, 38) --------------------------


def test_user_links_own_session_to_accessible_case(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    session_id = client.post("/api/sessions", headers=ALICE_HEADERS).json()["session_id"]

    response = client.post(f"/api/cases/{case['case_id']}/sessions/{session_id}", headers=ALICE_HEADERS)

    assert response.status_code == 201
    assert response.json()["session_id"] == session_id


def test_user_cannot_link_another_users_session(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    client.post(f"/api/cases/{case['case_id']}/members", json={"user_id": "bob"}, headers=ALICE_HEADERS)
    bob_session_id = client.post("/api/sessions", headers=BOB_HEADERS).json()["session_id"]

    # alice is a member of the case but does not own bob's session.
    response = client.post(f"/api/cases/{case['case_id']}/sessions/{bob_session_id}", headers=ALICE_HEADERS)

    assert response.status_code == 404


def test_non_member_cannot_link_to_case(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    eve_session_id = client.post("/api/sessions", headers=EVE_HEADERS).json()["session_id"]

    response = client.post(f"/api/cases/{case['case_id']}/sessions/{eve_session_id}", headers=EVE_HEADERS)

    assert response.status_code == 404


def test_session_cannot_silently_belong_to_two_cases(client: TestClient) -> None:
    case_a = _create_case(client, ALICE_HEADERS)
    case_b = client.post(
        "/api/cases", json={"title": "Case B", "problem_statement": "Problem B"}, headers=ALICE_HEADERS
    ).json()
    session_id = client.post("/api/sessions", headers=ALICE_HEADERS).json()["session_id"]
    client.post(f"/api/cases/{case_a['case_id']}/sessions/{session_id}", headers=ALICE_HEADERS)

    response = client.post(f"/api/cases/{case_b['case_id']}/sessions/{session_id}", headers=ALICE_HEADERS)

    assert response.status_code == 409


def test_explicit_unlink_endpoint_works(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    session_id = client.post("/api/sessions", headers=ALICE_HEADERS).json()["session_id"]
    client.post(f"/api/cases/{case['case_id']}/sessions/{session_id}", headers=ALICE_HEADERS)

    response = client.delete(f"/api/cases/{case['case_id']}/sessions/{session_id}", headers=ALICE_HEADERS)

    assert response.status_code == 204


# --- Context ledger over HTTP -----------------------------------------------


def test_add_context_item_records_user_as_source(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    response = client.post(
        f"/api/cases/{case['case_id']}/context",
        json={"kind": "observation", "content": "Loss started 14:00 UTC."},
        headers=ALICE_HEADERS,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["source_type"] == "user"
    assert body["source_author"] == "alice"


def test_client_cannot_forge_trusted_provenance() -> None:
    """The request schema (`AddCaseContextItemRequest`) has no field for
    source_type/source_author/source_ref at all.
    """
    from backend.api.schemas import AddCaseContextItemRequest

    assert set(AddCaseContextItemRequest.model_fields) == {"kind", "content"}


def test_client_cannot_forge_another_users_authorship(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    response = client.post(
        f"/api/cases/{case['case_id']}/context",
        json={"kind": "observation", "content": "x", "source_author": "someone-else", "created_by_user_id": "someone-else"},
        headers=ALICE_HEADERS,
    )
    assert response.status_code == 201
    assert response.json()["source_author"] == "alice"


# --- Case-aware chat response (instruction section 32) ----------------------


def test_chat_response_active_case_is_null_when_not_linked(client: TestClient) -> None:
    session_id = client.post("/api/sessions", headers=ALICE_HEADERS).json()["session_id"]
    response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hi"}, headers=ALICE_HEADERS)
    assert response.json()["active_case"] is None


def test_chat_response_active_case_is_populated_when_linked(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    session_id = client.post("/api/sessions", headers=ALICE_HEADERS).json()["session_id"]
    client.post(f"/api/cases/{case['case_id']}/sessions/{session_id}", headers=ALICE_HEADERS)

    response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hi"}, headers=ALICE_HEADERS)

    active_case = response.json()["active_case"]
    assert active_case is not None
    assert active_case["case_id"] == case["case_id"]
    assert active_case["title"] == "Packet loss"


def test_chat_response_never_returns_the_full_case_ledger(client: TestClient) -> None:
    case = _create_case(client, ALICE_HEADERS)
    session_id = client.post("/api/sessions", headers=ALICE_HEADERS).json()["session_id"]
    client.post(f"/api/cases/{case['case_id']}/sessions/{session_id}", headers=ALICE_HEADERS)
    client.post(
        f"/api/cases/{case['case_id']}/context",
        json={"kind": "observation", "content": "UNIQUE_MARKER_CONTENT"},
        headers=ALICE_HEADERS,
    )

    response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hi"}, headers=ALICE_HEADERS)

    assert "UNIQUE_MARKER_CONTENT" not in response.text
    assert set(response.json()["active_case"]) == {"case_id", "title", "status"}


# --- Fresh multi-user collaboration (instruction sections 33, 42) ----------


def test_full_multi_user_shared_case_context_flow(client: TestClient) -> None:
    """1. User A creates Case. 2. User A adds User B. 3. User A and User B
    each attach a different Session. 4. Session B loads Case context
    (via the chat response's active_case + the context endpoint -- the
    actual snapshot-in-prompt injection is tested separately at the
    instruction-provider level, since it never appears in the HTTP
    response). 5. User A appends new Case context. 6. User B (via the
    context endpoint, standing in for "next turn") sees the new item.
    Conversation histories remain fully separate throughout.
    """
    # 1.
    case = _create_case(client, ALICE_HEADERS)
    # 2.
    add_member = client.post(f"/api/cases/{case['case_id']}/members", json={"user_id": "bob"}, headers=ALICE_HEADERS)
    assert add_member.status_code == 201
    # 3.
    session_a = client.post("/api/sessions", headers=ALICE_HEADERS).json()["session_id"]
    session_b = client.post("/api/sessions", headers=BOB_HEADERS).json()["session_id"]
    assert client.post(f"/api/cases/{case['case_id']}/sessions/{session_a}", headers=ALICE_HEADERS).status_code == 201
    assert client.post(f"/api/cases/{case['case_id']}/sessions/{session_b}", headers=BOB_HEADERS).status_code == 201

    # 4. (before any new context -- empty ledger)
    before = client.get(f"/api/cases/{case['case_id']}/context", headers=BOB_HEADERS).json()
    assert before["items"] == []

    # Separate conversation histories: each session's chat only reflects
    # its own turns.
    client.post(f"/api/sessions/{session_a}/messages", json={"message": "alice's message"}, headers=ALICE_HEADERS)
    client.post(f"/api/sessions/{session_b}/messages", json={"message": "bob's message"}, headers=BOB_HEADERS)

    # 5.
    added = client.post(
        f"/api/cases/{case['case_id']}/context",
        json={"kind": "observation", "content": "New evidence from Alice."},
        headers=ALICE_HEADERS,
    )
    assert added.status_code == 201

    # 6. Bob's next context load sees it -- no session restart needed.
    after = client.get(f"/api/cases/{case['case_id']}/context", headers=BOB_HEADERS).json()
    assert len(after["items"]) == 1
    assert after["items"][0]["content"] == "New evidence from Alice."

    # Both sessions' active_case still correctly resolves for each user.
    chat_a = client.post(f"/api/sessions/{session_a}/messages", json={"message": "status?"}, headers=ALICE_HEADERS)
    chat_b = client.post(f"/api/sessions/{session_b}/messages", json={"message": "status?"}, headers=BOB_HEADERS)
    assert chat_a.json()["active_case"]["case_id"] == case["case_id"]
    assert chat_b.json()["active_case"]["case_id"] == case["case_id"]
