"""Tests for backend/selection/service.py -- the trusted-boundary
selection lifecycle (create/resolve/skip), mirroring
test_approval_service.py's own coverage shape for backend/approval/service.py.
"""
from __future__ import annotations

from backend.selection.schemas import PendingReadIntent, SelectionKind, SelectionStatus
from backend.selection.service import (
    SelectionDenialReason,
    create_pending_selection,
    load_active_selection,
    resolve_selection,
    skip_selection,
    supersede_active_selection,
)
from backend.tools.teams.schemas import ChatSummary


def _candidates() -> list[ChatSummary]:
    return [
        ChatSummary(chat_id="chat-real-1", title="Project Falcon Room Test"),
        ChatSummary(chat_id="chat-real-2", title="Project Falcon Test"),
        ChatSummary(chat_id="chat-real-3", title="Project Falcon Operations"),
    ]


def test_create_pending_selection_stores_it_as_the_active_one() -> None:
    state: dict = {}
    selection = create_pending_selection(
        SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state
    )

    loaded = load_active_selection(state)
    assert loaded is not None
    assert loaded.selection_id == selection.selection_id
    assert loaded.status == SelectionStatus.PENDING
    assert loaded.requested_value == "Project Falcon Room"


def test_options_are_safe_labels_only_never_the_raw_chat_id() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)

    labels = {opt.label for opt in selection.options}
    assert labels == {"Project Falcon Room Test", "Project Falcon Test", "Project Falcon Operations"}
    option_ids = {opt.option_id for opt in selection.options}
    # option_id is a fresh uuid, never one of the real chat ids.
    assert option_ids.isdisjoint({"chat-real-1", "chat-real-2", "chat-real-3"})


def test_option_targets_map_is_the_internal_authoritative_source() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)

    for option in selection.options:
        target = selection.option_targets[option.option_id]
        assert target["chat_id"] in {"chat-real-1", "chat-real-2", "chat-real-3"}
        assert target["topic"] == option.label


def test_a_second_selection_replaces_the_first_in_state() -> None:
    state: dict = {}
    first = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    second = create_pending_selection(SelectionKind.TEAMS_CHAT, "Another Ambiguous Name", _candidates(), state)

    loaded = load_active_selection(state)
    assert loaded.selection_id == second.selection_id
    assert loaded.selection_id != first.selection_id


def test_pending_write_message_is_preserved_when_given() -> None:
    state: dict = {}
    selection = create_pending_selection(
        SelectionKind.TEAMS_CHAT,
        "Project Falcon Room",
        _candidates(),
        state,
        pending_write_message="that this is a test",
    )
    assert selection.pending_write_message == "that this is a test"


def test_pending_write_message_is_none_for_a_read_intent() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    assert selection.pending_write_message is None


def test_pending_read_intent_is_preserved_when_given() -> None:
    state: dict = {}
    intent = PendingReadIntent(question="What are the open action items?", requested_time_range="today")
    selection = create_pending_selection(
        SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state, pending_read_intent=intent
    )
    assert selection.pending_read_intent == intent

    persisted = load_active_selection(state)
    assert persisted.pending_read_intent.question == "What are the open action items?"
    assert persisted.pending_read_intent.requested_time_range == "today"


def test_pending_read_intent_survives_serialization_with_no_question() -> None:
    """A plain "just summarize" read intent (no specific question) is a
    real, valid, non-None PendingReadIntent -- distinct from "no read
    intent was ever recorded at all" (None)."""
    state: dict = {}
    selection = create_pending_selection(
        SelectionKind.TEAMS_CHAT,
        "Project Falcon Room",
        _candidates(),
        state,
        pending_read_intent=PendingReadIntent(),
    )
    persisted = load_active_selection(state)
    assert persisted.pending_read_intent is not None
    assert persisted.pending_read_intent.question is None
    assert persisted.pending_read_intent.requested_time_range is None


def test_pending_read_intent_is_none_when_not_given() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    assert selection.pending_read_intent is None


def test_pending_write_message_and_pending_read_intent_are_independent_fields() -> None:
    """A write-kind selection can carry no read intent, and vice versa --
    this is enforced by the caller (list_chats.py), not by the schema
    itself, but the storage layer must not silently drop or conflate
    either.
    """
    state: dict = {}
    selection = create_pending_selection(
        SelectionKind.TEAMS_CHAT,
        "Project Falcon Room",
        _candidates(),
        state,
        pending_write_message="hi",
    )
    assert selection.pending_write_message == "hi"
    assert selection.pending_read_intent is None


# --- resolve_selection -------------------------------------------------


def test_resolve_selection_success_returns_the_authoritative_target() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    chosen_option = selection.options[1]

    result = resolve_selection(selection.selection_id, chosen_option.option_id, state)

    assert result.success is True
    assert result.target == {"chat_id": "chat-real-2", "topic": "Project Falcon Test"}
    assert result.selection.status == SelectionStatus.RESOLVED

    persisted = load_active_selection(state)
    assert persisted.status == SelectionStatus.RESOLVED


def test_resolve_selection_no_pending_selection_at_all() -> None:
    state: dict = {}
    result = resolve_selection("does-not-exist", "opt-1", state)
    assert result.success is False
    assert result.reason == SelectionDenialReason.NO_PENDING_SELECTION


def test_resolve_selection_wrong_selection_id_is_denied_and_mutates_nothing() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    option_id = selection.options[0].option_id

    result = resolve_selection("some-other-selection-id", option_id, state)

    assert result.success is False
    assert result.reason == SelectionDenialReason.SELECTION_ID_MISMATCH
    assert load_active_selection(state).status == SelectionStatus.PENDING


def test_resolve_selection_invalid_option_id_is_denied() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)

    result = resolve_selection(selection.selection_id, "not-a-real-option-id", state)

    assert result.success is False
    assert result.reason == SelectionDenialReason.OPTION_NOT_FOUND
    assert load_active_selection(state).status == SelectionStatus.PENDING


def test_resolve_selection_option_from_a_different_selection_is_rejected() -> None:
    state: dict = {}
    first = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    foreign_option_id = first.options[0].option_id
    # A second selection replaces the first as the active one.
    second = create_pending_selection(SelectionKind.TEAMS_CHAT, "Another Ambiguous Name", _candidates(), state)

    result = resolve_selection(second.selection_id, foreign_option_id, state)

    assert result.success is False
    assert result.reason == SelectionDenialReason.OPTION_NOT_FOUND


def test_resolve_an_already_resolved_selection_is_denied_reuse() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    option_id = selection.options[0].option_id
    first_attempt = resolve_selection(selection.selection_id, option_id, state)
    assert first_attempt.success is True

    second_attempt = resolve_selection(selection.selection_id, option_id, state)
    assert second_attempt.success is False
    assert second_attempt.reason == SelectionDenialReason.SELECTION_NOT_PENDING


# --- skip_selection ------------------------------------------------------


def test_skip_selection_success() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)

    result = skip_selection(selection.selection_id, state)

    assert result.success is True
    assert result.selection.status == SelectionStatus.SKIPPED
    assert load_active_selection(state).status == SelectionStatus.SKIPPED


def test_skip_never_populates_a_target() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    result = skip_selection(selection.selection_id, state)
    assert result.target is None


def test_skip_wrong_selection_id_is_denied() -> None:
    state: dict = {}
    create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    result = skip_selection("does-not-exist", state)
    assert result.success is False
    assert result.reason == SelectionDenialReason.SELECTION_ID_MISMATCH


# --- supersede_active_selection ------------------------------------------


def test_supersede_marks_a_pending_selection_superseded() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)

    superseded = supersede_active_selection(state)

    assert superseded is not None
    assert superseded.selection_id == selection.selection_id
    assert superseded.status == SelectionStatus.SUPERSEDED
    assert load_active_selection(state).status == SelectionStatus.SUPERSEDED


def test_supersede_is_a_no_op_when_no_selection_exists() -> None:
    state: dict = {}
    assert supersede_active_selection(state) is None
    assert load_active_selection(state) is None


def test_supersede_is_a_no_op_on_an_already_resolved_selection() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    resolve_selection(selection.selection_id, selection.options[0].option_id, state)

    assert supersede_active_selection(state) is None
    assert load_active_selection(state).status == SelectionStatus.RESOLVED


def test_superseded_selection_can_no_longer_be_resolved_or_skipped() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    supersede_active_selection(state)

    resolve_attempt = resolve_selection(selection.selection_id, selection.options[0].option_id, state)
    assert resolve_attempt.success is False
    assert resolve_attempt.reason == SelectionDenialReason.SELECTION_NOT_PENDING

    skip_attempt = skip_selection(selection.selection_id, state)
    assert skip_attempt.success is False
    assert skip_attempt.reason == SelectionDenialReason.SELECTION_NOT_PENDING


def test_a_new_ambiguous_lookup_after_supersede_still_replaces_it_normally() -> None:
    """`create_pending_selection` always replaces the active selection
    regardless of its status -- superseding an old one first must not
    interfere with that."""
    state: dict = {}
    old = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    supersede_active_selection(state)

    new = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)

    assert new.selection_id != old.selection_id
    assert load_active_selection(state).selection_id == new.selection_id


def test_skipping_an_already_skipped_selection_is_denied_reuse() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    skip_selection(selection.selection_id, state)

    second_attempt = skip_selection(selection.selection_id, state)
    assert second_attempt.success is False
    assert second_attempt.reason == SelectionDenialReason.SELECTION_NOT_PENDING


def test_skipping_a_resolved_selection_is_denied() -> None:
    state: dict = {}
    selection = create_pending_selection(SelectionKind.TEAMS_CHAT, "Project Falcon Room", _candidates(), state)
    resolve_selection(selection.selection_id, selection.options[0].option_id, state)

    result = skip_selection(selection.selection_id, state)
    assert result.success is False
    assert result.reason == SelectionDenialReason.SELECTION_NOT_PENDING
