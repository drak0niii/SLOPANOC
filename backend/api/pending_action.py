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

from backend.api.schemas import PendingActionDTO
from backend.approval.schemas import WriteOperation
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
    )
