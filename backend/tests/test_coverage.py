"""Unit tests for backend.tools.teams.coverage -- deterministic
retrieval-coverage classification. Pure functions, no mocking needed.
"""
from __future__ import annotations

from backend.tools.teams.coverage import (
    build_coverage,
    classify_coverage,
    classify_coverage_from_result,
)
from backend.tools.teams.schemas import CoverageStatus, TeamsCoverage


def test_explicit_range_fully_covered_is_full_range() -> None:
    status = classify_coverage("2026-08-25T00:00:00Z", None, True, False)
    assert status == CoverageStatus.FULL_RANGE


def test_explicit_range_partially_covered_is_partial_range() -> None:
    status = classify_coverage("2026-08-25T00:00:00Z", None, False, True)
    assert status == CoverageStatus.PARTIAL_RANGE


def test_no_range_truncated_is_latest_window() -> None:
    status = classify_coverage(None, None, True, True)
    assert status == CoverageStatus.LATEST_WINDOW


def test_no_range_not_truncated_is_complete() -> None:
    status = classify_coverage(None, None, True, False)
    assert status == CoverageStatus.COMPLETE


def test_only_requested_to_still_counts_as_an_explicit_range() -> None:
    assert classify_coverage(None, "2026-08-28T00:00:00Z", True, False) == (
        CoverageStatus.FULL_RANGE
    )
    assert classify_coverage(None, "2026-08-28T00:00:00Z", False, True) == (
        CoverageStatus.PARTIAL_RANGE
    )


def test_complete_empty_requested_range_is_still_full_range() -> None:
    """Fully covering an explicit range that happens to contain zero
    messages is a valid, complete result -- not a retrieval failure.
    """
    coverage = build_coverage(
        requested_from="2026-08-25T00:00:00Z",
        requested_to="2026-08-26T00:00:00Z",
        range_fully_covered=True,
        truncated=False,
        retrieved_count=0,
        oldest_retrieved_at=None,
        newest_retrieved_at=None,
    )

    assert coverage.status == CoverageStatus.FULL_RANGE
    assert coverage.retrieved_count == 0


def test_partial_empty_requested_range_is_partial_range() -> None:
    coverage = build_coverage(
        requested_from="2026-08-25T00:00:00Z",
        requested_to=None,
        range_fully_covered=False,
        truncated=True,
        retrieved_count=0,
        oldest_retrieved_at=None,
        newest_retrieved_at=None,
    )

    assert coverage.status == CoverageStatus.PARTIAL_RANGE
    assert coverage.retrieved_count == 0


def test_coverage_object_does_not_expose_next_before_or_raw_booleans() -> None:
    assert "next_before" not in TeamsCoverage.model_fields
    assert "range_fully_covered" not in TeamsCoverage.model_fields
    assert "truncated" not in TeamsCoverage.model_fields


def test_retrieved_count_is_preserved() -> None:
    coverage = build_coverage(
        requested_from=None,
        requested_to=None,
        range_fully_covered=True,
        truncated=False,
        retrieved_count=42,
        oldest_retrieved_at=None,
        newest_retrieved_at=None,
    )

    assert coverage.retrieved_count == 42


def test_oldest_and_newest_timestamps_are_preserved() -> None:
    coverage = build_coverage(
        requested_from=None,
        requested_to=None,
        range_fully_covered=True,
        truncated=False,
        retrieved_count=2,
        oldest_retrieved_at="2026-08-25T09:00:00Z",
        newest_retrieved_at="2026-08-26T09:00:00Z",
    )

    assert coverage.oldest_retrieved_at == "2026-08-25T09:00:00Z"
    assert coverage.newest_retrieved_at == "2026-08-26T09:00:00Z"


def test_classify_from_none_degrades_safely_to_the_conservative_default() -> None:
    # No range, and malformed/missing `truncated` conservatively means
    # "assume truncated" -- never a false claim of completeness.
    assert classify_coverage_from_result(None) == CoverageStatus.LATEST_WINDOW


def test_classify_from_empty_dict_degrades_safely() -> None:
    assert classify_coverage_from_result({}) == CoverageStatus.LATEST_WINDOW


def test_classify_from_wrong_typed_fields_degrades_safely() -> None:
    result = {"requested_from": 123, "truncated": "yes", "range_fully_covered": "no"}
    assert classify_coverage_from_result(result) == CoverageStatus.LATEST_WINDOW


def test_classify_from_result_with_valid_fields() -> None:
    result = {
        "requested_from": "2026-08-25T00:00:00Z",
        "requested_to": None,
        "range_fully_covered": True,
        "truncated": False,
    }
    assert classify_coverage_from_result(result) == CoverageStatus.FULL_RANGE
