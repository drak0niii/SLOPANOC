"""URGENT R2 fix: the resolved-continuation retrieval tool must never ask
Gemini to supply or reconstruct the destination chat id.

ROOT CAUSE (found by direct inspection of the pre-fix implementation, not
guessed -- see read_continuation_execution.py's own "URGENT R2 FIX"
docstrings for the full citation trail): the previous `_enforced_teams_
get_messages` wrapper still exposed `chat_id: str` as a REQUIRED,
model-visible parameter -- the model had to retype the resolved chat id
from its own prompt's `{resolved_chat_id?}` placeholder, and the wrapper
REJECTED any call whose `chat_id` argument did not byte-for-byte match
`_active_continuation_chat_id.get()`, returning a safe error to the model
instead of ever reaching Power Automate. Live logs showed exactly this
shape: a `function_call` happened, but no `power_automate_gateway
operation=teams.getMessages` line ever appeared.

THE FIX: `get_resolved_chat_messages` (read_continuation_execution.py) has
NO `chat_id` parameter in its schema at all -- the authoritative id is
injected internally from a server-bound `ContextVar`, never accepted as a
model argument. These tests prove the fix at the correct layer: the REAL
ADK-generated function declaration (never a re-derived approximation of
what ADK would produce).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import pytest
from google.adk.events import Event, EventActions
from google.adk.tools import FunctionTool
from google.genai import types

from backend.agents.team_manager import read_continuation_execution as execution_module
from backend.agents.team_manager.read_continuation_execution import (
    _CONTINUATION_INCIDENT_MANAGER,
    execute_read_continuation,
    get_resolved_chat_messages,
)
from backend.api.session_service import ApiSessionService
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.attachments.storage import ChatAttachmentStorage
from backend.selection.schemas import ResolvedReadContinuation
from backend.tests._fakes import FakeResponse, chat, message
from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY


def _attachment_service() -> AttachmentService:
    """POST-5.1 B6 -- this file's own continuations never carry
    `attachment_ids`, so a fresh in-memory service only needs to satisfy
    `execute_read_continuation`'s signature."""
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


def _attachment_storage() -> ChatAttachmentStorage:
    return ChatAttachmentStorage(None)


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


class _FakeEvent:
    def __init__(self, content: Any) -> None:
        self.content = content


def _resolved_continuation(
    chat_id: str = "chat-b2-id",
    chat_topic: str = "Chat B2",
    # P4B: this whole file specifically tests `get_resolved_chat_messages`
    # -- the MODEL-DRIVEN retrieval tool -- which is only reachable when
    # `requested_time_range` is present (see read_continuation_execution
    # .py's own docstring on why the no-time-range case now retrieves
    # deterministically instead, covered separately in test_p4b_incident_
    # manager_call_graph.py). Defaulting it here keeps every test in this
    # file on the path it was actually written to exercise.
    requested_time_range: Optional[str] = "the last 7 days",
) -> ResolvedReadContinuation:
    return ResolvedReadContinuation(
        selected_chat_id=chat_id, selected_chat_topic=chat_topic, requested_time_range=requested_time_range
    )


# --- TEST 1: tool schema -----------------------------------------------------


def test_1_resolved_retrieval_tool_present_with_no_chat_id_in_its_schema() -> None:
    tool_names = {getattr(t, "__name__", getattr(t, "name", None)) for t in _CONTINUATION_INCIDENT_MANAGER.tools}
    assert "get_resolved_chat_messages" in tool_names
    assert "teams_list_chats" not in tool_names
    assert "teams_get_messages" not in tool_names  # the chat_id-bearing tool itself is gone too

    declaration = FunctionTool(func=get_resolved_chat_messages)._get_declaration()
    param_names = set(declaration.parameters.properties.keys()) if declaration.parameters else set()
    assert "chat_id" not in param_names
    # The model may still decide time range/pagination -- those remain.
    assert "from_datetime" in param_names
    assert "to_datetime" in param_names


def test_1_declaration_has_no_chat_id_property_of_any_kind() -> None:
    """The definitive check: whatever the description prose says, the
    model can only ever SET fields that appear in `parameters.properties`
    -- and `chat_id` is not one of them, checked directly against the
    real, ADK-generated JSON-schema-shaped properties dict.
    """
    declaration = FunctionTool(func=get_resolved_chat_messages)._get_declaration()
    properties = declaration.parameters.properties if declaration.parameters else {}
    assert "chat_id" not in properties


# --- TEST 2: authoritative chat binding --------------------------------------


@pytest.mark.asyncio
async def test_2_underlying_retrieval_receives_the_authoritative_chat_id_not_a_model_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini calls the tool WITHOUT chat_id (it cannot supply one -- the
    schema has no such parameter); the real `teams_get_messages` must
    still receive the authoritative `selected_chat_id`.
    """
    from backend.gateway import power_automate_client as pac_module

    call_counts: dict[str, int] = {}
    received_chat_ids: list[str] = []

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.getMessages":
            received_chat_ids.append(json.get("chatId"))
            return FakeResponse(200, [message("m1", "Alex", "We decided to proceed.", "2026-08-20T09:00:00Z")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    class _ModelCallsToolWithNoChatIdRunner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            self._app_name = app_name
            self._session_service = session_service

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            from backend.agents.incident_manager.evidence import validate_evidence

            session = await self._session_service.get_session(
                app_name=self._app_name, user_id=user_id, session_id=session_id
            )
            ctx = _Ctx(dict(session.state))
            # Exactly what Gemini would send: no chat_id argument at all.
            retrieval = get_resolved_chat_messages(tool_context=ctx)
            assert "error" not in retrieval
            candidate_evidence = [
                {"message_id": m["id"], "author": m["author"], "sent_at": m["sent_at"]} for m in retrieval["messages"]
            ]
            known_ids = set(ctx.state.get(KNOWN_MESSAGE_IDS_STATE_KEY, []))
            validated = validate_evidence(candidate_evidence, known_ids)

            await self._session_service.append_event(
                session,
                Event(
                    author="incident_manager",
                    invocation_id="inv-1",
                    actions=EventActions(state_delta={KNOWN_MESSAGE_IDS_STATE_KEY: list(known_ids)}),
                ),
            )

            payload = {"outcome": "ok", "chat_id": "chat-b2-id", "chat_title": "Chat B2", "summary": "Grounded.", "evidence": validated}
            yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(execution_module, "Runner", _ModelCallsToolWithNoChatIdRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-authoritative",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(chat_id="chat-b2-id"),
    )

    assert result is not None
    assert result["outcome"] == "ok"
    assert received_chat_ids == ["chat-b2-id"]  # the authoritative id, never model-supplied
    assert call_counts.get("teams.getMessages", 0) == 1
    assert call_counts.get("teams.listChats", 0) == 0
    assert len(result["evidence"]) == 1


# --- TEST 3: model cannot override destination -------------------------------


def test_3_schema_has_no_field_the_model_could_use_to_supply_a_destination() -> None:
    """Structural proof: no `chat_id`, no alternate id field, no topic/name
    field exists anywhere in the tool's real parameter schema for the
    model to set, regardless of what it might try to pass.
    """
    declaration = FunctionTool(func=get_resolved_chat_messages)._get_declaration()
    param_names = set(declaration.parameters.properties.keys()) if declaration.parameters else set()
    for forbidden in ("chat_id", "chatId", "id", "alternate_id", "topic", "chat_topic", "chat_name", "destination"):
        assert forbidden not in param_names


def test_3_passing_a_chat_id_keyword_argument_is_a_typeerror_not_silently_accepted() -> None:
    """Even a caller that IGNORES the schema and tries to pass `chat_id`
    directly cannot -- the function itself has no such parameter, so
    Python rejects the call before any of this module's own logic runs.
    """
    with pytest.raises(TypeError):
        get_resolved_chat_messages(chat_id="some-other-chat-id", tool_context=_Ctx({}))  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_3_authoritative_destination_is_the_only_possible_outcome_across_repeated_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binds two DIFFERENT continuations in sequence (simulating two
    different resumed selections) and confirms each one's retrieval is
    bound to ITS OWN authoritative id -- never influenced by anything a
    caller might attempt, and never leaking from one to the other.
    """
    from backend.gateway import power_automate_client as pac_module

    received_chat_ids: list[str] = []

    def fake_post(url, json, timeout):
        if json.get("operation") == "teams.getMessages":
            received_chat_ids.append(json.get("chatId"))
            return FakeResponse(200, [])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    class _PlainRetrievalRunner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            self._app_name = app_name
            self._session_service = session_service

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            session = await self._session_service.get_session(
                app_name=self._app_name, user_id=user_id, session_id=session_id
            )
            ctx = _Ctx(dict(session.state))
            get_resolved_chat_messages(tool_context=ctx)
            payload = {"outcome": "no_result", "chat_id": None, "chat_title": None}
            yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(execution_module, "Runner", _PlainRetrievalRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-a",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(chat_id="chat-a-id", chat_topic="Chat A"),
    )

    session = await service.get_session(session_id)
    await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-b",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(chat_id="chat-b-id", chat_topic="Chat B"),
    )

    assert received_chat_ids == ["chat-a-id", "chat-b-id"]


# --- TEST 4: missing authoritative binding -----------------------------------


def test_4_calling_the_tool_with_no_bound_continuation_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    """Outside of `execute_read_continuation`'s own bound scope, the
    ContextVar is unset -- the tool must refuse, never call the gateway.
    """
    with caplog.at_level(logging.WARNING, logger=execution_module.__name__):
        result = get_resolved_chat_messages(tool_context=_Ctx({}))

    assert "error" in result
    assert any("resolved_chat_retrieval_rejected" in r.getMessage() for r in caplog.records)
    assert any("missing_binding" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_4_missing_binding_never_reaches_the_gateway_or_produces_a_trusted_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.gateway import power_automate_client as pac_module

    call_counts: dict[str, int] = {}

    def fake_post(url, json, timeout):
        call_counts[json.get("operation")] = call_counts.get(json.get("operation"), 0) + 1
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    class _NoBindingRunner:
        """Simulates the tool being invoked with the ContextVar reset
        (defensive -- should be structurally impossible in production,
        since this tool only ever exists on the continuation-specific
        agent, run only from inside the bound scope -- but proven here
        directly regardless).
        """

        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            self._app_name = app_name
            self._session_service = session_service

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            token = execution_module._active_continuation_chat_id.set(None)
            try:
                result = get_resolved_chat_messages(tool_context=_Ctx({}))
            finally:
                execution_module._active_continuation_chat_id.reset(token)
            assert "error" in result
            payload = {"outcome": "ok", "chat_id": "chat-x", "chat_title": "X", "summary": "Fabricated.", "evidence": []}
            yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(execution_module, "Runner", _NoBindingRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-missing-binding",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(),
    )

    assert call_counts.get("teams.getMessages", 0) == 0
    assert call_counts.get("teams.listChats", 0) == 0
    assert result is not None
    assert result["outcome"] == "error"  # the fabricated "ok" never survives -- R2 verification rejects it


# --- TEST 5: duplicate retrieval attempt --------------------------------------


@pytest.mark.asyncio
async def test_5_second_retrieval_attempt_in_the_same_continuation_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.gateway import power_automate_client as pac_module

    call_counts: dict[str, int] = {}

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.getMessages":
            return FakeResponse(200, [message("m1", "Alex", "Hello.", "2026-08-20T09:00:00Z")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    class _DoubleCallRunner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            self._app_name = app_name
            self._session_service = session_service

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            session = await self._session_service.get_session(
                app_name=self._app_name, user_id=user_id, session_id=session_id
            )
            ctx = _Ctx(dict(session.state))
            first = get_resolved_chat_messages(tool_context=ctx)
            second = get_resolved_chat_messages(tool_context=ctx)  # a stray/duplicate model call
            assert "error" not in first
            assert "error" in second

            await self._session_service.append_event(
                session,
                Event(
                    author="incident_manager",
                    invocation_id="inv-1",
                    actions=EventActions(state_delta={KNOWN_MESSAGE_IDS_STATE_KEY: [m["id"] for m in first["messages"]]}),
                ),
            )
            payload = {"outcome": "ok", "chat_id": "chat-b2-id", "chat_title": "Chat B2", "summary": "Hello.", "evidence": []}
            yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(execution_module, "Runner", _DoubleCallRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-double",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(),
    )

    assert result is not None
    assert call_counts.get("teams.getMessages", 0) == 1  # never two real PA requests


# --- TEST 6: full ambiguous -> selection -> summary flow --------------------


@pytest.mark.asyncio
async def test_6_full_ambiguous_to_resolved_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api import selection_service
    from backend.api.chat_service import ChatService
    from backend.api.streaming_events import StreamEventType
    from backend.selection.service import load_active_selection
    from backend.tests._api_fakes import FakeEvent, FakeFunctionCall, FakeFunctionResponse, FakeRunner
    from backend.tests.test_p0_p1_live_incident_regression import _RealRetrievalFakeIncidentManagerRunner

    call_counts: dict[str, int] = {}

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, [chat("kb-a", "Knowledge Base Weekly"), chat("kb-b", "Knowledge Base Daily")])
        if operation == "teams.getMessages":
            return FakeResponse(200, [message("m1", "Sam", "We reviewed priorities.", "2026-08-20T09:00:00Z")])
        return FakeResponse(200, [])

    from backend.gateway import power_automate_client as pac_module

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    monkeypatch.setattr(execution_module, "Runner", _RealRetrievalFakeIncidentManagerRunner)

    service = ApiSessionService()
    session_id = await service.create_session()

    async def ambiguous_side_effect(session_service, session, text):
        from backend.tools.teams.list_chats import teams_list_chats

        ctx = _Ctx(dict(session.state))
        teams_list_chats(topic="Knowledge Base", pending_operation="summarize", tool_context=ctx)
        await session_service.persist_state_delta(session, dict(ctx.state))

    chat_service_1 = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=[
                FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
                FakeEvent(
                    final=False,
                    function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "selection_needed"})],
                ),
                FakeEvent(text="I found similar chats -- please pick one below.", final=True),
            ],
            side_effect=ambiguous_side_effect,
        ),
    )
    collected_1 = [e async for e in chat_service_1.execute_turn_events(session_id, "summarize Knowledge Base", "api-user")]
    assert any(e.type == StreamEventType.SELECTION_PENDING for e in collected_1)
    assert call_counts.get("teams.listChats", 0) == 1

    pending = load_active_selection((await service.get_session(session_id)).state)
    chosen = next(o for o in pending.options if o.label == "Knowledge Base Daily")
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    chat_service_2 = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "WRONG -- normal runner must not be used here."),
        presentation_runner=FakeRunner(service, respond=lambda t: "Here is the Knowledge Base Daily summary."),
    )
    collected_2 = [e async for e in chat_service_2.execute_turn_events(session_id, choose_response.resume_message, "api-user")]

    assert call_counts.get("teams.listChats", 0) == 1  # zero NEW listChats post-selection
    assert call_counts.get("teams.getMessages", 0) == 1
    completed = next(e for e in collected_2 if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Here is the Knowledge Base Daily summary."
    assert "source" in completed.data
    assert completed.data["source"]["message_count"] == 1


# --- TEST 7: real no_result case ---------------------------------------------


@pytest.mark.asyncio
async def test_7_no_result_is_accepted_when_retrieval_genuinely_returned_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.gateway import power_automate_client as pac_module

    call_counts: dict[str, int] = {}

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.getMessages":
            return FakeResponse(200, [])  # a real, empty result
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    class _NoResultRunner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            self._app_name = app_name
            self._session_service = session_service

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            session = await self._session_service.get_session(
                app_name=self._app_name, user_id=user_id, session_id=session_id
            )
            ctx = _Ctx(dict(session.state))
            retrieval = get_resolved_chat_messages(tool_context=ctx)
            assert "error" not in retrieval
            assert retrieval["messages"] == []

            await self._session_service.append_event(
                session,
                Event(
                    author="incident_manager",
                    invocation_id="inv-1",
                    actions=EventActions(state_delta={KNOWN_MESSAGE_IDS_STATE_KEY: []}),
                ),
            )
            payload = {"outcome": "no_result", "chat_id": "chat-b2-id", "chat_title": "Chat B2"}
            yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(execution_module, "Runner", _NoResultRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-no-result",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(),
    )

    assert result is not None
    assert result["outcome"] == "no_result"  # retrieval verification passed -- it really happened
    assert call_counts.get("teams.getMessages", 0) == 1
    assert not result.get("evidence")


# --- TEST 8: retrieval gateway failure ---------------------------------------


@pytest.mark.asyncio
async def test_8_gateway_failure_produces_a_safe_error_never_a_fabricated_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.gateway.safe_error import internal_error

    call_counts: dict[str, int] = {}

    def raising_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        raise internal_error("The Teams connector is temporarily unavailable.")

    from backend.gateway import power_automate_client as pac_module

    monkeypatch.setattr(pac_module.requests, "post", raising_post)

    class _GatewayFailureRunner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            self._app_name = app_name
            self._session_service = session_service

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            session = await self._session_service.get_session(
                app_name=self._app_name, user_id=user_id, session_id=session_id
            )
            ctx = _Ctx(dict(session.state))
            retrieval = get_resolved_chat_messages(tool_context=ctx)
            assert "error" in retrieval  # the gateway failure surfaces as a safe error

            payload = {"outcome": "error", "detail": "The Teams connector is temporarily unavailable."}
            yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(execution_module, "Runner", _GatewayFailureRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-gateway-failure",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(),
    )

    assert result is not None
    assert result["outcome"] == "error"
    assert call_counts.get("teams.getMessages", 0) == 1  # exactly one attempt, no retry
    assert call_counts.get("teams.listChats", 0) == 0
    assert not result.get("summary")
