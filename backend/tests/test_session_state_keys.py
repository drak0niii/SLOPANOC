"""Tests for POST-5.1 B4B's `backend/api/session_state_keys.py` -- the
leaf module owning `HAS_VISIBLE_MESSAGE_STATE_KEY`/`CHAT_TITLE_STATE_KEY`/
`CHAT_ACTIVITY_AT_STATE_KEY`, title derivation, and the per-turn activity
write.
"""
from __future__ import annotations

import pytest
from google.adk.events import Event
from google.genai import types

from backend.api.session_service import ApiSessionService
from backend.api.session_state_keys import (
    CHAT_ACTIVITY_AT_STATE_KEY,
    CHAT_TITLE_STATE_KEY,
    HAS_VISIBLE_MESSAGE_STATE_KEY,
    derive_chat_title,
    find_user_event_for_invocation,
    record_user_turn_activity,
)


# --- derive_chat_title -------------------------------------------------


def test_derive_chat_title_collapses_whitespace_and_trims() -> None:
    assert derive_chat_title("  hello   world  ") == "hello world"


def test_derive_chat_title_truncates_past_48_chars_with_ellipsis() -> None:
    text = "x" * 60
    title = derive_chat_title(text)
    assert title == "x" * 48 + "…"
    assert len(title) == 49


def test_derive_chat_title_does_not_truncate_at_exactly_48_chars() -> None:
    text = "x" * 48
    assert derive_chat_title(text) == text


def test_derive_chat_title_empty_or_whitespace_only_falls_back_to_new_chat() -> None:
    assert derive_chat_title("") == "New chat"
    assert derive_chat_title("   \n\t  ") == "New chat"


def test_derive_chat_title_collapses_newlines_and_tabs_too() -> None:
    assert derive_chat_title("line one\nline\ttwo") == "line one line two"


# --- record_user_turn_activity ------------------------------------------


async def _append_real_user_event(service: ApiSessionService, session, invocation_id: str, text: str, timestamp: float):
    event = Event(
        invocation_id=invocation_id,
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text=text)]),
    )
    event.timestamp = timestamp
    await service.adk_session_service.append_event(session, event)
    return await service.get_session(session.id, session.user_id)


@pytest.mark.asyncio
async def test_first_turn_writes_marker_title_and_activity() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    assert session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is False

    session = await _append_real_user_event(service, session, "inv-1", "what's the status of the ops incident?", 100.0)
    await record_user_turn_activity(service, session, "what's the status of the ops incident?", "inv-1")

    refreshed = await service.get_session(session_id)
    assert refreshed.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True
    assert refreshed.state.get(CHAT_TITLE_STATE_KEY) == "what's the status of the ops incident?"
    assert refreshed.state.get(CHAT_ACTIVITY_AT_STATE_KEY) == 100.0


@pytest.mark.asyncio
async def test_second_turn_advances_activity_but_never_rewrites_title() -> None:
    """Instruction: 'Do not rewrite title automatically on later turns' --
    but `chat_activity_at` MUST advance on every genuine turn.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    session = await _append_real_user_event(service, session, "inv-1", "first message", 100.0)
    await record_user_turn_activity(service, session, "first message", "inv-1")

    session = await service.get_session(session_id)
    session = await _append_real_user_event(service, session, "inv-2", "second message, much later", 200.0)
    await record_user_turn_activity(service, session, "second message, much later", "inv-2")

    refreshed = await service.get_session(session_id)
    assert refreshed.state.get(CHAT_TITLE_STATE_KEY) == "first message"
    assert refreshed.state.get(CHAT_ACTIVITY_AT_STATE_KEY) == 200.0


@pytest.mark.asyncio
async def test_activity_timestamp_comes_from_the_real_persisted_user_event_not_wall_clock() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    session = await _append_real_user_event(service, session, "inv-1", "message", 1_700_000_000.0)
    await record_user_turn_activity(service, session, "message", "inv-1")

    refreshed = await service.get_session(session_id)
    assert refreshed.state.get(CHAT_ACTIVITY_AT_STATE_KEY) == 1_700_000_000.0


@pytest.mark.asyncio
async def test_activity_write_reads_in_memory_state_without_a_refetch() -> None:
    """Proves the documented ADK guarantee this function depends on:
    `append_event` mutates the passed `session` object's `.state`/
    `.events` in place, so a SECOND call using the SAME (unrefetched)
    `session` object still sees `has_visible_message` as already-true --
    exactly how `chat_service.py` uses it (one re-fetched `session`
    object per `_run_turn_events` call).
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    session = await _append_real_user_event(service, session, "inv-1", "first message", 100.0)
    await record_user_turn_activity(service, session, "first message", "inv-1")
    # Deliberately reuse the SAME (now-stale-for-title-purposes) session
    # object reference from before -- `session` here was reassigned by
    # `_append_real_user_event`'s own return value, so it already reflects
    # the append above.
    await record_user_turn_activity(service, session, "should never overwrite the title", "inv-1")

    refreshed = await service.get_session(session_id)
    assert refreshed.state.get(CHAT_TITLE_STATE_KEY) == "first message"


@pytest.mark.asyncio
async def test_missing_user_event_for_invocation_skips_activity_but_still_marks_visible() -> None:
    """Defensive path: if the genuine user event can't be found for the
    given invocation_id (should never happen given B4A's proven happens-
    before guarantee), the visibility/title write must still proceed --
    only the activity-timestamp write is skipped.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    await record_user_turn_activity(service, session, "orphaned text, no matching event", "inv-does-not-exist")

    refreshed = await service.get_session(session_id)
    assert refreshed.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True
    assert refreshed.state.get(CHAT_TITLE_STATE_KEY) == "orphaned text, no matching event"
    assert refreshed.state.get(CHAT_ACTIVITY_AT_STATE_KEY) is None


# --- find_user_event_for_invocation --------------------------------------


def test_find_user_event_for_invocation_ignores_state_only_events_with_same_author() -> None:
    from google.adk.events import EventActions

    state_only = Event(invocation_id="inv-1", author="user", actions=EventActions(state_delta={"x": 1}))
    real_user_event = Event(
        invocation_id="inv-1",
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text="hi")]),
    )
    found = find_user_event_for_invocation([state_only, real_user_event], "inv-1")
    assert found is real_user_event


def test_find_user_event_for_invocation_returns_none_when_absent() -> None:
    assert find_user_event_for_invocation([], "inv-1") is None
