"""Tests for the deterministic proposal-creation tools
(backend/tools/teams/propose_write.py) -- `teams_propose_create_chat` /
`teams_propose_send_message`.

`tool_context` is a plain fake with a `.state` dict, mirroring the
established pattern in test_teams_get_messages_message_references.py --
these tools only need a mutable mapping, never a real ADK object.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.approval.canonical import compute_payload_hash
from backend.approval.schemas import ProposalStatus, WriteOperation
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY, load_active_proposal
from backend.tools.teams.propose_write import teams_propose_create_chat, teams_propose_send_message


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = dict(state or {})


# --- createChat ----------------------------------------------------------


def test_valid_create_chat_proposal() -> None:
    ctx = _FakeToolContext()

    result = teams_propose_create_chat(
        "Ops Bridge", ["user1@example.com", "user2@example.com"], tool_context=ctx
    )

    assert "error" not in result
    assert result["operation"] == "teams.createChat"
    assert result["status"] == "pending"
    assert result["title"] == "Ops Bridge"
    assert result["members"] == ["user1@example.com", "user2@example.com"]
    assert "proposal_id" in result
    # Phase 4G: deliberately no expiry field in the model-facing result --
    # see propose_write.py's _proposal_info docstring.
    assert "expires_at" not in result


def test_create_chat_proposal_never_exposes_the_payload_hash() -> None:
    ctx = _FakeToolContext()
    result = teams_propose_create_chat(
        "Ops Bridge", ["user1@example.com", "user2@example.com"], tool_context=ctx
    )
    assert "payload_hash" not in result


def test_create_chat_proposal_is_stored_in_the_given_state() -> None:
    ctx = _FakeToolContext()
    result = teams_propose_create_chat(
        "Ops Bridge", ["user1@example.com", "user2@example.com"], tool_context=ctx
    )

    stored = load_active_proposal(ctx.state)
    assert stored is not None
    assert stored.proposal_id == result["proposal_id"]
    assert stored.status == ProposalStatus.PENDING


def test_create_chat_proposal_hash_matches_the_exact_normalized_payload() -> None:
    """The stored `payload_hash` must be computed from the NORMALIZED
    payload (trimmed title/members) -- not from the raw, possibly
    unnormalized arguments.
    """
    ctx = _FakeToolContext()
    teams_propose_create_chat(
        "  Ops Bridge  ", ["  user1@example.com ", "user2@example.com"], tool_context=ctx
    )

    stored = load_active_proposal(ctx.state)
    expected_hash = compute_payload_hash(
        WriteOperation.TEAMS_CREATE_CHAT.value,
        {"title": "Ops Bridge", "members": ["user1@example.com", "user2@example.com"]},
    )
    assert stored.payload_hash == expected_hash


def test_create_chat_proposal_rejects_empty_title_without_storing_anything() -> None:
    ctx = _FakeToolContext()

    result = teams_propose_create_chat("", ["user1@example.com", "user2@example.com"], tool_context=ctx)

    assert "error" in result
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in ctx.state


def test_create_chat_proposal_rejects_fewer_than_two_emails() -> None:
    ctx = _FakeToolContext()

    result = teams_propose_create_chat("Ops Bridge", ["user1@example.com"], tool_context=ctx)

    assert "error" in result
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in ctx.state


def test_create_chat_proposal_rejects_invalid_email() -> None:
    ctx = _FakeToolContext()

    result = teams_propose_create_chat(
        "Ops Bridge", ["user1@example.com", "not-an-email"], tool_context=ctx
    )

    assert "error" in result
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in ctx.state


def test_create_chat_proposal_rejects_duplicate_emails() -> None:
    ctx = _FakeToolContext()

    result = teams_propose_create_chat(
        "Ops Bridge", ["user1@example.com", "user1@example.com"], tool_context=ctx
    )

    assert "error" in result
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in ctx.state


def test_second_create_chat_proposal_replaces_the_first_in_state() -> None:
    ctx = _FakeToolContext()
    first = teams_propose_create_chat(
        "Ops Bridge", ["user1@example.com", "user2@example.com"], tool_context=ctx
    )
    second = teams_propose_create_chat(
        "Ops Bridge v2", ["user1@example.com", "user2@example.com"], tool_context=ctx
    )

    assert first["proposal_id"] != second["proposal_id"]
    stored = load_active_proposal(ctx.state)
    assert stored.proposal_id == second["proposal_id"]


# --- sendMessage -----------------------------------------------------------


def test_valid_send_message_proposal() -> None:
    ctx = _FakeToolContext()

    result = teams_propose_send_message("c1", "Hello team", tool_context=ctx)

    assert "error" not in result
    assert result["operation"] == "teams.sendMessage"
    assert result["status"] == "pending"
    assert result["chat_id"] == "c1"
    assert result["message"] == "Hello team"
    assert "proposal_id" in result


def test_send_message_proposal_never_exposes_the_payload_hash() -> None:
    ctx = _FakeToolContext()
    result = teams_propose_send_message("c1", "Hello team", tool_context=ctx)
    assert "payload_hash" not in result


def test_send_message_proposal_hash_matches_the_exact_normalized_payload() -> None:
    ctx = _FakeToolContext()
    teams_propose_send_message("  c1  ", "Hello team", tool_context=ctx)

    stored = load_active_proposal(ctx.state)
    expected_hash = compute_payload_hash(
        WriteOperation.TEAMS_SEND_MESSAGE.value, {"chatId": "c1", "message": "Hello team"}
    )
    assert stored.payload_hash == expected_hash


def test_send_message_proposal_rejects_empty_message_without_storing_anything() -> None:
    ctx = _FakeToolContext()

    result = teams_propose_send_message("c1", "", tool_context=ctx)

    assert "error" in result
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in ctx.state


def test_send_message_proposal_rejects_missing_chat_id() -> None:
    ctx = _FakeToolContext()

    result = teams_propose_send_message("", "Hello team", tool_context=ctx)

    assert "error" in result
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in ctx.state


def test_send_message_proposal_rejects_invalid_chat_id() -> None:
    ctx = _FakeToolContext()

    result = teams_propose_send_message("   ", "Hello team", tool_context=ctx)

    assert "error" in result
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in ctx.state


def test_a_create_chat_proposal_does_not_clobber_an_unrelated_state_key() -> None:
    ctx = _FakeToolContext(state={"selected_teams_chat_topic": "Ops Bridge"})
    teams_propose_create_chat("New Chat", ["user1@example.com", "user2@example.com"], tool_context=ctx)

    assert ctx.state["selected_teams_chat_topic"] == "Ops Bridge"
