"""Tests for the presentation-only `target_display_name` field (Phase 4G
hardening pass -- fixes the approval card showing the generic "Selected
Teams conversation" placeholder instead of the real, already-known Teams
chat topic for a `teams.sendMessage` proposal).

Covers the full path: `teams_propose_send_message` (sources it from
team_manager's own `selected_teams_chat_id`/`selected_teams_chat_topic`
session-state keys, copied into incident_manager's nested session per
ADK's `AgentTool` semantics -- see propose_write.py's module docstring)
-> the stored `ActionProposal` -> `map_pending_action` -> the API-facing
`PendingActionDTO`.

Explicitly proves the separation instruction requires: this field never
affects `payload_hash`/`authorize_write`/the execution payload -- it is
sourced independently and carried alongside, never mixed in.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.api.pending_action import map_pending_action
from backend.approval.canonical import compute_payload_hash
from backend.approval.schemas import WriteOperation
from backend.approval.service import create_action_proposal, load_active_proposal
from backend.tools.teams.propose_write import teams_propose_create_chat, teams_propose_send_message
from backend.tools.teams.state_keys import (
    SELECTED_TEAMS_CHAT_ID_STATE_KEY,
    SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY,
)


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = dict(state or {})


# --- teams_propose_send_message sources it from authoritative state --------


def test_target_display_name_populated_when_the_selected_chat_matches() -> None:
    ctx = _FakeToolContext(
        state={
            SELECTED_TEAMS_CHAT_ID_STATE_KEY: "c1",
            SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY: "SLOPANOC Gateway Group Test",
        }
    )
    result = teams_propose_send_message("c1", "test", tool_context=ctx)

    assert result["target_display_name"] == "SLOPANOC Gateway Group Test"
    stored = load_active_proposal(ctx.state)
    assert stored.target_display_name == "SLOPANOC Gateway Group Test"


def test_target_display_name_absent_when_no_chat_is_selected_yet() -> None:
    ctx = _FakeToolContext()  # no selected_teams_chat_id/_topic at all
    result = teams_propose_send_message("c1", "test", tool_context=ctx)

    assert "target_display_name" not in result
    stored = load_active_proposal(ctx.state)
    assert stored.target_display_name is None


def test_target_display_name_absent_when_the_selected_chat_is_a_different_one() -> None:
    """The user may be sending to a chat other than the one most recently
    discussed -- never show a mismatched chat's topic as if it were this
    proposal's destination.
    """
    ctx = _FakeToolContext(
        state={
            SELECTED_TEAMS_CHAT_ID_STATE_KEY: "some-other-chat",
            SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY: "Some Other Chat",
        }
    )
    result = teams_propose_send_message("c1", "test", tool_context=ctx)

    assert "target_display_name" not in result
    stored = load_active_proposal(ctx.state)
    assert stored.target_display_name is None


def test_target_display_name_never_invented_from_an_empty_topic() -> None:
    ctx = _FakeToolContext(
        state={SELECTED_TEAMS_CHAT_ID_STATE_KEY: "c1", SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY: ""}
    )
    result = teams_propose_send_message("c1", "test", tool_context=ctx)
    assert "target_display_name" not in result


# --- teams_propose_create_chat: unaffected, continues using `title` --------


def test_create_chat_proposal_never_has_a_target_display_name_field() -> None:
    ctx = _FakeToolContext(
        state={SELECTED_TEAMS_CHAT_ID_STATE_KEY: "c1", SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY: "Unrelated Chat"}
    )
    result = teams_propose_create_chat("Ops Bridge", ["a@example.com", "b@example.com"], tool_context=ctx)

    assert "target_display_name" not in result
    stored = load_active_proposal(ctx.state)
    assert stored.target_display_name is None
    # createChat's own display identity remains `title`, unchanged.
    assert result["title"] == "Ops Bridge"


# --- Presentation metadata never affects hashing/execution -----------------


def test_target_display_name_has_zero_effect_on_the_payload_hash() -> None:
    payload = {"chatId": "c1", "message": "test"}
    with_name = create_action_proposal(
        "teams.sendMessage", payload, {}, target_display_name="SLOPANOC Gateway Group Test"
    )
    without_name = create_action_proposal("teams.sendMessage", payload, {}, target_display_name=None)

    assert with_name.payload_hash == without_name.payload_hash == compute_payload_hash("teams.sendMessage", payload)


def test_target_display_name_is_not_part_of_payload() -> None:
    proposal = create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "test"}, {}, target_display_name="Some Topic"
    )
    assert "target_display_name" not in proposal.payload
    assert "targetDisplayName" not in proposal.payload


# --- map_pending_action carries it through to the frontend-facing DTO ------


def test_map_pending_action_includes_the_stored_target_display_name() -> None:
    state: dict[str, Any] = {}
    create_action_proposal(
        "teams.sendMessage",
        {"chatId": "c1", "message": "test"},
        state,
        target_display_name="SLOPANOC Gateway Group Test",
    )
    dto = map_pending_action(state)
    assert dto is not None
    assert dto.target_display_name == "SLOPANOC Gateway Group Test"
    # The raw chat id is still available as its own, separate field --
    # never conflated with the display name.
    assert dto.chat_id == "c1"


def test_map_pending_action_target_display_name_is_none_when_not_captured() -> None:
    state: dict[str, Any] = {}
    create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "test"}, state)
    dto = map_pending_action(state)
    assert dto is not None
    assert dto.target_display_name is None


def test_pending_action_dto_has_the_new_field_as_optional_and_additive() -> None:
    from backend.api.schemas import PendingActionDTO

    assert "target_display_name" in PendingActionDTO.model_fields
    assert PendingActionDTO.model_fields["target_display_name"].is_required() is False
