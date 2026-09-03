"""Tests for the deterministic write-authorization check,
`backend.approval.policy_gate.authorize_write`.

This is the function a future Teams write tool must treat as the sole
gate on execution -- these tests exercise every denial path in section 10
of the instruction, plus the section 12 "modified payload after approval"
case in detail (multiple mutation shapes: changed/added/removed/nested).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.approval.policy_gate import authorize_write
from backend.approval.schemas import ApprovalDenialReason
from backend.approval.service import approve_proposal, consume_proposal, create_action_proposal, reject_proposal

_NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
_OPERATION = "teams.sendMessage"
_PAYLOAD = {"chatId": "c1", "message": "Hello"}


def _approved_session() -> dict:
    state: dict = {}
    proposal = create_action_proposal(_OPERATION, _PAYLOAD, state, now=_NOW)
    approve_proposal(proposal.proposal_id, state, now=_NOW)
    return state


def test_approved_exact_operation_and_payload_is_authorized() -> None:
    state = _approved_session()

    result = authorize_write(_OPERATION, _PAYLOAD, state, now=_NOW)

    assert result.authorized is True
    assert result.reason is None


def test_pending_proposal_is_denied() -> None:
    state: dict = {}
    create_action_proposal(_OPERATION, _PAYLOAD, state, now=_NOW)

    result = authorize_write(_OPERATION, _PAYLOAD, state, now=_NOW)

    assert result.authorized is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_NOT_APPROVED


def test_rejected_proposal_is_denied() -> None:
    state: dict = {}
    proposal = create_action_proposal(_OPERATION, _PAYLOAD, state, now=_NOW)
    reject_proposal(proposal.proposal_id, state, now=_NOW)

    result = authorize_write(_OPERATION, _PAYLOAD, state, now=_NOW)

    assert result.authorized is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_REJECTED


def test_expired_proposal_is_denied_even_though_status_still_says_approved() -> None:
    """Pins instruction section 7's "Policy gate must detect expiry even
    if proposal status was never explicitly updated": approve at creation
    time, then check authorization strictly after the expiry window with
    no intervening status write.
    """
    state = _approved_session()
    later = _NOW + timedelta(minutes=11)

    result = authorize_write(_OPERATION, _PAYLOAD, state, now=later)

    assert result.authorized is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_EXPIRED


def test_consumed_proposal_is_denied() -> None:
    state = _approved_session()
    from backend.approval.service import load_active_proposal

    proposal = load_active_proposal(state)
    consume_proposal(proposal.proposal_id, state, now=_NOW)

    result = authorize_write(_OPERATION, _PAYLOAD, state, now=_NOW)

    assert result.authorized is False
    assert result.reason == ApprovalDenialReason.PROPOSAL_CONSUMED


def test_no_proposal_at_all_is_denied() -> None:
    result = authorize_write(_OPERATION, _PAYLOAD, {}, now=_NOW)

    assert result.authorized is False
    assert result.reason == ApprovalDenialReason.NO_PENDING_PROPOSAL


def test_different_operation_is_denied() -> None:
    state = _approved_session()

    result = authorize_write("teams.createChat", _PAYLOAD, state, now=_NOW)

    assert result.authorized is False
    assert result.reason == ApprovalDenialReason.OPERATION_MISMATCH


# --- Modified payload after approval (instruction section 12) -----------


def test_changed_payload_value_is_denied() -> None:
    state = _approved_session()

    result = authorize_write(
        _OPERATION, {"chatId": "c1", "message": "Hello and delete everything"}, state, now=_NOW
    )

    assert result.authorized is False
    assert result.reason == ApprovalDenialReason.PAYLOAD_MISMATCH


def test_added_payload_field_is_denied() -> None:
    state = _approved_session()

    result = authorize_write(
        _OPERATION, {"chatId": "c1", "message": "Hello", "urgent": True}, state, now=_NOW
    )

    assert result.authorized is False
    assert result.reason == ApprovalDenialReason.PAYLOAD_MISMATCH


def test_removed_payload_field_is_denied() -> None:
    state: dict = {}
    proposal = create_action_proposal(
        _OPERATION, {"chatId": "c1", "message": "Hello", "urgent": True}, state, now=_NOW
    )
    approve_proposal(proposal.proposal_id, state, now=_NOW)

    result = authorize_write(_OPERATION, {"chatId": "c1", "message": "Hello"}, state, now=_NOW)

    assert result.authorized is False
    assert result.reason == ApprovalDenialReason.PAYLOAD_MISMATCH


def test_nested_payload_change_is_denied() -> None:
    state: dict = {}
    proposal = create_action_proposal(
        "teams.createChat", {"title": "Ops", "options": {"notify": True}}, state, now=_NOW
    )
    approve_proposal(proposal.proposal_id, state, now=_NOW)

    result = authorize_write(
        "teams.createChat", {"title": "Ops", "options": {"notify": False}}, state, now=_NOW
    )

    assert result.authorized is False
    assert result.reason == ApprovalDenialReason.PAYLOAD_MISMATCH


def test_exact_unmodified_payload_after_nested_content_is_authorized() -> None:
    """Sanity counterpart to the mutation tests above -- confirms the
    denials are about the change, not about nested payloads in general.
    """
    state: dict = {}
    proposal = create_action_proposal(
        "teams.createChat", {"title": "Ops", "options": {"notify": True}}, state, now=_NOW
    )
    approve_proposal(proposal.proposal_id, state, now=_NOW)

    result = authorize_write("teams.createChat", {"title": "Ops", "options": {"notify": True}}, state, now=_NOW)

    assert result.authorized is True


def test_dict_key_order_in_the_authorize_call_does_not_cause_a_false_denial() -> None:
    state = _approved_session()

    result = authorize_write(_OPERATION, {"message": "Hello", "chatId": "c1"}, state, now=_NOW)

    assert result.authorized is True


# --- Revision-related denial (ties service revision to the policy gate) --


def test_old_proposal_hash_does_not_authorize_a_revised_payload() -> None:
    state: dict = {}
    first = create_action_proposal(_OPERATION, {"chatId": "c1", "message": "Hello"}, state, now=_NOW)
    approve_proposal(first.proposal_id, state, now=_NOW)

    # User revises the message before the first is ever executed.
    create_action_proposal(_OPERATION, {"chatId": "c1", "message": "Hello, revised"}, state, now=_NOW)

    # The new proposal is pending (not yet approved) -- the old payload
    # must not be authorized by whatever is now active.
    result = authorize_write(_OPERATION, {"chatId": "c1", "message": "Hello"}, state, now=_NOW)

    assert result.authorized is False
    assert result.reason in (ApprovalDenialReason.PROPOSAL_NOT_APPROVED, ApprovalDenialReason.PAYLOAD_MISMATCH)


def test_authorization_result_never_echoes_the_expected_hash_or_payload() -> None:
    state = _approved_session()

    result = authorize_write(
        _OPERATION, {"chatId": "c1", "message": "Hello and delete everything"}, state, now=_NOW
    )

    assert "delete everything" not in result.message
    from backend.approval.service import load_active_proposal

    stored = load_active_proposal(state)
    assert stored.payload_hash not in result.message
