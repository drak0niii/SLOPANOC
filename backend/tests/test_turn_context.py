"""Tests for backend/api/turn_context.py -- the in-process, never-
persisted mailbox that lets teams_get_messages forward the current turn's
retrieved message text out to chat_service.py (snippet-authenticity fix).
See that module's own docstring for why session state was ruled out.
"""
from __future__ import annotations

from backend.api.turn_context import (
    bind_run_id,
    current_run_id,
    pop_message_texts,
    record_message_texts,
    reset_run_id,
)


def test_current_run_id_is_none_outside_any_bound_turn() -> None:
    assert current_run_id() is None


def test_bind_and_reset_round_trip() -> None:
    assert current_run_id() is None
    token = bind_run_id("run-1")
    try:
        assert current_run_id() == "run-1"
    finally:
        reset_run_id(token)
    assert current_run_id() is None


def test_record_and_pop_round_trip() -> None:
    record_message_texts("run-a", {"m1": "Body one."})
    assert pop_message_texts("run-a") == {"m1": "Body one."}


def test_pop_returns_empty_dict_when_nothing_was_recorded() -> None:
    assert pop_message_texts("run-never-used") == {}


def test_pop_removes_the_entry_so_a_second_pop_is_empty() -> None:
    record_message_texts("run-b", {"m1": "Body."})
    first = pop_message_texts("run-b")
    second = pop_message_texts("run-b")
    assert first == {"m1": "Body."}
    assert second == {}


def test_record_accumulates_across_multiple_calls_for_the_same_run_id() -> None:
    record_message_texts("run-c", {"m1": "First."})
    record_message_texts("run-c", {"m2": "Second."})
    assert pop_message_texts("run-c") == {"m1": "First.", "m2": "Second."}


def test_record_is_a_safe_no_op_for_a_missing_run_id() -> None:
    record_message_texts(None, {"m1": "Body."})
    record_message_texts("", {"m1": "Body."})
    # Neither call should have created an entry under any key.
    assert pop_message_texts(None) == {}  # type: ignore[arg-type]
    assert pop_message_texts("") == {}


def test_record_is_a_safe_no_op_for_empty_texts() -> None:
    record_message_texts("run-d", {})
    assert pop_message_texts("run-d") == {}


def test_different_run_ids_are_fully_isolated() -> None:
    record_message_texts("run-e", {"m1": "Body e."})
    record_message_texts("run-f", {"m1": "Body f. (different chat, same message id)"})
    assert pop_message_texts("run-e") == {"m1": "Body e."}
    assert pop_message_texts("run-f") == {"m1": "Body f. (different chat, same message id)"}


def test_bind_run_id_makes_it_visible_to_a_simulated_downstream_reader() -> None:
    """Mirrors the real usage shape: chat_service.py binds, then
    (synchronously, deep in the same call chain) teams_get_messages reads
    it back via current_run_id() to know where to record."""
    token = bind_run_id("run-g")
    try:

        def simulated_tool_call() -> None:
            record_message_texts(current_run_id(), {"m1": "Retrieved body."})

        simulated_tool_call()
    finally:
        reset_run_id(token)

    assert pop_message_texts("run-g") == {"m1": "Retrieved body."}
