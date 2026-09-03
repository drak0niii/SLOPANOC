"""Shared, non-collected test doubles for the backend API test suite
(backend/api/). Named with a leading underscore so pytest never tries to
collect it as a test module -- mirrors `_fakes.py`.

FakeRunner stands in for `google.adk.runners.Runner` so tests never call a
real Gemini model, exactly the same "mock the external boundary, keep
everything else real" approach `_fakes.py`/`FakeResponse` already use for
the Power Automate gateway.

IMPORTANT, discovered while building this fixture: `InMemorySessionService
.get_session` returns a COPY of the stored session (verified against the
installed ADK 1.33.0 source, `_copy_session`/`_get_session_impl`) --
mutating `session.state[...]` directly on a fetched session object is
silently discarded, never persisted. The only way state genuinely
persists is `session_service.append_event(session, event)` with the
change captured in `event.actions.state_delta` -- exactly what the real
`Runner`'s tool-calling machinery does automatically for a live agent
turn. `simulate_proposal`/`append_state_delta` below go through that same
real mechanism, so tests exercise the identical persistence path
production code relies on, not a shortcut that happens to look similar.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from google.adk.events import Event, EventActions
from google.adk.sessions import Session

from backend.approval.service import create_action_proposal

_TEST_INVOCATION_ID = "test-invocation"


class FakePart:
    def __init__(self, text: Optional[str], thought: bool = False) -> None:
        self.text = text
        self.thought = thought


class FakeContent:
    def __init__(self, parts: list[FakePart]) -> None:
        self.parts = parts


class FakeFunctionCall:
    """Duck-typed stand-in for a `google.genai.types.FunctionCall` --
    only `.name`/`.args`, exactly what
    `backend.api.activity_translator` reads.
    """

    def __init__(self, name: str, args: Optional[dict[str, Any]] = None) -> None:
        self.name = name
        self.args = args or {}


class FakeFunctionResponse:
    """Duck-typed stand-in for a `google.genai.types.FunctionResponse` --
    only `.name`/`.response`.
    """

    def __init__(self, name: str, response: Optional[dict[str, Any]] = None) -> None:
        self.name = name
        self.response = response or {}


class FakeEvent:
    """Duck-typed stand-in for `google.adk.events.Event` -- satisfies
    exactly the shape `backend.api.chat_service`/`activity_translator`
    actually read (`get_function_calls`/`get_function_responses`/
    `is_final_response`/`content`/`partial`), nothing more.

    `partial` and `final` are independent, exactly like the real ADK
    `Event`: a true streaming-mode turn yields several `partial=True,
    final=False` chunk events followed by one `partial=False,
    final=True` aggregated event (see chat_service.py's module docstring
    for how this was verified against the installed ADK source).
    """

    def __init__(
        self,
        text: Optional[str] = None,
        final: bool = True,
        partial: bool = False,
        function_calls: Optional[list[Any]] = None,
        function_responses: Optional[list[Any]] = None,
        thought: bool = False,
    ) -> None:
        self.content = FakeContent([FakePart(text, thought=thought)]) if text is not None else None
        self.partial = partial
        self._final = final
        self._function_calls = function_calls or []
        self._function_responses = function_responses or []

    def get_function_calls(self) -> list[Any]:
        return self._function_calls

    def get_function_responses(self) -> list[Any]:
        return self._function_responses

    def is_final_response(self) -> bool:
        return self._final


SideEffect = Callable[["ApiSessionServiceLike", Session, str], Awaitable[None]]


class ApiSessionServiceLike:
    """Structural placeholder only, for the type hint above -- avoids a
    circular import of `backend.api.session_service.ApiSessionService` at
    module load time; any object with the same shape works.
    """


class FakeRunner:
    """Stands in for `google.adk.runners.Runner`. `respond` computes the
    assistant's final text from the user's message (defaults to a simple
    echo); `side_effect`, if given, runs before the response is yielded
    and can mutate real session state via `simulate_proposal`/
    `append_state_delta` below -- exactly like a real tool call would.

    Also appends one real (empty-delta) event via
    `session_service.persist_state_delta` for each call, mirroring the
    fact that a real `Runner` turn always records at least one `Event` in
    `session.events` -- tests asserting on conversation-history growth
    (e.g. "session A's history is unaffected by a turn in session B")
    exercise the real event-append mechanism, not a purely-decorative
    fake.
    """

    def __init__(
        self,
        session_service: Any,
        respond: Optional[Callable[[str], str]] = None,
        side_effect: Optional[SideEffect] = None,
        events: Optional[list[FakeEvent]] = None,
    ) -> None:
        self._session_service = session_service
        self._respond = respond or (lambda text: f"echo: {text}")
        self._side_effect = side_effect
        self._events = events
        # Phase 4G hardening pass -- a plain recording stub, never a
        # reimplementation of ADK's own `Runner.rewind_async` (which does
        # real state-delta reversal; see chat_service.py's
        # `rewind_before_user_turn` docstring). Precise enough to test
        # this backend's OWN orchestration code (did it resolve the right
        # invocation id? did it call through exactly once? did a failure
        # path correctly avoid calling this at all?) without duplicating
        # ADK's already-shipped, already-correct algorithm in a fake.
        self.rewind_calls: list[dict[str, Any]] = []

    async def rewind_async(
        self, *, user_id: str, session_id: str, rewind_before_invocation_id: str, run_config: Any = None
    ) -> None:
        self.rewind_calls.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "rewind_before_invocation_id": rewind_before_invocation_id,
            }
        )

    async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
        text = new_message.parts[0].text
        session = await self._session_service.get_session(session_id, user_id)
        if self._side_effect is not None:
            await self._side_effect(self._session_service, session, text)
        await self._session_service.persist_state_delta(session, {})
        if self._events is not None:
            for event in self._events:
                yield event
            return
        yield FakeEvent(text=self._respond(text))


class NoFinalTextRunner:
    """A runner whose stream contains only non-final/tool-shaped events --
    used to test the "no final text produced" failure path.
    """

    async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
        yield FakeEvent(text=None, final=False, function_calls=[object()])


class RaisingRunner:
    """A runner whose stream raises partway through -- used to test that a
    raw exception from the agent runtime never reaches the API response.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
        raise self._exc
        yield  # pragma: no cover -- makes this an async generator


async def append_state_delta(session_service: Any, session: Session, delta: dict[str, Any]) -> None:
    """The real persistence mechanism (see module docstring). Delegates to
    `ApiSessionService.persist_state_delta` (Phase 4B production code)
    when available, so tests exercise the exact same code path production
    code uses rather than a parallel copy of it; falls back to
    constructing the event directly for any test double that doesn't
    implement that method.
    """
    if hasattr(session_service, "persist_state_delta"):
        await session_service.persist_state_delta(session, delta)
        return
    event = Event(author="team_manager", invocation_id=_TEST_INVOCATION_ID, actions=EventActions(state_delta=delta))
    await session_service.adk_session_service.append_event(session, event)


async def append_user_turn(
    session_service: Any,
    session: Session,
    invocation_id: str,
    user_text: str,
    assistant_text: str = "ok",
    assistant_author: str = "team_manager",
) -> Session:
    """Appends one real, complete conversational turn -- a `user`-authored
    event followed by a model-authored reply, both sharing `invocation_id`
    -- exactly the shape the real `google.adk.runners.Runner` produces for
    one live turn (verified against the installed 1.33.0 source; see
    `chat_service.py`'s `rewind_before_user_turn`/`_active_events`
    docstrings). Used to build realistic session history for Phase 4G
    rewind tests without needing a real model call.

    Returns the freshly re-fetched session (each `append_event` call only
    mutates the STORED copy -- see this module's own docstring -- so
    callers must always continue from the object this returns, never the
    one passed in).
    """
    from google.genai import types

    user_event = Event(
        invocation_id=invocation_id,
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text=user_text)]),
    )
    await session_service.adk_session_service.append_event(session, user_event)
    session = await session_service.get_session(session.id, session.user_id)

    assistant_event = Event(
        invocation_id=invocation_id,
        author=assistant_author,
        content=types.Content(role="model", parts=[types.Part.from_text(text=assistant_text)]),
    )
    await session_service.adk_session_service.append_event(session, assistant_event)
    return await session_service.get_session(session.id, session.user_id)


async def simulate_proposal(
    session_service: Any,
    session: Session,
    operation: str,
    payload: dict[str, Any],
    summary: Optional[str] = None,
) -> None:
    """Simulates a tool call that creates an `ActionProposal` during a
    turn -- computes the delta with the real `create_action_proposal`
    (never a hand-rolled fake shape) and persists it the real way.
    """
    delta: dict[str, Any] = {}
    create_action_proposal(operation, payload, delta, summary=summary)
    await append_state_delta(session_service, session, delta)


async def simulate_selection(
    session_service: Any,
    session: Session,
    requested_value: str,
    candidate_titles: list[str],
    pending_write_message: Optional[str] = None,
) -> None:
    """Simulates `teams_list_chats` creating a `PendingSelection` when no
    exact chat match exists but similar candidates do -- mirrors
    `simulate_proposal`'s own real-mechanism-only philosophy.
    """
    from backend.selection.schemas import SelectionKind
    from backend.selection.service import create_pending_selection
    from backend.tools.teams.schemas import ChatSummary

    delta: dict[str, Any] = {}
    candidates = [ChatSummary(chat_id=f"chat-{i}", title=title) for i, title in enumerate(candidate_titles)]
    create_pending_selection(
        SelectionKind.TEAMS_CHAT, requested_value, candidates, delta, pending_write_message=pending_write_message
    )
    await append_state_delta(session_service, session, delta)
