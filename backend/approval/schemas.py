"""Data contracts for the deterministic Teams write-action approval
framework.

Nothing here executes anything or talks to Teams/Power Automate -- these
are plain data shapes shared by canonical.py (hashing), service.py
(lifecycle transitions), and policy_gate.py (authorization).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class WriteOperation(str, Enum):
    """Teams write operations this milestone's framework can gate.

    Execution of either operation is explicitly out of scope for this
    milestone (instruction: "Do NOT wire createChat or sendMessage
    execution yet.") -- this enum exists only so proposals/approvals can
    be bound to one of a known, closed set of operations, never an
    arbitrary caller-supplied string.
    """

    TEAMS_CREATE_CHAT = "teams.createChat"
    TEAMS_SEND_MESSAGE = "teams.sendMessage"


class ProposalStatus(str, Enum):
    """The minimal proposal lifecycle.

    Deliberately does NOT include a "superseded" status: this framework
    supports exactly one active proposal per session (see
    `service.PENDING_ACTION_PROPOSAL_STATE_KEY`), and creating a new
    proposal always replaces whatever was previously stored there (see
    `service.create_action_proposal`). The instant that happens, the old
    proposal's id no longer matches the session's active proposal, so
    `approve_proposal`/`reject_proposal`/`authorize_write` called with
    that old id already and unavoidably fail (`proposal_id_mismatch` /
    `no_pending_proposal`) -- a distinct "superseded" status would carry
    no additional security meaning, only a cosmetic one, so it is left out
    per instruction ("avoid over-engineering").
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class ApprovalDenialReason(str, Enum):
    """Deterministic, safe-to-surface reasons a proposal transition or a
    write authorization check failed. Never derived from or containing
    any raw exception text, secret, or internal implementation detail --
    mirrors the SafeError philosophy in backend/gateway/safe_error.py.
    """

    NO_PENDING_PROPOSAL = "no_pending_proposal"
    PROPOSAL_NOT_APPROVED = "proposal_not_approved"
    PROPOSAL_EXPIRED = "proposal_expired"
    PROPOSAL_REJECTED = "proposal_rejected"
    PROPOSAL_CONSUMED = "proposal_consumed"
    OPERATION_MISMATCH = "operation_mismatch"
    PAYLOAD_MISMATCH = "payload_mismatch"
    PROPOSAL_ID_MISMATCH = "proposal_id_mismatch"


class ActionProposal(BaseModel):
    """A proposed Teams write action awaiting (or holding) approval.

    `payload` is the exact, execution-relevant argument set for
    `operation` (e.g. `{"chatId": "...", "message": "..."}` for
    `teams.sendMessage`) -- never a secret, never a Power Automate URL
    (instruction: "Do not store Power Automate URLs or credentials.").
    `payload_hash` binds this proposal to that exact payload (see
    canonical.py) -- Python generates it; nothing here accepts a
    caller-supplied hash.
    """

    proposal_id: str
    operation: WriteOperation
    payload: dict[str, Any]
    payload_hash: str
    created_at: datetime
    expires_at: datetime
    status: ProposalStatus
    summary: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable description of the proposed action, suitable "
            "for later display on a SLOPANOC approval card. Contains only "
            "user-visible payload fields -- never a secret or internal "
            "identifier."
        ),
    )
    target_display_name: Optional[str] = Field(
        default=None,
        description=(
            "PRESENTATION ONLY -- the human-readable name of the write "
            "action's destination (e.g. a Teams chat's topic), captured "
            "once at proposal-creation time so it stays fixed for this "
            "proposal's whole lifecycle. Deliberately NOT part of "
            "`payload`: never included in `compute_payload_hash`, never "
            "consulted by `authorize_write`, and never sent to Power "
            "Automate as part of the execution payload -- it exists only "
            "so a frontend approval card can show a real name instead of "
            "a raw internal id or a generic placeholder. Left unset when "
            "no authoritative display name was available at proposal "
            "time (never guessed/invented)."
        ),
    )
