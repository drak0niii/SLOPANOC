"""P4B.3 -- fast path for first-time direct/exact Teams reads.

Two tiers of coverage, matching this pass's own "verify against source,
don't guess" discipline for anything touching ADK internals:

  1. An ADK-MECHANISM sanity check: a REAL `google.adk.runners.Runner`
     driving a REAL `LlmAgent`, with a fake `BaseLlm` standing in for
     Gemini -- proves the actual claim direct_read_fast_path.py's own
     docstring makes (a `before_model_callback` returning a non-`None`
     `LlmResponse` skips the real model call and ends the agent's turn),
     rather than merely asserting my own Python logic ran.

  2. Domain-logic tests for `_capture_unique_match_for_fast_path`/
     `_fast_path_before_model_callback`/`_fast_path_incident_manager`,
     using the SAME fake-Runner-class / monkeypatched-gateway patterns
     already established throughout this test suite (P4B/P4B.1/P4B.2).
     `execute_read_continuation` itself is monkeypatched rather than
     exercised for real in most of these -- its own correctness is
     already covered extensively by test_p4b_incident_manager_call_
     graph.py and friends (frozen, untouched by this pass); these tests
     are about THIS module's own bridging logic.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.agents import Agent
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from backend.agents.team_manager import direct_read_fast_path as fast_path_module
from backend.agents.team_manager.direct_read_fast_path import (
    _capture_unique_match_for_fast_path,
    _fast_path_before_model_callback,
    _fast_path_incident_manager,
    _FAST_PATH_PENDING_STATE_KEY,
)
from backend.agents.incident_manager.schemas import IncidentManagerOutcome
from backend.selection.schemas import ReadOperation, ResolvedReadContinuation


# --- Tier 1: ADK mechanism sanity check --------------------------------


class _CountingFakeLlm(BaseLlm):
    """A minimal, real `BaseLlm` -- first call returns a function call to
    `probe_tool`; ANY further call raises, so the test fails loudly if
    the short-circuit did not actually prevent a second real model call.
    """

    calls: int = 0

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("real model was called a second time -- shortcut did not engage")
        part = types.Part.from_function_call(name="probe_tool", args={})
        part.function_call.id = "call-1"
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def probe_tool(tool_context: Any = None) -> dict[str, Any]:
    return {"ok": True}


@pytest.mark.asyncio
async def test_before_model_callback_shortcut_skips_the_real_second_model_call() -> None:
    fake_llm = _CountingFakeLlm(model="fake")

    def _shortcut_after_probe(callback_context: Any, llm_request: Any) -> Optional[LlmResponse]:
        # Fires on whichever turn follows the tool call -- mirrors
        # direct_read_fast_path.py's own marker-driven shortcut, but with
        # a plain counter standing in for the `temp:` state marker.
        if callback_context.state.get("temp:_probe_seen"):
            return LlmResponse(
                content=types.Content(role="model", parts=[types.Part.from_text(text="synthesized, not generated")])
            )
        return None

    def _mark_after_probe(tool: Any, args: dict, tool_context: Any, tool_response: Any) -> None:
        tool_context.state["temp:_probe_seen"] = True
        return None

    probe_agent = Agent(
        name="probe",
        model=fake_llm,
        instruction="probe",
        tools=[probe_tool],
        after_tool_callback=_mark_after_probe,
        before_model_callback=_shortcut_after_probe,
    )

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="u1")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )

    last_text: Optional[str] = None
    async for event in runner.run_async(
        user_id="u1", session_id=session.id, new_message=types.Content(role="user", parts=[types.Part.from_text(text="go")])
    ):
        if event.content and event.content.parts:
            texts = [p.text for p in event.content.parts if p.text]
            if texts:
                last_text = texts[-1]
    await runner.close()

    assert fake_llm.calls == 1  # the real model was invoked exactly once, never for the "second turn"
    assert last_text == "synthesized, not generated"


# --- Tier 2: domain logic -------------------------------------------------


class _Ctx:
    def __init__(self, state: Optional[dict] = None) -> None:
        self.state = state if state is not None else {}


def _matched_response(chat_id: str = "chat-b2-id", title: str = "Network Operations Daily") -> dict[str, Any]:
    return {"chats": [], "match": "matched", "matched_chat": {"chat_id": chat_id, "title": title}}


def _teams_list_chats_tool() -> Any:
    return type("Tool", (), {"name": "teams_list_chats"})()


def test_capture_marks_state_on_unique_read_match() -> None:
    ctx = _Ctx()
    _capture_unique_match_for_fast_path(
        _teams_list_chats_tool(),
        {"topic": "Network Operations Daily", "pending_question": None, "pending_time_range": None},
        ctx,
        _matched_response(),
    )
    pending = ctx.state[_FAST_PATH_PENDING_STATE_KEY]
    assert pending["chat_id"] == "chat-b2-id"
    assert pending["chat_title"] == "Network Operations Daily"
    assert pending["operation"] == ReadOperation.SUMMARIZE.value


def test_capture_does_not_mark_state_for_a_write_resolution() -> None:
    ctx = _Ctx()
    _capture_unique_match_for_fast_path(
        _teams_list_chats_tool(),
        {"topic": "Ops Bridge", "pending_write_message": "please deploy the fix"},
        ctx,
        _matched_response(chat_id="chat-write", title="Ops Bridge"),
    )
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


@pytest.mark.parametrize("match_outcome", ["ambiguous", "not_found"])
def test_capture_does_not_mark_state_for_non_unique_resolution(match_outcome: str) -> None:
    ctx = _Ctx()
    _capture_unique_match_for_fast_path(
        _teams_list_chats_tool(),
        {"topic": "Knowledge Management Daily Sync"},
        ctx,
        {"chats": [], "match": match_outcome},
    )
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


def test_capture_ignores_calls_from_a_different_tool() -> None:
    ctx = _Ctx()
    other_tool = type("Tool", (), {"name": "teams_get_messages"})()
    _capture_unique_match_for_fast_path(other_tool, {}, ctx, _matched_response())
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


def test_capture_preserves_pending_question_and_time_range() -> None:
    ctx = _Ctx()
    _capture_unique_match_for_fast_path(
        _teams_list_chats_tool(),
        {
            "topic": "Network Operations Daily",
            "pending_question": "what are the open action items?",
            "pending_time_range": "the last 7 days",
            "pending_operation": "get_messages",
        },
        ctx,
        _matched_response(),
    )
    pending = ctx.state[_FAST_PATH_PENDING_STATE_KEY]
    assert pending["question"] == "what are the open action items?"
    assert pending["requested_time_range"] == "the last 7 days"
    assert pending["operation"] == ReadOperation.GET_MESSAGES.value


class _FakeInvocationContext:
    def __init__(self, session_service: Any, user_id: str, session: Any, invocation_id: str) -> None:
        self.session_service = session_service
        self.user_id = user_id
        self.session = session
        self.invocation_id = invocation_id


class _FakeSession:
    def __init__(self, session_id: str, state: dict) -> None:
        self.id = session_id
        self.state = state


class _FakeCallbackContext:
    def __init__(self, state: dict, invocation_context: Any) -> None:
        self.state = state
        self._invocation_context = invocation_context


@pytest.mark.asyncio
async def test_before_model_callback_noop_when_no_marker_present() -> None:
    ctx = _FakeCallbackContext({}, invocation_context=None)
    result = await _fast_path_before_model_callback(ctx, llm_request=None)
    assert result is None


@pytest.mark.asyncio
async def test_before_model_callback_runs_fast_path_and_clears_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_continuation: dict[str, Any] = {}

    async def fake_execute_read_continuation(**kwargs: Any) -> dict[str, Any]:
        captured_continuation.update(kwargs)
        return {"outcome": "ok", "chat_id": "chat-b2-id", "chat_title": "Network Operations Daily", "summary": "Done."}

    monkeypatch.setattr(fast_path_module, "execute_read_continuation", fake_execute_read_continuation)
    # SOURCE/PROVENANCE REGRESSION FIX: the callback now requires a bound
    # run_id (see direct_read_fast_path.py's own docstring) -- this
    # bare-unit-test harness never runs through chat_service.py's own
    # `bind_run_id`, so it must supply one directly.
    monkeypatch.setattr(fast_path_module, "current_run_id", lambda: "run-marker-test")

    state = {
        _FAST_PATH_PENDING_STATE_KEY: {
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "question": None,
            "operation": "summarize",
            "requested_time_range": None,
        }
    }
    invocation_context = _FakeInvocationContext(
        session_service=object(), user_id="api-user", session=_FakeSession("inner-session", dict(state)), invocation_id="inv-1"
    )
    ctx = _FakeCallbackContext(state, invocation_context)

    result = await _fast_path_before_model_callback(ctx, llm_request=None)

    assert result is not None
    assert state[_FAST_PATH_PENDING_STATE_KEY] is None  # single-use marker cleared
    continuation = captured_continuation["continuation"]
    assert isinstance(continuation, ResolvedReadContinuation)
    assert continuation.selected_chat_id == "chat-b2-id"
    assert continuation.selected_chat_topic == "Network Operations Daily"

    response_text = "".join(p.text for p in result.content.parts if p.text)
    payload = json.loads(response_text)
    assert payload["outcome"] == "ok"
    assert payload["chat_id"] == "chat-b2-id"


@pytest.mark.asyncio
async def test_before_model_callback_safe_fallback_when_execute_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_read_continuation(**kwargs: Any) -> Optional[dict[str, Any]]:
        return None

    monkeypatch.setattr(fast_path_module, "execute_read_continuation", fake_execute_read_continuation)
    monkeypatch.setattr(fast_path_module, "current_run_id", lambda: "run-fallback-test")

    state = {
        _FAST_PATH_PENDING_STATE_KEY: {
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "question": None,
            "operation": "summarize",
            "requested_time_range": None,
        }
    }
    invocation_context = _FakeInvocationContext(
        session_service=object(), user_id="api-user", session=_FakeSession("inner-session", dict(state)), invocation_id="inv-1"
    )
    ctx = _FakeCallbackContext(state, invocation_context)

    result = await _fast_path_before_model_callback(ctx, llm_request=None)

    assert result is not None
    response_text = "".join(p.text for p in result.content.parts if p.text)
    payload = json.loads(response_text)
    assert payload["outcome"] == IncidentManagerOutcome.ERROR.value
    assert "evidence" not in payload or not payload.get("evidence")


# --- Wiring: the variant used by team_manager -----------------------------


def test_fast_path_variant_keeps_full_tool_and_schema_surface_unchanged() -> None:
    """Section 47 -- team_manager's own tool-calling surface must be
    byte-for-byte identical to the base agent's: same name, same tools,
    same input/output schema, same instruction.
    """
    from backend.agents.incident_manager.agent import incident_manager

    assert _fast_path_incident_manager.name == incident_manager.name
    assert _fast_path_incident_manager.tools == incident_manager.tools
    assert _fast_path_incident_manager.input_schema is incident_manager.input_schema
    assert _fast_path_incident_manager.output_schema is incident_manager.output_schema
    assert _fast_path_incident_manager.instruction is incident_manager.instruction
    assert _fast_path_incident_manager.after_agent_callback is incident_manager.after_agent_callback


def test_fast_path_shortcut_is_ordered_before_p2_timing_callback() -> None:
    """P2 must never log an unpaired `model_call_start` for a turn the
    shortcut prevented from ever reaching the real model -- see this
    module's own docstring, part 2, "MUST RUN BEFORE P2's OWN
    before_model_call".
    """
    callbacks = _fast_path_incident_manager.before_model_callback
    assert isinstance(callbacks, list)
    assert callbacks[0] is fast_path_module._fast_path_before_model_callback


def test_team_manager_incident_manager_tool_uses_the_fast_path_variant() -> None:
    from backend.agents.team_manager.agent import incident_manager_tool

    assert incident_manager_tool.agent is _fast_path_incident_manager


# --- Tier 3: end-to-end through the real `_fast_path_incident_manager` ---
# --- agent object, via a real Runner (only the gateway HTTP call and    ---
# --- the nested `execute_read_continuation` synthesis call are faked)   ---


class _SingleCallFakeLlm(BaseLlm):
    """Yields a `teams_list_chats` function call on its one and only real
    turn. Raises if ever called again -- the strongest possible proof
    that no second generic-instruction model turn happened.
    """

    calls: int = 0
    topic: str = "Network Operations Daily"

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("real incident_manager model was called a second time")
        part = types.Part.from_function_call(name="teams_list_chats", args={"topic": self.topic})
        part.function_call.id = "call-1"
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


@pytest.mark.asyncio
async def test_end_to_end_unique_match_never_reaches_a_second_real_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.gateway import power_automate_client as pac_module

    def fake_post(url, json, timeout):
        from backend.tests._fakes import FakeResponse, chat

        if json.get("operation") == "teams.listChats":
            return FakeResponse(200, [chat("chat-b2-id", "Network Operations Daily")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    captured: dict[str, Any] = {}

    async def fake_execute_read_continuation(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "outcome": "ok",
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "summary": "Fast-path synthesis result.",
        }

    monkeypatch.setattr(fast_path_module, "execute_read_continuation", fake_execute_read_continuation)

    fake_llm = _SingleCallFakeLlm(model="fake")
    probe_agent = _fast_path_incident_manager.model_copy(update={"model": fake_llm})

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="u1")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )

    # SOURCE/PROVENANCE REGRESSION FIX: `_fast_path_before_model_callback`
    # now requires a bound `run_id` (see direct_read_fast_path.py's own
    # docstring for why an unbound fallback was found to leak) -- this
    # test drives `_fast_path_incident_manager` directly, outside chat_
    # service.py's own orchestration, so it must bind one itself, exactly
    # like test_p4b3_completion_trusted_presentation.py's own equivalent
    # tests already do.
    from backend.api.turn_context import bind_run_id, reset_run_id

    token = bind_run_id("run-single-call-test")
    last_text: Optional[str] = None
    request_text = json.dumps({"chat_topic": "Network Operations Daily"})
    try:
        async for event in runner.run_async(
            user_id="u1",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=request_text)]),
        ):
            if event.content and event.content.parts:
                texts = [p.text for p in event.content.parts if p.text]
                if texts:
                    last_text = texts[-1]
    finally:
        reset_run_id(token)
        await runner.close()

    assert fake_llm.calls == 1  # exactly one real incident_manager model call total
    continuation = captured["continuation"]
    assert continuation.selected_chat_id == "chat-b2-id"

    payload = json.loads(last_text)
    assert payload["outcome"] == "ok"
    assert payload["summary"] == "Fast-path synthesis result."


@pytest.mark.asyncio
async def test_end_to_end_forwards_requested_time_range_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 24/25/38 (Test K): a relative time expression must still
    reach `execute_read_continuation` (whose own, frozen dispatch keeps
    that branch model-driven -- see read_continuation_execution.py's own
    docstring) verbatim, never parsed/normalized here.
    """
    from backend.gateway import power_automate_client as pac_module

    def fake_post(url, json, timeout):
        from backend.tests._fakes import FakeResponse, chat

        if json.get("operation") == "teams.listChats":
            return FakeResponse(200, [chat("chat-b2-id", "Network Operations Daily")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    captured: dict[str, Any] = {}

    async def fake_execute_read_continuation(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"outcome": "ok", "chat_id": "chat-b2-id", "chat_title": "Network Operations Daily", "summary": "ok"}

    monkeypatch.setattr(fast_path_module, "execute_read_continuation", fake_execute_read_continuation)

    class _TimeRangeFakeLlm(BaseLlm):
        calls: int = 0

        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("real model called twice")
            part = types.Part.from_function_call(
                name="teams_list_chats",
                args={
                    "topic": "Network Operations Daily",
                    "pending_time_range": "the last 7 days",
                    "pending_operation": "summarize",
                },
            )
            part.function_call.id = "call-1"
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    fake_llm = _TimeRangeFakeLlm(model="fake")
    probe_agent = _fast_path_incident_manager.model_copy(update={"model": fake_llm})

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="u1")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )
    request_text = json.dumps({"chat_topic": "Network Operations Daily", "requested_time_range": "the last 7 days"})

    # See the sibling test above for why this must bind a run_id.
    from backend.api.turn_context import bind_run_id, reset_run_id

    token = bind_run_id("run-time-range-fast-path-test")
    try:
        async for _event in runner.run_async(
            user_id="u1", session_id=session.id, new_message=types.Content(role="user", parts=[types.Part.from_text(text=request_text)])
        ):
            pass
    finally:
        reset_run_id(token)
        await runner.close()

    assert fake_llm.calls == 1
    assert captured["continuation"].requested_time_range == "the last 7 days"


@pytest.mark.asyncio
async def test_end_to_end_ambiguous_match_never_engages_the_shortcut(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 22/31 (Test D): ambiguous resolution must still fall
    through to `incident_manager`'s own SECOND real model turn (it, not
    this module, decides how to phrase `outcome="ambiguous"`) -- the
    shortcut must never fire, and `execute_read_continuation` must never
    be called.
    """
    from backend.gateway import power_automate_client as pac_module

    def fake_post(url, json, timeout):
        from backend.tests._fakes import FakeResponse, chat

        if json.get("operation") == "teams.listChats":
            return FakeResponse(
                200,
                [chat("chat-a", "Knowledge Management Daily Sync"), chat("chat-b", "Knowledge Management Daily Sync")],
            )
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    execute_calls = 0

    async def fake_execute_read_continuation(**kwargs: Any) -> dict[str, Any]:
        nonlocal execute_calls
        execute_calls += 1
        return {"outcome": "ok"}

    monkeypatch.setattr(fast_path_module, "execute_read_continuation", fake_execute_read_continuation)

    class _TwoTurnFakeLlm(BaseLlm):
        calls: int = 0

        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
            self.calls += 1
            if self.calls == 1:
                part = types.Part.from_function_call(
                    name="teams_list_chats", args={"topic": "Knowledge Management Daily Sync"}
                )
                part.function_call.id = "call-1"
                yield LlmResponse(content=types.Content(role="model", parts=[part]))
                return
            # Second, REAL turn -- exactly what should still happen for a
            # genuinely ambiguous result.
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=json.dumps({"outcome": "ambiguous", "candidate_titles": []}))],
                )
            )

    fake_llm = _TwoTurnFakeLlm(model="fake")
    probe_agent = _fast_path_incident_manager.model_copy(update={"model": fake_llm})

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="u1")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )
    last_text: Optional[str] = None
    request_text = json.dumps({"chat_topic": "Knowledge Management Daily Sync"})
    async for event in runner.run_async(
        user_id="u1", session_id=session.id, new_message=types.Content(role="user", parts=[types.Part.from_text(text=request_text)])
    ):
        if event.content and event.content.parts:
            texts = [p.text for p in event.content.parts if p.text]
            if texts:
                last_text = texts[-1]
    await runner.close()

    assert fake_llm.calls == 2  # the model's own second turn genuinely ran -- shortcut did not fire
    assert execute_calls == 0  # the fast path was never invoked
    payload = json.loads(last_text)
    assert payload["outcome"] == "ambiguous"


@pytest.mark.asyncio
async def test_authoritative_chat_id_cannot_be_substituted_via_tool_args() -> None:
    """Section 8/30 (Test C): the marker's `chat_id` is derived ONLY from
    `tool_response["matched_chat"]["chat_id"]` (what `teams_list_chats`
    itself, deterministically, resolved) -- never from any model-supplied
    argument, even a deliberately mismatched one.
    """
    ctx = _Ctx()
    _capture_unique_match_for_fast_path(
        _teams_list_chats_tool(),
        {"topic": "some other name entirely", "chat_id": "attacker-supplied-id"},
        ctx,
        _matched_response(chat_id="chat-b2-id", title="Network Operations Daily"),
    )
    pending = ctx.state[_FAST_PATH_PENDING_STATE_KEY]
    assert pending["chat_id"] == "chat-b2-id"


@pytest.mark.asyncio
async def test_before_model_callback_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 42 (Test O): a cancellation during the fast path's own
    retrieval/synthesis must propagate normally, never be swallowed.
    """
    import asyncio

    async def cancelling_execute_read_continuation(**kwargs: Any) -> dict[str, Any]:
        raise asyncio.CancelledError()

    monkeypatch.setattr(fast_path_module, "execute_read_continuation", cancelling_execute_read_continuation)
    monkeypatch.setattr(fast_path_module, "current_run_id", lambda: "run-cancellation-test")

    state = {
        _FAST_PATH_PENDING_STATE_KEY: {
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "question": None,
            "operation": "summarize",
            "requested_time_range": None,
        }
    }
    invocation_context = _FakeInvocationContext(
        session_service=object(), user_id="api-user", session=_FakeSession("inner-session", dict(state)), invocation_id="inv-1"
    )
    ctx = _FakeCallbackContext(state, invocation_context)

    with pytest.raises(asyncio.CancelledError):
        await _fast_path_before_model_callback(ctx, llm_request=None)
