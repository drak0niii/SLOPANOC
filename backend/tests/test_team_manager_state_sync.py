"""Tests for the cross-turn Teams-context regression fix.

Two layers are covered:

1. `state_sync.compute_state_updates` -- the pure, deterministic rule for
   *what* to persist from an incident_manager result (fully unit-testable,
   no ADK objects needed).
2. An integration-style check of the actual ADK mechanism the fix relies
   on for *reading* that state back into team_manager's own prompt:
   `google.adk.utils.instructions_utils.inject_session_state`'s `{var?}`
   placeholder syntax, exercised against real `InMemorySessionService`/
   `Session`/`InvocationContext` objects (not fakes) -- this is the actual
   ADK boundary the regression lived at, so it is verified for real rather
   than assumed.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION
from backend.agents.team_manager.state_sync import (
    LAST_TEAMS_EVIDENCE_STATE_KEY,
    SELECTED_TEAMS_CHAT_ID_STATE_KEY,
    SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY,
    compute_state_updates,
    sync_incident_manager_result_to_state,
)

# --- compute_state_updates: the pure, deterministic rule -------------------


def _ok_response(
    chat_id: str = "c1",
    chat_title: str = "Operations Bridge",
    evidence: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    return {
        "outcome": "ok",
        "chat_id": chat_id,
        "chat_title": chat_title,
        "summary": "Something happened.",
        "evidence": evidence if evidence is not None else [],
        "candidate_titles": [],
    }


def test_selected_chat_stored_after_successful_resolution() -> None:
    updates = compute_state_updates(_ok_response(chat_id="c1", chat_title="Operations Bridge"))

    assert updates[SELECTED_TEAMS_CHAT_ID_STATE_KEY] == "c1"
    assert updates[SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY] == "Operations Bridge"


def test_no_result_outcome_also_stores_the_resolved_chat() -> None:
    """"no_result" still means the chat itself was found -- just empty."""
    response = _ok_response(chat_id="c1", chat_title="Operations Bridge")
    response["outcome"] = "no_result"
    response["evidence"] = []

    updates = compute_state_updates(response)

    assert updates[SELECTED_TEAMS_CHAT_ID_STATE_KEY] == "c1"
    assert updates[SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY] == "Operations Bridge"


def test_explicit_new_chat_replaces_selected_chat() -> None:
    first = compute_state_updates(_ok_response(chat_id="c1", chat_title="Operations Bridge"))
    second = compute_state_updates(_ok_response(chat_id="c2", chat_title="Production Bridge"))

    assert first[SELECTED_TEAMS_CHAT_ID_STATE_KEY] == "c1"
    assert second[SELECTED_TEAMS_CHAT_ID_STATE_KEY] == "c2"
    assert second[SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY] == "Production Bridge"


def test_ambiguous_result_does_not_replace_selected_chat() -> None:
    response = {
        "outcome": "ambiguous",
        "candidate_titles": ["Operations Bridge", "Operations Bridge (Archived)"],
    }

    updates = compute_state_updates(response)

    assert SELECTED_TEAMS_CHAT_ID_STATE_KEY not in updates
    assert SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY not in updates


def test_not_found_result_does_not_replace_selected_chat() -> None:
    response = {"outcome": "not_found"}

    updates = compute_state_updates(response)

    assert SELECTED_TEAMS_CHAT_ID_STATE_KEY not in updates
    assert SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY not in updates


def test_error_result_does_not_replace_selected_chat() -> None:
    response = {"outcome": "error", "detail": "Something went wrong."}

    updates = compute_state_updates(response)

    assert SELECTED_TEAMS_CHAT_ID_STATE_KEY not in updates
    assert SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY not in updates


def test_evidence_follow_up_uses_last_validated_evidence() -> None:
    evidence = [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"}]

    updates = compute_state_updates(_ok_response(evidence=evidence))

    assert updates[LAST_TEAMS_EVIDENCE_STATE_KEY] == evidence


def test_invented_evidence_shape_never_enters_state() -> None:
    """state_sync trusts that `evidence` already passed incident_manager's
    own deterministic validation (evidence.py) -- but it still refuses to
    persist any entry that is malformed at the shape level, so a stray
    invented/incomplete entry can never leak into session state even if
    somehow present.
    """
    evidence = [
        {"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"},
        {"message_id": "invented", "author": "Nobody"},  # missing sent_at -- dropped
        "not a dict",  # dropped
    ]

    updates = compute_state_updates(_ok_response(evidence=evidence))

    assert updates[LAST_TEAMS_EVIDENCE_STATE_KEY] == [
        {"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"}
    ]


def test_non_ok_outcome_never_touches_evidence_state() -> None:
    response = {"outcome": "no_result", "chat_id": "c1", "chat_title": "Ops"}

    updates = compute_state_updates(response)

    assert LAST_TEAMS_EVIDENCE_STATE_KEY not in updates


def test_non_dict_tool_response_is_a_no_op() -> None:
    assert compute_state_updates(None) == {}
    assert compute_state_updates("not a dict") == {}


# --- Message content is never persisted into session state -----------------
#
# This is the deterministic half of the follow-up-content fix: the reason
# team_manager cannot answer "what did that message say?" from state alone
# is that state is structurally incapable of holding message content, not
# an accidental omission. These tests pin that guarantee at the
# `compute_state_updates`/`_sanitize_evidence` boundary.


def test_evidence_text_field_is_dropped_even_if_present_on_the_input() -> None:
    """`_sanitize_evidence` reshapes down to exactly `message_id`/`author`/
    `sent_at` -- if an evidence entry somehow carried a `text`/`content`
    field (it shouldn't, since `TeamsEvidence` has no such field), that
    field must never survive into what gets stored in session state.
    """
    evidence = [
        {
            "message_id": "m1",
            "author": "Alex",
            "sent_at": "2026-08-31T13:00:00Z",
            "text": "This should never be persisted.",
            "content": "<p>Neither should this.</p>",
        }
    ]

    updates = compute_state_updates(_ok_response(evidence=evidence))

    stored = updates[LAST_TEAMS_EVIDENCE_STATE_KEY]
    assert stored == [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"}]
    assert "text" not in stored[0]
    assert "content" not in stored[0]


def test_summary_text_is_never_written_to_session_state() -> None:
    """`summary` -- incident_manager's actual prose answer, which may
    contain full message content -- has no corresponding state key at all;
    `compute_state_updates` only ever writes the three known keys.
    """
    response = _ok_response(evidence=[{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"}])
    response["summary"] = "Alex said: the deployment is delayed until Friday."

    updates = compute_state_updates(response)

    assert set(updates) <= {
        SELECTED_TEAMS_CHAT_ID_STATE_KEY,
        SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY,
        LAST_TEAMS_EVIDENCE_STATE_KEY,
    }
    for value in updates.values():
        if isinstance(value, str):
            assert "deployment is delayed" not in value
        elif isinstance(value, list):
            for entry in value:
                assert "deployment is delayed" not in str(entry)


def test_callback_never_writes_a_message_text_or_content_state_key() -> None:
    """End-to-end through the actual `after_tool_callback`: whatever keys
    land in `tool_context.state`, none of them is a message-content key.
    """
    ctx = _FakeToolContext()
    response = _ok_response(
        evidence=[{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"}]
    )
    response["summary"] = "Full grounded answer text goes here."

    sync_incident_manager_result_to_state(
        tool=_FakeTool("incident_manager"),
        args={},
        tool_context=ctx,
        tool_response=response,
    )

    for key in ctx.state:
        assert key in {
            SELECTED_TEAMS_CHAT_ID_STATE_KEY,
            SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY,
            LAST_TEAMS_EVIDENCE_STATE_KEY,
        }
    for entry in ctx.state.get(LAST_TEAMS_EVIDENCE_STATE_KEY, []):
        assert set(entry) == {"message_id", "author", "sent_at"}


# --- sync_incident_manager_result_to_state: the after_tool_callback wiring -


class _FakeState(dict):
    pass


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = _FakeState(state or {})


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_callback_ignores_other_tool_calls() -> None:
    ctx = _FakeToolContext()

    result = sync_incident_manager_result_to_state(
        tool=_FakeTool("some_other_tool"),
        args={},
        tool_context=ctx,
        tool_response=_ok_response(),
    )

    assert result is None
    assert ctx.state == {}


def test_callback_writes_state_for_incident_manager_calls() -> None:
    ctx = _FakeToolContext()

    result = sync_incident_manager_result_to_state(
        tool=_FakeTool("incident_manager"),
        args={},
        tool_context=ctx,
        tool_response=_ok_response(chat_id="c1", chat_title="Operations Bridge"),
    )

    assert result is None  # never substitutes the tool result the LLM sees
    assert ctx.state[SELECTED_TEAMS_CHAT_ID_STATE_KEY] == "c1"
    assert ctx.state[SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY] == "Operations Bridge"


def test_callback_preserves_prior_state_on_a_failed_follow_up() -> None:
    """A failed attempt to select a different chat must not destroy the
    previously valid selected chat.
    """
    ctx = _FakeToolContext(
        state={
            SELECTED_TEAMS_CHAT_ID_STATE_KEY: "c1",
            SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY: "Operations Bridge",
        }
    )

    sync_incident_manager_result_to_state(
        tool=_FakeTool("incident_manager"),
        args={},
        tool_context=ctx,
        tool_response={"outcome": "not_found"},
    )

    assert ctx.state[SELECTED_TEAMS_CHAT_ID_STATE_KEY] == "c1"
    assert ctx.state[SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY] == "Operations Bridge"


def test_multiple_consecutive_calls_keep_reusing_the_same_stored_chat() -> None:
    ctx = _FakeToolContext()

    for _ in range(3):
        sync_incident_manager_result_to_state(
            tool=_FakeTool("incident_manager"),
            args={},
            tool_context=ctx,
            tool_response=_ok_response(chat_id="c1", chat_title="Operations Bridge"),
        )

    assert ctx.state[SELECTED_TEAMS_CHAT_ID_STATE_KEY] == "c1"
    assert ctx.state[SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY] == "Operations Bridge"


# --- Integration: real ADK session-state instruction templating ------------


@pytest.mark.asyncio
async def test_instruction_placeholder_is_empty_when_nothing_is_selected() -> None:
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.agents.readonly_context import ReadonlyContext
    from google.adk.sessions import InMemorySessionService
    from google.adk.utils.instructions_utils import inject_session_state

    from backend.agents.team_manager.agent import team_manager

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="slopanoc-test", user_id="u1", state={}
    )
    invocation_context = InvocationContext(
        session_service=session_service,
        invocation_id="test-inv-1",
        agent=team_manager,
        session=session,
    )

    rendered = await inject_session_state(
        TEAM_MANAGER_INSTRUCTION, ReadonlyContext(invocation_context)
    )

    assert 'Currently selected Teams chat (from session state, if any): topic "".' in rendered
    assert "{selected_teams_chat_topic?}" not in rendered
    assert "{last_teams_evidence?}" not in rendered


@pytest.mark.asyncio
async def test_instruction_placeholder_reflects_selected_chat_and_evidence() -> None:
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.agents.readonly_context import ReadonlyContext
    from google.adk.sessions import InMemorySessionService
    from google.adk.utils.instructions_utils import inject_session_state

    from backend.agents.team_manager.agent import team_manager

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="slopanoc-test",
        user_id="u1",
        state={
            SELECTED_TEAMS_CHAT_ID_STATE_KEY: "c1",
            SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY: "Operations Bridge",
            LAST_TEAMS_EVIDENCE_STATE_KEY: [
                {"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"}
            ],
        },
    )
    invocation_context = InvocationContext(
        session_service=session_service,
        invocation_id="test-inv-2",
        agent=team_manager,
        session=session,
    )

    rendered = await inject_session_state(
        TEAM_MANAGER_INSTRUCTION, ReadonlyContext(invocation_context)
    )

    assert (
        'Currently selected Teams chat (from session state, if any): topic '
        '"Operations Bridge".' in rendered
    )
    assert "Alex" in rendered
    assert "m1" in rendered


@pytest.mark.asyncio
async def test_state_survives_across_turns_in_the_same_session() -> None:
    """Simulates two turns in the same ADK session: the first "writes"
    (as team_manager's after_tool_callback would, via a real State
    object backed by the session), the second reads back the same value.
    """
    from google.adk.sessions import InMemorySessionService
    from google.adk.sessions.state import State

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="slopanoc-test", user_id="u1", state={}
    )

    turn_1_state = State(value=session.state, delta={})
    turn_1_state[SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY] = "Operations Bridge"

    # A later turn re-reading the same (persisted) session state.
    turn_2_state = State(value=session.state, delta={})
    assert turn_2_state.get(SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY) == "Operations Bridge"


@pytest.mark.asyncio
async def test_separate_sessions_do_not_share_selected_chat_state() -> None:
    from google.adk.sessions import InMemorySessionService
    from google.adk.sessions.state import State

    session_service = InMemorySessionService()
    session_a = await session_service.create_session(
        app_name="slopanoc-test", user_id="u1", state={}
    )
    session_b = await session_service.create_session(
        app_name="slopanoc-test", user_id="u2", state={}
    )

    State(value=session_a.state, delta={})[SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY] = (
        "Operations Bridge"
    )

    assert session_a.state.get(SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY) == "Operations Bridge"
    assert session_b.state.get(SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY) is None
