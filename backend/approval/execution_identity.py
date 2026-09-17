"""POST-6A -- Durable execution identity for Teams writes.

THE GAP THIS CLOSES: a write was dispatched with nothing persisted
beforehand. If the gateway call timed out, or the response was lost after
Power Automate had already sent the message, the backend had no record
that a dispatch had even been attempted -- so it could neither report the
ambiguity nor prevent a second attempt that might double-send.

WHAT THIS IS: one durable record, written BEFORE dispatch, carrying a
stable operation id bound to the things that make this dispatch THIS
dispatch:

    proposal identity  -- which approved proposal
    destination        -- where it goes
    payload hash       -- exactly what is sent

Any change to those is a different operation and gets a different id. The
id is derived, not random, so the same proposal+destination+payload
always produces the same id across attempts and across restarts.

UNKNOWN_OUTCOME IS THE POINT. A timeout or a lost response after the
request left the process is genuinely ambiguous: the message may have
been sent. Recording UNKNOWN_OUTCOME says exactly that, and BLOCKS a
blind retry -- because retrying an operation that may already have
succeeded is how a single approved message becomes two.

WE DO NOT CLAIM EXACTLY-ONCE. Application code cannot provide it: the
gateway would have to deduplicate on our operation id and expose a
reconciliation lookup. Neither exists in the current flow, so
`retry_permitted` returns `False` for UNKNOWN_OUTCOME and the honest
resolution is a human checking Teams. `gateway_deduplication_available`
is the single switch a future, genuinely-deduplicating flow would flip --
it is not a code change to this module's rules.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = [
    "EXECUTION_RECORD_STATE_KEY",
    "ExecutionRecord",
    "ExecutionStatus",
    "build_execution_record_delta",
    "derive_operation_id",
    "parse_execution_record",
    "payload_hash",
    "retry_permitted",
]

EXECUTION_RECORD_STATE_KEY = "action_execution_record"
"""One record per session for the CURRENT proposal's execution. Plain and
overwritable: a new proposal is a new operation, and the previous
record's job is done once its own proposal is no longer active."""


class ExecutionStatus(str, Enum):
    PREPARED = "prepared"
    """Persisted BEFORE dispatch. Seeing this on load means a dispatch was
    about to happen, or was in flight when the process stopped."""

    DISPATCHED = "dispatched"
    """The request left this process. Whether it took effect is not yet
    known."""

    SUCCEEDED = "succeeded"
    """The gateway returned a VALIDATED success envelope."""

    FAILED = "failed"
    """The gateway positively reported failure, or refused the request.
    Safe to treat as not executed."""

    UNKNOWN_OUTCOME = "unknown_outcome"
    """Dispatched, and then a timeout or a lost response. May or may not
    have executed. NOT safe to retry."""


def payload_hash(payload: Any) -> str:
    """Stable hash of the exact payload being sent. Canonical JSON with
    sorted keys, so an equivalent payload always hashes the same and a
    changed one never does."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def derive_operation_id(proposal_id: str, operation: str, destination: str, payload: Any) -> str:
    """The stable operation id.

    DERIVED, never random: the same proposal, operation, destination and
    payload always yield the same id, so an attempt after a restart is
    recognisably the SAME operation rather than a new one. Any difference
    in any of the four produces a different id -- which is exactly what
    should happen, because it is then a different operation.
    """
    basis = "\x1f".join((proposal_id, operation, destination or "", payload_hash(payload)))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation_id: str
    proposal_id: str
    operation: str
    destination: str
    payload_hash: str
    status: ExecutionStatus
    attempts: int = 0
    detail: str = ""
    """Safe diagnostic -- a typed reason or short phrase, never a gateway
    body, a URL, or a message body."""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def matches(self, proposal_id: str, operation: str, destination: str, payload: Any) -> bool:
        """Is this record about the operation now being attempted?
        Compared on every bound field -- a record for a different
        destination or payload is a different operation and must not be
        reused."""
        return (
            self.proposal_id == proposal_id
            and self.operation == operation
            and self.destination == destination
            and self.payload_hash == payload_hash(payload)
        )

    @property
    def is_ambiguous(self) -> bool:
        return self.status in (ExecutionStatus.DISPATCHED, ExecutionStatus.UNKNOWN_OUTCOME)


def parse_execution_record(raw: Any) -> Optional[ExecutionRecord]:
    """Tolerant, fail-closed parse -- malformed state yields `None`,
    meaning "no prior attempt known", which is the conservative reading
    only because every caller re-derives the operation id from the
    proposal itself rather than trusting this record for identity."""
    if not isinstance(raw, dict):
        return None
    try:
        return ExecutionRecord.model_validate(raw)
    except ValidationError:
        return None


def build_execution_record_delta(record: Optional[ExecutionRecord]) -> dict[str, object]:
    return {EXECUTION_RECORD_STATE_KEY: record.model_dump(mode="json") if record is not None else None}


def retry_permitted(record: Optional[ExecutionRecord], *, gateway_deduplication_available: bool = False) -> bool:
    """May this operation be dispatched (again)?

    - no record, or PREPARED          -> yes. Nothing left this process.
    - FAILED                          -> yes. The gateway positively said
                                         it did not execute.
    - SUCCEEDED                       -> no. It is done.
    - DISPATCHED / UNKNOWN_OUTCOME    -> ONLY if the gateway can
                                         deduplicate on our operation id.
                                         Otherwise NO: a blind retry of
                                         something that may already have
                                         sent is how one approved message
                                         becomes two.

    `gateway_deduplication_available` is `False` today and is the single
    switch a genuinely deduplicating flow would flip. It is a statement
    about the GATEWAY, never a preference of this module.
    """
    if record is None:
        return True
    if record.status in (ExecutionStatus.PREPARED, ExecutionStatus.FAILED):
        return True
    if record.status is ExecutionStatus.SUCCEEDED:
        return False
    return bool(gateway_deduplication_available)
