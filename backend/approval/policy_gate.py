"""The deterministic write-authorization check.

This is the one function a future Teams write tool (`teams.createChat`/
`teams.sendMessage` execution -- not built in this milestone) must call,
and its answer must be the *only* thing that gates execution. It never
trusts the model's own claim that "the user approved this" -- it only
trusts what is actually recorded in `session_state` by the trusted
approve/reject boundary in service.py, which is itself never reachable
from model output (see service.py's module docstring).

`authorize_write` is a pure, read-only check: it never mutates
`session_state` and never consumes the proposal itself (see
`service.consume_proposal` -- consumption is a separate, deliberate step
the future write layer performs only after execution actually succeeds).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, MutableMapping, Optional

from backend.approval.canonical import compute_payload_hash
from backend.approval.schemas import ActionProposal, ApprovalDenialReason, ProposalStatus
from backend.approval.service import load_active_proposal, effective_status


@dataclass(frozen=True)
class AuthorizationResult:
    """The policy gate's answer. `authorized=False` always carries a safe
    `reason`/`message` -- never a raw exception, never a hint that could
    help an attacker guess the correct payload (e.g. `message` never
    echoes the expected hash or the stored payload).
    """

    authorized: bool
    reason: Optional[ApprovalDenialReason] = None
    message: str = ""
    proposal: Optional[ActionProposal] = None


def authorize_write(
    operation: str,
    payload: Mapping[str, Any],
    session_state: MutableMapping[str, Any],
    now: Optional[datetime] = None,
) -> AuthorizationResult:
    """Deterministically decide whether `operation` may execute right now
    with exactly `payload`, based only on `session_state`'s single active
    proposal. See module docstring for the trust model.
    """
    proposal = load_active_proposal(session_state)
    if proposal is None:
        return AuthorizationResult(
            authorized=False,
            reason=ApprovalDenialReason.NO_PENDING_PROPOSAL,
            message="There is no approved action for this session.",
        )

    status = effective_status(proposal, now)
    if status == ProposalStatus.CONSUMED:
        return AuthorizationResult(
            authorized=False,
            reason=ApprovalDenialReason.PROPOSAL_CONSUMED,
            message="This approval has already been used.",
        )
    if status == ProposalStatus.REJECTED:
        return AuthorizationResult(
            authorized=False,
            reason=ApprovalDenialReason.PROPOSAL_REJECTED,
            message="This action was rejected.",
        )
    if status == ProposalStatus.EXPIRED:
        return AuthorizationResult(
            authorized=False,
            reason=ApprovalDenialReason.PROPOSAL_EXPIRED,
            message="The approval for this action has expired.",
        )
    if status == ProposalStatus.PENDING:
        return AuthorizationResult(
            authorized=False,
            reason=ApprovalDenialReason.PROPOSAL_NOT_APPROVED,
            message="This action has not been approved yet.",
        )

    # status == ProposalStatus.APPROVED from here on.
    if proposal.operation.value != operation:
        return AuthorizationResult(
            authorized=False,
            reason=ApprovalDenialReason.OPERATION_MISMATCH,
            message="The approved action does not match the requested operation.",
        )

    if compute_payload_hash(operation, payload) != proposal.payload_hash:
        return AuthorizationResult(
            authorized=False,
            reason=ApprovalDenialReason.PAYLOAD_MISMATCH,
            message="The requested action no longer matches what was approved.",
        )

    return AuthorizationResult(authorized=True, message="Authorized.", proposal=proposal)
