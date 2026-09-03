"""Tests for the trusted-boundary proposal lifecycle
(create/approve/reject/consume) in backend/approval/service.py.

`session_state` is exercised as a plain `dict` throughout -- the module
only requires a mutable string-keyed mapping (see its module docstring),
and a plain dict is the simplest thing that satisfies that contract
without depending on real ADK session objects (those are already
exercised for the unrelated Teams-state-sync feature in
test_team_manager_state_sync.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.approval.schemas import ApprovalDenialReason, ProposalStatus, WriteOperation
from backend.approval.service import (
    PENDING_ACTION_PROPOSAL_STATE_KEY,
    UnsupportedOperationError,
    approve_proposal,
    consume_proposal,
    create_action_proposal,
    reject_proposal,
)

_NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)


# --- PROPOSAL CREATION -----------------------------------------------------


def test_valid_create_chat_proposal() -> None:
    state: dict = {}
    proposal = create_action_proposal(
        "teams.createChat", {"title": "Ops Bridge", "participants": ["u1", "u2"]}, state, now=_NOW
    )

    assert proposal.operation == WriteOperation.TEAMS_CREATE_CHAT
    assert proposal.status == ProposalStatus.PENDING
    assert state[PENDING_ACTION_PROPOSAL_STATE_KEY]["proposal_id"] == proposal.proposal_id


def test_valid_send_message_proposal() -> None:
    state: dict = {}
    proposal = create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "Hello"}, state, now=_NOW
    )

    assert proposal.operation == WriteOperation.TEAMS_SEND_MESSAGE
    assert proposal.status == ProposalStatus.PENDING


def test_unsupported_operation_is_rejected() -> None:
    with pytest.raises(UnsupportedOperationError):
        create_action_proposal("teams.deleteChat", {"chatId": "c1"}, {}, now=_NOW)


def test_proposal_ids_are_unique() -> None:
    state: dict = {}
    first = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "A"}, state, now=_NOW)
    second = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "B"}, state, now=_NOW)

    assert first.proposal_id != second.proposal_id


def test_timestamps_are_timezone_aware() -> None:
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, {}, now=_NOW)

    assert proposal.created_at.tzinfo is not None
    assert proposal.expires_at.tzinfo is not None


def test_default_expiry_is_ten_minutes() -> None:
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, {}, now=_NOW)

    assert proposal.expires_at - proposal.created_at == timedelta(minutes=10)


def test_payload_hash_is_deterministic() -> None:
    state: dict = {}
    payload = {"chatId": "c1", "message": "Hello"}
    first = create_action_proposal("teams.sendMessage", payload, state, now=_NOW)
    second = create_action_proposal("teams.sendMessage", payload, {}, now=_NOW)

    assert first.payload_hash == second.payload_hash


def test_dict_key_order_does_not_alter_the_stored_hash() -> None:
    a = create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "Hello"}, {}, now=_NOW
    )
    b = create_action_proposal(
        "teams.sendMessage", {"message": "Hello", "chatId": "c1"}, {}, now=_NOW
    )

    assert a.payload_hash == b.payload_hash


def test_summary_is_optional_and_carried_through() -> None:
    proposal = create_action_proposal(
        "teams.sendMessage",
        {"chatId": "c1", "message": "Hi"},
        {},
        summary="Send 'Hi' to Ops Bridge",
        now=_NOW,
    )
    assert proposal.summary == "Send 'Hi' to Ops Bridge"


# --- APPROVAL ---------------------------------------------------------------


def test_pending_proposal_can_be_approved() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)

    result = approve_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is True
    assert result.proposal.status == ProposalStatus.APPROVED


def test_wrong_proposal_id_is_rejected() -> None:
    state: dict = {}
    create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)

    result = approve_proposal("not-the-real-id", state, now=_NOW)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_ID_MISMATCH


def test_expired_proposal_cannot_be_approved() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    later = _NOW + timedelta(minutes=11)

    result = approve_proposal(proposal.proposal_id, state, now=later)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_EXPIRED


def test_rejected_proposal_cannot_be_approved() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    reject_proposal(proposal.proposal_id, state, now=_NOW)

    result = approve_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_REJECTED


def test_consumed_proposal_cannot_be_approved() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    approve_proposal(proposal.proposal_id, state, now=_NOW)
    consume_proposal(proposal.proposal_id, state, now=_NOW)

    result = approve_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_CONSUMED


def test_approving_an_already_approved_proposal_is_an_idempotent_no_op() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    approve_proposal(proposal.proposal_id, state, now=_NOW)

    result = approve_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is True
    assert result.proposal.status == ProposalStatus.APPROVED


def test_approving_with_no_active_proposal_is_denied() -> None:
    result = approve_proposal("some-id", {}, now=_NOW)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.NO_PENDING_PROPOSAL


# --- REJECTION ----------------------------------------------------------


def test_pending_proposal_can_be_rejected() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)

    result = reject_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is True
    assert result.proposal.status == ProposalStatus.REJECTED


def test_approved_proposal_can_still_be_rejected_before_execution() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    approve_proposal(proposal.proposal_id, state, now=_NOW)

    result = reject_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is True
    assert result.proposal.status == ProposalStatus.REJECTED


def test_rejecting_an_already_rejected_proposal_is_an_idempotent_no_op() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    reject_proposal(proposal.proposal_id, state, now=_NOW)

    result = reject_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is True
    assert result.proposal.status == ProposalStatus.REJECTED


def test_consumed_proposal_cannot_be_rejected() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    approve_proposal(proposal.proposal_id, state, now=_NOW)
    consume_proposal(proposal.proposal_id, state, now=_NOW)

    result = reject_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_CONSUMED


def test_expired_proposal_cannot_be_rejected() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    later = _NOW + timedelta(minutes=11)

    result = reject_proposal(proposal.proposal_id, state, now=later)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_EXPIRED


def test_wrong_proposal_id_is_rejected_for_reject_too() -> None:
    state: dict = {}
    create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)

    result = reject_proposal("not-the-real-id", state, now=_NOW)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_ID_MISMATCH


# --- REPLAY / ONE-TIME USE ------------------------------------------------


def test_successful_consume_changes_status_to_consumed() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    approve_proposal(proposal.proposal_id, state, now=_NOW)

    result = consume_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is True
    assert result.proposal.status == ProposalStatus.CONSUMED
    assert state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "consumed"


def test_consumed_proposal_cannot_authorize_a_second_consume() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)
    approve_proposal(proposal.proposal_id, state, now=_NOW)
    consume_proposal(proposal.proposal_id, state, now=_NOW)

    result = consume_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_CONSUMED


def test_pending_proposal_cannot_be_consumed_without_approval() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)

    result = consume_proposal(proposal.proposal_id, state, now=_NOW)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_NOT_APPROVED


# --- REVISION ---------------------------------------------------------------


def test_revised_proposal_invalidates_prior_approval() -> None:
    state: dict = {}
    first = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hello"}, state, now=_NOW)
    approve_proposal(first.proposal_id, state, now=_NOW)

    second = create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "Hello, revised"}, state, now=_NOW
    )

    assert second.proposal_id != first.proposal_id
    assert second.status == ProposalStatus.PENDING
    # The old (approved) proposal is no longer the session's active one.
    approve_result = approve_proposal(first.proposal_id, state, now=_NOW)
    assert approve_result.success is False
    assert approve_result.reason == ApprovalDenialReason.PROPOSAL_ID_MISMATCH


def test_revised_proposal_gets_a_new_payload_hash() -> None:
    state: dict = {}
    first = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hello"}, state, now=_NOW)
    second = create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "Hello, revised"}, state, now=_NOW
    )

    assert first.payload_hash != second.payload_hash


def test_old_proposal_cannot_authorize_the_new_payload() -> None:
    """Even if the old proposal were somehow still referenced, its
    payload_hash was computed from the OLD payload -- see
    test_approval_policy_gate.py for the full authorize_write-level check.
    This test pins the service-level fact that makes that true: revision
    never reuses or updates the old proposal's hash in place.
    """
    state: dict = {}
    first = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hello"}, state, now=_NOW)
    second = create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "Hello, revised"}, state, now=_NOW
    )

    from backend.approval.canonical import compute_payload_hash

    assert first.payload_hash == compute_payload_hash("teams.sendMessage", {"chatId": "c1", "message": "Hello"})
    assert second.payload_hash != first.payload_hash


# --- SESSION ISOLATION -------------------------------------------------


def test_approval_in_one_session_does_not_authorize_another() -> None:
    session_a: dict = {}
    session_b: dict = {}
    proposal_a = create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "Hi"}, session_a, now=_NOW
    )
    approve_proposal(proposal_a.proposal_id, session_a, now=_NOW)

    # session_b never had anything created/approved in it.
    result = approve_proposal(proposal_a.proposal_id, session_b, now=_NOW)

    assert result.success is False
    assert result.reason == ApprovalDenialReason.NO_PENDING_PROPOSAL
