"""Deterministic, presentation-only relative-expiry computation for Teams
write proposals.

This exists so neither the model nor any prompt has to do timestamp
arithmetic (or duplicate the configured expiry window) to describe when a
pending approval expires -- Python computes it once, cleanly, from the
proposal's own `expires_at`. Nothing here is part of the security
boundary: `backend/approval/policy_gate.py`'s `authorize_write` continues
to compare `datetime.now(timezone.utc)` against `ActionProposal.expires_at`
directly and never reads anything computed by this module. This module
also never reads or duplicates `action_proposal_expiry_seconds`
(config/settings.py) -- it only ever looks at the one already-computed
`expires_at` timestamp a specific proposal actually has, so it is
automatically correct for whatever the configured window was at the time
that proposal was created, with no risk of drifting out of sync with it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def compute_expires_in_seconds(expires_at: datetime, now: Optional[datetime] = None) -> int:
    """Whole seconds remaining until `expires_at`, floored at 0 once it has
    passed. Never negative -- "expired" and "long expired" both read as 0,
    which is exactly what presentation code needs (there is no user-facing
    difference between them).
    """
    reference = now if now is not None else datetime.now(timezone.utc)
    remaining = (expires_at - reference).total_seconds()
    return max(0, round(remaining))


def compute_expires_in_minutes(expires_in_seconds: int) -> int:
    """Whole minutes, rounded to the nearest minute from
    `expires_in_seconds` (Python's standard round-half-to-even) --
    human-friendly presentation only, never used for enforcement.

    Deliberately not clamped to a minimum of 1: a proposal with, say, 20
    seconds left legitimately rounds to 0 minutes here. The prompt layer
    is instructed to treat "0 minutes but not yet expired" as "expires
    shortly" rather than reciting a misleading "0 minutes" -- this
    function's job is only the arithmetic, not the wording.
    """
    return max(0, round(expires_in_seconds / 60))
