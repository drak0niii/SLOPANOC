"""Unit tests for backend/agents/team_manager/conversation_target.py --
the deterministic, closed-set declaration tool for the semantic-scope bug
fix. Never performs any natural-language classification itself -- these
tests only exercise its validation/state-cross-check logic.
"""
from __future__ import annotations

from backend.agents.team_manager.conversation_target import ConversationTarget, record_conversation_target
from backend.tools.teams.state_keys import SELECTED_TEAMS_CHAT_ID_STATE_KEY


class _FakeToolContext:
    def __init__(self, state: dict) -> None:
        self.state = state


def test_current_thread_is_always_accepted() -> None:
    result = record_conversation_target(ConversationTarget.CURRENT_THREAD)
    assert result == {"target": "current_thread"}


def test_explicit_external_conversation_is_always_accepted() -> None:
    result = record_conversation_target(ConversationTarget.EXPLICIT_EXTERNAL_CONVERSATION)
    assert result == {"target": "explicit_external_conversation"}


def test_selected_external_conversation_is_accepted_when_a_chat_is_selected() -> None:
    ctx = _FakeToolContext({SELECTED_TEAMS_CHAT_ID_STATE_KEY: "chat-real-1"})
    result = record_conversation_target(ConversationTarget.SELECTED_EXTERNAL_CONVERSATION, tool_context=ctx)
    assert result == {"target": "selected_external_conversation"}


def test_selected_external_conversation_is_rejected_with_no_selection() -> None:
    ctx = _FakeToolContext({})
    result = record_conversation_target(ConversationTarget.SELECTED_EXTERNAL_CONVERSATION, tool_context=ctx)
    assert "error" in result
    assert result["error"]["errorCode"] == "validation_error"


def test_selected_external_conversation_is_rejected_with_no_tool_context_at_all() -> None:
    result = record_conversation_target(ConversationTarget.SELECTED_EXTERNAL_CONVERSATION)
    assert "error" in result


def test_an_unrecognized_target_is_rejected() -> None:
    result = record_conversation_target("teams_conversation")  # not one of the three real values
    assert "error" in result
    assert result["error"]["errorCode"] == "validation_error"


def test_empty_string_target_is_rejected() -> None:
    result = record_conversation_target("")
    assert "error" in result


def test_the_closed_set_has_exactly_three_values() -> None:
    values = {
        ConversationTarget.CURRENT_THREAD,
        ConversationTarget.SELECTED_EXTERNAL_CONVERSATION,
        ConversationTarget.EXPLICIT_EXTERNAL_CONVERSATION,
    }
    assert values == {"current_thread", "selected_external_conversation", "explicit_external_conversation"}
    assert len(values) == 3


def test_never_exposes_the_raw_chat_id_in_its_response() -> None:
    ctx = _FakeToolContext({SELECTED_TEAMS_CHAT_ID_STATE_KEY: "19:super-secret-chat-id@thread.v2"})
    result = record_conversation_target(ConversationTarget.SELECTED_EXTERNAL_CONVERSATION, tool_context=ctx)
    assert "19:super-secret-chat-id@thread.v2" not in str(result)
