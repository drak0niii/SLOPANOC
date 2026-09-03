"""In-process, never-persisted "mailbox" for passing the current turn's
ALREADY-RETRIEVED Teams message text from `teams_get_messages` (running
deep inside `incident_manager`'s nested `AgentTool` call) back out to
`chat_service.py` (the orchestrating code that builds the Source drawer's
snippet) -- snippet-authenticity fix.

WHY THIS EXISTS, VERIFIED AGAINST THE INSTALLED ADK (1.33.0) SOURCE before
implementing (per this codebase's own standing practice): the two more
"obvious" ADK-native channels were both investigated and found NOT to
reliably deliver message text here, for two DIFFERENT reasons each proven
from source, not assumed:

  1. A `temp:`-prefixed session-state key (`sessions/base_session_service
     .py`'s `State.TEMP_PREFIX`/`_apply_temp_state`/`_trim_temp_delta_
     state`) IS correctly forwarded from `incident_manager`'s nested
     session up into team_manager's own `tool_context.actions.state_delta`
     (`AgentTool.run_async`'s "forward state delta to parent session"
     step) -- but `Runner._exec_with_plugin` calls
     `session_service.append_event(...)` (which TRIMS every `temp:` key
     from `event.actions.state_delta`, IN PLACE) BEFORE yielding that
     event to the caller. `chat_service.py`'s own `async for event in
     agen:` loop therefore never sees the temp value on the event
     itself.
  2. Reading it back via a POST-loop `session.state` lookup instead (the
     same technique already used for `SELECTED_TEAMS_CHAT_ID_STATE_KEY`)
     also fails for the `database` session backend (this backend's own
     production default -- `config/settings.py`'s
     `_DEFAULT_SESSION_BACKEND`): `DatabaseSessionService.get_session()`
     re-reads `storage_session.state` directly from the DB row, and a
     `temp:` key -- by design -- was never written there in the first
     place. Using a NON-temp key instead would fix that, at the cost of
     persisting raw Teams message bodies into the session store forever
     (exactly what `TeamsEvidence`'s own docstring, and this backend's
     posture throughout, has consistently avoided) -- rejected as worse
     than the mailbox below, not merely inconvenient.

Given the explicit constraints this fix operates under -- "do not make
another Teams network call merely to generate the snippet" (rules out
re-fetching independently) and "preferably not from model-reproduced
text" (rules out trusting `incident_manager`'s own output) -- this small,
explicit, in-process channel is the remaining option: it never touches
session persistence at all (so it works identically regardless of session
backend), and it is cleared immediately after each turn, so nothing
lingers beyond the turn that produced it (see `pop_message_texts`).

LIFECYCLE CONTRACT (production-safety audit pass -- verified, not merely
documented; see test_chat_service_turn_context_lifecycle.py): the ONE
caller, `chat_service.py`'s `_run_turn_events`, MUST follow this shape --

    run_id_token = bind_run_id(run_id)
    try:
        ... drive the Runner, translate events ...
    except Exception:
        ... convert to a safe error ...
    finally:
        message_texts_by_id = pop_message_texts(run_id)   # <- BOTH calls
        reset_run_id(run_id_token)                        #    live HERE

Both cleanup calls MUST live in the SAME `finally` block that wraps the
Runner-driving code, never as separate statements written after a
try/except with no enclosing `finally` of their own. `except Exception:`
does not catch `asyncio.CancelledError` (a `BaseException` since Python
3.8) -- exactly what a real server-side Stop (`ChatService.cancel_run`)
or a superseded/rejected run delivers -- so any cleanup NOT inside the
`finally` itself would be skipped entirely on that path, silently
retaining that run's mailbox entry forever (this was a real, since-fixed
bug -- see chat_service.py's own comment at that `finally` block). A
`finally` block runs on every exit path from its `try` -- normal
completion, a caught `Exception`, or an uncaught `BaseException`
(including `CancelledError`) as it propagates through -- which is what
makes this the correct, and only, guaranteed-cleanup shape.

CORRELATION, NOT SESSION STATE: `incident_manager`'s own nested run uses a
BRAND NEW `InMemorySessionService()` and session id on every call
(`AgentTool.run_async`, verified) -- there is no session/invocation id
shared between the two layers to key this store by. `_CURRENT_RUN_ID`
(a `contextvars.ContextVar`) carries this turn's own `run_id`
(`EventSequencer.run_id`, already a fresh, unique-per-turn uuid4) DOWN
through the SAME async call chain team_manager's own turn already runs on
-- verified this propagates correctly even through ADK's optional
tool-thread-pool path (`flows/llm_flows/functions.py`'s
`_call_tool_in_thread_pool` explicitly uses `contextvars.copy_context()`/
`ctx.run(...)`, which preserves it); in this backend's own configuration
(`RunConfig` never sets `tool_thread_pool_config`), sync tools run
directly on the same event-loop thread, so propagation is trivial anyway.
"""
from __future__ import annotations

import contextvars
import threading
from typing import Optional

_CURRENT_RUN_ID: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "teams_message_text_run_id", default=None
)

_lock = threading.Lock()
_store: dict[str, dict[str, str]] = {}


def current_run_id() -> Optional[str]:
    """Read by `teams_get_messages` to find out which turn (if any) it is
    running as part of -- `None` outside of a `chat_service.py`-driven
    turn (e.g. a standalone `adk run`/test invocation), in which case
    `record_message_texts` below is simply a safe no-op.
    """
    return _CURRENT_RUN_ID.get()


def bind_run_id(run_id: str) -> "contextvars.Token":
    """Called by `chat_service.py` immediately before starting the
    Runner for this turn. Returns a token for `reset_run_id` -- callers
    MUST reset in a `finally` so a later, unrelated turn on the same
    thread/task pool never inherits a stale `run_id`.
    """
    return _CURRENT_RUN_ID.set(run_id)


def reset_run_id(token: "contextvars.Token") -> None:
    _CURRENT_RUN_ID.reset(token)


def record_message_texts(run_id: Optional[str], texts: dict[str, str]) -> None:
    """Called by `teams_get_messages` with this call's own already-
    filtered, retrieved messages. A no-op for a missing `run_id` or an
    empty `texts` -- never raises (a tool call must never fail because of
    this side channel).
    """
    if not run_id or not texts:
        return
    with _lock:
        existing = _store.setdefault(run_id, {})
        existing.update(texts)


def pop_message_texts(run_id: str) -> dict[str, str]:
    """Called exactly once by `chat_service.py`, from WITHIN the same
    `finally` block that resets the ContextVar (see this module's own
    "LIFECYCLE CONTRACT" docstring) -- that placement, not merely calling
    this function somewhere, is what guarantees it runs on EVERY exit path
    (success, a caught `Exception`, or an uncaught `BaseException` like
    `asyncio.CancelledError`). Removes and returns whatever was recorded,
    so nothing for this `run_id` lingers in process memory beyond the turn
    that produced it. Idempotent -- a second call for the same `run_id`
    (which should never legitimately happen, but is safe if it does)
    simply returns `{}`. Returns `{}` if nothing was ever recorded (a
    non-Teams turn, or a Teams turn whose messages all failed to
    retrieve).
    """
    with _lock:
        return _store.pop(run_id, {})
