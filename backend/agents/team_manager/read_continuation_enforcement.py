"""Production-hardening pass: deterministic enforcement of a
`ResolvedReadContinuation` (selection/schemas.py) against team_manager's
own `incident_manager` tool call, for the one turn that resumes a chosen
`SelectionCard` read -- the mechanism that removes model reinterpretation
from that path.

WHY A `before_tool_callback`, VERIFIED AGAINST THE INSTALLED ADK (1.33.0)
SOURCE before implementing (per instruction): `flows/llm_flows/functions
.py`'s tool-dispatch (`_run_with_trace`) calls every
`agent.canonical_before_tool_callbacks` entry with `(tool=tool,
args=function_args, tool_context=tool_context)` BEFORE `__call_tool_async`
ever runs the tool -- and passes that SAME `function_args` dict object
through to it. Mutating `args` in place here therefore changes exactly
what `AgentTool.run_async` sends to `incident_manager`, with no new
Runner, no synthetic session/event construction, and no bypass of
team_manager as the one user-facing agent -- team_manager still decides
TO delegate (an always-safe decision once a continuation exists: at worst,
if its model somehow does not call `incident_manager` at all this turn,
nothing incorrect happens, the continuation is simply not applied and
was already consumed -- see `chat_service.py`), it just never gets to
decide WHAT arguments that delegation carries once a continuation is
active. This is `AgentTool`'s and `functions.py`'s own documented
extension point, not an undocumented internal.

KNOWN LIMITATION, DISCOVERED DURING THE P1 LIVE-INCIDENT INVESTIGATION,
DOCUMENTED HONESTLY RATHER THAN SILENTLY LEFT WRONG: this function still
sets `tool_context.state["temp:resolved_chat_id"] = ...` below, but that
value does NOT actually reach `incident_manager`'s child session in
practice. `AgentTool.run_async` DOES build `state_dict` from
`tool_context.state.to_dict()` (filtered only for `_adk`-prefixed keys --
`temp:` keys ARE present in that dict, as originally verified) and DOES
pass it to `runner.session_service.create_session(..., state=state_dict)`
-- but `create_session`'s OWN handling of its `state=` parameter
(`google.adk.sessions._session_util.extract_state_delta`, verified
directly) SILENTLY DROPS every `temp:`-prefixed key from ALL of its app/
user/session buckets -- unlike `append_event`'s `state_delta` handling
(`_apply_temp_state`), which applies a `temp:` value to the session
in-memory before trimming it from what gets persisted, `create_session`
has no "apply, then trim" step at all: a `temp:`-prefixed key is simply
never stored anywhere it creates. `read_continuation_execution.py`'s own
`_seeded_state` hit this exact bug and now uses a PLAIN key instead (safe
there because that session is always a throwaway, deleted immediately
after use) -- but the SAME fix is not safe here: `tool_context.state[...]`
here is team_manager's OWN, DURABLE session state; a plain (non-`temp:`)
key would persist across turns, risking exactly the "stale value leaks
into a later, unrelated turn" class of bug this codebase has otherwise
been careful to avoid. Given `execute_read_continuation` (production
hardening pass #2 onward) is now the UNCONDITIONAL, primary path for
every resumed continuation, this function's chat_id-forwarding is only
ever relevant for the rare, defensive case of team_manager's own model
REDUNDANTLY re-delegating after a continuation already executed
deterministically -- in that rare case, `incident_manager` falls back to
resolving `chat_id` via `teams_list_chats` itself, exactly as it always
would have before continuations existed at all: a missed optimization
for an edge case, not a correctness regression. `chat_topic`/`question`/
`requested_time_range` below are unaffected -- they flow through the
model-visible `args` dict directly, not through session state, and are
correctly overridden regardless.

WHY question/requested_time_range ARE ALSO OVERRIDDEN HERE, NOT LEFT TO
WHATEVER team_manager's MODEL PASSED: the whole point of a
`ResolvedReadContinuation` is that focus/time-range were already captured,
verbatim, before the ambiguity existed (`PendingReadIntent`) -- letting
the model re-supply them from its own reading of the resume turn would
silently reopen exactly the reinterpretation risk this hardening pass
exists to remove. `question` reuses `read_resume.build_read_resume_
message` (the SAME deterministic operation-to-generic-phrase mapping
already used to build `resume_message` for the frontend) so `incident_
manager`'s existing, unmodified RESPONSE STRUCTURE reasoning keeps
receiving exactly the shape of `question` text it always has -- this
function reuses that logic rather than inventing a second one.

SINGLE-USE: `chat_service.py` is the ONLY producer (`stash_active_read_
continuation`), called at most once per turn, immediately after popping
(consuming) the continuation from persisted session state. This callback
is the ONLY consumer, and pops (not peeks) from `_ACTIVE_CONTINUATIONS`
-- so even if team_manager's model called `incident_manager` more than
once in the same turn (it never legitimately would for a plain resumed
read, but nothing here assumes that), only the FIRST call would ever see
the override; every subsequent call in that same turn proceeds as a
normal, model-driven call. Never written to persisted session state --
a purely in-process, per-turn hand-off (same category of mechanism as
`backend.api.turn_context`'s mailbox, for the same underlying reason:
`AgentTool.run_async` spins up a brand-new `InMemorySessionService()` per
call, so there is no shared nested-session identity to key durable state
by, and this data must not outlive the one call it applies to anyway).
"""
from __future__ import annotations

from typing import Any, Optional

from backend.selection.read_resume import build_read_resume_message
from backend.selection.schemas import PendingReadIntent, ResolvedReadContinuation

_INCIDENT_MANAGER_TOOL_NAME = "incident_manager"

_ACTIVE_CONTINUATIONS: dict[str, ResolvedReadContinuation] = {}


def stash_active_read_continuation(session_id: str, continuation: ResolvedReadContinuation) -> None:
    """Called by `chat_service.py`, at most once per turn, immediately
    after it pops (consumes) the continuation from persisted session
    state -- makes it available to `enforce_read_continuation` for the
    `incident_manager` tool call this SAME turn's team_manager Runner
    invocation is about to make, if any.
    """
    _ACTIVE_CONTINUATIONS[session_id] = continuation


def _pop_active_read_continuation(session_id: str) -> Optional[ResolvedReadContinuation]:
    return _ACTIVE_CONTINUATIONS.pop(session_id, None)


def discard_active_read_continuation(session_id: str) -> None:
    """Called by `chat_service.py`, unconditionally, from the SAME
    `finally` block that already guarantees `turn_context.py`'s mailbox
    cleanup runs on every exit path (success, a caught `Exception`, or an
    uncaught `BaseException` like `asyncio.CancelledError` -- a real
    server-side Stop). Without this, a stash that `enforce_read_
    continuation` never got a chance to consume (team_manager's model did
    not call `incident_manager` this turn at all -- e.g. the run was
    cancelled first) would otherwise remain in `_ACTIVE_CONTINUATIONS`
    indefinitely and could be wrongly picked up by a LATER, unrelated
    turn's `incident_manager` call. Always safe to call even when nothing
    was ever stashed for this session (a no-op `dict.pop` default).
    """
    _ACTIVE_CONTINUATIONS.pop(session_id, None)


def enforce_read_continuation(tool: Any, args: dict[str, Any], tool_context: Any) -> None:
    """ADK `before_tool_callback` for team_manager (wired in agent.py).

    Always returns `None` -- this only ever mutates `args`/`tool_context
    .state` in place as a deterministic side effect, never substitutes a
    fake tool result (returning non-`None` from a `before_tool_callback`
    skips the real tool call entirely, which this must never do:
    `incident_manager`'s own specialist reasoning over the retrieved
    evidence still needs to run for real).
    """
    if getattr(tool, "name", None) != _INCIDENT_MANAGER_TOOL_NAME:
        return None

    session_id = tool_context.session.id
    continuation = _pop_active_read_continuation(session_id)
    if continuation is None:
        return None

    args["chat_topic"] = continuation.selected_chat_topic
    args["question"] = build_read_resume_message(
        PendingReadIntent(operation=continuation.operation, question=continuation.question)
    )
    args["requested_time_range"] = continuation.requested_time_range
    tool_context.state["temp:resolved_chat_id"] = continuation.selected_chat_id
    return None
