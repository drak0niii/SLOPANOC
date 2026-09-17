"""POST-6A -- One consistent failure boundary for the whole turn.

THE GAP THIS CLOSES: generation, finalization and persistence each had
their own failure handling, and none of them recorded that a turn had
been ACCEPTED. So a turn that died between "the Runner produced text" and
"the canonical result is durably stored" left nothing behind at all: no
completion, no failure, no record that anything had been attempted. On
the next load it was simply absent -- indistinguishable from a turn that
never happened, and impossible to reconcile.

WHAT THIS IS: one small, durable per-turn status, written through the
session-state mechanism every other piece of turn bookkeeping already
uses. Four terminal outcomes and one non-terminal one:

    ACCEPTED    -- the turn was admitted and is in flight. NOT terminal.
                   A turn found in this state on a LATER turn's load was
                   interrupted (process death, storage outage mid-write)
                   and is reconciled to INTERRUPTED.
    COMPLETED   -- canonical result durably persisted, `message.completed`
                   emitted.
    FAILED      -- a safe terminal error event was emitted instead.
    CANCELLED   -- the user stopped it.
    INTERRUPTED -- recoverably incomplete: we know it was accepted, we
                   know it never reached a terminal state, and we do NOT
                   know whether its work took effect.

WHY INTERRUPTED IS NOT FAILED: claiming a turn failed asserts that it did
not take effect. After a storage outage mid-finalization we cannot assert
that. INTERRUPTED says exactly what is true -- accepted, not completed,
outcome unknown -- which is the honest input to reconciliation.

NEVER A HISTORY FALLBACK: this status says whether a turn reached a
terminal state. It is never a source of assistant text, and it never
licenses displaying raw ADK output -- `session_history_service` continues
to fail closed for a canonical-required turn with no canonical result.
"""
from __future__ import annotations

import os
import socket
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = [
    "TURN_LIFECYCLE_STATE_KEY",
    "WORKER_ID",
    "TurnLifecycleRecord",
    "TurnStatus",
    "build_turn_lifecycle_delta",
    "turn_ownership_identity",
    "parse_turn_lifecycle",
    "reconcile_interrupted_turns",
]

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
"""This process's own worker identity. Host + pid is enough to tell
two workers apart on one machine and across machines, and it changes
on restart -- which is correct: a restarted process is not the worker
that accepted the turn before the crash."""

TURN_LIFECYCLE_STATE_KEY = "turn_lifecycle"
"""`{turn_key: {...}}`. Accumulating, like `TURN_SOURCE_REFERENCES_STATE_
KEY` -- each turn keeps its own outcome rather than overwriting the
previous one, because "was that turn completed?" must stay answerable
after later turns have run."""


class TurnStatus(str, Enum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


_TERMINAL = frozenset({TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED, TurnStatus.INTERRUPTED})


class TurnLifecycleRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn_key: str
    """The turn's own `run_id` -- known at acceptance, unlike the ADK
    `invocation_id`, which does not exist until the Runner's first event.
    Using run_id is what makes it possible to record ACCEPTED *before*
    anything can fail."""

    worker_id: str = ""
    """POST-6A -- which worker accepted this turn.

    Essential once more than one worker exists: a turn sitting at
    ACCEPTED may be another worker's turn, running perfectly well right
    now. Marking it INTERRUPTED because THIS process happens to be
    starting a different turn would be a fabrication. Reconciliation
    therefore only ever touches a turn this same worker accepted, or one
    whose OWNERSHIP has been shown to be gone -- never one that is merely
    old. See `reconcile_interrupted_turns`."""

    status: TurnStatus
    updated_at: Optional[datetime] = None
    detail: str = ""
    """Safe diagnostic only -- a typed reason or a short phrase, never
    user content, model output, or an exception message."""

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL


def turn_ownership_identity(session_id: str, turn_key: str) -> str:
    """The identity a turn's OWNERSHIP lock is keyed by.

    One function so the holder and the prober cannot drift apart -- if
    they ever disagreed, every probe would find the lock free and
    reconciliation would declare every live turn interrupted.
    """
    return f"{session_id}\x1f{turn_key}"


def parse_turn_lifecycle(raw: Any) -> dict[str, TurnLifecycleRecord]:
    """Tolerant, fail-closed parse. A malformed entry is dropped rather
    than trusted; a malformed store yields nothing, which simply means
    "no lifecycle information", never "everything completed"."""
    if not isinstance(raw, dict):
        return {}
    records: dict[str, TurnLifecycleRecord] = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            records[str(key)] = TurnLifecycleRecord.model_validate(entry)
        except ValidationError:
            continue
    return records


def build_turn_lifecycle_delta(
    existing: Any,
    turn_key: str,
    status: TurnStatus,
    *,
    detail: str = "",
    worker_id: str = "",
    now: Optional[datetime] = None,
    max_entries: int = 50,
) -> dict[str, object]:
    """The full updated value for `TURN_LIFECYCLE_STATE_KEY`.

    A TERMINAL status is never overwritten by a later write for the same
    turn: once a turn is completed/failed/cancelled, a late or duplicated
    finalization attempt cannot silently rewrite history.

    THIS IS WHAT MAKES CANCELLATION RACE-SAFE. A cancellation that
    arrives after the turn already completed finds a terminal COMPLETED
    record and changes nothing -- the user is not told a finished answer
    was cancelled, and a stale worker cannot reopen it.

    Bounded to `max_entries` most-recent turns so a long session's state
    cannot grow without limit -- older entries are dropped, never mutated.
    """
    records = parse_turn_lifecycle(existing)
    current = records.get(turn_key)
    if current is not None and current.is_terminal:
        return {TURN_LIFECYCLE_STATE_KEY: {k: v.model_dump(mode="json") for k, v in records.items()}}
    records[turn_key] = TurnLifecycleRecord(
        turn_key=turn_key,
        worker_id=worker_id or (current.worker_id if current else ""),
        status=status,
        updated_at=now or datetime.now(timezone.utc),
        detail=detail,
    )
    ordered = list(records.items())[-max_entries:]
    return {TURN_LIFECYCLE_STATE_KEY: {k: v.model_dump(mode="json") for k, v in ordered}}


async def reconcile_interrupted_turns(
    existing: Any,
    *,
    current_turn_key: str,
    worker_id: str = "",
    ownership_probe: Optional[Callable[[str], Awaitable[bool]]] = None,
    single_worker: bool = False,
    now: Optional[datetime] = None,
) -> dict[str, object]:
    """Called at the START of every turn, against the state just loaded.

    POST-6A -- OWNERSHIP, NOT AGE. The previous implementation also
    reconciled any ACCEPTED turn older than a threshold (15 minutes). That
    was wrong in the one case it most needed to be right: a genuinely
    long-running turn -- a large multimodal investigation, a slow
    specialist chain, a gateway that is simply taking its time -- is
    ACTIVE, not interrupted, and some other worker starting an unrelated
    turn would declare it dead and write INTERRUPTED over a turn still
    running. An age threshold cannot tell "still working" from "gone";
    only ownership can.

    So a turn belonging to another worker is reconciled ONLY when its
    ownership is shown to be invalid. Three ways to qualify, and only
    three:

      1. THIS worker accepted it. We are demonstrably past it -- we are
         starting a different turn -- so it will never finish.
      2. `single_worker`: this deployment has no cross-process locking at
         all, so no other process exists. A record naming a DIFFERENT
         worker id can only come from an earlier process on this host
         (`WORKER_ID` carries the pid and changes on restart), and that
         process is gone. This is a fact about the deployment, not a
         guess about elapsed time.
      3. `ownership_probe(turn_key)` returns `False` -- the turn's
         ownership lock could be taken, which means the owner's
         connection is gone and the database has already released it.

    With none of those satisfied, the turn is LEFT ACCEPTED. A turn we
    cannot prove is dead stays active, however long it has been running.

    Marked INTERRUPTED -- not FAILED, because we cannot honestly assert
    their work did not take effect.

    Returns an empty dict when there is nothing to reconcile, mirroring
    this codebase's established "empty means no change" convention so the
    ordinary turn writes nothing extra.
    """
    records = parse_turn_lifecycle(existing)
    stamp = now or datetime.now(timezone.utc)

    stale: list[str] = []
    for key, record in records.items():
        if record.status is not TurnStatus.ACCEPTED or key == current_turn_key:
            continue
        if worker_id and record.worker_id == worker_id:
            stale.append(key)
            continue
        if single_worker:
            stale.append(key)
            continue
        if ownership_probe is None:
            # No way to establish that ownership is gone. Leaving the
            # record ACCEPTED is the honest outcome: "we do not know"
            # must never be recorded as "it was interrupted".
            continue
        try:
            if not await ownership_probe(key):
                stale.append(key)
        except Exception:  # noqa: BLE001 -- a failed probe proves nothing
            continue

    if not stale:
        return {}
    for key in stale:
        records[key] = records[key].model_copy(
            update={
                "status": TurnStatus.INTERRUPTED,
                "updated_at": stamp,
                "detail": "accepted but never reached a terminal state",
            }
        )
    return {TURN_LIFECYCLE_STATE_KEY: {k: v.model_dump(mode="json") for k, v in records.items()}}
