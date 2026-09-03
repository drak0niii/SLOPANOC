"""Unit tests for the teams_list_chats tool -- deterministic chat
discovery/matching. The Power Automate call itself is mocked via
requests.post; matching logic (exact / case-insensitive / ambiguous /
not_found) is exercised directly on the tool's return value.

The mocked gateway response defaults to a bare top-level JSON array (the
live gateway's proven shape, see gateway/power_automate_client.py). See
test_shape_* below for the still-supported wrapped shapes and malformed
responses specifically.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.gateway import power_automate_client as pac_module
from backend.selection.service import load_active_selection
from backend.tests._fakes import FakeResponse, chat
from backend.tools.teams.list_chats import teams_list_chats


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = dict(state or {})


def _mock_chats(monkeypatch: pytest.MonkeyPatch, chats: list[dict]) -> None:
    """Mock the gateway returning `chats` as a bare top-level JSON array --
    the live gateway's proven response shape for `teams.listChats`.
    """
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, chats))


def test_exact_chat_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_chats(
        monkeypatch,
        [chat("c1", "SLOPANOC Gateway Group Test"), chat("c2", "Unrelated Chat")],
    )

    result = teams_list_chats(topic="SLOPANOC Gateway Group Test")

    assert result["match"] == "matched"
    assert result["matched_chat"]["chat_id"] == "c1"
    assert result["candidates"] == []


def test_case_insensitive_chat_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_chats(monkeypatch, [chat("c1", "SLOPANOC Gateway Group Test")])

    result = teams_list_chats(topic="slopanoc gateway group test")

    assert result["match"] == "matched"
    assert result["matched_chat"]["chat_id"] == "c1"


def test_chat_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_chats(monkeypatch, [chat("c1", "Some Other Chat")])

    result = teams_list_chats(topic="Does Not Exist")

    assert result["match"] == "not_found"
    assert result["matched_chat"] is None
    assert result["candidates"] == []


def test_never_invents_chat_id_for_a_partial_title_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule #7 of CHAT DISCOVERY: never invent a chatId. A title that only
    partially overlaps the requested topic must not be treated as a match
    -- it must come back not_found, not a guessed chat_id.
    """
    _mock_chats(monkeypatch, [chat("c1", "SLOPANOC Gateway Group Test - Archived")])

    result = teams_list_chats(topic="SLOPANOC Gateway Group Test")

    assert result["match"] == "not_found"
    assert result["matched_chat"] is None


def test_ambiguous_chat_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_chats(monkeypatch, [chat("c1", "Ops Bridge"), chat("c2", "Ops Bridge")])

    result = teams_list_chats(topic="Ops Bridge")

    assert result["match"] == "ambiguous"
    assert result["matched_chat"] is None
    candidate_ids = {c["chat_id"] for c in result["candidates"]}
    assert candidate_ids == {"c1", "c2"}


def test_power_automate_gateway_error_is_reported_as_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(500))

    result = teams_list_chats(topic="Anything")

    assert "error" in result
    assert result["error"]["errorCode"] == "run_failure"
    assert "chats" not in result


def test_blank_topic_is_a_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    result = teams_list_chats(topic="   ")

    assert result["error"]["errorCode"] == "validation_error"


def test_raw_top_level_array_is_the_accepted_success_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is the live gateway's actual, proven response shape for
    `teams.listChats`: a bare JSON array, each item using `id`/`topic`/
    `lastUpdatedDateTime` -- not a `{"chats": [...]}` envelope.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                {
                    "id": "c1",
                    "topic": "SLOPANOC Gateway Group Test",
                    "createdDateTime": "2026-08-01T00:00:00Z",
                    "lastUpdatedDateTime": "2026-08-30T12:00:00Z",
                }
            ],
        ),
    )

    result = teams_list_chats(topic="SLOPANOC Gateway Group Test")

    assert result["match"] == "matched"
    assert result["matched_chat"] == {
        "chat_id": "c1",
        "title": "SLOPANOC Gateway Group Test",
        "participant_count": None,
        "last_activity_at": "2026-08-30T12:00:00Z",
    }


def test_legacy_wrapped_chats_envelope_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward/backward compatibility: a `{"chats": [...]}` envelope
    (the shape an earlier, unverified draft of this tool assumed) is
    still accepted if a gateway ever sends it.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"chats": [chat("c1", "Ops Bridge")]}),
    )

    result = teams_list_chats(topic="Ops Bridge")

    assert result["match"] == "matched"
    assert result["matched_chat"]["chat_id"] == "c1"


def test_success_data_envelope_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward compatibility: a generic `{"success": true, "data": [...]}`
    envelope is accepted if a gateway ever sends one.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, {"success": True, "data": [chat("c1", "Ops Bridge")]}
        ),
    )

    result = teams_list_chats(topic="Ops Bridge")

    assert result["match"] == "matched"
    assert result["matched_chat"]["chat_id"] == "c1"


def test_success_false_envelope_is_a_run_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"success": False, "data": []}),
    )

    result = teams_list_chats(topic="Anything")

    assert result["error"]["errorCode"] == "run_failure"


def test_malformed_response_shape_is_an_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither a bare array nor a recognized envelope -- must fail closed
    with a SafeError, never be silently treated as zero chats.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"unexpected": "shape"}),
    )

    result = teams_list_chats(topic="Anything")

    assert result["error"]["errorCode"] == "internal_error"


def test_malformed_item_within_a_valid_array_is_an_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shape-level validation passing must not weaken per-item validation:
    an entry missing the required `id` field still fails closed.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [{"topic": "No id field"}]),
    )

    result = teams_list_chats(topic="No id field")

    assert result["error"]["errorCode"] == "internal_error"


# --- Similar-candidate resolution + PendingSelection (interaction-capability extension) --


def test_no_exact_match_with_similar_candidates_creates_a_pending_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_chats(
        monkeypatch,
        [
            chat("c1", "Project Falcon Room Test"),
            chat("c2", "Project Falcon Test"),
            chat("c3", "Project Falcon Operations"),
        ],
    )
    ctx = _FakeToolContext()

    result = teams_list_chats(topic="Project Falcon Room", tool_context=ctx)

    assert result["match"] == "not_found"
    assert result["matched_chat"] is None
    assert result["selection_pending"] is True
    assert [c["title"] for c in result["similar_candidates"]][0] == "Project Falcon Room Test"

    selection = load_active_selection(ctx.state)
    assert selection is not None
    assert selection.requested_value == "Project Falcon Room"
    assert {opt.label for opt in selection.options} == {
        "Project Falcon Room Test",
        "Project Falcon Test",
        "Project Falcon Operations",
    }


def test_no_similar_candidates_never_creates_a_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_chats(monkeypatch, [chat("c1", "Something Else Entirely")])
    ctx = _FakeToolContext()

    result = teams_list_chats(topic="Project Falcon Room", tool_context=ctx)

    assert result["match"] == "not_found"
    assert result["selection_pending"] is False
    assert result["similar_candidates"] == []
    assert load_active_selection(ctx.state) is None


def test_selection_options_never_expose_the_raw_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_chats(
        monkeypatch,
        [chat("chat-real-internal-id", "Project Falcon Room Test")],
    )
    ctx = _FakeToolContext()

    teams_list_chats(topic="Project Falcon Room", tool_context=ctx)

    selection = load_active_selection(ctx.state)
    option_ids = {opt.option_id for opt in selection.options}
    assert "chat-real-internal-id" not in option_ids
    # The real id only ever lives in the internal-only target map.
    assert selection.option_targets[selection.options[0].option_id]["chat_id"] == "chat-real-internal-id"


def test_no_tool_context_never_creates_a_selection_even_with_similar_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller that never supplies `tool_context` (e.g. a manual/offline
    invocation) cannot cause a selection to be created -- there is no
    session state to write to, so this must degrade safely, not raise.
    """
    _mock_chats(monkeypatch, [chat("c1", "Project Falcon Room Test")])

    result = teams_list_chats(topic="Project Falcon Room")  # tool_context omitted entirely

    assert result["match"] == "not_found"
    assert result["selection_pending"] is False
    assert "error" not in result


def test_pending_write_message_is_threaded_into_the_created_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_chats(monkeypatch, [chat("c1", "Project Falcon Room Test")])
    ctx = _FakeToolContext()

    teams_list_chats(topic="Project Falcon Room", pending_write_message="that this is a test", tool_context=ctx)

    selection = load_active_selection(ctx.state)
    assert selection.pending_write_message == "that this is a test"


def test_selection_pending_write_message_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_chats(monkeypatch, [chat("c1", "Project Falcon Room Test")])
    ctx = _FakeToolContext()

    teams_list_chats(topic="Project Falcon Room", tool_context=ctx)

    selection = load_active_selection(ctx.state)
    assert selection.pending_write_message is None


def test_pending_question_and_time_range_are_threaded_into_the_created_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_chats(monkeypatch, [chat("c1", "Project Falcon Room Test")])
    ctx = _FakeToolContext()

    teams_list_chats(
        topic="Project Falcon Room",
        pending_question="What are the open action items?",
        pending_time_range="the last 7 days",
        tool_context=ctx,
    )

    selection = load_active_selection(ctx.state)
    assert selection.pending_read_intent is not None
    assert selection.pending_read_intent.question == "What are the open action items?"
    assert selection.pending_read_intent.requested_time_range == "the last 7 days"
    assert selection.pending_write_message is None


# --- Live-traced bug: `pending_question` restating the ambiguous topic ----


def test_pending_question_restating_the_ambiguous_topic_is_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root cause of the live read-resume bug: `question` set to "the
    user's original request" (team_manager's own historical instruction
    for a first question about a chat) still names the very chat that
    turns out to be ambiguous. If replayed verbatim as the resume text,
    it would reopen the exact ambiguity the user just resolved (see
    list_chats.py's `_safe_pending_question`). The captured intent must
    fall back to an empty (but still non-None) `PendingReadIntent`.
    """
    _mock_chats(monkeypatch, [chat("c1", "Knowledge Management Daily Sync up")])
    ctx = _FakeToolContext()

    teams_list_chats(
        topic="Knowledge Management Daily",
        pending_question="now i need you to sum up this chat room Knowledge Management Daily",
        tool_context=ctx,
    )

    selection = load_active_selection(ctx.state)
    assert selection.pending_read_intent is not None
    assert selection.pending_read_intent.question is None


def test_pending_question_restating_the_topic_case_insensitively_is_also_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_chats(monkeypatch, [chat("c1", "Knowledge Management Daily Sync up")])
    ctx = _FakeToolContext()

    teams_list_chats(
        topic="knowledge management daily",
        pending_question="Please summarize KNOWLEDGE MANAGEMENT DAILY for me",
        tool_context=ctx,
    )

    selection = load_active_selection(ctx.state)
    assert selection.pending_read_intent.question is None


def test_a_genuinely_distinct_sub_question_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """The safety net only discards a question that restates the SAME
    ambiguous topic -- a real, distinguishable sub-question about an
    unrelated chat is never touched (never over-broad)."""
    _mock_chats(monkeypatch, [chat("c1", "Ops Bridge Test")])
    ctx = _FakeToolContext()

    teams_list_chats(
        topic="Ops Bridge",
        pending_question="What are the open action items?",
        tool_context=ctx,
    )

    selection = load_active_selection(ctx.state)
    assert selection.pending_read_intent.question == "What are the open action items?"


# --- pending_operation (pre-4H hardening pass, item 1) ----------------------


def test_pending_operation_is_threaded_into_the_created_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_chats(monkeypatch, [chat("c1", "Ops Bridge Test")])
    ctx = _FakeToolContext()

    teams_list_chats(topic="Ops Bridge", pending_operation="get_messages", tool_context=ctx)

    selection = load_active_selection(ctx.state)
    assert selection.pending_read_intent.operation == "get_messages"


def test_pending_operation_defaults_to_summarize_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_chats(monkeypatch, [chat("c1", "Ops Bridge Test")])
    ctx = _FakeToolContext()

    teams_list_chats(topic="Ops Bridge", tool_context=ctx)

    selection = load_active_selection(ctx.state)
    assert selection.pending_read_intent.operation == "summarize"


def test_pending_operation_defaults_to_summarize_for_an_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never raises on a malformed/unexpected value -- safe-defaults
    exactly like every other closed-enum coercion in this codebase."""
    _mock_chats(monkeypatch, [chat("c1", "Ops Bridge Test")])
    ctx = _FakeToolContext()

    teams_list_chats(topic="Ops Bridge", pending_operation="delete_everything", tool_context=ctx)

    selection = load_active_selection(ctx.state)
    assert selection.pending_read_intent.operation == "summarize"


def test_operation_survives_independently_even_when_the_focus_question_must_be_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core fix for item 1: `operation` is a structurally separate
    field from `question` -- when `question` still names the ambiguous
    topic and must be discarded by `_safe_pending_question`, `operation`
    is completely unaffected, since it was never derived from that text
    at all.
    """
    _mock_chats(monkeypatch, [chat("c1", "Knowledge Management Daily Sync up")])
    ctx = _FakeToolContext()

    teams_list_chats(
        topic="Knowledge Management Daily",
        pending_question="now i need you to sum up this chat room Knowledge Management Daily",
        pending_operation="summarize",
        tool_context=ctx,
    )

    selection = load_active_selection(ctx.state)
    assert selection.pending_read_intent.question is None  # discarded -- still names the topic
    assert selection.pending_read_intent.operation == "summarize"  # untouched


def test_a_focus_question_without_the_topic_survives_alongside_its_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 1's positive case: once the base operation is carried
    separately, `pending_question` only ever needs to carry the FOCUS
    detail beyond it (e.g. "decisions and open actions") -- destination-
    free by construction, per the strengthened prompt contract -- and
    that focus text survives fully, right alongside the operation.
    """
    _mock_chats(monkeypatch, [chat("c1", "Knowledge Management Daily Sync up")])
    ctx = _FakeToolContext()

    teams_list_chats(
        topic="Knowledge Management Daily",
        pending_question="decisions and open actions",
        pending_operation="summarize",
        tool_context=ctx,
    )

    selection = load_active_selection(ctx.state)
    assert selection.pending_read_intent.question == "decisions and open actions"
    assert selection.pending_read_intent.operation == "summarize"


def test_a_read_lookup_with_no_specific_question_still_records_an_empty_read_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain "just summarize" request (no `question`/`requested_time_range`
    at all) must still be distinguishable from a write ambiguity -- an
    empty, non-None PendingReadIntent is the correct signal for "this was
    a read," used by selection_service.choose() to decide whether to
    compute a resume_message at all."""
    _mock_chats(monkeypatch, [chat("c1", "Project Falcon Room Test")])
    ctx = _FakeToolContext()

    teams_list_chats(topic="Project Falcon Room", tool_context=ctx)

    selection = load_active_selection(ctx.state)
    assert selection.pending_read_intent is not None
    assert selection.pending_read_intent.question is None
    assert selection.pending_read_intent.requested_time_range is None


def test_pending_question_is_never_recorded_when_pending_write_message_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A given ambiguity is either a write or a read, never both --
    passing both (a defensive/misuse case) must still resolve to the
    write path taking precedence, never a contradictory selection that
    looks like both at once."""
    _mock_chats(monkeypatch, [chat("c1", "Project Falcon Room Test")])
    ctx = _FakeToolContext()

    teams_list_chats(
        topic="Project Falcon Room",
        pending_write_message="hi",
        pending_question="What are the open action items?",
        tool_context=ctx,
    )

    selection = load_active_selection(ctx.state)
    assert selection.pending_write_message == "hi"
    assert selection.pending_read_intent is None


def test_a_new_ambiguous_lookup_supersedes_an_earlier_pending_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _FakeToolContext()
    _mock_chats(monkeypatch, [chat("c1", "Project Falcon Room Test")])
    teams_list_chats(topic="Project Falcon Room", tool_context=ctx)
    first = load_active_selection(ctx.state)

    _mock_chats(monkeypatch, [chat("c2", "Another Ambiguous Test")])
    teams_list_chats(topic="Another Ambiguous", tool_context=ctx)
    second = load_active_selection(ctx.state)

    assert second.selection_id != first.selection_id
    assert second.requested_value == "Another Ambiguous"


def test_an_exact_match_supersedes_an_earlier_pending_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instruction section 29 (manual correction): the user typing an
    exact alternative chat name while an earlier disambiguation is still
    open must invalidate that earlier selection -- otherwise it would
    remain resolvable indefinitely even though the conversation moved on.
    """
    ctx = _FakeToolContext()
    _mock_chats(monkeypatch, [chat("c1", "Project Falcon Room Test"), chat("c2", "Project Falcon Room Ops")])
    teams_list_chats(topic="Project Falcon Room", tool_context=ctx)
    pending = load_active_selection(ctx.state)
    assert pending.status == "pending"

    _mock_chats(monkeypatch, [chat("c3", "Project Falcon Room Test")])
    result = teams_list_chats(topic="Project Falcon Room Test", tool_context=ctx)

    assert result["match"] == "matched"
    superseded = load_active_selection(ctx.state)
    assert superseded.selection_id == pending.selection_id
    assert superseded.status == "superseded"


def test_an_exact_match_without_a_prior_selection_is_a_safe_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _FakeToolContext()
    _mock_chats(monkeypatch, [chat("c1", "Project Falcon Room Test")])

    result = teams_list_chats(topic="Project Falcon Room Test", tool_context=ctx)

    assert result["match"] == "matched"
    assert load_active_selection(ctx.state) is None


def test_an_exact_match_with_no_tool_context_never_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_chats(monkeypatch, [chat("c1", "Project Falcon Room Test")])

    result = teams_list_chats(topic="Project Falcon Room Test")

    assert result["match"] == "matched"
