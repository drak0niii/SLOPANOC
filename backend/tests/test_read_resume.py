"""Tests for backend/selection/read_resume.py -- the deterministic,
destination-free resume-text builder for a resolved READ Teams selection
(hardening pass: replaces the previous, buggy "resend the user's original
raw text" design -- see selection_service.py's module docstring).
"""
from __future__ import annotations

from backend.selection.read_resume import (
    GENERIC_GET_MESSAGES_RESUME_TEXT,
    GENERIC_SUMMARY_RESUME_TEXT,
    build_read_resume_message,
)
from backend.selection.schemas import PendingReadIntent, ReadOperation


def test_none_intent_falls_back_to_the_generic_summary_phrase() -> None:
    assert build_read_resume_message(None) == GENERIC_SUMMARY_RESUME_TEXT


def test_empty_intent_falls_back_to_the_generic_summary_phrase() -> None:
    assert build_read_resume_message(PendingReadIntent()) == GENERIC_SUMMARY_RESUME_TEXT


def test_a_stored_question_is_used_verbatim() -> None:
    intent = PendingReadIntent(question="What are the open action items?")
    assert build_read_resume_message(intent) == "What are the open action items?"


def test_a_blank_question_is_treated_the_same_as_no_question() -> None:
    intent = PendingReadIntent(question="   ")
    assert build_read_resume_message(intent) == GENERIC_SUMMARY_RESUME_TEXT


def test_time_range_is_appended_as_a_labeled_clause() -> None:
    intent = PendingReadIntent(requested_time_range="the last 7 days")
    result = build_read_resume_message(intent)
    assert result == f"{GENERIC_SUMMARY_RESUME_TEXT} (time range: the last 7 days)"


def test_question_and_time_range_both_present() -> None:
    intent = PendingReadIntent(question="Who is on antibiotics?", requested_time_range="today")
    assert build_read_resume_message(intent) == "Who is on antibiotics? (time range: today)"


def test_never_contains_a_destination_placeholder() -> None:
    """Structural guarantee: the function body itself never reads/writes
    anything resembling a chat name/topic/id -- it takes only
    `PendingReadIntent`, which has no destination field at all (checked
    against the function's own code, not the module's explanatory prose).
    """
    import inspect

    from backend.selection.read_resume import build_read_resume_message

    source = inspect.getsource(build_read_resume_message)
    for forbidden in ("chat_topic", "requested_value", "chat_id", ".topic"):
        assert forbidden not in source


def test_output_is_deterministic_for_the_same_input() -> None:
    intent = PendingReadIntent(question="Summarize the incident", requested_time_range="since Monday")
    assert build_read_resume_message(intent) == build_read_resume_message(intent)


# --- operation-based fallback (pre-4H hardening pass, item 1) ---------------


def test_get_messages_operation_uses_its_own_generic_phrase_with_no_question() -> None:
    intent = PendingReadIntent(operation=ReadOperation.GET_MESSAGES)
    assert build_read_resume_message(intent) == GENERIC_GET_MESSAGES_RESUME_TEXT
    assert GENERIC_GET_MESSAGES_RESUME_TEXT != GENERIC_SUMMARY_RESUME_TEXT


def test_summarize_operation_is_the_default_and_matches_prior_behavior() -> None:
    assert build_read_resume_message(PendingReadIntent()) == GENERIC_SUMMARY_RESUME_TEXT
    assert build_read_resume_message(PendingReadIntent(operation=ReadOperation.SUMMARIZE)) == GENERIC_SUMMARY_RESUME_TEXT


def test_a_present_question_still_takes_priority_over_the_operation_phrase() -> None:
    intent = PendingReadIntent(operation=ReadOperation.GET_MESSAGES, question="from the incident channel")
    assert build_read_resume_message(intent) == "from the incident channel"


def test_get_messages_operation_combines_correctly_with_a_time_range() -> None:
    intent = PendingReadIntent(operation=ReadOperation.GET_MESSAGES, requested_time_range="today")
    assert build_read_resume_message(intent) == f"{GENERIC_GET_MESSAGES_RESUME_TEXT} (time range: today)"


def test_operation_is_never_blended_with_a_discarded_question_via_text_surgery() -> None:
    """Structural guarantee, mirroring `test_never_contains_a_destination_
    placeholder`: the function never slices/concatenates fragments of
    `question` -- it only ever selects a WHOLE, pre-written phrase or uses
    `question` verbatim in full, never a partial/reconstructed string.
    """
    import inspect

    source = inspect.getsource(build_read_resume_message)
    for forbidden in ("re.sub", "re.compile", ".replace(", "question[", "question.split", "question.partition"):
        assert forbidden not in source
