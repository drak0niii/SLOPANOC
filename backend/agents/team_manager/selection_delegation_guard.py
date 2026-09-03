"""R3 FIX (correctness-regression pass): structurally prevents team_manager
from delegating to `incident_manager` more than once in the same turn once
an EARLIER delegation this SAME turn already returned `outcome=
"selection_needed"`.

THE BUG: prompt wording alone ("you have no separate action to take for
that; it resumes automatically") was not a structural guarantee -- live
behavior showed team_manager's model calling `incident_manager` again
after an ambiguous lookup, each doing its own `teams_list_chats`, before
the `SelectionCard` was ever shown. This is the SAME class of problem R1's
own investigation found for trusted-result presentation (prompt-only "do
not delegate" is insufficient against live Gemini behavior) -- the fix
here follows the identical philosophy: make the disallowed action
structurally impossible, never merely discouraged.

WHY `tool_context.state["temp:...]`, NOT A NEW IN-PROCESS/CONTEXTVAR
MECHANISM (unlike read_continuation_enforcement.py's `_ACTIVE_
CONTINUATIONS`): that module needs cross-session in-process state because
`AgentTool.run_async` spins up a brand-new, throwaway session per call --
there is no shared session identity to key durable state by. Here, by
contrast, team_manager's OWN outer Runner keeps ONE, single, live Session
object for the whole turn: an earlier step's `tool_context.state["temp:
..."] = True` write is applied to that SAME in-memory session
(`_apply_temp_state`, verified against the installed ADK 1.33.0 source in
an earlier pass -- see read_continuation_presentation.py's own docstring,
"WHY `temp:`-PREFIXING IS *NOT* USED HERE" -- the inverse case: THAT module
needed a plain key because it crosses a session boundary via `create_
session`; THIS module never does, so `temp:` is exactly correct here) and
is visible to a LATER step's `before_tool_callback` within the SAME turn.
`temp:`-prefixing is deliberate: this marker must never survive to a LATER
turn, and `_trim_temp_delta_state` already guarantees that for free -- no
explicit `chat_service.py` cleanup needed, unlike `_ACTIVE_CONTINUATIONS`
(which durably-scoped, cross-call state requires).

NEVER RE-DELEGATES, NEVER RE-RUNS `teams_list_chats`: `before_tool_
callback`s that return a non-`None` value skip the real tool call
entirely (the same documented ADK behavior `read_continuation_
enforcement.py`'s own docstring already establishes) -- so a blocked call
here never reaches `incident_manager`, never reaches `teams_list_chats`,
and never costs a second real gateway round trip.
"""
from __future__ import annotations

from typing import Any

_INCIDENT_MANAGER_TOOL_NAME = "incident_manager"

SELECTION_NEEDED_THIS_TURN_STATE_KEY = "temp:selection_needed_this_turn"


def record_selection_needed(tool: Any, args: dict[str, Any], tool_context: Any, tool_response: Any) -> None:
    """`after_tool_callback` for team_manager (wired in agent.py, alongside
    `state_sync.sync_incident_manager_result_to_state`). Marks this turn,
    in the live session's own `temp:` state, once `incident_manager`
    reports `outcome="selection_needed"` -- read back by
    `block_repeated_delegation_after_selection_needed` below. Always
    returns `None`: a deterministic side effect only, never a substitute
    tool result (same discipline `sync_incident_manager_result_to_state`
    already follows).
    """
    if getattr(tool, "name", None) != _INCIDENT_MANAGER_TOOL_NAME:
        return None
    if not isinstance(tool_response, dict) or tool_response.get("outcome") != "selection_needed":
        return None
    tool_context.state[SELECTION_NEEDED_THIS_TURN_STATE_KEY] = True
    return None


def block_repeated_delegation_after_selection_needed(tool: Any, args: dict[str, Any], tool_context: Any) -> Any:
    """`before_tool_callback` for team_manager (wired in agent.py, alongside
    `read_continuation_enforcement.enforce_read_continuation` -- both are
    registered as a LIST, ADK's own documented multi-callback mechanism
    verified against the installed 1.33.0 source, `flows/llm_flows/
    functions.py`'s `_run_with_trace`: each callback in `agent.canonical_
    before_tool_callbacks` runs in order, and the first to return a
    truthy value short-circuits the rest AND the real tool call).

    Once this turn has already seen ONE `selection_needed` result for
    `incident_manager`, ANY further call to it this same turn is
    intercepted here and answered with the SAME, safe, no-op-shaped
    result -- never a second real delegation, never a second `teams_list_
    chats`. `outcome="selection_needed"` alone (every other field already
    documented as left unset for this outcome -- see `IncidentManagerOutcome
    .SELECTION_NEEDED`'s own docstring) is exactly the truthful state:
    nothing has changed, the user still has not chosen.
    """
    if getattr(tool, "name", None) != _INCIDENT_MANAGER_TOOL_NAME:
        return None
    if not tool_context.state.get(SELECTION_NEEDED_THIS_TURN_STATE_KEY):
        return None
    return {"outcome": "selection_needed"}
