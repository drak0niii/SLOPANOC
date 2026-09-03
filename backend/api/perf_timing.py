"""Developer-side performance instrumentation (pre-4H latency
investigation pass, extended for the dedicated latency-diagnosis pass).

DEVELOPER DIAGNOSTICS ONLY -- never sent to the frontend, never added to
the user-facing `RunTrace` (`run_trace.py`, unchanged by this module),
never part of any SSE event payload. This is a server-log-only side
channel: `PerfTimer`/`DelegationTimer`/the model-call instrumentation
below only ever call into `logging`: they cannot affect what streams to
the client, so adding/removing timing calls can never alter the SSE
contract (see test_chat_service_streaming.py's own instrumentation tests
for a direct proof of this).

WHAT IS LOGGED, AND WHY IT IS SAFE: only `run_id` (already a
non-secret, per-run identifier that already reaches the frontend in every
SSE event's own envelope -- see streaming_events.py), a fixed `stage`
name from a small, closed set of boundaries this module defines, a
`duration_ms`/`elapsed_ms` float, and (for the model-call instrumentation
below) an `agent` name from the closed set already known statically
("team_manager"/"incident_manager"), a small integer `index`/`count`, a
`kind` from a closed classification set ("text"/"function_call"/
"text_and_function_call"/"empty"/"other"), and token COUNTS (integers
only). NEVER a message body, a prompt, a model response, a chat id, a
Teams identifier, a raw token/token string, or a raw tool payload --
there is no code path here that could log one, because this module never
receives that data in the first place (its callers, and the ADK model-
callback parameters it reads, are only ever asked for counts/classification,
never content).

MONOTONIC CLOCK: `time.monotonic()` (never `time.time()`/wall-clock),
per the instruction -- immune to system clock adjustments, the only
correct choice for measuring elapsed duration.

MODEL-CALL INSTRUMENTATION (this pass), VERIFIED AGAINST THE INSTALLED
ADK 1.33.0 SOURCE before implementing: `google.adk.agents.llm_agent
.LlmAgent.before_model_callback`/`after_model_callback` are ADK's own
documented extension points, invoked once per actual model request
(`Callable[[Context, LlmRequest], ...]`/`Callable[[Context,
LlmResponse], ...]`) -- exactly the "actual model invocation boundary"
instrumentation this pass calls for, not a hand-rolled substitute.
`before_model_call(agent_name)`/`after_model_call(agent_name)` below
build the paired callback FUNCTIONS `team_manager`/`incident_manager`
register (see their own `agent.py` modules) -- both are plain, stateless-
per-call functions (never a bound object attached to the shared,
cross-request `Agent` singleton) that correlate to the CURRENT turn via
`backend.api.turn_context.current_run_id()` -- the SAME already-
established, already-verified ContextVar this codebase already uses for
exactly this "get the current run_id into a deeply-nested ADK callback
that doesn't receive it as a parameter" problem (see turn_context.py's
own module docstring, including its own verification that this
propagates correctly even through ADK's `tool_thread_pool_config` worker-
thread path via `contextvars.copy_context()`). Both callbacks always
return `None` -- never substituting a fake model response, purely a
timing/classification side effect, mirroring `sync_incident_manager_
result_to_state`'s own "never returns a value" tool-callback discipline.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

_logger = logging.getLogger("backend.perf")

Clock = Callable[[], float]


class PerfTimer:
    """Per-run timing helper -- one instance per `_run_turn_events` call
    (chat_service.py), keyed by that turn's own `run_id`. Every `mark()`
    logs the time since the PREVIOUS mark (this stage's own cost) and the
    time since this timer was constructed (cumulative elapsed) -- both
    are useful: the former isolates which single stage is slow, the
    latter shows the running total a developer can compare against a
    live-observed end-to-end time.

    `clock` is injectable (defaults to `time.monotonic`) purely so tests
    can use a deterministic fake clock instead of asserting against real
    wall-clock timing (never "brittle wall-clock assertions against
    external services" -- see this module's own tests).
    """

    def __init__(self, run_id: str, clock: Clock = time.monotonic) -> None:
        self.run_id = run_id
        self._clock = clock
        self._start = clock()
        self._last = self._start

    def mark(self, stage: str) -> float:
        """Records and logs a point-in-time boundary; returns this
        stage's own duration in milliseconds (time since the previous
        mark, or since construction for the first mark).
        """
        now = self._clock()
        duration_ms = (now - self._last) * 1000.0
        elapsed_ms = (now - self._start) * 1000.0
        self._last = now
        _logger.info(
            "perf stage=%s run_id=%s duration_ms=%.1f elapsed_ms=%.1f",
            stage,
            self.run_id,
            duration_ms,
            elapsed_ms,
        )
        return duration_ms

    def log_duration(self, stage: str, duration_seconds: float) -> None:
        """For a duration measured independently of the sequential
        `mark()` timeline (e.g. `DelegationTimer`'s own start/end pair,
        or a Teams tool call timed at its own call site) -- logged in the
        exact same safe, structured shape as `mark()`, without disturbing
        this timer's own `_last` sequencing.
        """
        _logger.info(
            "perf stage=%s run_id=%s duration_ms=%.1f",
            stage,
            self.run_id,
            duration_seconds * 1000.0,
        )

    def elapsed_seconds(self) -> float:
        return self._clock() - self._start


class DelegationTimer:
    """Tracks how much of a team_manager turn was spent waiting on the
    `incident_manager` AgentTool call(s) -- independently re-observes the
    same ADK event stream `StatusTranslator`/`RunTraceTranslator`/
    `TeamsSourceCapture` already do (see source_reference.py's own module
    docstring for this established "each helper inspects what it needs"
    pattern), rather than threading a new dependency through any of them.

    Measures from the event carrying `incident_manager`'s function CALL to
    the event carrying its matching function RESPONSE -- this brackets
    the entire nested Runner/session `AgentTool.run_async` creates for
    that call (verified against the installed ADK source; see
    chat_service.py's own module docstring), i.e. genuinely "how long did
    the Teams specialist delegation take," not merely a single tool
    invocation inside it.

    If `incident_manager` is called more than once in one turn (e.g. a
    chat-selection follow-up), `total_seconds`/`call_count` accumulate
    across all of them -- this is a total-time-spent figure, distinct
    from `TeamsSourceCapture`'s own "last successful result wins"
    semantics (which answers a different question: what grounds the
    final answer, not how long delegation took in aggregate).
    """

    _TOOL_NAME = "incident_manager"

    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._pending_start: Optional[float] = None
        self.total_seconds: float = 0.0
        self.call_count: int = 0

    def observe(self, event: Any) -> None:
        if getattr(event, "partial", False):
            return
        if self._pending_start is None:
            for call in event.get_function_calls():
                if getattr(call, "name", None) == self._TOOL_NAME:
                    self._pending_start = self._clock()
                    break
            return
        for response in event.get_function_responses():
            if getattr(response, "name", None) == self._TOOL_NAME:
                self.total_seconds += self._clock() - self._pending_start
                self.call_count += 1
                self._pending_start = None
                break


# --- Model-call instrumentation (latency-diagnosis pass, corrected for --
# --- streaming in the P2 diagnostics-correctness pass) ----------------------
#
# Answers "how many Gemini calls does this turn actually require, and how
# long does each take" -- section 3's central question. Keyed by `run_id`
# (via `current_run_id()`), never by anything agent-instance-local, since
# `team_manager`/`incident_manager` are shared, cross-request singletons.
#
# P2 ROOT CAUSE, VERIFIED AGAINST THE INSTALLED ADK 1.33.0 SOURCE (`flows/
# llm_flows/base_llm_flow.py`) before implementing -- do not guess:
#
#   - `before_model_callback` fires EXACTLY ONCE per real model
#     invocation: `_handle_before_model_callback` is called once, at the
#     very start of `_call_llm_with_tracing` (the async generator that
#     represents ONE logical model request), strictly BEFORE the actual
#     `generate_content`/streaming call is ever made. No change needed
#     here beyond what pass #1 already fixed (the keyword-argument
#     regression).
#   - `after_model_callback` fires ONCE PER STREAMED `LlmResponse`
#     CHUNK, not once per request: `async for llm_response in agen:
#     ... self._handle_after_model_callback(invocation_context,
#     llm_response, model_response_event)` -- this loop iterates every
#     `LlmResponse` the model's own streaming generator yields (for
#     `RunConfig(streaming_mode=StreamingMode.SSE)`, this backend's own
#     team_manager configuration -- see chat_service.py's module
#     docstring on ADK streaming: many `partial=True` chunks followed by
#     exactly one aggregated, non-partial final one). The PREVIOUS
#     implementation treated every one of these as a completed
#     invocation: the FIRST callback correctly popped the pending record
#     and logged a valid `model_call_end`; every SUBSEQUENT callback for
#     the SAME real invocation then found nothing pending and fabricated
#     a synthetic `index=-1, duration_ms=-1.0` record -- exactly the live
#     bug (`model_call_end index=4` immediately followed by repeated
#     `model_call_end index=-1`).
#   - `LlmResponse.partial` (verified present on `LlmResponse.model_
#     fields`, and explicitly documented as the same "intermediate
#     streaming chunk vs. complete, aggregated response" signal
#     `Event.partial` already carries -- `Event`s are built FROM
#     `LlmResponse`s via `_finalize_model_response_event`) is the
#     reliable, ADK-native terminal marker used below: a chunk with
#     `partial` truthy is an intermediate fragment (never logged, never
#     pops the pending record); the first chunk with `partial` falsy is
#     the real completion of that invocation (logged exactly once, then
#     the pending record is closed) -- never inferred from timing,
#     content, or response text.

_registry_lock = threading.Lock()
_model_call_index_by_run: dict[str, int] = {}


class _PendingModelCall:
    """One real, in-flight model invocation's accumulating record --
    created by `before_model_call`, updated (never re-created) by every
    `after_model_call` callback for the SAME invocation (partial or
    terminal), and consumed exactly once when the terminal chunk arrives.
    `prompt_tokens`/`output_tokens`/`kind` are kept as "best known so
    far" across ALL chunks (section 8: a terminal chunk that itself lacks
    usage metadata still gets whatever an earlier chunk already reported).
    """

    __slots__ = ("agent", "index", "started_at", "prompt_tokens", "output_tokens", "kind")

    def __init__(self, agent: str, index: int, started_at: float) -> None:
        self.agent = agent
        self.index = index
        self.started_at = started_at
        self.prompt_tokens: Optional[int] = None
        self.output_tokens: Optional[int] = None
        self.kind: Optional[str] = None


_pending_model_call_by_run: dict[str, _PendingModelCall] = {}


def _next_model_call_index(run_id: str) -> int:
    with _registry_lock:
        index = _model_call_index_by_run.get(run_id, 0) + 1
        _model_call_index_by_run[run_id] = index
        return index


def discard_model_call_tracking(run_id: str) -> None:
    """Called from `chat_service.py`'s own turn-lifecycle `finally` block
    (mirroring `read_continuation_enforcement.discard_active_read_
    continuation`'s own pattern) -- guarantees no per-run bookkeeping
    entry here survives past the ONE turn it was created for, on every
    exit path including cancellation. Safe to call even when nothing was
    ever recorded for `run_id` (e.g. a turn that made zero model calls,
    which should not itself be possible, but this must never raise
    either way).
    """
    with _registry_lock:
        _model_call_index_by_run.pop(run_id, None)
        _pending_model_call_by_run.pop(run_id, None)


def _classify_llm_response(llm_response: Any) -> str:
    """Closed classification of a model response's SHAPE only -- never
    its content. Answers "did this call return text, a function/tool
    call, both, or nothing" (section 3's own explicit ask), without ever
    reading the text itself or a function call's arguments.
    """
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return "empty"
    has_text = any(bool(getattr(p, "text", None)) for p in parts)
    has_function_call = any(getattr(p, "function_call", None) is not None for p in parts)
    if has_text and has_function_call:
        return "text_and_function_call"
    if has_function_call:
        return "function_call"
    if has_text:
        return "text"
    return "other"


def before_model_call(agent_name: str, clock: Clock = time.monotonic) -> Callable[[Any, Any], None]:
    """Builds the `before_model_callback` `agent_name` ("team_manager" or
    "incident_manager") registers on its own `Agent(...)` construction.
    `clock` is injectable purely for deterministic tests (mirrors
    `PerfTimer`/`DelegationTimer`'s own pattern).

    Reads ONLY `llm_request.contents`'s own LENGTH (never its content) to
    log a rough "how much history/context is this call sending" signal
    (section 7's own "number of active ADK events" question) -- still
    never a message body, a prompt, or anything content-shaped.

    REGRESSION FIX: ADK invokes every canonical `before_model_callback`
    with KEYWORD arguments ONLY -- `callback(callback_context=...,
    llm_request=...)` (verified directly in the installed ADK 1.33.0
    source, `flows/llm_flows/base_llm_flow.py`'s `_handle_before_model_
    callback`). The parameter must be named `callback_context`, not `ctx`
    -- a positionally-named-but-keyword-called mismatch raised
    `TypeError: ...got an unexpected keyword argument 'callback_context'`
    on every single model call, for every agent, before the model was
    ever reached -- exactly reproducing the reported "hello fails after
    0-1s" regression (caught by chat_service.py's own generic `except
    Exception:` and surfaced as the generic run-failure message).
    """

    def _before_model_callback(callback_context: Any, llm_request: Any) -> None:
        from backend.api.turn_context import current_run_id

        run_id = current_run_id()
        if run_id is None:
            # Outside a chat_service.py-driven turn (e.g. a standalone
            # `adk run`/`adk web` invocation, or a test that never bound
            # a run_id) -- a safe no-op, exactly like turn_context.py's
            # own `record_message_texts` does for the same condition.
            return None
        index = _next_model_call_index(run_id)
        content_count = len(llm_request.contents) if getattr(llm_request, "contents", None) else 0
        with _registry_lock:
            _pending_model_call_by_run[run_id] = _PendingModelCall(agent_name, index, clock())
        _logger.info(
            "perf stage=model_call_start run_id=%s agent=%s index=%d input_content_count=%d",
            run_id,
            agent_name,
            index,
            content_count,
        )
        return None

    return _before_model_callback


def after_model_call(agent_name: str, clock: Clock = time.monotonic) -> Callable[[Any, Any], None]:
    """Builds the paired `after_model_callback` -- see `before_model_call`
    for the keyword-argument contract (unchanged from pass #1's fix).

    P2 STREAMING FIX: ADK invokes this callback once per streamed
    `LlmResponse` chunk, not once per real model request (verified
    against source -- see this module's own "P2 ROOT CAUSE" comment
    above `_registry_lock`). This function is therefore idempotent per
    invocation and terminal-aware:

      - Every call (partial or not) for a run with an ACTIVE pending
        record opportunistically updates that record's best-known
        `prompt_tokens`/`output_tokens`/`kind` -- a later chunk's more
        complete usage metadata is captured even if an earlier one
        lacked it, and vice versa if the terminal chunk itself happens
        to lack it (section 8).
      - A chunk with `llm_response.partial` truthy is an intermediate
        streaming fragment -- updates the record above, but is NEVER
        logged and NEVER pops the pending record.
      - The FIRST chunk with `partial` falsy is the real completion of
        that invocation -- pops the pending record (closing it) and logs
        EXACTLY ONE `model_call_end`, using the record's accumulated
        best-known metadata.
      - Any FURTHER callback for a run with NO active pending record
        (whether a genuine duplicate terminal callback, or any other
        stray/late invocation) is SILENTLY IGNORED for end-event
        logging -- never fabricates a synthetic `index=-1`/`duration_ms=
        -1.0` record (section 9's explicit requirement).
    """

    def _after_model_callback(callback_context: Any, llm_response: Any) -> None:
        from backend.api.turn_context import current_run_id

        run_id = current_run_id()
        if run_id is None:
            return None

        is_partial = bool(getattr(llm_response, "partial", False))
        kind = _classify_llm_response(llm_response)
        usage = getattr(llm_response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", None) if usage is not None else None
        output_tokens = getattr(usage, "candidates_token_count", None) if usage is not None else None

        finalized: Optional[_PendingModelCall] = None
        with _registry_lock:
            pending = _pending_model_call_by_run.get(run_id)
            if pending is None:
                # No active invocation for this run -- already finalized
                # by an earlier terminal chunk, or a callback fired
                # outside a tracked before/after pair. Never fabricate a
                # record here.
                return None

            if prompt_tokens is not None:
                pending.prompt_tokens = prompt_tokens
            if output_tokens is not None:
                pending.output_tokens = output_tokens
            if kind != "empty":
                pending.kind = kind

            if is_partial:
                return None  # intermediate fragment -- not yet a completion

            # Terminal (non-partial) chunk -- the real completion. Pop
            # (close) the record so any further callback for this SAME
            # invocation finds nothing pending and is ignored above.
            finalized = _pending_model_call_by_run.pop(run_id)

        duration_ms = (clock() - finalized.started_at) * 1000.0
        _logger.info(
            "perf stage=model_call_end run_id=%s agent=%s index=%d duration_ms=%.1f kind=%s "
            "prompt_tokens=%s output_tokens=%s",
            run_id,
            finalized.agent,
            finalized.index,
            duration_ms,
            finalized.kind or "empty",
            finalized.prompt_tokens if finalized.prompt_tokens is not None else "unknown",
            finalized.output_tokens if finalized.output_tokens is not None else "unknown",
        )
        return None

    return _after_model_callback
