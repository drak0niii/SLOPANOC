"""Phase 6A.4 Gate 2: TELCO APPLICABILITY.

Reuses, never duplicates or wraps, 6A.2's own `dimension_scope`-style
CONSTRAINED/EXPLICIT_ANY/UNSPECIFIED distinction
(`backend/knowledge/domain/telco_applicability.py`, byte-for-byte
unmodified by 6A.4) and 5.1B's own exact, normalized dimension-value
comparison (`backend/knowledge/domain/models.py`'s `normalize_dimension_
value`, also unmodified). What is genuinely NEW here, and does not exist
anywhere else in this codebase: comparing a knowledge object's CONSTRAINED
dimension against a full, STATE-AWARE `ContextValue` (KNOWN/UNKNOWN/
CONFLICTING/NOT_APPLICABLE) rather than a flat "known values or nothing"
`ApplicabilityContext` (5.1G's `evaluate_applicability` only ever sees
the latter -- see this module's own audit note in the 6A.4 closure
report for why that existing function cannot be reused as-is for this
specific comparison).

CANONICAL-SOURCE RECONCILIATION (§4, mandatory): a knowledge object now
has TWO potentially-overlapping sources of CONSTRAINED dimensions -- its
own frozen `Applicability.dimensions` (5.1B/A5) and the 6A.3-addendum
`asset_metadata`-derived dimensions
(`derive_applicability_dimensions_from_asset_metadata`, unmodified).
`resolve_canonical_dimensions` below merges them deterministically:
additive where only one source declares a dimension, FAIL-CLOSED
(`METADATA_SOURCE_CONFLICT`, never last-write-wins) where both declare
the SAME dimension with a DIFFERENT normalized value set. The pre-
existing, dormant `KnowledgeMetadata.attributes: dict[str, Any]` "legacy"
bag is handled separately -- see `LEGACY_ATTRIBUTE_COMPATIBILITY_MAP`
below (currently empty; no real ingested object has ever populated it,
confirmed by grep during this milestone's own audit) -- so no arbitrary
legacy key is ever silently trusted as narrowing input.
"""
from __future__ import annotations

from backend.context.domain.enums import ContextDimension, ContextState
from backend.context.domain.models import ContextValue
from backend.knowledge.domain.asset_metadata_applicability_bridge import derive_applicability_dimensions_from_asset_metadata
from backend.knowledge.domain.models import Applicability, KnowledgeObject, normalize_dimension_key, normalize_dimension_value
from backend.knowledge.domain.telco_applicability import ApplicabilityScopeKind, dimension_scope, from_knowledge_object
from backend.knowledge.narrowing.context_adapter import lookup_context_value
from backend.knowledge.narrowing.contracts import ApplicabilityDimensionResult, NarrowingReasonCode, TelcoApplicabilityDecision

__all__ = ["resolve_canonical_dimensions", "evaluate_telco_applicability", "LEGACY_ATTRIBUTE_COMPATIBILITY_MAP"]


LEGACY_ATTRIBUTE_COMPATIBILITY_MAP: dict[str, str] = {}
"""Deterministic compatibility bridge from `KnowledgeMetadata.attributes`
(the pre-6A.3, untyped, open key/value bag -- §4's "legacy compatibility
input only") into a canonical applicability dimension key. Keys in
`attributes` NOT present in this map are NEVER read for narrowing
purposes -- "canonical missing -> silently trust arbitrary legacy key"
is the exact failure mode §4 forbids. Deliberately EMPTY: audited (grep,
this milestone's own AUDIT phase) that `KnowledgeMetadata.attributes` is
completely unused by any real ingested object in this codebase today, so
there is no real evidence-backed key to map yet. The mechanism exists
and is tested (`test_applicability_gate.py`) so a future milestone can
add a real mapping without any further architecture change -- adding an
entry here is a deliberate, reviewable code change, never an implicit
runtime behavior."""


def resolve_canonical_dimensions(knowledge_object: KnowledgeObject) -> tuple[dict[str, list[str]], set[str], set[str]]:
    """Returns `(merged_constrained_dimensions, explicit_any_keys,
    conflicting_keys)`.

    `merged_constrained_dimensions` combines `knowledge_object.
    applicability.dimensions` (base) with `derive_applicability_
    dimensions_from_asset_metadata`'s output (additive where the base
    doesn't already declare that key). `explicit_any_keys` comes from
    `asset_metadata.applicability_scope.explicit_any_dimensions`
    (6A.4's own new persisted field). `conflicting_keys` holds every
    dimension where the two CONSTRAINED sources disagree on normalized
    values, OR where a dimension is claimed as EXPLICIT_ANY by asset
    metadata while ALSO being CONSTRAINED by the base `Applicability`
    (an authoring contradiction) -- for any key in `conflicting_keys`,
    the dimension is REMOVED from both `merged_constrained_dimensions`
    and `explicit_any_keys` so downstream code (including 6A.2's own
    `KnowledgeApplicabilityProfile`, which structurally REJECTS a
    dimension present in both) never sees the contradiction; Gate 2
    instead reports it explicitly as `METADATA_SOURCE_CONFLICT`.
    """
    base = dict(knowledge_object.applicability.dimensions)
    derived = derive_applicability_dimensions_from_asset_metadata(knowledge_object.metadata.asset_metadata)
    explicit_any = {
        normalize_dimension_key(key)
        for key in knowledge_object.metadata.asset_metadata.applicability_scope.explicit_any_dimensions
        if normalize_dimension_key(key)
    }

    conflicts: set[str] = set()
    merged = dict(base)
    for key, values in derived.items():
        if key not in merged:
            merged[key] = values
            continue
        base_folded = {normalize_dimension_value(v) for v in merged[key]}
        derived_folded = {normalize_dimension_value(v) for v in values}
        if base_folded != derived_folded:
            conflicts.add(key)

    for key in explicit_any & set(merged):
        conflicts.add(key)

    for key in conflicts:
        merged.pop(key, None)
        explicit_any.discard(key)

    return merged, explicit_any, conflicts


def evaluate_telco_applicability(
    knowledge_object: KnowledgeObject, context_state: dict[ContextDimension, ContextValue]
) -> TelcoApplicabilityDecision:
    """Gate 2 for one already-ELIGIBLE knowledge object. Deterministic,
    synchronous, no model call anywhere in this function or anything it
    calls (verified structurally: no `google.adk`/`google.genai` import
    anywhere under `backend/knowledge/narrowing/`, enforced by this
    milestone's own extension of `test_dependency_boundary.py`).
    """
    merged, explicit_any, conflicts = resolve_canonical_dimensions(knowledge_object)
    dimensions_of_interest = sorted(set(merged) | explicit_any | conflicts)

    if not dimensions_of_interest:
        # PROFILE-LEVEL APPLICABILITY SUFFICIENCY (6A.4 corrective pass):
        # zero CONSTRAINED, zero EXPLICIT_ANY, zero conflicting dimensions
        # means this object carries NO explicit TELCO applicability intent
        # at all -- "absence of applicability information is not evidence
        # of applicability." Previously this returned `match` (an object
        # with nothing to check "matched" trivially), which incorrectly
        # let a wholly unspecified object into `permitted_knowledge_ids`
        # merely because no constrained dimension mismatched -- confirmed
        # a real defect by this milestone's own reproduction test. Now
        # returns `indeterminate`: the object is neither excluded (no
        # mismatch was ever found) nor permitted (no applicability intent
        # was ever declared) -- it belongs in `indeterminate_knowledge_ids`
        # (service.py's existing bucket logic already routes any
        # `indeterminate` outcome there -- no further change needed).
        # Genuinely generic Knowledge MUST declare that explicitly via
        # EXPLICIT_ANY (never via leaving every dimension unspecified) --
        # see resolve_canonical_dimensions' own EXPLICIT_ANY handling
        # below, structurally unaffected by this branch.
        return TelcoApplicabilityDecision(
            knowledge_id=knowledge_object.knowledge_id,
            version_label=knowledge_object.version.label,
            outcome="indeterminate",
            dimension_results=[],
            reason_codes=[NarrowingReasonCode.APPLICABILITY_UNSPECIFIED],
        )

    # Reuse, never duplicate, 6A.2's own CONSTRAINED/EXPLICIT_ANY/
    # UNSPECIFIED resolution: build a real KnowledgeApplicabilityProfile
    # from the already-reconciled (conflict-free) merged dimensions and
    # call the existing, unmodified `dimension_scope` for each dimension
    # of interest, rather than re-deciding scope by hand here.
    profile = from_knowledge_object(
        knowledge_id=knowledge_object.knowledge_id,
        version_label=knowledge_object.version.label,
        applicability=Applicability(dimensions=merged),
        explicit_any_dimensions=frozenset(explicit_any),
    )

    results: list[ApplicabilityDimensionResult] = []
    reason_codes: list[NarrowingReasonCode] = []
    has_mismatch = False
    has_indeterminate = False

    for dimension_key in dimensions_of_interest:
        if dimension_key in conflicts:
            results.append(
                ApplicabilityDimensionResult(
                    dimension=dimension_key,
                    scope=ApplicabilityScopeKind.CONSTRAINED.value,
                    required_values=[],
                    context_state=None,
                    observed_values=[],
                    outcome="indeterminate",
                )
            )
            has_indeterminate = True
            if NarrowingReasonCode.METADATA_SOURCE_CONFLICT not in reason_codes:
                reason_codes.append(NarrowingReasonCode.METADATA_SOURCE_CONFLICT)
            continue

        scope = dimension_scope(profile, dimension_key)
        if scope is ApplicabilityScopeKind.EXPLICIT_ANY:
            results.append(
                ApplicabilityDimensionResult(
                    dimension=dimension_key,
                    scope=ApplicabilityScopeKind.EXPLICIT_ANY.value,
                    required_values=[],
                    context_state=None,
                    observed_values=[],
                    outcome="match",
                )
            )
            continue

        # scope is CONSTRAINED here (UNSPECIFIED never appears in
        # dimensions_of_interest -- membership requires being in merged/
        # explicit_any/conflicts).
        required_values = merged[dimension_key]
        context_value = _lookup_context_value_by_key(dimension_key, context_state)
        outcome, ctx_state_label, observed_values = _compare_dimension(required_values, context_value)
        results.append(
            ApplicabilityDimensionResult(
                dimension=dimension_key,
                scope=ApplicabilityScopeKind.CONSTRAINED.value,
                required_values=required_values,
                context_state=ctx_state_label,
                observed_values=observed_values,
                outcome=outcome,
            )
        )
        if outcome == "mismatch":
            has_mismatch = True
            if NarrowingReasonCode.DIMENSION_MISMATCH not in reason_codes:
                reason_codes.append(NarrowingReasonCode.DIMENSION_MISMATCH)
        elif outcome == "indeterminate":
            has_indeterminate = True
            code = NarrowingReasonCode.CONTEXT_CONFLICTING if ctx_state_label == "conflicting" else NarrowingReasonCode.CONTEXT_UNKNOWN
            if code not in reason_codes:
                reason_codes.append(code)

    if has_mismatch:
        overall = "mismatch"
    elif has_indeterminate:
        overall = "indeterminate"
    else:
        overall = "match"
        if not reason_codes:
            reason_codes = [NarrowingReasonCode.MATCH]

    return TelcoApplicabilityDecision(
        knowledge_id=knowledge_object.knowledge_id,
        version_label=knowledge_object.version.label,
        outcome=overall,
        dimension_results=results,
        reason_codes=reason_codes,
    )


def _lookup_context_value_by_key(dimension_key: str, context_state: dict[ContextDimension, ContextValue]):
    return lookup_context_value(dimension_key, context_state)


def _compare_dimension(required_values: list[str], context_value) -> tuple[str, "str | None", list[str]]:
    """Returns `(outcome, context_state_label, observed_values)`.
    `outcome` is one of `'match'`/`'mismatch'`/`'indeterminate'`. Mirrors
    `applicability.py`'s own `_evaluate_dimension` matching semantics
    (exact normalized-value overlap, never fuzzy/range/ordering logic --
    §26's release-matching requirement is satisfied by this same generic
    exact-match rule, with no dimension-specific special-casing) but adds
    the CONFLICTING/NOT_APPLICABLE context-state handling that function
    has no way to express.
    """
    if context_value is None or context_value.state == ContextState.UNKNOWN:
        return "indeterminate", "unknown", []
    if context_value.state == ContextState.CONFLICTING:
        return "indeterminate", "conflicting", []
    if context_value.state == ContextState.NOT_APPLICABLE:
        # §30: context explicitly says this dimension does not apply,
        # but the knowledge object requires a specific value -- a real
        # mismatch, never silently skipped.
        return "mismatch", "not_applicable", []

    # KNOWN.
    observed = [assertion.canonical_value for assertion in context_value.accepted if assertion.canonical_value is not None]
    observed_folded = {normalize_dimension_value(v) for v in observed}
    matched = [v for v in required_values if normalize_dimension_value(v) in observed_folded]
    return ("match" if matched else "mismatch"), "known", observed
