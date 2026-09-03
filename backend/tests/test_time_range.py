"""Unit tests for backend.tools.teams.time_range -- deterministic UTC
timestamp parsing and range filtering. Pure functions, no mocking needed.
"""
from __future__ import annotations

import pytest

from backend.tools.teams.time_range import (
    InvalidTimestampError,
    cursor_advanced,
    format_utc,
    in_range,
    parse_utc_timestamp,
)


def test_parses_z_suffixed_timestamp() -> None:
    dt = parse_utc_timestamp("2026-08-25T00:00:00Z")
    assert dt.isoformat() == "2026-08-25T00:00:00+00:00"


def test_parses_seven_digit_fractional_seconds() -> None:
    """Microsoft Graph `createdDateTime` commonly uses 7-digit fractional
    seconds -- must not raise.
    """
    dt = parse_utc_timestamp("2026-08-25T00:00:00.1234567Z")
    assert dt.microsecond == 123456


def test_parses_numeric_offset_with_colon() -> None:
    dt = parse_utc_timestamp("2026-08-25T02:00:00+02:00")
    assert dt.isoformat() == "2026-08-25T00:00:00+00:00"


def test_parses_numeric_offset_without_colon() -> None:
    dt = parse_utc_timestamp("2026-08-25T02:00:00+0200")
    assert dt.isoformat() == "2026-08-25T00:00:00+00:00"


def test_rejects_missing_timezone() -> None:
    with pytest.raises(InvalidTimestampError):
        parse_utc_timestamp("2026-08-25T00:00:00")


def test_rejects_relative_natural_language_input() -> None:
    """The tool layer must never understand "today"/"yesterday" -- that
    belongs to the agent layer.
    """
    with pytest.raises(InvalidTimestampError):
        parse_utc_timestamp("today")


def test_rejects_non_string_input() -> None:
    with pytest.raises(InvalidTimestampError):
        parse_utc_timestamp(12345)  # type: ignore[arg-type]


def test_format_utc_round_trips() -> None:
    dt = parse_utc_timestamp("2026-08-25T00:00:00Z")
    assert format_utc(dt) == "2026-08-25T00:00:00Z"


def test_in_range_lower_bound_is_inclusive() -> None:
    from_dt = parse_utc_timestamp("2026-08-25T00:00:00Z")
    assert in_range("2026-08-25T00:00:00.000Z", from_dt, None) is True


def test_in_range_upper_bound_is_exclusive() -> None:
    to_dt = parse_utc_timestamp("2026-08-28T00:00:00Z")
    assert in_range("2026-08-28T00:00:00.000Z", None, to_dt) is False


def test_in_range_just_before_upper_bound_is_included() -> None:
    to_dt = parse_utc_timestamp("2026-08-28T00:00:00Z")
    assert in_range("2026-08-27T23:59:59.999999Z", None, to_dt) is True


def test_in_range_with_no_bounds_is_always_true() -> None:
    assert in_range("anything, even invalid", None, None) is True


def test_in_range_malformed_sent_at_is_excluded_not_raised() -> None:
    from_dt = parse_utc_timestamp("2026-08-25T00:00:00Z")
    assert in_range("not-a-timestamp", from_dt, None) is False


def test_cursor_advanced_true_for_strictly_older() -> None:
    assert cursor_advanced("2026-08-24T00:00:00Z", "2026-08-25T00:00:00Z") is True


def test_cursor_advanced_false_when_equal() -> None:
    assert cursor_advanced("2026-08-25T00:00:00Z", "2026-08-25T00:00:00Z") is False


def test_cursor_advanced_handles_precision_mismatch() -> None:
    """Naive lexicographic comparison would incorrectly treat a message
    just after midnight (with sub-second precision) as older than a
    plain `to_datetime` cursor at exactly midnight, because "." sorts
    before "Z". This is exactly the scenario introduced by seeding the
    pagination cursor with an externally-supplied `to_datetime`
    (get_messages.py) -- proper UTC parsing must get it right.
    """
    message_ts = "2026-08-25T00:00:00.5000000Z"  # 0.5s after midnight: NEWER than the cursor
    cursor = "2026-08-25T00:00:00Z"

    # Sanity-check the naive/wrong answer this function guards against.
    assert (message_ts < cursor) is True

    assert cursor_advanced(message_ts, cursor) is False
