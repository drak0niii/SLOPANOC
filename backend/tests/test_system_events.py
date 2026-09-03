"""Unit tests for backend.tools.teams.system_events -- deterministic
detection of Teams system/event messages. Pure functions, no mocking
needed.
"""
from __future__ import annotations

from backend.tools.teams.system_events import (
    is_excludable_from_reasoning,
    is_system_event_content,
)


def test_system_event_self_closing_tag_is_detected() -> None:
    assert is_system_event_content("<systemEventMessage/>") is True


def test_system_event_tag_with_attributes_is_detected() -> None:
    assert is_system_event_content('<systemEventMessage eventType="memberAdded"/>') is True


def test_system_event_detection_is_case_insensitive() -> None:
    assert is_system_event_content("<SYSTEMEVENTMESSAGE/>") is True


def test_normal_html_is_not_a_system_event() -> None:
    assert is_system_event_content("<p>hello</p>") is False


def test_empty_raw_content_is_not_a_system_event_marker() -> None:
    assert is_system_event_content("") is False


def test_excludable_for_system_event_marker() -> None:
    assert is_excludable_from_reasoning("<systemEventMessage/>", "") is True


def test_excludable_for_empty_normalized_text() -> None:
    assert is_excludable_from_reasoning("", "") is True
    assert is_excludable_from_reasoning("   ", "") is True


def test_not_excludable_with_meaningful_normalized_text() -> None:
    assert is_excludable_from_reasoning("<p>Real update</p>", "Real update") is False


def test_not_excludable_for_attachment_only_message() -> None:
    """An attachment-only message normalizes to "[Attachment]" -- real,
    meaningful content, even though the sender or raw HTML looks sparse.
    """
    assert is_excludable_from_reasoning('<attachment id="a1"></attachment>', "[Attachment]") is False
