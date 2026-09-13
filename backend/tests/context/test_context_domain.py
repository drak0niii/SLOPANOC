"""Domain-level tests for the TELCO Context state machine (6A.2
instruction section 33's own test matrix): the four states, valid/
invalid `ContextValue` construction, deterministic merge/reduce
behavior, provenance, multi-value dimensions, and fail-closed semantics.
No database, no ADK, no Gemini -- pure domain logic only.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.context.domain.enums import (
    DIMENSION_CARDINALITY,
    AssertionKind,
    ContextDimension,
    ContextOrigin,
    ContextProfileOwnerKind,
    ContextState,
    DimensionCardinality,
)
from backend.context.domain.models import (
    ContextAssertion,
    ContextValue,
    TelcoContextProfile,
    compute_context_state,
    default_canonical_value,
    reduce_dimension,
)

_NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def _value_assertion(
    dimension: ContextDimension,
    raw: str,
    *,
    origin: ContextOrigin = ContextOrigin.USER,
    canonical: str | None = None,
    assertion_id: str | None = None,
) -> ContextAssertion:
    return ContextAssertion(
        assertion_id=assertion_id or f"a-{raw}-{origin.value}",
        dimension=dimension,
        kind=AssertionKind.VALUE,
        raw_value=raw,
        canonical_value=canonical or default_canonical_value(raw),
        origin=origin,
        created_at=_NOW,
    )


def _not_applicable_assertion(dimension: ContextDimension, *, assertion_id: str = "na-1") -> ContextAssertion:
    return ContextAssertion(
        assertion_id=assertion_id,
        dimension=dimension,
        kind=AssertionKind.NOT_APPLICABLE,
        origin=ContextOrigin.SYSTEM,
        created_at=_NOW,
    )


# --- ContextAssertion invariants --------------------------------------------


def test_value_assertion_requires_raw_and_canonical_value() -> None:
    with pytest.raises(ValidationError):
        ContextAssertion(
            assertion_id="a1",
            dimension=ContextDimension.VENDOR,
            kind=AssertionKind.VALUE,
            origin=ContextOrigin.USER,
        )


def test_not_applicable_assertion_must_not_carry_a_value() -> None:
    with pytest.raises(ValidationError):
        ContextAssertion(
            assertion_id="a1",
            dimension=ContextDimension.CELL,
            kind=AssertionKind.NOT_APPLICABLE,
            raw_value="anything",
            origin=ContextOrigin.SYSTEM,
        )


def test_default_canonical_value_is_generic_trim_and_uppercase_only() -> None:
    assert default_canonical_value("  ericsson ") == "ERICSSON"
    # No alias mapping exists -- a raw value with no canonical form
    # supplied stays exactly what it is, trimmed and uppercased, never
    # guessed into a different vendor name.
    assert default_canonical_value("E///") == "E///"


# --- ContextValue construction invariants -----------------------------------


def test_unknown_must_not_carry_assertions() -> None:
    with pytest.raises(ValidationError):
        ContextValue(
            dimension=ContextDimension.VENDOR,
            state=ContextState.UNKNOWN,
            accepted=[_value_assertion(ContextDimension.VENDOR, "Ericsson")],
        )


def test_known_requires_at_least_one_accepted_assertion() -> None:
    with pytest.raises(ValidationError):
        ContextValue(dimension=ContextDimension.VENDOR, state=ContextState.KNOWN, accepted=[])


def test_conflicting_with_only_one_unique_value_is_rejected() -> None:
    same_value_twice = [
        _value_assertion(ContextDimension.VENDOR, "Ericsson", assertion_id="a1"),
        _value_assertion(ContextDimension.VENDOR, "Ericsson", assertion_id="a2"),
    ]
    with pytest.raises(ValidationError):
        ContextValue(dimension=ContextDimension.VENDOR, state=ContextState.CONFLICTING, conflicting=same_value_twice)


def test_conflicting_requires_at_least_two_assertions() -> None:
    with pytest.raises(ValidationError):
        ContextValue(
            dimension=ContextDimension.VENDOR,
            state=ContextState.CONFLICTING,
            conflicting=[_value_assertion(ContextDimension.VENDOR, "Ericsson")],
        )


def test_not_applicable_accepted_assertions_must_all_be_not_applicable_kind() -> None:
    with pytest.raises(ValidationError):
        ContextValue(
            dimension=ContextDimension.CELL,
            state=ContextState.NOT_APPLICABLE,
            accepted=[_value_assertion(ContextDimension.CELL, "CELL_1")],
        )


# --- reduce_dimension: the deterministic merge/transition table ------------


def test_no_assertions_is_unknown() -> None:
    result = reduce_dimension(ContextDimension.VENDOR, [])
    assert result.state == ContextState.UNKNOWN
    assert result.accepted == []
    assert result.conflicting == []


def test_unknown_plus_trusted_assertion_becomes_known() -> None:
    result = reduce_dimension(ContextDimension.VENDOR, [_value_assertion(ContextDimension.VENDOR, "Ericsson")])
    assert result.state == ContextState.KNOWN
    assert result.accepted[0].canonical_value == "ERICSSON"


def test_known_same_value_corroborating_assertion_stays_known_with_enriched_provenance() -> None:
    a1 = _value_assertion(ContextDimension.VENDOR, "Ericsson", origin=ContextOrigin.USER, assertion_id="a1")
    a2 = _value_assertion(ContextDimension.VENDOR, "Ericsson", origin=ContextOrigin.TEAMS, assertion_id="a2")
    result = reduce_dimension(ContextDimension.VENDOR, [a1, a2])
    assert result.state == ContextState.KNOWN
    assert {a.assertion_id for a in result.accepted} == {"a1", "a2"}
    assert {a.origin for a in result.accepted} == {ContextOrigin.USER, ContextOrigin.TEAMS}


def test_known_plus_credible_conflicting_assertion_becomes_conflicting() -> None:
    a1 = _value_assertion(ContextDimension.VENDOR, "Ericsson", assertion_id="a1")
    b1 = _value_assertion(ContextDimension.VENDOR, "Nokia", assertion_id="b1")
    result = reduce_dimension(ContextDimension.VENDOR, [a1, b1])
    assert result.state == ContextState.CONFLICTING
    assert result.accepted == []
    assert {a.assertion_id for a in result.conflicting} == {"a1", "b1"}


def test_conflicting_plus_additional_evidence_remains_conflicting() -> None:
    a1 = _value_assertion(ContextDimension.VENDOR, "Ericsson", assertion_id="a1")
    b1 = _value_assertion(ContextDimension.VENDOR, "Nokia", assertion_id="b1")
    c1 = _value_assertion(ContextDimension.VENDOR, "Nokia", assertion_id="c1")  # corroborates b1, doesn't resolve
    result = reduce_dimension(ContextDimension.VENDOR, [a1, b1, c1])
    assert result.state == ContextState.CONFLICTING
    assert {a.assertion_id for a in result.conflicting} == {"a1", "b1", "c1"}


def test_not_applicable_alone_is_not_applicable() -> None:
    result = reduce_dimension(ContextDimension.CELL, [_not_applicable_assertion(ContextDimension.CELL)])
    assert result.state == ContextState.NOT_APPLICABLE
    assert result.accepted[0].kind == AssertionKind.NOT_APPLICABLE


def test_not_applicable_never_silently_becomes_known() -> None:
    """Invariant 4 (instruction section 32): a NOT_APPLICABLE assertion
    followed by a later VALUE assertion must never silently resolve to
    KNOWN -- the conflict itself must remain visible.
    """
    na = _not_applicable_assertion(ContextDimension.CELL)
    value = _value_assertion(ContextDimension.CELL, "CELL_2")
    result = reduce_dimension(ContextDimension.CELL, [na, value])
    assert result.state == ContextState.CONFLICTING
    assert result.state != ContextState.KNOWN


def test_multi_dimension_accepts_multiple_distinct_values_without_conflict() -> None:
    assert DIMENSION_CARDINALITY[ContextDimension.ALARM] == DimensionCardinality.MULTI
    a1 = _value_assertion(ContextDimension.ALARM, "VSWR", assertion_id="a1")
    a2 = _value_assertion(ContextDimension.ALARM, "LOS", assertion_id="a2")
    result = reduce_dimension(ContextDimension.ALARM, [a1, a2])
    assert result.state == ContextState.KNOWN
    assert {a.canonical_value for a in result.accepted} == {"VSWR", "LOS"}


def test_singular_dimension_two_distinct_values_is_conflicting_not_multi_accept() -> None:
    assert DIMENSION_CARDINALITY[ContextDimension.VENDOR] == DimensionCardinality.SINGULAR
    a1 = _value_assertion(ContextDimension.VENDOR, "Ericsson", assertion_id="a1")
    a2 = _value_assertion(ContextDimension.VENDOR, "Nokia", assertion_id="a2")
    result = reduce_dimension(ContextDimension.VENDOR, [a1, a2])
    assert result.state == ContextState.CONFLICTING


def test_reduce_dimension_is_order_independent() -> None:
    a1 = _value_assertion(ContextDimension.ALARM, "VSWR", assertion_id="a1")
    a2 = _value_assertion(ContextDimension.ALARM, "LOS", assertion_id="a2")
    a3 = _value_assertion(ContextDimension.ALARM, "VSWR", assertion_id="a3")

    forward = reduce_dimension(ContextDimension.ALARM, [a1, a2, a3])
    backward = reduce_dimension(ContextDimension.ALARM, [a3, a2, a1])
    assert forward.state == backward.state == ContextState.KNOWN
    assert {a.assertion_id for a in forward.accepted} == {a.assertion_id for a in backward.accepted}


def test_reduce_dimension_rejects_assertion_for_a_different_dimension() -> None:
    wrong = _value_assertion(ContextDimension.VENDOR, "Ericsson")
    with pytest.raises(ValueError):
        reduce_dimension(ContextDimension.TECHNOLOGY, [wrong])


# --- compute_context_state ---------------------------------------------------


def test_compute_context_state_groups_by_dimension_and_omits_unasserted_dimensions() -> None:
    assertions = [
        _value_assertion(ContextDimension.VENDOR, "Ericsson", assertion_id="a1"),
        _value_assertion(ContextDimension.ALARM, "VSWR", assertion_id="a2"),
    ]
    state = compute_context_state(assertions)
    assert set(state.keys()) == {ContextDimension.VENDOR, ContextDimension.ALARM}
    assert ContextDimension.TECHNOLOGY not in state
    assert state[ContextDimension.VENDOR].state == ContextState.KNOWN


def test_compute_context_state_empty_input_is_empty_mapping() -> None:
    assert compute_context_state([]) == {}


# --- Provenance survives construction ---------------------------------------


def test_provenance_fields_survive_construction() -> None:
    assertion = ContextAssertion(
        assertion_id="a1",
        dimension=ContextDimension.VENDOR,
        kind=AssertionKind.VALUE,
        raw_value="Ericsson",
        canonical_value="ERICSSON",
        origin=ContextOrigin.TEAMS,
        source_reference="msg-123",
        asserted_at=_NOW,
        created_at=_NOW,
    )
    assert assertion.origin == ContextOrigin.TEAMS
    assert assertion.source_reference == "msg-123"
    assert assertion.asserted_at == _NOW


# --- TelcoContextProfile identity -------------------------------------------


def test_profile_requires_non_blank_identity() -> None:
    with pytest.raises(ValidationError):
        TelcoContextProfile(profile_id="", owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1")
    with pytest.raises(ValidationError):
        TelcoContextProfile(profile_id="p1", owner_kind=ContextProfileOwnerKind.SESSION, owner_id="")


def test_profile_owner_kind_is_closed() -> None:
    profile = TelcoContextProfile(profile_id="p1", owner_kind=ContextProfileOwnerKind.CASE, owner_id="case-1")
    assert profile.owner_kind == ContextProfileOwnerKind.CASE
