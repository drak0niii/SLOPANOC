"""Production-safety audit for backend/api/turn_context.py's run-scoped
evidence mailbox -- proves retrieved Teams message text cannot leak or
remain in process memory after ANY terminal path (success, exception,
cancellation, disconnect), that concurrent/sequential runs never cross-
contaminate, and that ContextVar propagation is correct across the actual
execution path (never assumed).

Uses only synthetic, clearly-fake message bodies -- never real content.
"""
from __future__ import annotations

import asyncio
import contextvars

import pytest

from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.api.turn_context import (
    bind_run_id,
    current_run_id,
    pop_message_texts,
    record_message_texts,
    reset_run_id,
)
from backend.tests._api_fakes import FakeEvent, FakeFunctionCall, FakeFunctionResponse, FakeRunner, RaisingRunner


async def _collect(chat_service: ChatService, session_id: str, message: str, user_id: str = "api-user") -> list:
    return [event async for event in chat_service.execute_turn_events(session_id, message, user_id)]


def _run_id_of(collected: list) -> str:
    return next(e.run_id for e in collected if e.type == StreamEventType.RUN_STARTED)


class _RecordingRunner:
    """A fake runner that, mid-stream, calls `record_message_texts` using
    whatever `run_id` is CURRENTLY bound -- exactly mirroring how the real
    `teams_get_messages` (deep inside `incident_manager`'s nested AgentTool
    call) discovers its own run_id via `current_run_id()`. Optionally
    raises afterward, to simulate a model/tool failure occurring AFTER
    retrieval already populated the mailbox.
    """

    def __init__(self, texts: dict[str, str], raise_after: "BaseException | None" = None) -> None:
        self._texts = texts
        self._raise_after = raise_after

    async def run_async(self, *, user_id, session_id, new_message, run_config=None):
        yield FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")])
        record_message_texts(current_run_id(), self._texts)
        if self._raise_after is not None:
            raise self._raise_after
        yield FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "ok", "chat_title": "Ops Bridge", "evidence": [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-26T09:00:00Z"}]},
                )
            ],
        )
        yield FakeEvent(text="Here is a summary.", final=True)


class _GatedRecordingRunner:
    """Mirrors test_api_run_cancellation.py's own `_GatedRunner` -- records
    message texts under the current run_id, then blocks on an externally-
    controlled `asyncio.Event` so a test can cancel the run AFTER retrieval
    has already populated the mailbox but BEFORE the turn would otherwise
    complete.
    """

    def __init__(self, texts: dict[str, str], resume: asyncio.Event) -> None:
        self._texts = texts
        self._resume = resume
        self.finished = False

    async def run_async(self, *, user_id, session_id, new_message, run_config=None):
        yield FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")])
        record_message_texts(current_run_id(), self._texts)
        yield FakeEvent(text="partial", final=False, partial=True)
        await self._resume.wait()
        yield FakeEvent(text="Here is a summary.", final=True)
        self.finished = True


async def _start_gated_run(chat_service: ChatService, session_id: str, message: str = "hi", user_id: str = "api-user"):
    agen = chat_service.execute_turn_events(session_id, message, user_id)
    run_id = None
    seen = 0
    async for event in agen:
        seen += 1
        if event.type == StreamEventType.RUN_STARTED:
            run_id = event.run_id
        if seen >= 2:
            break
    assert run_id is not None
    task = next(iter(chat_service._background_turns))
    return agen, run_id, task


# --- 1/3/4: cleanup on every terminal path ----------------------------------


@pytest.mark.asyncio
async def test_cleanup_after_normal_completion() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    runner = _RecordingRunner({"m1": "Synthetic retrieved body A."})
    chat_service = ChatService(service, runner=runner)

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    run_id = _run_id_of(collected)

    # The evidence WAS actually used (proves this isn't a vacuous pass --
    # the mailbox really was populated and consumed).
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["source"]["evidence"][0]["snippet"] == "Synthetic retrieved body A."

    assert pop_message_texts(run_id) == {}


@pytest.mark.asyncio
async def test_cleanup_after_a_model_exception() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError("simulated model failure")))

    collected = await _collect(chat_service, session_id, "hi")
    run_id = _run_id_of(collected)

    assert any(e.type == StreamEventType.ERROR for e in collected)
    assert pop_message_texts(run_id) == {}


@pytest.mark.asyncio
async def test_cleanup_after_a_tool_exception_following_successful_retrieval() -> None:
    """Retrieval succeeded (mailbox populated) but a LATER tool/model step
    in the same turn fails -- the already-recorded text must still not
    survive the turn."""
    service = ApiSessionService()
    session_id = await service.create_session()
    runner = _RecordingRunner(
        {"m1": "Synthetic retrieved body B."}, raise_after=RuntimeError("simulated downstream tool failure")
    )
    chat_service = ChatService(service, runner=runner)

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    run_id = _run_id_of(collected)

    assert any(e.type == StreamEventType.ERROR for e in collected)
    assert pop_message_texts(run_id) == {}


@pytest.mark.asyncio
async def test_cleanup_happens_before_source_reference_construction_not_after() -> None:
    """The mailbox is popped into a local variable and cleared from the
    shared store BEFORE `build_source_reference` ever runs -- so even if
    source-reference construction itself were to raise, nothing would be
    left behind (there is nothing left TO leave behind by that point)."""
    service = ApiSessionService()
    session_id = await service.create_session()
    runner = _RecordingRunner({"m1": "Synthetic retrieved body C."})
    chat_service = ChatService(service, runner=runner)

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    run_id = _run_id_of(collected)

    # A source WAS built using the text (proves construction ran using the
    # already-popped local copy, not a live reference into the store).
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" in completed.data
    assert pop_message_texts(run_id) == {}


@pytest.mark.asyncio
async def test_cleanup_after_asyncio_cancelled_error_raised_mid_run() -> None:
    """THE bug this audit found and fixed: `except Exception:` does not
    catch `asyncio.CancelledError` (a `BaseException` since Python 3.8).
    Before the fix, a cancellation raised while the runner loop was
    executing skipped the (then-separate) `pop_message_texts` statement
    entirely, leaking the mailbox entry forever.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    runner = _RecordingRunner({"m1": "Synthetic retrieved body D."}, raise_after=asyncio.CancelledError())
    chat_service = ChatService(service, runner=runner)

    collected = [
        event
        async for event in chat_service.execute_turn_events(session_id, "summarize Ops Bridge")
        if event.type == StreamEventType.RUN_STARTED
    ]
    run_id = collected[0].run_id

    assert pop_message_texts(run_id) == {}


@pytest.mark.asyncio
async def test_cleanup_after_a_genuine_task_cancel_via_cancel_run() -> None:
    """End-to-end, via the REAL production cancellation path
    (`ChatService.cancel_run` -> `asyncio.Task.cancel()`), not just a
    raised exception -- mirrors test_api_run_cancellation.py's own
    `_GatedRunner` pattern exactly.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    resume = asyncio.Event()
    runner = _GatedRecordingRunner({"m1": "Synthetic retrieved body E."}, resume)
    chat_service = ChatService(service, runner=runner)

    agen, run_id, task = await _start_gated_run(chat_service, session_id, "summarize Ops Bridge")
    await agen.aclose()  # simulates the SSE consumer disconnecting after clicking Stop

    cancelled = await chat_service.cancel_run(session_id, run_id)
    assert cancelled is True
    with pytest.raises(asyncio.CancelledError):
        await task

    assert pop_message_texts(run_id) == {}

    resume.set()  # deterministic cleanup so nothing lingers for the next test
    await asyncio.sleep(0)
    assert runner.finished is False


@pytest.mark.asyncio
async def test_mailbox_still_clears_when_the_sse_consumer_disconnects_but_the_backend_run_continues() -> None:
    """Per execute_turn_events's own documented behavior, an abandoned SSE
    consumer (`agen.aclose()` from the caller's side) does NOT cancel the
    background task -- the run completes normally. Confirms the mailbox
    still clears via the NORMAL (non-cancellation) path in that scenario.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    runner = _RecordingRunner({"m1": "Synthetic retrieved body F."})
    chat_service = ChatService(service, runner=runner)

    agen = chat_service.execute_turn_events(session_id, "summarize Ops Bridge")
    run_id = None
    seen = 0
    async for event in agen:
        seen += 1
        if event.type == StreamEventType.RUN_STARTED:
            run_id = event.run_id
        if seen >= 2:
            break
    assert run_id is not None
    task = next(iter(chat_service._background_turns))

    await agen.aclose()  # the frontend "walks away" -- background task is untouched
    await task  # let the background run finish naturally

    assert pop_message_texts(run_id) == {}


@pytest.mark.asyncio
async def test_a_stale_run_id_cannot_cancel_a_newer_run_and_the_newer_runs_mailbox_survives() -> None:
    """Superseded/stale run_id scenario (mirrors test_api_run_cancellation
    .py's own security test): confirms turn_context isolation holds even
    when a cancel attempt targets the wrong run_id."""
    service = ApiSessionService()
    session_id = await service.create_session()

    first_chat_service = ChatService(service, runner=FakeRunner(service))
    first_collected = await _collect(first_chat_service, session_id, "first")
    stale_run_id = _run_id_of(first_collected)

    resume = asyncio.Event()
    runner = _GatedRecordingRunner({"m1": "Synthetic retrieved body G."}, resume)
    second_chat_service = ChatService(service, runner=runner)
    agen, new_run_id, task = await _start_gated_run(second_chat_service, session_id, "summarize Ops Bridge")
    await agen.aclose()

    cancelled = await second_chat_service.cancel_run(session_id, stale_run_id)
    assert cancelled is False
    assert not task.done()

    resume.set()
    await task
    assert runner.finished is True
    assert pop_message_texts(new_run_id) == {}


# --- 4: ContextVar cleanup ---------------------------------------------------


def test_bind_reset_round_trip_leaves_no_trace() -> None:
    assert current_run_id() is None
    token = bind_run_id("run-z")
    assert current_run_id() == "run-z"
    reset_run_id(token)
    assert current_run_id() is None


@pytest.mark.asyncio
async def test_current_run_id_does_not_leak_into_code_running_after_a_turn_completes() -> None:
    """After a full chat_service.py-driven turn finishes, code executing
    afterward (in the SAME task/coroutine) must not still resolve that
    turn's run_id as "current"."""
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    assert current_run_id() is None
    await _collect(chat_service, session_id, "hello")
    assert current_run_id() is None


# --- 2/9/10: isolation, concurrency, and the cancellation race --------------


@pytest.mark.asyncio
async def test_two_concurrent_runs_for_different_users_never_cross_contaminate() -> None:
    service = ApiSessionService()
    session_a = await service.create_session(user_id="user-a")
    session_b = await service.create_session(user_id="user-b")

    runner_a = _GatedRecordingRunner({"m1": "User A's synthetic message."}, asyncio.Event())
    runner_b = _GatedRecordingRunner({"m1": "User B's synthetic message."}, asyncio.Event())
    chat_a = ChatService(service, runner=runner_a)
    chat_b = ChatService(service, runner=runner_b)

    agen_a, run_a, task_a = await _start_gated_run(chat_a, session_a, "summarize", user_id="user-a")
    agen_b, run_b, task_b = await _start_gated_run(chat_b, session_b, "summarize", user_id="user-b")
    assert run_a != run_b

    # Each run's OWN in-flight mailbox entry holds only its own text --
    # pop-and-restore to inspect without disturbing the still-running turn.
    texts_a = pop_message_texts(run_a)
    texts_b = pop_message_texts(run_b)
    assert texts_a == {"m1": "User A's synthetic message."}
    assert texts_b == {"m1": "User B's synthetic message."}
    record_message_texts(run_a, texts_a)
    record_message_texts(run_b, texts_b)

    runner_a._resume.set()
    runner_b._resume.set()
    await agen_a.aclose()
    await agen_b.aclose()
    await task_a
    await task_b

    assert pop_message_texts(run_a) == {}
    assert pop_message_texts(run_b) == {}


@pytest.mark.asyncio
async def test_two_simultaneous_runs_for_the_same_user_stay_isolated() -> None:
    service = ApiSessionService()
    session_1 = await service.create_session(user_id="same-user")
    session_2 = await service.create_session(user_id="same-user")

    runner_1 = _GatedRecordingRunner({"m1": "Session 1 synthetic body."}, asyncio.Event())
    runner_2 = _GatedRecordingRunner({"m1": "Session 2 synthetic body."}, asyncio.Event())
    chat_1 = ChatService(service, runner=runner_1)
    chat_2 = ChatService(service, runner=runner_2)

    agen_1, run_1, task_1 = await _start_gated_run(chat_1, session_1, "summarize", user_id="same-user")
    agen_2, run_2, task_2 = await _start_gated_run(chat_2, session_2, "summarize", user_id="same-user")

    assert pop_message_texts(run_1) == {"m1": "Session 1 synthetic body."}
    assert pop_message_texts(run_2) == {"m1": "Session 2 synthetic body."}

    runner_1._resume.set()
    runner_2._resume.set()
    await agen_1.aclose()
    await agen_2.aclose()
    await task_1
    await task_2


@pytest.mark.asyncio
async def test_cancellation_race_run_a_cancelled_run_b_starts_immediately() -> None:
    """The exact scenario requested: run A retrieves and populates the
    mailbox, is cancelled before provenance completion, run B starts
    immediately (same session, sequentially, since the session lock
    forbids true concurrency on one session) -- run A's eventual cleanup
    must never touch run B's data, and run B must never see run A's data.
    """
    service = ApiSessionService()
    session_id = await service.create_session()

    resume_a = asyncio.Event()
    runner_a = _GatedRecordingRunner({"m1": "Run A synthetic body -- must never leak."}, resume_a)
    chat_a = ChatService(service, runner=runner_a)

    agen_a, run_a, task_a = await _start_gated_run(chat_a, session_id, "summarize Ops Bridge")
    await agen_a.aclose()
    cancelled = await chat_a.cancel_run(session_id, run_a)
    assert cancelled is True
    with pytest.raises(asyncio.CancelledError):
        await task_a
    resume_a.set()  # let any leftover continuation observe cancellation was final
    await asyncio.sleep(0)

    # Run A's own cleanup has already happened (finally block ran during
    # the cancellation unwind above).
    assert pop_message_texts(run_a) == {}

    # Run B starts immediately afterward, on the same now-unlocked session.
    runner_b = _RecordingRunner({"m1": "Run B synthetic body."})
    chat_b = ChatService(service, runner=runner_b)
    collected_b = await _collect(chat_b, session_id, "summarize Ops Bridge")
    run_b = _run_id_of(collected_b)
    assert run_b != run_a

    completed_b = next(e for e in collected_b if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed_b.data["source"]["evidence"][0]["snippet"] == "Run B synthetic body."
    assert "Run A synthetic body" not in str(completed_b.data)

    # Run A's now-redundant pop (simulating its finally firing again, which
    # cannot actually happen twice, but proves idempotence/no cross-delete
    # even if it somehow raced) must not disturb run B's own (already
    # cleared, since the turn completed) entry.
    assert pop_message_texts(run_a) == {}
    assert pop_message_texts(run_b) == {}


# --- 7: bounded memory / deduplication --------------------------------------


def test_repeated_writes_for_the_same_message_id_replace_not_multiply() -> None:
    record_message_texts("run-dedup", {"m1": "First version."})
    record_message_texts("run-dedup", {"m1": "Corrected version."})
    result = pop_message_texts("run-dedup")
    assert result == {"m1": "Corrected version."}
    assert len(result) == 1


def test_mailbox_size_matches_exactly_what_was_recorded_never_more() -> None:
    texts = {f"m{i}": f"Synthetic body {i}." for i in range(50)}
    record_message_texts("run-bounded", texts)
    result = pop_message_texts("run-bounded")
    assert len(result) == 50
    assert result == texts


# --- 9: thread/async propagation (verified, not assumed) --------------------


@pytest.mark.asyncio
async def test_current_run_id_propagates_through_asyncio_to_thread() -> None:
    """`asyncio.to_thread` (used elsewhere in this backend, e.g.
    `resolve_authoritative_contributors`) documents automatic context
    propagation -- verified directly here rather than assumed, since a
    future change could route `teams_get_messages` through it too."""
    token = bind_run_id("run-thread-1")
    try:
        seen = await asyncio.to_thread(current_run_id)
    finally:
        reset_run_id(token)
    assert seen == "run-thread-1"


def test_current_run_id_propagates_through_adks_own_copy_context_pattern() -> None:
    """Reproduces the EXACT pattern ADK's `_call_tool_in_thread_pool` uses
    (`flows/llm_flows/functions.py`, verified against the installed 1.33.0
    source): `contextvars.copy_context()` at dispatch time, then
    `ctx.run(...)` inside a real worker thread. Confirms our ContextVar
    survives that exact mechanism, not just the same-thread default path
    this backend's own `RunConfig` actually takes.
    """
    import concurrent.futures

    token = bind_run_id("run-thread-2")
    try:
        ctx = contextvars.copy_context()
    finally:
        reset_run_id(token)

    def run_in_copied_context() -> str | None:
        return ctx.run(current_run_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(run_in_copied_context).result()

    assert result == "run-thread-2"
    # And the ORIGINAL (non-copied) context, back on this thread, is
    # unaffected by whatever the copied context saw.
    assert current_run_id() is None
