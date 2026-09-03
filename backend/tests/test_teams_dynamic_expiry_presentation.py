"""Tests for approval-expiry presentation (Phase 4G hardening pass).

HISTORY: an earlier milestone (3B) deliberately exposed a dynamically-
computed `expires_in_seconds`/`expires_in_minutes` to the model so it
could narrate approval expiry "naturally" instead of using a hardcoded
duration. Live end-to-end testing then showed this was itself a defect:
the model said "Approval required. (Expires in approximately 10
minutes.)" in normal conversation, which violates the locked "no visible
timer/countdown" UX requirement -- expiry is a backend security
constraint, not normal conversational content. This file was rewritten to
match the corrected contract:

  - the deterministic seconds/minutes computation
    (backend/tools/teams/expiry_presentation.py) is UNCHANGED and still
    used elsewhere (the API-facing PendingActionDTO, the manual dev CLI)
  - the propose tools' MODEL-FACING result no longer includes any expiry
    field at all -- the actual source fix (see
    propose_write.py's `_proposal_info` docstring), not a prompt-only
    request or post-hoc string stripping
  - neither agent's prompt instructs narrating expiry duration in normal
    proposal presentation
  - the security-critical expiry check
    (backend.approval.policy_gate.authorize_write) remains completely
    unaffected -- it never read the presentation fields even before this
    pass, and still doesn't

Per this task's test strategy: deterministic calculation and prompt/tool
contract only, never live-model wording.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION
from backend.agents.incident_manager.schemas import TeamsWriteActionResult
from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION
from backend.approval.policy_gate import authorize_write
from backend.approval.service import approve_proposal, create_action_proposal, load_active_proposal
from backend.tools.teams.expiry_presentation import (
    compute_expires_in_minutes,
    compute_expires_in_seconds,
)
from backend.tools.teams.propose_write import teams_propose_create_chat, teams_propose_send_message

_NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
_TM = " ".join(TEAM_MANAGER_INSTRUCTION.split())
_IM = " ".join(INCIDENT_MANAGER_INSTRUCTION.split())


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = state if state is not None else {}


# --- Deterministic computation (unchanged, still used elsewhere) --------


def test_expires_in_seconds_for_the_default_configured_window() -> None:
    expires_at = _NOW + timedelta(seconds=600)
    assert compute_expires_in_seconds(expires_at, now=_NOW) == 600


def test_expires_in_minutes_for_the_default_configured_window() -> None:
    assert compute_expires_in_minutes(600) == 10


def test_expires_in_seconds_for_a_custom_configured_window() -> None:
    expires_at = _NOW + timedelta(seconds=120)
    assert compute_expires_in_seconds(expires_at, now=_NOW) == 120


def test_expires_in_minutes_for_a_custom_configured_window() -> None:
    assert compute_expires_in_minutes(120) == 2


def test_expires_in_seconds_derives_from_the_actual_expires_at_not_a_constant() -> None:
    short = compute_expires_in_seconds(_NOW + timedelta(seconds=90), now=_NOW)
    long_ = compute_expires_in_seconds(_NOW + timedelta(seconds=900), now=_NOW)
    assert short == 90
    assert long_ == 900
    assert short != long_


def test_expires_in_seconds_floors_at_zero_once_expired() -> None:
    already_expired = _NOW - timedelta(seconds=5)
    assert compute_expires_in_seconds(already_expired, now=_NOW) == 0


def test_expires_in_seconds_floors_at_zero_long_after_expiry() -> None:
    long_expired = _NOW - timedelta(hours=3)
    assert compute_expires_in_seconds(long_expired, now=_NOW) == 0


def test_expires_in_minutes_is_zero_once_expired() -> None:
    assert compute_expires_in_minutes(0) == 0


def test_expires_in_minutes_rounds_to_nearest_whole_minute() -> None:
    assert compute_expires_in_minutes(331) == 6  # 5.5166... -> 6
    assert compute_expires_in_minutes(59) == 1  # 0.983... -> 1
    assert compute_expires_in_minutes(29) == 0  # 0.483... -> 0


def test_expires_in_seconds_never_returns_a_negative_value() -> None:
    for delta_seconds in (-1, -60, -3600 * 24):
        assert compute_expires_in_seconds(_NOW + timedelta(seconds=delta_seconds), now=_NOW) == 0


# --- The propose tools' model-facing result carries NO expiry field -----


def test_create_chat_proposal_result_has_no_expiry_field() -> None:
    ctx = _FakeToolContext()
    result = teams_propose_create_chat("Ops Bridge", ["user1@example.com", "user2@example.com"], tool_context=ctx)
    assert "expires_at" not in result
    assert "expires_in_seconds" not in result
    assert "expires_in_minutes" not in result


def test_send_message_proposal_result_has_no_expiry_field() -> None:
    ctx = _FakeToolContext()
    result = teams_propose_send_message("c1", "Hello team", tool_context=ctx)
    assert "expires_at" not in result
    assert "expires_in_seconds" not in result
    assert "expires_in_minutes" not in result


def test_teams_write_action_result_schema_has_no_expiry_field() -> None:
    """incident_manager's own structured read-back schema (passed to
    team_manager) has no expiry field to populate -- a schema-level
    guarantee the model cannot narrate a countdown even if a future
    prompt edit forgot to say so.
    """
    assert "expires_at" not in TeamsWriteActionResult.model_fields
    assert "expires_in_seconds" not in TeamsWriteActionResult.model_fields
    assert "expires_in_minutes" not in TeamsWriteActionResult.model_fields


def test_the_proposal_still_has_a_real_expires_at_server_side() -> None:
    """The fix removes the field from the MODEL-facing result only -- the
    actual `ActionProposal` stored in session state is untouched and
    still carries a real `expires_at`.
    """
    ctx = _FakeToolContext()
    teams_propose_send_message("c1", "Hello team", tool_context=ctx)
    stored = load_active_proposal(ctx.state)
    assert stored.expires_at is not None


# --- No expiry duration/countdown guidance in either prompt --------------


def test_no_fixed_expiry_duration_in_team_manager_prompt() -> None:
    for forbidden in ("10 minutes", "600 seconds", "ten minutes"):
        assert forbidden not in _TM


def test_no_fixed_expiry_duration_in_incident_manager_prompt() -> None:
    for forbidden in ("10 minutes", "600 seconds", "ten minutes"):
        assert forbidden not in _IM


def test_neither_prompt_references_the_removed_expiry_presentation_fields() -> None:
    """Confirms the prompt text was actually updated alongside the tool
    change -- not just that the tool stopped returning the fields while a
    stale instruction still told the model to use them.
    """
    for forbidden in ("expires_in_minutes", "expires_in_seconds"):
        assert forbidden not in _TM
        assert forbidden not in _IM


def test_team_manager_prompt_explicitly_forbids_mentioning_expiry_in_normal_presentation() -> None:
    assert "Do NOT mention when the approval expires" in TEAM_MANAGER_INSTRUCTION
    assert "how much time remains" in _TM


def test_incident_manager_prompt_explicitly_forbids_mentioning_expiry_in_summary() -> None:
    assert "Do NOT mention when the approval expires" in INCIDENT_MANAGER_INSTRUCTION


def test_prompts_still_allow_relaying_an_already_expired_denial() -> None:
    """The prohibition is on ESTIMATING/narrating expiry in advance --
    both prompts must still permit relaying an authoritative "already
    expired" fact once the backend reports one via a failed execute
    attempt (existing generic tool-error-relay path, unchanged)."""
    assert "approval expired" in _TM
    assert "expired" in _IM


# --- Security: authorize_write is completely unaffected -----------------


def test_authorize_write_still_uses_expires_at_and_ignores_presentation_fields() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    approve_proposal(proposal.proposal_id, state, now=_NOW)

    result = authorize_write("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    assert result.authorized is True

    later = _NOW + timedelta(minutes=11)
    expired_result = authorize_write("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=later)
    assert expired_result.authorized is False
    assert expired_result.reason.value == "proposal_expired"


def test_pending_action_dto_still_has_all_its_frozen_expiry_fields() -> None:
    """The API-facing `PendingActionDTO` (action.pending SSE events,
    /approve, /reject, /execute responses -- Phase 4E/4G's own frozen
    contract) is a COMPLETELY SEPARATE data path from the model-facing
    propose-tool result this pass changed. It must still carry
    `expires_at`/`expires_in_seconds`/`expires_in_minutes` unchanged --
    the frontend is simply instructed (and already verified elsewhere,
    see src/components/conversation/ApprovalCard.test.tsx) not to render
    them; the backend/API contract itself is untouched by this pass.
    """
    from backend.api.schemas import PendingActionDTO

    assert "expires_at" in PendingActionDTO.model_fields
    assert "expires_in_seconds" in PendingActionDTO.model_fields
    assert "expires_in_minutes" in PendingActionDTO.model_fields


def test_no_regex_or_string_stripping_was_introduced_to_solve_this() -> None:
    """The preferred fix is removing the data at its source (the propose
    tools' model-facing result) and updating the prompt contract -- never
    post-processing generated text to delete expiry-sounding phrases.
    Mirrors the existing no-regex-routing guarantee already pinned for
    both prompt modules in test_teams_write_ux_prompt_contract.py.
    """
    import inspect

    import backend.tools.teams.propose_write as propose_write_module

    source = inspect.getsource(propose_write_module)
    for forbidden in ("import re", "re.compile(", "re.sub(", "re.match(", "re.search(", "replace(\"expires"):
        assert forbidden not in source


def test_action_proposal_schema_has_no_presentation_fields() -> None:
    """The security-critical `ActionProposal` model itself must remain
    untouched -- presentation fields never lived in the schema
    `authorize_write` parses, before or after this pass.
    """
    from backend.approval.schemas import ActionProposal

    assert "expires_in_seconds" not in ActionProposal.model_fields
    assert "expires_in_minutes" not in ActionProposal.model_fields


def test_settings_expiry_window_is_unchanged_and_still_drives_authorization() -> None:
    """A custom configured expiry window still changes when authorize_write
    denies -- proves the removal of MODEL-facing presentation fields had
    zero effect on the actual, server-side expiry window/enforcement."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("SLOPANOC_ACTION_PROPOSAL_EXPIRY_SECONDS", "60")
    from backend.config.settings import get_settings

    get_settings.cache_clear()
    try:
        state: dict = {}
        proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
        approve_proposal(proposal.proposal_id, state, now=_NOW)

        still_valid = authorize_write("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW + timedelta(seconds=30))
        assert still_valid.authorized is True

        now_expired = authorize_write("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW + timedelta(seconds=90))
        assert now_expired.authorized is False
        assert now_expired.reason.value == "proposal_expired"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
