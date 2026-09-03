"""Prompt/text-contract tests for the ambiguous-Teams-chat-selection ->
read-resume scope bugfix -- pins the disambiguating wording added to
`read_resume.py`'s generic fallback phrases and to team_manager's
CONVERSATION TARGET section, both aimed at the SAME root cause: once
`current_thread` became a legitimate meaning of "chat"/"conversation",
the pre-existing, purely-generic resume phrasing ("the currently selected
chat") stopped unambiguously signaling an external Teams resource.
"""
from __future__ import annotations

from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION
from backend.selection.read_resume import (
    GENERIC_GET_MESSAGES_RESUME_TEXT,
    GENERIC_SUMMARY_RESUME_TEXT,
    build_read_resume_message,
)
from backend.selection.schemas import PendingReadIntent

_TM = " ".join(TEAM_MANAGER_INSTRUCTION.split())


def test_generic_summary_resume_text_explicitly_names_teams() -> None:
    assert "Teams conversation" in GENERIC_SUMMARY_RESUME_TEXT


def test_generic_get_messages_resume_text_explicitly_names_teams() -> None:
    assert "Teams conversation" in GENERIC_GET_MESSAGES_RESUME_TEXT


def test_generic_resume_texts_still_never_contain_a_destination_name() -> None:
    for text in (GENERIC_SUMMARY_RESUME_TEXT, GENERIC_GET_MESSAGES_RESUME_TEXT):
        assert "SLOPANOC" not in text
        assert "Gateway" not in text


def test_build_read_resume_message_default_output_is_teams_scoped() -> None:
    assert "Teams conversation" in build_read_resume_message(None)
    assert "Teams conversation" in build_read_resume_message(PendingReadIntent())


def test_prompt_treats_a_plain_post_selection_followup_as_selected_external_conversation() -> None:
    assert (
        "immediately after you just presented a choice between similar Teams chats and the "
        in _TM
    )
    assert "precisely a Teams conversation being" in _TM


def test_prompt_still_never_lets_selected_state_force_current_thread_globally() -> None:
    # Regression guard: the new clarifying sentence must not have replaced
    # or weakened the existing "structured selection is context, never
    # global precedence" rule from the ConversationTarget milestone.
    assert "A structured external selection is context, never" in _TM
    assert "the target for the CURRENT request controls what" in _TM


def test_prompt_does_not_hardcode_the_exact_resume_boilerplate_sentence() -> None:
    """The prompt reasons about the SITUATION (a just-resolved selection
    followed by a plain read request), never about matching read_resume
    .py's own literal generated string -- these two modules stay
    independent, closed-form fixes for the same interaction, not one
    hardcoded to the other's exact text."""
    assert GENERIC_SUMMARY_RESUME_TEXT not in TEAM_MANAGER_INSTRUCTION
    assert GENERIC_GET_MESSAGES_RESUME_TEXT not in TEAM_MANAGER_INSTRUCTION
