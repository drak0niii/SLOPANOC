"""Tests for backend/api/perf_timing.py -- developer-only performance
instrumentation (pre-4H latency investigation pass). Every test uses a
deterministic fake clock -- never a real `time.sleep`/wall-clock
assertion (see this module's own docstring: "never brittle wall-clock
assertions against external services").
"""
from __future__ import annotations

import logging

import pytest

from backend.api.perf_timing import DelegationTimer, PerfTimer
from backend.tests._api_fakes import FakeEvent, FakeFunctionCall, FakeFunctionResponse


class _FakeClock:
    """A deterministic, manually-advanced stand-in for `time.monotonic`."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def __call__(self) -> float:
        return self._now


# --- PerfTimer ---------------------------------------------------------


def test_mark_reports_duration_since_the_previous_mark() -> None:
    clock = _FakeClock()
    timer = PerfTimer("run-1", clock=clock)

    clock.advance(0.5)
    duration_ms = timer.mark("session_loaded")

    assert duration_ms == pytest.approx(500.0)


def test_mark_duration_is_relative_not_cumulative() -> None:
    clock = _FakeClock()
    timer = PerfTimer("run-1", clock=clock)

    clock.advance(1.0)
    first = timer.mark("a")
    clock.advance(0.25)
    second = timer.mark("b")

    assert first == pytest.approx(1000.0)
    assert second == pytest.approx(250.0)


def test_elapsed_seconds_is_cumulative_from_construction() -> None:
    clock = _FakeClock()
    timer = PerfTimer("run-1", clock=clock)

    clock.advance(0.3)
    timer.mark("a")
    clock.advance(0.4)

    assert timer.elapsed_seconds() == pytest.approx(0.7)


def test_mark_logs_only_safe_structured_fields(caplog: pytest.LogCaptureFixture) -> None:
    clock = _FakeClock()
    timer = PerfTimer("run-safe-1", clock=clock)
    clock.advance(0.01)

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        timer.mark("teams_get_messages")

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "stage=teams_get_messages" in message
    assert "run_id=run-safe-1" in message
    assert "duration_ms=" in message
    # Never a message body, prompt, chat_id, token, or raw payload -- this
    # log call only ever receives a stage name, run_id, and float.
    for forbidden in ("chat_id", "message_id", "prompt", "token", "payload"):
        assert forbidden not in message


def test_log_duration_uses_the_same_safe_shape(caplog: pytest.LogCaptureFixture) -> None:
    timer = PerfTimer("run-2", clock=_FakeClock())

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        timer.log_duration("incident_manager_delegation", 1.234)

    message = caplog.records[0].getMessage()
    assert "stage=incident_manager_delegation" in message
    assert "run_id=run-2" in message
    assert "duration_ms=1234.0" in message


def test_log_duration_does_not_disturb_the_mark_sequence() -> None:
    clock = _FakeClock()
    timer = PerfTimer("run-3", clock=clock)

    clock.advance(0.2)
    timer.log_duration("teams_get_messages", 99.0)  # unrelated, independently-measured duration
    clock.advance(0.1)
    duration_ms = timer.mark("next_stage")

    # log_duration must not have reset `_last` -- "next_stage" still
    # measures the full 0.3s since construction, not just the 0.1s since
    # the log_duration call.
    assert duration_ms == pytest.approx(300.0)


# --- DelegationTimer -----------------------------------------------------


def test_delegation_timer_measures_call_to_response_span() -> None:
    clock = _FakeClock()
    timer = DelegationTimer(clock=clock)

    timer.observe(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    clock.advance(2.5)
    timer.observe(FakeEvent(final=False, function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "ok"})]))

    assert timer.total_seconds == pytest.approx(2.5)
    assert timer.call_count == 1


def test_delegation_timer_ignores_other_tool_names() -> None:
    clock = _FakeClock()
    timer = DelegationTimer(clock=clock)

    timer.observe(FakeEvent(final=False, function_calls=[FakeFunctionCall("record_case_analysis")]))
    clock.advance(1.0)
    timer.observe(FakeEvent(final=False, function_responses=[FakeFunctionResponse("record_case_analysis", {})]))

    assert timer.total_seconds == 0.0
    assert timer.call_count == 0


def test_delegation_timer_ignores_partial_events() -> None:
    clock = _FakeClock()
    timer = DelegationTimer(clock=clock)

    timer.observe(FakeEvent(final=False, partial=True, function_calls=[FakeFunctionCall("incident_manager")]))
    clock.advance(1.0)
    timer.observe(FakeEvent(final=False, function_responses=[FakeFunctionResponse("incident_manager", {})]))

    # The call event was ignored (partial), so no matching start exists --
    # the response is a no-op, never a negative/garbage duration.
    assert timer.total_seconds == 0.0
    assert timer.call_count == 0


def test_delegation_timer_accumulates_across_multiple_calls_in_one_turn() -> None:
    clock = _FakeClock()
    timer = DelegationTimer(clock=clock)

    timer.observe(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    clock.advance(1.0)
    timer.observe(FakeEvent(final=False, function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "selection_needed"})]))

    timer.observe(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    clock.advance(3.0)
    timer.observe(FakeEvent(final=False, function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "ok"})]))

    assert timer.total_seconds == pytest.approx(4.0)
    assert timer.call_count == 2


def test_delegation_timer_never_started_if_no_delegation_happens() -> None:
    timer = DelegationTimer(clock=_FakeClock())
    timer.observe(FakeEvent(text="hello", final=True))
    assert timer.total_seconds == 0.0
    assert timer.call_count == 0
