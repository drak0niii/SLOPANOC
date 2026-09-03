"""Prompt-contract test for the `{resolved_chat_id?}` short-circuit
paragraph in `incident_manager`'s instruction -- pins that the
deterministic-resolution rule exists, precedes the normal discovery step,
and is scoped to reads only (never a write's own `chat_id` resolution).

`resolved_chat_id` is a PLAIN, non-`temp:`-prefixed state key -- see
read_continuation_execution.py's own "P1 LIVE-INCIDENT FIX" docstring for
why `temp:`-prefixing this specific value was itself the root cause of a
live production incident (`create_session` silently drops every
`temp:`-prefixed key from a session's initial state).
"""
from __future__ import annotations

from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION

_IM = " ".join(INCIDENT_MANAGER_INSTRUCTION.split())


def test_prompt_references_the_temp_resolved_chat_id_state_placeholder() -> None:
    assert "{resolved_chat_id?}" in _IM


def test_prompt_instructs_skipping_list_chats_when_resolved_chat_id_present() -> None:
    assert "skip `teams_list_chats`" in _IM or "skip `teams_list_chats` (step 2) ENTIRELY" in _IM


def test_prompt_states_resolved_chat_id_is_never_set_by_the_model_itself() -> None:
    assert "never by you" in _IM


def test_prompt_excludes_write_actions_from_the_short_circuit() -> None:
    assert "This never applies to a `teams_propose_send_message`" in _IM
    assert "only ever set for a resumed READ" in _IM


def test_short_circuit_paragraph_precedes_step_1() -> None:
    marker_idx = _IM.index("{resolved_chat_id?}")
    step1_idx = _IM.index("1. If `requested_time_range` is present")
    assert marker_idx < step1_idx
