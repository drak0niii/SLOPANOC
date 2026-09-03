"""Deterministic UTC timestamp parsing and time-range filtering for Teams
message retrieval.

Natural-language time interpretation ("today", "last 7 days", "since
Monday") belongs entirely to the agent layer (incident_manager's prompt,
using conversation/user timezone context when available) --
this module only ever accepts already-normalized ISO-8601 timestamps with
an explicit UTC offset and performs plain, deterministic parsing/
comparison. It never understands relative time words and never assumes a
timezone that isn't explicitly present in the input. See
docs/TEAMS_TOOL_CONTRACT.md #4c.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

# Accepts: YYYY-MM-DDTHH:MM:SS, optionally .fraction (any digit count --
# Microsoft Graph `createdDateTime` commonly uses 7 digits), and either a
# literal "Z" or a numeric +HH:MM/-HH:MM/+HHMM/-HHMM offset. Timezone is
# required -- a timestamp with no offset is rejected rather than assumed
# to be UTC, so callers are never silently wrong about what instant was
# meant.
_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d+))?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})$"
)


class InvalidTimestampError(ValueError):
    """A timestamp string is not a recognized ISO-8601 value with an
    explicit UTC offset."""


def parse_utc_timestamp(value: str) -> datetime:
    """Tolerantly parse an ISO-8601 timestamp into an aware UTC
    `datetime`.

    Accepts any fractional-second precision (Teams'/Graph's own
    `createdDateTime` commonly uses 7 digits, beyond what
    `datetime.fromisoformat` alone reliably handles across versions) and
    either `Z` or a numeric offset, always normalizing the result to UTC
    so timestamps from different sources/precisions compare correctly.

    Raises `InvalidTimestampError` for anything else (missing timezone,
    malformed date/time, non-string input).
    """
    if not isinstance(value, str):
        raise InvalidTimestampError(
            f"timestamp must be a string, got {type(value).__name__}"
        )

    match = _TIMESTAMP_PATTERN.match(value.strip())
    if not match:
        raise InvalidTimestampError(f"not a recognized ISO-8601 timestamp: {value!r}")

    frac = match.group("frac") or ""
    # Truncate/pad to microsecond precision -- sub-microsecond differences
    # are irrelevant for chat-message ordering/comparison.
    microseconds = int((frac + "000000")[:6]) if frac else 0

    tz = match.group("tz")
    if tz == "Z":
        tzinfo = timezone.utc
    else:
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        offset = timedelta(hours=int(digits[0:2]), minutes=int(digits[2:4]))
        tzinfo = timezone(sign * offset)

    try:
        naive = datetime.fromisoformat(f"{match.group('date')}T{match.group('time')}")
    except ValueError as exc:
        raise InvalidTimestampError(str(exc)) from exc

    aware = naive.replace(microsecond=microseconds, tzinfo=tzinfo)
    return aware.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    """Canonical `Z`-suffixed ISO-8601 string for an aware UTC `datetime`,
    used to echo back validated `from_datetime`/`to_datetime` boundaries.
    """
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def in_range(
    sent_at: str, from_dt: Optional[datetime], to_dt: Optional[datetime]
) -> bool:
    """True if `sent_at` (a message's own ISO-8601 timestamp) falls in
    `[from_dt, to_dt)` -- lower bound inclusive, upper bound exclusive. A
    `None` bound is treated as unbounded on that side. A malformed
    `sent_at` is treated as out of range (excluded) rather than raising --
    message-shape validation already happened earlier in the pipeline.
    """
    if from_dt is None and to_dt is None:
        return True
    try:
        parsed = parse_utc_timestamp(sent_at)
    except InvalidTimestampError:
        return False
    if from_dt is not None and parsed < from_dt:
        return False
    if to_dt is not None and parsed >= to_dt:
        return False
    return True


def cursor_advanced(oldest_in_page: str, cursor: str) -> bool:
    """True if `oldest_in_page` represents a strictly earlier instant
    than `cursor`.

    Uses tolerant UTC parsing rather than raw string comparison, because
    `cursor` may -- on the very first page, when `to_datetime` seeds
    retrieval -- be an externally-supplied boundary with different
    formatting/precision than Teams' own timestamps, where plain
    lexicographic comparison is not reliable (e.g. a `to_datetime` with no
    fractional seconds vs. a Teams timestamp with 7 digits). Falls back to
    lexicographic comparison only if either value doesn't match the
    tolerant parser, which should not happen for well-formed Teams
    timestamps or already-validated input.
    """
    try:
        return parse_utc_timestamp(oldest_in_page) < parse_utc_timestamp(cursor)
    except InvalidTimestampError:
        return oldest_in_page < cursor
