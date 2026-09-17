"""POST-6A -- Structured target confirmation.

THE GAP THIS CLOSES: the first cut granted `TargetConfirmation.CONFIRMED`
whenever a later message supplied a parameter whose NAME appeared in an
outstanding question's `missing_context`. That is still an inference over
message content, just a narrower one -- and it is wrong in the ways that
matter:

  - the outstanding question may have been about a DIFFERENT operation by
    the time the answer arrives (topic changed, a new procedure was
    selected, the pending record was rewritten);
  - the governed operation itself may have been EDITED and re-approved
    between the question and the answer, so the thing being confirmed is
    no longer the thing that was asked about;
  - the user may have CORRECTED the value since, leaving an older
    confirmation that still names a superseded target.

WHAT THIS IS: a confirmation is a RECORD, created at the moment of
answering and bound to four things at once -- the pending request it
answers, the governed operation it is for, the exact target parameter and
value, and the candidate revision (the descriptor fingerprint) in force
when it was made. It authorizes only while all four still match. Anything
else is stale, and stale is rejected rather than reinterpreted.

CONFIRMATION IS NOT EXECUTION PERMISSION. A confirmed target says "we
know which unit you mean". It says nothing about whether the runtime may
run anything: `may_emit_command`, the approval boundary and
`may_execute_action` are separate, later, and unaffected by this module.
Conflating them would turn "yes, RRU-3" into consent to act on RRU-3.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.agents.team_manager.request_contract import (
    PendingGovernedRequest,
    RequestParameter,
    TargetConfirmation,
)

__all__ = [
    "TARGET_CONFIRMATION_STATE_KEY",
    "TargetConfirmationRecord",
    "TargetConfirmationRejection",
    "apply_confirmations",
    "build_confirmation",
    "build_target_confirmation_state_update",
    "evaluate_confirmation",
    "parse_target_confirmations",
    "pending_request_fingerprint",
]

TARGET_CONFIRMATION_STATE_KEY = "target_confirmations"
"""Plain, overwritable session-state value -- a list of live
confirmations, rewritten every turn that changes one. Same additive,
single-key idiom as `PENDING_GOVERNED_REQUEST_STATE_KEY`."""


class TargetConfirmationRejection(str, Enum):
    """Why a stored confirmation does not authorize this turn. Closed,
    typed, and safe to log -- never a value or a free-text reason."""

    NONE = "none"
    NO_CONFIRMATION = "no_confirmation"
    PENDING_REQUEST_CHANGED = "pending_request_changed"
    OPERATION_CHANGED = "operation_changed"
    CANDIDATE_REVISION_CHANGED = "candidate_revision_changed"
    VALUE_CHANGED = "value_changed"
    SUPERSEDED_BY_CORRECTION = "superseded_by_correction"


def pending_request_fingerprint(pending: Optional[PendingGovernedRequest]) -> str:
    """Deterministic identity of the outstanding request a confirmation
    answers.

    Covers the governance class, the semantic operation, the answer shape
    and the exact set of things still being asked for. If any of those
    change, the question is a different question -- and an answer to the
    old one must not silently authorize the new one.
    """
    if pending is None:
        return ""
    basis = "\x1f".join(
        (
            pending.request_class,
            pending.intent,
            pending.requested_output,
            pending.subject or "",
            ",".join(sorted(pending.missing_context)),
        )
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class TargetConfirmationRecord(BaseModel):
    """One structured confirmation. Server-built only -- there is no tool
    parameter and no request field through which a model or a user can
    author one directly."""

    model_config = ConfigDict(frozen=True)

    parameter_name: str
    value: str
    pending_request_fingerprint: str
    operation_id: Optional[str] = None
    candidate_revision: Optional[str] = Field(
        default=None,
        description=(
            "The governed descriptor fingerprint in force when this confirmation was made. A re-approved or "
            "edited operation has a different fingerprint, which correctly invalidates a confirmation taken "
            "against the previous revision."
        ),
    )
    confirmed_at: Optional[datetime] = None
    source_turn_id: Optional[str] = Field(
        default=None, description="The invocation this confirmation came from -- provenance, never authority."
    )


def build_confirmation(
    parameter: RequestParameter,
    pending: Optional[PendingGovernedRequest],
    *,
    operation_id: Optional[str],
    candidate_revision: Optional[str],
    source_turn_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> TargetConfirmationRecord:
    """Record that THIS parameter answered THIS outstanding request, for
    THIS operation, at THIS revision. Called only where the runtime has
    already established the relationship deterministically."""
    return TargetConfirmationRecord(
        parameter_name=parameter.name,
        value=parameter.value,
        pending_request_fingerprint=pending_request_fingerprint(pending),
        operation_id=operation_id,
        candidate_revision=candidate_revision,
        confirmed_at=now,
        source_turn_id=source_turn_id,
    )


def evaluate_confirmation(
    record: Optional[TargetConfirmationRecord],
    parameter: RequestParameter,
    pending: Optional[PendingGovernedRequest],
    *,
    operation_id: Optional[str],
    candidate_revision: Optional[str],
) -> "TargetConfirmationRejection":
    """Does `record` still authorize `parameter` as a confirmed target?

    Every binding is re-checked against CURRENT state. Returns `NONE`
    when the confirmation holds, or the typed reason it does not.
    A correction (`corrects_prior_value`) always wins: the user has told
    us the old value was wrong, so no confirmation of it can survive.
    """
    if record is None:
        return TargetConfirmationRejection.NO_CONFIRMATION
    if parameter.corrects_prior_value is not None and record.value == parameter.corrects_prior_value:
        return TargetConfirmationRejection.SUPERSEDED_BY_CORRECTION
    if record.value != parameter.value:
        return TargetConfirmationRejection.VALUE_CHANGED
    if record.pending_request_fingerprint != pending_request_fingerprint(pending):
        return TargetConfirmationRejection.PENDING_REQUEST_CHANGED
    if record.operation_id != operation_id:
        return TargetConfirmationRejection.OPERATION_CHANGED
    if record.candidate_revision != candidate_revision:
        return TargetConfirmationRejection.CANDIDATE_REVISION_CHANGED
    return TargetConfirmationRejection.NONE


def apply_confirmations(
    parameters: Sequence[RequestParameter],
    records: Sequence[TargetConfirmationRecord],
    pending: Optional[PendingGovernedRequest],
    *,
    operation_id: Optional[str],
    candidate_revision: Optional[str],
) -> tuple[list[RequestParameter], dict[str, str]]:
    """Upgrade each parameter to `CONFIRMED` only where a live, matching
    confirmation exists.

    Returns `(parameters, rejections_by_name)`. A parameter with no
    matching confirmation is returned UNCHANGED -- still `MENTIONED`,
    which is what keeps a state-changing operation waiting rather than
    proceeding on an assumption.
    """
    by_name = {record.parameter_name: record for record in records}
    upgraded: list[RequestParameter] = []
    rejections: dict[str, str] = {}
    for parameter in parameters:
        outcome = evaluate_confirmation(
            by_name.get(parameter.name),
            parameter,
            pending,
            operation_id=operation_id,
            candidate_revision=candidate_revision,
        )
        if outcome == TargetConfirmationRejection.NONE:
            upgraded.append(parameter.model_copy(update={"confirmation": TargetConfirmation.CONFIRMED}))
        else:
            if outcome != TargetConfirmationRejection.NO_CONFIRMATION:
                rejections[parameter.name] = outcome.value
            upgraded.append(parameter)
    return upgraded, rejections


def parse_target_confirmations(raw: Any) -> list[TargetConfirmationRecord]:
    """Tolerant, fail-closed parse -- a malformed entry is dropped rather
    than trusted, and a malformed store yields no confirmations at all
    (which keeps targets MENTIONED, the safe direction)."""
    if not isinstance(raw, list):
        return []
    records: list[TargetConfirmationRecord] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            records.append(TargetConfirmationRecord.model_validate(entry))
        except ValidationError:
            continue
    return records


def build_target_confirmation_state_update(records: Sequence[TargetConfirmationRecord]) -> dict[str, object]:
    return {
        TARGET_CONFIRMATION_STATE_KEY: [record.model_dump(mode="json") for record in records] or None
    }
