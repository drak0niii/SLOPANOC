"""Architectural performance tests for the dedicated latency-diagnosis
pass -- these prove STRUCTURE (call counts, header presence, redaction,
client reuse), never external wall-clock timing (instruction section 22:
"Do NOT create brittle tests asserting external wall-clock timing").
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.agents.incident_manager.agent import incident_manager
from backend.agents.team_manager.agent import team_manager
from backend.agents.team_manager.read_continuation_execution import _SPECIALIST_RUN_CONFIG
from backend.api.app import app
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.perf_timing import (
    after_model_call,
    before_model_call,
    discard_model_call_tracking,
)
from backend.api.session_service import ApiSessionService, get_session_service
from backend.api.streaming_events import StreamEventType
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.config.settings import get_shared_llm
from backend.tests._api_fakes import FakeRunner


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


# --- Section 4/22: "hello" invokes zero Teams tools / no Incident Manager ---


@pytest.mark.asyncio
async def test_hello_turn_invokes_zero_teams_tools_and_no_incident_manager() -> None:
    """A plain, non-Teams-shaped turn with no pending continuation and a
    team_manager stream that never mentions `incident_manager` must never
    trigger the deterministic specialist path, never produce a Source
    reference, and never start a contributors fetch.
    """
    service = ApiSessionService()
    session_id = await service.create_session()

    async def must_not_be_called(*, user_id, continuation, **kwargs):
        raise AssertionError("no continuation exists for a plain hello -- executor must never run")

    async def contributors_must_not_be_called(chat_id):
        raise AssertionError("no Teams chat was ever resolved -- contributors must never be fetched")

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Hello! How can I help you today?"),
        read_continuation_executor=must_not_be_called,
        teams_contributors_resolver=contributors_must_not_be_called,
    )
    collected = [event async for event in chat_service.execute_turn_events(session_id, "hello", "api-user")]

    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Hello! How can I help you today?"
    assert "source" not in completed.data
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    assert not any(e.type == StreamEventType.ACTION_PENDING for e in collected)


@pytest.mark.asyncio
async def test_no_duplicate_team_manager_runner_invocation_for_a_plain_turn() -> None:
    """`self._runner.run_async` (team_manager's own Runner) must be
    called EXACTLY ONCE for a turn with no `ResolvedReadContinuation` --
    application code must never itself drive a second, redundant Runner
    invocation beyond what ADK's own single turn already requires.
    """
    service = ApiSessionService()
    session_id = await service.create_session()

    call_count = 0
    real_runner = FakeRunner(service, respond=lambda t: "ok")
    original_run_async = real_runner.run_async

    def counting_run_async(**kwargs):
        nonlocal call_count
        call_count += 1
        return original_run_async(**kwargs)

    real_runner.run_async = counting_run_async  # type: ignore[method-assign]

    chat_service = ChatService(service, runner=real_runner)
    async for _ in chat_service.execute_turn_events(session_id, "hello", "api-user"):
        pass

    assert call_count == 1


# --- Section 10/12: getMembers overlap-with-generation still works ---------


@pytest.mark.asyncio
async def test_contributors_fetch_is_started_exactly_once_per_resolved_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 12: verifies (does not merely assume) that the
    overlap-with-generation optimization for `teams_get_members` is still
    wired correctly after passes #2-#4's architectural changes -- the
    contributors resolver must be invoked exactly once for a turn that
    resolves exactly one chat_id, never once per event/chunk.
    """
    from backend.gateway import power_automate_client as pac_module
    from backend.tests._fakes import FakeResponse, chat

    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda url, json, timeout: FakeResponse(200, [chat("chat-real-1", "SLOPANOC Gateway Group Test")]),
    )

    from backend.api import selection_service
    from backend.selection.service import load_active_selection
    from backend.tools.teams.list_chats import teams_list_chats

    svc = ApiSessionService()
    session_id = await svc.create_session()
    session = await svc.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(topic="SLOPANOC Gateway Group", pending_operation="summarize", tool_context=ctx)
    await svc.persist_state_delta(session, dict(ctx.state))
    pending = load_active_selection((await svc.get_session(session.id)).state)
    chosen = next(o for o in pending.options if o.label == "SLOPANOC Gateway Group Test")
    choose_response = await selection_service.choose(svc, session.id, pending.selection_id, chosen.option_id)

    contributors_calls: list[str] = []

    async def counting_contributors(chat_id):
        contributors_calls.append(chat_id)
        return ["Alex", "Priya"]

    async def fake_executor(*, user_id, continuation, **kwargs):
        return {
            "outcome": "ok",
            "chat_id": continuation.selected_chat_id,
            "chat_title": continuation.selected_chat_topic,
            "summary": "Summary.",
            "evidence": [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-20T09:00:00Z"}],
        }

    chat_service = ChatService(
        svc,
        runner=FakeRunner(svc, respond=lambda t: "Summary."),
        read_continuation_executor=fake_executor,
        teams_contributors_resolver=counting_contributors,
    )
    async for _ in chat_service.execute_turn_events(session.id, choose_response.resume_message, "api-user"):
        pass

    assert contributors_calls == ["chat-real-1"]  # exactly one call, not duplicated


# --- Section 18: shared, long-lived model client ----------------------------


def test_team_manager_and_incident_manager_share_the_same_model_client() -> None:
    """Latency fix: both agents must use the SAME cached `BaseLlm`
    instance (see `get_shared_llm`'s own docstring) -- never a bare model
    string that ADK would otherwise re-resolve into a fresh client before
    every single model call.
    """
    assert team_manager.model is incident_manager.model
    from backend.agents.incident_manager.agent import _settings as im_settings

    assert team_manager.model is get_shared_llm(im_settings.gemini_model)


def test_get_shared_llm_is_cached_per_model_name() -> None:
    first = get_shared_llm("gemini-2.5-flash")
    second = get_shared_llm("gemini-2.5-flash")
    assert first is second


def test_specialist_runner_enables_tool_thread_pool() -> None:
    """The deterministic continuation-execution path's own Runner call
    must use ADK's documented `tool_thread_pool_config` -- see read_
    continuation_execution.py's own `_SPECIALIST_RUN_CONFIG` docstring
    for the full rationale/scope.
    """
    assert _SPECIALIST_RUN_CONFIG.tool_thread_pool_config is not None


# --- Section 2/11: perf logging never contains sensitive content -----------


class _FakePart:
    def __init__(self, text: Any = None, function_call: Any = None) -> None:
        self.text = text
        self.function_call = function_call


class _FakeContent:
    def __init__(self, parts: list[Any]) -> None:
        self.parts = parts


class _FakeUsage:
    def __init__(self, prompt_tokens: int, output_tokens: int) -> None:
        self.prompt_token_count = prompt_tokens
        self.candidates_token_count = output_tokens


class _FakeLlmRequest:
    def __init__(self, contents: list[Any]) -> None:
        self.contents = contents


class _FakeLlmResponse:
    def __init__(self, content: Any, usage_metadata: Any = None) -> None:
        self.content = content
        self.usage_metadata = usage_metadata


def test_model_call_perf_logging_never_contains_message_or_prompt_content(caplog: pytest.LogCaptureFixture) -> None:
    run_id = "perf-test-run-1"
    token = bind_run_id(run_id)
    try:
        sensitive_text = "SECRET_CHAT_TITLE the patient is on antibiotics chat_id=abc-123-XYZ"
        before = before_model_call("team_manager")
        after = after_model_call("team_manager")
        with caplog.at_level(logging.INFO, logger="backend.perf"):
            before(None, _FakeLlmRequest(contents=[object(), object(), object()]))
            after(
                None,
                _FakeLlmResponse(
                    content=_FakeContent([_FakePart(text=sensitive_text)]),
                    usage_metadata=_FakeUsage(prompt_tokens=123, output_tokens=45),
                ),
            )
        log_text = "\n".join(r.message for r in caplog.records)
        assert sensitive_text not in log_text
        assert "abc-123-XYZ" not in log_text
        assert "run_id=perf-test-run-1" in log_text
        assert "agent=team_manager" in log_text
        assert "kind=text" in log_text
        assert "prompt_tokens=123" in log_text
        assert "output_tokens=45" in log_text
    finally:
        reset_run_id(token)
        discard_model_call_tracking(run_id)


def test_model_call_perf_classifies_function_call_responses(caplog: pytest.LogCaptureFixture) -> None:
    run_id = "perf-test-run-2"
    token = bind_run_id(run_id)
    try:
        before = before_model_call("incident_manager")
        after = after_model_call("incident_manager")
        with caplog.at_level(logging.INFO, logger="backend.perf"):
            before(None, _FakeLlmRequest(contents=[]))
            after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(function_call=object())])))
        log_text = "\n".join(r.message for r in caplog.records)
        assert "kind=function_call" in log_text
        assert "agent=incident_manager" in log_text
    finally:
        reset_run_id(token)
        discard_model_call_tracking(run_id)


def test_before_model_call_is_a_safe_no_op_outside_a_tracked_turn() -> None:
    """No `run_id` bound (e.g. a standalone `adk run`/`adk web` invocation,
    or any code path that never called `bind_run_id`) -- must never raise,
    must simply skip instrumentation.
    """
    before = before_model_call("team_manager")
    after = after_model_call("team_manager")
    assert before(None, _FakeLlmRequest(contents=[])) is None
    assert after(None, _FakeLlmResponse(content=_FakeContent([]))) is None


def test_discard_model_call_tracking_is_safe_for_an_unknown_run_id() -> None:
    discard_model_call_tracking("a-run-id-nothing-was-ever-tracked-for")  # must not raise


def test_model_callbacks_are_compatible_with_adks_real_keyword_calling_convention() -> None:
    """Regression test for the "hello fails after 0-1s" incident: ADK
    invokes every canonical `before_model_callback`/`after_model_callback`
    with KEYWORD ARGUMENTS ONLY -- `callback(callback_context=...,
    llm_request=...)` / `callback(callback_context=..., llm_response=...)`
    -- verified directly in the installed ADK 1.33.0 source (`flows/
    llm_flows/base_llm_flow.py`'s `_handle_before_model_callback`/
    `_handle_after_model_callback`, both of which call every canonical
    callback exactly this way, never positionally).

    Exercises the REAL callbacks actually registered on the REAL
    `team_manager`/`incident_manager` `Agent` objects (`agent.canonical_
    before_model_callbacks`/`canonical_after_model_callbacks` -- the
    SAME ADK properties `base_llm_flow.py` itself reads), called with
    THIS exact keyword convention -- not `FakeRunner`, which never
    invokes these callbacks at all (and so could never have caught a
    parameter-name mismatch here), and not a plain positional call
    (which would silently succeed regardless of parameter naming, masking
    exactly the bug this test exists to catch: the previous parameter
    name `ctx` raised `TypeError: ...unexpected keyword argument
    'callback_context'` on every single model call).
    """
    run_id = "keyword-contract-regression-run"
    token = bind_run_id(run_id)
    try:
        for agent in (team_manager, incident_manager):
            before_callbacks = agent.canonical_before_model_callbacks
            after_callbacks = agent.canonical_after_model_callbacks
            assert before_callbacks, f"{agent.name} has no before_model_callback registered"
            assert after_callbacks, f"{agent.name} has no after_model_callback registered"

            for callback in before_callbacks:
                result = callback(callback_context=object(), llm_request=_FakeLlmRequest(contents=[]))
                assert result is None  # never short-circuits the real model call

            for callback in after_callbacks:
                result = callback(
                    callback_context=object(),
                    llm_response=_FakeLlmResponse(content=_FakeContent([_FakePart(text="hi")])),
                )
                assert result is None  # never substitutes a fake model response
    finally:
        reset_run_id(token)
        discard_model_call_tracking(run_id)


# --- Section 16: SSE headers prevent intermediary buffering -----------------


@pytest.fixture()
def _sse_client() -> TestClient:
    session_service = ApiSessionService()
    chat_service = ChatService(session_service, runner=FakeRunner(session_service))
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_chat_service] = lambda: chat_service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_sse_stream_sets_anti_buffering_headers(_sse_client: TestClient) -> None:
    session_id = _sse_client.post("/api/sessions").json()["session_id"]
    with _sse_client.stream(
        "POST", f"/api/sessions/{session_id}/messages/stream", json={"message": "hi"}
    ) as response:
        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("x-accel-buffering") == "no"
        # Drain the stream so the test client's context manager exits cleanly.
        for _ in response.iter_text():
            pass
