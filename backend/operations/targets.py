"""Server-owned target confirmation.

A TargetConfirmation exists ONLY because an operator explicitly confirmed a specific target for
a specific action context through the trusted approve endpoint (see control_plane.py). It is
never derived from model prose, TAE assertions, conversation meaning, command text, or the mere
presence of a verified parameter value.

It is bound to (control/check, session, procedure_action_id, target, binding_hash). Any change
to the action, command, parameters, target, or governed source version/text changes the
binding hash, so the confirmation stops matching -- it is never reused for another target or
another action, and it is never a global "target_confirmed" flag.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, MutableMapping, Optional

from pydantic import BaseModel

from backend.config.settings import get_settings
from backend.operations.context import OperationalActionContext, TargetIdentity

TARGET_CONFIRMATIONS_STATE_KEY = "operational_target_confirmations"


class TargetConfirmationStatus(str, Enum):
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"


class TargetConfirmation(BaseModel):
    confirmation_id: str
    control_id: str
    session_id: Optional[str] = None
    case_id: Optional[str] = None
    check_id: Optional[str] = None
    procedure_action_id: str
    target: TargetIdentity
    binding_hash: str
    command_hash: str
    source_canonical_id: str
    source_version: str
    confirmed_by: str
    confirmed_at: datetime
    expires_at: datetime
    status: TargetConfirmationStatus = TargetConfirmationStatus.CONFIRMED
    invalidation_reason: Optional[str] = None


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _load_all(state: MutableMapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = state.get(TARGET_CONFIRMATIONS_STATE_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def create_target_confirmation(
    state: MutableMapping[str, Any],
    context: OperationalActionContext,
    confirmed_by: str,
    now: Optional[datetime] = None,
) -> TargetConfirmation:
    """Record an explicit operator confirmation. Callers: the trusted approve endpoint only."""
    if not confirmed_by:
        raise ValueError("a target confirmation requires the confirming user's identity")
    at = _now(now)
    confirmation = TargetConfirmation(
        confirmation_id=str(uuid.uuid4()),
        control_id=context.control_id,
        session_id=context.session_id,
        case_id=context.case_id,
        check_id=context.check_id,
        procedure_action_id=context.procedure_action_id,
        target=context.target,
        binding_hash=context.binding_hash(),
        command_hash=context.command_hash(),
        source_canonical_id=context.source.canonical_source_id,
        source_version=context.source.version_label,
        confirmed_by=confirmed_by,
        confirmed_at=at,
        expires_at=at + timedelta(seconds=get_settings().action_proposal_expiry_seconds),
    )
    records = _load_all(state)
    records[confirmation.confirmation_id] = confirmation.model_dump(mode="json")
    state[TARGET_CONFIRMATIONS_STATE_KEY] = records
    return confirmation


def load_target_confirmation(state: MutableMapping[str, Any], confirmation_id: Optional[str]) -> Optional[TargetConfirmation]:
    if not confirmation_id:
        return None
    raw = _load_all(state).get(confirmation_id)
    if not isinstance(raw, dict):
        return None
    try:
        return TargetConfirmation.model_validate(raw)
    except Exception:
        return None


def confirmation_mismatch(
    confirmation: Optional[TargetConfirmation],
    context: OperationalActionContext,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """None when `confirmation` is usable for exactly `context`; else a stable reason code."""
    if confirmation is None:
        return "TARGET_NOT_CONFIRMED"
    if confirmation.status is not TargetConfirmationStatus.CONFIRMED:
        return "TARGET_CONFIRMATION_INVALIDATED"
    if _now(now) >= confirmation.expires_at:
        return "TARGET_CONFIRMATION_EXPIRED"
    if confirmation.control_id != context.control_id or confirmation.check_id != context.check_id:
        return "TARGET_CONFIRMATION_OTHER_ACTION"
    if confirmation.procedure_action_id != context.procedure_action_id:
        return "TARGET_CONFIRMATION_OTHER_ACTION"
    if confirmation.target.canonical_identifier != context.target.canonical_identifier or confirmation.target.target_type != context.target.target_type:
        return "TARGET_CONFIRMATION_OTHER_TARGET"
    if confirmation.command_hash != context.command_hash():
        return "TARGET_CONFIRMATION_COMMAND_CHANGED"
    if confirmation.source_canonical_id != context.source.canonical_source_id or confirmation.source_version != context.source.version_label:
        return "TARGET_CONFIRMATION_SOURCE_CHANGED"
    if confirmation.binding_hash != context.binding_hash():
        return "TARGET_CONFIRMATION_BINDING_CHANGED"
    return None


def invalidate_target_confirmation(state: MutableMapping[str, Any], confirmation_id: str, reason: str) -> None:
    records = _load_all(state)
    raw = records.get(confirmation_id)
    if isinstance(raw, dict):
        raw = dict(raw)
        raw["status"] = TargetConfirmationStatus.INVALIDATED.value
        raw["invalidation_reason"] = reason
        records[confirmation_id] = raw
        state[TARGET_CONFIRMATIONS_STATE_KEY] = records
