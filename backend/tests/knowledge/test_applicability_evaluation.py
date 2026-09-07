"""Phase 5.1B: deterministic applicability evaluation semantics --
MATCH/PARTIAL_MATCH/NOT_APPLICABLE/UNKNOWN, multi-value OR within one
dimension, AND across dimensions, custom dimensions, per-dimension
detail, and the explicit absence of any fuzzy/model-based matching.
"""
from __future__ import annotations

from backend.knowledge.domain.applicability import (
    ApplicabilityContext,
    ApplicabilityOutcome,
    evaluate_applicability,
)
from backend.knowledge.domain.models import Applicability


def _evaluate(applicability_dims: dict, context_dims: dict):
    return evaluate_applicability(Applicability(dimensions=applicability_dims), ApplicabilityContext(dimensions=context_dims))


# --- MATCH ---------------------------------------------------------------


def test_single_dimension_match() -> None:
    result = _evaluate({"vendor": ["Ericsson"]}, {"vendor": ["Ericsson"]})
    assert result.outcome is ApplicabilityOutcome.MATCH


def test_multi_dimension_full_match() -> None:
    result = _evaluate(
        {"vendor": ["Ericsson"], "technology": ["5G"], "release": ["24.Q2"]},
        {"vendor": ["Ericsson"], "technology": ["5G"], "release": ["24.Q2"]},
    )
    assert result.outcome is ApplicabilityOutcome.MATCH
    assert {r.dimension: r.outcome for r in result.dimension_results} == {
        "vendor": ApplicabilityOutcome.MATCH,
        "technology": ApplicabilityOutcome.MATCH,
        "release": ApplicabilityOutcome.MATCH,
    }


# --- NOT_APPLICABLE --------------------------------------------------------


def test_single_dimension_conflict_is_not_applicable() -> None:
    result = _evaluate({"vendor": ["Ericsson"]}, {"vendor": ["Nokia"]})
    assert result.outcome is ApplicabilityOutcome.NOT_APPLICABLE


def test_explicit_conflict_dominates_even_with_a_matching_dimension() -> None:
    result = _evaluate(
        {"vendor": ["Ericsson"], "technology": ["5G"]},
        {"vendor": ["Ericsson"], "technology": ["4G"]},
    )
    assert result.outcome is ApplicabilityOutcome.NOT_APPLICABLE
    by_dim = {r.dimension: r.outcome for r in result.dimension_results}
    assert by_dim["vendor"] is ApplicabilityOutcome.MATCH
    assert by_dim["technology"] is ApplicabilityOutcome.NOT_APPLICABLE


# --- PARTIAL_MATCH ---------------------------------------------------------


def test_one_match_and_one_missing_dimension_is_partial_match() -> None:
    result = _evaluate({"vendor": ["Ericsson"], "release": ["24.Q2"]}, {"vendor": ["Ericsson"]})
    assert result.outcome is ApplicabilityOutcome.PARTIAL_MATCH
    by_dim = {r.dimension: r.outcome for r in result.dimension_results}
    assert by_dim["vendor"] is ApplicabilityOutcome.MATCH
    assert by_dim["release"] is ApplicabilityOutcome.UNKNOWN


# --- UNKNOWN ---------------------------------------------------------------


def test_no_known_context_at_all_is_unknown() -> None:
    result = _evaluate({"vendor": ["Ericsson"], "release": ["24.Q2"]}, {})
    assert result.outcome is ApplicabilityOutcome.UNKNOWN
    assert all(r.outcome is ApplicabilityOutcome.UNKNOWN for r in result.dimension_results)


def test_context_with_unrelated_dimension_only_is_still_unknown() -> None:
    """The context knows something, just not anything relevant to this
    object's constrained dimensions -- still UNKNOWN, not a guess.
    """
    result = _evaluate({"vendor": ["Ericsson"]}, {"market": ["EU"]})
    assert result.outcome is ApplicabilityOutcome.UNKNOWN


# --- UNCONSTRAINED ---------------------------------------------------------


def test_unconstrained_knowledge_is_match_regardless_of_context() -> None:
    result = _evaluate({}, {"vendor": ["Nokia"]})
    assert result.outcome is ApplicabilityOutcome.MATCH
    assert result.dimension_results == []


def test_unconstrained_knowledge_matches_even_an_empty_context() -> None:
    result = _evaluate({}, {})
    assert result.outcome is ApplicabilityOutcome.MATCH


# --- multi-value OR within one dimension ------------------------------


def test_multi_value_or_vendor() -> None:
    result = _evaluate({"vendor": ["Ericsson", "Nokia"]}, {"vendor": ["Nokia"]})
    assert result.outcome is ApplicabilityOutcome.MATCH


def test_multi_value_or_release() -> None:
    result = _evaluate({"release": ["24.Q1", "24.Q2"]}, {"release": ["24.Q2"]})
    assert result.outcome is ApplicabilityOutcome.MATCH


# --- cross-dimension AND -------------------------------------------------


def test_cross_dimension_and_semantics() -> None:
    result = _evaluate(
        {"vendor": ["Ericsson"], "technology": ["5G"]},
        {"vendor": ["Ericsson"], "technology": ["4G"]},
    )
    assert result.outcome is ApplicabilityOutcome.NOT_APPLICABLE


# --- custom dimensions ------------------------------------------------


def test_custom_dimension_behaves_exactly_like_a_canonical_one() -> None:
    result = _evaluate({"hardware_family": ["AIR3268"]}, {"hardware_family": ["AIR3268"]})
    assert result.outcome is ApplicabilityOutcome.MATCH


def test_custom_dimension_not_applicable_when_conflicting() -> None:
    result = _evaluate({"hardware_family": ["AIR3268"]}, {"hardware_family": ["AIR6488"]})
    assert result.outcome is ApplicabilityOutcome.NOT_APPLICABLE


# --- per-dimension detail --------------------------------------------------


def test_matched_values_use_the_knowledge_side_original_casing() -> None:
    result = _evaluate({"vendor": ["Ericsson", "Nokia"]}, {"vendor": ["ERICSSON"]})
    vendor_result = next(r for r in result.dimension_results if r.dimension == "vendor")
    assert vendor_result.matched_values == ["Ericsson"]
    assert vendor_result.required_values == ["Ericsson", "Nokia"]
    assert vendor_result.observed_values == ["ERICSSON"]


def test_dimension_results_are_deterministically_ordered_by_name() -> None:
    result = _evaluate(
        {"vendor": ["Ericsson"], "domain": ["RAN"], "technology": ["5G"]},
        {"vendor": ["Ericsson"], "domain": ["RAN"], "technology": ["5G"]},
    )
    assert [r.dimension for r in result.dimension_results] == ["domain", "technology", "vendor"]


def test_evaluation_round_trips_through_json_deterministically() -> None:
    result = _evaluate({"vendor": ["Ericsson"], "release": ["24.Q2"]}, {"vendor": ["Ericsson"]})
    restored_json = result.model_dump_json()
    assert result.model_validate_json(restored_json) == result


# --- no fuzzy magic ----------------------------------------------------


def test_prefix_substring_does_not_match() -> None:
    result = _evaluate({"vendor": ["Ericsson"]}, {"vendor": ["Eric"]})
    assert result.outcome is ApplicabilityOutcome.NOT_APPLICABLE


def test_synonym_like_terms_do_not_automatically_match() -> None:
    result = _evaluate({"technology": ["5G"]}, {"technology": ["NR"]})
    assert result.outcome is ApplicabilityOutcome.NOT_APPLICABLE


def test_release_point_revision_does_not_automatically_match() -> None:
    result = _evaluate({"release": ["24.Q2"]}, {"release": ["24.Q2.1"]})
    assert result.outcome is ApplicabilityOutcome.NOT_APPLICABLE
