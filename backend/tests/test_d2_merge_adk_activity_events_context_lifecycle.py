"""D2 corrective pass regression tests.

REAL-STACK DEFECT: `backend.api.chat_service._merge_adk_and_activity_events`
used to drive the ADK Runner's own event generator (`agen`) with a fresh
`asyncio.ensure_future(agen.__anext__())` on EVERY loop iteration -- each
call wrapping that ONE resumption of `agen` in a brand-new asyncio Task.
`asyncio.ensure_future`/`create_task` copies the current
`contextvars.Context` at Task-creation time (the SAME mechanism this
codebase's own `direct_read_fast_path.py` already independently proved
and documented for a different ContextVar, `_pending_trusted_result` --
see that module's own "ContextVar bridge never worked" correction-pass
docstring). Installed `google-adk==1.33.0`'s `Runner.run_async`
(`runners.py`'s `_run_with_trace`) wraps its own generator body in `with
tracer.start_as_current_span('invocation'):` -- a context manager whose
`attach()` runs on whichever Task first resumes the generator and whose
`detach()` runs on whichever Task resumes it LAST (at exhaustion).
Handing every resumption to a fresh Task meant `attach()`/`detach()`
almost always ran in different `contextvars.Context` objects, so
OpenTelemetry's own `ContextVar.reset(token)` raised `ValueError: Token
... was created in a different Context` on effectively every multi-event
turn -- caught and merely logged by `opentelemetry.context.detach()`'s
own `try/except`, so requests still succeeded, but the tracing lifecycle
was genuinely broken.

FIX: `_merge_adk_and_activity_events` now drives `agen` via exactly ONE
persistent task (`_drain_agen`, created once) that consumes it with a
plain `async for` loop into an internal `asyncio.Queue` -- every
resumption of `agen`, first to last, now happens inside that SAME
Task/Context.

These tests reproduce the ACTUAL async lifecycle hazard directly with a
real `contextvars.ContextVar` -- a fake generator shaped exactly like
ADK's own `_run_with_trace` (`token = cv.set(...)` before the first
yield, `cv.reset(token)` in a `finally` after the last) proves the SAME
Token/Context pairing OpenTelemetry relies on survives being driven
through the merge function, for any number of yielded items, with or
without a concurrent activity channel -- never a log-string assertion,
never an arbitrary sleep (ordering is controlled with `asyncio.Event`).
"""
from __future__ import annotations

import asyncio
import contextvars
from typing import Any, AsyncIterator

import pytest

from backend.api.chat_service import _merge_adk_and_activity_events

_probe_var: "contextvars.ContextVar[str]" = contextvars.ContextVar("d2_probe_var", default="untouched")


async def _traced_agen(events: list[Any], reset_results: list[bool]) -> AsyncIterator[Any]:
    """Mirrors ADK's own `Runner.run_async`/`_run_with_trace` shape
    exactly: a `contextvars.Token` is created before the first yield and
    reset in a `finally` after the last -- precisely what
    `tracer.start_as_current_span('invocation')` does internally via
    `opentelemetry.context.attach`/`detach`. `reset_results` records
    whether the eventual `.reset()` call succeeded (`True`) or raised the
    exact real-stack `ValueError` (`False`) -- the pass/fail signal these
    tests check, never a log string.
    """
    token = _probe_var.set("attached-for-this-generator")
    try:
        for event in events:
            yield event
    finally:
        try:
            _probe_var.reset(token)
            reset_results.append(True)
        except ValueError:
            reset_results.append(False)


@pytest.mark.asyncio
async def test_multi_event_agen_survives_with_no_activity_channel() -> None:
    """`activity_channel=None` degrades to plain passthrough -- unaffected
    by this correction, but confirms the baseline ContextVar pairing
    works.
    """
    reset_results: list[bool] = []
    agen = _traced_agen(["e1", "e2", "e3"], reset_results)

    merged = [item async for item in _merge_adk_and_activity_events(agen, None)]

    assert [m.item for m in merged] == ["e1", "e2", "e3"]
    assert reset_results == [True]


@pytest.mark.asyncio
async def test_multi_event_agen_survives_with_a_real_activity_channel() -> None:
    """THE reproduction: with an activity channel present (the code path
    that previously created a fresh asyncio Task per `agen` item), a real
    ContextVar Token created before the first yield must still be
    resettable after the last -- proving `agen` was driven by one
    consistent Task throughout, not a fresh one per item. Before this
    correction, `reset_results` here would be `[False]`.
    """
    reset_results: list[bool] = []
    agen = _traced_agen(["e1", "e2", "e3", "e4"], reset_results)
    channel: "asyncio.Queue[Any]" = asyncio.Queue()

    merged = [item async for item in _merge_adk_and_activity_events(agen, channel)]

    assert [m.item for m in merged] == ["e1", "e2", "e3", "e4"]
    assert all(m.source == "adk" for m in merged)
    assert reset_results == [True]


@pytest.mark.asyncio
async def test_single_event_agen_also_survives() -> None:
    """Even a single-event turn (attach+detach would coincidentally land
    on the same task even under the old per-item-task code) must keep
    working -- non-regression for the simplest case.
    """
    reset_results: list[bool] = []
    agen = _traced_agen(["only"], reset_results)
    channel: "asyncio.Queue[Any]" = asyncio.Queue()

    merged = [item async for item in _merge_adk_and_activity_events(agen, channel)]

    assert [m.item for m in merged] == ["only"]
    assert reset_results == [True]


@pytest.mark.asyncio
async def test_many_events_repeatedly_survive_the_persistent_task() -> None:
    """A longer event stream (closer to a real multi-tool-call turn) --
    proves the persistent-task fix holds regardless of event count.
    """
    reset_results: list[bool] = []
    events = [f"e{i}" for i in range(25)]
    agen = _traced_agen(events, reset_results)
    channel: "asyncio.Queue[Any]" = asyncio.Queue()

    merged = [item async for item in _merge_adk_and_activity_events(agen, channel)]

    assert [m.item for m in merged] == events
    assert reset_results == [True]


@pytest.mark.asyncio
async def test_activity_events_interleave_with_adk_events_in_arrival_order() -> None:
    """Ordering/interleaving semantics are unaffected by the persistent-
    task refactor: an activity event reported while the outer Runner is
    still "blocked" is not stuck behind it. Paced against the MERGE
    GENERATOR's own observed output (never the inner `agen`'s internal
    progress, and never a sleep) -- driving `agen` through an internal
    queue means it may legitimately run ahead of what the caller has
    consumed so far, so the test must synchronize on what the caller
    actually sees, not on `agen`'s own execution point.
    """
    reset_results: list[bool] = []
    adk_may_continue = asyncio.Event()

    async def agen() -> AsyncIterator[Any]:
        token = _probe_var.set("interleave")
        try:
            yield "adk-1"
            await adk_may_continue.wait()
            yield "adk-2"
        finally:
            _probe_var.reset(token)
            reset_results.append(True)

    channel: "asyncio.Queue[Any]" = asyncio.Queue()
    merge_gen = _merge_adk_and_activity_events(agen(), channel)

    first = await merge_gen.__anext__()
    assert (first.source, first.item) == ("adk", "adk-1")

    # Only now -- after the caller has genuinely observed "adk-1" -- queue
    # the activity item and let the ADK side proceed.
    await channel.put("activity-1")
    adk_may_continue.set()

    second = await merge_gen.__anext__()
    third = await merge_gen.__anext__()
    with pytest.raises(StopAsyncIteration):
        await merge_gen.__anext__()

    results = [(first.source, first.item), (second.source, second.item), (third.source, third.item)]
    assert results == [("adk", "adk-1"), ("activity", "activity-1"), ("adk", "adk-2")]
    assert reset_results == [True]


@pytest.mark.asyncio
async def test_real_exception_from_agen_propagates_to_the_caller() -> None:
    """A genuine failure inside `agen` (never `StopAsyncIteration`) must
    still propagate out of `_merge_adk_and_activity_events`, exactly as
    before this correction -- the persistent-task relay must not swallow
    or transform it.
    """

    class _Boom(Exception):
        pass

    async def failing_agen() -> AsyncIterator[Any]:
        yield "before-failure"
        raise _Boom("real runner failure")

    channel: "asyncio.Queue[Any]" = asyncio.Queue()

    collected: list[Any] = []
    with pytest.raises(_Boom):
        async for item in _merge_adk_and_activity_events(failing_agen(), channel):
            collected.append(item.item)

    assert collected == ["before-failure"]


@pytest.mark.asyncio
async def test_agen_raising_cancellederror_directly_does_not_deadlock() -> None:
    """DEADLOCK FOUND AND FIXED during this pass' own full-regression run
    (`test_chat_service_turn_context_lifecycle.py::test_cleanup_after_
    asyncio_cancelled_error_raised_mid_run`, a real fake-Runner simulating
    a mid-stream `asyncio.CancelledError` -- NOT external task
    cancellation, just a raised exception instance): `asyncio.
    CancelledError` is a `BaseException`, not an `Exception`. An earlier
    revision of `_drain_agen`'s `except Exception:` clause did not catch
    it, so it propagated straight out of the persistent drain task
    WITHOUT ever reaching either `adk_queue.put()` call -- leaving the
    consumer's `adk_queue.get()` awaiting forever. This test reproduces
    that exact shape directly and must complete (via `asyncio.wait_for`,
    bounding the test itself rather than the production code -- a hang
    here must fail loudly as a test failure, never hang the whole suite).
    """

    async def cancelling_agen() -> AsyncIterator[Any]:
        yield "before-cancel"
        raise asyncio.CancelledError()

    channel: "asyncio.Queue[Any]" = asyncio.Queue()

    async def _drive() -> list[Any]:
        collected: list[Any] = []
        with pytest.raises(asyncio.CancelledError):
            async for item in _merge_adk_and_activity_events(cancelling_agen(), channel):
                collected.append(item.item)
        return collected

    collected = await asyncio.wait_for(_drive(), timeout=5.0)
    assert collected == ["before-cancel"]


@pytest.mark.asyncio
async def test_trailing_activity_items_are_drained_on_normal_completion() -> None:
    """An activity item reported with no `await` between the Runner's own
    final internal action and its last yield must not be silently lost --
    unchanged non-regression from before this correction.
    """

    async def agen() -> AsyncIterator[Any]:
        yield "final"

    channel: "asyncio.Queue[Any]" = asyncio.Queue()
    channel.put_nowait("late-activity")

    merged = [item async for item in _merge_adk_and_activity_events(agen(), channel)]

    sources = [(m.source, m.item) for m in merged]
    assert ("adk", "final") in sources
    assert ("activity", "late-activity") in sources


@pytest.mark.asyncio
async def test_early_abandonment_still_cleans_up_the_persistent_drain_task() -> None:
    """No Runner/resource leak is introduced: if the caller stops
    consuming `_merge_adk_and_activity_events` early (`.aclose()`), the
    persistent drain task must be cancelled and `agen`'s own cleanup
    (its `finally`) must still run -- proving cancellation correctly
    propagates through the single, consistent driving Task all the way
    down into the underlying ADK-shaped generator.
    """
    cleanup_ran = asyncio.Event()

    async def slow_agen() -> AsyncIterator[Any]:
        try:
            yield "first"
            await asyncio.sleep(3600)
        finally:
            cleanup_ran.set()

    channel: "asyncio.Queue[Any]" = asyncio.Queue()
    merge_gen = _merge_adk_and_activity_events(slow_agen(), channel)

    first = await merge_gen.__anext__()
    assert first.item == "first"

    await merge_gen.aclose()

    assert cleanup_ran.is_set()
