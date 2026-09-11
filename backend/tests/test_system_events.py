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


# --- Teams Rich Content milestone (single-image scope): has_hosted_content ----


def test_default_has_hosted_content_false_preserves_exact_existing_behavior() -> None:
    """Byte-for-byte non-regression: omitting `has_hosted_content` behaves
    identically to before this parameter existed.
    """
    assert is_excludable_from_reasoning("", "") is True
    assert is_excludable_from_reasoning("<systemEventMessage/>", "") is True
    assert is_excludable_from_reasoning("<p>Real update</p>", "Real update") is False


def test_image_only_message_with_hosted_content_is_not_excludable_despite_empty_text() -> None:
    """An `<img>` tag (a real inline Teams image) produces NO text via
    `html_text.normalize_teams_content` -- unlike `<attachment>`, which
    becomes "[Attachment]". Without `has_hosted_content=True`, this would
    be wrongly excluded as content-free -- proven by the immediately
    preceding "empty normalized text" behavior still holding when
    `has_hosted_content` is left at its default `False`.
    """
    raw = '<p><img src="https://graph.microsoft.com/beta/.../messages/m1/hostedContents/ABC/$value"></p>'
    assert is_excludable_from_reasoning(raw, "", has_hosted_content=False) is True
    assert is_excludable_from_reasoning(raw, "", has_hosted_content=True) is False


def test_system_event_marker_excluded_even_with_hosted_content_true() -> None:
    """`has_hosted_content=True` must NEVER override genuine system/event
    exclusion -- rich content can never smuggle a system/event entry into
    evidence (instruction: system-event non-regression is absolute)."""
    assert is_excludable_from_reasoning("<systemEventMessage/>", "", has_hosted_content=True) is True
