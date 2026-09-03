"""Unit tests for backend.tools.runtime_time -- the deterministic
runtime-time capability incident_manager uses to interpret relative time
expressions.

No ADK Runner needed: `get_current_time_context` is a plain Python
function. `tool_context` is faked with a minimal stand-in exposing just
`.state.get(...)`, matching the one thing this module actually reads off
ADK's real `ToolContext`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from backend.tools.runtime_time import get_current_time_context


class _FakeState:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, key: str, default: Optional[Any] = None) -> Any:
        return self._data.get(key, default)


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = _FakeState(state or {})


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_current_utc_is_returned_and_matches_python_datetime_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLOPANOC_USER_TIMEZONE", raising=False)

    before = datetime.now(timezone.utc)
    result = get_current_time_context(tool_context=None)
    after = datetime.now(timezone.utc)

    returned = _parse(result["current_datetime_utc"])
    assert before <= returned <= after


def test_valid_iana_timezone_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLOPANOC_USER_TIMEZONE", "Asia/Tokyo")

    result = get_current_time_context(tool_context=None)

    assert result["user_timezone"] == "Asia/Tokyo"
    utc_dt = _parse(result["current_datetime_utc"])
    local_dt = datetime.fromisoformat(result["current_datetime_user_timezone"])
    assert local_dt.utcoffset() == timedelta(hours=9)
    assert local_dt.astimezone(timezone.utc) == utc_dt


def test_timezone_from_session_state_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLOPANOC_USER_TIMEZONE", raising=False)
    ctx = _FakeToolContext(state={"user_timezone": "America/New_York"})

    result = get_current_time_context(tool_context=ctx)

    assert result["user_timezone"] == "America/New_York"
    assert result["timezone_source"] == "session"


def test_session_timezone_takes_precedence_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLOPANOC_USER_TIMEZONE", "Asia/Tokyo")
    ctx = _FakeToolContext(state={"user_timezone": "America/New_York"})

    result = get_current_time_context(tool_context=ctx)

    assert result["user_timezone"] == "America/New_York"
    assert result["timezone_source"] == "session"


def test_env_var_fallback_when_no_session_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLOPANOC_USER_TIMEZONE", "Europe/Paris")

    result = get_current_time_context(tool_context=_FakeToolContext(state={}))

    assert result["user_timezone"] == "Europe/Paris"
    assert result["timezone_source"] == "env"


def test_falls_back_to_utc_when_nothing_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLOPANOC_USER_TIMEZONE", raising=False)

    result = get_current_time_context(tool_context=None)

    assert result["user_timezone"] == "UTC"
    assert result["timezone_source"] == "default_utc"
    assert "requested_timezone_invalid" not in result


def test_invalid_timezone_is_rejected_safely_and_falls_back_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLOPANOC_USER_TIMEZONE", "Not/AZone")

    result = get_current_time_context(tool_context=None)

    assert result["user_timezone"] == "UTC"
    assert result["timezone_source"] == "invalid_fallback_utc"
    assert result["requested_timezone_invalid"] == "Not/AZone"


def test_invalid_session_timezone_falls_through_to_a_valid_env_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLOPANOC_USER_TIMEZONE", "Asia/Tokyo")
    ctx = _FakeToolContext(state={"user_timezone": "Not/AZone"})

    result = get_current_time_context(tool_context=ctx)

    assert result["user_timezone"] == "Asia/Tokyo"
    assert result["timezone_source"] == "env"


def test_today_boundary_conversion_across_timezone_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asia/Tokyo is a fixed UTC+9 offset (no DST) -- a stable,
    reproducible way to prove `day_start_utc` reflects the *local* day
    boundary, not the UTC day boundary.
    """
    monkeypatch.setenv("SLOPANOC_USER_TIMEZONE", "Asia/Tokyo")

    result = get_current_time_context(tool_context=None, days_ago=0)

    day_start = _parse(result["day_start_utc"])
    day_end = _parse(result["day_end_utc"])

    assert day_end - day_start == timedelta(days=1)
    # Tokyo local midnight is always 15:00 UTC the previous calendar day.
    assert (day_start.hour, day_start.minute, day_start.second) == (15, 0, 0)


def test_yesterday_boundary_is_exactly_one_day_before_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLOPANOC_USER_TIMEZONE", "Asia/Tokyo")

    today = get_current_time_context(tool_context=None, days_ago=0)
    yesterday = get_current_time_context(tool_context=None, days_ago=1)

    today_start = _parse(today["day_start_utc"])
    yesterday_start = _parse(yesterday["day_start_utc"])
    yesterday_end = _parse(yesterday["day_end_utc"])

    assert today_start - yesterday_start == timedelta(days=1)
    assert yesterday_end == today_start


def test_days_ago_defaults_to_zero() -> None:
    result = get_current_time_context(tool_context=None)
    assert result["days_ago"] == 0
