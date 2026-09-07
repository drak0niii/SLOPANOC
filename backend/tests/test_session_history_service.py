"""Tests for POST-5.1 B4B's `backend/api/session_history_service.py` --
the safe saved-chat list, transcript projection, and durable rename.

Builds REAL `google.adk.events.Event`/`google.genai.types.Content`
objects directly (mirroring `backend/tests/_api_fakes.py`'s own
`append_user_turn` pattern) rather than duck-typed fakes, since this
module's correctness depends on real ADK event shapes (`invocation_id`,
`content.role`, `is_final_response()`, `get_function_calls()`,
`timestamp`) that a minimal fake could accidentally get wrong.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest
from google.adk.events import Event, EventActions
from google.genai import types

from backend.api import session_history_service as history
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.session_state_keys import (
    CHAT_TITLE_STATE_KEY,
    HAS_VISIBLE_MESSAGE_STATE_KEY,
    record_user_turn_activity,
)
from backend.attachments.models import ChatAttachmentRecord, ChatAttachmentStatus
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.gateway.safe_error import SafeErrorException
from backend.tests._api_fakes import FakeRunner


def _user_event(invocation_id: str, text: str, timestamp: Optional[float] = None) -> Event:
    ev = Event(
        invocation_id=invocation_id,
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text=text)]),
    )
    if timestamp is not None:
        ev.timestamp = timestamp
    return ev


def _assistant_event(
    invocation_id: str,
    text: Optional[str] = None,
    author: str = "team_manager",
    timestamp: Optional[float] = None,
    thought_text: Optional[str] = None,
) -> Event:
    parts = []
    if thought_text is not None:
        thought_part = types.Part.from_text(text=thought_text)
        thought_part.thought = True
        parts.append(thought_part)
    if text is not None:
        parts.append(types.Part.from_text(text=text))
    ev = Event(invocation_id=invocation_id, author=author, content=types.Content(role="model", parts=parts))
    if timestamp is not None:
        ev.timestamp = timestamp
    return ev


def _function_call_event(invocation_id: str, name: str, args: dict[str, Any], author: str = "team_manager") -> Event:
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    return Event(invocation_id=invocation_id, author=author, content=types.Content(role="model", parts=[part]))


def _function_response_event(invocation_id: str, name: str, response: dict[str, Any], author: str = "team_manager") -> Event:
    part = types.Part(function_response=types.FunctionResponse(name=name, response=response))
    return Event(invocation_id=invocation_id, author=author, content=types.Content(role="model", parts=[part]))


def _state_only_event(invocation_id: str, delta: dict[str, Any]) -> Event:
    return Event(author="user", invocation_id=invocation_id, actions=EventActions(state_delta=delta))


async def _append(service: ApiSessionService, session, event: Event):
    await service.adk_session_service.append_event(session, event)
    return await service.get_session(session.id, session.user_id)


async def _clear_visibility_marker(service: ApiSessionService, session_id: str, user_id: str):
    """Simulates a pre-B4B session whose `has_visible_message` marker was
    never written. `get_session` returns a COPY (documented in
    `session_service.py`) -- mutating a fetched session's `.state` dict
    directly is silently discarded, never persisted -- so this goes
    through the REAL `persist_state_delta` mechanism instead. A state
    delta value of `None` and a genuinely absent key are indistinguishable
    through `.get()` either way, which is exactly the "missing" condition
    `list_saved_sessions`/`_backfill_marker` need to see.
    """
    session = await service.get_session(session_id, user_id)
    await service.persist_state_delta(session, {HAS_VISIBLE_MESSAGE_STATE_KEY: None})
    return await service.get_session(session_id, user_id)


def _fresh_attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


# --- list_saved_sessions -------------------------------------------------


@pytest.mark.asyncio
async def test_new_session_is_excluded_from_the_saved_chat_list() -> None:
    service = ApiSessionService()
    await service.create_session("u1")

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert summaries == []


@pytest.mark.asyncio
async def test_session_with_a_real_first_turn_is_listed_with_a_derived_title() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    chat_service = ChatService(service, runner=FakeRunner(service))
    await chat_service.run_turn(session_id, "how is the ops incident going?", "u1")

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert len(summaries) == 1
    assert summaries[0].session_id == session_id
    assert summaries[0].title == "how is the ops incident going?"


@pytest.mark.asyncio
async def test_other_users_sessions_are_never_listed() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("owner")
    chat_service = ChatService(service, runner=FakeRunner(service))
    await chat_service.run_turn(session_id, "owner's message", "owner")

    summaries = await history.list_saved_sessions(service, "someone-else", limit=50)
    assert summaries == []


async def _seed_visible_session(service: ApiSessionService, user_id: str, text: str, timestamp: float) -> str:
    """Builds a real, minimally-visible session whose `chat_activity_at`
    is deterministic BY CONSTRUCTION (B4B correction pass) -- it comes
    directly from the real, explicitly-injected `Event.timestamp`, never
    from wall-clock time, so ordering/limit tests never depend on real
    elapsed time between fast in-memory operations (no reaching into ADK
    internals needed, unlike the pre-correction version of this helper).
    """
    session_id = await service.create_session(user_id)
    session = await service.get_session(session_id, user_id)
    session = await _append(service, session, _user_event("inv-1", text, timestamp=timestamp))
    await record_user_turn_activity(service, session, text, "inv-1")
    return session_id


@pytest.mark.asyncio
async def test_sessions_are_sorted_by_chat_activity_at_most_recent_first() -> None:
    service = ApiSessionService()

    older = await _seed_visible_session(service, "u1", "first chat", timestamp=100.0)
    newer = await _seed_visible_session(service, "u1", "second chat", timestamp=200.0)
    # A second real turn on `older` advances ITS `chat_activity_at` past
    # `newer`'s.
    session = await service.get_session(older, "u1")
    session = await _append(service, session, _user_event("inv-2", "a later message", timestamp=300.0))
    await _append(service, session, _assistant_event("inv-2", "a later reply", timestamp=301.0))
    session = await service.get_session(older, "u1")
    await record_user_turn_activity(service, session, "a later message", "inv-2")

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert [s.session_id for s in summaries] == [older, newer]


@pytest.mark.asyncio
async def test_list_limit_applies_after_visibility_filtering_not_before() -> None:
    service = ApiSessionService()

    session_ids = [
        await _seed_visible_session(service, "u1", f"message {i}", timestamp=100.0 + i)
        for i in range(5)
    ]

    summaries = await history.list_saved_sessions(service, "u1", limit=2)
    assert len(summaries) == 2
    # The 2 most recently active ones.
    assert [s.session_id for s in summaries] == [session_ids[4], session_ids[3]]


@pytest.mark.asyncio
async def test_legacy_session_with_a_real_message_but_no_marker_is_backfilled_true() -> None:
    """Simulates a pre-B4B session: real conversational events exist, but
    `has_visible_message` was never written (B4B's own `create_session`
    change didn't exist yet when this session was created).
    """
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await _clear_visibility_marker(service, session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "a pre-existing legacy message"))
    session = await _append(service, session, _assistant_event("inv-1", "a legacy reply"))

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert len(summaries) == 1
    assert summaries[0].title == "a pre-existing legacy message"

    refreshed = await service.get_session(session_id, "u1")
    assert refreshed.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True
    assert refreshed.state.get(CHAT_TITLE_STATE_KEY) == "a pre-existing legacy message"


@pytest.mark.asyncio
async def test_legacy_empty_session_with_no_marker_is_backfilled_false_and_excluded() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await _clear_visibility_marker(service, session_id, "u1")
    # Only a state-only event -- never a genuine user message.
    await _append(service, session, _state_only_event("inv-x", {"some_internal_key": "value"}))

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert summaries == []

    refreshed = await service.get_session(session_id, "u1")
    assert refreshed.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is False


@pytest.mark.asyncio
async def test_legacy_backfill_happens_only_once_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await _clear_visibility_marker(service, session_id, "u1")
    await _append(service, session, _user_event("inv-1", "legacy message"))

    call_count = 0
    real_backfill = history._backfill_unknown_marker

    async def counting_backfill(session_service, session, user_id):
        nonlocal call_count
        call_count += 1
        return await real_backfill(session_service, session, user_id)

    monkeypatch.setattr(history, "_backfill_unknown_marker", counting_backfill)

    await history.list_saved_sessions(service, "u1", limit=50)
    await history.list_saved_sessions(service, "u1", limit=50)

    assert call_count == 1


@pytest.mark.asyncio
async def test_session_summary_dto_never_carries_raw_state_or_events() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    chat_service = ChatService(service, runner=FakeRunner(service))
    await chat_service.run_turn(session_id, "hello", "u1")

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    dumped = summaries[0].model_dump()
    assert set(dumped.keys()) == {"session_id", "title", "updated_at"}


# --- get_session_history --------------------------------------------------


@pytest.mark.asyncio
async def test_single_turn_projects_user_and_assistant_messages() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "hello there", timestamp=100.0))
    await _append(service, session, _assistant_event("inv-1", "hi, how can I help?", timestamp=101.0))

    response = await history.get_session_history(service, _fresh_attachment_service(), session_id, "u1")

    assert response.session_id == session_id
    assert len(response.messages) == 2
    user_msg, assistant_msg = response.messages
    assert user_msg.role == "user"
    assert user_msg.text == "hello there"
    assert user_msg.turn_id == "inv-1"
    assert user_msg.message_id == "inv-1:user"
    assert assistant_msg.role == "assistant"
    assert assistant_msg.text == "hi, how can I help?"
    assert assistant_msg.message_id == "inv-1:assistant"
    assert assistant_msg.turn_id == "inv-1"


@pytest.mark.asyncio
async def test_multiple_turns_preserve_chronological_order() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "first"))
    session = await _append(service, session, _assistant_event("inv-1", "first reply"))
    session = await _append(service, session, _user_event("inv-2", "second"))
    await _append(service, session, _assistant_event("inv-2", "second reply"))

    response = await history.get_session_history(service, _fresh_attachment_service(), session_id, "u1")

    texts = [m.text for m in response.messages]
    assert texts == ["first", "first reply", "second", "second reply"]


@pytest.mark.asyncio
async def test_user_turn_with_no_final_assistant_response_returns_only_the_user_message() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "are you there?"))
    # Only a function call -- never a final response.
    await _append(service, session, _function_call_event("inv-1", "some_tool", {"x": 1}))

    response = await history.get_session_history(service, _fresh_attachment_service(), session_id, "u1")

    assert len(response.messages) == 1
    assert response.messages[0].role == "user"
    assert response.messages[0].text == "are you there?"


@pytest.mark.asyncio
async def test_state_only_user_authored_events_are_excluded() -> None:
    """`persist_state_delta`-authored events (author='user', content=None)
    must never be mistaken for a real user message.
    """
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _state_only_event("inv-0", {"selected_teams_chat_id": "c1"}))
    session = await _append(service, session, _user_event("inv-1", "real message"))
    await _append(service, session, _assistant_event("inv-1", "real reply"))

    response = await history.get_session_history(service, _fresh_attachment_service(), session_id, "u1")

    assert [m.text for m in response.messages] == ["real message", "real reply"]


def _make_real_runner(service: ApiSessionService):
    """A REAL `google.adk.runners.Runner`, never `FakeRunner` -- that
    fake's own `rewind_async` is documented as "a plain recording stub,
    never a reimplementation of ADK's own `Runner.rewind_async`"
    (`_api_fakes.py`), so it never actually mutates the session. Mirrors
    `test_chat_service_rewind.py`'s own `_make_real_runner` exactly.
    """
    from google.adk.agents import Agent
    from google.adk.runners import Runner

    agent = Agent(name="test_agent", model="gemini-2.0-flash")
    return Runner(app_name="slopanoc-api", agent=agent, session_service=service.adk_session_service)


@pytest.mark.asyncio
async def test_rewound_turn_is_excluded_from_history() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "turn one"))
    session = await _append(service, session, _assistant_event("inv-1", "reply one"))
    session = await _append(service, session, _user_event("inv-2", "turn two, about to be discarded"))
    session = await _append(service, session, _assistant_event("inv-2", "reply two"))

    chat_service = ChatService(service, runner=_make_real_runner(service))
    await chat_service.rewind_before_user_turn(session_id, 1, "u1")  # discard turn two onward

    response = await history.get_session_history(service, _fresh_attachment_service(), session_id, "u1")

    assert [m.text for m in response.messages] == ["turn one", "reply one"]


@pytest.mark.asyncio
async def test_function_call_and_response_events_never_become_messages() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "do the thing"))
    session = await _append(service, session, _function_call_event("inv-1", "teams_get_messages", {"chat_id": "SECRET-CHAT-ID"}))
    session = await _append(
        service, session, _function_response_event("inv-1", "teams_get_messages", {"messages": ["SECRET CONTENT"]})
    )
    await _append(service, session, _assistant_event("inv-1", "here is the summary"))

    response = await history.get_session_history(service, _fresh_attachment_service(), session_id, "u1")

    texts = [m.text for m in response.messages]
    assert texts == ["do the thing", "here is the summary"]
    for message in response.messages:
        assert "SECRET" not in message.text


@pytest.mark.asyncio
async def test_only_the_last_final_event_in_a_turn_is_used_never_intermediate_text() -> None:
    """A turn can have more than one is_final_response()==True-shaped
    event only in unusual cases, but the projection must always take the
    LAST one seen -- mirrors chat_service.py's own `final_text = text`
    overwrite-as-you-go loop exactly.
    """
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "question"))
    session = await _append(service, session, _assistant_event("inv-1", "draft answer, superseded"))
    await _append(service, session, _assistant_event("inv-1", "final answer"))

    response = await history.get_session_history(service, _fresh_attachment_service(), session_id, "u1")

    assert response.messages[-1].text == "final answer"


@pytest.mark.asyncio
async def test_thought_parts_are_never_included_in_projected_text() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "question"))
    await _append(
        service,
        session,
        _assistant_event("inv-1", text="the visible answer", thought_text="SECRET CHAIN OF THOUGHT"),
    )

    response = await history.get_session_history(service, _fresh_attachment_service(), session_id, "u1")

    assistant_message = response.messages[-1]
    assert assistant_message.text == "the visible answer"
    assert "SECRET" not in assistant_message.text


@pytest.mark.asyncio
async def test_timestamps_are_mapped_from_the_real_projected_event() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "hello", timestamp=1_700_000_000.0))
    await _append(service, session, _assistant_event("inv-1", "hi", timestamp=1_700_000_005.0))

    response = await history.get_session_history(service, _fresh_attachment_service(), session_id, "u1")

    assert response.messages[0].created_at == history._iso(1_700_000_000.0)
    assert response.messages[1].created_at == history._iso(1_700_000_005.0)


@pytest.mark.asyncio
async def test_cross_user_history_access_is_denied_like_not_found() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("owner")
    session = await service.get_session(session_id, "owner")
    await _append(service, session, _user_event("inv-1", "private message"))

    with pytest.raises(SafeErrorException) as exc_info:
        await history.get_session_history(service, _fresh_attachment_service(), session_id, "someone-else")
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_unknown_session_history_is_denied() -> None:
    service = ApiSessionService()
    with pytest.raises(SafeErrorException) as exc_info:
        await history.get_session_history(service, _fresh_attachment_service(), "does-not-exist", "u1")
    assert exc_info.value.safe_error.error_code == "not_found"


# --- attachment history (fixture-based, per B4A: B5 creates no real LINKED rows yet) ---


@pytest.mark.asyncio
async def test_linked_attachment_appears_only_on_the_matching_user_message() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "here is a screenshot"))
    await _append(service, session, _assistant_event("inv-1", "thanks, I see it"))

    attachment_service = _fresh_attachment_service()
    created = await attachment_service.create(
        owner_user_id="u1",
        session_id=session_id,
        original_filename="screenshot.png",
        mime_type="image/png",
        size_bytes=1234,
        sha256="a" * 64,
    )
    await attachment_service.link_to_message(created.attachment_id, "u1", session_id, "inv-1")

    response = await history.get_session_history(service, attachment_service, session_id, "u1")

    user_msg, assistant_msg = response.messages
    assert len(user_msg.attachments) == 1
    assert user_msg.attachments[0].attachment_id == created.attachment_id
    assert user_msg.attachments[0].filename == "screenshot.png"
    assert user_msg.attachments[0].mime_type == "image/png"
    assert user_msg.attachments[0].size_bytes == 1234
    assert assistant_msg.attachments == []


@pytest.mark.asyncio
async def test_ready_unlinked_attachment_never_appears_in_history() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    await _append(service, session, _user_event("inv-1", "an image was uploaded but never sent"))

    attachment_service = _fresh_attachment_service()
    await attachment_service.create(
        owner_user_id="u1",
        session_id=session_id,
        original_filename="draft.png",
        mime_type="image/png",
        size_bytes=100,
        sha256="b" * 64,
    )

    response = await history.get_session_history(service, attachment_service, session_id, "u1")
    assert response.messages[0].attachments == []


@pytest.mark.asyncio
async def test_deleted_attachment_never_appears_in_history() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    await _append(service, session, _user_event("inv-1", "message"))

    attachment_service = _fresh_attachment_service()
    created = await attachment_service.create(
        owner_user_id="u1",
        session_id=session_id,
        original_filename="deleted.png",
        mime_type="image/png",
        size_bytes=100,
        sha256="c" * 64,
    )
    await attachment_service.link_to_message(created.attachment_id, "u1", session_id, "inv-1")
    await attachment_service.mark_deleted(created.attachment_id, "u1", session_id)

    response = await history.get_session_history(service, attachment_service, session_id, "u1")
    assert response.messages[0].attachments == []


@pytest.mark.asyncio
async def test_attachment_service_is_queried_exactly_once_per_history_request(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "first"))
    session = await _append(service, session, _assistant_event("inv-1", "reply"))
    session = await _append(service, session, _user_event("inv-2", "second"))
    await _append(service, session, _assistant_event("inv-2", "reply two"))

    attachment_service = _fresh_attachment_service()
    call_count = 0
    real_get_for_owner_session = attachment_service.get_for_owner_session

    async def counting(*args: Any, **kwargs: Any):
        nonlocal call_count
        call_count += 1
        return await real_get_for_owner_session(*args, **kwargs)

    monkeypatch.setattr(attachment_service, "get_for_owner_session", counting)

    await history.get_session_history(service, attachment_service, session_id, "u1")

    assert call_count == 1


@pytest.mark.asyncio
async def test_attachment_history_dto_never_leaks_internal_storage_fields() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    await _append(service, session, _user_event("inv-1", "message"))

    attachment_service = _fresh_attachment_service()
    created = await attachment_service.create(
        owner_user_id="u1",
        session_id=session_id,
        original_filename="photo.png",
        mime_type="image/png",
        size_bytes=100,
        sha256="d" * 64,
    )
    await attachment_service.link_to_message(created.attachment_id, "u1", session_id, "inv-1")

    response = await history.get_session_history(service, attachment_service, session_id, "u1")
    dumped = response.messages[0].attachments[0].model_dump()
    assert set(dumped.keys()) == {"attachment_id", "filename", "mime_type", "size_bytes"}


# --- rename_session --------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_owned_session_succeeds_and_persists() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")

    result = await history.rename_session(service, session_id, "u1", "My renamed chat")
    assert result.title == "My renamed chat"

    refreshed = await service.get_session(session_id, "u1")
    assert refreshed.state.get(CHAT_TITLE_STATE_KEY) == "My renamed chat"


@pytest.mark.asyncio
async def test_rename_trims_surrounding_whitespace() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")

    result = await history.rename_session(service, session_id, "u1", "   spaced out   ")
    assert result.title == "spaced out"


@pytest.mark.asyncio
async def test_rename_rejects_empty_title() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")

    with pytest.raises(SafeErrorException) as exc_info:
        await history.rename_session(service, session_id, "u1", "   ")
    assert exc_info.value.safe_error.error_code == "validation_error"


@pytest.mark.asyncio
async def test_rename_rejects_a_title_over_the_maximum_length_without_truncating() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")

    too_long = "x" * 201
    with pytest.raises(SafeErrorException) as exc_info:
        await history.rename_session(service, session_id, "u1", too_long)
    assert exc_info.value.safe_error.error_code == "validation_error"

    # Never silently truncated and stored -- the rejected rename must
    # leave no title at all.
    refreshed = await service.get_session(session_id, "u1")
    assert refreshed.state.get(CHAT_TITLE_STATE_KEY) is None


@pytest.mark.asyncio
async def test_rename_of_another_users_session_behaves_like_not_found() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("owner")

    with pytest.raises(SafeErrorException) as exc_info:
        await history.rename_session(service, session_id, "someone-else", "hijacked title")
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_a_later_real_turn_never_overwrites_a_manual_rename() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    chat_service = ChatService(service, runner=FakeRunner(service))
    await chat_service.run_turn(session_id, "first message", "u1")

    await history.rename_session(service, session_id, "u1", "Manually renamed")
    await chat_service.run_turn(session_id, "a later, unrelated message", "u1")

    refreshed = await service.get_session(session_id, "u1")
    assert refreshed.state.get(CHAT_TITLE_STATE_KEY) == "Manually renamed"

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert summaries[0].title == "Manually renamed"
