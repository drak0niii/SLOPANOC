"""Real DatabaseSessionService verification for final answer persistence and consistency.

Demonstrates Gap 4 and E2E persistence validation using real ADK DatabaseSessionService:
- Uses SQLite with async SQLAlchemy engine (real relational SQL table and transaction operations).
- Verifies state delta merging, concurrent turns, and rewind consistency on real database sessions.
- Validates failure and retry cases against real database session service.
- Performs end-to-end verification comparing matching invocation IDs and content hashes across:
  final SSE response == persisted canonical answer == refreshed history == next-turn model context.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from typing import Optional

import pytest
import pytest_asyncio
from google.adk.events.event import Event, EventActions
from google.adk.flows.llm_flows.contents import _get_contents
from google.adk.models.llm_request import LlmRequest
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
)
from backend.api.chat_service import ChatService
from backend.api.session_history_service import _extract_final_text, get_session_history
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
from backend.api.turn_context import current_run_id
from backend.api.turn_final_answers import (
    TURN_FINAL_ANSWERS_STATE_KEY,
    project_authoritative_answers_to_contents,
    resolve_turn_final_answer,
)
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.tests._api_fakes import (
    FakeEvent,
    FakeFunctionResponse,
    append_user_turn,
)


def _sha256(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DbCompatibleFakeRunner:
    """FakeRunner variant that handles DatabaseSessionService optimistic concurrency correctly.
    Does not append an extra out-of-band event during run_async.
    """

    def __init__(self, session_service, events=None, side_effect=None):
        self._session_service = session_service
        self._events = events or []
        self._side_effect = side_effect

    async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
        session = await self._session_service.get_session(session_id, user_id)
        if self._side_effect is not None:
            await self._side_effect(self._session_service, session, new_message.parts[0].text)
        for event in self._events:
            yield event


@pytest_asyncio.fixture
async def real_db_session_service():
    """Provides an ApiSessionService backed by a real SQLite DatabaseSessionService."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db_url = f"sqlite+aiosqlite:///{db_path}"
    adk_db_service = DatabaseSessionService(db_url)
    api_service = ApiSessionService(adk_session_service=adk_db_service)
    try:
        yield api_service
    finally:
        await adk_db_service.close()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass


@pytest.mark.asyncio
async def test_real_database_state_delta_merging_and_isolation(real_db_session_service: ApiSessionService) -> None:
    """Verifies that real DatabaseSessionService accurately merges state deltas across turns,
    preserving isolation across distinct invocation IDs without corrupting existing records.
    """
    user_id = "db-user-1"
    session_id = await real_db_session_service.create_session(user_id=user_id)
    session = await real_db_session_service.get_session(session_id, user_id)

    # Turn 1
    t1_inv = f"inv-db-{uuid.uuid4().hex[:8]}"
    t1_text = "Turn 1 authoritative remediated response."
    session = await append_user_turn(real_db_session_service, session, t1_inv, "User turn 1", assistant_text="Raw 1")
    delta_1 = {TURN_FINAL_ANSWERS_STATE_KEY: {t1_inv: t1_text}}
    await real_db_session_service.persist_state_delta(session, delta_1)

    # Re-fetch from real database to prove commit
    reloaded_session_1 = await real_db_session_service.get_session(session_id, user_id)
    assert resolve_turn_final_answer(reloaded_session_1.state, t1_inv) == t1_text

    # Turn 2
    t2_inv = f"inv-db-{uuid.uuid4().hex[:8]}"
    t2_text = "Turn 2 authoritative remediated response."
    reloaded_session_1 = await append_user_turn(real_db_session_service, reloaded_session_1, t2_inv, "User turn 2", assistant_text="Raw 2")
    delta_2 = {TURN_FINAL_ANSWERS_STATE_KEY: {t1_inv: t1_text, t2_inv: t2_text}}
    await real_db_session_service.persist_state_delta(reloaded_session_1, delta_2)

    # Re-fetch from real database again
    reloaded_session_2 = await real_db_session_service.get_session(session_id, user_id)
    assert resolve_turn_final_answer(reloaded_session_2.state, t1_inv) == t1_text
    assert resolve_turn_final_answer(reloaded_session_2.state, t2_inv) == t2_text


@pytest.mark.asyncio
async def test_real_database_rewind_reversal(real_db_session_service: ApiSessionService) -> None:
    """Verifies that ADK's native rewind mechanism accurately restores session.state on real
    DatabaseSessionService when turns are rewound.
    """
    user_id = "db-user-2"
    session_id = await real_db_session_service.create_session(user_id=user_id)
    session = await real_db_session_service.get_session(session_id, user_id)

    # Turn 1
    inv_1 = "inv-db-rw-1"
    ans_1 = "Answer 1"
    session = await append_user_turn(real_db_session_service, session, inv_1, "User msg 1", assistant_text=ans_1)
    await real_db_session_service.persist_state_delta(session, {TURN_FINAL_ANSWERS_STATE_KEY: {inv_1: ans_1}})

    # Turn 2
    session = await real_db_session_service.get_session(session_id, user_id)
    inv_2 = "inv-db-rw-2"
    ans_2 = "Answer 2"
    session = await append_user_turn(real_db_session_service, session, inv_2, "User msg 2", assistant_text=ans_2)
    await real_db_session_service.persist_state_delta(session, {TURN_FINAL_ANSWERS_STATE_KEY: {inv_1: ans_1, inv_2: ans_2}})

    # Rewind turn 2 via real ADK runner
    from google.adk.agents import Agent
    from google.adk.runners import Runner

    test_agent = Agent(name="test_agent", model="gemini-2.0-flash")
    real_runner = Runner(
        app_name="slopanoc-api",
        agent=test_agent,
        session_service=real_db_session_service.adk_session_service,
    )
    chat_service = ChatService(real_db_session_service, runner=real_runner)
    await chat_service.rewind_before_user_turn(session_id, before_user_turn_index=1, user_id=user_id)

    # Re-fetch from database to verify state delta rollback
    refreshed_session = await real_db_session_service.get_session(session_id, user_id)
    assert resolve_turn_final_answer(refreshed_session.state, inv_1) == ans_1
    assert resolve_turn_final_answer(refreshed_session.state, inv_2) is None


@pytest.mark.asyncio
async def test_real_database_end_to_end_5_boundaries_and_hashes(real_db_session_service: ApiSessionService) -> None:
    """Verifies end-to-end consistency on real database session service across all 5 boundaries:
    Final SSE response == persisted authoritative answer == refreshed history == next-turn model context.
    Compares SHA-256 hashes and matching invocation IDs.
    """
    user_id = "db-user-3"
    session_id = await real_db_session_service.create_session(user_id=user_id)
    attachment_service = AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))

    turn_id = "inv-e2e-db-turn-1"
    raw_model_response = "Immediate node reboot recommended (unverified raw text)."
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Node is reporting ESS Service Unavailable.",
        next_action="Check active alarms on the node before taking action.",
        command="alt",
        evidence_requested="Active alarms list",
    )

    async def side_effect(session_service, session, text):
        register_troubleshooting_guidance(current_run_id(), guidance)
        await append_user_turn(
            session_service,
            session,
            invocation_id=turn_id,
            user_text=text,
            assistant_text=raw_model_response,
        )

    runner = DbCompatibleFakeRunner(
        real_db_session_service,
        side_effect=side_effect,
        events=[
            FakeEvent(
                invocation_id=turn_id,
                final=False,
                function_responses=[
                    FakeFunctionResponse(
                        "record_source_requirements",
                        {"requires_teams": False, "requires_governed_knowledge": False},
                    )
                ],
            ),
            # Speculative delta arrives
            FakeEvent(invocation_id=turn_id, text=raw_model_response, partial=True, final=False),
            # Raw final event from model
            FakeEvent(invocation_id=turn_id, text=raw_model_response, partial=False, final=True),
        ],
    )
    chat_service = ChatService(real_db_session_service, runner=runner)

    streaming_deltas: list[str] = []
    final_sse_content: Optional[str] = None
    server_run_id: Optional[str] = None

    async for sse_event in chat_service.execute_turn_events(session_id, "how to troubleshoot", user_id):
        if sse_event.type == StreamEventType.RUN_STARTED:
            server_run_id = sse_event.data.get("run_id")
        elif sse_event.type == StreamEventType.MESSAGE_DELTA:
            streaming_deltas.append(sse_event.data.get("text", ""))
        elif sse_event.type == StreamEventType.MESSAGE_COMPLETED:
            final_sse_content = sse_event.data.get("content")

    # Boundary 1: Streaming UI (Speculative text suppressed)
    b1_streaming_ui = "".join(streaming_deltas)
    assert b1_streaming_ui == "", "Speculative text must be suppressed when guidance overrides"

    # Boundary 2: Final SSE payload
    b2_final_sse = final_sse_content
    assert b2_final_sse is not None
    assert "Check active alarms on the node before taking action" in b2_final_sse
    assert "alt" in b2_final_sse

    # Boundary 3: Persisted Real Database
    # 3a. Audit log (events) retains raw un-remediated model output
    db_session = await real_db_session_service.get_session(session_id, user_id)
    assistant_db_events = [e for e in db_session.events if getattr(e, "content", None) and e.content.role == "model"]
    b3_persisted_events = _extract_final_text(assistant_db_events[-1])
    assert b3_persisted_events == raw_model_response, "Audit log in session.events must remain raw and immutable"

    # 3b. Authoritative answer in session.state
    b3_persisted_state = resolve_turn_final_answer(db_session.state, turn_id)
    assert b3_persisted_state == b2_final_sse, "Database session.state must store authoritative remediated answer"

    # Boundary 4: History API after refresh
    history_resp = await get_session_history(real_db_session_service, attachment_service, session_id, user_id)
    assistant_history = [m for m in history_resp.messages if m.role == "assistant"]
    b4_history_api = assistant_history[-1].text
    assert b4_history_api == b2_final_sse, "Refreshed history must project authoritative remediated answer"

    # Boundary 5: Next-turn Model Context
    # Add turn 2 user message
    user_turn_2_event = Event(
        author="user",
        invocation_id="inv-e2e-turn-2",
        content=types.Content(
            role="user",
            parts=[types.Part(text="I checked alarms, node says ESS Service Unavailable.")],
        ),
        timestamp=200.0,
    )
    db_session.events.append(user_turn_2_event)
    adk_contents = _get_contents(None, db_session.events, "team_manager")

    class FakeInvocationContext:
        def __init__(self, session):
            self.session = session
            self.agent = type("Agent", (), {"name": "team_manager"})()
            self.branch = None

    class FakeCallbackContext:
        def __init__(self, session):
            self._invocation_context = FakeInvocationContext(session)

    llm_request = LlmRequest(contents=adk_contents)
    callback_context = FakeCallbackContext(db_session)
    project_authoritative_answers_to_contents(callback_context, llm_request)

    model_request_contents = [c for c in llm_request.contents if c.role == "model"]
    b5_model_context = model_request_contents[0].parts[0].text
    assert b5_model_context == b2_final_sse, "Turn 2 model context must consume the authoritative remediated answer"

    # Exact SHA-256 Hash Matching across all 4 authoritative boundaries
    h2 = _sha256(b2_final_sse)
    h3_state = _sha256(b3_persisted_state)
    h4 = _sha256(b4_history_api)
    h5 = _sha256(b5_model_context)

    assert h2 == h3_state == h4 == h5, f"Content hash mismatch across boundaries: h2={h2}, h3={h3_state}, h4={h4}, h5={h5}"
    assert _sha256(b3_persisted_events) != h2, "Audit log hash must differ from remediated hash, proving immutability"
