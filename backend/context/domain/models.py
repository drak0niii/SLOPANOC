"""The TELCO Context domain model: `ContextAssertion`, `ContextValue`,
`TelcoContextProfile`, and the pure `reduce_dimension`/
`compute_context_state` functions that deterministically turn a set of
assertions into current state.

Everything in this module is plain, deterministic, synchronous Python --
no Gemini/ADK call, no database, no network, no hidden global state (6A.2
instruction section 26). No model-generated fact is ever promoted into
this domain implicitly: this module has no code path from any LLM output
to a `ContextAssertion` at all -- the only way an assertion enters the
canonical model is a deterministic caller (a future tool/service)
explicitly constructing one, exactly like `backend/cases/service.py`'s
own `record_case_analysis` is the sole, explicit gate for a model-
originated Case item (instruction section 7).

STATE DERIVATION IS ALWAYS COMPUTED, NEVER STORED: a dimension's current
`ContextValue` (state + accepted/conflicting assertions) is always
recomputed from the full, persisted, append-only assertion history by
`reduce_dimension`/`compute_context_state` -- never persisted as separate
mutable state that could drift out of sync with its own assertions. This
mirrors this codebase's existing preference for deterministic
recomputation over stored derived state (e.g.
`backend.knowledge.governance.versioning.resolve_current_version`
recomputes "is this version current" from stored version/lifecycle facts
rather than a stored flag). It also makes `reduce_dimension` naturally
order-independent -- "do not let caller ordering create arbitrary truth"
(instruction section 23).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from pydantic import BaseModel, Field, model_validator

from backend.context.domain._shared import require_non_blank
from backend.context.domain.enums import (
    DIMENSION_CARDINALITY,
    AssertionKind,
    ContextDimension,
    ContextOrigin,
    ContextProfileOwnerKind,
    ContextState,
    DimensionCardinality,
)

__all__ = [
    "ContextAssertion",
    "ContextValue",
    "TelcoContextProfile",
    "reduce_dimension",
    "compute_context_state",
]


class ContextAssertion(BaseModel):
    """One immutable, append-only claim about one `ContextDimension`.

    Two kinds (`AssertionKind`): `VALUE` (asserts `raw_value`/
    `canonical_value`) or `NOT_APPLICABLE` (asserts the dimension itself
    does not apply -- carries no value fields at all). `origin`/
    `source_reference` provide the provenance instruction section 8
    requires; `source_reference` is a deliberately opaque identifier
    string (e.g. a Case item id, a Teams message id, a Knowledge evidence
    id) -- mirroring `backend.cases.schemas.CaseContextItemDTO.source_ref`'s
    own proven minimal shape (instruction section 8: "reuse existing
    SourceReference/evidence types where technically appropriate") rather
    than embedding a heavier, presentation-oriented type. This model
    itself never resolves or validates that the referenced id actually
    exists -- exactly like `CaseContextItemDTO.source_ref` today.

    CANONICAL VALUE NORMALIZATION (instruction section 12): kept narrow
    and honest -- `canonical_value` is caller-supplied (a future,
    evidence-backed alias table may compute it), with NO built-in
    vendor/technology alias dictionary in this milestone (no such mapping
    has real evidence yet; inventing one would be exactly the "giant
    TELCO taxonomy/ontology" instruction section 12 forbids building in
    6A.2). `default_canonical_value` below provides only the generic,
    safe default (trim + uppercase) every caller gets for free unless it
    supplies a better one.
    """

    assertion_id: str
    dimension: ContextDimension
    kind: AssertionKind
    raw_value: Optional[str] = None
    canonical_value: Optional[str] = None
    origin: ContextOrigin
    source_reference: Optional[str] = None
    asserted_at: Optional[datetime] = Field(
        default=None, description="When the underlying fact was true/observed, if known -- distinct from created_at."
    )
    created_at: Optional[datetime] = Field(default=None, description="When this assertion was recorded in SLOPANOC.")

    @model_validator(mode="after")
    def _validate_kind_consistency(self) -> "ContextAssertion":
        require_non_blank(self.assertion_id, "assertion_id")
        if self.kind == AssertionKind.NOT_APPLICABLE:
            if self.raw_value is not None or self.canonical_value is not None:
                raise ValueError("a NOT_APPLICABLE assertion must not carry raw_value/canonical_value")
        else:
            require_non_blank(self.raw_value or "", "raw_value")
            require_non_blank(self.canonical_value or "", "canonical_value")
        return self


def default_canonical_value(raw_value: str) -> str:
    """The only normalization 6A.2 performs: trim + uppercase. Never a
    fuzzy/alias mapping (e.g. "E///" -> "ERICSSON" is explicitly NOT
    implemented here -- instruction section 12: "unknown aliases remain
    unnormalized rather than guessed"). A future, evidence-backed
    normalization service may supply a better `canonical_value` to
    `ContextAssertion` directly; this function is only the safe fallback.
    """
    return require_non_blank(raw_value, "raw_value").strip().upper()


class ContextValue(BaseModel):
    """The deterministic, current state of exactly one `ContextDimension`
    within one profile -- always produced by `reduce_dimension`, never
    hand-constructed by ordinary callers (the validators below exist to
    catch a caller who tries to bypass that and construct an inconsistent
    value directly, e.g. in a test).
    """

    dimension: ContextDimension
    state: ContextState
    accepted: list[ContextAssertion] = Field(
        default_factory=list,
        description="Assertions currently backing the state -- VALUE assertions for KNOWN, NOT_APPLICABLE assertions for NOT_APPLICABLE, always empty for UNKNOWN/CONFLICTING.",
    )
    conflicting: list[ContextAssertion] = Field(
        default_factory=list,
        description="The disputed assertions when state is CONFLICTING -- always empty otherwise.",
    )

    @model_validator(mode="after")
    def _validate_state_consistency(self) -> "ContextValue":
        if self.state == ContextState.UNKNOWN:
            if self.accepted or self.conflicting:
                raise ValueError("UNKNOWN must not carry any accepted or conflicting assertions")
        elif self.state == ContextState.KNOWN:
            if not self.accepted:
                raise ValueError("KNOWN must contain at least one accepted assertion")
            if self.conflicting:
                raise ValueError("KNOWN must not carry conflicting assertions")
            if any(a.kind != AssertionKind.VALUE for a in self.accepted):
                raise ValueError("KNOWN's accepted assertions must all be VALUE assertions")
        elif self.state == ContextState.NOT_APPLICABLE:
            if not self.accepted:
                raise ValueError("NOT_APPLICABLE must contain at least one accepted assertion")
            if self.conflicting:
                raise ValueError("NOT_APPLICABLE must not carry conflicting assertions")
            if any(a.kind != AssertionKind.NOT_APPLICABLE for a in self.accepted):
                raise ValueError("NOT_APPLICABLE's accepted assertions must all be NOT_APPLICABLE assertions")
        elif self.state == ContextState.CONFLICTING:
            if self.accepted:
                raise ValueError("CONFLICTING must not carry accepted assertions")
            if len(self.conflicting) < 2:
                raise ValueError("CONFLICTING must carry at least two disputed assertions")
            if len({_position_key(a) for a in self.conflicting}) < 2:
                raise ValueError("CONFLICTING must carry at least two DISTINCT disputed positions, not one value repeated")
        return self


def _position_key(assertion: ContextAssertion) -> tuple:
    if assertion.kind == AssertionKind.NOT_APPLICABLE:
        return ("not_applicable",)
    return ("value", assertion.canonical_value)


def reduce_dimension(dimension: ContextDimension, assertions: Sequence[ContextAssertion]) -> ContextValue:
    """Deterministically fold every assertion for `dimension` into its
    current `ContextValue`. Pure and order-independent -- the SAME set of
    assertions always produces the SAME `ContextValue`, regardless of the
    order they are supplied in (instruction section 23).

    Rules (see this module's own docstring for the full rationale):
      - no assertions                                -> UNKNOWN
      - only NOT_APPLICABLE assertion(s)              -> NOT_APPLICABLE
      - only VALUE assertion(s), SINGULAR dimension,
        exactly one distinct canonical_value           -> KNOWN
      - only VALUE assertion(s), SINGULAR dimension,
        2+ distinct canonical_values                   -> CONFLICTING
      - only VALUE assertion(s), MULTI dimension        -> KNOWN, every
        distinct canonical_value accepted (multiple concurrent facts are
        never a conflict merely for being different -- e.g. two real
        concurrent alarms)
      - a mix of VALUE and NOT_APPLICABLE assertions     -> CONFLICTING
        (a NOT_APPLICABLE declaration must never be silently overridden
        by a later value, nor silently win over one -- instruction
        section 32's Invariant 4; the conflict itself must be visible)

    Retracting/negating a specific prior value is explicitly out of scope
    for 6A.2 (no such concept is requested) -- a MULTI dimension can only
    grow its accepted set through this function, never shrink it based on
    assertion content alone.
    """
    if not assertions:
        return ContextValue(dimension=dimension, state=ContextState.UNKNOWN, accepted=[], conflicting=[])
    for assertion in assertions:
        if assertion.dimension != dimension:
            raise ValueError(f"assertion {assertion.assertion_id!r} belongs to dimension {assertion.dimension!r}, not {dimension!r}")

    not_applicable_assertions = [a for a in assertions if a.kind == AssertionKind.NOT_APPLICABLE]
    value_assertions = [a for a in assertions if a.kind == AssertionKind.VALUE]

    if not_applicable_assertions and value_assertions:
        return ContextValue(dimension=dimension, state=ContextState.CONFLICTING, accepted=[], conflicting=list(assertions))
    if not_applicable_assertions:
        return ContextValue(dimension=dimension, state=ContextState.NOT_APPLICABLE, accepted=list(not_applicable_assertions), conflicting=[])

    cardinality = DIMENSION_CARDINALITY[dimension]
    distinct_canonical_values = {a.canonical_value for a in value_assertions}
    if cardinality == DimensionCardinality.SINGULAR and len(distinct_canonical_values) > 1:
        return ContextValue(dimension=dimension, state=ContextState.CONFLICTING, accepted=[], conflicting=list(value_assertions))
    return ContextValue(dimension=dimension, state=ContextState.KNOWN, accepted=list(value_assertions), conflicting=[])


def compute_context_state(assertions: Sequence[ContextAssertion]) -> dict[ContextDimension, ContextValue]:
    """Group `assertions` by dimension and reduce each group. Dimensions
    with zero assertions are simply absent from the returned mapping --
    callers that need to present a "full profile" (all 20 canonical
    dimensions, most UNKNOWN) do that projection themselves; this
    function never fabricates an UNKNOWN entry for a dimension nobody has
    ever asserted anything about, keeping profiles with few known facts
    cheap to compute and serialize.
    """
    by_dimension: dict[ContextDimension, list[ContextAssertion]] = {}
    for assertion in assertions:
        by_dimension.setdefault(assertion.dimension, []).append(assertion)
    return {dimension: reduce_dimension(dimension, group) for dimension, group in by_dimension.items()}


class TelcoContextProfile(BaseModel):
    """Identity/ownership metadata for one canonical TELCO Context
    profile. Deliberately carries NO dimension/state data inline --
    current state is always computed on demand from the profile's own
    persisted assertions via `compute_context_state` (see this module's
    docstring) -- so this type never goes stale relative to its own
    assertion history.

    OWNERSHIP MODEL (instruction section 22, decided after auditing
    `backend/cases/`): mirrors the EXISTING, already-proven Case/session
    relationship exactly -- `backend.cases.models.CaseSessionLinkRecord
    .session_id` is a primary key, enforcing "a session is linked to at
    most one Case at a time" at the database level. `TelcoContextProfile`
    reuses the identical pattern for its OWN ownership, one level up: a
    profile belongs to EXACTLY ONE scope at a time, expressed as
    `(owner_kind, owner_id)` --
      - `owner_kind=SESSION`, `owner_id=<ADK session_id>`: an ephemeral,
        session-scoped profile, used when no Case is linked.
      - `owner_kind=CASE`, `owner_id=<case_id>`: a durable, Case-scoped
        profile, shared by every session later linked to that Case --
        matching Case's own "durable, cross-session context" role
        (docs/INTELLIGENCE_ARCHITECTURE.md's own topology diagram: "Case/
        Fault -> may provide durable operational context").
    This is a DELIBERATELY SEPARATE table/domain from `backend.cases`
    (instruction section 22: "do not silently merge Case storage and
    TELCO Context storage") -- a profile references a case_id/session_id
    by plain string identity only, never a foreign key into
    `backend.cases`' own tables (keeping the two domains' migrations,
    ownership checks, and lifecycles fully independent, exactly like
    Knowledge and Case already are). Choosing which owner a given request
    should use (promote a session-scoped profile to Case-scoped once a
    Case gets linked, etc.) is deliberately NOT implemented in 6A.2 --
    out of scope, left to whichever future milestone actually wires this
    into a live request path.
    """

    profile_id: str
    owner_kind: ContextProfileOwnerKind
    owner_id: str
    created_by_user_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _validate_identity(self) -> "TelcoContextProfile":
        require_non_blank(self.profile_id, "profile_id")
        require_non_blank(self.owner_id, "owner_id")
        return self
