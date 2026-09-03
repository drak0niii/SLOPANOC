"""Unit tests for `enforce_read_continuation` (team_manager's
`before_tool_callback`, production hardening pass) and its
stash/discard lifecycle -- exercised directly, without any ADK Runner,
mirroring how `test_conversation_target_tool.py`/`state_sync.py`'s own
tests call ADK callbacks directly with duck-typed `tool`/`tool_context`
stand-ins.
"""
from __future__ import annotations

import inspect

from backend.agents.team_manager import read_continuation_enforcement as rce_module
from backend.agents.team_manager.read_continuation_enforcement import (
    discard_active_read_continuation,
    enforce_read_continuation,
    stash_active_read_continuation,
)
from backend.selection.read_resume import GENERIC_SUMMARY_RESUME_TEXT
from backend.selection.schemas import ReadOperation, ResolvedReadContinuation


class _FakeSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id


class _FakeToolContext:
    def __init__(self, session_id: str, state: dict) -> None:
        self.session = _FakeSession(session_id)
        self.state = state


class _IncidentManagerTool:
    name = "incident_manager"


class _OtherTool:
    name = "record_conversation_target"


def _continuation(**overrides) -> ResolvedReadContinuation:
    defaults = dict(
        operation=ReadOperation.SUMMARIZE,
        selected_chat_id="chat-real-1",
        selected_chat_topic="SLOPANOC Gateway Group Test",
        question=None,
        requested_time_range=None,
    )
    defaults.update(overrides)
    return ResolvedReadContinuation(**defaults)


def teardown_function(_fn) -> None:
    # `_ACTIVE_CONTINUATIONS` is module-level, in-process state shared
    # across tests -- discard defensively after every test so an
    # assertion failure in one test can never leak a stash into another.
    discard_active_read_continuation("sess-1")
    discard_active_read_continuation("sess-2")


def test_no_op_when_no_continuation_was_stashed_for_this_session() -> None:
    args: dict = {"chat_topic": "whatever the model chose", "question": None, "requested_time_range": None}
    ctx = _FakeToolContext("sess-1", {})
    result = enforce_read_continuation(_IncidentManagerTool(), args, ctx)
    assert result is None
    assert args["chat_topic"] == "whatever the model chose"  # untouched
    assert "temp:resolved_chat_id" not in ctx.state


def test_no_op_for_a_different_tool_even_with_an_active_continuation() -> None:
    stash_active_read_continuation("sess-1", _continuation())
    args: dict = {"target": "selected_external_conversation"}
    ctx = _FakeToolContext("sess-1", {})
    result = enforce_read_continuation(_OtherTool(), args, ctx)
    assert result is None
    assert args == {"target": "selected_external_conversation"}  # untouched
    # Not consumed -- still available for the actual incident_manager call.
    assert "sess-1" in rce_module._ACTIVE_CONTINUATIONS


def test_overrides_chat_topic_question_and_time_range_from_the_continuation() -> None:
    stash_active_read_continuation(
        "sess-1",
        _continuation(
            operation=ReadOperation.SUMMARIZE,
            question="decisions",
            requested_time_range="the last 7 days",
        ),
    )
    args: dict = {
        "chat_topic": "some stale model-chosen name",
        "question": "some stale model-chosen question",
        "requested_time_range": None,
    }
    ctx = _FakeToolContext("sess-1", {})
    result = enforce_read_continuation(_IncidentManagerTool(), args, ctx)

    assert result is None  # never short-circuits the real tool call
    assert args["chat_topic"] == "SLOPANOC Gateway Group Test"
    assert args["question"] == "decisions"
    assert args["requested_time_range"] == "the last 7 days"
    assert ctx.state["temp:resolved_chat_id"] == "chat-real-1"
    assert ctx.state["temp:resolved_chat_id"].startswith("temp:") is False  # sanity: value, not the key


def test_falls_back_to_the_generic_operation_phrase_when_no_specific_question() -> None:
    stash_active_read_continuation("sess-1", _continuation(question=None))
    args: dict = {"chat_topic": "x", "question": "model's own guess", "requested_time_range": None}
    ctx = _FakeToolContext("sess-1", {})
    enforce_read_continuation(_IncidentManagerTool(), args, ctx)
    assert args["question"] == GENERIC_SUMMARY_RESUME_TEXT


def test_single_use_second_incident_manager_call_same_turn_is_unaffected() -> None:
    stash_active_read_continuation("sess-1", _continuation(selected_chat_topic="Chat One"))
    ctx = _FakeToolContext("sess-1", {})

    first_args: dict = {"chat_topic": "irrelevant", "question": None, "requested_time_range": None}
    enforce_read_continuation(_IncidentManagerTool(), first_args, ctx)
    assert first_args["chat_topic"] == "Chat One"

    second_args: dict = {"chat_topic": "a genuinely new chat name", "question": None, "requested_time_range": None}
    enforce_read_continuation(_IncidentManagerTool(), second_args, ctx)
    assert second_args["chat_topic"] == "a genuinely new chat name"  # untouched -- already consumed


def test_stash_is_scoped_per_session_id() -> None:
    stash_active_read_continuation("sess-1", _continuation(selected_chat_topic="Session One Chat"))
    ctx_other_session = _FakeToolContext("sess-2", {})
    args: dict = {"chat_topic": "whatever", "question": None, "requested_time_range": None}
    enforce_read_continuation(_IncidentManagerTool(), args, ctx_other_session)
    assert args["chat_topic"] == "whatever"  # sess-2 has no stash of its own

    ctx_sess1 = _FakeToolContext("sess-1", {})
    args2: dict = {"chat_topic": "whatever", "question": None, "requested_time_range": None}
    enforce_read_continuation(_IncidentManagerTool(), args2, ctx_sess1)
    assert args2["chat_topic"] == "Session One Chat"


def test_discard_removes_an_unconsumed_stash_and_is_safe_when_nothing_was_stashed() -> None:
    stash_active_read_continuation("sess-1", _continuation())
    discard_active_read_continuation("sess-1")
    args: dict = {"chat_topic": "x", "question": None, "requested_time_range": None}
    ctx = _FakeToolContext("sess-1", {})
    enforce_read_continuation(_IncidentManagerTool(), args, ctx)
    assert args["chat_topic"] == "x"  # nothing to apply -- already discarded

    discard_active_read_continuation("a-session-nothing-was-ever-stashed-for")  # must not raise


def test_no_natural_language_intent_routing_in_the_enforcement_module() -> None:
    """Structural guard (instruction requirement): the deterministic
    override logic must never fall back to inspecting free text for
    keywords/phrases -- only structured `ResolvedReadContinuation` fields
    drive the override.
    """
    import backend.agents.team_manager.read_continuation_enforcement as module

    source = inspect.getsource(module)
    forbidden = ["message.lower(", "text.lower(", ".startswith(", "re.search(", "re.match(", "if 'summar"]
    for token in forbidden:
        assert token not in source, f"unexpected NL-routing token in enforcement module: {token!r}"
