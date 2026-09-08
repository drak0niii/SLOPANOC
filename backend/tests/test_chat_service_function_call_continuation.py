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
from backend.attachments.models import ChatAttachmentStatus
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.attachments.storage import ChatAttachmentStorage

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


# --- Section 3: POST-5.1 B5 -- attachment linkage must not resurrect the ---
# --- B4B staleness defect ----------------------------------------------


def _attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


def _configured_storage() -> ChatAttachmentStorage:
    """A `ChatAttachmentStorage` with a bucket name configured, so
    `is_configured`/`uri_for` work -- but `uri_for` never touches the real
    `google.cloud.storage` client at all (see storage.py's own
    implementation: it only formats a string from `self._bucket_name`),
    so this stays a fully offline test double, never a real GCS call.
    """
    return ChatAttachmentStorage("test-bucket")


@pytest.mark.asyncio
async def test_attachment_linkage_at_first_event_does_not_reproduce_b4b_staleness(tmp_path: Path) -> None:
    """MANDATORY (B5 instruction section 26/48) -- structurally identical
    to `test_function_call_continuation_completes_normally_with_saved_chat
    _bookkeeping_enabled` above, but now with a REAL attachment linked at
    the exact same "first Runner event observed" point B4B's own
    bookkeeping write already uses. `AttachmentService.link_many_to_message`
    writes ONLY to `slopanoc_chat_attachments` (a separate table, via a
    plain SQLAlchemy UPDATE, never through `session_service.append_event`)
    -- this test is the empirical proof that doing so, inline, between the
    Runner's own two internal appends (the user-content append and the
    function-response append), does NOT reproduce the "session has been
    modified in storage" `ValueError` B4B hit. Uses a real, file-backed
    `DatabaseSessionService` (the only session-service implementation with
    actual revision-marker staleness checking) -- not a mock.
    """
    service = _db_backed_service(tmp_path)
    session_id = await service.create_session("u1")

    attachment_service = _attachment_service()
    record = await attachment_service.create(
        owner_user_id="u1",
        session_id=session_id,
        original_filename="screenshot.png",
        mime_type="image/png",
        size_bytes=180337,
        sha256="a" * 64,
    )
    assert record.status == ChatAttachmentStatus.READY.value

    runner = _make_real_agent_and_runner(
        service,
        [
            _function_call_response("get_incident_status"),
            _final_text_response("B5 multimodal smoke confirmed."),
        ],
    )
    chat_service = ChatService(
        service,
        runner=runner,
        attachment_service=attachment_service,
        attachment_storage=_configured_storage(),
    )

    response = await chat_service.run_turn(
        session_id, "here's a screenshot", "u1", [record.attachment_id]
    )

    # The turn completed normally -- the function-call continuation was
    # NOT broken by the inline attachment-linkage write, proving the B4B
    # defect was not reintroduced.
    assert response.message.content == "B5 multimodal smoke confirmed."

    # The attachment is now genuinely LINKED to the REAL ADK turn -- never
    # marked merely because the request arrived or validation passed.
    linked = await attachment_service.get_owned(record.attachment_id, "u1")
    assert linked.status == ChatAttachmentStatus.LINKED.value
    assert linked.message_id is not None

    # The linked message_id is the REAL invocation_id of the genuine user
    # turn -- cross-checked against the session's own persisted events.
    session = await service.get_session(session_id, "u1")
    matching_user_events = [
        e for e in session.events if e.invocation_id == linked.message_id and e.author == "user" and e.content
    ]
    assert len(matching_user_events) == 1

    # The saved-chat bookkeeping ALSO landed correctly (unchanged B4B
    # behavior, proving the two independent deferred/inline writes don't
    # interfere with each other either).
    assert session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True
    assert session.state.get(CHAT_ACTIVITY_AT_STATE_KEY) is not None


@pytest.mark.asyncio
async def test_attachment_link_failure_after_genuine_turn_fails_closed_and_leaves_attachment_ready(
    tmp_path: Path,
) -> None:
    """Instruction section 25: if linkage fails AFTER the genuine user
    turn already exists, the turn must fail closed (never a successful-
    looking response) and the attachment must remain READY -- never
    fraudulently LINKED. Simulated by requesting an attachment id that
    does not exist in THIS test's attachment service at all (the request-
    time validation in `prepare_attachments_for_turn` would normally catch
    this earlier: this test exercises `link_many_to_message`'s own
    atomic-failure path directly, by monkeypatching it to simulate a
    narrow concurrent-transition race after validation already passed).
    """
    from unittest.mock import AsyncMock

    from backend.gateway.safe_error import SafeErrorException

    service = _db_backed_service(tmp_path)
    session_id = await service.create_session("u1")

    attachment_service = _attachment_service()
    record = await attachment_service.create(
        owner_user_id="u1",
        session_id=session_id,
        original_filename="screenshot.png",
        mime_type="image/png",
        size_bytes=180337,
        sha256="a" * 64,
    )

    # Simulate a race: the attachment was genuinely READY when `prepare_
    # attachments_for_turn` validated it, but `link_many_to_message` itself
    # now fails (e.g. a concurrent transition, or -- as tested here -- any
    # unexpected error at that exact call site).
    attachment_service.link_many_to_message = AsyncMock(side_effect=RuntimeError("simulated race"))

    runner = _make_real_agent_and_runner(
        service,
        [_final_text_response("this must never be presented as successful")],
    )
    chat_service = ChatService(
        service,
        runner=runner,
        attachment_service=attachment_service,
        attachment_storage=_configured_storage(),
    )

    with pytest.raises(SafeErrorException):
        await chat_service.run_turn(session_id, "here's a screenshot", "u1", [record.attachment_id])

    # The attachment was never fraudulently LINKED.
    still_ready = await attachment_service.get_owned(record.attachment_id, "u1")
    assert still_ready.status == ChatAttachmentStatus.READY.value
    assert still_ready.message_id is None

    # The genuine user turn itself is still durable/visible -- a link
    # failure does not undo the fact that the user really submitted this
    # turn (mirrors B4B's own "model failure after turn creation" rule).
    session = await service.get_session(session_id, "u1")
    assert session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True


# --- Section 4: POST-5.1 B5 -- multimodal Content construction -------------


async def _persisted_user_parts(service: ApiSessionService, session_id: str) -> list:
    session = await service.get_session(session_id, "u1")
    user_events = [e for e in session.events if e.author == "user" and e.content and e.content.parts]
    assert len(user_events) == 1, "expected exactly one genuine user event for this turn"
    return list(user_events[0].content.parts)


@pytest.mark.asyncio
async def test_content_construction_text_only(tmp_path: Path) -> None:
    service = _db_backed_service(tmp_path)
    session_id = await service.create_session("u1")
    runner = _make_real_agent_and_runner(service, [_final_text_response("ok")])
    chat_service = ChatService(
        service, runner=runner, attachment_service=_attachment_service(), attachment_storage=_configured_storage()
    )

    await chat_service.run_turn(session_id, "hello world", "u1", [])

    parts = await _persisted_user_parts(service, session_id)
    assert len(parts) == 1
    assert parts[0].text == "hello world"
    assert parts[0].file_data is None


@pytest.mark.asyncio
async def test_content_construction_image_only_has_no_fabricated_text_part(tmp_path: Path) -> None:
    service = _db_backed_service(tmp_path)
    session_id = await service.create_session("u1")
    attachment_service = _attachment_service()
    record = await attachment_service.create(
        owner_user_id="u1",
        session_id=session_id,
        original_filename="screenshot.png",
        mime_type="image/png",
        size_bytes=180337,
        sha256="a" * 64,
    )
    runner = _make_real_agent_and_runner(service, [_final_text_response("ok")])
    chat_service = ChatService(
        service, runner=runner, attachment_service=attachment_service, attachment_storage=_configured_storage()
    )

    await chat_service.run_turn(session_id, "", "u1", [record.attachment_id])

    parts = await _persisted_user_parts(service, session_id)
    assert len(parts) == 1
    assert parts[0].text is None
    assert parts[0].file_data is not None
    assert parts[0].file_data.file_uri == f"gs://test-bucket/chat-attachments/{session_id}/{record.attachment_id}"
    assert parts[0].file_data.mime_type == "image/png"


@pytest.mark.asyncio
async def test_content_construction_text_plus_multiple_images_preserves_client_order(tmp_path: Path) -> None:
    service = _db_backed_service(tmp_path)
    session_id = await service.create_session("u1")
    attachment_service = _attachment_service()
    # Created out of order on purpose -- proves order comes from the
    # CLIENT'S requested attachment_ids list, never DB/creation order.
    for i in (2, 1):
        await attachment_service.create(
            owner_user_id="u1",
            session_id=session_id,
            original_filename=f"{i}.png",
            mime_type="image/png",
            size_bytes=100,
            sha256="a" * 64,
            attachment_id=f"att-{i}",
        )
    client_order = ["att-1", "att-2"]

    runner = _make_real_agent_and_runner(service, [_final_text_response("ok")])
    chat_service = ChatService(
        service, runner=runner, attachment_service=attachment_service, attachment_storage=_configured_storage()
    )

    await chat_service.run_turn(session_id, "look at these", "u1", client_order)

    parts = await _persisted_user_parts(service, session_id)
    assert len(parts) == 3
    assert parts[0].text == "look at these"
    assert parts[1].file_data.file_uri.endswith("att-1")
    assert parts[2].file_data.file_uri.endswith("att-2")


@pytest.mark.asyncio
async def test_never_uses_part_from_bytes_for_attachment_content(tmp_path: Path) -> None:
    """Instruction section 17/45 -- patches `Part.from_bytes` and proves
    it is NEVER called anywhere in a real multimodal turn. Durable
    attachment input MUST use `Part.from_uri` exclusively -- see storage
    .py's `uri_for` and CLAUDE.md's own locked B0 rule.
    """
    from unittest.mock import patch

    service = _db_backed_service(tmp_path)
    session_id = await service.create_session("u1")
    attachment_service = _attachment_service()
    record = await attachment_service.create(
        owner_user_id="u1",
        session_id=session_id,
        original_filename="screenshot.png",
        mime_type="image/png",
        size_bytes=180337,
        sha256="a" * 64,
    )
    runner = _make_real_agent_and_runner(service, [_final_text_response("ok")])
    chat_service = ChatService(
        service, runner=runner, attachment_service=attachment_service, attachment_storage=_configured_storage()
    )

    with patch.object(
        types.Part,
        "from_bytes",
        side_effect=AssertionError("Part.from_bytes must never be used for durable attachment input"),
    ) as spy:
        response = await chat_service.run_turn(session_id, "here", "u1", [record.attachment_id])
        assert response.message.content == "ok"
        spy.assert_not_called()
