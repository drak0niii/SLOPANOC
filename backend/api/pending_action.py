"""Deterministic mapper: ADK session state's `pending_action_proposal` ->
the safe, frontend-facing `PendingActionDTO`.

Plain Python only -- the model never sees or decides any part of this
mapping (instruction: "This mapper must be Python code, not
model-generated JSON. The model must not decide what security fields are
exposed."). Reuses the same deterministic building blocks already
established for this exact purpose elsewhere in the backend, rather than
re-deriving any of them:
  - `backend.approval.service.load_active_proposal` -- the same parser
    `authorize_write`/`approve_proposal`/`consume_proposal` all use.
  - `backend.approval.service.effective_status` -- the same dynamic-
    expiry-aware status computation the policy gate relies on, so the
    DTO's `status` can never claim "pending" past the real expiry moment
    just because nothing happened to write that back yet.
  - `backend.tools.teams.expiry_presentation` -- the same
    `expires_in_seconds`/`expires_in_minutes` computation already used in
    the propose tools' own model-facing output and the dev CLI.

The underlying `ActionProposal`/session state is read-only here -- this
module never mutates anything.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from backend.api.schemas import OperationalActionDTO, PendingActionDTO
from backend.approval.schemas import OperationalOperation, WriteOperation
from backend.approval.service import effective_status, load_active_proposal
from backend.tools.teams.expiry_presentation import compute_expires_in_minutes, compute_expires_in_seconds


def map_pending_action(session_state: Mapping[str, Any]) -> Optional[PendingActionDTO]:
    """Returns `None` when there is no active proposal in this session's
    state -- the frontend-facing `pending_action` field is then `null`,
    exactly matching "no pending proposal -> pending_action=null".
    """
    proposal = load_active_proposal(session_state)
    if proposal is None:
        return None

    status = effective_status(proposal)
    expires_in_seconds = compute_expires_in_seconds(proposal.expires_at)

    title: Optional[str] = None
    members: list[str] = []
    chat_id: Optional[str] = None
    message: Optional[str] = None
    if proposal.operation == WriteOperation.TEAMS_CREATE_CHAT:
        title = proposal.payload.get("title")
        raw_members = proposal.payload.get("members")
        members = list(raw_members) if isinstance(raw_members, list) else []
    elif proposal.operation == WriteOperation.TEAMS_SEND_MESSAGE:
        chat_id = proposal.payload.get("chatId")
        message = proposal.payload.get("message")

    operational = _operational_details(session_state, proposal) if isinstance(proposal.operation, OperationalOperation) else None

    return PendingActionDTO(
        proposal_id=proposal.proposal_id,
        operation=proposal.operation.value,
        status=status.value,
        summary=proposal.summary,
        title=title,
        members=members,
        chat_id=chat_id,
        message=message,
        expires_at=proposal.expires_at.isoformat(),
        expires_in_seconds=expires_in_seconds,
        expires_in_minutes=compute_expires_in_minutes(expires_in_seconds),
        target_display_name=proposal.target_display_name,
        operational=operational,
    )


def _operational_details(session_state: Mapping[str, Any], proposal: Any) -> Optional[OperationalActionDTO]:
    """Card details from the control plane's trusted context only (never from model text)."""
    from backend.operations.control_plane import load_control
    from backend.operations.targets import load_target_confirmation

    control_id = proposal.payload.get("control_id") if isinstance(proposal.payload, dict) else None
    record = load_control(session_state, control_id)  # type: ignore[arg-type]
    if record is None:
        return None
    context = record.context
    confirmation = load_target_confirmation(session_state, record.confirmation_id)  # type: ignore[arg-type]
    return OperationalActionDTO(
        kind="target_confirmation" if proposal.operation is OperationalOperation.CONFIRM_TARGET else "approval",
        control_id=context.control_id,
        what=context.description or context.intent,
        command=context.command,
        operation_type=context.operation_type,
        risk="state_changing" if context.action_type == "state_change" else "read_only",
        target_type=context.target.target_type,
        target=context.target.display_value,
        reason=context.reason or None,
        source_title=context.source.title,
        source_section=context.source.heading,
        source_id=context.source.canonical_source_id,
        source_version=context.source.version_label,
        restrictions=list(context.restrictions),
        control_stage=record.stage.value,
        approval_status=record.approval_status.value if record.approval_status else None,
        confirmed_by=confirmation.confirmed_by if confirmation else None,
        approved_by=record.approved_by,
        invalidation_reason=record.invalidation_reason,
    )
