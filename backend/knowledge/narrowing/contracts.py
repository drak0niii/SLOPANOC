"""Phase 6A.4: the deterministic narrowing result contract.

Two conceptual gates (docs/KNOWLEDGE_CONTRACT.md §26, this milestone's
own instruction §7):

    ALL GOVERNED KNOWLEDGE
            |
    GATE 1 -- AUTHORITY / ELIGIBILITY   (eligibility.py)
            |
    ELIGIBLE KNOWLEDGE
            |
    GATE 2 -- TELCO APPLICABILITY       (applicability_gate.py)
            |
    PERMITTED CANDIDATE SET  (+ EXCLUDED + INDETERMINATE, both explicit)

Every type here is plain, deterministic, synchronous Python -- no
Gemini/ADK call, no database, no network (mirrors `backend/knowledge/
domain/`'s own dependency-boundary discipline, verified by this
milestone's own extension of `test_dependency_boundary.py`).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NarrowingReasonCode(str, Enum):
    """A closed, generic set of reasons ONE knowledge object was
    excluded, held indeterminate, or matched -- never a raw exception,
    never model-generated text, never document content. Dimension-
    specific mismatch/indeterminate detail (WHICH dimension, what the
    required/observed values were) lives in `ApplicabilityDimensionResult`
    (below), keyed by the real, open dimension name -- this enum stays a
    small, stable, machine-readable CORE vocabulary rather than growing
    one member per possible TELCO dimension (dimension names are open,
    per `backend/knowledge/domain/applicability.py`'s own "dimension-
    agnostic by design" invariant, preserved unchanged here).
    """

    MATCH = "match"
    APPLICABILITY_UNSPECIFIED = "applicability_unspecified"
    """Informational, never exclusionary: the object declared zero TELCO
    applicability constraints at all (both `Applicability.dimensions`
    and the asset-metadata bridge produced nothing) -- it matches by
    having nothing to check, distinct from actively matching every
    constraint."""

    DIMENSION_MISMATCH = "dimension_mismatch"
    """At least one CONSTRAINED dimension's known context value(s) do not
    overlap the object's required value(s) -- see `dimension_results`
    for exactly which dimension(s)."""

    CONTEXT_UNKNOWN = "context_unknown"
    """At least one CONSTRAINED dimension has no known current-context
    value at all -- see `dimension_results`."""

    CONTEXT_CONFLICTING = "context_conflicting"
    """At least one CONSTRAINED dimension's current context is itself
    CONFLICTING (two disputed values, §12) -- never arbitrarily
    resolved."""

    METADATA_SOURCE_CONFLICT = "metadata_source_conflict"
    """The object's own `Applicability.dimensions` and its asset-metadata
    -derived dimensions disagree on the SAME dimension's required
    value(s) -- fail-closed (§4): this dimension becomes indeterminate,
    never last-write-wins."""

    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    CANDIDATE_NOT_APPROVED = "candidate_not_approved"
    NOT_EFFECTIVE = "not_effective"
    AMBIGUOUS_VERSION_RESOLUTION = "ambiguous_version_resolution"
    UNAUTHORIZED = "unauthorized"
    """Reserved: no authorization/confidentiality enforcement mechanism
    exists anywhere in the current Governed Knowledge architecture as of
    6A.4 (Phase 4H is still future) -- this code exists so a future
    Phase 4H authorization gate has a stable place to report an
    exclusion without a schema change; unreachable today (see the
    closure report's Gate 1 audit)."""

    AI_APPROVAL_MISSING = "ai_approval_missing"
    AI_APPROVAL_FALSE = "ai_approval_false"
    EXPIRED = "expired"
    REVIEW_DUE = "review_due"
    """Informational only (§19) -- never causes exclusion in 6A.4."""


class NarrowingPolicy(BaseModel):
    """The explicit OBSERVE/ENFORCE boundary (§9, §20, §21): every flag
    here defaults to OBSERVE (`False`) -- the corresponding signal is
    always computed and reported, but never excludes a candidate until a
    caller explicitly sets the flag. This is the ONLY way 6A.4 policy
    becomes exclusionary; there is no other code path. Missing
    historical metadata therefore never silently disappears from the
    corpus merely because 6A.4 was deployed.
    """

    enforce_ai_approved: bool = False
    enforce_not_expired: bool = False


class ApplicabilityDimensionResult(BaseModel):
    """Per-dimension Gate 2 detail -- mirrors `backend/knowledge/domain/
    applicability.py`'s own `ApplicabilityDimensionEvaluation` shape
    (same instinct: enough structural detail to explain WHY, no natural-
    language explanation, no score).
    """

    dimension: str
    scope: str = Field(description="The 6A.2 ApplicabilityScopeKind for this dimension on this object: 'constrained', 'explicit_any', or 'unspecified'.")
    required_values: list[str] = Field(default_factory=list)
    context_state: Optional[str] = Field(default=None, description="The TELCO Context ContextState for this dimension, if a corresponding ContextDimension exists: 'known', 'unknown', 'conflicting', 'not_applicable' -- None if no corresponding ContextDimension exists at all.")
    observed_values: list[str] = Field(default_factory=list)
    outcome: str = Field(description="'match', 'mismatch', 'indeterminate', or 'not_constrained'.")


class EligibilityDecision(BaseModel):
    """Gate 1's own per-object result."""

    knowledge_id: str
    version_label: str
    eligible: bool
    reason_codes: list[NarrowingReasonCode] = Field(default_factory=list)
    ai_approved: Optional[bool] = Field(default=None, description="The raw governance signal, always computed and reported regardless of policy.enforce_ai_approved.")
    expired: Optional[bool] = Field(default=None, description="Always computed and reported regardless of policy.enforce_not_expired. None if no expiry_date is set.")
    review_due: Optional[bool] = None
    source_of_truth: Optional[bool] = None


class TelcoApplicabilityDecision(BaseModel):
    """Gate 2's own per-object result -- only ever produced for objects
    that were `eligible=True` in Gate 1 (§7: Gate 2 only ever sees
    ELIGIBLE KNOWLEDGE)."""

    knowledge_id: str
    version_label: str
    outcome: str = Field(description="'match', 'mismatch', or 'indeterminate'.")
    dimension_results: list[ApplicabilityDimensionResult] = Field(default_factory=list)
    reason_codes: list[NarrowingReasonCode] = Field(default_factory=list)


class KnowledgeNarrowingItem(BaseModel):
    """The full, combined per-object narrowing record -- both gates'
    own decisions, never collapsing them into one opaque boolean."""

    knowledge_id: str
    version_label: str
    eligibility: EligibilityDecision
    applicability: Optional[TelcoApplicabilityDecision] = Field(
        default=None, description="None only when eligibility.eligible is False -- Gate 2 never runs for an ineligible object."
    )
    final_bucket: str = Field(description="'permitted', 'excluded', or 'indeterminate'.")


class KnowledgeNarrowingResult(BaseModel):
    """The full 6A.4 deterministic narrowing result -- what 6A.5 (a
    future milestone) is expected to consume directly (§31/§V of the
    instruction): `permitted_knowledge_ids` is the small, permitted,
    applicable candidate set; `excluded_knowledge_ids`/`indeterminate_
    knowledge_ids` are both explicit, never silently merged into either
    the candidate set or into each other. `items` carries the full
    per-object reasoning for every input object, for observability/
    troubleshooting-explainability (§40) -- never exposed to any model.
    """

    input_count: int
    permitted_knowledge_ids: list[str] = Field(default_factory=list)
    excluded_knowledge_ids: list[str] = Field(default_factory=list)
    indeterminate_knowledge_ids: list[str] = Field(default_factory=list)
    items: list[KnowledgeNarrowingItem] = Field(default_factory=list)
    exclusion_reason_counts: dict[str, int] = Field(default_factory=dict)
