"""Unit tests for `ResolvedReadContinuation` and its storage/consumption
helpers (selection/schemas.py, selection/service.py) -- production
hardening pass. ADK-independent: exercises only plain data/state-dict
operations, no Runner, no tool_context.
"""
from __future__ import annotations

from backend.selection.schemas import PendingReadIntent, ReadOperation, ResolvedReadContinuation
from backend.selection.service import (
    PENDING_READ_CONTINUATION_STATE_KEY,
    pop_read_continuation,
    store_read_continuation,
)


def test_resolved_read_continuation_defaults_conversation_target_to_selected_external() -> None:
    continuation = ResolvedReadContinuation(selected_chat_id="chat-1", selected_chat_topic="Chat One")
    assert continuation.conversation_target == "selected_external_conversation"


def test_store_then_pop_round_trips_all_fields() -> None:
    state: dict = {}
    continuation = ResolvedReadContinuation(
        operation=ReadOperation.GET_MESSAGES,
        selected_chat_id="chat-42",
        selected_chat_topic="SLOPANOC Gateway Group Test",
        question="who is on antibiotics",
        requested_time_range="the last 7 days",
    )
    store_read_continuation(state, continuation)
    assert PENDING_READ_CONTINUATION_STATE_KEY in state

    popped = pop_read_continuation(state)
    assert popped == continuation


def test_pop_removes_the_key_single_use() -> None:
    state: dict = {}
    store_read_continuation(
        state, ResolvedReadContinuation(selected_chat_id="chat-1", selected_chat_topic="Chat One")
    )
    first = pop_read_continuation(state)
    assert first is not None
    second = pop_read_continuation(state)
    assert second is None
    assert PENDING_READ_CONTINUATION_STATE_KEY not in state


def test_pop_with_no_stored_continuation_returns_none() -> None:
    assert pop_read_continuation({}) is None


def test_pop_tolerates_a_malformed_stored_value() -> None:
    # Mirrors `load_active_selection`'s own safe-default tolerance -- a
    # corrupted/legacy value is treated as "no continuation", never raised.
    state = {PENDING_READ_CONTINUATION_STATE_KEY: {"selected_chat_id": 123}}
    assert pop_read_continuation(state) is None


def test_pop_tolerates_a_non_dict_stored_value() -> None:
    assert pop_read_continuation({PENDING_READ_CONTINUATION_STATE_KEY: "not-a-dict"}) is None


def test_operation_field_carries_the_pending_read_intents_own_enum() -> None:
    intent = PendingReadIntent(operation=ReadOperation.GET_MESSAGES, question=None, requested_time_range=None)
    continuation = ResolvedReadContinuation(
        operation=intent.operation,
        selected_chat_id="chat-1",
        selected_chat_topic="Chat One",
        question=intent.question,
        requested_time_range=intent.requested_time_range,
    )
    assert continuation.operation == ReadOperation.GET_MESSAGES
