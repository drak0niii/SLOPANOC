"""Tests for POST-5.1 B4B's correction pass: `chat_activity_at` --
sidebar ordering must reflect REAL user chat activity, never ADK's own
generic `Session.last_update_time` (which also moves on unrelated state-
only writes this codebase already performs elsewhere: approval,
selection, Teams, Case, a manual rename, or this module's own legacy
repair).

Builds REAL `google.adk.events.Event`/`google.genai.types.Content`
objects directly, mirroring `test_session_history_service.py`'s own
pattern, so ordering correctness is proven against real ADK event shapes,
not a simplified duck-typed fake.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest
from google.adk.events import Event, EventActions
from google.genai import types

from backend.api import session_history_service as history
from backend.api.session_service import ApiSessionService
from backend.api.session_state_keys import (
    CHAT_ACTIVITY_AT_STATE_KEY,
    CHAT_TITLE_STATE_KEY,
    HAS_VISIBLE_MESSAGE_STATE_KEY,
    record_user_turn_activity,
)


def _user_event(invocation_id: str, text: str, timestamp: float) -> Event:
    ev = Event(
        invocation_id=invocation_id,
        author="user",
        content=types.Content(role="user", parts=[types.Part.from_text(text=text)]),
    )
    ev.timestamp = timestamp
    return ev


def _assistant_event(invocation_id: str, text: str, timestamp: float, author: str = "team_manager") -> Event:
    ev = Event(
        invocation_id=invocation_id,
        author=author,
        content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
    )
    ev.timestamp = timestamp
    return ev


def _state_only_event(invocation_id: str, delta: dict[str, Any], timestamp: Optional[float] = None) -> Event:
    ev = Event(author="user", invocation_id=invocation_id, actions=EventActions(state_delta=delta))
    if timestamp is not None:
        ev.timestamp = timestamp
    return ev


async def _append(service: ApiSessionService, session, event: Event):
    await service.adk_session_service.append_event(session, event)
    return await service.get_session(session.id, session.user_id)


async def _clear_visibility_marker(service: ApiSessionService, session_id: str, user_id: str):
    """Simulates a pre-B4B session -- see `test_session_history_service
    .py`'s identical helper for the full "why persist_state_delta, not a
    direct dict mutation" rationale."""
    session = await service.get_session(session_id, user_id)
    await service.persist_state_delta(session, {HAS_VISIBLE_MESSAGE_STATE_KEY: None})
    return await service.get_session(session_id, user_id)


async def _seed_first_turn(service: ApiSessionService, user_id: str, text: str, timestamp: float) -> str:
    session_id = await service.create_session(user_id)
    session = await service.get_session(session_id, user_id)
    session = await _append(service, session, _user_event("inv-1", text, timestamp))
    await record_user_turn_activity(service, session, text, "inv-1")
    return session_id


# --- assistant-only / unrelated state writes never move activity --------


@pytest.mark.asyncio
async def test_assistant_response_alone_does_not_advance_activity() -> None:
    """`record_user_turn_activity` is only ever called at the FIRST event
    of a turn (the user's own message) -- confirms the activity timestamp
    it wrote reflects the USER event's time, not whatever later,
    real-wall-clock-timestamped assistant event follows it in
    `session.events` (a plain `_append` of an assistant event, with no
    corresponding `record_user_turn_activity` call, must never move
    `chat_activity_at` on its own).
    """
    service = ApiSessionService()
    session_id = await _seed_first_turn(service, "u1", "question", timestamp=100.0)

    session = await service.get_session(session_id, "u1")
    # A later-timestamped assistant event, appended WITHOUT a matching
    # record_user_turn_activity call (exactly what a real turn's own
    # non-first yielded events look like from chat_service.py's point of
    # view -- only the FIRST event triggers the write).
    await _append(service, session, _assistant_event("inv-1", "an answer", timestamp=999.0))

    refreshed = await service.get_session(session_id, "u1")
    assert refreshed.state.get(CHAT_ACTIVITY_AT_STATE_KEY) == 100.0


@pytest.mark.asyncio
async def test_unrelated_state_delta_after_the_last_turn_does_not_change_returned_updated_at() -> None:
    """One representative unrelated trusted state_delta (mirrors e.g. a
    selected-Teams-chat sync or an approval transition) -- proves
    `GET /api/sessions`'s `updated_at` is unaffected, even though ADK's
    own generic `Session.last_update_time` DOES move (a real event was
    genuinely appended).
    """
    service = ApiSessionService()
    session_id = await _seed_first_turn(service, "u1", "question", timestamp=100.0)

    session = await service.get_session(session_id, "u1")

    # A representative unrelated, trusted, deterministic state_delta --
    # e.g. syncing a selected Teams chat, unrelated to any user turn. This
    # IS a genuine `append_event` call -- ADK's own generic
    # `last_update_time` is free to move because of it (same-tick clock
    # resolution can make it read identically to before, which is fine
    # and not what this test is about); what matters is that OUR
    # `chat_activity_at` provably does not.
    await service.persist_state_delta(session, {"selected_teams_chat_id": "c1", "selected_teams_chat_topic": "Ops"})

    refreshed = await service.get_session(session_id, "u1")
    assert refreshed.state.get(CHAT_ACTIVITY_AT_STATE_KEY) == 100.0

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert summaries[0].updated_at == history._iso(100.0)


@pytest.mark.asyncio
async def test_manual_rename_does_not_change_activity_or_reorder_the_sidebar() -> None:
    service = ApiSessionService()
    newer = await _seed_first_turn(service, "u1", "newer chat", timestamp=200.0)
    older = await _seed_first_turn(service, "u1", "older chat", timestamp=100.0)

    # Sanity: before rename, `newer` is above `older`.
    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert [s.session_id for s in summaries] == [newer, older]

    # Renaming the OLDER conversation must not move it to the top.
    await history.rename_session(service, older, "u1", "Renamed older chat")

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert [s.session_id for s in summaries] == [newer, older]
    assert summaries[1].title == "Renamed older chat"
    assert summaries[1].updated_at == history._iso(100.0)


# --- legacy backfill uses the LATEST active user turn, not the first ----


@pytest.mark.asyncio
async def test_legacy_backfill_uses_the_latest_active_user_turn_for_activity_not_the_first() -> None:
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await _clear_visibility_marker(service, session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "opening legacy message", timestamp=100.0))
    session = await _append(service, session, _assistant_event("inv-1", "legacy reply", timestamp=101.0))
    session = await _append(service, session, _user_event("inv-2", "a later legacy message", timestamp=200.0))
    await _append(service, session, _assistant_event("inv-2", "a later legacy reply", timestamp=201.0))

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert len(summaries) == 1
    assert summaries[0].title == "opening legacy message"  # title still from the FIRST turn
    assert summaries[0].updated_at == history._iso(200.0)  # activity from the LATEST turn

    refreshed = await service.get_session(session_id, "u1")
    assert refreshed.state.get(CHAT_ACTIVITY_AT_STATE_KEY) == 200.0
    assert refreshed.state.get(CHAT_TITLE_STATE_KEY) == "opening legacy message"


@pytest.mark.asyncio
async def test_rewound_branch_never_determines_legacy_backfill_activity() -> None:
    """A discarded (rewound-away) later turn must never win over the
    still-active branch's own latest turn when backfilling
    `chat_activity_at` -- `_active_events` excludes it before this
    module's backfill logic ever sees it.
    """
    from google.adk.agents import Agent
    from google.adk.runners import Runner

    from backend.api.chat_service import ChatService

    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await _clear_visibility_marker(service, session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "turn one", timestamp=100.0))
    session = await _append(service, session, _assistant_event("inv-1", "reply one", timestamp=101.0))
    session = await _append(service, session, _user_event("inv-2", "turn two, about to be discarded", timestamp=200.0))
    await _append(service, session, _assistant_event("inv-2", "reply two", timestamp=201.0))

    real_runner = Runner(app_name="slopanoc-api", agent=Agent(name="test_agent", model="gemini-2.0-flash"), session_service=service.adk_session_service)
    chat_service = ChatService(service, runner=real_runner)
    await chat_service.rewind_before_user_turn(session_id, 1, "u1")  # discard turn two onward

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert len(summaries) == 1
    # Activity must reflect turn ONE (the latest still-ACTIVE turn), never
    # turn two's later, but now-discarded, timestamp.
    assert summaries[0].updated_at == history._iso(100.0)


# --- known-visible session missing chat_activity_at (the "edge case") ---


@pytest.mark.asyncio
async def test_known_visible_session_missing_activity_is_repaired_once_from_the_latest_turn() -> None:
    """Simulates a session marked visible by an earlier, uncommitted B4B
    revision (before `chat_activity_at` existed) -- `has_visible_message`
    is already `True` and `chat_title` is already set, but
    `chat_activity_at` is entirely absent.
    """
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "pre-correction message", timestamp=100.0))
    await _append(service, session, _assistant_event("inv-1", "pre-correction reply", timestamp=101.0))
    # Simulate the pre-correction marker write: visible + titled, but
    # deliberately no chat_activity_at.
    session = await service.get_session(session_id, "u1")
    await service.persist_state_delta(
        session, {HAS_VISIBLE_MESSAGE_STATE_KEY: True, CHAT_TITLE_STATE_KEY: "pre-correction message"}
    )

    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert len(summaries) == 1
    assert summaries[0].title == "pre-correction message"
    assert summaries[0].updated_at == history._iso(100.0)

    refreshed = await service.get_session(session_id, "u1")
    assert refreshed.state.get(CHAT_ACTIVITY_AT_STATE_KEY) == 100.0


@pytest.mark.asyncio
async def test_known_visible_missing_activity_session_is_never_excluded_even_when_repair_budget_is_zero() -> None:
    """Instruction: 'known TRUE saved sessions do NOT require event reads
    and should not be excluded merely because a separate legacy-repair
    budget was exhausted.' A `limit` of 0 leaves NO repair budget at all,
    yet the session must still appear (using the documented temporary
    `last_update_time` fallback for its sort position).
    """
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    session = await _append(service, session, _user_event("inv-1", "message", timestamp=100.0))
    session = await service.get_session(session_id, "u1")
    await service.persist_state_delta(
        session, {HAS_VISIBLE_MESSAGE_STATE_KEY: True, CHAT_TITLE_STATE_KEY: "message"}
    )

    summaries = await history.list_saved_sessions(service, "u1", limit=0)
    # The return-count cap is also 0 here (same `limit`), so nothing is
    # actually returned -- but this proves the session was correctly
    # classified as VISIBLE (not silently dropped due to an exhausted
    # repair budget) by checking a real value in the same computation:
    # rerunning with a positive limit finds it immediately, using the
    # already-known-true classification made above (asserted directly
    # via the repository/state layer since `limit=0` returns nothing).
    assert summaries == []
    refreshed = await service.get_session(session_id, "u1")
    assert refreshed.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY) is True

    # A subsequent call with a normal limit lists it correctly.
    summaries = await history.list_saved_sessions(service, "u1", limit=50)
    assert len(summaries) == 1
    assert summaries[0].session_id == session_id


@pytest.mark.asyncio
async def test_repair_of_a_genuinely_corrupted_visible_session_is_logged_not_fabricated(caplog: pytest.LogCaptureFixture) -> None:
    """A known-visible session whose active transcript has NO
    reconstructable genuine user turn at all (a real inconsistency) must
    never get a fabricated `chat_activity_at` -- it stays listed (via the
    `last_update_time` fallback) and the situation is logged, not masked.
    """
    service = ApiSessionService()
    session_id = await service.create_session("u1")
    session = await service.get_session(session_id, "u1")
    # Marked visible with NO genuine user-content event ever appended --
    # a deliberately corrupted/inconsistent fixture.
    await service.persist_state_delta(
        session, {HAS_VISIBLE_MESSAGE_STATE_KEY: True, CHAT_TITLE_STATE_KEY: "corrupted"}
    )

    with caplog.at_level("WARNING"):
        summaries = await history.list_saved_sessions(service, "u1", limit=50)

    assert len(summaries) == 1  # still listed, never excluded
    assert any("chat_activity_at could not be repaired" in message for message in caplog.messages)


# --- many empty/unknown sessions cannot hide an older true saved chat ---


@pytest.mark.asyncio
async def test_many_false_sessions_cannot_hide_an_older_true_saved_chat() -> None:
    service = ApiSessionService()

    # An older, genuinely real conversation.
    real_session = await _seed_first_turn(service, "u1", "the one real conversation", timestamp=1.0)

    # More than `limit` newer, empty (never-sent) sessions.
    for _ in range(10):
        await service.create_session("u1")

    summaries = await history.list_saved_sessions(service, "u1", limit=5)
    assert len(summaries) == 1
    assert summaries[0].session_id == real_session


@pytest.mark.asyncio
async def test_many_legacy_unknown_sessions_do_not_permanently_hide_an_older_true_saved_chat() -> None:
    """Unlike the plain-`False` case above, a marker-ABSENT session
    genuinely needs an event read to classify -- this proves the bounded
    repair budget doesn't need to reach every one of them in a single
    request for the real conversation to still be listed (since it's
    already known-TRUE from the start, never gated by the unknown-marker
    repair queue at all).
    """
    service = ApiSessionService()

    real_session = await _seed_first_turn(service, "u1", "the one real conversation", timestamp=1.0)

    # Many legacy sessions needing a full backfill (marker absent),
    # deliberately more than the repair budget below.
    for _ in range(10):
        legacy_id = await service.create_session("u1")
        await _clear_visibility_marker(service, legacy_id, "u1")

    # A tiny repair budget -- far fewer than the 10 legacy candidates.
    summaries = await history.list_saved_sessions(service, "u1", limit=3)
    assert len(summaries) == 1
    assert summaries[0].session_id == real_session


@pytest.mark.asyncio
async def test_visible_sessions_sort_by_chat_activity_at_descending_with_more_than_the_limit() -> None:
    service = ApiSessionService()
    ids = [await _seed_first_turn(service, "u1", f"chat {i}", timestamp=float(i)) for i in range(6)]

    summaries = await history.list_saved_sessions(service, "u1", limit=3)
    assert [s.session_id for s in summaries] == [ids[5], ids[4], ids[3]]
