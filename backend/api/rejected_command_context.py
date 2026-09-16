"""CONTROL-PLANE-SEQ-06 -- run-id-keyed, in-process, never-persisted store
of exact command values THIS turn's own grounding (evidence.py) genuinely
possessed and structurally rejected/stripped. Mirrors `troubleshooting_
guidance_context.py`'s own register/pop/discard lifecycle EXACTLY -- same
module shape, same `current_run_id()`-correlated precedent (the caller
passes `run_id` explicitly here, exactly like `enforce_procedure_scoped_
command_grounding_with_reason` already receives it), same "consumed once,
never persisted" discipline.

THE GAP THIS CLOSES (SEQ-05's own disclosed residual, audited and closed
by CONTROL-PLANE-SEQ-06): `final_output_validator.validate_final_output`
can only protect against a command value `chat_service.py`'s own
`known_commands_this_turn` actually contains. Before this module, a
command evidence.py itself rejected (`GROUNDING_REJECTED`/`UNVERIFIED_
SECTION_REFERENCE`/`CROSS_PROCEDURE_EVIDENCE`) was visible ONLY to
evidence.py's own internal scrub (`_scrub_narrative_operational_leaks`),
which cleans the `TroubleshootingGuidance` object's own narrative fields
but has no way to protect team_manager's own SEPARATE, later free-form
response text. This module lets evidence.py forward the EXACT,
already-known rejected value(s) it already possesses at rejection time --
never a new judgment, never text scanning, never a new grounding
decision -- into chat_service.py's own command inventory.

WRITTEN from `enforce_procedure_scoped_command_grounding_with_reason`
(evidence.py) at each of its own EXISTING rejection sites (FULL_PROCEDURE
per-step, NEXT_STEP, and the wholesale cross-procedure-evidence wipe),
using the SAME `run_id` that function already receives -- never a new
correlation mechanism, and never changing which commands ARE rejected or
why (that logic is completely unmodified). READ exactly once by
`chat_service.py`, at the SAME turn-completion/preflight-governed-read
boundaries `pop_troubleshooting_guidance` is already read at.
"""
from __future__ import annotations

import threading
from typing import Iterable, Optional

_lock = threading.Lock()
_store: dict[str, frozenset[str]] = {}


def register_rejected_commands(run_id: Optional[str], commands: Iterable[str]) -> None:
    """Additive within one run_id -- merges `commands` into whatever this
    run_id already has registered (never overwrites), since `enforce_
    procedure_scoped_command_grounding_with_reason` may call this from
    more than one rejection site within the SAME call (e.g. several
    rejected `FULL_PROCEDURE` steps, or a NEXT_STEP rejection following an
    earlier structural-integrity strip). A no-op for a missing `run_id` or
    when `commands` contains no non-blank values -- never raises, mirrors
    `register_troubleshooting_guidance`'s own "never fail the turn over a
    side channel" discipline.
    """
    if not run_id:
        return
    values = frozenset(c for c in commands if c)
    if not values:
        return
    with _lock:
        existing = _store.get(run_id, frozenset())
        _store[run_id] = existing | values


def pop_rejected_commands(run_id: Optional[str]) -> frozenset[str]:
    """Read exactly once, at the SAME turn-completion/preflight-governed-
    read boundary `pop_troubleshooting_guidance` is already read at --
    removes the entry as it reads it. Returns an empty `frozenset` for a
    missing `run_id` or a turn that rejected nothing (the overwhelming
    majority of turns) -- never an error.
    """
    if not run_id:
        return frozenset()
    with _lock:
        return _store.pop(run_id, frozenset())


def discard_rejected_commands(run_id: str) -> None:
    """Backstop/normal cleanup, called alongside `discard_troubleshooting_
    guidance` from the same `finally` block(s) -- safe to call whether or
    not an entry exists.
    """
    with _lock:
        _store.pop(run_id, None)
