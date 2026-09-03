"""The trusted-application-boundary proposal lifecycle.

SECURITY CONTRACT (instruction section 2 and 9): every function in this
module is a plain Python function, never registered as an ADK tool on
`team_manager`, `incident_manager`, or any other agent -- grep the agent
definitions (backend/agents/*/agent.py) and there is no
`create_action_proposal`/`approve_proposal`/`reject_proposal` anywhere in
either agent's `tools=[...]`. The model can describe a proposed action to
the user (using the fields listed in `ActionProposal`), but it has no
callable path to move a proposal's `status` itself. In production, only a
trusted application boundary (a future SLOPANOC approval-card backend
endpoint -- not built in this milestone, see policy_gate.py's module
docstring) is expected to call `approve_proposal`/`reject_proposal`, and
only ever with a `session_state` it obtained from ADK's own session
storage for the user's own session -- never from model output.

`session_state` throughout this module is any mutable string-keyed
mapping (a plain `dict`, an ADK `google.adk.sessions.state.State`, or
`ToolContext.state`) -- this module reads/writes exactly one key,
`PENDING_ACTION_PROPOSAL_STATE_KEY`, and never a parallel/second memory
system.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, MutableMapping, Optional

from backend.approval.canonical import compute_payload_hash
from backend.approval.schemas import ActionProposal, ApprovalDenialReason, ProposalStatus, WriteOperation
from backend.config.settings import get_settings

PENDING_ACTION_PROPOSAL_STATE_KEY = "pending_action_proposal"

_SUPPORTED_OPERATIONS = {op.value for op in WriteOperation}


class UnsupportedOperationError(ValueError):
    """`operation` is not one of the closed set of supported Teams write
    operations (`WriteOperation`). Raised only for a caller/programming
    error (a trusted boundary passing an unrecognized operation string),
    never something a model can trigger with natural language.
    """


@dataclass(frozen=True)
class ProposalResult:
    """The outcome of a proposal lifecycle transition
    (approve/reject/consume) -- a plain deterministic value, never an
    exception used for control flow, so trusted-boundary callers (and
    tests) can inspect a denial without a try/except.
    """

    success: bool
    reason: Optional[ApprovalDenialReason] = None
    message: str = ""
    proposal: Optional[ActionProposal] = None


def _now(now: Optional[datetime] = None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def load_active_proposal(session_state: MutableMapping[str, Any]) -> Optional[ActionProposal]:
    """Read and parse the single active proposal from session state, if
    any. A missing key or a value that fails to parse as `ActionProposal`
    are both treated as "no active proposal" -- corrupted/malformed state
    is a safe default-deny condition, never something that could be
    coerced into an authorization.
    """
    raw = session_state.get(PENDING_ACTION_PROPOSAL_STATE_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return ActionProposal.model_validate(raw)
    except Exception:
        return None


def _store_proposal(session_state: MutableMapping[str, Any], proposal: ActionProposal) -> None:
    session_state[PENDING_ACTION_PROPOSAL_STATE_KEY] = proposal.model_dump(mode="json")


def effective_status(proposal: ActionProposal, now: Optional[datetime] = None) -> ProposalStatus:
    """The proposal's status as of `now`, independent of what was last
    persisted: an unexpired stored status is returned as-is, but a
    `pending`/`approved` proposal whose `expires_at` has passed is always
    reported as `expired`, regardless of whether anything ever wrote that
    back to state (instruction: "Policy gate must detect expiry even if
    proposal status was never explicitly updated."). `rejected`/`consumed`
    are terminal and are never overridden by expiry.
    """
    if proposal.status in (ProposalStatus.REJECTED, ProposalStatus.CONSUMED):
        return proposal.status
    if _now(now) >= proposal.expires_at:
        return ProposalStatus.EXPIRED
    return proposal.status


def create_action_proposal(
    operation: str,
    payload: dict[str, Any],
    session_state: MutableMapping[str, Any],
    summary: Optional[str] = None,
    target_display_name: Optional[str] = None,
    now: Optional[datetime] = None,
) -> ActionProposal:
    """Create a new pending `ActionProposal` and store it as the session's
    single active proposal, replacing whatever was there before (see
    `ProposalStatus`'s docstring for why a prior proposal -- pending or
    even already-approved -- is safely invalidated by this, without a
    dedicated "superseded" status: its id simply stops matching the
    session's active proposal).

    Every execution-relevant value (`proposal_id`, `payload_hash`,
    `created_at`, `expires_at`) is generated here, deterministically, in
    Python -- never supplied by or negotiated with the model (instruction:
    "Do not involve Gemini in hash/id/time generation.").

    `target_display_name`, like `summary`, is presentation-only -- see
    `ActionProposal.target_display_name`'s own docstring -- and plays no
    part in `payload`/`payload_hash` computation above.
    """
    if operation not in _SUPPORTED_OPERATIONS:
        raise UnsupportedOperationError(
            f"{operation!r} is not a supported Teams write operation "
            f"(supported: {sorted(_SUPPORTED_OPERATIONS)})."
        )

    created_at = _now(now)
    expiry_seconds = get_settings().action_proposal_expiry_seconds
    expires_at = created_at + timedelta(seconds=expiry_seconds)
    payload_hash = compute_payload_hash(operation, payload)

    proposal = ActionProposal(
        proposal_id=str(uuid.uuid4()),
        operation=WriteOperation(operation),
        payload=payload,
        payload_hash=payload_hash,
        created_at=created_at,
        expires_at=expires_at,
        status=ProposalStatus.PENDING,
        summary=summary,
        target_display_name=target_display_name,
    )
    _store_proposal(session_state, proposal)
    return proposal


def approve_proposal(
    proposal_id: str,
    session_state: MutableMapping[str, Any],
    now: Optional[datetime] = None,
) -> ProposalResult:
    """The trusted application boundary's APPROVE transition. Never call
    this from model-facing/tool code (see module docstring).
    """
    proposal = load_active_proposal(session_state)
    if proposal is None:
        return ProposalResult(
            success=False,
            reason=ApprovalDenialReason.NO_PENDING_PROPOSAL,
            message="There is no active proposal to approve.",
        )
    if proposal.proposal_id != proposal_id:
        return ProposalResult(
            success=False,
            reason=ApprovalDenialReason.PROPOSAL_ID_MISMATCH,
            message="This proposal is no longer the active one for this session.",
        )

    status = effective_status(proposal, now)
    if status == ProposalStatus.CONSUMED:
        return ProposalResult(
            success=False, reason=ApprovalDenialReason.PROPOSAL_CONSUMED, message="This proposal was already used."
        )
    if status == ProposalStatus.REJECTED:
        return ProposalResult(
            success=False,
            reason=ApprovalDenialReason.PROPOSAL_REJECTED,
            message="This proposal was already rejected and cannot be approved.",
        )
    if status == ProposalStatus.EXPIRED:
        return ProposalResult(
            success=False, reason=ApprovalDenialReason.PROPOSAL_EXPIRED, message="This proposal has expired."
        )
    if status == ProposalStatus.APPROVED:
        # Idempotent no-op: this exact proposal is already approved.
        return ProposalResult(success=True, proposal=proposal)

    approved = proposal.model_copy(update={"status": ProposalStatus.APPROVED})
    _store_proposal(session_state, approved)
    return ProposalResult(success=True, proposal=approved)


def reject_proposal(
    proposal_id: str,
    session_state: MutableMapping[str, Any],
    now: Optional[datetime] = None,
) -> ProposalResult:
    """The trusted application boundary's REJECT transition. Allowed from
    `pending` or `approved` (a user may still cancel an approved-but-not-
    yet-executed action) -- never from `consumed` (already executed) or
    `expired` (already unusable). Never call this from model-facing/tool
    code (see module docstring).
    """
    proposal = load_active_proposal(session_state)
    if proposal is None:
        return ProposalResult(
            success=False,
            reason=ApprovalDenialReason.NO_PENDING_PROPOSAL,
            message="There is no active proposal to reject.",
        )
    if proposal.proposal_id != proposal_id:
        return ProposalResult(
            success=False,
            reason=ApprovalDenialReason.PROPOSAL_ID_MISMATCH,
            message="This proposal is no longer the active one for this session.",
        )

    status = effective_status(proposal, now)
    if status == ProposalStatus.CONSUMED:
        return ProposalResult(
            success=False, reason=ApprovalDenialReason.PROPOSAL_CONSUMED, message="This proposal was already used."
        )
    if status == ProposalStatus.EXPIRED:
        return ProposalResult(
            success=False, reason=ApprovalDenialReason.PROPOSAL_EXPIRED, message="This proposal has expired."
        )
    if status == ProposalStatus.REJECTED:
        # Idempotent no-op: this exact proposal is already rejected.
        return ProposalResult(success=True, proposal=proposal)

    rejected = proposal.model_copy(update={"status": ProposalStatus.REJECTED})
    _store_proposal(session_state, rejected)
    return ProposalResult(success=True, proposal=rejected)


def consume_proposal(
    proposal_id: str,
    session_state: MutableMapping[str, Any],
    now: Optional[datetime] = None,
) -> ProposalResult:
    """Mark an approved proposal as `consumed` -- the one-time-use/replay
    protection primitive (instruction section 11). NOT wired to any Teams
    write execution in this milestone: this function exists so the future
    write layer can call it (once execution exists) immediately after a
    successful `teams.createChat`/`teams.sendMessage` call, and never
    before, and never merely because `authorize_write` passed (instruction:
    "Do not automatically consume on policy-check alone.").

    Unlike `approve_proposal`/`reject_proposal`, this is intentionally NOT
    idempotent on an already-consumed proposal -- a second `consume_proposal`
    call for the same proposal signals a potential double-execution attempt
    and must be denied, not silently accepted.
    """
    proposal = load_active_proposal(session_state)
    if proposal is None:
        return ProposalResult(
            success=False,
            reason=ApprovalDenialReason.NO_PENDING_PROPOSAL,
            message="There is no active proposal to consume.",
        )
    if proposal.proposal_id != proposal_id:
        return ProposalResult(
            success=False,
            reason=ApprovalDenialReason.PROPOSAL_ID_MISMATCH,
            message="This proposal is no longer the active one for this session.",
        )

    status = effective_status(proposal, now)
    if status == ProposalStatus.CONSUMED:
        return ProposalResult(
            success=False, reason=ApprovalDenialReason.PROPOSAL_CONSUMED, message="This proposal was already used."
        )
    if status == ProposalStatus.REJECTED:
        return ProposalResult(
            success=False, reason=ApprovalDenialReason.PROPOSAL_REJECTED, message="This proposal was rejected."
        )
    if status == ProposalStatus.EXPIRED:
        return ProposalResult(
            success=False, reason=ApprovalDenialReason.PROPOSAL_EXPIRED, message="This proposal has expired."
        )
    if status == ProposalStatus.PENDING:
        return ProposalResult(
            success=False,
            reason=ApprovalDenialReason.PROPOSAL_NOT_APPROVED,
            message="This proposal has not been approved.",
        )

    consumed = proposal.model_copy(update={"status": ProposalStatus.CONSUMED})
    _store_proposal(session_state, consumed)
    return ProposalResult(success=True, proposal=consumed)
