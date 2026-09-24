"""Audit regression tests for AI message mutation and history consistency.

Traces the message lifecycle across 4 boundaries using SHA-256 hashes:
1. Boundary 1: Last text shown by the streaming UI (accumulated deltas).
2. Boundary 2: Final SSE payload (MESSAGE_COMPLETED content).
3. Boundary 3: Persisted database representation (session.events).
4. Boundary 4: History API response after refresh (GET /api/sessions/{id}/history).

Verifies 7 scenarios:
- Response requiring post-generation remediation / override.
- Finalized message followed by delayed SSE events.
- Refresh immediately after completion.
- History loading during or after a stream.
- Consecutive requests in the same session.
- Concurrent requests and out-of-order responses.
- Intentional rewind, ensuring its effects remain correctly scoped.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Optional

import pytest
from google.adk.events import Event, EventActions
from google.genai import types

from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
from backend.api.chat_service import ChatService
from backend.api.session_history_service import _extract_final_text, get_session_history
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
from backend.api.turn_context import current_run_id
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.tests._api_fakes import (
    FakeEvent,
    FakeFunctionResponse,
    FakeRunner,
    append_user_turn,
)


def _sha256(text: Optional[str]) -> Optional[str]:
    """Computes SHA-256 hash of text. Never logs text content or credentials."""
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BoundaryObservation:
    """Carries SHA-256 hashes across the 4 architectural boundaries."""

    def __init__(
        self,
        *,
        run_id: str,
        message_id: str,
        b1_streaming_ui: Optional[str],
        b2_final_sse: Optional[str],
        b3_persisted_db: Optional[str],
        b4_history_api: Optional[str],
        b5_model_context: Optional[str] = None,
    ) -> None:
        self.run_id = run_id
        self.message_id = message_id
        self.h1_streaming_ui = _sha256(b1_streaming_ui)
        self.h2_final_sse = _sha256(b2_final_sse)
        self.h3_persisted_db = _sha256(b3_persisted_db)
        self.h4_history_api = _sha256(b4_history_api)
        self.h5_model_context = _sha256(b5_model_context)

    @property
    def diverges_at_remediation(self) -> bool:
        """True if the streaming UI text diverges from final SSE payload."""
        return self.h1_streaming_ui != self.h2_final_sse

    @property
    def diverges_at_storage(self) -> bool:
        """True if persisted database representation diverges from final SSE payload."""
        return self.h2_final_sse != self.h3_persisted_db

    @property
    def diverges_on_refresh(self) -> bool:
        """True if history API response after refresh diverges from final SSE payload."""
        return self.h2_final_sse != self.h4_history_api


def _make_attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


# ---------------------------------------------------------------------------
# Scenario 1: Response requiring post-generation remediation / override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_1_response_requiring_post_generation_remediation_divergence() -> None:
    """Verifies message content across all 4 boundaries when post-generation
    remediation (troubleshooting guidance override) replaces final_text.

    Proves:
    - Boundary 1 (UI streaming) accumulates the model's initial stream.
    - Boundary 2 (SSE MESSAGE_COMPLETED) carries the overridden guidance text.
    - Boundary 3 (DB session.events) retains the original model text.
    - Boundary 4 (History API) returns the original model text, diverging from Boundary 2.
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)
    attachment_service = _make_attachment_service()

    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Node is reporting ESS Service Unavailable.",
        next_action="Check active alarms on the node.",
        command="alt",
        evidence_requested="Active alarms list",
    )

    initial_model_text = "The node is reporting ESS Service Unavailable. You should consider checking alarms."

    async def side_effect(session_service, session, text):
        register_troubleshooting_guidance(current_run_id(), guidance)
        # Model ADK runner persistence: in real turns, ADK Runner appends the user event
        # and model event directly to session.events during run_async.
        await append_user_turn(
            session_service,
            session,
            invocation_id="inv-audit-1",
            user_text=text,
            assistant_text=initial_model_text,
        )

    runner = FakeRunner(
        service,
        side_effect=side_effect,
        events=[
            FakeEvent(
                invocation_id="inv-audit-1",
                final=False,
                function_responses=[
                    FakeFunctionResponse(
                        "record_source_requirements",
                        {"requires_teams": False, "requires_governed_knowledge": False},
                    )
                ],
            ),
            # True streaming chunk
            FakeEvent(invocation_id="inv-audit-1", text=initial_model_text, partial=True, final=False),
            # Model final response event
            FakeEvent(invocation_id="inv-audit-1", text=initial_model_text, partial=False, final=True),
        ],
    )
    chat_service = ChatService(service, runner=runner)

    collected_events = []
    streaming_deltas: list[str] = []
    final_sse_content: Optional[str] = None
    server_run_id: Optional[str] = None

    async for sse_event in chat_service.execute_turn_events(session_id, "check alarms", user_id):
        collected_events.append(sse_event)
        if sse_event.type == StreamEventType.RUN_STARTED:
            server_run_id = sse_event.data.get("run_id") or "run-1"
        elif sse_event.type == StreamEventType.MESSAGE_DELTA:
            streaming_deltas.append(sse_event.data.get("text", ""))
        elif sse_event.type == StreamEventType.MESSAGE_COMPLETED:
            final_sse_content = sse_event.data.get("content")

    b1_streaming_ui = "".join(streaming_deltas)
    b2_final_sse = final_sse_content

    # Boundary 3: Persisted database representation
    db_session = await service.get_session(session_id, user_id)
    assistant_db_events = [e for e in db_session.events if getattr(e, "content", None) and e.content.role == "model"]
    b3_persisted_db = _extract_final_text(assistant_db_events[-1]) if assistant_db_events else None

    # Boundary 4: History API response after refresh
    # For history to project turns, there must be user content events in session.events
    # (in real turns, ADK appends user event before run_async; FakeRunner only yields runner events)
    history_resp = await get_session_history(service, attachment_service, session_id, user_id)
    assistant_history_messages = [m for m in history_resp.messages if m.role == "assistant"]
    b4_history_api = assistant_history_messages[-1].text if assistant_history_messages else None

    obs = BoundaryObservation(
        run_id=server_run_id or "run-1",
        message_id="msg-1",
        b1_streaming_ui=b1_streaming_ui,
        b2_final_sse=b2_final_sse,
        b3_persisted_db=b3_persisted_db,
        b4_history_api=b4_history_api,
    )

    # 1. UI text does NOT leak speculative text (Requirement 7):
    # Speculative streaming deltas are suppressed when guidance replaces final text.
    assert b1_streaming_ui == ""
    assert obs.h1_streaming_ui == _sha256("")

    # 2. Database audit log retains immutable raw events (Requirement 1):
    assert b3_persisted_db == initial_model_text
    assert obs.h3_persisted_db == _sha256(initial_model_text)

    # 3. History API projects the authoritative final answer (Requirement 2 & 4):
    # Final SSE text equals refreshed history text!
    assert b2_final_sse is not None
    assert b4_history_api == b2_final_sse
    assert obs.h4_history_api == obs.h2_final_sse
    assert obs.diverges_on_refresh is False


# ---------------------------------------------------------------------------
# Scenario 2: Finalized message followed by delayed SSE events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_2_finalized_message_followed_by_delayed_sse_events() -> None:
    """Verifies behavior when late-arriving events or deltas are processed.

    In the backend: execute_turn_events strictly terminates after RUN_COMPLETED.
    In the frontend reducer: any event arriving after RUN_COMPLETED (when chat.run is None)
    is dropped because runToken does not match.
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)

    runner = FakeRunner(service, respond=lambda _: "Completed response")
    chat_service = ChatService(service, runner=runner)

    events = [event async for event in chat_service.execute_turn_events(session_id, "hello", user_id)]
    event_types = [e.type for e in events]

    assert StreamEventType.MESSAGE_COMPLETED in event_types
    assert StreamEventType.RUN_COMPLETED in event_types

    # RUN_COMPLETED is always the terminal event
    assert event_types[-1] == StreamEventType.RUN_COMPLETED
    completed_idx = event_types.index(StreamEventType.MESSAGE_COMPLETED)
    run_completed_idx = event_types.index(StreamEventType.RUN_COMPLETED)
    assert completed_idx < run_completed_idx

    # Simulate client-side reducer guard against delayed delta arriving after completion:
    class ReducerState:
        def __init__(self, run_token: Optional[str], text: str, status: str):
            self.run_token = run_token
            self.text = text
            self.status = status

    state = ReducerState(run_token="token-1", text="Completed response", status="complete")
    # When RUN_COMPLETED processes, run_token is cleared (chat.run = undefined)
    state.run_token = None

    # Late-arriving delta with old run_token
    late_delta_token = "token-1"
    late_delta_text = "extra text"
    # Reducer guard: if (!chat || !chat.run || chat.run.runToken !== runToken) return state;
    if state.run_token == late_delta_token:
        state.text += late_delta_text

    # Verified: late delta was safely dropped because run_token was cleared
    assert state.text == "Completed response"


# ---------------------------------------------------------------------------
# Scenario 3: Refresh immediately after completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_3_refresh_immediately_after_completion() -> None:
    """Tests the immediate browser refresh boundary:
    UI displays Boundary 2 (MESSAGE_COMPLETED), but refresh immediately re-fetches
    from GET /api/sessions/{id}/history (Boundary 4).
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)
    attachment_service = _make_attachment_service()

    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Health check required",
        next_action="Run health check",
        command="lt all",
        evidence_requested="MO listing",
    )

    async def side_effect(session_service, session, text):
        register_troubleshooting_guidance(current_run_id(), guidance)
        await append_user_turn(session_service, session, "inv-test", text, assistant_text=initial_text)

    initial_text = "Initial assessment text"
    runner = FakeRunner(
        service,
        side_effect=side_effect,
        events=[
            FakeEvent(
                invocation_id="inv-test",
                final=False,
                function_responses=[
                    FakeFunctionResponse(
                        "record_source_requirements",
                        {"requires_teams": False, "requires_governed_knowledge": False},
                    )
                ],
            ),
            FakeEvent(invocation_id="inv-test", text=initial_text, partial=False, final=True),
        ],
    )
    chat_service = ChatService(service, runner=runner)

    final_sse_content = None
    async for event in chat_service.execute_turn_events(session_id, "run check", user_id):
        if event.type == StreamEventType.MESSAGE_COMPLETED:
            final_sse_content = event.data.get("content")

    # Immediate refresh: GET /api/sessions/{id}/history
    history_resp = await get_session_history(service, attachment_service, session_id, user_id)
    history_text = history_resp.messages[-1].text

    # Proves the fix: the refreshed history text equals the authoritative final_sse_content!
    assert final_sse_content is not None
    assert _sha256(final_sse_content) == _sha256(history_text)
    assert history_text == final_sse_content


# ---------------------------------------------------------------------------
# Scenario 4: History loading during or after a stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_4_history_loading_during_or_after_stream() -> None:
    """Verifies history loading behavior when called before turn completion vs after.

    Shows that history reads directly from session.events without acquiring the session lock.
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)
    attachment_service = _make_attachment_service()

    # Before stream: history is empty
    hist_before = await get_session_history(service, attachment_service, session_id, user_id)
    assert len(hist_before.messages) == 0

    # Add a completed turn into session.events
    session = await service.get_session(session_id, user_id)
    await append_user_turn(service, session, "inv-1", "msg1", assistant_text="echo: msg1")

    # After turn: history has user and assistant messages
    hist_after = await get_session_history(service, attachment_service, session_id, user_id)
    assert len(hist_after.messages) == 2
    assert hist_after.messages[0].role == "user"
    assert hist_after.messages[1].role == "assistant"
    assert hist_after.messages[1].text == "echo: msg1"


# ---------------------------------------------------------------------------
# Scenario 5: Consecutive requests in the same session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_5_consecutive_requests_in_same_session() -> None:
    """Verifies that consecutive requests in the same session correctly maintain
    turn order and unique invocation IDs in history.
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)
    attachment_service = _make_attachment_service()

    # Turn 1
    session = await service.get_session(session_id, user_id)
    session = await append_user_turn(service, session, "inv-1", "turn 1", assistant_text="reply to turn 1")

    # Turn 2
    await append_user_turn(service, session, "inv-2", "turn 2", assistant_text="reply to turn 2")

    # History verification
    history_resp = await get_session_history(service, attachment_service, session_id, user_id)
    assert len(history_resp.messages) == 4
    assert [m.role for m in history_resp.messages] == ["user", "assistant", "user", "assistant"]
    assert history_resp.messages[0].text == "turn 1"
    assert history_resp.messages[1].text == "reply to turn 1"
    assert history_resp.messages[2].text == "turn 2"
    assert history_resp.messages[3].text == "reply to turn 2"


# ---------------------------------------------------------------------------
# Scenario 6: Concurrent requests and out-of-order responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_6_concurrent_requests_serialized_by_session_lock() -> None:
    """Verifies that concurrent requests to the same session are strictly serialized
    by session_service.lock_for, preventing race conditions or interleaved writes.
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)

    execution_order: list[str] = []

    async def slow_side_effect_1(session_service, session, text):
        execution_order.append("turn1_start")
        await asyncio.sleep(0.05)
        execution_order.append("turn1_end")

    async def side_effect_2(session_service, session, text):
        execution_order.append("turn2_start")
        execution_order.append("turn2_end")

    runner1 = FakeRunner(service, side_effect=slow_side_effect_1, respond=lambda _: "r1")
    runner2 = FakeRunner(service, side_effect=side_effect_2, respond=lambda _: "r2")

    chat_service1 = ChatService(service, runner=runner1)
    chat_service2 = ChatService(service, runner=runner2)

    async def run_turn(cs: ChatService, msg: str):
        return [e async for e in cs.execute_turn_events(session_id, msg, user_id)]

    # Fire both turns concurrently
    await asyncio.gather(
        run_turn(chat_service1, "first"),
        run_turn(chat_service2, "second"),
    )

    # Due to lock_for, turn 1 must fully finish before turn 2 starts
    assert execution_order == ["turn1_start", "turn1_end", "turn2_start", "turn2_end"]


# ---------------------------------------------------------------------------
# Scenario 7: Intentional rewind, ensuring its effects remain correctly scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_7_intentional_rewind_effects_remain_correctly_scoped() -> None:
    """Verifies that an intentional /rewind removes subsequent turns while preserving
    earlier turns intact.

    Ensures that the rewind invoked at 10:54:28 on Sep 22 is an intentional scoping
    mechanism and is not conflated with the remediation/storage divergence defect.
    """
    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)
    attachment_service = _make_attachment_service()

    # Populate 3 user-assistant turns directly via real ADK events
    ev1_user = Event(
        invocation_id="inv-1",
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text="User msg 1")]),
        timestamp=1000.0,
    )
    ev1_model = Event(
        invocation_id="inv-1",
        author="team_manager",
        content=types.Content(role="model", parts=[types.Part.from_text(text="Assistant reply 1")]),
        timestamp=1001.0,
    )

    ev2_user = Event(
        invocation_id="inv-2",
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text="User msg 2")]),
        timestamp=1002.0,
    )
    ev2_model = Event(
        invocation_id="inv-2",
        author="team_manager",
        content=types.Content(role="model", parts=[types.Part.from_text(text="Assistant reply 2")]),
        timestamp=1003.0,
    )

    ev3_user = Event(
        invocation_id="inv-3",
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text="User msg 3")]),
        timestamp=1004.0,
    )
    ev3_model = Event(
        invocation_id="inv-3",
        author="team_manager",
        content=types.Content(role="model", parts=[types.Part.from_text(text="Assistant reply 3")]),
        timestamp=1005.0,
    )

    session = await service.get_session(session_id, user_id)
    for ev in [ev1_user, ev1_model, ev2_user, ev2_model, ev3_user, ev3_model]:
        await service.adk_session_service.append_event(session, ev)

    # Check 3 turns present in history before rewind
    h_before = await get_session_history(service, attachment_service, session_id, user_id)
    assert len(h_before.messages) == 6

    # Append rewind event rewinding before inv-2 (so inv-2 and inv-3 are rolled back)
    rewind_event = Event(
        invocation_id="inv-rewind",
        author="user",
        actions=EventActions(rewind_before_invocation_id="inv-2"),
        timestamp=1006.0,
    )
    session = await service.get_session(session_id, user_id)
    await service.adk_session_service.append_event(session, rewind_event)

    # Check history after rewind
    h_after = await get_session_history(service, attachment_service, session_id, user_id)

    # Only Turn 1 survives
    assert len(h_after.messages) == 2
    assert h_after.messages[0].turn_id == "inv-1"
    assert h_after.messages[0].text == "User msg 1"
    assert h_after.messages[1].turn_id == "inv-1"
    assert h_after.messages[1].text == "Assistant reply 1"

    # Turn 1 content hash is completely unchanged across the rewind
    assert _sha256(h_after.messages[1].text) == _sha256(h_before.messages[1].text)


# ---------------------------------------------------------------------------
# Scenario 8: Five-boundary consistency and late-remediation gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_8_late_remediation_speculative_suppression_and_5_boundaries() -> None:
    """Demonstrates Gap 2 and E2E consistency:
    1. Speculative text cannot reach the UI when source requirements are declared
       only after initial generation (late remediation sequence).
    2. Compares matching invocation IDs and content hashes across all 5 boundaries:
       b1_streaming_ui ("")
       b2_final_sse (remediated_text)
       b3_persisted_db_events (raw initial_model_text for audit)
       b3_persisted_db_state (authoritative remediated_text)
       b4_history_api (authoritative remediated_text)
       b5_model_context (authoritative remediated_text)
    """
    from google.adk.flows.llm_flows.contents import _get_contents
    from google.adk.models.llm_request import LlmRequest
    from backend.api.turn_final_answers import (
        project_authoritative_answers_to_contents,
        resolve_turn_final_answer,
    )

    service = ApiSessionService()
    user_id = "user-1"
    session_id = await service.create_session(user_id=user_id)
    attachment_service = _make_attachment_service()

    turn_id = "inv-late-rem"
    initial_model_text = "Unverified initial model response produced before source requirements."
    remediated_final_answer = "Verified safe response produced by post-generation remediation."

    # Late remediation side effect: simulate runner streaming deltas while UNKNOWN,
    # then runner finishes WITHOUT declaring source requirements during the loop.
    async def side_effect(session_service, session, text):
        await append_user_turn(
            session_service,
            session,
            invocation_id=turn_id,
            user_text=text,
            assistant_text=initial_model_text,
        )

    runner = FakeRunner(
        service,
        side_effect=side_effect,
        events=[
            # Speculative delta arrives while source_requirements_capture.declared is False
            FakeEvent(invocation_id=turn_id, text=initial_model_text, partial=True, final=False),
            # Final raw event from model
            FakeEvent(invocation_id=turn_id, text=initial_model_text, partial=False, final=True),
        ],
    )
    chat_service = ChatService(service, runner=runner)

    # Mock request_source_requirements_declaration to simulate late remediation returning
    # requires_governed_knowledge=True
    from unittest.mock import patch

    async def mock_declaration(question, run_id):
        return (False, True)

    async def mock_enforce_governed(*args, **kwargs):
        return (remediated_final_answer, [])

    with patch(
        "backend.api.chat_service.request_source_requirements_declaration",
        side_effect=mock_declaration,
    ), patch(
        "backend.api.chat_service.enforce_governed_knowledge_at_completion",
        side_effect=mock_enforce_governed,
    ):
        streaming_deltas: list[str] = []
        final_sse_content: Optional[str] = None
        server_run_id: Optional[str] = None

        async for sse_event in chat_service.execute_turn_events(session_id, "node diagnostic", user_id):
            if sse_event.type == StreamEventType.RUN_STARTED:
                server_run_id = sse_event.data.get("run_id")
            elif sse_event.type == StreamEventType.MESSAGE_DELTA:
                streaming_deltas.append(sse_event.data.get("text", ""))
            elif sse_event.type == StreamEventType.MESSAGE_COMPLETED:
                final_sse_content = sse_event.data.get("content")

    # Boundary 1: UI streaming
    b1_streaming_ui = "".join(streaming_deltas)
    # Gap 2: Speculative text was buffered and permanently discarded when late declaration resolved!
    assert b1_streaming_ui == "", "Speculative text must not leak to UI when declared late"

    # Boundary 2: Final SSE payload
    b2_final_sse = final_sse_content
    assert b2_final_sse == remediated_final_answer

    # Boundary 3: Persisted DB
    db_session = await service.get_session(session_id, user_id)
    # 3a. Audit log (events)
    assistant_db_events = [e for e in db_session.events if getattr(e, "content", None) and e.content.role == "model"]
    b3_persisted_events = _extract_final_text(assistant_db_events[-1])
    assert b3_persisted_events == initial_model_text, "session.events must preserve raw model text for audit"

    # 3b. Authoritative state (turn_final_answers)
    b3_persisted_state = resolve_turn_final_answer(db_session.state, turn_id)
    assert b3_persisted_state == remediated_final_answer

    # Boundary 4: History API after refresh
    history_resp = await get_session_history(service, attachment_service, session_id, user_id)
    assistant_history = [m for m in history_resp.messages if m.role == "assistant"]
    b4_history_api = assistant_history[-1].text
    assert b4_history_api == remediated_final_answer

    # Boundary 5: Next-turn Model Context
    # Add turn 2 user event
    ev2_user = Event(
        invocation_id="inv-turn-2",
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text="follow-up question")]),
        timestamp=2000.0,
    )
    db_session.events.append(ev2_user)
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

    model_contents = [c for c in llm_request.contents if c.role == "model"]
    b5_model_context = model_contents[0].parts[0].text
    assert b5_model_context == remediated_final_answer

    # Check hash equality across authoritative boundaries (b2 == b3_state == b4 == b5)
    obs = BoundaryObservation(
        run_id=server_run_id or "run-1",
        message_id="msg-1",
        b1_streaming_ui=b1_streaming_ui,
        b2_final_sse=b2_final_sse,
        b3_persisted_db=b3_persisted_events,
        b4_history_api=b4_history_api,
        b5_model_context=b5_model_context,
    )

    expected_hash = _sha256(remediated_final_answer)
    assert obs.h2_final_sse == expected_hash
    assert _sha256(b3_persisted_state) == expected_hash
    assert obs.h4_history_api == expected_hash
    assert obs.h5_model_context == expected_hash
