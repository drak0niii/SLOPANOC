"""ADK tool: `get_current_time_context`.

A small, deterministic runtime-time capability that closes the temporal-
grounding gap in relative time-expression handling: incident_manager must
never guess "now" (or do its own day-boundary arithmetic) when
interpreting expressions like "today", "yesterday", "last 7 days", or
"since Monday" -- it calls this tool instead.

Not Teams-domain (no Power Automate call, no chat/message data) --
deliberately kept separate from backend/tools/teams/.

DETERMINISM:
  - The current UTC instant comes from `datetime.now(timezone.utc)` at
    call time -- never from LLM reasoning.
  - Timezone conversion uses the Python standard library `zoneinfo` (IANA
    tzdata), not a hand-rolled offset table.
  - Day-boundary computation (`day_start_utc`/`day_end_utc`, via the
    `days_ago` parameter) is also computed here, in Python, against the
    resolved timezone's actual UTC offset *for that calendar day*
    (correctly handling DST transitions, since it constructs local
    midnight directly rather than subtracting 24 hours from "now") --
    not left to the model to compute. `days_ago=0` is "today",
    `days_ago=1` is "yesterday", and any small non-negative integer
    covers "the last N days" / "since <weekday>" (incident_manager
    computes which small integer that is -- e.g. counting back to the
    most recent Monday -- which is a much lower-risk calculation than
    determining "what is today" itself, the actual problem this tool
    exists to remove).

TIMEZONE RESOLUTION PRECEDENCE (see `_resolve_timezone_name`):
  1. ADK/session state (`ToolContext.state["user_timezone"]`), if present
     and a valid IANA name.
  2. The `SLOPANOC_USER_TIMEZONE` environment variable (local/manual
     development), if present and a valid IANA name.
  3. UTC, explicitly -- never a hardcoded non-UTC default.
An invalid name at a given source is rejected safely (never raises) and
falls through to the next source; the final result always reports
`timezone_source` and, if something was rejected, `requested_timezone_invalid`,
so the caller/prompt can be transparent about what happened rather than
silently misinterpreting a typo'd timezone as UTC.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.adk.tools import ToolContext

_SESSION_STATE_KEY = "user_timezone"
_ENV_VAR = "SLOPANOC_USER_TIMEZONE"
_DEFAULT_TIMEZONE = "UTC"


def _is_valid_iana_timezone(name: Any) -> bool:
    if not isinstance(name, str) or not name.strip():
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False


def _resolve_timezone_name(
    tool_context: Optional[ToolContext],
) -> tuple[str, str, Optional[str]]:
    """Resolve which IANA timezone name to use, and how it was chosen.

    Returns `(resolved_name, source, requested_but_invalid_name)`, where
    `source` is one of `"session"`, `"env"`, `"default_utc"`, or
    `"invalid_fallback_utc"`, and `requested_but_invalid_name` is set only
    when some source supplied a name that turned out not to be a valid
    IANA timezone.
    """
    candidates: list[tuple[str, str]] = []

    if tool_context is not None:
        session_value = tool_context.state.get(_SESSION_STATE_KEY)
        if session_value:
            candidates.append((session_value, "session"))

    env_value = os.environ.get(_ENV_VAR)
    if env_value:
        candidates.append((env_value, "env"))

    for name, source in candidates:
        if _is_valid_iana_timezone(name):
            return name, source, None

    if candidates:
        # Something was supplied, at one or more sources, but none of it
        # was a valid IANA name -- reject safely rather than raising, and
        # report the first rejected value for transparency.
        return _DEFAULT_TIMEZONE, "invalid_fallback_utc", candidates[0][0]

    return _DEFAULT_TIMEZONE, "default_utc", None


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _day_start_local(reference_date: date, zone: ZoneInfo) -> datetime:
    """Local midnight for `reference_date` in `zone`, constructed directly
    (not by subtracting a fixed 24h from "now") so the correct UTC offset
    for that specific calendar day -- including any DST transition -- is
    used when converting to UTC.
    """
    return datetime.combine(reference_date, time.min, tzinfo=zone)


def get_current_time_context(
    tool_context: ToolContext, days_ago: int = 0
) -> dict[str, Any]:
    """Return the actual current time, deterministically, plus the
    resolved user timezone and a ready-made day boundary -- for
    interpreting relative time expressions before calling
    `teams_get_messages`. Never call this for an absolute date/timestamp
    the user already gave explicitly (e.g. "between 25 August and 28
    August") -- that does not depend on "now" and does not need this tool.

    Args:
      days_ago: How many days before today (in the resolved user
        timezone) to compute the boundary for. `0` = today, `1` =
        yesterday, `7` = 7 days ago (useful as the `from_datetime` for
        "the last 7 days"), etc. Defaults to `0`.

    Returns a dict with:
      current_datetime_utc: the actual current instant, ISO-8601 UTC
        (e.g. "2026-08-31T12:34:56Z"), from `datetime.now(timezone.utc)`
        -- never guessed or inferred by the model.
      user_timezone: the resolved IANA timezone name actually used (e.g.
        "Europe/Bucharest", or "UTC" if none could be resolved).
      current_datetime_user_timezone: the same instant, converted into
        `user_timezone`, ISO-8601 with its UTC offset.
      days_ago: echoes the input, for traceability.
      day_start_utc: the UTC instant of local midnight, `days_ago` days
        before today, in `user_timezone` -- e.g. for `days_ago=0` this is
        "today"'s `from_datetime`; for `days_ago=1`, "yesterday"'s.
      day_end_utc: `day_start_utc` plus one local day -- together,
        `[day_start_utc, day_end_utc)` bounds exactly the single calendar
        day `days_ago` days before today (e.g. "today" is
        `days_ago=0`'s `[day_start_utc, day_end_utc)`; "yesterday" is
        `days_ago=1`'s).
      timezone_source: how `user_timezone` was resolved -- "session"
        (ADK/session state), "env" (SLOPANOC_USER_TIMEZONE, local
        development), "default_utc" (nothing else was available), or
        "invalid_fallback_utc" (something was supplied but was not a
        valid IANA timezone name, so UTC was used instead).
      requested_timezone_invalid: the rejected timezone name, only present
        when `timezone_source` is "invalid_fallback_utc".
    """
    now_utc = datetime.now(timezone.utc)

    tz_name, tz_source, requested_invalid = _resolve_timezone_name(tool_context)
    zone = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(zone)

    reference_date = now_local.date() - timedelta(days=days_ago)
    day_start_local = _day_start_local(reference_date, zone)
    day_end_local = _day_start_local(reference_date + timedelta(days=1), zone)

    result: dict[str, Any] = {
        "current_datetime_utc": _format_utc(now_utc),
        "user_timezone": tz_name,
        "current_datetime_user_timezone": now_local.isoformat(),
        "days_ago": days_ago,
        "day_start_utc": _format_utc(day_start_local),
        "day_end_utc": _format_utc(day_end_local),
        "timezone_source": tz_source,
    }
    if requested_invalid is not None:
        result["requested_timezone_invalid"] = requested_invalid
    return result
