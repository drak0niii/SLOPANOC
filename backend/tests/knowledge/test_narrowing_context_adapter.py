"""Phase 6A.4: TELCO Context -> narrowing input adapter
(`backend/knowledge/narrowing/context_adapter.py`)."""
from __future__ import annotations

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin, ContextState
from backend.context.domain.models import ContextAssertion, reduce_dimension
from backend.knowledge.domain.models import normalize_dimension_key
from backend.knowledge.narrowing.context_adapter import context_dimension_applicability_key, lookup_context_value


def test_every_context_dimension_maps_to_a_unique_applicability_key() -> None:
    """No two ContextDimension members collapse to the same applicability
    key -- proven, not assumed."""
    keys = [context_dimension_applicability_key(d) for d in ContextDimension]
    assert len(keys) == len(set(keys)) == len(ContextDimension)


def test_context_dimension_key_is_normalize_dimension_key_of_its_own_value() -> None:
    for dimension in ContextDimension:
        assert context_dimension_applicability_key(dimension) == normalize_dimension_key(dimension.value)


def test_vendor_dimension_key_matches_literal_string() -> None:
    assert context_dimension_applicability_key(ContextDimension.VENDOR) == "vendor"


def test_customer_dimension_key_matches_literal_string() -> None:
    assert context_dimension_applicability_key(ContextDimension.CUSTOMER) == "customer"


def test_lookup_returns_none_for_dimension_with_no_context_state_entry() -> None:
    assert lookup_context_value("vendor", {}) is None


def test_lookup_returns_none_for_dimension_with_no_context_dimension_at_all() -> None:
    """"territory"/"related_systems" (asset-metadata bridge dimension
    keys) have no corresponding ContextDimension -- must resolve to None,
    never a guess."""
    assert lookup_context_value("territory", {ContextDimension.VENDOR: reduce_dimension(ContextDimension.VENDOR, [])}) is None
    assert lookup_context_value("related_systems", {}) is None


def test_lookup_finds_the_correct_context_value() -> None:
    a = ContextAssertion(assertion_id="a1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER)
    cv = reduce_dimension(ContextDimension.VENDOR, [a])
    found = lookup_context_value("vendor", {ContextDimension.VENDOR: cv})
    assert found is not None
    assert found.state == ContextState.KNOWN
    assert found.accepted[0].canonical_value == "ERICSSON"


def test_lookup_is_case_and_separator_insensitive_via_normalize_dimension_key() -> None:
    a = ContextAssertion(assertion_id="a1", dimension=ContextDimension.NETWORK_ELEMENT, kind=AssertionKind.VALUE, raw_value="RBS6601", canonical_value="RBS6601", origin=ContextOrigin.USER)
    cv = reduce_dimension(ContextDimension.NETWORK_ELEMENT, [a])
    assert lookup_context_value("Network-Element", {ContextDimension.NETWORK_ELEMENT: cv}) is not None
    assert lookup_context_value("network_element", {ContextDimension.NETWORK_ELEMENT: cv}) is not None
