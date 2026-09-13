"""Phase 6A.4 Gate 2: TELCO APPLICABILITY
(`backend/knowledge/narrowing/applicability_gate.py`)."""
from __future__ import annotations

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin
from backend.context.domain.models import ContextAssertion, reduce_dimension
from backend.knowledge.domain.asset_metadata import KnowledgeAssetMetadata, MultiValueMetadataField
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeMetadata, KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.narrowing.applicability_gate import evaluate_telco_applicability, resolve_canonical_dimensions
from backend.knowledge.narrowing.contracts import NarrowingReasonCode


def _obj(applicability=None, asset_metadata=None) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id="K1",
        document_type=KnowledgeDocumentType.MOP,
        title="T",
        version=KnowledgeVersion(label="1.0"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="x", source_id="y"),
        applicability=applicability or Applicability(),
        metadata=KnowledgeMetadata(asset_metadata=asset_metadata or KnowledgeAssetMetadata()),
    )


def _known(dimension: ContextDimension, canonical_value: str):
    a = ContextAssertion(assertion_id="a1", dimension=dimension, kind=AssertionKind.VALUE, raw_value=canonical_value, canonical_value=canonical_value, origin=ContextOrigin.USER)
    return reduce_dimension(dimension, [a])


def _conflicting(dimension: ContextDimension, v1: str, v2: str):
    a = ContextAssertion(assertion_id="a1", dimension=dimension, kind=AssertionKind.VALUE, raw_value=v1, canonical_value=v1, origin=ContextOrigin.USER)
    b = ContextAssertion(assertion_id="a2", dimension=dimension, kind=AssertionKind.VALUE, raw_value=v2, canonical_value=v2, origin=ContextOrigin.TEAMS)
    return reduce_dimension(dimension, [a, b])


def _not_applicable(dimension: ContextDimension):
    a = ContextAssertion(assertion_id="a1", dimension=dimension, kind=AssertionKind.NOT_APPLICABLE, origin=ContextOrigin.SYSTEM)
    return reduce_dimension(dimension, [a])


# --- Customer isolation (§16, highest priority) -----------------------------


def test_customer_exact_match() -> None:
    obj = _obj(Applicability(dimensions={"customer": ["Vodafone"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.CUSTOMER: _known(ContextDimension.CUSTOMER, "VODAFONE")})
    assert decision.outcome == "match"


def test_customer_mismatch_isolates_other_customer() -> None:
    """A customer-specific document must never leak into another
    customer's candidate set."""
    obj = _obj(Applicability(dimensions={"customer": ["Rogers"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.CUSTOMER: _known(ContextDimension.CUSTOMER, "VODAFONE")})
    assert decision.outcome == "mismatch"
    assert NarrowingReasonCode.DIMENSION_MISMATCH in decision.reason_codes


def test_customer_unspecified_context() -> None:
    obj = _obj(Applicability(dimensions={"customer": ["Vodafone"]}))
    decision = evaluate_telco_applicability(obj, {})
    assert decision.outcome == "indeterminate"
    assert NarrowingReasonCode.CONTEXT_UNKNOWN in decision.reason_codes


def test_customer_context_unknown_explicit_state() -> None:
    obj = _obj(Applicability(dimensions={"customer": ["Vodafone"]}))
    # No assertions at all for CUSTOMER -> the profile simply never has an entry.
    decision = evaluate_telco_applicability(obj, {})
    assert decision.dimension_results[0].context_state == "unknown"


def test_customer_context_conflicting() -> None:
    obj = _obj(Applicability(dimensions={"customer": ["Vodafone"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.CUSTOMER: _conflicting(ContextDimension.CUSTOMER, "VODAFONE", "ROGERS")})
    assert decision.outcome == "indeterminate"
    assert NarrowingReasonCode.CONTEXT_CONFLICTING in decision.reason_codes


# --- Vendor ------------------------------------------------------------------


def test_vendor_exact_match() -> None:
    obj = _obj(Applicability(dimensions={"vendor": ["Ericsson"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "ERICSSON")})
    assert decision.outcome == "match"


def test_vendor_mismatch() -> None:
    obj = _obj(Applicability(dimensions={"vendor": ["Ericsson"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "NOKIA")})
    assert decision.outcome == "mismatch"


def test_vendor_explicit_any() -> None:
    am = KnowledgeAssetMetadata()
    am.applicability_scope.explicit_any_dimensions = ["vendor"]
    obj = _obj(asset_metadata=am)
    decision = evaluate_telco_applicability(obj, {ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "NOKIA")})
    assert decision.outcome == "match"
    assert decision.dimension_results[0].scope == "explicit_any"


def test_vendor_unspecified_never_treated_as_any() -> None:
    """§10's critical invariant: UNSPECIFIED must not be treated as ANY.
    Uses a PARTIALLY constrained object (technology constrained, vendor
    left unspecified) rather than a wholly unconstrained one, so this
    test's own point (vendor stays unevaluated, never ANY) is isolated
    from the 6A.4 corrective-pass sufficiency rule -- a wholly
    unconstrained object is covered separately by `test_fully_
    unconstrained_object_is_indeterminate_not_match`.
    """
    obj = _obj(Applicability(dimensions={"technology": ["LTE"]}))  # no vendor constraint anywhere
    decision = evaluate_telco_applicability(
        obj,
        {
            ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "NOKIA"),
            ContextDimension.TECHNOLOGY: _known(ContextDimension.TECHNOLOGY, "LTE"),
        },
    )
    # "vendor" is not a dimension_of_interest at all since it's UNSPECIFIED.
    assert not any(r.dimension == "vendor" for r in decision.dimension_results)
    assert decision.outcome == "match"  # matches because technology matched, not because vendor=ANY


# --- Technology ----------------------------------------------------------------


def test_technology_match() -> None:
    obj = _obj(Applicability(dimensions={"technology": ["LTE"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.TECHNOLOGY: _known(ContextDimension.TECHNOLOGY, "LTE")})
    assert decision.outcome == "match"


def test_technology_mismatch() -> None:
    obj = _obj(Applicability(dimensions={"technology": ["LTE"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.TECHNOLOGY: _known(ContextDimension.TECHNOLOGY, "5G")})
    assert decision.outcome == "mismatch"


# --- Release (§26: exact match only, no assumed ordering) --------------------


def test_release_exact_match() -> None:
    obj = _obj(Applicability(dimensions={"release": ["24.Q2"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.RELEASE: _known(ContextDimension.RELEASE, "24.Q2")})
    assert decision.outcome == "match"


def test_release_mismatch_never_assumes_lexical_ordering() -> None:
    """24.Q3 must not be treated as satisfying a 24.Q2 requirement merely
    because it looks "newer" -- exact match only."""
    obj = _obj(Applicability(dimensions={"release": ["24.Q2"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.RELEASE: _known(ContextDimension.RELEASE, "24.Q3")})
    assert decision.outcome == "mismatch"


def test_release_opaque_value_no_range_comparison() -> None:
    obj = _obj(Applicability(dimensions={"release": ["R23A"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.RELEASE: _known(ContextDimension.RELEASE, "23R2")})
    assert decision.outcome == "mismatch"


def test_release_unknown_context() -> None:
    obj = _obj(Applicability(dimensions={"release": ["24.Q2"]}))
    decision = evaluate_telco_applicability(obj, {})
    assert decision.outcome == "indeterminate"


def test_release_explicit_any() -> None:
    am = KnowledgeAssetMetadata()
    am.applicability_scope.explicit_any_dimensions = ["release"]
    obj = _obj(asset_metadata=am)
    decision = evaluate_telco_applicability(obj, {ContextDimension.RELEASE: _known(ContextDimension.RELEASE, "99.Z9")})
    assert decision.outcome == "match"


# --- NOT_APPLICABLE context state (§30) --------------------------------------


def test_not_applicable_context_mismatches_a_constrained_dimension() -> None:
    obj = _obj(Applicability(dimensions={"cell": ["CELL_123"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.CELL: _not_applicable(ContextDimension.CELL)})
    assert decision.outcome == "mismatch"


def test_not_applicable_context_irrelevant_when_dimension_unconstrained() -> None:
    """A dimension irrelevant to both sides (knowledge doesn't constrain
    it) must not create a false failure -- proven against a PARTIALLY
    constrained object (vendor constrained, cell not) so this test's own
    point (irrelevant NOT_APPLICABLE context never causes a false
    mismatch) stays isolated from the 6A.4 corrective-pass sufficiency
    rule (a WHOLLY unconstrained object is a separate concern, covered by
    `test_fully_unconstrained_object_is_indeterminate_not_match` below).
    """
    obj = _obj(Applicability(dimensions={"vendor": ["Ericsson"]}))  # cell unconstrained
    decision = evaluate_telco_applicability(
        obj, {ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "ERICSSON"), ContextDimension.CELL: _not_applicable(ContextDimension.CELL)}
    )
    assert decision.outcome == "match"
    assert not any(r.dimension == "cell" for r in decision.dimension_results)


# --- Multi-value applicability (§15) -----------------------------------------


def test_multi_value_non_empty_intersection_matches() -> None:
    obj = _obj(Applicability(dimensions={"vendor": ["Ericsson", "Nokia"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "NOKIA")})
    assert decision.outcome == "match"


def test_multi_value_no_intersection_mismatches() -> None:
    obj = _obj(Applicability(dimensions={"vendor": ["Ericsson", "Nokia"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "HUAWEI")})
    assert decision.outcome == "mismatch"


def test_multi_value_multiple_context_values_technology() -> None:
    """TECHNOLOGY is a MULTI-cardinality TelcoContext dimension -- two
    concurrent known values are both legitimately accepted."""
    a = ContextAssertion(assertion_id="a1", dimension=ContextDimension.TECHNOLOGY, kind=AssertionKind.VALUE, raw_value="LTE", canonical_value="LTE", origin=ContextOrigin.USER)
    b = ContextAssertion(assertion_id="a2", dimension=ContextDimension.TECHNOLOGY, kind=AssertionKind.VALUE, raw_value="5G", canonical_value="5G", origin=ContextOrigin.USER)
    cv = reduce_dimension(ContextDimension.TECHNOLOGY, [a, b])
    obj = _obj(Applicability(dimensions={"technology": ["5G"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.TECHNOLOGY: cv})
    assert decision.outcome == "match"


# --- Unconstrained object -- 6A.4 CORRECTIVE PASS -----------------------------
#
# "Absence of applicability information is not evidence of applicability."
# A Knowledge object with ZERO declared TELCO applicability intent (no
# CONSTRAINED dimension, no EXPLICIT_ANY, no conflict) must resolve
# INDETERMINATE, never MATCH -- it must never enter permitted_knowledge_ids
# merely because nothing was ever checked. Genuinely generic Knowledge
# must declare that explicitly via EXPLICIT_ANY (see the "explicitly
# generic" tests below), never by leaving every dimension unspecified.


def test_fully_unconstrained_object_is_indeterminate_not_match() -> None:
    """THE corrective-pass regression proof: reproduces the exact defect
    this pass fixes -- before the fix, this assertion was `== "match"`.
    """
    obj = _obj()
    decision = evaluate_telco_applicability(obj, {})
    assert decision.outcome == "indeterminate"
    assert decision.reason_codes == [NarrowingReasonCode.APPLICABILITY_UNSPECIFIED]


def test_unspecified_still_differs_from_explicit_any() -> None:
    """Regression proof (§14's own explicit requirement): UNSPECIFIED
    (indeterminate) and EXPLICIT_ANY (match) must never be conflated."""
    unspecified = evaluate_telco_applicability(_obj(), {})
    am = KnowledgeAssetMetadata()
    am.applicability_scope.explicit_any_dimensions = ["vendor"]
    explicit_any_obj = _obj(asset_metadata=am)
    explicit_any_decision = evaluate_telco_applicability(explicit_any_obj, {})
    assert unspecified.outcome == "indeterminate"
    assert explicit_any_decision.outcome == "match"
    assert unspecified.outcome != explicit_any_decision.outcome


def test_explicitly_generic_knowledge_still_matches() -> None:
    """§5/§8: Customer=EXPLICIT_ANY, Vendor=ERICSSON (constrained),
    Technology=EXPLICIT_ANY -- a deliberately, explicitly declared
    generic profile must still MATCH when its own constrained dimensions
    are satisfied, never downgraded to indeterminate merely because some
    dimensions are EXPLICIT_ANY rather than value-constrained.
    """
    am = KnowledgeAssetMetadata()
    am.applicability_scope.explicit_any_dimensions = ["customer", "technology"]
    obj = _obj(Applicability(dimensions={"vendor": ["Ericsson"]}), am)
    decision = evaluate_telco_applicability(obj, {ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "ERICSSON")})
    assert decision.outcome == "match"


def test_wholly_explicit_any_profile_matches_unconditionally() -> None:
    """§5's own worked example: Customer=ANY, Vendor=ANY, Technology=ANY,
    Release=ANY -- a wholly EXPLICIT_ANY profile (never a wholly
    UNSPECIFIED one) is a real, valid, deliberately generic declaration
    and must MATCH regardless of context.
    """
    am = KnowledgeAssetMetadata()
    am.applicability_scope.explicit_any_dimensions = ["customer", "vendor", "technology", "release"]
    obj = _obj(asset_metadata=am)
    decision = evaluate_telco_applicability(obj, {})
    assert decision.outcome == "match"


def test_partially_constrained_knowledge_still_matches() -> None:
    """§6/§7: Vendor=ERICSSON, Technology=LTE declared; Product/Release/
    Alarm left unspecified -- a legitimately partial profile must still
    MATCH when its own declared dimensions are satisfied; unspecified
    dimensions beyond what the object itself declares are simply never
    evaluated (never a reason to downgrade to indeterminate)."""
    obj = _obj(Applicability(dimensions={"vendor": ["Ericsson"], "technology": ["LTE"]}))
    decision = evaluate_telco_applicability(
        obj,
        {
            ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "ERICSSON"),
            ContextDimension.TECHNOLOGY: _known(ContextDimension.TECHNOLOGY, "LTE"),
        },
    )
    assert decision.outcome == "match"
    assert not any(r.dimension in ("product", "release", "alarm") for r in decision.dimension_results)


def test_partially_constrained_knowledge_mismatches_on_its_own_declared_dimension() -> None:
    """Same partial profile as above, but the context now disagrees on
    the ONE dimension the object actually declared -- must MISMATCH, not
    indeterminate and not match."""
    obj = _obj(Applicability(dimensions={"vendor": ["Ericsson"], "technology": ["LTE"]}))
    decision = evaluate_telco_applicability(
        obj,
        {
            ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "NOKIA"),
            ContextDimension.TECHNOLOGY: _known(ContextDimension.TECHNOLOGY, "LTE"),
        },
    )
    assert decision.outcome == "mismatch"


def test_customer_unspecified_never_interpreted_as_cross_customer_generic() -> None:
    """§8/§14 customer-safety regression: a Knowledge object that
    constrains ONLY vendor (customer left unspecified, never declared
    EXPLICIT_ANY) must NOT be silently treated as applicable to every
    customer merely because customer itself was never checked -- this is
    exactly the partially-constrained case (SUFFICIENT, since vendor IS
    declared), distinct from the wholly-unspecified case (INDETERMINATE).
    The object legitimately matches here because ITS OWN declared
    dimension (vendor) is satisfied -- customer was never asserted as a
    constraint by this object at all, so it is correctly never evaluated,
    exactly like any other never-constrained dimension (never an implicit
    cross-customer generic declaration).
    """
    obj = _obj(Applicability(dimensions={"vendor": ["Ericsson"]}))
    decision = evaluate_telco_applicability(obj, {ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "ERICSSON")})
    assert decision.outcome == "match"
    assert not any(r.dimension == "customer" for r in decision.dimension_results)


# --- Canonical-source reconciliation (§4, §28) -------------------------------


def test_asset_metadata_fills_gap_not_declared_by_legacy_applicability() -> None:
    am = KnowledgeAssetMetadata()
    am.technical_scope.vendor_oem = MultiValueMetadataField(raw_values=["Ericsson"], normalized_values=["ericsson"])
    obj = _obj(applicability=Applicability(), asset_metadata=am)  # legacy Applicability has NO vendor key
    merged, explicit_any, conflicts = resolve_canonical_dimensions(obj)
    assert merged["vendor"] == ["ericsson"]
    assert conflicts == set()


def test_agreeing_legacy_and_asset_metadata_produce_no_conflict() -> None:
    am = KnowledgeAssetMetadata()
    am.technical_scope.vendor_oem = MultiValueMetadataField(raw_values=["Ericsson"])
    obj = _obj(applicability=Applicability(dimensions={"vendor": ["ericsson"]}), asset_metadata=am)
    merged, explicit_any, conflicts = resolve_canonical_dimensions(obj)
    assert conflicts == set()


def test_conflicting_legacy_and_asset_metadata_fails_closed_never_last_write_wins() -> None:
    am = KnowledgeAssetMetadata()
    am.technical_scope.vendor_oem = MultiValueMetadataField(raw_values=["Nokia"])
    obj = _obj(applicability=Applicability(dimensions={"vendor": ["Ericsson"]}), asset_metadata=am)
    merged, explicit_any, conflicts = resolve_canonical_dimensions(obj)
    assert "vendor" in conflicts
    assert "vendor" not in merged  # removed, never silently resolved either direction

    decision = evaluate_telco_applicability(obj, {ContextDimension.VENDOR: _known(ContextDimension.VENDOR, "ERICSSON")})
    assert decision.outcome == "indeterminate"
    assert NarrowingReasonCode.METADATA_SOURCE_CONFLICT in decision.reason_codes


def test_explicit_any_conflicting_with_legacy_constrained_fails_closed() -> None:
    am = KnowledgeAssetMetadata()
    am.applicability_scope.explicit_any_dimensions = ["vendor"]
    obj = _obj(applicability=Applicability(dimensions={"vendor": ["Ericsson"]}), asset_metadata=am)
    merged, explicit_any, conflicts = resolve_canonical_dimensions(obj)
    assert "vendor" in conflicts
    assert "vendor" not in merged
    assert "vendor" not in explicit_any


def test_legacy_attribute_compatibility_map_is_empty_by_default() -> None:
    """No real ingested object populates KnowledgeMetadata.attributes
    (confirmed by this milestone's own audit) -- the compatibility map
    must not silently invent a mapping."""
    from backend.knowledge.narrowing.applicability_gate import LEGACY_ATTRIBUTE_COMPATIBILITY_MAP

    assert LEGACY_ATTRIBUTE_COMPATIBILITY_MAP == {}
