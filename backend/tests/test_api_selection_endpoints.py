"""Tests for the interactive Teams chat-name selection endpoints -- both
at the `selection_service` function level and through the full HTTP
stack via `TestClient`. Mirrors test_api_approval_endpoints.py's own
combined structure.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.api import selection_service
from backend.api.app import app
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.session_service import ApiSessionService, get_session_service
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY
from backend.gateway.safe_error import SafeErrorException
from backend.selection.read_resume import GENERIC_SUMMARY_RESUME_TEXT
from backend.selection.schemas import PendingReadIntent, SelectionKind
from backend.selection.service import PENDING_SELECTION_STATE_KEY, create_pending_selection
from backend.tests._api_fakes import FakeRunner
from backend.tools.teams.schemas import ChatSummary


def _candidates() -> list[ChatSummary]:
    return [
        ChatSummary(chat_id="chat-real-1", title="Project Falcon Room Test"),
        ChatSummary(chat_id="chat-real-2", title="Project Falcon Test"),
        ChatSummary(chat_id="chat-real-3", title="Project Falcon Operations"),
    ]


async def _seed_selection(
    service: ApiSessionService,
    session_id: str,
    pending_write_message: str | None = None,
    pending_read_intent: PendingReadIntent | None = None,
):
    session = await service.get_session(session_id)
    selection = create_pending_selection(
        SelectionKind.TEAMS_CHAT,
        "Project Falcon Room",
        _candidates(),
        session.state,
        pending_write_message=pending_write_message,
        pending_read_intent=pending_read_intent if pending_write_message is None else None,
    )
    await service.persist_state_delta(session, {PENDING_SELECTION_STATE_KEY: session.state[PENDING_SELECTION_STATE_KEY]})
    return selection


# --- CHOOSE (service level) --------------------------------------------------


@pytest.mark.asyncio
async def test_choose_a_write_intent_creates_a_proposal_using_the_real_chat_id() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, pending_write_message="that this is a test")
    chosen = selection.options[1]  # -> chat-real-2 / "Project Falcon Test"

    response = await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)

    assert response.status == "resolved"
    assert response.selected_label == "Project Falcon Test"
    assert response.pending_action is not None
    assert response.pending_action.operation == "teams.sendMessage"
    assert response.pending_action.message == "that this is a test"
    assert response.pending_action.target_display_name == "Project Falcon Test"

    refreshed = await service.get_session(session_id)
    stored_proposal = refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]
    assert stored_proposal["payload"]["chatId"] == "chat-real-2"
    assert stored_proposal["payload"]["message"] == "that this is a test"


@pytest.mark.asyncio
async def test_choose_updates_the_authoritative_selected_teams_state() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, pending_write_message="hi")
    chosen = selection.options[0]  # -> chat-real-1 / "Project Falcon Room Test"

    await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)

    refreshed = await service.get_session(session_id)
    assert refreshed.state["selected_teams_chat_id"] == "chat-real-1"
    assert refreshed.state["selected_teams_chat_topic"] == "Project Falcon Room Test"


@pytest.mark.asyncio
async def test_choose_a_read_intent_never_creates_a_proposal() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, pending_write_message=None)
    chosen = selection.options[0]

    response = await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)

    assert response.pending_action is None
    refreshed = await service.get_session(session_id)
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in refreshed.state


@pytest.mark.asyncio
async def test_choose_a_write_intent_returns_no_resume_message() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, pending_write_message="hi")
    chosen = selection.options[0]

    response = await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)

    assert response.pending_action is not None
    assert response.resume_message is None


@pytest.mark.asyncio
async def test_choose_a_read_intent_with_a_stored_question_returns_it_verbatim_as_resume_message() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(
        service,
        session_id,
        pending_read_intent=PendingReadIntent(question="What are the open action items?"),
    )
    chosen = selection.options[0]

    response = await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)

    assert response.pending_action is None
    assert response.resume_message == "What are the open action items?"


@pytest.mark.asyncio
async def test_choose_a_plain_summary_read_intent_returns_the_generic_fallback_resume_message() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, pending_read_intent=PendingReadIntent())
    chosen = selection.options[0]

    response = await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)

    assert response.resume_message == GENERIC_SUMMARY_RESUME_TEXT
    # Never the original ambiguous requested_value.
    assert "Project Falcon Room" not in response.resume_message


@pytest.mark.asyncio
async def test_choose_marks_the_selection_resolved() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id)

    await selection_service.choose(service, session_id, selection.selection_id, selection.options[0].option_id)

    refreshed = await service.get_session(session_id)
    assert refreshed.state[PENDING_SELECTION_STATE_KEY]["status"] == "resolved"


@pytest.mark.asyncio
async def test_choose_never_calls_the_runner() -> None:
    """Structural/behavioral proof: deterministic completion, no LLM
    involvement -- a runner whose run_async would fail the test if ever
    invoked is never touched by this call at all.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, pending_write_message="hi")

    # No ChatService/Runner is even constructed in this test -- proving
    # selection_service.choose has no dependency on either.
    response = await selection_service.choose(
        service, session_id, selection.selection_id, selection.options[0].option_id
    )
    assert response.pending_action is not None


# --- Security ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_choose_unknown_session_is_not_found() -> None:
    service = ApiSessionService()
    with pytest.raises(SafeErrorException) as exc_info:
        await selection_service.choose(service, "does-not-exist", "sel-1", "opt-1")
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_choose_a_foreign_session_is_indistinguishable_from_unknown() -> None:
    service = ApiSessionService()
    session_id = await service.create_session(user_id="alice")
    with pytest.raises(SafeErrorException) as exc_info:
        await selection_service.choose(service, session_id, "sel-1", "opt-1", user_id="mallory")
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_choose_wrong_selection_id_is_denied() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id)

    with pytest.raises(SafeErrorException) as exc_info:
        await selection_service.choose(service, session_id, "some-other-selection-id", selection.options[0].option_id)
    assert exc_info.value.safe_error.reason == "selection_id_mismatch"


@pytest.mark.asyncio
async def test_choose_invalid_option_id_is_denied() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id)

    with pytest.raises(SafeErrorException) as exc_info:
        await selection_service.choose(service, session_id, selection.selection_id, "not-a-real-option")
    assert exc_info.value.safe_error.reason == "option_not_found"


@pytest.mark.asyncio
async def test_raw_teams_chat_id_is_never_accepted_as_a_substitute_for_option_id() -> None:
    """The frontend never has a raw chat id to send in the first place,
    but this proves the server would reject it even if it tried."""
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id)

    with pytest.raises(SafeErrorException) as exc_info:
        await selection_service.choose(service, session_id, selection.selection_id, "chat-real-1")
    assert exc_info.value.safe_error.reason == "option_not_found"


@pytest.mark.asyncio
async def test_choosing_an_already_resolved_selection_is_denied_reuse() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id)
    await selection_service.choose(service, session_id, selection.selection_id, selection.options[0].option_id)

    with pytest.raises(SafeErrorException) as exc_info:
        await selection_service.choose(service, session_id, selection.selection_id, selection.options[1].option_id)
    assert exc_info.value.safe_error.reason == "selection_not_pending"


@pytest.mark.asyncio
async def test_choosing_a_skipped_selection_is_denied_reuse() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id)
    await selection_service.skip(service, session_id, selection.selection_id)

    with pytest.raises(SafeErrorException) as exc_info:
        await selection_service.choose(service, session_id, selection.selection_id, selection.options[0].option_id)
    assert exc_info.value.safe_error.reason == "selection_not_pending"


@pytest.mark.asyncio
async def test_option_from_a_different_selection_is_rejected() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    first = await _seed_selection(service, session_id)
    foreign_option_id = first.options[0].option_id
    second = await _seed_selection(service, session_id)  # replaces the first as active

    with pytest.raises(SafeErrorException) as exc_info:
        await selection_service.choose(service, session_id, second.selection_id, foreign_option_id)
    assert exc_info.value.safe_error.reason == "option_not_found"


# --- SKIP (service level) ----------------------------------------------------


@pytest.mark.asyncio
async def test_skip_never_creates_a_proposal_or_selects_anything() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, pending_write_message="hi")

    response = await selection_service.skip(service, session_id, selection.selection_id)

    assert response.status == "skipped"
    refreshed = await service.get_session(session_id)
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in refreshed.state
    assert "selected_teams_chat_id" not in refreshed.state


@pytest.mark.asyncio
async def test_skip_unknown_session_is_not_found() -> None:
    service = ApiSessionService()
    with pytest.raises(SafeErrorException) as exc_info:
        await selection_service.skip(service, "does-not-exist", "sel-1")
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_skipping_an_already_skipped_selection_is_denied_reuse() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id)
    await selection_service.skip(service, session_id, selection.selection_id)

    with pytest.raises(SafeErrorException) as exc_info:
        await selection_service.skip(service, session_id, selection.selection_id)
    assert exc_info.value.safe_error.reason == "selection_not_pending"


# --- HTTP level ---------------------------------------------------------------


@pytest.fixture()
def http_session_service() -> ApiSessionService:
    return ApiSessionService()


@pytest.fixture()
def http_client(http_session_service: ApiSessionService):
    app.dependency_overrides[get_session_service] = lambda: http_session_service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(
        http_session_service, runner=FakeRunner(http_session_service)
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_selection_via_http(session_service: ApiSessionService, session_id: str, pending_write_message=None):
    async def setup():
        return await _seed_selection(session_service, session_id, pending_write_message)

    return asyncio.run(setup())


def test_http_choose_success_returns_pending_action_for_a_write(
    http_client: TestClient, http_session_service: ApiSessionService
) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]
    selection = _seed_selection_via_http(http_session_service, session_id, pending_write_message="hi there")

    response = http_client.post(
        f"/api/sessions/{session_id}/selections/{selection.selection_id}/choose",
        json={"option_id": selection.options[0].option_id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["selected_label"] == "Project Falcon Room Test"
    assert body["pending_action"]["operation"] == "teams.sendMessage"
    # `pending_action.chat_id` legitimately carries the real chat id --
    # that is the existing, frozen `PendingActionDTO` contract (the
    # frontend simply never renders it; see ApprovalCard.tsx). What must
    # never happen is the raw chat id appearing as one of the SELECTION's
    # own options -- already proven at the service level
    # (test_selection_options_never_expose_the_raw_chat_id in
    # test_teams_list_chats.py); nothing new to assert here beyond the
    # response actually resolving successfully.
    assert body["pending_action"]["chat_id"] == "chat-real-1"


def test_http_choose_a_read_selection_has_no_pending_action(
    http_client: TestClient, http_session_service: ApiSessionService
) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]
    selection = _seed_selection_via_http(http_session_service, session_id)

    response = http_client.post(
        f"/api/sessions/{session_id}/selections/{selection.selection_id}/choose",
        json={"option_id": selection.options[0].option_id},
    )

    assert response.status_code == 200
    assert response.json()["pending_action"] is None


def test_http_skip_success(http_client: TestClient, http_session_service: ApiSessionService) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]
    selection = _seed_selection_via_http(http_session_service, session_id)

    response = http_client.post(f"/api/sessions/{session_id}/selections/{selection.selection_id}/skip")

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"


def test_http_unknown_session_choose_is_a_safe_404(http_client: TestClient) -> None:
    response = http_client.post(
        "/api/sessions/does-not-exist/selections/sel-1/choose", json={"option_id": "opt-1"}
    )
    assert response.status_code == 404
    assert response.json()["errorCode"] == "not_found"


def test_http_missing_option_id_is_a_safe_400(
    http_client: TestClient, http_session_service: ApiSessionService
) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]

    response = http_client.post(f"/api/sessions/{session_id}/selections/sel-1/choose", json={})

    assert response.status_code in (400, 422)


def test_http_wrong_selection_id_is_a_safe_error(
    http_client: TestClient, http_session_service: ApiSessionService
) -> None:
    session_id = http_client.post("/api/sessions").json()["session_id"]
    selection = _seed_selection_via_http(http_session_service, session_id)

    response = http_client.post(
        f"/api/sessions/{session_id}/selections/wrong-id/choose",
        json={"option_id": selection.options[0].option_id},
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "selection_id_mismatch"
