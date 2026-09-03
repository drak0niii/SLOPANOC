"""Prompt-contract tests for the conversational UX polish pass on the
Teams write flow (participant-collection UX, invalid-email UX, proposal
presentation, expiry presentation, rejection UX, minimize-unnecessary-
questions). Per this task's test strategy, actual conversational
behavior is a live-model concern -- these tests only pin the instruction
wording that governs it, plus a few structural/deterministic guarantees
(no phrase/keyword/regex routing was introduced anywhere in this pass,
and the deterministic validator/approval layers are untouched).

Whitespace is normalized before substring checks, matching the
established pattern elsewhere in this suite (see
test_teams_write_prompt_contract.py).
"""
from __future__ import annotations

import inspect

from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION
from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION
from backend.tests import manual as manual_package  # noqa: F401  (package sanity)

_TM = " ".join(TEAM_MANAGER_INSTRUCTION.split())
_IM = " ".join(INCIDENT_MANAGER_INSTRUCTION.split())


# --- 1. Participant collection UX ------------------------------------------


def test_prompt_has_a_collecting_write_action_details_section() -> None:
    assert "COLLECTING WRITE-ACTION DETAILS" in _TM


def test_prompt_requires_stating_more_is_needed_below_the_minimum() -> None:
    assert (
        'do not ask a vague, open-ended "anything else?" while something '
        "mandatory is still outstanding" in _TM
    )


def test_prompt_requires_tracking_what_was_already_given_across_turns() -> None:
    assert "do not ask the user to repeat information they already provided in an earlier turn" in _TM


def test_prompt_stops_requiring_more_once_minimum_is_met() -> None:
    assert "stop treating more input as required" in _TM
    assert "offer a natural choice" in _TM


def test_prompt_moves_on_once_nothing_is_missing_or_unclear() -> None:
    assert (
        "do not keep asking clarifying questions for their own sake once "
        "nothing is actually missing or unclear" in _TM
    )


# --- 2. Invalid email UX ----------------------------------------------------


def test_team_manager_prompt_preserves_valid_details_on_a_named_person() -> None:
    assert "keeping whatever title/other valid participants were already established" in _TM


def test_team_manager_prompt_never_implies_execution_was_attempted_on_validation_error() -> None:
    assert (
        "do not say or imply that a chat was created, a message was sent, "
        "or that creation/sending was attempted and failed" in _TM
    )
    assert "nothing was ever attempted at that point, only checked" in _TM
    assert "keep every other already-valid detail" in _TM


def test_incident_manager_prompt_never_implies_execution_was_attempted_on_validation_error() -> None:
    assert "that means the proposal was never even created" in _IM
    assert "nothing was attempted against Teams" in _IM
    assert "never imply that chat creation itself was attempted or failed" in _IM


# --- 3. Proposal presentation -----------------------------------------------


def test_team_manager_prompt_has_a_presenting_a_proposal_section() -> None:
    assert "PRESENTING A PROPOSAL" in _TM


def test_team_manager_prompt_describes_both_proposal_kinds_naturally() -> None:
    """Phase 4G hardening pass: the earlier mechanical, fixed-format
    template ("Title: ... / Participants: ... / Approval required.") was
    replaced with natural-language guidance -- see the "Professional
    presentation contract" section below for the full replacement.
    """
    assert "that the chat has been prepared, naturally referencing" in _TM
    assert "that the message has been prepared, naturally" in _TM


def test_team_manager_prompt_excludes_internal_identifiers_from_presentation() -> None:
    assert "Do not mention `proposal_id`, `payload_hash`" in TEAM_MANAGER_INSTRUCTION
    assert "for internal/ developer use, not for the conversation" in _TM


def test_incident_manager_prompt_excludes_internal_identifiers_from_summary() -> None:
    assert "never mention the proposal id, the payload hash, or any other internal identifier in `summary`" in _IM


# --- 4. Expiry presentation --------------------------------------------------
# Phase 4G hardening pass: live end-to-end testing showed the model
# actually saying "Approval required. (Expires in approximately 10
# minutes.)" in normal conversation -- a direct violation of the locked
# "no visible timer/countdown" UX requirement (expiry is a backend
# security constraint, not conversational content). The instruction
# wording pinned here was updated from "phrase expiry approximately" to
# an explicit prohibition; see test_teams_dynamic_expiry_presentation.py
# for the accompanying tool-contract change (the model-facing propose
# result no longer carries any expiry field at all -- the actual source
# fix, not a prompt-only request).


def test_team_manager_prompt_forbids_expiry_duration_in_normal_presentation() -> None:
    assert "Do NOT mention when the approval expires" in TEAM_MANAGER_INSTRUCTION
    assert "how much time remains" in _TM
    assert "10 minutes" not in _TM
    assert "expires_in_minutes" not in _TM
    assert "expires_in_seconds" not in _TM


def test_incident_manager_prompt_forbids_expiry_duration_in_summary() -> None:
    assert "Do NOT mention when the approval expires" in INCIDENT_MANAGER_INSTRUCTION
    assert "how much time remains" in _IM
    assert "expires in about 10 minutes" not in _IM
    assert "expires_in_minutes" not in _IM
    assert "expires_in_seconds" not in _IM


def test_prompts_still_permit_relaying_an_already_expired_denial_authoritatively() -> None:
    """The prohibition is on ESTIMATING expiry in advance -- both prompts
    must still allow plainly relaying an "already expired" fact once the
    backend reports one via a failed execute attempt (unchanged, generic
    tool-error-relay path)."""
    assert "approval expired" in _TM
    assert "expired" in _IM


# --- 5. Rejection UX --------------------------------------------------------


def test_team_manager_prompt_states_rejection_means_cancelled_not_a_failure() -> None:
    assert "say plainly that it was cancelled and nothing was sent" in _TM
    assert (
        "do not phrase this as if Teams or Power Automate malfunctioned" in _TM
    )


def test_team_manager_prompt_forbids_auto_recreating_a_rejected_proposal() -> None:
    assert "do not immediately create a new proposal for the same action on your own initiative" in _TM
    assert "wait for the user to say what they want to do next" in _TM


# --- 7. Professional pre-approval presentation contract (Phase 4G hardening) -
# Live end-to-end testing showed the model literally saying: "Send this
# message to SLOPANOC Gateway Group Test: 'test'; approval required." --
# mechanical, internal-sounding phrasing. The instruction wording pinned
# here was rewritten to require natural, professional language instead,
# while keeping every substantive requirement (what will happen, that
# nothing is sent/created yet, that the user decides) intact. Per this
# task's test strategy: pin the deterministic prompt contract, never an
# exact Gemini sentence.


def test_team_manager_prompt_asks_for_natural_professional_wording_not_a_script() -> None:
    assert "present it in natural, professional language" in _TM
    assert "not a rigid script" in _TM
    assert "exact phrasing may vary naturally turn to turn" in _TM
    assert "there is no fixed sentence to reproduce" in _TM


def test_team_manager_prompt_forbids_mechanical_internal_wording_when_avoidable() -> None:
    for forbidden_example in ('"operation teams.sendMessage"', '"payload"', '"execute"', '"proposal"'):
        assert forbidden_example in _TM  # named as an example of what to AVOID
    assert "reciting \"approval required\" as a" in _TM


def test_team_manager_prompt_requires_naming_the_destination_when_known() -> None:
    assert "naturally referencing" in _TM  # createChat: its title
    assert "naturally identifying the destination when it is known" in _TM  # sendMessage
    assert "write_action" in _TM
    assert "target_display_name" in _TM


def test_team_manager_prompt_never_speaks_the_raw_chat_id_aloud() -> None:
    assert "never its raw `chat_id`, which is an internal identifier and not something to say aloud" in _TM


def test_team_manager_prompt_never_invents_a_destination_name() -> None:
    assert "if `target_display_name` is not set, describe the destination generically rather than inventing a name" in _TM


def test_incident_manager_prompt_asks_for_natural_professional_wording_not_a_script() -> None:
    assert "natural, professional language" in _IM
    assert "not a rigid script" in _IM
    assert "there is no exact sentence to reproduce" in _IM


def test_incident_manager_prompt_instructs_copying_target_display_name_when_present() -> None:
    assert "including `target_display_name` when the tool returned one" in _IM
    assert "never the raw chat id" in _IM
    assert "leave unset when the tool did not return one rather than guessing at a name" in _IM


# --- 8. No hardcoded assistant prose / no post-processing --------------------


def test_no_canned_assistant_paragraph_was_hardcoded_in_either_prompt_module() -> None:
    """The fix must live in the deterministic PROMPT CONTRACT (instruction
    text), never as a Python string template the backend assembles and
    inserts verbatim -- e.g. no f-string/`.format()` building a full
    sentence like "I've prepared your message for {name}...".
    """
    import backend.agents.incident_manager.prompts as im_module
    import backend.agents.team_manager.prompts as tm_module

    for module in (im_module, tm_module):
        source = inspect.getsource(module)
        assert "I've prepared" not in source
        assert "Please review it below" not in source


def test_no_canned_assistant_paragraph_was_hardcoded_in_the_propose_tool() -> None:
    import backend.tools.teams.propose_write as propose_write_module

    source = inspect.getsource(propose_write_module)
    assert "I've prepared" not in source
    assert "Please review it below" not in source


# --- 6/9. Generic, non-hardcoded wording ------------------------------------


def test_ux_guidance_is_framed_around_state_not_literal_phrases() -> None:
    """The new guidance is expressed in terms of what's missing/valid/
    complete -- not literal example utterances presented as things to
    match.
    """
    assert "not a fixed script" in _TM
    assert "there is no fixed phrase to recognize" in _TM


def test_prompts_module_files_contain_no_regex_or_keyword_routing() -> None:
    import backend.agents.incident_manager.prompts as im_module
    import backend.agents.team_manager.prompts as tm_module

    for module in (im_module, tm_module):
        source = inspect.getsource(module)
        for forbidden in ("import re", "re.compile(", "re.match(", "re.search("):
            assert forbidden not in source


# --- Dev CLI cleanup: presentation-only, no NLP routing ---------------------


def test_dev_cli_never_routes_approval_from_user_conversation_text() -> None:
    """`user_text` (what the developer types AS the simulated end user)
    must never reach `approve_proposal`/`reject_proposal` -- only the
    separate, fixed `choice` menu input does.
    """
    from backend.tests.manual import approval_dev_cli

    source = inspect.getsource(approval_dev_cli)
    approve_call = source.index("def _maybe_handle_pending_proposal")
    handler_body = source[approve_call:]
    # `_maybe_handle_pending_proposal`'s own signature/body never
    # references `user_text` as a value -- only mentions it in a prose
    # comment elsewhere in the file (checked separately below); a real
    # data-flow reference would appear as `user_text)`/`(user_text`.
    assert "(user_text" not in handler_body
    assert "user_text)" not in handler_body
    assert "user_text" not in inspect.signature(approval_dev_cli._maybe_handle_pending_proposal).parameters


def test_dev_cli_contains_no_regex_or_keyword_confirmation_parsing() -> None:
    from backend.tests.manual import approval_dev_cli

    source = inspect.getsource(approval_dev_cli)
    for forbidden in ("import re", "re.compile(", "re.match(", "re.search("):
        assert forbidden not in source


def test_dev_cli_only_suppresses_the_known_sdk_warning_logger() -> None:
    """The logging-level cleanup is scoped to exactly one named logger --
    not a blanket suppression that could hide a genuine error.
    """
    from backend.tests.manual import approval_dev_cli

    source = inspect.getsource(approval_dev_cli)
    assert 'logging.getLogger("google_genai.types")' in source
    assert "logging.disable(" not in source
    assert "logging.getLogger()." not in source  # never touches the root logger
