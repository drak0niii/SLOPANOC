"""Unit tests for backend.tools.teams.html_text.normalize_teams_content --
deterministic Teams message HTML/text cleanup. No mocking needed: this is
a pure function.
"""
from __future__ import annotations

from backend.tools.teams.html_text import normalize_teams_content


def test_normal_html_paragraph() -> None:
    assert normalize_teams_content("<p>Hello world</p>") == "Hello world"


def test_multiple_paragraphs_become_separate_lines() -> None:
    assert normalize_teams_content("<p>First</p><p>Second</p>") == "First\nSecond"


def test_br_tag_becomes_a_line_break() -> None:
    assert normalize_teams_content("Line one<br>Line two") == "Line one\nLine two"


def test_div_tag_becomes_a_line_break() -> None:
    assert normalize_teams_content("<div>First</div><div>Second</div>") == "First\nSecond"


def test_html_entities_are_decoded() -> None:
    assert (
        normalize_teams_content("<p>A&amp;B &lt;tag&gt; C&nbsp;D</p>") == "A&B <tag> C D"
    )


def test_trailing_nbsp_does_not_leave_a_dangling_space() -> None:
    assert normalize_teams_content("<p>text&nbsp;</p>") == "text"


def test_emoji_element_preserves_alt_value() -> None:
    assert (
        normalize_teams_content('<p>Great job <emoji alt="🎉"></emoji>!</p>')
        == "Great job 🎉!"
    )


def test_self_closing_emoji_element_preserves_alt_value() -> None:
    assert normalize_teams_content('<p><emoji alt="🎉"/></p>') == "🎉"


def test_emoji_element_without_alt_uses_fallback_marker() -> None:
    assert normalize_teams_content("<emoji></emoji>") == "[emoji]"


def test_attachment_element_is_represented_safely() -> None:
    assert (
        normalize_teams_content('<p>See attached: <attachment id="123"></attachment></p>')
        == "See attached: [Attachment]"
    )


def test_attachment_inner_content_is_not_leaked_around_the_marker() -> None:
    """The marker replaces the element entirely -- any raw payload nested
    inside <attachment> must not leak into the output alongside it.
    """
    result = normalize_teams_content(
        '<attachment id="123">raw-blob-should-not-appear</attachment>'
    )
    assert result == "[Attachment]"
    assert "raw-blob-should-not-appear" not in result


def test_plain_text_content_passes_through() -> None:
    assert normalize_teams_content("Just plain text") == "Just plain text"


def test_plain_text_extra_whitespace_is_collapsed() -> None:
    assert normalize_teams_content("  Just   plain   text  ") == "Just plain text"


def test_empty_content_returns_empty_string() -> None:
    assert normalize_teams_content("") == ""


def test_whitespace_only_content_returns_empty_string() -> None:
    assert normalize_teams_content("   \n\t  ") == ""


def test_none_content_returns_empty_string() -> None:
    assert normalize_teams_content(None) == ""


def test_unknown_inline_tags_are_stripped_but_text_kept() -> None:
    assert (
        normalize_teams_content('<p>Hello <strong>bold</strong> and <a href="x">link</a></p>')
        == "Hello bold and link"
    )


def test_realistic_mixed_message() -> None:
    raw = (
        '<p>Deploy finished&nbsp;<emoji alt="✅"></emoji></p>'
        '<p>Logs: <attachment id="a1"></attachment></p>'
    )
    assert normalize_teams_content(raw) == "Deploy finished ✅\nLogs: [Attachment]"
