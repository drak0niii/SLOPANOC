"""POST-5.1 B4B DEFECT REGRESSION -- a real live-production incident
(real Vertex/Gemini, a function-call tool turn) proved that writing B4B's
saved-chat bookkeeping (`record_user_turn_activity`) from INSIDE
`chat_service.py`'s `_run_turn_events` loop -- while `Runner.run_async`'s
own generator was still actively producing more events for the SAME
invocation -- corrupts ADK's session-revision tracking out from under the
Runner's own internal session handle, causing the Runner's NEXT internal
`append_event` call (the tool's own function-response) to raise ADK's
"session has been modified in storage" `ValueError`. `chat_service.py`'s
own outer `except Exception:` swallows this silently, so the user saw
only a generic "The assistant could not complete this request" with no
further model continuation and no logged cause.

Section 1 proves the underlying ADK mechanism directly (no chat_service.py
involved) -- a real `DatabaseSessionService` against a real, file-backed
SQLite database (the ONLY session-service implementation with actual
revision-marker staleness checking; `InMemorySessionService` has none).
Section 2 proves the FIX at the full `ChatService`/`_run_turn_events`
integration level, using a real `Agent` + real `Runner` + a real tool,
with only the underlying model call scripted (via a minimal `BaseLlm`
subclass -- an ADK-native, fully-supported test seam, never a fake
Runner) so no live Vertex/Gemini network call is needed.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import AsyncGenerator, Optional

import pytest
from google.adk.agents import Agent
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import PrivateAttr

from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.session_state_keys import CHAT_ACTIVITY_AT_STATE_KEY, CHAT_TITLE_STATE_KEY, HAS_VISIBLE_MESSAGE_STATE_KEY

APP_NAME = "slopanoc-api"


def _sqlite_url(tmp_path: Path) -> str:
    db_file = tmp_path / "function_call_continuation.db"
    return f"sqlite+aiosqlite:///{db_file.as_posix()}"


def _db_backed_service(tmp_path: Path) -> ApiSessionService:
    return ApiSessionService(adk_session_service=DatabaseSessionService(_sqlite_url(tmp_path)))


# --- Section 1: the underlying ADK mechanism, isolated ----------------------


@pytest.mark.asyncio
async def test_external_append_between_two_runner_session_appends_raises_staleness_error(tmp_path: Path) -> None:
    """Proves the exact mechanism, independent of chat_service.py: a
    session object held across an invocation (simulating `Runner`'s own
    internal handle) can no longer append once a DIFFERENT, freshly-
    fetched session object for the SAME session_id has appended anything
    in between -- exactly what the OLD (broken) mid-run
    `record_user_turn_activity` call did on every real turn.
    """
    service = DatabaseSessionService(_sqlite_url(tmp_path))
    await service.create_session(app_name=APP_NAME, user_id="u1", session_id="s1", state={})

    # Simulates the Runner's OWN session handle, held for the whole invocation.
    runner_session = await service.get_session(app_name=APP_NAME, user_id="u1", session_id="s1")

    user_event = Event(
        invocation_id="inv-1",
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text="hi")]),
    )
    await service.append_event(session=runner_session, event=user_event)

    # The OLD, broken B4B timing: a separate get_session() + append_event()
    # call for the SAME session, while the Runner's own invocation is still
    # in progress.
    from google.adk.events import EventActions

    external_session = await service.get_session(app_name=APP_NAME, user_id="u1", session_id="s1")
    external_event = Event(
        author="user", invocation_id="bookkeeping", actions=EventActions(state_delta={"chat_activity_at": 123.0})
    )
    await service.append_event(session=external_session, event=external_event)

    # The Runner tries to continue the SAME invocation -- append a
    # function_response event through its OWN (now-stale) session handle,
    # exactly like runners.py's own `append_event(session=session, ...)`
    # inside its main event-consuming loop.
    function_response_event = Event(
        invocation_id="inv-1",
        author="team_manager",
        content=types.Content(
            role="model",
            parts=[types.Part(function_response=types.FunctionResponse(name="some_tool", response={"ok": True}))],
        ),
    )
    with pytest.raises(ValueError, match="modified in storage"):
        await service.append_event(session=runner_session, event=function_response_event)


@pytest.mark.asyncio
async def test_deferred_append_after_the_runner_session_is_done_does_not_raise(tmp_path: Path) -> None:
    """The FIX's own shape, proven at the same isolated level: performing
    the external write AFTER the Runner's own session handle has already
    finished appending everything it needs never collides.
    """
    service = DatabaseSessionService(_sqlite_url(tmp_path))
    await service.create_session(app_name=APP_NAME, user_id="u1", session_id="s1", state={})

    runner_session = await service.get_session(app_name=APP_NAME, user_id="u1", session_id="s1")
    user_event = Event(
        invocation_id="inv-1",
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text="hi")]),
    )
    await service.append_event(session=runner_session, event=user_event)

    function_response_event = Event(
        invocation_id="inv-1",
        author="team_manager",
        content=types.Content(
            role="model",
            parts=[types.Part(function_response=types.FunctionResponse(name="some_tool", response={"ok": True}))],
        ),
    )
    # The Runner finishes its OWN invocation entirely (both its appends
    # complete) BEFORE any external write happens.
    await service.append_event(session=runner_session, event=function_response_event)

    # ONLY NOW does the deferred, post-run finalization write happen --
    # via a freshly-reloaded session, exactly like `_finalize_user_turn_
    # activity` does.
    from google.adk.events import EventActions

    fresh_session = await service.get_session(app_name=APP_NAME, user_id="u1", session_id="s1")
    finalize_event = Event(author="user", invocation_id="bookkeeping", actions=EventActions(state_delta={"chat_activity_at": 123.0}))
    await service.append_event(session=fresh_session, event=finalize_event)  # must not raise

    final = await service.get_session(app_name=APP_NAME, user_id="u1", session_id="s1")
    assert final.state.get("chat_activity_at") == 123.0


# --- Section 2: full ChatService/_run_turn_events integration ---------------


class _ScriptedLlm(BaseLlm):
    """A real `google.adk.models.base_llm.BaseLlm` subclass -- an ADK-
    native, fully-supported test seam (`Agent.model: Union[str, BaseLlm]`
    accepts a real instance directly) -- never a fake Runner. Returns one
    scripted, non-streaming `LlmResponse` per call, advancing through
    `responses` in order: call #1 -> function_call, call #2 -> final text.
    """

    _responses: list[LlmResponse] = PrivateAttr(default_factory=list)
    _call_count: int = PrivateAttr(default=0)

    def __init__(self, responses: list[LlmResponse], **kwargs) -> None:
        super().__init__(model="scripted-test-model", **kwargs)
        self._responses = responses

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        response = self._responses[self._call_count]
        self._call_count += 1
        yield response


def _final_text_response(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]), partial=False)


def _function_call_response(name: str, args: Optional[dict] = None) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args or {}))]
        ),
        partial=False,
    )


def get_incident_status() -> dict:
    """A trivial real tool -- ADK auto-wraps this as a FunctionTool."""
    return {"status": "resolved"}


def record_source_requirements(requires_teams: bool, requires_governed_knowledge: bool) -> dict:
    """A stub matching the REAL `record_source_requirements` tool's name
    and response shape -- `chat_service.py`'s own `SourceRequirementsCapture`
    gate (unrelated to B4B, pre-existing) requires a declaration via this
    exact tool name before accepting ANY completion. Every scripted
    sequence below calls this FIRST, exactly like `FakeRunner`'s own
    default event sequence already does for every other test in this
    suite.
    """
    return {"requires_teams": requires_teams, "requires_governed_knowledge": requires_governed_knowledge}


def _source_requirements_call() -> LlmResponse:
    return _function_call_response(
        "record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}
    )


def _make_real_agent_and_runner(session_service: ApiSessionService, responses: list[LlmResponse]) -> Runner:
    agent = Agent(
        name="team_manager",
        model=_ScriptedLlm(responses=[_source_requirements_call(), *responses]),
        tools=[get_incident_status, record_source_requirements],
    )
    return Runner(app_name=APP_NAME, agent=agent, session_service=session_service.adk_session_service)


@pytest.mark.asyncio
async def test_function_call_continuation_completes_normally_with_saved_chat_bookkeeping_enabled(
    tmp_path: Path,
) -> None:
    """THE required regression (B4B correction pass instruction section
    9): reproduces the exact live-incident shape -- model call #1 returns
    a function_call, the real tool executes, model call #2 returns the
    final text -- through the REAL `ChatService`/`_run_turn_events` path,
    with B4B's saved-chat bookkeeping enabled. Before the fix, this
    scenario reliably raised ADK's staleness `ValueError` between the two
    model calls (proven in Section 1 above) and surfaced as a generic
    `run_failure` with no continuation; after the fix, the second model
    call MUST still happen and the real final answer MUST be returned.
    """
    service = _db_backed_service(tmp_path)
    session_id = await service.create_session("u1")

    runner = _make_real_agent_and_runner(
        service,
        [
            _function_call_response("get_incident_status"),
            _final_text_response("B4B smoke confirmed."),
        ],
    )
    chat_service = ChatService(service, runner=runner)

    response = await chat_service.run_turn(session_id, "what's the incident status?", "u1")

    assert response.message.content == "B4B smoke confirmed."

    # The saved-chat bookkeeping must ALSO have landed correctly, proving
    # the deferred (post-run) write itself succeeded.
    session = await service.get_session(session_id, "u1")
    assert session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True
    assert session.state.get(CHAT_TITLE_STATE_KEY) == "what's the incident status?"
    assert session.state.get(CHAT_ACTIVITY_AT_STATE_KEY) is not None


@pytest.mark.asyncio
async def test_no_final_answer_after_a_real_function_call_still_marks_the_session_visible(tmp_path: Path) -> None:
    """B4B correction pass instruction section 10, against the REAL
    staleness-prone path: a turn whose model call sequence never produces
    a final text response (only ever more function calls) must still
    raise `run_failure` (existing, unrelated behavior) AND still leave
    the session correctly marked visible with a real activity timestamp
    -- the user's own real message must never vanish merely because the
    assistant's turn didn't complete.
    """
    from backend.gateway.safe_error import SafeErrorException

    service = _db_backed_service(tmp_path)
    session_id = await service.create_session("u1")

    # Every model call keeps returning a function_call -- no final text is
    # ever reached (`_ScriptedLlm` will index past its list, so use a
    # dedicated always-function-call double to avoid an IndexError).
    class _AlwaysFunctionCallLlm(_ScriptedLlm):
        async def generate_content_async(self, llm_request, stream=False):
            yield _function_call_response("get_incident_status")

    agent = Agent(name="team_manager", model=_AlwaysFunctionCallLlm(responses=[]), tools=[get_incident_status])
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=service.adk_session_service)
    chat_service = ChatService(service, runner=runner)

    with pytest.raises(SafeErrorException):
        await chat_service.run_turn(session_id, "diagnose the fault", "u1")

    session = await service.get_session(session_id, "u1")
    assert session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True
    assert session.state.get(CHAT_TITLE_STATE_KEY) == "diagnose the fault"
    assert session.state.get(CHAT_ACTIVITY_AT_STATE_KEY) is not None


@pytest.mark.asyncio
async def test_multi_turn_real_runner_no_bookkeeping_interference(tmp_path: Path) -> None:
    """B4B correction pass instruction section 11: two successful turns,
    each going through a real function-call round trip, against the same
    real DB-backed session. Neither turn's own bookkeeping write may
    interfere with the other's Runner invocation, activity must advance,
    and the title must remain the FIRST turn's.
    """
    service = _db_backed_service(tmp_path)
    session_id = await service.create_session("u1")

    runner_1 = _make_real_agent_and_runner(
        service,
        [_function_call_response("get_incident_status"), _final_text_response("first answer")],
    )
    chat_service_1 = ChatService(service, runner=runner_1)
    response_1 = await chat_service_1.run_turn(session_id, "first message", "u1")
    assert response_1.message.content == "first answer"

    session_after_1 = await service.get_session(session_id, "u1")
    first_activity = session_after_1.state.get(CHAT_ACTIVITY_AT_STATE_KEY)
    assert first_activity is not None
    assert session_after_1.state.get(CHAT_TITLE_STATE_KEY) == "first message"

    runner_2 = _make_real_agent_and_runner(
        service,
        [_function_call_response("get_incident_status"), _final_text_response("second answer")],
    )
    chat_service_2 = ChatService(service, runner=runner_2)
    response_2 = await chat_service_2.run_turn(session_id, "a later, unrelated message", "u1")
    assert response_2.message.content == "second answer"

    session_after_2 = await service.get_session(session_id, "u1")
    second_activity = session_after_2.state.get(CHAT_ACTIVITY_AT_STATE_KEY)
    assert second_activity is not None
    assert second_activity >= first_activity
    # Title remains the FIRST turn's -- never overwritten by the second.
    assert session_after_2.state.get(CHAT_TITLE_STATE_KEY) == "first message"
