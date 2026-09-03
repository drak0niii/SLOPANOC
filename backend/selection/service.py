"""The trusted-application-boundary selection lifecycle.

SECURITY CONTRACT, mirroring backend/approval/service.py: every function
here is a plain Python function, never registered as an ADK tool --
choosing/skipping a selection is never something the model can do on the
user's behalf. `create_pending_selection` (called from the deterministic
tool layer, e.g. list_chats.py) is the one exception, exactly like
`create_action_proposal` -- creating a PROPOSAL/SELECTION is safe (it
changes nothing external); only resolving/consuming one is restricted to
the trusted API boundary (backend/api/selection_service.py).

`session_state` throughout is any mutable string-keyed mapping -- this
module reads/writes exactly one key, `PENDING_SELECTION_STATE_KEY`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, MutableMapping, Optional

from backend.selection.schemas import (
    PendingReadIntent,
    PendingSelection,
    ResolvedReadContinuation,
    SelectionKind,
    SelectionOption,
    SelectionStatus,
)
from backend.tools.teams.schemas import ChatSummary

PENDING_SELECTION_STATE_KEY = "pending_teams_selection"

PENDING_READ_CONTINUATION_STATE_KEY = "pending_read_continuation"
"""Production-hardening pass: session-state key for the single-use
`ResolvedReadContinuation` `selection_service.choose()` creates for a
resolved READ selection. Separate from `PENDING_SELECTION_STATE_KEY` --
the `PendingSelection` itself stays RESOLVED (an audit record of what was
asked/chosen); this key is the narrower, consumable "the next turn should
execute this deterministically" instruction, popped exactly once by
`chat_service.py` (see `pop_read_continuation` below).
"""


class SelectionDenialReason(str, Enum):
    """Deterministic, safe-to-surface reasons a selection transition
    failed -- mirrors `ApprovalDenialReason`'s philosophy exactly: never
    derived from a raw exception, never containing anything sensitive.
    """

    NO_PENDING_SELECTION = "no_pending_selection"
    SELECTION_ID_MISMATCH = "selection_id_mismatch"
    SELECTION_NOT_PENDING = "selection_not_pending"
    OPTION_NOT_FOUND = "option_not_found"


@dataclass(frozen=True)
class SelectionResult:
    """The outcome of a selection lifecycle transition (choose/skip) --
    a plain deterministic value, never an exception used for control
    flow. `target` is populated only by a successful `resolve_selection`
    -- the authoritative `{chat_id, topic}` (or future-kind-equivalent)
    the chosen option maps to.
    """

    success: bool
    reason: Optional[SelectionDenialReason] = None
    message: str = ""
    selection: Optional[PendingSelection] = None
    target: Optional[dict[str, Any]] = None


def _now(now: Optional[datetime] = None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def load_active_selection(session_state: MutableMapping[str, Any]) -> Optional[PendingSelection]:
    """Mirrors `approval.service.load_active_proposal` exactly: a missing
    key or a value that fails to parse is "no active selection" -- a safe
    default, never coerced into anything actionable.
    """
    raw = session_state.get(PENDING_SELECTION_STATE_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return PendingSelection.model_validate(raw)
    except Exception:
        return None


def _store_selection(session_state: MutableMapping[str, Any], selection: PendingSelection) -> None:
    session_state[PENDING_SELECTION_STATE_KEY] = selection.model_dump(mode="json")


def create_pending_selection(
    kind: SelectionKind,
    requested_value: str,
    candidates: list[ChatSummary],
    session_state: MutableMapping[str, Any],
    pending_write_message: Optional[str] = None,
    pending_read_intent: Optional[PendingReadIntent] = None,
    now: Optional[datetime] = None,
) -> PendingSelection:
    """Creates a new pending selection and stores it as the session's
    single active one, replacing whatever was there before -- exactly
    like `create_action_proposal` replaces the prior proposal (see
    `ProposalStatus`'s docstring for why no dedicated "superseded" status
    was needed there; the same reasoning applies here: a caller holding
    an old `selection_id` simply stops matching the session's active one,
    and `resolve_selection`/`skip_selection` then fail with
    `SELECTION_ID_MISMATCH`). This is also how a manual chat-name
    correction (instruction section 29) implicitly supersedes an older
    card: the older `selection_id` is no longer active, without needing
    a separate "supersede" call.

    `option_id`s are generated here, deterministically random (`uuid4`)
    -- never derived from or resembling the real chat id, so a client can
    never guess or substitute one (instruction section 32).
    """
    options: list[SelectionOption] = []
    option_targets: dict[str, dict[str, Any]] = {}
    for chat in candidates:
        option_id = str(uuid.uuid4())
        options.append(SelectionOption(option_id=option_id, label=chat.title))
        option_targets[option_id] = {"chat_id": chat.chat_id, "topic": chat.title}

    selection = PendingSelection(
        selection_id=str(uuid.uuid4()),
        kind=kind,
        status=SelectionStatus.PENDING,
        requested_value=requested_value,
        options=options,
        created_at=_now(now),
        option_targets=option_targets,
        pending_write_message=pending_write_message,
        pending_read_intent=pending_read_intent,
    )
    _store_selection(session_state, selection)
    return selection


def resolve_selection(
    selection_id: str, option_id: str, session_state: MutableMapping[str, Any]
) -> SelectionResult:
    """Validates ownership/state/option membership, then marks the
    selection RESOLVED and returns the chosen option's authoritative
    target. Never mutates anything on a denial -- every failure path
    returns before `_store_selection` is ever called (instruction:
    security protections against selection_id/option_id manipulation).
    """
    current = load_active_selection(session_state)
    if current is None:
        return SelectionResult(success=False, reason=SelectionDenialReason.NO_PENDING_SELECTION, message="There is no pending selection to resolve.")
    if current.selection_id != selection_id:
        return SelectionResult(success=False, reason=SelectionDenialReason.SELECTION_ID_MISMATCH, message="This selection is no longer the active one for this session -- it may have been replaced.")
    if current.status != SelectionStatus.PENDING:
        return SelectionResult(success=False, reason=SelectionDenialReason.SELECTION_NOT_PENDING, message="This selection has already been resolved.")
    target = current.option_targets.get(option_id)
    if target is None:
        return SelectionResult(success=False, reason=SelectionDenialReason.OPTION_NOT_FOUND, message="That option does not belong to this selection.")

    resolved = current.model_copy(update={"status": SelectionStatus.RESOLVED})
    _store_selection(session_state, resolved)
    return SelectionResult(success=True, selection=resolved, target=target)


def supersede_active_selection(session_state: MutableMapping[str, Any]) -> Optional[PendingSelection]:
    """Marks the session's active selection SUPERSEDED, if one exists and
    is still PENDING -- a no-op otherwise (no active selection, or it is
    already resolved/skipped/superseded).

    Called when a Teams chat name resolves EXACTLY (list_chats.py's
    `_match`, MATCHED branch) while an earlier disambiguation is still
    open: the conversation has moved on, so the earlier ambiguity is no
    longer meaningful to resolve. Without this, the earlier
    `selection_id` -- though no longer reachable from any fresh
    `selection.pending` event -- would otherwise remain PENDING and
    resolvable indefinitely (a real correctness gap, not just a cosmetic
    one: an old, stale option could still be chosen and would still
    successfully resolve, potentially creating an ActionProposal against
    a chat the user's conversation had already moved past). After this
    call, `resolve_selection`/`skip_selection` against the old
    `selection_id` both fail with `SELECTION_NOT_PENDING`, exactly like
    any other already-resolved selection.

    Deliberately narrow in scope: only called from the MATCHED branch
    (instruction section 29's "manual correction that resolves exactly"
    case). An AMBIGUOUS or plain NOT_FOUND result does not itself
    represent a resolved destination, so it does not supersede an
    existing pending selection -- and a NOT_FOUND-with-candidates result
    already replaces the active selection via `create_pending_selection`
    (see its own docstring), needing no separate call here.
    """
    current = load_active_selection(session_state)
    if current is None or current.status != SelectionStatus.PENDING:
        return None
    superseded = current.model_copy(update={"status": SelectionStatus.SUPERSEDED})
    _store_selection(session_state, superseded)
    return superseded


def store_read_continuation(
    session_state: MutableMapping[str, Any], continuation: ResolvedReadContinuation
) -> None:
    """Stores the single-use continuation `choose()` creates for a resolved
    READ selection -- mirrors `_store_selection`'s plain `model_dump`
    shape. Callers are responsible for including this key in whatever
    `persist_state_delta` call they already make (see
    `api/selection_service.py`'s `choose()`) so it is written atomically
    alongside `selected_teams_chat_id`/`selected_teams_chat_topic`, never
    as a separate write that could race or partially fail.
    """
    session_state[PENDING_READ_CONTINUATION_STATE_KEY] = continuation.model_dump(mode="json")


def pop_read_continuation(
    session_state: MutableMapping[str, Any],
) -> Optional[ResolvedReadContinuation]:
    """Reads AND clears the pending continuation in one call -- the single
    consumption point. A missing key or a value that fails to parse is
    treated as "no continuation" (mirrors `load_active_selection`'s own
    safe-default tolerance), never raised. Callers (`chat_service.py`, at
    the very start of turn processing) MUST persist the clearing this
    causes (`session_state[PENDING_READ_CONTINUATION_STATE_KEY]` is left
    absent after this call) via their own `persist_state_delta`, exactly
    once per turn, BEFORE the turn's Runner call begins -- this is what
    makes the continuation single-use and prevents a cancelled/retried/
    duplicated turn from ever consuming the same continuation twice (see
    that module's own call site for the full rationale).
    """
    raw = session_state.pop(PENDING_READ_CONTINUATION_STATE_KEY, None)
    if not isinstance(raw, dict):
        return None
    try:
        return ResolvedReadContinuation.model_validate(raw)
    except Exception:
        return None


def skip_selection(selection_id: str, session_state: MutableMapping[str, Any]) -> SelectionResult:
    """Same validation as `resolve_selection`, minus the option lookup --
    marks the selection SKIPPED. Never creates a proposal, never selects
    any candidate, never mutates anything on a denial."""
    current = load_active_selection(session_state)
    if current is None:
        return SelectionResult(success=False, reason=SelectionDenialReason.NO_PENDING_SELECTION, message="There is no pending selection to skip.")
    if current.selection_id != selection_id:
        return SelectionResult(success=False, reason=SelectionDenialReason.SELECTION_ID_MISMATCH, message="This selection is no longer the active one for this session -- it may have been replaced.")
    if current.status != SelectionStatus.PENDING:
        return SelectionResult(success=False, reason=SelectionDenialReason.SELECTION_NOT_PENDING, message="This selection has already been resolved.")

    skipped = current.model_copy(update={"status": SelectionStatus.SKIPPED})
    _store_selection(session_state, skipped)
    return SelectionResult(success=True, selection=skipped)
