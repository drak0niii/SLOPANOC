"""Tests for the centralized, deterministic Teams chat-name resolver
(chat_resolution.py) -- interaction-capability extension.

Uses only synthetic fixture names, never real Teams content (instruction:
"Do not put private real Teams content into production fixtures.").
"""
from __future__ import annotations

from backend.tools.teams.chat_resolution import (
    MAX_SIMILAR_CANDIDATES,
    ChatResolutionOutcome,
    resolve_chat,
)
from backend.tools.teams.schemas import ChatSummary


def _chat(chat_id: str, title: str) -> ChatSummary:
    return ChatSummary(chat_id=chat_id, title=title)


def test_exact_case_insensitive_match_wins_without_any_scoring() -> None:
    chats = [_chat("c1", "Project Falcon Room"), _chat("c2", "Project Falcon Room Test")]
    result = resolve_chat("project falcon room", chats)

    assert result.outcome == ChatResolutionOutcome.MATCHED
    assert result.matched_chat.chat_id == "c1"
    assert result.similar_candidates == []


def test_exact_match_ignores_leading_trailing_whitespace() -> None:
    chats = [_chat("c1", "Project Falcon Room")]
    result = resolve_chat("  Project Falcon Room  ", chats)
    assert result.outcome == ChatResolutionOutcome.MATCHED


def test_multiple_chats_sharing_the_exact_title_are_ambiguous_not_similar() -> None:
    chats = [_chat("c1", "Project Falcon Room"), _chat("c2", "Project Falcon Room")]
    result = resolve_chat("Project Falcon Room", chats)

    assert result.outcome == ChatResolutionOutcome.AMBIGUOUS
    assert {c.chat_id for c in result.exact_duplicates} == {"c1", "c2"}
    assert result.similar_candidates == []


def test_no_exact_match_returns_similar_candidates_ordered_by_score() -> None:
    chats = [
        _chat("c1", "Project Falcon Room Test"),
        _chat("c2", "Project Falcon Test"),
        _chat("c3", "Project Falcon Operations"),
        _chat("c4", "Completely Unrelated Room"),
    ]
    result = resolve_chat("Project Falcon Room", chats)

    assert result.outcome == ChatResolutionOutcome.NOT_FOUND
    assert result.matched_chat is None
    titles = [c.title for c in result.similar_candidates]
    # The closest match (shares every token with the request) ranks first.
    assert titles[0] == "Project Falcon Room Test"
    assert "Completely Unrelated Room" not in titles


def test_never_returns_more_than_the_documented_maximum() -> None:
    assert MAX_SIMILAR_CANDIDATES == 3
    chats = [_chat(f"c{i}", f"Project Falcon Variant {i}") for i in range(10)]
    result = resolve_chat("Project Falcon", chats)
    assert len(result.similar_candidates) <= 3


def test_completely_unrelated_names_yield_no_candidates_at_all() -> None:
    chats = [_chat("c1", "Completely Different Topic"), _chat("c2", "Another Unrelated Room")]
    result = resolve_chat("Project Falcon Room", chats)

    assert result.outcome == ChatResolutionOutcome.NOT_FOUND
    assert result.similar_candidates == []


def test_empty_chat_list_never_errors() -> None:
    result = resolve_chat("Project Falcon Room", [])
    assert result.outcome == ChatResolutionOutcome.NOT_FOUND
    assert result.similar_candidates == []


def test_never_invents_a_chat_not_present_in_the_input_list() -> None:
    chats = [_chat("c1", "Project Falcon Room Test")]
    result = resolve_chat("Project Falcon Room", chats)
    for candidate in result.similar_candidates:
        assert candidate in chats


def test_deterministic_and_stable_across_repeated_calls() -> None:
    chats = [
        _chat("c1", "Project Falcon Room Test"),
        _chat("c2", "Project Falcon Test"),
        _chat("c3", "Project Falcon Operations"),
    ]
    first = resolve_chat("Project Falcon Room", chats)
    second = resolve_chat("Project Falcon Room", chats)
    assert [c.chat_id for c in first.similar_candidates] == [c.chat_id for c in second.similar_candidates]


def test_one_meaningful_candidate_is_still_returned_as_a_candidate_not_auto_matched() -> None:
    chats = [_chat("c1", "Project Falcon Room Test"), _chat("c2", "Totally Different Thing")]
    result = resolve_chat("Project Falcon Room", chats)

    assert result.outcome == ChatResolutionOutcome.NOT_FOUND
    assert result.matched_chat is None
    assert len(result.similar_candidates) == 1
    assert result.similar_candidates[0].chat_id == "c1"
