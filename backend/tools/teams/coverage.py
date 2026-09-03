"""Deterministic coverage/truncation classification for
`teams_get_messages` results.

"The assistant must never imply that it has reviewed a complete Teams
conversation or complete requested time period unless the retrieval
metadata proves that." This module turns the retrieval metadata
`teams_get_messages` already computes (`requested_from`/`requested_to`,
`range_fully_covered`, `truncated`) into one simple, deterministic
`CoverageStatus` (schemas.py) -- incident_manager reasons about *that*,
never re-derives it from the raw booleans itself
(docs/AGENT_CONTRACT.md #5's "agent vs. tool" principle: classifying
already-known metadata is a deterministic capability, not something a
model should re-derive per turn, and not something worth risking
inconsistent phrasing/logic across turns for).

Four distinct cases, matching exactly what `teams_get_messages` can prove
(see `CoverageStatus`'s docstring in schemas.py for the full description
of each):

  A. explicit range requested + range_fully_covered -> FULL_RANGE
  B. explicit range requested + NOT range_fully_covered -> PARTIAL_RANGE
  C. no explicit range requested + NOT truncated -> COMPLETE
  D. no explicit range requested + truncated -> LATEST_WINDOW

This module never changes pagination decisions -- it only classifies
after `teams_get_messages`'s own loop has already finished, from fields
that loop already produced.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.tools.teams.schemas import CoverageStatus, TeamsCoverage


def classify_coverage(
    requested_from: Optional[str],
    requested_to: Optional[str],
    range_fully_covered: bool,
    truncated: bool,
) -> CoverageStatus:
    """Pure classification (see the module docstring for the four cases).

    An explicit time range is considered "requested" if either boundary
    was supplied -- a caller asking for just a lower bound ("since
    Monday") or just an upper bound is still asking for a specific range,
    not merely "the latest messages".
    """
    has_explicit_range = requested_from is not None or requested_to is not None
    if has_explicit_range:
        return CoverageStatus.FULL_RANGE if range_fully_covered else CoverageStatus.PARTIAL_RANGE
    return CoverageStatus.LATEST_WINDOW if truncated else CoverageStatus.COMPLETE


def classify_coverage_from_result(result: Any) -> CoverageStatus:
    """Defensive entry point: classify directly from a
    `teams_get_messages`-shaped mapping, tolerating missing/malformed
    fields rather than raising. A malformed/missing field degrades to the
    most conservative reading (not fully covered / truncated) -- this
    module must never let bad input produce a false claim of
    completeness.
    """
    if not isinstance(result, dict):
        result = {}

    requested_from = result.get("requested_from")
    if not isinstance(requested_from, str):
        requested_from = None

    requested_to = result.get("requested_to")
    if not isinstance(requested_to, str):
        requested_to = None

    range_fully_covered = result.get("range_fully_covered")
    if not isinstance(range_fully_covered, bool):
        range_fully_covered = False

    truncated = result.get("truncated")
    if not isinstance(truncated, bool):
        truncated = True

    return classify_coverage(requested_from, requested_to, range_fully_covered, truncated)


def build_coverage(
    *,
    requested_from: Optional[str],
    requested_to: Optional[str],
    range_fully_covered: bool,
    truncated: bool,
    retrieved_count: int,
    oldest_retrieved_at: Optional[str],
    newest_retrieved_at: Optional[str],
) -> TeamsCoverage:
    """Build the minimal `TeamsCoverage` object exposed to
    incident_manager -- deliberately excludes `next_before` and the raw
    `range_fully_covered`/`truncated` booleans (see `TeamsCoverage`'s
    docstring in schemas.py).
    """
    status = classify_coverage(requested_from, requested_to, range_fully_covered, truncated)
    return TeamsCoverage(
        status=status,
        requested_from=requested_from,
        requested_to=requested_to,
        retrieved_count=retrieved_count,
        oldest_retrieved_at=oldest_retrieved_at,
        newest_retrieved_at=newest_retrieved_at,
    )
