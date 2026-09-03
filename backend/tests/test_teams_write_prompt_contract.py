"""Prompt-contract tests for milestone 3B (approval-protected Teams write
execution) -- confirms the actual instruction text sent to each model
establishes the required rules. Actual model behavior is a live-
acceptance concern (see the final report's live test procedure); these
tests only pin the wording that governs it.

Whitespace is normalized (`" ".join(text.split())`) before substring
checks, matching the established pattern in test_materiality_gate.py /
test_followup_routing_contract.py -- both prompt files wrap prose across
multiple backslash-continued lines with varying indentation.
"""
from __future__ import annotations

from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION
from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION

_IM = " ".join(INCIDENT_MANAGER_INSTRUCTION.split())
_TM = " ".join(TEAM_MANAGER_INSTRUCTION.split())


# --- incident_manager --------------------------------------------------


def test_incident_manager_prompt_has_a_write_actions_section() -> None:
    assert "TEAMS WRITE ACTIONS" in _IM


def test_incident_manager_prompt_states_it_can_never_approve_its_own_proposal() -> None:
    assert "you can never approve anything yourself" in _IM
    assert (
        "There is no tool available to you, now or ever, that approves, "
        "rejects, or otherwise authorizes a write" in _IM
    )


def test_incident_manager_prompt_forbids_name_to_email_resolution() -> None:
    assert "never a person's name" in _IM
    assert "you have no directory to resolve a name to an email address" in _IM


def test_incident_manager_prompt_requires_resolved_chat_id_never_invented() -> None:
    assert "never invent one" in _IM


def test_incident_manager_prompt_does_not_require_it_to_judge_real_approval() -> None:
    """Section 17's security invariant depends on incident_manager NOT
    trying to be the judge of whether approval is real -- that judgment is
    explicitly deferred to the deterministic tool.
    """
    assert "You do not need to determine whether a real, trusted approval has actually happened" in _IM
    assert "the tool itself independently, deterministically re-verifies approval" in _IM


def test_incident_manager_prompt_no_longer_claims_pure_read_only() -> None:
    """The old Teams-READ-v1-era "you have no way to create a chat or send
    a message" rule must be gone -- it would now be actively wrong and
    would contradict the tools this agent actually has.
    """
    assert "you have no way to create a chat or send a" not in _IM.lower()


def test_incident_manager_prompt_ties_write_action_field_to_proposed_and_executed() -> None:
    assert '"proposed"' in INCIDENT_MANAGER_INSTRUCTION
    assert '"executed"' in INCIDENT_MANAGER_INSTRUCTION
    assert "write_action" in INCIDENT_MANAGER_INSTRUCTION


def test_incident_manager_prompt_requires_exact_resend_of_proposed_values_for_execution() -> None:
    assert "never a value you" in _IM
    assert "even a trivial wording change is treated as a different action" in _IM


# --- team_manager --------------------------------------------------------


def test_team_manager_prompt_states_it_cannot_approve_write_actions() -> None:
    assert "You have no ability to approve, reject, or execute a write yourself" in _TM
    assert "approval is always a decision made by a trusted party outside this conversation" in _TM


def test_team_manager_prompt_forbids_claiming_approval_happened_in_conversation() -> None:
    assert (
        "Never state or imply that you, or anyone within this conversation, approved a write action"
        in _TM
    )
    assert "approval is never something that happens inside this conversation" in _TM


def test_team_manager_prompt_requires_presenting_proposals_professionally() -> None:
    """Phase 4G hardening pass: the earlier mechanical template ("Create
    Teams chat / Title: ... / Approval required.") was replaced with
    natural-language guidance -- see
    test_teams_write_ux_prompt_contract.py's "Professional presentation"
    section for the full contract. This test just confirms the core
    substantive requirement survived the wording change: the user must
    still be told, one way or another, that nothing happens until they
    review and confirm.
    """
    assert "make clear that nothing is sent or created until the user reviews and confirms it below" in _TM
    assert "the decision is entirely theirs" in _TM


def test_team_manager_prompt_requires_exact_restatement_for_execution_not_paraphrase() -> None:
    assert "restating the SAME exact title/ participants or chat/message you most recently presented" in _TM
    assert "never a paraphrase" in _TM


def test_team_manager_prompt_does_not_gate_execution_attempt_on_its_own_judgment() -> None:
    """Mirrors incident_manager's equivalent rule: team_manager attempting
    an execution delegation is always safe because the real gate is
    downstream -- it must not be instructed to withhold delegation while
    trying to determine whether real approval happened.
    """
    assert "you are not the one who determines that" in _TM
    assert "simply comes back as a normal \"error\" outcome" in _TM


def test_team_manager_prompt_treats_any_changed_detail_as_a_new_proposal() -> None:
    assert "treat it as a new action" in _TM


def test_team_manager_prompt_no_longer_claims_writes_are_entirely_unavailable() -> None:
    assert "you cannot yet create teams chats or send teams messages" not in _TM.lower()
