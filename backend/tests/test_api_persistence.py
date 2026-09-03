"""Real persistence tests against a genuine temporary SQLite database file
(instruction section 18: "Do NOT fake persistence for these tests. Use
isolated temporary database files.").

Each test builds a real `google.adk.sessions.DatabaseSessionService`
bound to a per-test SQLite file (via pytest's `tmp_path`), writes through
it, disposes it (`.close()`), constructs a SECOND, independent instance
bound to the SAME file, and verifies the data survived -- proving actual
restart-safety, not merely that a Python object still holds a reference
to what it wrote.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.adk.sessions import DatabaseSessionService

from backend.api.pending_action import map_pending_action
from backend.api.session_service import ApiSessionService
from backend.approval.policy_gate import authorize_write
from backend.approval.schemas import ActionProposal
from backend.approval.service import (
    PENDING_ACTION_PROPOSAL_STATE_KEY,
    approve_proposal,
    create_action_proposal,
    reject_proposal,
)
from backend.tests._api_fakes import append_state_delta, simulate_proposal

ALICE = "alice"
BOB = "bob"


def _sqlite_url(tmp_path: Path) -> str:
    db_file = tmp_path / "slopanoc_test_sessions.db"
    # SQLAlchemy URLs always use forward slashes, regardless of platform.
    return f"sqlite+aiosqlite:///{db_file.as_posix()}"


# --- 1-6: create instance A, write, dispose, recreate as instance B -----


@pytest.mark.asyncio
async def test_session_survives_service_recreation(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)

    service_a = DatabaseSessionService(url)
    session_id = "s1"
    await service_a.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
    await service_a.close()

    service_b = DatabaseSessionService(url)
    try:
        reloaded = await service_b.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
        assert reloaded is not None
        assert reloaded.id == session_id
        assert reloaded.user_id == ALICE
    finally:
        await service_b.close()


@pytest.mark.asyncio
async def test_conversation_history_survives_restart_via_the_normal_adk_mechanism(tmp_path: Path) -> None:
    """Real ADK `Event`s (not merely custom state keys) survive -- a
    resumed session still provides genuine conversation history through
    ADK's own event-storage mechanism, per instruction section 19.
    """
    from google.adk.events import Event, EventActions
    from google.genai import types

    url = _sqlite_url(tmp_path)
    session_id = "s1"

    service_a = DatabaseSessionService(url)
    session = await service_a.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
    user_event = Event(
        author="user",
        invocation_id="inv-1",
        content=types.Content(role="user", parts=[types.Part.from_text(text="Summarize Ops Bridge")]),
    )
    await service_a.append_event(session, user_event)
    session = await service_a.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
    assistant_event = Event(
        author="team_manager",
        invocation_id="inv-1",
        content=types.Content(role="model", parts=[types.Part.from_text(text="Here is the summary...")]),
        actions=EventActions(state_delta={}),
    )
    await service_a.append_event(session, assistant_event)
    await service_a.close()

    service_b = DatabaseSessionService(url)
    try:
        reloaded = await service_b.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
        assert reloaded is not None
        assert len(reloaded.events) == 2
        texts = [
            "".join(p.text for p in e.content.parts if p.text) for e in reloaded.events if e.content
        ]
        assert "Summarize Ops Bridge" in texts
        assert "Here is the summary..." in texts
        assert reloaded.events[0].author == "user"
        assert reloaded.events[1].author == "team_manager"
    finally:
        await service_b.close()


@pytest.mark.asyncio
async def test_selected_teams_chat_id_and_topic_survive_restart(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    session_id = "s1"

    service_a = DatabaseSessionService(url)
    session = await service_a.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
    api_service_a = ApiSessionService(service_a)
    await append_state_delta(
        api_service_a, session, {"selected_teams_chat_id": "c1", "selected_teams_chat_topic": "Ops Bridge"}
    )
    await service_a.close()

    service_b = DatabaseSessionService(url)
    try:
        reloaded = await service_b.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
        assert reloaded.state["selected_teams_chat_id"] == "c1"
        assert reloaded.state["selected_teams_chat_topic"] == "Ops Bridge"
    finally:
        await service_b.close()


@pytest.mark.asyncio
async def test_last_teams_evidence_survives_restart(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    session_id = "s1"
    evidence = [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"}]

    service_a = DatabaseSessionService(url)
    session = await service_a.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
    api_service_a = ApiSessionService(service_a)
    await append_state_delta(api_service_a, session, {"last_teams_evidence": evidence})
    await service_a.close()

    service_b = DatabaseSessionService(url)
    try:
        reloaded = await service_b.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
        assert reloaded.state["last_teams_evidence"] == evidence
    finally:
        await service_b.close()


# --- Approval round-trip verification (instruction section 14) ----------


@pytest.mark.asyncio
async def test_pending_action_proposal_round_trips_exactly(tmp_path: Path) -> None:
    """Every field of the stored proposal dict -- proposal_id, operation,
    payload, payload_hash, created_at, expires_at, status, summary --
    must survive write -> DB -> process recreation -> reload byte-for-byte
    (as JSON-safe primitives), and reconstructing an `ActionProposal` from
    the reloaded dict must succeed with no semantic mutation.
    """
    url = _sqlite_url(tmp_path)
    session_id = "s1"

    service_a = DatabaseSessionService(url)
    session = await service_a.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
    api_service_a = ApiSessionService(service_a)
    await simulate_proposal(
        api_service_a,
        session,
        "teams.createChat",
        {"title": "Ops Bridge", "members": ["a@example.com", "b@example.com"]},
        summary="Create Teams chat titled 'Ops Bridge'...",
    )
    session = await service_a.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
    original_stored = dict(session.state[PENDING_ACTION_PROPOSAL_STATE_KEY])
    await service_a.close()

    service_b = DatabaseSessionService(url)
    try:
        reloaded = await service_b.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
        reloaded_stored = reloaded.state[PENDING_ACTION_PROPOSAL_STATE_KEY]

        for field in (
            "proposal_id",
            "operation",
            "payload",
            "payload_hash",
            "created_at",
            "expires_at",
            "status",
            "summary",
        ):
            assert reloaded_stored[field] == original_stored[field], f"{field} mutated across restart"

        # Reconstructing the Pydantic model from the reloaded dict must
        # succeed with no semantic change -- proves the JSON-safe
        # representation is genuinely round-trip-safe, not merely
        # dict-equal by coincidence.
        proposal = ActionProposal.model_validate(reloaded_stored)
        assert proposal.payload == {"title": "Ops Bridge", "members": ["a@example.com", "b@example.com"]}
        assert proposal.payload_hash == original_stored["payload_hash"]
    finally:
        await service_b.close()


@pytest.mark.asyncio
async def test_approved_proposal_status_survives_restart(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    session_id = "s1"

    service_a = DatabaseSessionService(url)
    session = await service_a.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
    api_service_a = ApiSessionService(service_a)
    await simulate_proposal(api_service_a, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    session = await service_a.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
    proposal_id = session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

    result = approve_proposal(proposal_id, session.state)
    assert result.success
    await api_service_a.persist_state_delta(
        session, {PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
    )
    await service_a.close()

    service_b = DatabaseSessionService(url)
    try:
        reloaded = await service_b.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
        assert reloaded.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "approved"
    finally:
        await service_b.close()


@pytest.mark.asyncio
async def test_rejected_proposal_status_survives_restart(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    session_id = "s1"

    service_a = DatabaseSessionService(url)
    session = await service_a.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
    api_service_a = ApiSessionService(service_a)
    await simulate_proposal(api_service_a, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    session = await service_a.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
    proposal_id = session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

    result = reject_proposal(proposal_id, session.state)
    assert result.success
    await api_service_a.persist_state_delta(
        session, {PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
    )
    await service_a.close()

    service_b = DatabaseSessionService(url)
    try:
        reloaded = await service_b.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
        assert reloaded.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "rejected"
    finally:
        await service_b.close()


# --- Expiry after restart (instruction section 15) -----------------------


@pytest.mark.asyncio
async def test_timezone_aware_expires_at_survives_restart(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    session_id = "s1"

    service_a = DatabaseSessionService(url)
    session = await service_a.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
    api_service_a = ApiSessionService(service_a)
    await simulate_proposal(api_service_a, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    session = await service_a.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
    original_expires_at = session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["expires_at"]
    await service_a.close()

    service_b = DatabaseSessionService(url)
    try:
        reloaded = await service_b.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
        reloaded_expires_at = reloaded.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["expires_at"]
        assert reloaded_expires_at == original_expires_at

        proposal = ActionProposal.model_validate(reloaded.state[PENDING_ACTION_PROPOSAL_STATE_KEY])
        assert proposal.expires_at.tzinfo is not None
    finally:
        await service_b.close()


@pytest.mark.asyncio
async def test_effective_expiry_remains_correct_after_reload(tmp_path: Path) -> None:
    """A proposal created (and approved) far enough in the past that it
    would already be expired must still report as expired after a
    process restart -- the stored `status` field alone (still "approved")
    is not authoritative; `expires_at` is.
    """
    url = _sqlite_url(tmp_path)
    session_id = "s1"
    long_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    service_a = DatabaseSessionService(url)
    session = await service_a.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
    delta: dict = {}
    proposal = create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "Hi"}, delta, now=long_ago
    )
    api_service_a = ApiSessionService(service_a)
    await api_service_a.persist_state_delta(session, delta)
    session = await service_a.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
    # Approve AT the same fictional past moment the proposal was created
    # -- i.e. "this was validly approved back then" -- so the later
    # expiry check (using the real current time) is exercising "expired
    # after having been legitimately approved", not "never approvable at
    # all".
    approve_result = approve_proposal(proposal.proposal_id, session.state, now=long_ago)
    assert approve_result.success
    await api_service_a.persist_state_delta(
        session, {PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
    )
    await service_a.close()

    service_b = DatabaseSessionService(url)
    try:
        reloaded = await service_b.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
        dto = map_pending_action(reloaded.state)
        assert dto.status == "expired"
        assert dto.expires_in_seconds == 0
    finally:
        await service_b.close()


@pytest.mark.asyncio
async def test_restart_cannot_revive_an_expired_proposal_for_authorization(tmp_path: Path) -> None:
    """Security-critical: after a simulated restart, `authorize_write`
    still denies an expired (even if stored as "approved") proposal --
    restart must never reset `expires_at`, recreate the proposal, extend
    the window, or otherwise revive it.
    """
    url = _sqlite_url(tmp_path)
    session_id = "s1"
    long_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    service_a = DatabaseSessionService(url)
    session = await service_a.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
    delta: dict = {}
    proposal = create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "Hi"}, delta, now=long_ago
    )
    api_service_a = ApiSessionService(service_a)
    await api_service_a.persist_state_delta(session, delta)
    session = await service_a.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
    # Approved validly AT that same past moment -- see the sibling test's
    # comment for why `now=long_ago` matters here.
    approve_result = approve_proposal(proposal.proposal_id, session.state, now=long_ago)
    assert approve_result.success
    await api_service_a.persist_state_delta(
        session, {PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
    )
    await service_a.close()

    service_b = DatabaseSessionService(url)
    try:
        reloaded = await service_b.get_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id)
        result = authorize_write("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, reloaded.state)
        assert result.authorized is False
        assert result.reason.value == "proposal_expired"
    finally:
        await service_b.close()


# --- Isolation on the real persistent backend ----------------------------


@pytest.mark.asyncio
async def test_session_a_approval_does_not_authorize_session_b_on_real_backend(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)

    service = DatabaseSessionService(url)
    api_service = ApiSessionService(service)
    try:
        session_a_id, session_b_id = "sa", "sb"
        await service.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_a_id, state={})
        await service.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_b_id, state={})

        session_a = await api_service.get_session(session_a_id, ALICE)
        await simulate_proposal(api_service, session_a, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
        refreshed_a = await api_service.get_session(session_a_id, ALICE)
        proposal_id = refreshed_a.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"]

        result = authorize_write(
            "teams.sendMessage",
            {"chatId": "c1", "message": "Hi"},
            (await api_service.get_session(session_b_id, ALICE)).state,
        )
        assert result.authorized is False
        assert result.reason.value == "no_pending_proposal"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_no_database_credentials_leak_into_pending_action_dto(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)

    service = DatabaseSessionService(url)
    api_service = ApiSessionService(service)
    try:
        session_id = "s1"
        await service.create_session(app_name="slopanoc-api", user_id=ALICE, session_id=session_id, state={})
        session = await api_service.get_session(session_id, ALICE)
        await simulate_proposal(api_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
        refreshed = await api_service.get_session(session_id, ALICE)

        dto = map_pending_action(refreshed.state)
        dumped = dto.model_dump_json()
        assert url not in dumped
        assert "sqlite" not in dumped.lower()
        assert "payload_hash" not in dumped
    finally:
        await service.close()


# --- Persistence failures map to a safe generic error (instruction 27) ---


def test_a_database_failure_during_a_request_is_a_safe_error_not_a_raw_one() -> None:
    """A raw DB-layer exception (here simulated, but shaped like a real
    SQLAlchemy/driver error that could embed a connection string/
    credentials) must never reach the HTTP response body.
    """
    from fastapi.testclient import TestClient

    from backend.api.app import app
    from backend.api.session_service import get_session_service

    class _FailingAdkSessionService:
        async def get_session(self, **kwargs):
            raise RuntimeError(
                "connection to server failed: password authentication failed for user "
                "'slopanoc' host=10.0.0.5 dbname=slopanoc_prod"
            )

        async def create_session(self, **kwargs):
            raise RuntimeError("could not connect to server: Connection refused")

    failing_service = ApiSessionService(_FailingAdkSessionService())
    app.dependency_overrides[get_session_service] = lambda: failing_service
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/sessions")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"errorCode", "userMessage", "retryable", "correlationId"}
    assert "password" not in response.text
    assert "10.0.0.5" not in response.text
    assert "slopanoc_prod" not in response.text
    assert "Traceback" not in response.text


def test_a_database_failure_on_chat_is_also_a_safe_error() -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import app
    from backend.api.chat_service import ChatService, get_chat_service
    from backend.api.session_service import get_session_service

    service = ApiSessionService()

    class _FailingRunner:
        async def run_async(self, *, user_id, session_id, new_message, run_config=None):
            raise RuntimeError("psycopg.OperationalError: connection to server at secret-host failed")
            yield  # pragma: no cover

    app.dependency_overrides[get_session_service] = lambda: service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(service, runner=_FailingRunner())
    try:
        with TestClient(app) as client:
            session_id = client.post("/api/sessions").json()["session_id"]
            response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hi"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert "secret-host" not in response.text
    assert "psycopg" not in response.text
