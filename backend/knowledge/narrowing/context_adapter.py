"""Phase 6A.4: pure, deterministic adapter from a 6A.2 `TelcoContextProfile`
's computed state into a lookup Gate 2 (`applicability_gate.py`) can use.

Preserves the full `ContextState` (KNOWN/UNKNOWN/CONFLICTING/
NOT_APPLICABLE) -- never flattens it into a string or a bare value list
(§29's explicit requirement). Does not import `backend.context.sqlalchemy`
(persistence) at all -- only the pure `backend.context.domain` types, so
this module (and everything importing it) stays free of any storage
dependency, matching `backend/knowledge/domain/`'s own dependency-
boundary discipline.

DIMENSION KEY JOIN (audited, not assumed): every `ContextDimension`
member's own `.value` is ALREADY a valid, `normalize_dimension_key`-safe
applicability dimension key string (e.g. `ContextDimension.VENDOR.value
== "vendor"`, exactly the same literal key
`asset_metadata_applicability_bridge.py`'s `ASSET_METADATA_APPLICABILITY_
DIMENSIONS` already uses for "vendor"/"customer"/"technology") -- proven
by a dedicated test, not merely assumed. No translation table was
invented; the join is the identity function composed with the existing,
frozen `normalize_dimension_key`.

NOT EVERY APPLICABILITY DIMENSION HAS A CORRESPONDING ContextDimension
(audited, documented, not silently patched over): the asset-metadata
bridge's own "territory"/"related_systems" keys (Territory/Country,
Related Systems/Tools) have NO corresponding `ContextDimension` member --
6A.2's TELCO Context vocabulary (20 members) does not include either.
For such a dimension, `lookup_context_value` returns `None` -- Gate 2
treats a `None` lookup identically to an `UNKNOWN` `ContextValue` (no
context is no context, regardless of whether the cause is "nobody has
asserted anything yet" or "this dimension has no TELCO Context
representation at all") -- never silently excluded, never silently
matched.
"""
from __future__ import annotations

from typing import Optional

from backend.context.domain.enums import ContextDimension
from backend.context.domain.models import ContextValue
from backend.knowledge.domain.models import normalize_dimension_key

__all__ = ["context_dimension_applicability_key", "lookup_context_value"]


def context_dimension_applicability_key(dimension: ContextDimension) -> str:
    """The canonical `Applicability.dimensions` key string a given
    `ContextDimension` corresponds to -- always `normalize_dimension_key
    (dimension.value)`, which for every one of the 20 canonical members
    is already a no-op (each `.value` is already lowercase, underscore-
    separated, with no leading/trailing/repeated separators)."""
    return normalize_dimension_key(dimension.value)


def lookup_context_value(
    applicability_dimension_key: str, context_state: dict[ContextDimension, ContextValue]
) -> Optional[ContextValue]:
    """Reverse lookup: given an applicability dimension key string (from
    a knowledge object's own CONSTRAINED dimensions), find the matching
    `ContextDimension`'s current `ContextValue`, if any correspondence
    exists AND the profile has ever recorded anything for it. Returns
    `None` for both "no corresponding ContextDimension exists" and "a
    corresponding ContextDimension exists but has zero assertions" --
    both cases are indistinguishable to a caller, and both correctly mean
    "no known context", never a guess.
    """
    normalized_key = normalize_dimension_key(applicability_dimension_key)
    for dimension, value in context_state.items():
        if context_dimension_applicability_key(dimension) == normalized_key:
            return value
    return None
