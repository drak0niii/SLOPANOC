"""Prompt-contract tests for the interactive Teams chat-name resolution
extension (`IncidentManagerOutcome.SELECTION_NEEDED` /
`StreamEventType.SELECTION_PENDING`). Mirrors
test_teams_write_ux_prompt_contract.py's approach exactly: pin the
deterministic instruction wording that governs conversational behavior,
never an exact Gemini sentence -- and prove no keyword/regex routing or
hardcoded canned prose was introduced anywhere in this pass.

Whitespace is normalized before substring checks, matching the
established pattern elsewhere in this suite.
"""
from __future__ import annotations

import inspect

from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION
from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION

_TM = " ".join(TEAM_MANAGER_INSTRUCTION.split())
_IM = " ".join(INCIDENT_MANAGER_INSTRUCTION.split())


# --- incident_manager: step 2 (threading pending_write_message) ------------


def test_incident_manager_prompt_threads_pending_write_message_through_step_2() -> None:
    assert "also pass it as `pending_write_message`" in _IM
    assert "the user is never asked to repeat it after picking a chat" in _IM
    assert "leave `pending_write_message` unset for anything that is not a sendMessage write" in _IM


def test_incident_manager_prompt_threads_pending_question_and_time_range_through_step_2() -> None:
    """Hardening pass: the read-side analogue of pending_write_message --
    lets a real chat-name ambiguity preserve WHAT was being asked (never
    the destination), so choosing a candidate can resume a read without
    replaying the user's original raw text (which still named the OLD,
    unresolved destination -- the exact live bug this closes).
    """
    assert (
        "also pass your own `question` and `requested_time_range` (exactly as given to you, including leaving "
        "either unset if it is unset) as `pending_question`/`pending_time_range`, PLUS `pending_operation`" in _IM
    )
    assert "this lets a real chat-name ambiguity preserve what was actually being asked" in _IM
    assert "without replaying or re-deriving anything from raw conversation text" in _IM
    assert (
        "Leave `pending_question`/`pending_time_range`/`pending_operation` all unset for a sendMessage write "
        "(it already has `pending_write_message` for the same purpose)" in _IM
    )


def test_incident_manager_prompt_separates_operation_from_focus_question() -> None:
    """Pre-4H hardening pass (item 1): `pending_operation` (a closed
    "summarize"/"get_messages" classification) is a SEPARATE signal from
    `pending_question` (free-text focus/detail) -- set every time,
    independent of whether `pending_question` is also set, so the base
    read intent survives even when `pending_question` itself must later
    be discarded for still naming the ambiguous chat.
    """
    assert 'set to whichever of "summarize" or "get_messages"' in _IM
    assert "`pending_operation` is a SEPARATE signal from `pending_question`" in _IM
    assert "set it every time, even when `pending_question` is also set" in _IM
    assert "`pending_question` must NEVER repeat or restate `chat_topic`'s own name" in _IM


# --- incident_manager: step 3 (selection_needed branch) --------------------


def test_incident_manager_prompt_has_a_selection_needed_branch_in_step_3() -> None:
    assert 'set `outcome` to "selection_needed"' in _IM


def test_incident_manager_prompt_never_sees_or_repeats_candidate_titles() -> None:
    assert "you never see the candidate titles (`similar_candidates`)" in _IM
    assert "must never repeat, summarize, or invent any of them" in _IM


def test_incident_manager_prompt_leaves_structured_fields_unset_for_selection_needed() -> None:
    assert (
        'Leave `chat_id`, `chat_title`, `summary`, and `candidate_titles` unset -- a `PendingSelection` was '
        "already created by the tool itself" in _IM
    )


def test_incident_manager_prompt_permits_a_short_plain_detail_sentence() -> None:
    assert (
        "`detail` may hold one short, plain sentence stating that the exact chat was not found and similar "
        "ones are available to choose from" in _IM
    )


def test_incident_manager_plain_not_found_branch_is_unchanged_and_distinct_from_selection_needed() -> None:
    assert 'If `match` is "not_found" (and `selection_pending` is not true): set `outcome` to "not_found"' in _IM


# --- incident_manager: TEAMS WRITE ACTIONS section --------------------------


def test_incident_manager_prompt_stops_on_selection_needed_for_writes() -> None:
    assert 'If step 3 results in "selection_needed", stop exactly as step 3 says' in _IM
    assert "do not attempt `teams_propose_send_message` without a resolved chat id" in _IM
    assert "do not ask the user to repeat the message text; it is already preserved" in _IM


def test_incident_manager_prompt_never_invents_a_chat_id_for_writes() -> None:
    assert (
        "get it exactly the way you already do for reads (step 2/3 above, or the currently selected chat "
        "if this is a follow-up" in _IM
    )


# --- team_manager: selection_needed outcome-handling bullet ----------------


def test_team_manager_prompt_has_a_selection_needed_outcome_bullet() -> None:
    assert '"selection_needed" (interaction-capability extension):' in _TM


def test_team_manager_prompt_explains_naturally_with_no_fixed_sentence() -> None:
    assert (
        "could not find an exact chat with that name but found similar ones, and invite them to review and "
        "pick one below" in _TM
    )
    assert "exact phrasing may vary turn to turn, there is no fixed sentence to reproduce" in _TM


def test_team_manager_prompt_never_lists_or_guesses_candidate_names() -> None:
    assert "You do NOT know the candidate names yourself" in _TM
    assert "do not list, guess, or imply specific chat names in this response" in _TM
    assert "the interactive selection below already presents the real options" in _TM


def test_team_manager_prompt_frames_selection_like_a_proposal_not_an_approval() -> None:
    assert "Make clear the choice is theirs, exactly like presenting a proposal" in _TM
    assert "nothing is sent or resolved until they pick one" in _TM


def test_team_manager_prompt_allows_typing_a_manual_correction_instead() -> None:
    assert "they may simply type the correct chat name directly instead of using the selection" in _TM


def test_team_manager_prompt_never_asks_the_user_to_repeat_anything_after_resolution() -> None:
    assert (
        "If they later pick one (or type an exact correction that resolves it), continue the original "
        "request normally" in _TM
    )
    assert "it resumes automatically" in _TM


# --- No hardcoded assistant prose / no post-processing ----------------------


def test_no_canned_selection_paragraph_was_hardcoded_in_either_prompt_module() -> None:
    """The fix must live in the deterministic PROMPT CONTRACT (instruction
    text), never as a Python string template the backend assembles and
    inserts verbatim -- the illustrative example sentences from the spec
    ("I've prepared your message for X...", "The requested Teams chat
    could not be found, so no message was prepared or sent.") must never
    appear as literal source strings.
    """
    import backend.agents.incident_manager.prompts as im_module
    import backend.agents.team_manager.prompts as tm_module

    for module in (im_module, tm_module):
        source = inspect.getsource(module)
        assert "I've prepared your message for" not in source
        assert "so no message was prepared or sent" not in source


def test_no_canned_selection_paragraph_was_hardcoded_in_selection_service() -> None:
    import backend.api.selection_service as selection_service_module

    source = inspect.getsource(selection_service_module)
    assert "I've prepared your message for" not in source
    assert "so no message was prepared or sent" not in source


def test_prompts_module_files_contain_no_regex_or_keyword_routing() -> None:
    import backend.agents.incident_manager.prompts as im_module
    import backend.agents.team_manager.prompts as tm_module

    for module in (im_module, tm_module):
        source = inspect.getsource(module)
        for forbidden in ("import re", "re.compile(", "re.match(", "re.search("):
            assert forbidden not in source


def test_selection_resolution_modules_contain_no_regex_or_ml_matching() -> None:
    """Similarity resolution must stay stdlib-deterministic (difflib +
    plain set/string operations) -- no new ML/search dependency, and no
    hidden regex-based name matching either.
    """
    import backend.tools.teams.chat_resolution as chat_resolution_module

    source = inspect.getsource(chat_resolution_module)
    assert "import difflib" in source
    for forbidden in ("import re", "re.compile(", "re.match(", "re.search("):
        assert forbidden not in source


def test_read_resume_module_contains_no_regex_or_keyword_routing() -> None:
    """Hardening pass guardrail: the read-resume text builder must stay
    pure structured-field assembly -- no phrase list, no keyword
    matching, no regex-based rewriting of anything.
    """
    import backend.selection.read_resume as read_resume_module

    source = inspect.getsource(read_resume_module)
    for forbidden in ("import re", "re.compile(", "re.match(", "re.search(", "if \"summary\"", "if \"summarize\""):
        assert forbidden not in source


def test_incident_manager_never_names_a_new_chat_matching_agent() -> None:
    """Architectural guardrail: chat resolution is deterministic tooling,
    not a new reasoning boundary -- no prompt anywhere should reference a
    separate "Chat Matching Agent" or similar.
    """
    assert "chat matching agent" not in _IM.lower()
    assert "chat matching agent" not in _TM.lower()


# --- team_manager: selected-chat state is authoritative across turns -------
# Hardening pass: live testing showed team_manager, on a follow-up turn
# after the user resolved an ambiguity via SelectionCard, incorrectly
# reverting to "I still can't find that chat" and re-mentioning the
# original unresolved name -- even though `selected_teams_chat_topic`
# was already correctly persisted (proven independently by
# test_team_manager_state_sync.py's real-ADK-templating tests and
# test_api_selection_endpoints.py's persistence tests). The state
# mechanism itself was never broken; the prompt simply never told the
# model that this state outranks its own earlier replies in the same
# conversation. These tests pin the added anti-resurrection wording.


def test_team_manager_prompt_states_selected_chat_is_authoritative_over_earlier_replies() -> None:
    assert "This state value is authoritative and always reflects the CURRENT resolution" in _TM
    assert "authoritative even over your OWN earlier replies in this same conversation" in _TM


def test_team_manager_prompt_forbids_resurrecting_a_superseded_lookup_failure() -> None:
    assert "never repeat it, never re-ask for the chat name, never re-mention the original name that did not resolve" in _TM
    assert "never re-open or reference that earlier lookup again for this same destination" in _TM


def test_team_manager_prompt_trusts_state_over_transcript_history() -> None:
    assert "Trust this state value over anything said earlier in the transcript, not the other way around" in _TM


def test_team_manager_prompt_treats_interactive_selection_resolution_the_same_as_a_direct_one() -> None:
    assert (
        "this applies identically whether the selection came from an earlier direct resolution or from the "
        "user picking a candidate from an interactive selection" in _TM
    )
    assert "do not act as if the destination were still unresolved" in _TM


def test_no_hardcoded_follow_up_phrase_routing_was_introduced_for_state_continuity() -> None:
    """The fix is expressed entirely in terms of state precedence over
    transcript history -- never literal follow-up phrases like "that
    chatroom"/"summarize it" to detect or match.
    """
    import backend.agents.team_manager.prompts as tm_module

    source = inspect.getsource(tm_module)
    for forbidden in ("that chatroom", "summarize it", "that chat\"", "if \"that"):
        assert forbidden not in source.lower()
    for forbidden in ("import re", "re.compile(", "re.match(", "re.search("):
        assert forbidden not in source
